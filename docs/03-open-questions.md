# Open Questions & Decisions Log

## Resolved ✓

| Decision | Resolved To |
|---|---|
| Execution modes | Paper + Live only. Backtest deferred to Phase 2. |
| Market scope Phase 1 | Indian markets only (NSE/BSE). US deferred. |
| Broker | Zerodha Kite Connect. Daily token refresh via Telegram (manual). |
| Market data source | Zerodha Kite API (covers both data + execution) |
| News source | NewsAPI.org (stock + sector + macro news) + Tavily API (web search) |
| Trade journal | Full rationale records per trade, thesis tracking across cycles, outcome review |
| News processing | Summarized via Claude Haiku before storing/sending — never raw articles to Claude |
| Decision approach | Hybrid tool-use — lightweight briefing + Claude calls tools on demand |
| Tool architecture | Expandable registry pattern — new tools added by dropping files in `tools/` |
| Storage | SQLite Phase 1, Postgres later. No vector DB in Phase 1. |
| Decision frequency | Every 15 minutes during market hours (9:15 AM – 3:30 PM IST) |
| Tech stack | Python 3.11+, Anthropic SDK, Kite Connect SDK, SQLite, pandas-ta, NewsAPI |
| Watchlist strategy | 30 stocks (20 Nifty 50 + 10 new-age), Claude-adjustable, toggleable, guardrails |
| Trading modes | Safe / Balanced / Aggressive — each with distinct risk parameters |
| Risk rules | Documented in `04-risk-rules.md` |
| Executor role | Validates + gates every Claude decision before execution. Python always wins. |
| Profit handling | Aggressive: compound. Safe: harvest. Balanced: 50/50 split. |
| Tool expandability | Plug-and-play registry — Phase 2+ tools listed in `05-tools-and-expandability.md` |
| System prompt | Documented in `07-system-prompt.md`. Watchlist passed as config at session start. |
| Seed watchlist | 30 stocks in `config/watchlist_seed.yaml`. |
| Deployment | Oracle Cloud free tier VPS. Cron-based scheduling. |
| Notifications | Telegram bot — trade alerts, daily summaries, commands (`/status`, `/stop`, `/pause`, `/resume`) |
| Concurrent sessions | Supported. Each session isolated by `session_id`. Multi-session YAML config for parallel paper runs. |
| Live mode overlap | Virtual sub-accounts — DB tracks ownership per session, Zerodha is just execution layer. One live session recommended initially. |
| Kite token refresh | Manual via Telegram command each morning. |
| Market holidays | NSE holiday calendar check — skip non-trading days. |
| Crash recovery | Resume active sessions on restart from DB state. |
| Session control | Pause/resume per session via Telegram commands + CLI. |

---

## All Pre-Coding Decisions Resolved

Nothing remaining. System is in implementation phase.

---

## Deferred to Phase 2

- Vector DB for semantic memory
- US market + Alpaca integration
- Dynamic stock screening (Claude discovers candidates)
- Web dashboard / UI
- Backtest mode
- F&O (options/futures) trading
- Multiple broker accounts for true live session isolation
