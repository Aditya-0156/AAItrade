# System Architecture

## Core Philosophy
Claude is the brain. Python is the body. Every component exists to give Claude the best possible context for making trade decisions — no more, no less.

---

## High-Level System Diagram

```
┌─────────────────────────────────────────────────────┐
│                   USER / CLI                        │
│  start_session(capital, mode, duration, market)     │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│              SESSION MANAGER                        │
│  Owns session state: capital, mode, day count       │
│  Orchestrates the decision loop on a schedule       │
└────────────────────┬────────────────────────────────┘
                     │
         ┌───────────▼───────────┐
         │   CONTEXT BUILDER     │
         │  Assembles the prompt │
         └───────────┬───────────┘
                     │
        ┌────────────┼─────────────┐
        ▼            ▼             ▼
  [Market Data]  [Memory DB]  [Portfolio State]
  live prices    past trades   holdings, cash
  news           decisions     P&L so far
  indicators     outcomes
                     │
         ┌───────────▼───────────┐
         │     CLAUDE (brain)    │
         │  Tool-use enabled     │
         │  Drives own research  │
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │      EXECUTOR         │
         │  Paper: simulate_order│
         │  Live:  place_order   │
         └───────────┬───────────┘
                     │
         ┌───────────▼───────────┐
         │      REPORTER         │
         │  Logs, P&L, summaries │
         └───────────────────────┘
```

---

## The Decision Loop

Runs every N minutes during market hours (9:15 AM – 3:30 PM IST). One full cycle:

```
1. FETCH     → Pull live prices, news headlines, indicators for watchlist
2. BUILD     → Context Builder assembles lightweight briefing prompt
3. DECIDE    → Send briefing + tools to Claude
               Claude calls tools as needed (0 to N times)
               Claude outputs structured JSON decision
4. EXECUTE   → Paper: log the trade | Live: place order via Zerodha Kite API
5. STORE     → Save decision + reasoning + outcome to DB
6. REPEAT    → Wait for next interval
```

Claude is called only in step 3. Everything else is Python. This keeps costs controlled.

---

## Decision Step — Hybrid Tool-Use Approach

Claude receives a **lightweight briefing** plus a **set of tools** it can call on demand.

### Initial Briefing (always sent)
- Session state: mode, capital, day N of M, goal
- Market snapshot: Nifty, Bank Nifty, breadth, sentiment
- Watchlist summary: one line per stock (price, RSI, 1-line news flag)
- Current portfolio: holdings, cash, P&L

### Tools Claude Can Call (on demand)
Claude calls these only when it needs deeper information on a specific stock:

| Tool | What it returns |
|---|---|
| `get_price_history(symbol, days)` | OHLCV candles for N days |
| `get_indicators(symbol)` | RSI, MAs, VWAP, volume ratio |
| `get_news(symbol)` | Latest 3-5 news items for the stock |
| `get_trade_history(symbol)` | Past decisions on this stock in current session |
| `get_portfolio()` | Full holdings breakdown with P&L |

### Claude's Output (always structured JSON)
```json
{
  "action": "BUY | SELL | HOLD",
  "symbol": "INFY",
  "quantity": 5,
  "reason": "RSI recovering from oversold, strong Q3 results, within risk limits",
  "confidence": "high | medium | low"
}
```

If Claude outputs `HOLD` with `confidence: low` and a reason of "insufficient data" — that is valid and handled gracefully. Claude never guesses.

---

## Context Builder — Prompt Structure

The prompt has distinct sections assembled at runtime:

### 1. System Prompt (static, cached)
Defines Claude's identity, mode, hard rules, output format. Sent every call but cached by Anthropic — cheaper after first call.

### 2. Market Snapshot (live, fetched each call)
Nifty/Bank Nifty levels, today's advance/decline ratio, any circuit breakers or halts.

### 3. Watchlist Summary (pre-computed)
Indicators computed in Python before sending. Raw candles never sent to Claude — too many tokens. Claude gets the interpretation:
```
RELIANCE | ₹2,847 | RSI: 62 (neutral) | Above 20MA | Vol: 1.2x avg | No news
INFY     | ₹1,432 | RSI: 71 (overbought) | Below 50MA | Vol: 0.8x avg | Q3 beat
```

