#!/usr/bin/env bash
# One-time setup for macOS: install launchd service so AAItrade starts on login.
#
# Usage: ./deploy/setup_macos.sh
#
# After this, the server:
#   - Starts automatically when you log in
#   - Restarts if it crashes (KeepAlive)
#   - Runs in background — no terminal needed
#
# Commands:
#   launchctl list | grep aaitrade        # check if running
#   launchctl stop com.aaitrade.server    # stop
#   launchctl start com.aaitrade.server   # start
#   tail -f logs/launchd_stdout.log       # live logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.aaitrade.server.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.aaitrade.server.plist"

# Ensure logs dir exists
mkdir -p "$PROJECT_DIR/logs"

# Unload if already loaded
if launchctl list 2>/dev/null | grep -q com.aaitrade.server; then
    echo "Unloading existing service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

echo "Installing AAItrade launchd service..."
cp "$PLIST_SRC" "$PLIST_DST"
launchctl load "$PLIST_DST"

echo ""
echo "Done! AAItrade server is now running."
echo ""
echo "  Check:   launchctl list | grep aaitrade"
echo "  Logs:    tail -f $PROJECT_DIR/logs/launchd_stdout.log"
echo "  Stop:    launchctl stop com.aaitrade.server"
echo "  Start:   launchctl start com.aaitrade.server"
echo "  Remove:  launchctl unload $PLIST_DST && rm $PLIST_DST"
