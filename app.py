"""
Field Service Open Tasks — CSV → PDF
Tailored for the FieldServiceAllOpenTasks export format.
"""

import io
import json
import re
from datetime import datetime

from flask import Flask, request, jsonify, send_file, render_template
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

app = Flask(__name__)

# ── Region colours ───────────────────────────────────────────────────────────
REGION_PALETTE = [
    ("#1a5e8a", "#d4ebf7"),
    ("#7b3f8c", "#f0ddf7"),
    ("#2e7d55", "#d2f0e3"),
    ("#c45c1a", "#fde8d5"),
    ("#b5392a", "#fad5d0"),
    ("#1f4e8a", "#d5e4f7"),
    ("#6b6b1a", "#f0f0cc"),
    ("#8a1a6b", "#f7d0ec"),
    ("#1a6b6b", "#d0f5f5"),
    ("#8a5c1a", "#f5e6cc"),
]

PRIORITY_STYLE = {
    "p1":             ("#ffffff", "#cc0000", "P1"),
    "p2":             ("#ffffff", "#e05000", "P2"),
    "p3":             ("#ffffff", "#c47800", "P3"),
    "p4":             ("#1a1a2e", "#f5d060", "P4"),
    "p5":             ("#1a1a2e", "#b8d4f0", "P5"),
    "high":           ("#ffffff", "#cc0000", "High"),
    "medium":         ("#1a1a2e", "#f5d060", "Med"),
    "low":            ("#1a1a2e", "#b8d4f0", "Low"),
    "un-prioritised": ("#888888", "#eeeeee", "—"),
}

STATUS_STYLE = {
    "not started": ("#666688", "#f4f4f4"),
    "in progress": ("#1a5e8a", "#d4ebf7"),
    "completed":   ("#2e7d55", "#d2f0e3"),
    "on hold":     ("#c45c1a", "#fde8d5"),
}


def hex_to_rl(h):
    h = h.lstrip("#")
    return colors.Color(int(h[0:2],16)/255, int(h[2:4],16)/255, int(h[4:6],16)/255)


def strip_html(val):
    if not isinstance(val, str):
        return ""
    m = re.search(r'>([^<]+)</a>', val)
    if m:
        return m.group(1).strip()
    return re.sub(r'<[^>]+>', '', val).strip()


def fmt_date(val):
    if not val or str(val).strip() in ("", "nan", "NaT"):
        return ""
    s = str(val).strip()
    for fmt in ("%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %H:%M:%S %p",
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return f"{dt.day} {dt.strftime('%b %Y')}"
        except ValueError:
            continue
    return s


def normalise_priority(val):
    if not val or str(val).strip().lower() in ("", "nan"):
        return "un-prioritised"
    return str(val).strip().lower()


def parse_csv(file_bytes: bytes) -> pd.DataFrame:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc)
            break
        except Exception:
            continue

    df.columns = [c.strip() for c in df.columns]

    out = pd.DataFrame()
    def clean_case(val):
        text = strip_html(val)
        # If the link text has the CASE number duplicated (system export artefact),
        # extract just the leading CASE/TASK number + the meaningful description.
        # Pattern: "CASExxxxx <junk> CASExxxxx <actual description>"  → "CASExxxxx <actual description>"
        m = re.match(r'^(CASE\d+|TASK\d+)\s+.+?\s+(CASE\d+|TASK\d+)\s+(.+)$', text)
        if m:
            return f"{m.group(1)} {m.group(3)}"
        return text

    out["case_number"] = df.get("Case Subject", pd.Series(dtype=str)).apply(clean_case)
    out["task_number"] = df.get("Task#",        pd.Series(dtype=str)).apply(strip_html)
    out["case_type"]   = df.get("Case Type",    pd.Series(dtype=str)).fillna("").str.strip()
    out["site"]        = df.get("Site",         pd.Series(dtype=str)).fillna("").str.strip()
    out["region"]      = df.get("Region",       pd.Series(dtype=str)).fillna("").str.strip()
    out["start_date"]  = df.get("Field Service Start Date", pd.Series(dtype=str)).apply(fmt_date)
    out["target_date"] = df.get("Target Resolution",        pd.Series(dtype=str)).apply(fmt_date)

    task_pri = df.get("Priority.1", pd.Series(dtype=str)).fillna("")
    case_pri = df.get("Priority",   pd.Series(dtype=str)).fillna("")
    out["priority"]    = task_pri.where(task_pri.str.strip() != "", case_pri).str.strip()

    out["assigned_to"] = df.get("Assigned To", pd.Series(dtype=str)).fillna("").str.strip()
    out["status"]      = df.get("Status",      pd.Series(dtype=str)).fillna("").str.strip()
    out["comment"]     = df.get("Comment",     pd.Series(dtype=str)).fillna("").str.strip()

    # Clean site name
    out["site_clean"] = out["site"].str.replace(r"\s*-\s*Technology$", "", regex=True).str.strip()

    # Sort
    priority_order = {"High":0,"P1":1,"P2":2,"P3":3,"P4":4,"Medium":3,"P5":5,"Low":6,"UN-PRIORITISED":7,"":8}
    out["_ps"] = out["priority"].map(lambda p: priority_order.get(p, 99))
    out = out.sort_values(["region","_ps","start_date"]).drop(columns=["_ps"]).reset_index(drop=True)
    return out