### 4. Portfolio State
Current holdings, average buy price, unrealised P&L, available cash, secured profit (in safe/balanced mode).

### 5. Session Memory (retrieved, not full history)
Only recent and relevant past decisions — not the full trade log. Retrieved by DB query.

### 6. Task Instruction
What Claude should do: analyse, call tools if needed, output a decision in JSON.

---

## API Cost Control

| What | How we control cost |
|---|---|
| Raw OHLCV candles | Never sent — pre-computed to summary in Python |
| Full news articles | Summarized to 1-2 lines before sending |
| Full trade history | DB query returns only recent/relevant trades |
| Continuous polling | Claude called on schedule (e.g. every 15 min), not every tick |
| System prompt | Cached via Anthropic prompt caching — cheaper after first call |
| Tool calls | Claude only calls tools for stocks it finds interesting |

Estimated cost per decision cycle: ~1,500–3,000 tokens including tool calls. Extremely cheap at Claude API pricing.

---

## Memory & Storage

### Structured DB — SQLite (Phase 1), Postgres (later)
Fixed-schema data with known structure:

| Table | Contents |
|---|---|
| `sessions` | Config, start capital, mode, status, start/end time |
| `trades` | Every buy/sell: symbol, price, qty, timestamp, P&L |
| `portfolio` | Current holdings snapshot |
| `decisions` | Every Claude decision including reasoning and confidence |
| `daily_summary` | End-of-day performance stats |

### Vector DB — Deferred to Phase 2
Semantic retrieval of past decisions ("find past trades where RSI was high on INFY"). Not needed in Phase 1 with a focused watchlist and structured queries.

---

## Seed Prompts (to be designed carefully)

Three prompt templates to build:

**1. System Prompt** — Claude's identity, mode logic, hard constraints, output format. Most important. Gets the most iteration during paper testing.

**2. Decision Prompt** — Per-call template filled with live context. Drives step 3 of the loop.

**3. End-of-Day Summary Prompt** — Runs once after market close. Input: today's trades and P&L. Output: performance summary, what worked, what to watch tomorrow.

---

## Component List (Phase 1)

| Component | Responsibility | Priority |
|---|---|---|
| `session_manager.py` | Start/stop sessions, own state, run the loop | Core |
| `context_builder.py` | Assemble prompt from all data sources | Core |
| `claude_client.py` | Send prompt + tools, parse JSON response | Core |
| `market_data.py` | Fetch live prices, pre-compute indicators | Core |
| `news_fetcher.py` | Fetch + summarize recent news per stock | Important |
| `portfolio.py` | Track holdings, cash, P&L in real time | Core |
| `executor.py` | `simulate_order()` / `place_order()` | Core |
| `db.py` | SQLite read/write for all persistent state | Core |
| `reporter.py` | End-of-day summary, performance stats | Important |
| `watchlist.py` | Manage which stocks to monitor | Core |
| `scheduler.py` | Run loop every N min during market hours | Core |
| `tools.py` | All Claude-callable tool functions | Core |

---

## Watchlist Strategy

**Phase 1: Fixed watchlist** — a curated list of 15–25 liquid NSE stocks (large caps from Nifty 50). Claude analyses these each cycle.

Dynamic stock screening (Claude discovers its own candidates) is a Phase 2 feature. Fixed watchlist keeps scope manageable and results interpretable during paper testing.

---

## Decision Frequency

**Every 15 minutes during market hours.** Rationale:
- Fast enough to catch meaningful intraday moves
- Slow enough to avoid noise and keep API costs low
- ~26 decision cycles per trading day

Configurable — can be tuned after paper testing reveals what frequency works best.

---

## Human Alerts (exceptions only)

The system runs autonomously. The user is only notified when:
- A single trade would use > 30% of capital (pause + alert)
- Daily loss exceeds configured threshold (stop session + alert)
- Broker API error / authentication failure
- End-of-day summary (always sent)

Everything else runs without interruption.

---

## What Domain Knowledge Claude Already Has
Claude already understands: RSI, moving averages, candlestick patterns, NSE/BSE market structure, sector dynamics, earnings impact, macro factors.

What we must supply (Claude cannot know on its own):
- Real-time prices and live market data
- News published after its training cutoff
- Your specific session state (capital, mode, holdings)
- Your specific rules and risk constraints
- Past decisions made within the current session
