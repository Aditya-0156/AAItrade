# Open Questions Before Coding

Things that need a decision before we write the first line of code.
Once answered, this file moves to resolved and gets folded into the relevant architecture doc.

---

## Must Resolve Before Coding

### 1. Market Data Source
How do we get live NSE prices and historical OHLCV?

Options:
- **Zerodha Kite API** — we already have an account. Provides live quotes, historical data, websocket feed. Most natural choice.
- **Yahoo Finance (yfinance)** — free, no account needed, but unofficial and unreliable for production
- **NSE India unofficial API** — free but fragile
- **Angel One SmartAPI / Upstox** — alternative brokers with free data APIs

**Recommended:** Zerodha Kite for both data AND execution — one integration covers both. Need to check if Kite historical data API is sufficient for indicator computation.

### 2. News Source
How do we fetch stock-specific news?

Options:
- **NewsAPI.org** — free tier (100 req/day), easy to use
- **Google News RSS** — free, no account, scraping-based
- **MoneyControl / Economic Times scraping** — India-specific but fragile
- **Zerodha's own news feed** — if available via Kite

**Recommended:** NewsAPI.org to start (free tier is enough for paper phase). Upgrade later if needed.

### 3. Watchlist — Which Stocks?
Which 15–25 NSE stocks should the initial watchlist contain?

Criteria for selection:
- High liquidity (easy to buy/sell without slippage)
- Part of Nifty 50 (well-covered by news, stable)
- Mix of sectors (not all IT or all banking)

**Decision needed:** Finalize the initial watchlist before first paper run.

### 4. Tech Stack Confirmation
Based on everything discussed, the natural stack is:

- **Language:** Python 3.11+
- **Claude integration:** Anthropic Python SDK (tool use)
- **Broker:** Zerodha Kite Connect Python SDK
- **Database:** SQLite (via Python `sqlite3` or SQLAlchemy)
- **Scheduler:** APScheduler or simple `time.sleep` loop
- **News:** NewsAPI Python client
- **Indicators:** `pandas-ta` or `ta-lib` (compute RSI, MAs, etc.)
- **CLI:** `argparse` or `typer` for `start_session` command

Any objections or preferences before this is locked?

### 5. Zerodha API Access
Zerodha Kite Connect requires:
- A Kite Connect app (created at developers.kite.trade)
- API key + secret
- Daily login token (Zerodha tokens expire daily — this affects the automation)

**The daily token problem:** Zerodha requires a manual login flow each morning to generate a fresh access token. This is a known pain point for automation. Options:
- Manual login each morning (simple, acceptable for now)
- Automate login with `selenium` / `playwright` (brittle, against ToS technically)
- Use a third-party token automation service

**Decision needed:** Accept manual daily login for Phase 1?

---

## Can Decide Later (Post Paper Phase)

- Vector DB for semantic memory (Phase 2)
- US market integration — Alpaca (Phase 2)
- Dynamic stock screening — Claude discovers candidates (Phase 2)
- Web dashboard / UI (Phase 2)
- Telegram / WhatsApp alerts (Phase 2)
