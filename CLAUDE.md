# AAItrade — Claude Code Context

## Project Overview
AAItrade (Autonomous Artificial Intelligence Trade) is an AI-powered autonomous stock trading system. Claude is the core decision-making brain. Everything else — data pipelines, memory, retrieval, integrations — exists to make Claude smarter at trading.

## Markets (Phase 1)
- Indian markets: NSE (primary), BSE (secondary)
- US markets deferred to Phase 2

## Phase
**Phase 1: Implementation** — core system is built, entering testing.

## Tech Stack
- Python 3.11+
- Claude API (Anthropic SDK) — decision brain with tool use
- Zerodha Kite Connect — market data + order execution
- NewsAPI + Tavily — news and web search
- SQLite — persistent storage
- pandas-ta — technical indicator computation
- Telegram Bot API — notifications + remote commands

## Project Structure
```
aaitrade/
├── __init__.py              Package root
├── main.py                  CLI entry point (single/multi/recover modes)
├── config.py                Session config, risk rules, mode definitions
├── db.py                    SQLite database layer (all tables)
├── claude_client.py         Claude API with tool-use loop
├── context_builder.py       System prompt + per-cycle briefing assembly
├── executor.py              Risk validation + trade execution (paper/live)
├── session_manager.py       Session lifecycle + decision loop + holidays + pause/resume
├── reporter.py              Daily summaries + session reports
├── telegram_bot.py          Telegram notifications + command handling
├── holidays.py              NSE holiday calendar (skip non-trading days)
├── multi_session.py         Concurrent sessions + crash recovery
└── tools/                   Claude-callable tools (expandable registry)
    ├── __init__.py           Tool registry + auto-loader
    ├── market.py             Prices, OHLCV, indicators
    ├── news.py               Stock/sector/macro news + summarization
    ├── search.py             Web search via Tavily
    ├── portfolio_tools.py    Holdings, cash, P&L
    ├── memory.py             Trade history, session stats
    ├── journal.py            Trade rationale + thesis tracking
    └── watchlist_tools.py    Watchlist view/add/remove

config/
├── watchlist_seed.yaml      30-stock seed watchlist
└── multi_session.yaml       Multi-session config template
```

## Key Architectural Decisions
- **Two execution modes**: Paper (simulation) and Live (real orders) — identical code, only the final execution step differs
- **Three trading modes**: Safe, Balanced, Aggressive — each with distinct risk parameters
- **Hybrid tool-use**: Claude gets a lightweight briefing + tools to call on demand
- **Risk enforcement**: executor.py validates every decision; Python always wins over Claude
- **Expandable tools**: add new tools by writing a function + `@register_tool` decorator
- **Trade journal**: every BUY has a rationale record; thesis tracked across cycles
- **Concurrent sessions**: multi_session.py runs parallel sessions in threads, each isolated by session_id
- **Crash recovery**: `--recover` flag resumes active/paused sessions from DB state
- **Pause/resume**: via Telegram `/pause <id>` and `/resume <id>` commands, or DB status update
- **Holiday calendar**: NSE holidays + weekends auto-skipped
- **Telegram integration**: trade alerts, daily summaries, halt warnings, remote commands

## How to Run
```bash
# Paper trading, balanced mode, ₹10,000 capital, 14 days
aaitrade --capital 10000 --mode balanced --days 14 --execution paper

# With custom watchlist
aaitrade --capital 10000 --mode aggressive --days 7 --watchlist config/my_watchlist.yaml

# Multi-session mode (parallel sessions from YAML config)
aaitrade --multi config/multi_session.yaml

# Crash recovery (resume active sessions from DB)
aaitrade --recover
```

## Telegram Commands
`/status` — Show active sessions | `/sessions` — List recent sessions
`/pause <id>` — Pause a session | `/resume <id>` — Resume a session
`/stop <id>` — Stop a session | `/token <token>` — Update Kite access token

## Docs
Architecture, risk rules, system prompt, and all design decisions in [`docs/`](docs/).

## Goals
1. Learn Python, API building, and LLM integration through building this
2. Create a genuinely professional, scalable trading tool
