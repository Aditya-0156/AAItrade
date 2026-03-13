# Risk Rules & Guardrails

## Two-Layer Enforcement

Every rule exists in two places:
1. **System prompt** — Claude is told the rules and reasons within them
2. **executor.py** — Python enforces them as hard limits before any order executes

If Claude's output ever conflicts with a hard rule, Python overrides it. Claude's math is never trusted blindly — the executor recalculates everything independently.

---

## Execution Mode vs Trading Mode

These are two independent settings:

| Dimension | Options |
|---|---|
| **Execution mode** | `paper` (simulate) or `live` (real orders) |
| **Trading mode** | `safe`, `balanced`, or `aggressive` |

Example: you can run `paper + aggressive` to test aggressive strategy without real money, or `live + safe` when deploying capital carefully. They are orthogonal.

---

## Risk Rules by Trading Mode

### Per-Trade Rules

| Rule | Safe | Balanced | Aggressive |
|---|---|---|---|
| Max capital per trade | 7% | 10% | 15% |
| Stop-loss (exit if position drops by) | 2% | 3% | 5% |
| Take-profit target | 4% | 5% | 8% |

### Portfolio-Level Rules

| Rule | Safe | Balanced | Aggressive |
|---|---|---|---|
| Max open positions | 4 | 5 | 6 |
| Max capital deployed at once | 50% | 70% | 90% |
| Daily loss limit (halt trading for the day) | 3% | 5% | 8% |

### Session-Level Rules (apply to ALL modes — non-negotiable)

| Rule | Value | Notes |
|---|---|---|
| Session stop-loss | 20% of starting capital | If total drawdown hits 20%, halt session and alert user |
| Human alert threshold | Any single trade > 25% of capital | Should never happen — if it does, pause and notify |

Session-level rules are universal safety nets. They do not change per mode. An aggressive session can still blow up — 20% total drawdown is the hard floor regardless.

---

## What executor.py Does

When Claude outputs a decision, the executor runs a validation checklist before anything executes:

```
Claude says: BUY INFY x10

executor checks:
├── Is INFY on the watchlist?
├── Is the market currently open?
├── Do we have enough cash?
├── Would this trade exceed max % of capital for current mode?
├── Are we already at max open positions for current mode?
├── Has today's loss limit been hit?
├── Has the session stop-loss been hit?
└── Would this exceed the human alert threshold?

If all pass → attach stop-loss + take-profit prices → execute (simulate or live)
If any fail → override to HOLD, log the reason, continue
```

The executor also handles:
- **Compounding logic** (aggressive/balanced mode) — after a profitable trade closes, update available capital upward so next trades can be larger
- **Harvesting logic** (safe/balanced mode) — move a portion of realised profit into a "secured" pot that never gets reinvested
- **Position sizing** — translate "10% of capital" into an actual share quantity based on current price

---

## Profit Handling by Mode

| Mode | What happens when a trade closes in profit |
|---|---|
| Aggressive | 100% of profit added back to deployable capital (compounds) |
| Safe | 100% of profit moved to secured pot — never reinvested |
| Balanced | 50% compounded, 50% secured (configurable split) |

---

## Expandability Note

The risk rule set is designed to be config-driven — all values live in a single config object/file, not hardcoded throughout the codebase. Adding a new mode or changing a threshold means editing one place, not hunting through multiple files.
