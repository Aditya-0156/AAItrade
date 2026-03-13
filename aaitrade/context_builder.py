"""Context builder — assembles the system prompt and per-cycle briefing.

This is the most important orchestration layer. It constructs what Claude
sees at every decision cycle by injecting runtime values into the prompt
templates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from aaitrade import db
from aaitrade.config import SessionConfig
from aaitrade.tools.market import get_current_price, get_market_snapshot

logger = logging.getLogger(__name__)


# ── System Prompt Template ─────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are AAItrade, an autonomous trading agent operating on Indian equity \
markets (NSE). You are the sole decision-making brain of a trading system. \
Your job is to analyse market conditions, reason carefully, and make \
disciplined trade decisions to achieve the session goal.

You do not have access to the internet directly. You gather information \
exclusively through the tools available to you. Use them selectively and \
purposefully — every tool call should have a clear reason.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SESSION STATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Execution mode  : {execution_mode}
Trading mode    : {trading_mode}
Starting capital: ₹{starting_capital:,.2f}
Current capital : ₹{current_capital:,.2f}
Secured profit  : ₹{secured_profit:,.2f}
Session day     : {current_day} of {total_days}
Time            : {current_time} IST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## YOUR MANDATE

{mode_mandate}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RISK RULES — ENFORCED BY CODE AND BY YOU
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Never risk more than {max_per_trade}% of current capital on one trade
2. Every BUY must include a stop-loss at {stop_loss}% below entry price
3. Every BUY must include a take-profit at {take_profit}% above entry price
4. Never hold more than {max_positions} open positions simultaneously
5. Never deploy more than {max_deployed}% of capital at once
6. If today's loss reaches {daily_loss_limit}%, output HOLD only for the rest of the day — include flag: "DAILY_LIMIT_HIT"
7. If total drawdown from starting capital reaches 20%, output HOLD and include flag: "HALT_SESSION"
8. Only trade symbols present on your current watchlist
9. Never trade in the first 15 minutes of market open (before 9:30 AM) or the last 15 minutes (after 3:15 PM) — too volatile
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## YOUR WATCHLIST
{watchlist_text}

## HOW TO MAKE A DECISION EACH CYCLE

Follow this sequence every cycle:

### Step 1 — Review open positions first
For each open position, you will receive its trade journal record showing \
why you bought it and your thesis. Ask yourself:
- Is the original thesis still valid?
- Has any news or price action changed the picture?
- Should I hold, add, or exit?
Call update_thesis(symbol, note) to record your assessment.

### Step 2 — Scan watchlist for new opportunities
Review the market briefing. Identify 1-3 stocks that look interesting. \
For those stocks only, gather deeper information:
- Call get_stock_news(symbol) for recent news
- Call get_indicators(symbol) for technical detail
- Call get_sector_news(sector) if sector context is relevant
- Call search_web(query) if you need specific information not covered above (max 2 search calls per cycle — use sparingly)

### Step 3 — Reason and decide
Think through your best candidate:
- What is the setup? (technical + fundamental + news)
- Does it fit the current mode mandate?
- Does it fit within risk rules given current portfolio?
- What is the thesis — why will this move, and when?

Then make a single decision: BUY, SELL, or HOLD.

Do not trade just to trade. A disciplined HOLD is often the right answer. \
Quality of decisions matters far more than quantity of trades.

### Step 4 — If BUY: write your rationale to the journal
Always call write_trade_rationale() when opening a new position. \
Record exactly why you are buying, what news or data supports it, \
and what would make you exit early (thesis broken conditions).

## WATCHLIST ADJUSTMENT
{watchlist_adjustment_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT — STRICT JSON ONLY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Output exactly one JSON object. No text, explanation, or markdown \
outside the JSON. The system parses your output programmatically.

Standard decision:
{{"action": "BUY" | "SELL" | "HOLD", "symbol": "<NSE symbol>" | null, "quantity": <integer> | null, "stop_loss_price": <float> | null, "take_profit_price": <float> | null, "reason": "<2-4 sentences>", "confidence": "high" | "medium" | "low", "flags": []}}

Flags (add to array as needed): "DAILY_LIMIT_HIT", "HALT_SESSION", "ALERT_USER"

## WHAT YOU NEVER DO
- Output any text outside the JSON object
- Trade a symbol not on your watchlist
- Exceed any hard risk rule even with high confidence
- Make a trade without a clear, articulable thesis
- Call search_web() more than twice per cycle
- Call get_macro_news() — it is pre-fetched and already in your briefing
- Trade in the first or last 15 minutes of market hours
- Guess at prices or quantities — use the data from your tools"""


# ── Briefing Template ──────────────────────────────────────────────────────────

BRIEFING_TEMPLATE = """## MARKET BRIEFING — Cycle {cycle_number}

### Market Snapshot
{market_snapshot}

### Macro News (today)
{macro_news}

### Watchlist Summary
{watchlist_summary}

### Open Positions with Rationale
{open_positions}

### Session Performance
{session_stats}

---
Based on the above, follow your decision sequence and output your decision as JSON."""


