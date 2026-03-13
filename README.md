# AAItrade

**Autonomous Artificial Intelligence Trade** — an AI-powered stock trading system where Claude (Anthropic) is the decision-making brain.

You give it capital and a watchlist. It runs autonomously — fetching market data, reading news, analyzing technical indicators, managing a portfolio, and making buy/sell decisions — all within hard-coded risk rules that Python enforces regardless of what the AI decides.

> **Status: Phase 1 — Core system built, entering paper trading.**
> Markets: NSE/BSE (Indian). US markets deferred to Phase 2.

---

## What It Does

### The Core Loop (every 4 hours during market hours)
1. Claude receives a briefing: market snapshot, macro news, open positions with thesis, session stats
2. Claude calls tools to gather more data — prices, indicators, stock news, sector context, web search
3. Claude reads its persistent session memory to recall what it was watching last cycle
4. Claude outputs a JSON array of decisions — it can BUY and SELL multiple stocks in one cycle
5. Python's executor validates every decision against hard risk rules before touching any order
6. Results are logged to SQLite; Telegram notifications sent for every trade

### What Claude Can Do Each Cycle
- `get_session_memory` — recall running context from the previous cycle
- `update_session_memory` — write a 2400-char narrative for the next cycle (what it watched, decided, goals)
- `get_current_price`, `get_ohlcv`, `get_market_snapshot` — live NSE prices via Kite Connect
- `get_indicators` — RSI, MACD, Bollinger Bands, EMA, SMA, ATR via pandas-ta
- `get_stock_news`, `get_sector_news`, `get_macro_news` — news via NewsAPI + LLM summarization
- `web_search` — Tavily web search (max 2 calls per cycle)
- `get_portfolio`, `get_cash_balance`, `get_session_summary` — portfolio state
- `get_trade_history`, `get_open_positions_with_rationale` — trade memory
- `write_trade_rationale`, `update_thesis` — trade journal (records why every BUY was made)
- `get_watchlist`, `add_to_watchlist`, `remove_from_watchlist` — dynamic watchlist management

---

## Risk Controls (Python always wins)

Every decision Claude makes passes through `executor.py` before execution. Claude cannot bypass these:

| Rule | Safe | Balanced | Aggressive |
|------|------|----------|------------|
| Max per trade | 7% of capital | 10% | 15% |
| Stop-loss | 2% below entry | 3% | 5% |
| Take-profit | 4% above entry | 5% | 8% |
| Max open positions | 4 | 5 | 6 |
| Max deployed capital | 50% | 70% | 90% |
| Daily loss limit | 3% | 5% | 8% |
| Session stop-loss | 20% drawdown → halt | same | same |

- Every BUY auto-sets stop-loss and take-profit prices
- End-of-day: all open positions checked against stop/take-profit levels
- If Claude sets `HALT_SESSION` flag → session halted immediately
- Human alert threshold: any single trade > 25% of capital triggers `ALERT_USER` flag

---

## Execution Modes

**Paper trading** — no real orders placed. Prices are fetched live from Kite Connect but execution is simulated. Full P&L tracking, portfolio state, all logging — identical to live.

**Live trading** — real orders via Zerodha Kite Connect. Same code, only the final execution step differs.

---

## Architecture

```
aaitrade/
├── main.py                  CLI entry: single / multi-session / recover modes
├── config.py                SessionConfig, RiskRules, TradingMode enums
├── db.py                    SQLite layer — 13 tables, all persistent state
├── claude_client.py         Anthropic API with tool-use loop + prompt caching
├── context_builder.py       System prompt + per-cycle briefing assembly
├── executor.py              Risk validation + trade execution (paper/live)
├── session_manager.py       Session lifecycle, decision loop, holidays, pause/resume
├── reporter.py              Daily + session reports with full trade journal
├── telegram_bot.py          Trade alerts, halt warnings, remote commands
├── holidays.py              NSE holiday calendar — non-trading days auto-skipped
├── multi_session.py         Parallel sessions in threads + crash recovery
└── tools/                   Claude-callable tool registry (plug-and-play)
    ├── market.py             Prices, OHLCV, indicators
    ├── news.py               Stock/sector/macro news + LLM summarization
    ├── search.py             Web search via Tavily
    ├── portfolio_tools.py    Holdings, cash, P&L
    ├── memory.py             Trade history, session stats
    ├── journal.py            Trade rationale + thesis tracking
    ├── watchlist_tools.py    Watchlist view/add/remove
    └── session_memory.py     Claude's persistent cross-cycle memory (2400 chars)
```

**Adding a new tool**: write a function, decorate with `@register_tool(name, description, parameters)`, import the module in `tools/__init__.py`. Claude sees it on the next run.

---

## Running It

```bash
# Install
pip install -e .

# Copy and fill in your API keys
cp .env.example .env

# Paper trading — balanced mode, ₹20,000, 10 market days
aaitrade --capital 20000 --mode balanced --days 10 --execution paper

# With a custom watchlist
aaitrade --capital 20000 --mode aggressive --days 10 --watchlist config/my_watchlist.yaml

# Run 3 parallel sessions (safe/balanced/aggressive) from YAML config
aaitrade --multi config/multi_session.yaml

# Resume sessions after a crash or restart
aaitrade --recover
```

### Telegram Commands (while running)
| Command | Action |
|---------|--------|
| `/status` | Show active sessions |
| `/sessions` | List recent sessions |
| `/pause <id>` | Pause a session |
| `/resume <id>` | Resume a paused session |
| `/stop <id>` | Stop a session |
| `/token <token>` | Update Kite access token |

---

## Required API Keys (`.env`)

```
ANTHROPIC_API_KEY=        # Claude API — decision brain
KITE_API_KEY=             # Zerodha Kite Connect — market data + execution
KITE_API_SECRET=
KITE_ACCESS_TOKEN=        # Refreshed daily
NEWSAPI_KEY=              # NewsAPI — stock/macro news
TAVILY_API_KEY=           # Tavily — web search
TELEGRAM_BOT_TOKEN=       # Optional — notifications + remote commands
TELEGRAM_CHAT_ID=
```

---

## Database (SQLite)

All state lives in `data/aaitrade.db`. Key tables:

| Table | What it stores |
|-------|----------------|
| `sessions` | Session config, capital, status, P&L |
| `trades` | Every executed trade with price and P&L |
| `portfolio` | Current open positions |
| `decisions` | Every Claude decision (including HOLDs) with reasoning |
| `tool_calls` | Every tool Claude called and its result |
| `trade_journal` | Trade rationale, thesis, target/stop, outcome |
| `thesis_updates` | Per-cycle thesis reviews for open positions |
| `session_memory` | Claude's self-maintained cross-cycle memory blob |
| `news_cache` | Cached news summaries (avoid duplicate API calls) |
| `watchlist` | Active watchlist per session |
| `daily_summary` | EOD capital, P&L, win/loss per day |

---

## Cost (Haiku model, paper trading)

Approximate per active decision cycle:
- ~850 tokens system prompt (cached after first call in a cycle — ~65% cost reduction)
- ~310 tokens briefing
- ~400 tokens tool results
- ~300 tokens output

**~$0.006 per active cycle** at Haiku pricing. With 3 cycles/day × 3 sessions × 10 days ≈ **~$0.54 for a full 10-day paper run.**

---

## Disclaimer

This project is for educational and research purposes. Autonomous trading involves significant financial risk. Past simulated performance does not guarantee future results. Use at your own discretion.
