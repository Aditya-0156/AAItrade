# Open Questions & Decisions Log

## Resolved ✓

| Decision | Resolved To |
|---|---|
| Execution modes | Paper + Live only. Backtest deferred to Phase 2. |
| Market scope Phase 1 | Indian markets only (NSE/BSE). US deferred. |
| Broker | Zerodha Kite Connect. Daily token refresh accepted. |
| Market data source | Zerodha Kite API (covers both data + execution) |
| News source | NewsAPI.org (stock + sector + macro news) + Tavily API (web search) |
| Trade journal | Full rationale records per trade, thesis tracking across cycles, outcome review |
| News processing | Summarized via Claude Haiku before storing/sending — never raw articles to Claude |
| Decision approach | Hybrid tool-use — lightweight briefing + Claude calls tools on demand |
| Tool architecture | Expandable registry pattern — new tools added by dropping files in `tools/` |
| Storage | SQLite Phase 1, Postgres later. No vector DB in Phase 1. |
| Decision frequency | Every 15 minutes during market hours (9:15 AM – 3:30 PM IST) |
| Tech stack | Python 3.11+, Anthropic SDK, Kite Connect SDK, SQLite, pandas-ta, NewsAPI |
| Watchlist strategy | Fixed seed list (20 Nifty 50 large caps), Claude-adjustable via tools, toggleable setting, guardrails on adds |
| Trading modes | Safe / Balanced / Aggressive — each with distinct risk parameters |
| Risk rules | Documented in `04-risk-rules.md` |
| Executor role | Validates + gates every Claude decision before execution. Python always wins. |
| Profit handling | Aggressive: compound. Safe: harvest. Balanced: 50/50 split. |
| Tool expandability | Plug-and-play registry — Phase 2+ tools listed in `05-tools-and-expandability.md` |

---

## Still To Resolve Before Coding

### 1. ~~System Prompt~~ — DONE
Documented in `07-system-prompt.md`. Watchlist passed as config at session start.

### 2. ~~Initial Seed Watchlist~~ — DONE
30 stocks in `config/watchlist_seed.yaml`. Tier 1: 20 Nifty 50 large caps. Tier 2: 10 new-age/high-growth NSE-listed stocks (Zomato, Swiggy, Paytm, Nykaa, etc.).

---

## Deferred to Phase 2

- Vector DB for semantic memory
- US market + Alpaca integration
- Dynamic stock screening (Claude discovers candidates)
- Web dashboard / UI
- Telegram / WhatsApp alerts
- Backtest mode
- F&O (options/futures) trading
