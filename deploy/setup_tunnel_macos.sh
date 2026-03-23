#!/usr/bin/env bash
# Install auto-tunnel on Mac: SSH tunnel opens at login and stays open.
# After this, just open http://localhost:8000 in your browser.
#
# Usage: bash deploy/setup_tunnel_macos.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

PLIST_SRC="$SCRIPT_DIR/com.aaitrade.tunnel.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.aaitrade.tunnel.plist"

mkdir -p "$PROJECT_DIR/logs"

# Unload existing if present
if launchctl list 2>/dev/null | grep -q com.aaitrade.tunnel; then
    echo "Removing existing tunnel service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"

echo ""
echo "Done. SSH tunnel is now running and will auto-start on login."
echo ""
echo "  Dashboard: http://localhost:8000"
echo "  Logs:      tail -f $PROJECT_DIR/logs/tunnel.log"
echo "  Stop:      launchctl stop com.aaitrade.tunnel"
echo "  Remove:    launchctl unload $PLIST_DST && rm $PLIST_DST"
