# Claude Tools — Design & Expandability

## Design Principle

The tool system is built to be **plug-and-play expandable**. Adding a new tool means:
1. Write the Python function
2. Register it in the tool registry
3. Done — Claude automatically has access to it next run

No changes to the decision loop, context builder, or Claude client needed.

---

## Tool Registry Pattern

All tools live in a central registry. The Claude client reads from this registry to build the tools list it sends to the API. This is the key to expandability — the rest of the system never needs to know what tools exist.

```
tools/
├── __init__.py          ← tool registry lives here
├── market.py            ← price, indicators, OHLCV
├── news.py              ← news fetching and summarization
├── portfolio.py         ← holdings, cash, P&L queries
├── memory.py            ← past decisions and trade history
└── [future tools...]    ← drop new files here to add tools
```

---

## Phase 1 Tools

These are the tools Claude has access to at launch:

### Market Tools (`tools/market.py`)
| Tool | Parameters | Returns |
|---|---|---|
| `get_price_history` | `symbol`, `days` | OHLCV candles for N days |
| `get_indicators` | `symbol` | RSI, 20MA, 50MA, VWAP, volume ratio |
| `get_current_price` | `symbol` | Live quote: price, change%, volume |
| `get_market_snapshot` | — | Nifty, Bank Nifty, advance/decline ratio |

### News Tools (`tools/news.py`)
| Tool | Parameters | Returns |
|---|---|---|
| `get_news` | `symbol`, `hours=24` | Latest N news items, each summarized to 2 lines |

### Portfolio Tools (`tools/portfolio.py`)
| Tool | Parameters | Returns |
|---|---|---|
| `get_portfolio` | — | All holdings with avg price, current price, P&L |
| `get_cash` | — | Available cash + secured profit breakdown |

### Memory Tools (`tools/memory.py`)
| Tool | Parameters | Returns |
|---|---|---|
| `get_trade_history` | `symbol`, `limit=5` | Past decisions on this stock in current session |
| `get_session_summary` | — | Running stats: win rate, total P&L, trades today |

### Watchlist Tools (`tools/watchlist.py`)
| Tool | Parameters | Returns |
|---|---|---|
| `get_watchlist` | — | Current watchlist with sector tags |
| `add_to_watchlist` | `symbol`, `reason` | Success/failure + validation result |
| `remove_from_watchlist` | `symbol`, `reason` | Success/failure |

These tools are only available to Claude when **watchlist adjustment is enabled** in session config. When disabled, Claude cannot modify the watchlist at all — it only sees `get_watchlist`.

#### Watchlist Adjustment Setting
```
session config:
  allow_watchlist_adjustment: true | false  (default: true)
```

When `false`: `add_to_watchlist` and `remove_from_watchlist` are not registered — Claude never even sees them.
When `true`: Claude can adjust the watchlist **only at end-of-day**, not mid-cycle. The system prompt enforces this timing constraint.

#### Guardrails on `add_to_watchlist`
Before adding a stock, the tool validates:
- Symbol exists on NSE
- Minimum market cap (large/mid cap only — no penny stocks or illiquid small caps)
- Minimum average daily volume threshold
- Watchlist has not exceeded max size cap (default: 30 stocks)

If any check fails, the stock is rejected and Claude is told why. Claude cannot bypass these checks.

---

## Phase 2+ Tools (planned, not built yet)

| Tool | Purpose |
|---|---|
| `screen_stocks` | Claude-driven stock discovery beyond fixed watchlist |
| `get_fiidii_flows` | FII/DII activity from NSE data |
| `get_earnings_calendar` | Upcoming earnings for watchlist stocks |
| `get_sector_performance` | Sector-wise performance today |
| `get_options_chain` | F&O data for a symbol |
| `search_web` | General web search for breaking news |
| `get_analyst_ratings` | Analyst buy/sell/hold consensus |

These are dropped into `tools/` as new files and registered — no other changes required.

---

## How Tool Calls Work in a Decision Cycle

```
1. Claude receives lightweight briefing
2. Claude decides it wants more info on INFY
3. Claude calls: get_news("INFY")
4. Python executes the function, returns result
5. Claude calls: get_indicators("INFY")
6. Python executes, returns result
7. Claude is satisfied, outputs decision JSON
8. Loop ends
```

Claude can call 0 to N tools per decision cycle. There is a configurable max (e.g. 5 tool calls per cycle) to prevent runaway loops.

---

## Tool Call Logging

Every tool call Claude makes is logged:
- Which tool was called
- What parameters were passed
- What was returned
- Timestamp

This creates an audit trail of *why* Claude made each decision — not just what it decided, but what information it gathered before deciding. Invaluable for debugging bad trades.
