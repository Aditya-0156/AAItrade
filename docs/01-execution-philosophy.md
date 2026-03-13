# Execution Philosophy & Testing Strategy

## Two Execution Modes

AAItrade will support two execution modes, selectable at runtime:

### 1. Paper Mode (Simulation)
- No real orders placed. Ever.
- System makes decisions exactly as it would in live mode
- All trades recorded in a local ledger with timestamps, prices, P&L
- Uses real market data for prices
- Purpose: validate Claude's decision quality before risking capital
- **This is the starting point. Minimum 2 weeks of paper runs before any live money.**

### 2. Live Mode (Real Money)
- Actual orders placed via broker API (Zerodha Kite)
- Same logic as paper mode — only the execution layer changes
- Will start with small amounts (e.g. ₹10,000) — learning is the priority, not profit
- Risk guardrails enforced in code, not just in Claude's reasoning

> Backtest mode (historical replay) is out of scope for Phase 1. It can be added later once the core system is stable and there are strategy variations worth comparing.

---

## Paper Trading Philosophy

The key insight: **paper mode and live mode share the same brain and the same code.**
The only difference is the final execution step — `simulate_order()` vs `place_order()`.

This means:
- If paper results are good → flip to live with confidence
- If paper results are bad → improve the system, never touch real money
- Paper runs generate real performance data: win rate, avg return, drawdown, Sharpe ratio

---

## Testing Plan (First 2 Weeks)

Run multiple parallel paper sessions with different modes and settings:
- Session A: Aggressive / compound mode — reinvest all profits
- Session B: Safe / harvest mode — lock in profits, minimize drawdown
- Session C: Balanced mode
- Compare results across modes and market conditions

---

## Trading Modes (Goal-Oriented Configuration)

When launching a session, the user sets:

| Parameter | Description | Example |
|---|---|---|
| `capital` | Starting amount | ₹10,000 |
| `duration` | How long to run | 14 days |
| `mode` | Strategy profile | aggressive / safe / balanced |
| `market` | Which market | NSE / BSE |
| `instruments` | What to trade | equities / F&O / ETFs |

### Mode Definitions

**Aggressive (Compound)**
- Reinvest profits into new positions
- Principal grows over time
- Higher risk tolerance, larger position sizes
- Goal: maximize total return

**Safe (Harvest)**
- Take profits off the table regularly
- Keep a "secured" pot that never gets reinvested
- Lower position sizes, tighter stop-losses
- Goal: preserve capital, steady gains

**Balanced**
- Reinvest a portion (e.g. 50%), harvest the rest
- Configurable split ratio
- Goal: grow and protect simultaneously

---

## Market Scope (Phase 1)

**Indian markets only to start:**
- NSE (National Stock Exchange) — primary
- BSE (Bombay Stock Exchange) — secondary / cross-check
- Broker: Zerodha (Kite API) — account already exists

US markets deferred to a later phase after the India system is validated.

---

## Success Criteria for Paper Phase

Before going live, paper results should show:
- [ ] Win rate > 50% over at least 50 trades
- [ ] No single position loss > 5% of capital
- [ ] System runs stably for 5+ consecutive days without errors
- [ ] All three modes behave as expected
- [ ] Performance reports generated correctly
