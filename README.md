# AAItrade

**Autonomous Artificial Intelligence Trade** — a production-grade AI trading system where Claude (Anthropic) acts as the sole decision-making brain for live Indian stock market trading on NSE via Zerodha Kite Connect.

You give it capital and a watchlist. It runs autonomously around the clock — fetching live market data, reading news, analyzing technical indicators, managing a real portfolio, and executing live buy/sell orders — all within hard-coded risk rules that Python enforces unconditionally, regardless of what the AI decides.

> **Status: Phase 1 (Live) — Running on NSE with real capital.**
> Indian markets (NSE/BSE) only. US markets deferred to Phase 2.

---

## What This Is

Most "AI trading" projects are wrappers around fixed rules — the AI just picks a stock, then scripted logic executes. AAItrade is different: Claude is genuinely the decision engine. It reads raw market data, calls tools in whatever order it decides, forms its own judgment about what to buy or sell, and executes trades directly via a tool call. The Python layer enforces risk rules and talks to the Kite API — it does not make trading decisions.

The trading philosophy is **swing trading for patience, not speed**: buy quality NSE stocks when they dip 3–8% on macro fear (not company-specific news), hold for 1–15 days, and take 0.5–1.5% profit per position. Monthly target: 2–3% total return by winning small multiple times. The system is explicitly instructed to never sell at a loss just because an indicator turned negative.

---

## How It Works

### Decision Cycles

The system runs **4 fixed slots per trading day**: 9:30 AM, 11:00 AM, 12:30 PM, and 2:00 PM IST. The 9:30 slot is observe-only (market open volatility) — Claude researches and plans but cannot execute trades until 11:00 AM.

At each cycle, Claude:
1. Receives a **briefing** with live market snapshot, global macro context (S&P 500, Nikkei, crude oil, gold, INR/USD, India VIX), open positions with thesis and hold duration, session statistics, and any active price alerts
2. **Calls tools** in whatever order it decides — prices, indicators, news, web search, portfolio state, session memory from the previous cycle
3. **Executes trades directly** via `execute_trade()` — trades happen in real-time during Claude's reasoning loop, not after it finishes. If a trade is rejected (e.g. exceeds position limit), Claude sees the exact reason and the corrected maximum quantity, and can retry immediately in the same cycle
4. **Writes session memory** — Claude maintains a narrative memory blob (max 2,880 chars) that persists across cycles so it remembers what it was watching, what it planned to do, and why it made prior decisions
5. Outputs a final JSON (HOLD + flags only — all actual trades already happened via tool calls)

### Price Alerts — Between-Cycle Monitoring

Claude can set **price alerts** that wake it up between scheduled cycles. When a target price is hit, the system immediately triggers an ad-hoc decision cycle without waiting for the next slot.

**Example**: Claude sees SUNPHARMA at ₹1,680 at the 11:00 AM cycle — good stock but wants to buy if it dips to ₹1,650. It calls `set_price_alert("SUNPHARMA", 1650, "below", "Buy the dip")`. A background thread polls prices every 30 seconds. When SUNPHARMA hits ₹1,650 ± 0.2%, Claude is woken for an ad-hoc cycle to act immediately.

**Timing guards**: alerts won't fire within 10 minutes of a scheduled cycle (no point interrupting something that's about to run), won't fire during a running cycle, and only fire during market hours (9:15 AM – 3:30 PM IST).

### Real-Time Execution Model

Claude doesn't output a list of decisions for Python to execute later — it calls `execute_trade()` as a tool during its own reasoning loop. This matters because:

- Trade rejections are visible to Claude **in the same cycle** with the exact reason and corrected parameters. Claude can retry with adjusted size, pivot to a different stock, or change strategy — all within one cycle.
- Multiple trades can execute in a single cycle (e.g. sell one position and buy another)
- Python's executor validates every call before touching any API — Claude cannot bypass risk rules

---

## Architecture

