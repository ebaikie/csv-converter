#!/usr/bin/env bash
set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Field Service Tasks → PDF  · Setup     ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Detect OS ────────────────────────────────────────────────────────────────
if [ -f /etc/os-release ]; then
  . /etc/os-release
  OS=$ID
else
  OS=$(uname -s | tr '[:upper:]' '[:lower:]')
fi

echo "Detected OS: $OS"

# ── System dependencies ───────────────────────────────────────────────────────
case "$OS" in
  debian|ubuntu|linuxmint|pop)
    echo "Installing system packages..."
    sudo apt-get update -qq
    sudo apt-get install -y python3 python3-venv python3-pip
    ;;
  fedora|rhel|centos|rocky|almalinux)
    sudo dnf install -y python3 python3-pip
    ;;
  arch|manjaro)
    sudo pacman -Sy --noconfirm python python-pip
    ;;
  *)
    echo "Unknown OS — please ensure python3, python3-venv and python3-pip are installed."
    ;;
esac

PYTHON=$(command -v python3)
echo "Python: $($PYTHON --version)"

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  # --system-site-packages lets the venv use the system pip to install into itself
  $PYTHON -m venv --system-site-packages venv
fi

VENV_PYTHON="venv/bin/python3"

# ── Python dependencies ───────────────────────────────────────────────────────
echo "Installing Python packages..."
$VENV_PYTHON -m pip install --quiet -r requirements.txt

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "✓ Setup complete. Starting server..."
echo ""
echo "  Open your browser at:  http://localhost:5050"
echo "  Press Ctrl+C to stop."
echo ""

$VENV_PYTHON app.py
