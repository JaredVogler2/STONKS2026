#!/usr/bin/env bash
# Run as your normal Pi user: bash scripts/install_service.sh
set -euo pipefail

BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(id -un)"
if [ "$RUN_USER" = "root" ]; then
  echo "Please run as your NORMAL login user, not with sudo."
  exit 1
fi
if [ ! -x "$BOT_DIR/.venv/bin/python" ]; then
  echo "Missing virtual environment. Complete Guide 05 installation first."
  exit 1
fi
if [ ! -f "$BOT_DIR/.env" ]; then
  echo "Missing private .env settings. Complete Guide 05 first."
  exit 1
fi
chmod 600 "$BOT_DIR/.env"
echo "Installing systemd service for $RUN_USER (sudo password may be requested)."
sudo tee /etc/systemd/system/apex-pi-bot.service >/dev/null <<EOF
[Unit]
Description=APEX Raspberry Pi Discord Starter Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python $BOT_DIR/apex_bot.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now apex-pi-bot.service
echo ""
echo "Installed! Service status:"
sudo systemctl --no-pager --full status apex-pi-bot.service || true
echo ""
echo "If active (running), test /apex_health in your Discord server."
echo "Logs: sudo journalctl -u apex-pi-bot -n 50 --no-pager"
