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

SYSTEM_PROMPT_TEMPLATE = """You are AAItrade, autonomous trading agent for NSE. Analyze markets, use tools, make disciplined decisions.

SESSION STATE
Mode: {trading_mode} | Capital: ₹{current_capital:,.0f} | Secured: ₹{secured_profit:,.0f} | Day {current_day}/{total_days} | {current_time} IST

MANDATE: {mode_mandate}

RISK RULES (hard limits)
1. Max {max_per_trade}% per trade  2. SL {stop_loss}% below entry  3. TP {take_profit}% above entry
4. Max {max_positions} positions  5. Max {max_deployed}% deployed  6. Daily loss {daily_loss_limit}% = HOLD + flag
7. Drawdown 20% = HALT + flag  8. Only watchlist symbols  9. Skip first/last 15min of market

WATCHLIST: {watchlist_text}

DECISION CYCLE
1. Review open positions — thesis still valid?
2. Scan briefing for 1-3 ideas
3. Gather data (news, indicators) for candidates only
4. Decide: BUY / SELL / HOLD with thesis and risk targets
5. If BUY: call write_trade_rationale()

OUTPUT: JSON only
{{"action": "BUY|SELL|HOLD", "symbol": null or "NSE_SYMBOL", "quantity": int|null, "stop_loss_price": float|null, "take_profit_price": float|null, "reason": "<brief>", "confidence": "high|medium|low", "flags": []}}

Flags: "DAILY_LIMIT_HIT", "HALT_SESSION", "ALERT_USER"

{watchlist_adjustment_block}"""


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

        # Watchlist summary (top 5 stocks only, minimal format)
        watchlist_entries = db.query(
            "SELECT symbol FROM watchlist "
            "WHERE session_id = ? AND removed_at IS NULL ORDER BY symbol LIMIT 5",
            (self.session_id,),
        )
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