```
aaitrade/
├── main.py                   CLI entry: single / multi-session / recover
├── config.py                 SessionConfig, RiskRules, TradingMode enums
├── db.py                     SQLite layer — all persistent state
├── claude_client.py          Anthropic API with tool-use loop + prompt caching
├── context_builder.py        System prompt + per-cycle briefing assembly
├── executor.py               Risk validation + order execution (paper/live)
├── session_manager.py        Session lifecycle, decision loop, holidays, pause/resume
├── price_monitor.py          Background thread — polls prices, fires price alerts
├── reporter.py               Daily + session reports with full trade journal
├── telegram_bot.py           Trade alerts, halt warnings, remote commands
├── holidays.py               NSE holiday calendar — non-trading days auto-skipped
├── multi_session.py          Parallel sessions in threads + crash recovery
├── portfolio_sync.py         Syncs DB state with live Kite holdings (detects drift)
└── tools/                    Claude-callable tool registry (plug-and-play)
    ├── trading.py             execute_trade — real-time BUY/SELL with immediate feedback
    ├── market.py              Live prices, OHLCV, indicators (RSI, MACD, BB, EMA, ATR)
    ├── news.py                Stock/sector/macro news + LLM summarization (Haiku)
    ├── search.py              Web search via Tavily
    ├── portfolio_tools.py     Holdings, cash, effective capital, deployed %
    ├── memory.py              Trade history, session stats, closed trade journal
    ├── journal.py             Open positions with rationale + thesis tracking
    ├── watchlist_tools.py     Watchlist view/add/remove
    ├── session_memory.py      Claude's persistent cross-cycle memory narrative
    ├── price_alerts.py        Set/remove/view price alerts for between-cycle monitoring
    ├── session_analysis.py    Comprehensive P&L breakdown and trade pattern analysis
    ├── stock_thesis.py        Per-stock notes that persist across sessions
    ├── fundamentals.py        Stock fundamentals and sector data
    └── fiidii.py              FII/DII institutional flow data

dashboard/                    React + Vite frontend (TypeScript)
├── src/pages/Overview.tsx    Session cards: total value, P&L, secured profit, deployed %
├── src/pages/DeepDive.tsx    Per-session drill-down: decisions, tool calls, journal
└── src/pages/ControlPanel.tsx  Start sessions, update token, adjust settings

api/                          FastAPI backend serving the dashboard
├── main.py                   App entry + WebSocket feed
├── routes/                   REST endpoints for sessions, trades, decisions
└── ws/feed.py                Real-time WebSocket push for live updates

config/
├── watchlist_seed.yaml       Seed watchlist (30 NSE stocks across sectors)
└── multi_session.yaml        Multi-session config template
```

### Adding a New Tool

Write a Python function anywhere in `tools/`, decorate it with `@register_tool(name, description, parameters)`, and add the module to `_TOOL_MODULES` in `tools/__init__.py`. Claude sees it on the next run. No other changes needed.

---

## Risk Controls

Every `execute_trade()` call passes through `executor.py`. These rules are enforced by Python — Claude cannot override them.

| Rule | Safe | Balanced | Aggressive |
|------|------|----------|------------|
| Max per trade | 7% of effective capital | 10% | 15% |
| Stop-loss | 2% below entry | 3% | 5% |
| Take-profit | 4% above entry | 5% | 8% |
| Max open positions | 4 | 5 | 6 |
| Max deployed capital | 50% | 70% | 90% |
| Daily loss limit | 3% → HOLD only | 5% | 8% |
| Session stop-loss | 20% drawdown → halt | same | same |

**Effective capital**: risk limits use `effective_capital = free_cash + deployed_at_cost`, not just starting capital. As profits are reinvested, position sizing scales with the actual portfolio size. Starting capital is used only for drawdown calculations.

**Paper vs live**: identical code paths. Only the final step differs — paper simulates the fill and updates the DB instantly; live places a real Kite order and waits for API confirmation before updating state.

**Order type**: all orders use LIMIT with a ±0.5% offset from current price. BUY limit is set 0.5% above market (fills immediately at market), SELL limit is 0.5% below market. This avoids Kite's `market_protection` API requirement while achieving near-instant fills.

