# Information Architecture & Trade Memory

## Core Principle
Claude only knows what we tell it. The quality of its decisions is directly proportional to the quality of information we provide. This document defines what information we gather, how we store it, and how we feed it back to Claude efficiently.

---

## Information Tiers

### Tier 1: Market Data (real-time, every cycle)
- Live prices, OHLCV via Zerodha Kite
- Technical indicators pre-computed in Python (RSI, MAs, VWAP, volume ratio)
- Raw candles never sent to Claude — only the computed summary

### Tier 2: News & Events (on-demand, cached where possible)
| Tool | Source | Frequency | Caching |
|---|---|---|---|
| `get_stock_news(symbol)` | NewsAPI | Per interesting stock | 1h cache |
| `get_sector_news(sector)` | NewsAPI | Per sector being evaluated | 2h cache |
| `get_macro_news()` | NewsAPI | Once at market open | Cached all day |
| `search_web(query)` | Tavily API | On demand by Claude | No cache |

**`get_macro_news()`** is the most important addition. It captures world events that move markets — geopolitical developments, central bank decisions, trade tariffs, commodity shocks. Fetched once at 9:00 AM IST, summarized, cached, and included in every briefing that day.

**`search_web(query)`** gives Claude free-form search capability. Claude can query anything: *"Trump tariff India manufacturing sector impact"*, *"Infosys fraud allegations 2025"*. Uses Tavily API (built for LLMs, returns clean summaries).

### Tier 3: Contextual Memory (persistent across cycles)
The trade journal — see below.

---

## API Cost Controls on Information Tools

Claude is instructed in the system prompt to use information tools selectively:

| Tool | Usage rule |
|---|---|
| `get_macro_news()` | Never call — pre-fetched at open, already in briefing |
| `get_sector_news()` | Only when actively evaluating a stock in that sector |
| `get_stock_news()` | Only for stocks Claude is seriously considering |
| `search_web()` | Max 2 calls per decision cycle, only when needed |
| `get_indicators()` | Only for stocks Claude wants deeper technical view on |

Macro news is cached and injected into every briefing automatically — Claude never needs to fetch it. This avoids repeated calls for the same information.

---

## News Processing Pipeline

Raw news articles and search results are never sent to Claude in full. They are pre-processed:

```
1. Fetch raw article / search result
2. Summarize to 3-5 key sentences
   → Use Claude Haiku (cheap, fast) for summarization
3. Store summary + source + timestamp in DB
4. Send summary to Claude, not full text
```

Full text is stored in DB for audit purposes but never sent to Claude unless explicitly needed.

---

## Trade Journal — Decision Memory

The most important memory component. Every BUY decision creates a **trade rationale record** stored in the DB. Every subsequent evaluation of that position reads this record and updates it.

### Trade Rationale Record Schema
```
symbol          : TATAMOTORS
action          : BUY
date            : 2025-03-13
entry_price     : ₹912
reason          : "Strong EV pipeline, Q3 beat expectations, JLR recovery
                   on track. EU tariff news is a risk but domestic demand
                   offsetting it."
news_cited      : ["Tata Motors Q3 profit up 37%", "JLR Feb sales +12%"]
key_thesis      : "Hold for 2-3 weeks pending JLR monthly sales data"
target_price    : ₹960
stop_price      : ₹885
─────────────────────────────────────────────────────
status          : OPEN
days_held       : 4
current_price   : ₹934
thesis_updates  : [
  { day: 1, note: "No new data. Thesis intact." },
  { day: 3, note: "JLR data still pending. Tariff risk not materialised." },
  { day: 4, note: "Domestic sales strong. Holding." }
]
```

### Journal Tools (`tools/journal.py`)
| Tool | Parameters | What it does |
|---|---|---|
| `write_trade_rationale` | `symbol`, `reason`, `news_cited`, `thesis`, `target`, `stop` | Creates record on BUY |
| `get_open_positions_with_rationale` | — | Returns all open positions with full journal records |
| `update_thesis` | `symbol`, `note` | Claude adds a thesis update note each time it reviews the position |
| `get_closed_trade_history` | `symbol`, `limit` | Returns past closed trades with outcome vs thesis |

### How Claude Uses the Journal

At the start of every decision cycle, Claude receives all open positions with their full rationale records. For each:

1. Read the original thesis
2. Check: has anything changed? (new news, price movement, time elapsed)
3. Update thesis_check note
4. Decide: hold / add / exit based on whether thesis is still valid

Example Claude reasoning:
> *"I bought TATAMOTORS because of JLR recovery thesis. JLR Feb sales came in at +12% — thesis is playing out. Price is ₹934, target ₹960. Holding."*

vs.

> *"I bought INFY expecting Q4 guidance to be strong. They just issued a guidance cut. Original thesis is broken. Exit."*

This is how a professional fund manager thinks — and now Claude can too.

---

## Estimated Token Usage Per Cycle

| Component | Tokens (approx) |
|---|---|
| System prompt (cached after first call) | ~800 (cached = much cheaper) |
| Market briefing (snapshot + watchlist summary) | ~600 |
| Open positions with rationale records | ~200 per open position |
| Macro news summary (pre-fetched, injected) | ~400 |
| Tool calls: stock news (2-3 stocks) | ~300 each |
| Tool calls: search_web (0-2 calls) | ~400 each |
| Tool calls: indicators (2-3 stocks) | ~100 each |
| Decision output | ~200 |
| **Typical total per cycle** | **~3,000–5,000 tokens** |

At 26 cycles/day with Claude Sonnet pricing, daily API cost is negligible. Summarization calls via Haiku (for news processing) are even cheaper.

---

## Information Flow Diagram

```
9:00 AM — Pre-market
  ├── Fetch macro news → summarize → cache for the day
  └── Fetch overnight news for all watchlist stocks → cache

9:15 AM — Market opens, decision loop starts

Every 15 min cycle:
  ├── BRIEFING ASSEMBLED:
  │   ├── Market snapshot (live)
  │   ├── Watchlist summary with indicators (live)
  │   ├── Open positions + journal records (DB)
  │   └── Macro news summary (cached from morning)
  │
  ├── CLAUDE RECEIVES BRIEFING
  │   ├── Reviews open positions → updates thesis notes
  │   ├── Identifies interesting watchlist candidates
  │   ├── Calls tools as needed:
  │   │   ├── get_stock_news(symbol)
  │   │   ├── get_sector_news(sector)
  │   │   ├── search_web(query)
  │   │   └── get_indicators(symbol)
  │   └── Outputs decision JSON
  │
  └── EXECUTOR validates → executes → journal updated → DB stored

3:30 PM — Market closes
  ├── End-of-day summary prompt sent to Claude
  ├── Claude reviews day's performance vs theses
  ├── Watchlist adjustment (if enabled)
  └── Daily report generated
```

---

## Storage Schema Addition

New tables for information and memory:

| Table | Contents |
|---|---|
| `trade_journal` | Rationale records for all trades |
| `thesis_updates` | Per-cycle thesis notes for open positions |
| `news_cache` | Summarized news items with symbol/sector/macro tags and timestamps |
| `search_cache` | Web search results with query, result, timestamp |
| `macro_daily` | Daily macro news summary |