class ContextBuilder:
    """Builds the system prompt and per-cycle briefing for Claude."""

    def __init__(self, config: SessionConfig, session_id: int):
        self.config = config
        self.session_id = session_id

    def build_system_prompt(self) -> str:
        """Build the static system prompt with runtime values injected."""
        session = db.query_one(
            "SELECT current_capital, secured_profit, current_day FROM sessions WHERE id = ?",
            (self.session_id,),
        )

        # Build watchlist text
        watchlist_entries = db.query(
            "SELECT symbol, sector, notes FROM watchlist "
            "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol",
            (self.session_id,),
        )
        watchlist_text = "\n".join(
            f"{e['symbol']} | {e['sector'] or 'N/A'} | {e['notes'] or ''}"
            for e in watchlist_entries
        ) or "No stocks in watchlist."

        # Watchlist adjustment block
        if self.config.allow_watchlist_adjustment:
            watchlist_adjustment_block = (
                "At the END of each trading day only (not mid-cycle), you may add or "
                "remove stocks from your watchlist using add_to_watchlist(symbol, reason) "
                "and remove_from_watchlist(symbol, reason). Always provide a specific, "
                "reasoned justification. The system will validate additions — do not "
                "attempt to add illiquid or unknown stocks."
            )
        else:
            watchlist_adjustment_block = (
                "Your watchlist is fixed for this session. Watchlist adjustment tools "
                "are not available."
            )

        rules = self.config.risk_rules
        return SYSTEM_PROMPT_TEMPLATE.format(
            execution_mode=self.config.execution_mode.value.upper(),
            trading_mode=self.config.trading_mode.value.upper(),
            starting_capital=self.config.starting_capital,
            current_capital=session["current_capital"] if session else self.config.starting_capital,
            secured_profit=session["secured_profit"] if session else 0,
            current_day=session["current_day"] if session else 1,
            total_days=self.config.total_days,
            current_time=datetime.now().strftime("%I:%M %p"),
            mode_mandate=self.config.mode_mandate,
            max_per_trade=rules.max_per_trade,
            stop_loss=rules.stop_loss,
            take_profit=rules.take_profit,
            max_positions=rules.max_positions,
            max_deployed=rules.max_deployed,
            daily_loss_limit=rules.daily_loss_limit,
            watchlist_text=watchlist_text,
            watchlist_adjustment_block=watchlist_adjustment_block,
        )

    def build_briefing(self, cycle_number: int) -> str:
        """Build the per-cycle briefing with live data."""

        # Market snapshot
        try:
            snapshot = get_market_snapshot()
            if "error" not in snapshot:
                market_text = (
                    f"Nifty 50: {snapshot['nifty_50']['last_price']} "
                    f"({snapshot['nifty_50']['change_percent']:+.2f}%)\n"
                    f"Bank Nifty: {snapshot['bank_nifty']['last_price']} "
                    f"({snapshot['bank_nifty']['change_percent']:+.2f}%)"
                )
            else:
                market_text = f"Market data unavailable: {snapshot['error']}"
        except Exception as e:
            market_text = f"Market data unavailable: {e}"

        # Macro news (from cache)
        macro_row = db.query_one(
            "SELECT summary FROM news_cache "
            "WHERE category = 'macro' AND key = 'macro' "
            "ORDER BY fetched_at DESC LIMIT 1",
        )
        macro_news = macro_row["summary"] if macro_row else "No macro news available today."

        # Watchlist summary with prices
        watchlist_entries = db.query(
            "SELECT symbol, sector FROM watchlist "
            "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol",
            (self.session_id,),
        )
        watchlist_lines = []
        for entry in watchlist_entries:
            try:
                price_data = get_current_price(entry["symbol"])
                if "error" not in price_data:
                    watchlist_lines.append(
                        f"{entry['symbol']:12s} | ₹{price_data['last_price']:>10,.2f} | "
                        f"{price_data['change_percent']:+.2f}% | "
                        f"Vol: {price_data['volume']:,} | {entry['sector'] or ''}"
                    )
                else:
                    watchlist_lines.append(f"{entry['symbol']:12s} | Price unavailable")
            except Exception:
                watchlist_lines.append(f"{entry['symbol']:12s} | Price unavailable")

        watchlist_summary = "\n".join(watchlist_lines) or "No watchlist data."

        # Open positions with rationale
        from aaitrade.tools.journal import get_open_positions_with_rationale
        positions_data = get_open_positions_with_rationale()

        if positions_data["total"] > 0:
            pos_lines = []
            for p in positions_data["open_positions"]:
                latest_thesis = ""
                if p["thesis_updates"]:
                    latest_thesis = f" | Latest review: {p['thesis_updates'][-1]['note']}"

                pos_lines.append(
                    f"  {p['symbol']} | {p['quantity']} shares @ ₹{p['entry_price']:.2f} | "
                    f"Target: ₹{p['target_price']:.2f} | Stop: ₹{p['stop_price']:.2f}\n"
                    f"    Thesis: {p['key_thesis']}{latest_thesis}"
                )
            open_positions = "\n".join(pos_lines)
        else:
            open_positions = "No open positions."

        # Session stats
        from aaitrade.tools.memory import get_session_summary
        stats = get_session_summary()
        if "error" not in stats:
            session_stats = (
                f"Day {stats['session_day']} | "
                f"Capital: ₹{stats['current_capital']:,.2f} | "
                f"Secured: ₹{stats['secured_profit']:,.2f} | "
                f"Total P&L: ₹{stats['total_pnl']:,.2f} ({stats['total_pnl_percent']:+.1f}%) | "
                f"Win rate: {stats['win_rate']}% ({stats['wins']}W/{stats['losses']}L) | "
                f"Today: {stats['trades_today']} trades, ₹{stats['today_pnl']:,.2f} P&L"
            )
        else:
            session_stats = "Session stats unavailable."

        return BRIEFING_TEMPLATE.format(
            cycle_number=cycle_number,
            market_snapshot=market_text,
            macro_news=macro_news,
            watchlist_summary=watchlist_summary,
            open_positions=open_positions,
            session_stats=session_stats,
        )