---

## Tools Claude Can Call

| Tool | What it does |
|------|-------------|
| `execute_trade` | BUY or SELL — executes immediately, returns result with price/quantity or rejection reason + corrected max quantity |
| `get_current_price` | Live NSE quote via Kite Connect (price, change %, volume, OHLC) |
| `get_price_history` | Historical OHLCV candles up to 360 days, with step parameter for compact long-range output |
| `get_multiple_prices` | Batch price fetch for multiple symbols in one call |
| `get_market_snapshot` | Nifty 50 and Bank Nifty live levels |
| `get_global_context` | S&P 500, Nikkei, crude oil, gold, INR/USD, India VIX |
| `get_indicators` | RSI, MACD, Bollinger Bands, EMA, SMA, ATR via pandas-ta |
| `get_stock_news` | Recent news for a specific NSE stock |
| `get_sector_news` | Sector-level news and trends |
| `get_macro_news` | Global macro + India economic news |
| `search_web` | Tavily web search for real-time information |
| `get_portfolio` | Open positions with entry price, current P&L, stop/target |
| `get_cash` | Free cash, deployed capital, effective capital |
| `get_session_summary` | Win/loss count, total P&L, today's P&L |
| `get_trade_history` | Past trades for a stock with outcomes and reasoning |
| `get_closed_trade_history` | Full journal context for closed trades: thesis, entry/exit, outcome |
| `get_open_positions_with_rationale` | Positions + original buy thesis from journal |
| `get_session_analysis` | Comprehensive breakdown: every trade, every position, what worked and why |
| `update_thesis` | Update reasoning for an existing open position |
| `write_trade_rationale` | Log the rationale for a buy (called automatically via execute_trade) |
| `update_stock_thesis` | Persistent per-stock notes that survive across sessions |
| `get_stock_thesis` | Recall past observations on a stock from prior sessions |
| `get_watchlist` | Current watchlist with company info |
| `add_to_watchlist` | Add a symbol to monitor |
| `remove_from_watchlist` | Remove a symbol |
| `get_session_memory` | Recall the cross-cycle narrative from last cycle |
| `update_session_memory` | Write a narrative of observations and plans for the next cycle |
| `set_price_alert` | Set a price target alert to wake Claude between scheduled cycles |
| `remove_price_alert` | Cancel an active alert |
| `get_price_alerts` | View all active alerts |
| `get_fii_dii_data` | FII/DII institutional buying/selling flows |

---

## Session Memory and Continuity

Claude writes a **session memory blob** at the end of every cycle (max 2,880 characters). On the next cycle, this memory is injected into the briefing so Claude remembers:
- What stocks it was watching and why
- What positions it holds and what the thesis was
- What it planned to do if certain conditions were met
- Why it made the decisions it did in the last cycle

This continuity is what makes the system behave like a consistent trader rather than making independent decisions from scratch every 90 minutes.

---

## Database (SQLite — `data/aaitrade.db`)

| Table | What it stores |
|-------|----------------|
| `sessions` | Config, capital, secured profit, status, trading mode |
| `trades` | Every executed trade: price, quantity, P&L |
| `portfolio` | Current open positions: avg price, stop-loss, take-profit |
| `decisions` | Every Claude output (BUY/SELL/HOLD) with reasoning and confidence |
| `tool_calls` | Every tool Claude called with parameters and result summary |
| `trade_journal` | Buy rationale, key thesis, target/stop, exit reason, realized P&L |
| `thesis_updates` | Per-cycle thesis reviews for each open position |
| `session_memory` | Claude's persistent cross-cycle narrative (one row per session) |
| `news_cache` | Cached news summaries with TTL (avoids duplicate API calls) |
| `watchlist` | Active watchlist per session with add/remove history |
| `daily_summary` | EOD capital, P&L, win/loss stats per day |
| `price_alerts` | Active/triggered/cancelled price alerts set by Claude |
| `stock_thesis_log` | Persistent per-stock notes across sessions |

All state survives restarts — the `--recover` flag resumes any active sessions by reading their last known state from the DB.

