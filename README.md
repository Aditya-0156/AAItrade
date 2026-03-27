# AAItrade

**Autonomous Artificial Intelligence Trade** — an AI-powered stock trading system where Claude (Anthropic) is the decision-making brain.

You give it capital and a watchlist. It runs autonomously — fetching live market data, reading news, analyzing technical indicators, managing a portfolio, and executing buy/sell decisions — all within hard-coded risk rules that Python enforces regardless of what the AI decides.

> **Status: Phase 1 — Live paper trading active on NSE.**
> Markets: NSE/BSE (India). US markets deferred to Phase 2.

---

## How It Works

### The Decision Loop (9:30 AM, 11:00 AM, 2:30 PM IST on trading days)

1. Claude receives a briefing: live market snapshot, global macro context (world markets, INR, India VIX), open positions with thesis, session stats
2. Claude calls tools to gather data — prices, indicators, news, web search
3. Claude reads its persistent session memory to recall what it was watching last cycle
4. **Claude calls `execute_trade()` directly** — trades execute in real-time during reasoning, not after. If rejected, Claude sees the reason and corrected max quantity immediately and can retry in the same cycle
5. Every `execute_trade()` call is validated by Python's executor against hard risk rules before touching any order
6. Results logged to SQLite; Telegram notifications sent for every trade and rejection

### Real-Time Execution Model

Claude doesn't output a JSON list of decisions to be processed afterward — it calls `execute_trade()` as a tool during its reasoning loop. This means:
- Trade rejections are visible to Claude **in the same cycle** with the exact reason
- Claude can retry with adjusted size, pivot to a different stock, or hold — all in one cycle
- The final JSON output is HOLD-only; all BUY/SELL happen exclusively via the tool

### Exit Discipline

Claude recognises two valid reasons to exit a position:
- **Thesis COMPLETION** — RSI recovered, price at MA20, setup delivered. Whether to take profit or continue holding is Claude's active choice each cycle, not a default
- **Thesis BREAK** — RSI fails to recover, price breaks MA20, catalyst invalidated. Exit without hesitation

When stop-loss and take-profit are set to 0, Claude has full discretion — managing exits based on RSI trajectory, price vs MA, sector momentum, volume, and macro backdrop.

---

## Risk Controls

Every `execute_trade()` call passes through `executor.py`. Claude cannot bypass these:

| Rule | Safe | Balanced | Aggressive |
|------|------|----------|------------|
| Max per trade | 7% of capital | 10% | 15% |
| Stop-loss | 2% below entry | 3% | 5% |
| Take-profit | 4% above entry | 5% | 8% |
| Max open positions | 4 | 5 | 6 |
| Max deployed capital | 50% | 70% | 90% |
| Daily loss limit | 3% | 5% | 8% |
| Session stop-loss | 20% drawdown → halt | same | same |

**Capital scaling**: risk limits use `effective_capital = free_cash + deployed`, not starting capital. As profits are reinvested, position sizing scales with the actual portfolio size. Starting capital is used only for drawdown calculations.

**Paper vs Live**: identical code paths. Only the final execution step differs — paper simulates the fill, live places a real order on Zerodha Kite and waits for order confirmation before updating the DB.

---

## Architecture

```
aaitrade/
├── main.py                  CLI entry: single / multi-session / recover modes
├── config.py                SessionConfig, RiskRules, TradingMode enums
├── db.py                    SQLite layer — all persistent state
├── claude_client.py         Anthropic API with tool-use loop + prompt caching
├── context_builder.py       System prompt + per-cycle briefing assembly
├── executor.py              Risk validation + trade execution (paper/live)
├── session_manager.py       Session lifecycle, decision loop, holidays, pause/resume
├── reporter.py              Daily + session reports with full trade journal
├── telegram_bot.py          Trade alerts, halt warnings, remote commands
├── holidays.py              NSE holiday calendar — non-trading days auto-skipped
├── multi_session.py         Parallel sessions in threads + crash recovery
└── tools/                   Claude-callable tool registry (plug-and-play)
    ├── trading.py            execute_trade — real-time BUY/SELL with immediate feedback
    ├── market.py             Live prices, OHLCV, technical indicators (RSI, MACD, BB, EMA)
    ├── news.py               Stock/sector/macro news via NewsAPI + LLM summarization
    ├── search.py             Web search via Tavily
    ├── portfolio_tools.py    Holdings, cash, effective capital, deployed %
    ├── memory.py             Trade history, session stats
    ├── journal.py            Trade rationale + thesis tracking (written post-execution only)
    ├── watchlist_tools.py    Watchlist view/add/remove
    └── session_memory.py     Claude's persistent cross-cycle memory narrative

dashboard/                   React + Vite frontend
├── src/pages/Overview.tsx   Session cards: total value, P&L, secured profit, deployed %
├── src/pages/DeepDive.tsx   Per-session drill-down: decisions, tool calls, journal
└── src/pages/ControlPanel.tsx  Start sessions, update token, adjust settings
```

