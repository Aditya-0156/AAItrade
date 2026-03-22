#!/usr/bin/env bash
# One-time server setup: install systemd service.
# Run this ON THE SERVER after git pull.
# Usage: bash ~/AAItrade/deploy/setup.sh

set -euo pipefail

SERVICE_SRC="$HOME/AAItrade/deploy/aaitrade.service"
SERVICE_DST="/etc/systemd/system/aaitrade.service"

echo "Installing AAItrade systemd service..."
sudo cp "$SERVICE_SRC" "$SERVICE_DST"
sudo systemctl daemon-reload
sudo systemctl enable aaitrade
sudo systemctl restart aaitrade

echo ""
sudo systemctl status aaitrade --no-pager
echo ""
echo "Done. AAItrade is running and will auto-start on reboot."
echo ""
echo "  Live logs:  sudo journalctl -u aaitrade -f"
echo "  Stop:       sudo systemctl stop aaitrade"
echo "  Restart:    sudo systemctl restart aaitrade"