---

## Setup and Running

### Prerequisites

- Python 3.11+
- Zerodha Kite Connect subscription (for NSE data + order execution)
- Anthropic API key (Claude)
- NewsAPI key (news)
- Tavily API key (web search)
- Telegram bot (optional — for trade alerts and remote commands)

### Installation

```bash
git clone https://github.com/Aditya-0156/AAItrade
cd AAItrade
pip install -e .

# Copy and fill in your API keys
cp .env.example .env
```

### Running

```bash
# Paper trading — balanced mode, ₹20,000 capital
aaitrade --capital 20000 --mode balanced --execution paper

# Live trading — real orders on Zerodha
aaitrade --capital 20000 --mode balanced --execution live

# With a custom watchlist
aaitrade --capital 20000 --mode aggressive --watchlist config/my_watchlist.yaml

# Run parallel sessions from a YAML config
aaitrade --multi config/multi_session.yaml

# Resume sessions after a restart or crash
aaitrade --recover
```

### Dashboard

The React dashboard runs separately and connects to the FastAPI backend via HTTP and WebSocket.

```bash
# Start the API server (serves dashboard data)
uvicorn api.main:app --host 127.0.0.1 --port 8000

# Start the dashboard (separate terminal)
cd dashboard && npm install && npm run dev
```

### Live Trading Notes

- Live mode places real Zerodha Kite orders. Activate **DDPI (Demat Debit and Pledge Instructions)** in your Zerodha console (one-time e-sign) — required for the system to auto-sell holdings.
- The Kite access token expires every day. Run `python refresh_token.py` each morning before market open, or update via Telegram `/token <new_token>`.
- Keep your Kite app's IP whitelist updated to include the server's IP — the system will fail silently otherwise.

---

## Telegram Remote Control

| Command | Action |
|---------|--------|
| `/status` | Show active sessions with capital and P&L |
| `/sessions` | List recent sessions |
| `/pause <id>` | Pause a session (completes current cycle first) |
| `/resume <id>` | Resume a paused session |
| `/stop <id>` | Stop a session |
| `/token <token>` | Update Kite access token across all active sessions |
| `/feed <n>` | Show the last n decisions (1–50) |

Trade alerts are sent automatically for every executed trade, every rejected trade, and every halt event. Price alert triggers also send a Telegram notification before the ad-hoc cycle runs.

---

## Environment Variables (`.env`)

```
ANTHROPIC_API_KEY=        # Claude API — decision brain
KITE_API_KEY=             # Zerodha Kite Connect
KITE_API_SECRET=
KITE_ACCESS_TOKEN=        # Refreshed daily
NEWSAPI_KEY=              # Stock and macro news
TAVILY_API_KEY=           # Web search
TELEGRAM_BOT_TOKEN=       # Optional — notifications + remote commands
TELEGRAM_CHAT_ID=
HF_API_TOKEN=             # Optional — HuggingFace for large result summarization
```

---

## Profit Handling

When a position closes at a profit:
- `profit_reinvest_ratio` (default 0.25) determines the split
- 25% goes to `secured_profit` — locked, not redeployed
- 75% returns to `current_capital` (free cash) for new trades

`effective_capital` (free cash + deployed at cost) grows with each reinvested gain, so position sizing compounds with the portfolio rather than staying fixed at the starting amount.

---

## Server Deployment

The production instance runs on a single Ubuntu VPS (systemd service):

```bash
# Service management
sudo systemctl start aaitrade
sudo systemctl stop aaitrade
sudo systemctl status aaitrade
sudo journalctl -u aaitrade -f   # Live logs

# Deploy updates
cd ~/AAItrade && git pull && sudo systemctl restart aaitrade
```

The service file runs the FastAPI/uvicorn API server. The trading session itself is started from the dashboard or CLI and runs as background threads within that process.

---

## Disclaimer

This project is for educational and research purposes. Autonomous trading involves significant financial risk. Past performance — simulated or real — does not guarantee future results. Use at your own discretion with capital you can afford to lose.
