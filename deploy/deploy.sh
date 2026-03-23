#!/usr/bin/env bash
# Deploy latest code to server: pull, build dashboard, restart service.
# Run this on your Mac after git push.
#
# Usage: bash deploy/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SSH_KEY="$PROJECT_DIR/server/ssh-key-2026-03-13.key"
REMOTE="ubuntu@68.233.98.35"

echo "Deploying to $REMOTE..."

ssh -i "$SSH_KEY" "$REMOTE" bash << 'ENDSSH'
set -e
cd ~/AAItrade

echo "--- Pulling latest code ---"
git pull

echo "--- Building dashboard ---"
cd dashboard
npm install --silent
npm run build
cd ..

echo "--- Restarting service ---"
sudo systemctl restart aaitrade
sleep 3
sudo systemctl status aaitrade --no-pager | head -8
echo ""
echo "Done. Dashboard live at: http://localhost:8000 (via tunnel)"
ENDSSH
