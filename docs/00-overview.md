# AAItrade Planning — Overview

## Vision
A fully autonomous AI trading system where Claude makes all trading decisions, grounded in real-time and historical data across Indian and US markets.

## Core Concept
Claude is not a wrapper around rule-based logic. Claude *is* the strategy. Data pipelines, memory systems, and tool integrations exist to give Claude the best possible context for every decision.

## Planning Documents
- `00-overview.md` — this file
- `01-features.md` — feature list and scope decisions
- `02-architecture.md` — system architecture and component design
- `03-data-sources.md` — market data, news, and other data providers
- `04-risk-management.md` — risk controls and guardrails
- `05-stack-decisions.md` — tech stack evaluation and choices

## Open Questions (to resolve in planning)
- [ ] Paper trading first or live from the start?
- [ ] Which broker APIs to integrate (Zerodha/Upstox for India, Alpaca/IBKR for US)?
- [ ] How much of Claude's reasoning should be logged and auditable?
- [ ] What memory architecture? (vector DB, structured DB, hybrid?)
- [ ] How do we handle market hours differences between India and US?
- [ ] What is the minimum capital to start?
- [ ] How do we evaluate Claude's trading performance over time?