**Adding a new tool**: write a function, decorate with `@register_tool(name, description, parameters)`, add the module to `_TOOL_MODULES` in `tools/__init__.py`. Claude sees it on the next run — no other changes needed.

---

## Tools Available to Claude

| Tool | What it does |
|------|-------------|
| `execute_trade` | BUY or SELL — executes immediately, returns result or rejection reason |
| `get_current_price` | Live NSE price via Kite Connect |
| `get_ohlcv` | Historical OHLCV candles |
| `get_multiple_prices` | Batch price fetch |
| `get_market_snapshot` | Nifty, Bank Nifty, sector indices |
| `get_indicators` | RSI, MACD, Bollinger Bands, EMA, SMA, ATR via pandas-ta |
| `get_stock_news` | Recent news for a specific stock |
| `get_sector_news` | Sector-level news and trends |
| `get_macro_news` | Global macro + India economic news |
| `search_web` | Tavily web search (max 2/cycle) |
| `get_portfolio` | Open positions with entry price, P&L, stop/target |
| `get_cash` | Free cash, deployed, effective capital |
| `get_session_summary` | Win rate, total P&L, session stats |
| `get_trade_history` | Past trades with outcomes |
| `get_open_positions_with_rationale` | Positions + original thesis from journal |
| `update_thesis` | Update the reasoning for an existing position |
| `get_watchlist` | Current watchlist |
| `add_to_watchlist` | Add a symbol |
| `remove_from_watchlist` | Remove a symbol |
| `get_session_memory` | Recall the cross-cycle memory narrative |
| `update_session_memory` | Write a narrative for the next cycle |

---

## Running It

```bash
# Install
pip install -e .

# Copy and fill in your API keys
cp .env.example .env

# Paper trading — balanced mode, ₹20,000, 10 market days
aaitrade --capital 20000 --mode balanced --days 10 --execution paper

# Live trading — same command, real orders via Zerodha Kite
aaitrade --capital 20000 --mode balanced --days 10 --execution live

# With a custom watchlist
aaitrade --capital 20000 --mode aggressive --days 10 --watchlist config/my_watchlist.yaml

# Run parallel sessions from YAML config
aaitrade --multi config/multi_session.yaml

# Resume sessions after a crash or restart
aaitrade --recover
```

### Live Trading Note
Live mode places real orders via Zerodha Kite Connect. Selling requires **DDPI (Demat Debit and Pledge Instructions)** to be activated on your Zerodha account — a one-time e-sign via the Zerodha console. Once active, sells execute automatically just like buys. The Kite access token expires daily and must be refreshed each morning before market open.

### Telegram Commands
| Command | Action |
|---------|--------|
| `/status` | Show active sessions |
| `/sessions` | List recent sessions |
| `/pause <id>` | Pause a session |
| `/resume <id>` | Resume a paused session |
| `/stop <id>` | Stop a session |
| `/token <token>` | Update Kite access token |
| `/feed <n>` | Show latest n decisions (1–50) |

---

## Required API Keys (`.env`)

```
ANTHROPIC_API_KEY=        # Claude API — decision brain
KITE_API_KEY=             # Zerodha Kite Connect — market data + execution
KITE_API_SECRET=
KITE_ACCESS_TOKEN=        # Refreshed daily (generate via Kite login flow)
NEWSAPI_KEY=              # NewsAPI — stock/macro news
TAVILY_API_KEY=           # Tavily — web search
TELEGRAM_BOT_TOKEN=       # Optional — notifications + remote commands
TELEGRAM_CHAT_ID=
HUGGINGFACE_API_KEY=      # Optional — news summarization
```

---

## Database (SQLite — `data/aaitrade.db`)

| Table | What it stores |
|-------|----------------|
| `sessions` | Config, capital, secured profit, status, P&L |
| `trades` | Every executed trade with price, quantity, P&L |
| `portfolio` | Current open positions with avg price, stop, target |
| `decisions` | Every Claude decision (BUY/SELL/HOLD) with reasoning |
| `tool_calls` | Every tool Claude called and its result |
| `trade_journal` | Entry rationale, thesis, target/stop, exit reason, outcome |
| `thesis_updates` | Per-cycle thesis reviews for open positions |
| `session_memory` | Claude's self-maintained cross-cycle memory blob |
| `news_cache` | Cached news summaries (avoids duplicate API calls) |
| `watchlist` | Active watchlist per session |
| `daily_summary` | EOD capital, P&L, win/loss stats per day |

---

## Profit Handling

When a position is sold at profit:
- A configurable `profit_reinvest_ratio` (default 50%) is added back to `current_capital` (free cash available for new trades)
- The remainder goes into `secured_profit` — tracked separately, not redeployed

`effective_capital` grows with each reinvested gain, allowing position sizing to compound with the portfolio rather than staying fixed at the starting amount.

---

## Disclaimer

This project is for educational and research purposes. Autonomous trading involves significant financial risk. Past simulated performance does not guarantee future results. Use at your own discretion.
