# AAItrade Dashboard

A React + TypeScript dashboard for monitoring the AAItrade autonomous trading system, backed by a FastAPI read-only API over the SQLite database.

---

## Architecture

```
Browser (localhost:5173)
  └── Vite dev server (proxies /api and /ws to localhost:8000)
        └── SSH tunnel (localhost:8000 → remote server:8000)
              └── FastAPI (api/main.py) reading aaitrade.db
```

---

## Setup

### 1. Backend — on the server running AAItrade

```bash
cd /path/to/AAItrade

# Create and activate a venv (or reuse existing)
python -m venv .venv && source .venv/bin/activate

# Install API dependencies
pip install -r api/requirements.txt

# Start the API (reads data/aaitrade.db by default)
uvicorn api.main:app --host 0.0.0.0 --port 8000

# Or with a custom DB path:
AAITRADE_DB_PATH=/path/to/aaitrade.db uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### 2. SSH tunnel — on your local machine

```bash
./api/tunnel.sh user@your-server-ip
```

This forwards `localhost:8000` → `server:8000`. Keep this terminal open.

### 3. Frontend — on your local machine

```bash
cd dashboard

# Install dependencies (first time only)
npm install

# Start dev server
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

---

## Pages

| Page | Description |
|------|-------------|
| **Overview** | Session cards with live capital, P&L %, deployment bar. Combined portfolio stats. Polls every 60s. |
| **Sessions** | Select a session; view open positions table, trade history, deployment pie chart, P&L-by-day line chart. |
| **Activity** | Live feed of decisions (BUY/SELL/HOLD) and tool calls, merged and sorted by time. Filter by session and type. WebSocket-powered (polls every 15s). |
| **Deep Dive** | Trade journal cards with full thesis, entry/stop/target, news cited, exit reason. Thesis update timeline. Claude's session memory blob. |

---

## WebSocket

The dashboard connects to `ws://localhost:8000/ws/feed` (proxied via Vite). It receives new decisions and tool_calls every 15 seconds. The connection status dot in the top-right shows live/disconnected state, with automatic exponential-backoff reconnect.

---

## Production build

```bash
cd dashboard
npm run build
# Serve dist/ with any static file server, e.g.:
npx serve dist
```

---

## Environment variables (backend)

| Variable | Default | Description |
|----------|---------|-------------|
| `AAITRADE_DB_PATH` | `/Users/aditya/AAItrade/data/aaitrade.db` | Path to SQLite DB |
