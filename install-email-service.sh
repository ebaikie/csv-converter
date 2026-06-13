#!/usr/bin/env bash
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER="$(whoami)"
SERVICE_NAME="csv-to-pdf-email"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_PYTHON="${APP_DIR}/venv/bin/python3"
CONFIG="${APP_DIR}/email_config.ini"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Field Service PDF · Email Worker       ║"
echo "╚══════════════════════════════════════════╝"
echo ""

if [ ! -f "$VENV_PYTHON" ]; then
  echo "ERROR: venv not found. Run ./setup.sh first."
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  echo "ERROR: email_config.ini not found."
  echo "Copy email_config.example.ini to email_config.ini and fill in your details."
  exit 1
fi

echo "Installing email worker service..."

SYSTEMD_APP_DIR="${APP_DIR// /\\x20}"
SYSTEMD_VENV_PYTHON="${VENV_PYTHON// /\\x20}"

sudo tee "$SERVICE_FILE" > /dev/null << UNIT
[Unit]
Description=Field Service PDF - Email Worker
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${SYSTEMD_APP_DIR}
ExecStart=${SYSTEMD_VENV_PYTHON} ${SYSTEMD_APP_DIR}/email_worker.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager

echo ""
echo "Useful commands:"
echo "  Status : sudo systemctl status $SERVICE_NAME"
echo "  Logs   : sudo journalctl -u $SERVICE_NAME -f"
echo "  Stop   : sudo systemctl stop $SERVICE_NAME"
echo ""
