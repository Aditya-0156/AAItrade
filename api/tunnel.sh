#!/usr/bin/env bash
# Open SSH tunnel: your Mac localhost:8000 -> server port 8000
# Run this on your Mac whenever you want to use the dashboard.
# Leave it running in a terminal tab — Ctrl+C to close.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SSH_KEY="$PROJECT_DIR/server/ssh-key-2026-03-13.key"
REMOTE="ubuntu@68.233.98.35"

if [[ ! -f "$SSH_KEY" ]]; then
    echo "ERROR: SSH key not found at $SSH_KEY"
    exit 1
fi

echo "Opening tunnel: localhost:8000 -> $REMOTE:8000"
echo "Dashboard: http://localhost:5173"
echo "Press Ctrl+C to close."
echo ""

ssh -i "$SSH_KEY" -N -L 8000:localhost:8000 "$REMOTE"
