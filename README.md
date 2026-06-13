# CSV to PDF Converter

Converts FieldServiceAllOpenTasks CSV exports from NetSuite into colour-coded A4 landscape PDF reports. Each region gets a distinct colour; tasks are grouped and sorted for easy field use.

## Features

- Colour-coded rows by region (up to 8 regions, auto-assigned)
- Priority highlighting
- Optional email delivery via a separate worker service
- Entirely in-memory: CSV is never written to disk
- Light/dark mode (Pastel/Aurora themes)

## Stack

Flask + pandas + ReportLab. Runs as a systemd service.

## Setup

```bash
./setup.sh                    # creates venv, installs deps
sudo ./install-service.sh     # web UI service (port 5050)
sudo ./install-email-service.sh  # optional email worker
```

## Services

```bash
sudo systemctl status csv-to-pdf
sudo systemctl status csv-to-pdf-email
sudo journalctl -u csv-to-pdf -f
```

## Email Worker

Copy `email_config.example.ini` to `email_config.ini` and fill in your SMTP details. The email worker runs independently of the web UI.

## Privacy

The uploaded CSV is processed in-memory and never stored. A privacy policy is available at `/privacy` on the running app.
