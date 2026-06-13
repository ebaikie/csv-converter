"""
Email Worker — polls a Gmail inbox, parses forwarded field service emails,
generates a PDF and replies with it attached.
"""

import imaplib
import smtplib
import email
import email.utils
import time
import re
import logging
import configparser
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime
from html.parser import HTMLParser

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ── HTML table parser ─────────────────────────────────────────────────────────

class TableParser(HTMLParser):
    """Extracts the first meaningful table from an HTML string."""
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_cell = False
        self.rows = []
        self.current_row = []
        self.current_cell = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth == 0:
                self.in_table = True
                self.rows = []
            self.depth += 1
        if self.in_table:
            if tag == "tr":
                self.current_row = []
            if tag in ("td", "th"):
                self.in_cell = True
                self.current_cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self.in_table = False
        if self.in_table:
            if tag in ("td", "th"):
                self.in_cell = False
                self.current_row.append(" ".join(self.current_cell).strip())
            if tag == "tr":
                if any(c.strip() for c in self.current_row):
                    self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_cell:
            stripped = data.strip()
            if stripped:
                self.current_cell.append(stripped)


def extract_table_from_html(html):
    parser = TableParser()
    parser.feed(html)

    if not parser.rows or len(parser.rows) < 2:
        return None

    # Find header row by scoring against known column keywords
    header_keywords = {"case", "subject", "type", "priority", "site", "region",
                       "task", "status", "date", "assigned", "comment", "detail"}
    header_idx = 0
    best_score = 0
    for i, row in enumerate(parser.rows[:5]):
        score = sum(1 for cell in row
                    if any(kw in cell.lower() for kw in header_keywords))
        if score > best_score:
            best_score = score
            header_idx = i

    headers = parser.rows[header_idx]
    data_rows = parser.rows[header_idx + 1:]

    # Pad short rows to header length
    n = len(headers)
    data_rows = [row[:n] + [""] * (n - len(row)) for row in data_rows]

    return pd.DataFrame(data_rows, columns=headers)


def normalise_email_df(df):
    """Map email table columns to the shape app.py's parse_csv expects."""
    df = df.copy()
    df.columns = [re.sub(r"\s+", " ", c.strip()) for c in df.columns]

    # We'll build a new mapping; handle duplicate Priority columns explicitly
    new_cols = []
    seen_priority = False
    for col in df.columns:
        low = col.lower()
        if "case subject" in low:
            new_cols.append("Case Subject")
        elif low in ("case type", "type"):
            new_cols.append("Case Type")
        elif "priority" in low:
            if not seen_priority:
                new_cols.append("Priority")
                seen_priority = True
            else:
                new_cols.append("Priority.1")
        elif "task" in low and ("#" in low or low == "task"):
            new_cols.append("Task#")
        elif "site" in low:
            new_cols.append("Site")
        elif "region" in low:
            new_cols.append("Region")
        elif "start" in low and "date" in low or "field service" in low:
            new_cols.append("Field Service Start Date")
        elif "target" in low or "resolution" in low:
            new_cols.append("Target Resolution")
        elif "assigned" in low:
            new_cols.append("Assigned To")
        elif low == "comment":
            new_cols.append("Comment")
        elif low == "status":
            new_cols.append("Status")
        elif "detail" in low:
            new_cols.append("Case Detail")
        else:
            new_cols.append(col)

    df.columns = new_cols
    return df


# ── Email handling ────────────────────────────────────────────────────────────

def get_html_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            charset = msg.get_content_charset() or "utf-8"
            return msg.get_payload(decode=True).decode(charset, errors="replace")
    return None


def send_reply(cfg, to_addr, original_subject, pdf_bytes, row_count):
    address  = cfg["gmail"]["address"]
    password = cfg["gmail"]["app_password"]

    msg = MIMEMultipart()
    msg["From"]    = address
    msg["To"]      = to_addr
    msg["Subject"] = f"Re: {original_subject} — PDF Report"

    now = datetime.now().strftime("%d %b %Y %H:%M")
    body = (
        f"Hi,\n\n"
        f"Please find attached your field service task report "
        f"({row_count} tasks), generated {now}.\n\n"
        f"— Field Service PDF Bot"
    )
    msg.attach(MIMEText(body, "plain"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    filename = f"FieldServiceTasks_{datetime.now().strftime('%Y%m%d')}.pdf"
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(address, password)
        server.sendmail(address, to_addr, msg.as_string())

    log.info("Reply sent to %s (%s)", to_addr, filename)


def process_email(cfg, mail, uid):
    from app import parse_csv, build_pdf

    _, data = mail.uid("fetch", uid, "(RFC822)")
    raw = data[0][1]
    msg = email.message_from_bytes(raw)

    subject  = msg.get("Subject", "Field Service Tasks")
    reply_to = email.utils.parseaddr(msg.get("Reply-To") or msg.get("From"))[1]
    log.info("Processing: '%s' from %s", subject, reply_to)

    html_body = get_html_body(msg)
    if not html_body:
        log.warning("No HTML body — skipping")
        return False

    df_raw = extract_table_from_html(html_body)
    if df_raw is None or df_raw.empty:
        log.warning("No table found in email — skipping")
        return False

    log.info("Extracted table: %d rows x %d cols", len(df_raw), len(df_raw.columns))
    log.info("Columns: %s", list(df_raw.columns))

    log.info("Raw columns from email: %s", list(df_raw.columns))
    log.info("Sample row: %s", df_raw.iloc[0].to_dict() if len(df_raw) > 0 else "empty")

    df_norm = normalise_email_df(df_raw)
    log.info("Normalised columns: %s", list(df_norm.columns))

    csv_bytes = df_norm.to_csv(index=False).encode("utf-8")

    df = parse_csv(csv_bytes)
    log.info("Parsed %d tasks, regions: %s", len(df), df["region"].unique().tolist())
    log.info("Region column sample: %s", df["region"].head(5).tolist())

    pdf_bytes = build_pdf(df, title="Field Service Open Tasks")
    send_reply(cfg, reply_to, subject, pdf_bytes, len(df))
    return True


def poll(cfg):
    address  = cfg["gmail"]["address"]
    password = cfg["gmail"]["app_password"]
    interval = int(cfg["gmail"].get("poll_interval_seconds", "60"))

    log.info("Poller started — checking %s every %ds", address, interval)

    while True:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(address, password)
            mail.select("inbox")

            _, uids = mail.uid("search", None, "UNSEEN")
            uid_list = [u for u in uids[0].split() if u]

            if uid_list:
                log.info("%d unread email(s) found", len(uid_list))
                for uid in uid_list:
                    try:
                        ok = process_email(cfg, mail, uid)
                        if ok:
                            mail.uid("store", uid, "+FLAGS", "\\Seen")
                    except Exception as e:
                        log.error("Failed on UID %s: %s", uid, e, exc_info=True)
            else:
                log.debug("No new emails")

            mail.logout()

        except Exception as e:
            log.error("IMAP error: %s", e, exc_info=True)

        time.sleep(interval)


if __name__ == "__main__":
    cfg = configparser.ConfigParser()
    cfg.read("email_config.ini")

    if not cfg.has_section("gmail"):
        log.error("email_config.ini missing or has no [gmail] section. See email_config.example.ini")
        raise SystemExit(1)

    poll(cfg)
