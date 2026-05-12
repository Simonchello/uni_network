#!/bin/bash
# Deploy the Xray bot on the VPS.
# Run from the directory containing bot.py etc.
set -e

BOT_DIR="/opt/xray-bot"
echo "==> Deploying to $BOT_DIR"

mkdir -p "$BOT_DIR"
cp bot.py db.py xray_manager.py stats.py requirements.txt "$BOT_DIR/"

if [ ! -f "$BOT_DIR/config.py" ]; then
    cp config.py.example "$BOT_DIR/config.py"
    chmod 600 "$BOT_DIR/config.py"
    echo "⚠  Created $BOT_DIR/config.py from example. Edit it before starting."
fi

echo "==> Installing Python dependencies"
apt-get install -y python3-venv python3-pip >/dev/null 2>&1 || true

if [ ! -d "$BOT_DIR/venv" ]; then
    python3 -m venv "$BOT_DIR/venv"
fi
"$BOT_DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$BOT_DIR/venv/bin/pip" install -r "$BOT_DIR/requirements.txt"

echo "==> Installing systemd service"
cp xray-bot.service /etc/systemd/system/
systemctl daemon-reload

echo ""
echo "✅ Bot deployed to $BOT_DIR"
echo ""
echo "Next steps:"
echo "  1. Edit /opt/xray-bot/config.py — set BOT_TOKEN and ADMIN_IDS"
echo "  2. systemctl enable --now xray-bot"
echo "  3. journalctl -u xray-bot -f  (watch logs)"
