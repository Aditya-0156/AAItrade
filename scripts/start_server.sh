#!/usr/bin/env bash
# Start the AAItrade server (API + trading engine).
#
# Usage:
#   ./scripts/start_server.sh          # foreground (for systemd / debugging)
#   ./scripts/start_server.sh --bg     # background with nohup (quick & dirty)
#
# The server:
#   - Serves the dashboard API on port 8000
#   - Recovers active sessions on startup
#   - Runs trading sessions in background threads
#   - Dashboard can be opened/closed without affecting trading

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

# Load .env if present
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

# Activate venv if present
if [[ -f .venv/bin/activate ]]; then
    source .venv/bin/activate
fi

# Ensure logs directory exists
mkdir -p logs

LOG_FILE="logs/server_$(date +%Y%m%d_%H%M%S).log"

if [[ "${1:-}" == "--bg" ]]; then
    echo "Starting AAItrade server in background..."
    echo "Log: $LOG_FILE"
    echo "PID file: logs/server.pid"
    nohup python3 -m uvicorn api.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --log-level info \
        > "$LOG_FILE" 2>&1 &
    echo $! > logs/server.pid
    echo "Server started (PID: $(cat logs/server.pid))"
    echo "Dashboard: http://localhost:5173  |  API: http://localhost:8000"
    echo "Stop: kill \$(cat logs/server.pid)"
else
    echo "Starting AAItrade server..."
    echo "Dashboard: http://localhost:5173  |  API: http://localhost:8000"
    echo "Press Ctrl+C to stop."
    exec python3 -m uvicorn api.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --log-level info \
        2>&1 | tee "$LOG_FILE"
fi
