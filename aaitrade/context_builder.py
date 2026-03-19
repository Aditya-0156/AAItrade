"""Context builder — assembles the system prompt and per-cycle briefing.

This is the most important orchestration layer. It constructs what Claude
sees at every decision cycle by injecting runtime values into the prompt
templates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

from aaitrade import db
from aaitrade.config import SessionConfig
from aaitrade.tools.market import get_current_price, get_market_snapshot

logger = logging.getLogger(__name__)


# ── System Prompt Template ─────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """You are AAItrade, autonomous trading agent for Indian markets (NSE). Your goal: MAXIMIZE PROFIT by actively deploying capital into high-conviction trades.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SESSION STATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mode: {trading_mode} | Free cash: ₹{current_capital:,.0f} | Secured: ₹{secured_profit:,.0f}
Day {current_day}/{total_days} | {current_time} IST

YOUR MANDATE
{mode_mandate}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RISK RULES (enforce always)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Max {max_per_trade}% of capital per trade
2. Every BUY: stop-loss {stop_loss}% below entry, take-profit {take_profit}% above
3. Max {max_positions} open positions
4. Max {max_deployed}% total deployed capital
5. Daily loss hits {daily_loss_limit}% → HOLD only (flag: DAILY_LIMIT_HIT)
6. Total drawdown hits {session_stop_loss}% → halt session (flag: HALT_SESSION)
7. Only trade symbols on your watchlist
8. Never trade first 15min (before 9:30 AM) or last 15min (after 3:15 PM) of market

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOUR WATCHLIST
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{watchlist_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAPITAL DEPLOYMENT — CRITICAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YOU MUST ACTIVELY DEPLOY CAPITAL. Cash sitting idle is a FAILURE.

EVERY CYCLE, you must:
1. Check how much free cash you have vs total capital
2. If free cash > 40% of total capital → you MUST find new trades to deploy into
3. Scan at least 5-8 DIFFERENT stocks from your watchlist each cycle (not just the ones you already own)
4. BUY multiple stocks in a single cycle if setups exist — do NOT limit yourself to 1 trade per cycle
5. Use get_indicators() on stocks you haven't checked recently to find new setups

DO NOT:
- Keep researching the same 2-3 stocks every cycle — spread your analysis across the full watchlist
- Hold excessive cash "waiting for perfect setups" — good enough setups are better than idle cash
- Let previous session memory about "halt" or "drawdown" stop you from trading — the system enforces hard limits automatically, you just trade

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRADING MINDSET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You are a sophisticated swing trader. Think in setups, not headlines.

NEVER do this:
- Buy simply because news is positive — news is already priced in
- Sell simply because news is negative — ask: does this break the thesis?
- Chase a stock that has already moved 5%+ today
- Panic-sell on a red day if the thesis is unchanged
- Refuse to trade because of "drawdown" — let the system manage risk limits, you find opportunities

ALWAYS ask:
- What is the setup? Entry, stop, target must make sense BEFORE you enter
- What would prove this trade wrong?
- Is potential gain at least 1.5x the potential loss?

PROVEN STRATEGIES:
1. Oversold Bounce — RSI below 35 + stock down 10%+ from recent high + fundamentals intact → buy the dip targeting mean reversion
2. Breakout on Volume — Stock consolidating near resistance, breaks above on above-average volume → buy the breakout
3. Sector Rotation — Macro event favors a sector → find the strongest stock in that sector that has NOT yet moved

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCHEDULE & RHYTHM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You run 2 cycles per trading day:
  Cycle 1: ~9:30 AM IST (market open — assess, plan, enter positions)
  Cycle 2: ~12:30 PM IST (midday — review, adjust, take profits or cut losses)
On the LAST day (Day {total_days}): open positions are NOT auto-sold — valued at market price for final P&L.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DECISION PROCESS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Call get_session_memory() to recall what you were watching and goals from last cycle.
2. Review open positions: is thesis still valid? Call update_thesis(symbol, note).
3. Check your FREE CASH — if > 40% of capital, you MUST scan for new trades.
4. Scan at least 5-8 stocks using get_indicators() and get_current_price() — pick different stocks each cycle, cover the full watchlist over multiple cycles.
5. For promising setups: gather news (get_stock_news), price history (get_price_history).
6. Make decisions: BUY/SELL multiple stocks per cycle. Output one JSON object per decision.
7. If BUY: call write_trade_rationale() with entry, stop, target, thesis.
8. Call update_session_memory() with observations, decisions, next-cycle goals, stocks to scan next. Max 2400 chars.

{watchlist_adjustment_block}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT (strict JSON array format)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Output a JSON array with one object per decision. If no trades, output a single HOLD. Examples:

One HOLD: [{{"action": "HOLD", "symbol": null, "quantity": null, "stop_loss_price": null, "take_profit_price": null, "reason": "<why>", "confidence": "low", "flags": []}}]

Multiple trades: [{{"action": "BUY", "symbol": "RELIANCE", "quantity": 5, "stop_loss_price": 2800.0, "take_profit_price": 3100.0, "reason": "<thesis>", "confidence": "high", "flags": []}}, {{"action": "SELL", "symbol": "TCS", "quantity": 10, "stop_loss_price": null, "take_profit_price": null, "reason": "<why selling>", "confidence": "high", "flags": []}}]

Flags: "DAILY_LIMIT_HIT", "HALT_SESSION", "ALERT_USER"

Output JSON array only — no markdown, explanation, or text outside the array."""


# ── Briefing Template ──────────────────────────────────────────────────────────

BRIEFING_TEMPLATE = """BRIEFING — Cycle {cycle_number}

Market: {market_snapshot}

News: {macro_news}

Watchlist: {watchlist_summary}

Holdings: {open_positions}

Stats: {session_stats}

Decide."""


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
            current_time=datetime.now(_IST).strftime("%I:%M %p IST"),
            mode_mandate=self.config.mode_mandate,
            max_per_trade=rules.max_per_trade,
            stop_loss=rules.stop_loss,
            take_profit=rules.take_profit,
            max_positions=rules.max_positions,
            max_deployed=rules.max_deployed,
            daily_loss_limit=rules.daily_loss_limit,
            session_stop_loss=rules.session_stop_loss,
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

        # Watchlist summary (top 10 stocks, rotating by cycle to cover full list)
        watchlist_entries = db.query(
            "SELECT symbol FROM watchlist "
            "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol",
            (self.session_id,),
        )
        # Rotate: show different stocks each cycle so Claude scans the full watchlist
        if watchlist_entries and cycle_number > 0:
            offset = ((cycle_number - 1) * 10) % max(len(watchlist_entries), 1)
            watchlist_entries = watchlist_entries[offset:offset + 10]
            if len(watchlist_entries) < 10:
                remaining = 10 - len(watchlist_entries)
                all_entries = db.query(
                    "SELECT symbol FROM watchlist "
                    "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol",
                    (self.session_id,),
                )
                watchlist_entries += all_entries[:remaining]
        watchlist_lines = []
        for entry in watchlist_entries:
            try:
                price_data = get_current_price(entry["symbol"])
                if "error" not in price_data:
                    watchlist_lines.append(
                        f"{entry['symbol']} ₹{price_data['last_price']:,.0f} {price_data['change_percent']:+.1f}%"
                    )
                else:
                    watchlist_lines.append(f"{entry['symbol']} N/A")
            except Exception:
                watchlist_lines.append(f"{entry['symbol']} N/A")

        watchlist_summary = " | ".join(watchlist_lines) or "No watchlist data."

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

        # Session stats (compact format)
        from aaitrade.tools.memory import get_session_summary
        stats = get_session_summary()
        if "error" not in stats:
            session_stats = (
                f"Capital ₹{stats['current_capital']:,.0f} | "
                f"P&L {stats['total_pnl_percent']:+.1f}% | "
                f"W/L {stats['wins']}W/{stats['losses']}L | "
                f"Today ₹{stats['today_pnl']:,.0f}"
            )
        else:
            session_stats = "N/A"

        return BRIEFING_TEMPLATE.format(
            cycle_number=cycle_number,
            market_snapshot=market_text,
            macro_news=macro_news,
            watchlist_summary=watchlist_summary,
            open_positions=open_positions,
            session_stats=session_stats,
        )
