#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/YOUR_USER/polymarket-scanner.git}"
INSTALL_DIR="/opt/polymarket-scanner"

echo "=== Polymarket Scanner VPS Setup ==="

# Install system dependencies
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git

# Verify Python 3.11+
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "       Python version: $PYTHON_VERSION"

# Create scanner user
echo "[2/7] Creating scanner user..."
if ! id scanner &>/dev/null; then
    useradd --system --create-home --shell /bin/bash scanner
    echo "       Created user 'scanner'"
else
    echo "       User 'scanner' already exists"
fi

# Clone repo
echo "[3/7] Cloning repository..."
if [ -d "$INSTALL_DIR" ]; then
    echo "       $INSTALL_DIR already exists, pulling latest..."
    cd "$INSTALL_DIR" && git pull
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi
chown -R scanner:scanner "$INSTALL_DIR"

# Install Python dependencies
echo "[4/7] Installing Python dependencies..."
cd "$INSTALL_DIR"
pip3 install -r requirements.txt

# Set up .env
echo "[5/7] Setting up environment file..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$INSTALL_DIR/.env.example" ]; then
        cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        chown scanner:scanner "$INSTALL_DIR/.env"
        chmod 600 "$INSTALL_DIR/.env"
        echo "       Copied .env.example to .env"
    else
        touch "$INSTALL_DIR/.env"
        chown scanner:scanner "$INSTALL_DIR/.env"
        chmod 600 "$INSTALL_DIR/.env"
        echo "       Created empty .env"
    fi
else
    echo "       .env already exists, skipping"
fi

# Install systemd services
echo "[6/7] Installing systemd services..."
cp "$INSTALL_DIR/deploy/polymarket-scanner.service" /etc/systemd/system/
cp "$INSTALL_DIR/deploy/polymarket-cron.service" /etc/systemd/system/
cp "$INSTALL_DIR/deploy/polymarket-cron.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable polymarket-scanner.service
systemctl enable polymarket-cron.timer

# Create logs directory
echo "[7/7] Creating logs directory..."
mkdir -p "$INSTALL_DIR/logs"
chown scanner:scanner "$INSTALL_DIR/logs"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/polymarket-scanner/.env with your API keys and config"
echo "  2. Start the API server:   systemctl start polymarket-scanner"
echo "  3. Start the cron timer:   systemctl start polymarket-cron.timer"
echo "  4. Check status:           systemctl status polymarket-scanner"
echo "  5. View logs:              journalctl -u polymarket-scanner -f"
echo ""
