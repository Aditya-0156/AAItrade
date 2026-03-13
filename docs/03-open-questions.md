# Open Questions & Decisions Log

## Resolved ✓

| Decision | Resolved To |
|---|---|
| Execution modes | Paper + Live only. Backtest deferred to Phase 2. |
| Market scope Phase 1 | Indian markets only (NSE/BSE). US deferred. |
| Broker | Zerodha Kite Connect. Daily token refresh accepted. |
| Market data source | Zerodha Kite API (covers both data + execution) |
| News source | NewsAPI.org free tier to start |
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

### 1. System Prompt Draft
The most important artifact in the system. Needs to be written and agreed on before coding starts. Defines Claude's identity, mode-specific behaviour, output format, hard rules.

### 2. Initial Watchlist
Which 15–20 NSE stocks to monitor in Phase 1. Criteria: high liquidity, Nifty 50 large caps, sector diversity.

---

## Deferred to Phase 2

- Vector DB for semantic memory
- US market + Alpaca integration
- Dynamic stock screening (Claude discovers candidates)
- Web dashboard / UI
- Telegram / WhatsApp alerts
- Backtest mode
- F&O (options/futures) trading