def build_pdf(df: pd.DataFrame, title: str = "Field Service Open Tasks") -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4),
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=12*mm, bottomMargin=14*mm,
    )

    regions = sorted(df["region"].unique())
    region_color_map = {}
    for i, r in enumerate(regions):
        dark, light = REGION_PALETTE[i % len(REGION_PALETTE)]
        region_color_map[r] = (hex_to_rl(dark), hex_to_rl(light), dark, light)

    INK       = colors.HexColor("#1a1a2e")
    INK_MUTED = colors.HexColor("#55556e")

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    title_ps  = ps("T",   fontName="Helvetica-Bold", fontSize=15, textColor=INK, spaceAfter=2)
    sub_ps    = ps("S",   fontName="Helvetica",      fontSize=8,  textColor=INK_MUTED)
    hdr_ps    = ps("H",   fontName="Helvetica-Bold", fontSize=7,  textColor=colors.white, alignment=TA_CENTER, leading=8)
    cell_ps   = ps("C",   fontName="Helvetica",      fontSize=7.5, textColor=INK, leading=9)
    cell_sm   = ps("Csm", fontName="Helvetica",      fontSize=6.5, textColor=INK_MUTED, leading=8)
    center_ps = ps("Ctr", fontName="Helvetica",      fontSize=7.5, textColor=INK, alignment=TA_CENTER, leading=9)

    story = []

    now = datetime.now()
    today = f"{now.day} {now.strftime('%b %Y')}"
    story.append(Paragraph(title, title_ps))
    story.append(Paragraph(
        f"{len(df)} open tasks  ·  {', '.join(regions)}  ·  Generated {today}", sub_ps))
    story.append(HRFlowable(width="100%", thickness=1.5, color=INK, spaceAfter=4, spaceBefore=4))

    # Legend
    legend_items = []
    for r in regions:
        _, _, dark_hex, light_hex = region_color_map[r]
        count = len(df[df["region"] == r])
        legend_items.append(
            Paragraph(f"<b>{r}</b>  ({count} tasks)",
                      ps("L", fontName="Helvetica", fontSize=7.5,
                         textColor=hex_to_rl(dark_hex), leading=9))
        )
    if legend_items:
        leg_w = (landscape(A4)[0] - 24*mm) / len(legend_items)
        legend_table = Table([legend_items], colWidths=[leg_w]*len(legend_items))
        leg_cmds = [
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("GRID",          (0,0),(-1,-1), 0.4, colors.HexColor("#ccccdd")),
        ]
        for i, r in enumerate(regions):
            _, light, _, _ = region_color_map[r]
            leg_cmds.append(("BACKGROUND", (i,0),(i,0), light))
        legend_table.setStyle(TableStyle(leg_cmds))
        story.append(legend_table)
        story.append(Spacer(1, 4*mm))

    # ── Main table ────────────────────────────────────────────────────────────
    PAGE_W = landscape(A4)[0] - 24*mm
    COLS = [
        ("Task #",    "task_number",  24),
        ("Case",      "case_number",  52),
        ("Type",      "case_type",    24),
        ("Site",      "site_clean",   52),
        ("Region",    "region",       30),
        ("Pri",       "priority",     16),
        ("Start",     "start_date",   22),
        ("Target",    "target_date",  22),
        ("Status",    "status",       22),
        ("Comment",   "comment",      46),
    ]
    total_w = sum(c[2] for c in COLS)
    col_widths = [c[2]*mm * PAGE_W / (total_w*mm) for c in COLS]

    header_row = [Paragraph(c[0], hdr_ps) for c in COLS]
    data_rows = [header_row]
    row_meta  = []

    pri_idx    = [c[1] for c in COLS].index("priority")
    status_idx = [c[1] for c in COLS].index("status")

    for _, row in df.iterrows():
        pri_key    = normalise_priority(row["priority"])
        status_key = str(row["status"]).strip().lower()
        pri_info   = PRIORITY_STYLE.get(pri_key, PRIORITY_STYLE["un-prioritised"])

        cells = []
        for col_hdr, field, _ in COLS:
            val = str(row.get(field, "")).strip()

            if field == "priority":
                cells.append(Paragraph(
                    f"<b>{pri_info[2]}</b>",
                    ps("P", fontName="Helvetica-Bold", fontSize=7.5,
                       textColor=hex_to_rl(pri_info[0]), alignment=TA_CENTER, leading=9)
                ))
            elif field == "case_number":
                parts = val.split(" ", 1)
                if len(parts) == 2:
                    desc = parts[1][:65] + ("…" if len(parts[1]) > 65 else "")
                    cells.append(Paragraph(
                        f'<b>{parts[0]}</b><br/>'
                        f'<font color="#888888" size="6.5"><i>{desc}</i></font>',
                        cell_ps))
                else:
                    cells.append(Paragraph(f"<b>{val}</b>", cell_ps))
            elif field == "comment":
                trunc = val[:100] + ("…" if len(val) > 100 else "")
                cells.append(Paragraph(trunc, cell_sm))
            elif field in ("start_date", "target_date", "status"):
                cells.append(Paragraph(val, center_ps))
            else:
                cells.append(Paragraph(val, cell_ps))

        data_rows.append(cells)
        row_meta.append((row["region"], pri_key, status_key))

    table = Table(data_rows, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND",    (0,0), (-1,0), INK),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 4),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4),
        ("GRID",          (0,0), (-1,-1), 0.25, colors.HexColor("#d0d0e8")),
        ("LINEBELOW",     (0,0), (-1,0), 1.5, colors.HexColor("#4444aa")),
    ]

    for ri, (region, pri_key, status_key) in enumerate(row_meta, start=1):
        if region in region_color_map:
            _, light, _, _ = region_color_map[region]
            style_cmds.append(("BACKGROUND", (0,ri), (-1,ri), light))

        pri_info = PRIORITY_STYLE.get(pri_key, PRIORITY_STYLE["un-prioritised"])
        style_cmds.append(("BACKGROUND", (pri_idx,ri),(pri_idx,ri), hex_to_rl(pri_info[1])))
        style_cmds.append(("TEXTCOLOR",  (pri_idx,ri),(pri_idx,ri), hex_to_rl(pri_info[0])))

        st_fg, st_bg = STATUS_STYLE.get(status_key, ("#444", "#f8f8f8"))
        style_cmds.append(("BACKGROUND", (status_idx,ri),(status_idx,ri), hex_to_rl(st_bg)))
        style_cmds.append(("TEXTCOLOR",  (status_idx,ri),(status_idx,ri), hex_to_rl(st_fg)))

    table.setStyle(TableStyle(style_cmds))
    story.append(table)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(INK_MUTED)
        w, _ = landscape(A4)
        canvas.drawString(12*mm, 7*mm, title)
        canvas.drawRightString(w-12*mm, 7*mm, f"Page {doc.page}  ·  {today}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    return buffer.read()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/preview", methods=["POST"])
def preview():
    f = request.files.get("csv")
    if not f:
        return jsonify({"error": "No file uploaded"}), 400
    try:
        df = parse_csv(f.read())
        regions    = sorted(df["region"].unique().tolist())
        case_types = df["case_type"].value_counts().to_dict()
        statuses   = df["status"].value_counts().to_dict()
        priorities = df["priority"].value_counts().to_dict()
        columns = ["task_number","case_number","case_type","site_clean",
                   "region","priority","start_date","target_date","status","comment"]
        return jsonify({
            "columns":    columns,
            "rows":       json.loads(df.head(20).to_json(orient="records")),
            "total":      len(df),
            "regions":    regions,
            "case_types": case_types,
            "statuses":   statuses,
            "priorities": priorities,
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 400


@app.route("/generate", methods=["POST"])
def generate():
    f = request.files.get("csv")
    title = request.form.get("title", "Field Service Open Tasks").strip() or "Field Service Open Tasks"
    if not f:
        return jsonify({"error": "No file uploaded"}), 400
    try:
        df  = parse_csv(f.read())
        pdf = build_pdf(df, title=title)
        return send_file(
            io.BytesIO(pdf),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{title.replace(' ','_')}.pdf",
        )
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5050)
