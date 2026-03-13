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

## Project Structure
```
aaitrade/
├── __init__.py              Package root
├── main.py                  CLI entry point
├── config.py                Session config, risk rules, mode definitions
├── db.py                    SQLite database layer (all tables)
├── claude_client.py         Claude API with tool-use loop
├── context_builder.py       System prompt + per-cycle briefing assembly
├── executor.py              Risk validation + trade execution (paper/live)
├── session_manager.py       Session lifecycle + decision loop scheduler
├── reporter.py              Daily summaries + session reports
└── tools/                   Claude-callable tools (expandable registry)
    ├── __init__.py           Tool registry + auto-loader
    ├── market.py             Prices, OHLCV, indicators
    ├── news.py               Stock/sector/macro news + summarization
    ├── search.py             Web search via Tavily
    ├── portfolio_tools.py    Holdings, cash, P&L
    ├── memory.py             Trade history, session stats
    ├── journal.py            Trade rationale + thesis tracking
    └── watchlist_tools.py    Watchlist view/add/remove
```

## Key Architectural Decisions
- **Two execution modes**: Paper (simulation) and Live (real orders) — identical code, only the final execution step differs
- **Three trading modes**: Safe, Balanced, Aggressive — each with distinct risk parameters
- **Hybrid tool-use**: Claude gets a lightweight briefing + tools to call on demand
- **Risk enforcement**: executor.py validates every decision; Python always wins over Claude
- **Expandable tools**: add new tools by writing a function + `@register_tool` decorator
- **Trade journal**: every BUY has a rationale record; thesis tracked across cycles

## How to Run
```bash
# Paper trading, balanced mode, ₹10,000 capital, 14 days
aaitrade --capital 10000 --mode balanced --days 14 --execution paper

# With custom watchlist
aaitrade --capital 10000 --mode aggressive --days 7 --watchlist config/my_watchlist.yaml
```

## Docs
Architecture, risk rules, system prompt, and all design decisions in [`docs/`](docs/).

## Goals
1. Learn Python, API building, and LLM integration through building this
2. Create a genuinely professional, scalable trading tool
