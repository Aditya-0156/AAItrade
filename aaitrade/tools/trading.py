"""Trade execution tool — Claude calls this to execute BUY/SELL during its reasoning.

Claude calls execute_trade() directly and gets the result immediately:
- success → trade is confirmed, DB updated
- rejected → reason + correct max quantity given so Claude can retry right away

This replaces the old pattern of Claude outputting JSON decisions that Python
then executed after the conversation ended.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from aaitrade.tools import register_tool
from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

_executor = None
_session_id: int | None = None
_cycle_number: int | None = None
_alert_mode: bool = False  # True during ad-hoc alert-triggered cycles — bypasses the 9:30 slot trade block


GOAL_MARGIN_LOW = 1.0    # % — the usual profit band per trade
GOAL_MARGIN_HIGH = 2.0   # % — beyond this is allowed, but must be earned
STRONG_EVIDENCE_CHARS = 80  # evidence bar for pushing past the usual band


@register_tool(
    name="update_position_targets",
    description=(
        "Move the stop-loss and/or take-profit on a position you already hold. "
        "This is how you let a winner run WITHOUT giving the gain back: raise "
        "the target only while also raising the stop behind it.\n\n"
        "Rules enforced by the system:\n"
        "- The stop can only move UP, never down. Widening a stop to avoid "
        "being stopped out is how small losses become large ones.\n"
        "- Extending the target REQUIRES raising the stop to at least breakeven "
        "in the same call. An unprotected runner is not a plan.\n"
        f"- The usual profit band is {GOAL_MARGIN_LOW:.0f}-{GOAL_MARGIN_HIGH:.0f}% per trade. "
        f"You MAY go beyond it, but the evidence bar rises with the ambition — a bigger "
        f"target is a bigger claim and needs specific proof the move continues.\n"
        "- `evidence` must be specific and factual (volume surge, sector "
        "breakout, catalyst, next level clearly open). Restating metrics is "
        "rejected. If you have no evidence, sell at your target instead."
    ),
    parameters={
        "properties": {
            "symbol": {"type": "string", "description": "NSE symbol of a position you hold"},
            "take_profit_price": {
                "type": "number",
                "description": "New take-profit level. Raise to let a winner run; lower to bank sooner.",
            },
            "stop_loss_price": {
                "type": "number",
                "description": "New stop level. May only move UP. Required when extending the target.",
            },
            "evidence": {
                "type": "string",
                "description": (
                    "Specific, factual reason for the change — what you observed that "
                    "says this move continues. Not metrics you already reported."
                ),
            },
        },
        "required": ["symbol", "evidence"],
    },
)
def update_position_targets(symbol: str, evidence: str,
                            take_profit_price: float | None = None,
                            stop_loss_price: float | None = None) -> dict:
    symbol = symbol.upper().strip()
    if take_profit_price is None and stop_loss_price is None:
        return {"status": "rejected", "reason": "Provide take_profit_price and/or stop_loss_price."}

    pos = db.query_one(
        "SELECT id, quantity, avg_price, stop_loss_price, take_profit_price "
        "FROM portfolio WHERE session_id = ? AND symbol = ?",
        (_session_id, symbol),
    )
    if not pos:
        return {"status": "rejected", "reason": f"You have no position in {symbol}."}

    entry = pos["avg_price"]
    old_stop, old_target = pos["stop_loss_price"], pos["take_profit_price"]

    # Evidence quality — same bar as why_now on a buy
    text = (evidence or "").strip()
    if len(text) < 30:
        return {
            "status": "rejected",
            "reason": (
                "Evidence is too thin. Name what you actually observed that says this "
                "move continues — volume, a catalyst, the next level being open. "
                "Without it, sell at your existing target."
            ),
        }
    if sum(1 for p in _TEMPLATE_PHRASES if p in text.lower()) >= 2:
        return {
            "status": "rejected",
            "reason": (
                "Evidence just restates the metrics. Those describe where the price has "
                "been, not why it keeps going. Give a real observation or take the profit."
            ),
        }

    updates: dict = {}

    # ── Stop: may only ratchet up ──
    if stop_loss_price is not None:
        if old_stop is not None and stop_loss_price < old_stop:
            return {
                "status": "rejected",
                "reason": (
                    f"Stop can only move UP. Yours is ₹{old_stop}; you asked for "
                    f"₹{stop_loss_price}. Widening a stop to avoid being stopped out is "
                    f"how a small loss becomes a large one. If the thesis is dead, SELL."
                ),
            }
        updates["stop_loss_price"] = round(stop_loss_price, 2)

    # ── Target ──
    if take_profit_price is not None:
        target_pct = (take_profit_price - entry) / entry * 100
        extending = old_target is not None and take_profit_price > old_target

        # Above the usual 1-2% band is permitted — but the evidence bar rises
        # with the ambition. A bigger target is a bigger claim.
        if target_pct > GOAL_MARGIN_HIGH and len(text) < STRONG_EVIDENCE_CHARS:
            return {
                "status": "rejected",
                "reason": (
                    f"₹{take_profit_price} is {target_pct:.1f}% above your entry ₹{entry:.2f}, "
                    f"beyond the usual {GOAL_MARGIN_LOW:.0f}-{GOAL_MARGIN_HIGH:.0f}% band. That "
                    f"is allowed, but it needs real evidence — what specifically says this runs "
                    f"further? Volume expansion, a breakout above a long-held level, a fresh "
                    f"catalyst, sector-wide strength. Give the detail, or take the profit at "
                    f"your current target."
                ),
            }

        if extending:
            # Letting it run is only allowed WITH protection
            effective_stop = updates.get("stop_loss_price", old_stop)
            if effective_stop is None or effective_stop < entry:
                return {
                    "status": "rejected",
                    "reason": (
                        f"To extend the target you must also raise the stop to at least "
                        f"breakeven (₹{entry:.2f}) in the same call. Currently "
                        f"₹{effective_stop if effective_stop else 'none'}. A runner without "
                        f"protection is how a win round-trips to a loss."
                    ),
                }
        updates["take_profit_price"] = round(take_profit_price, 2)

    db.update("portfolio", pos["id"], updates)

    note = ""
    if "take_profit_price" in updates:
        pct = (updates["take_profit_price"] - entry) / entry * 100
        if pct > GOAL_MARGIN_HIGH:
            note = (f" NOTE: {pct:.1f}% is above the usual {GOAL_MARGIN_LOW:.0f}-"
                    f"{GOAL_MARGIN_HIGH:.0f}% band — you have justified it, now manage it. "
                    f"Ratchet the stop up as it advances and take the profit the moment "
                    f"the momentum you cited fades.")

    logger.info(f"{symbol} targets updated: {updates} | evidence: {text[:120]}")
    return {
        "status": "updated",
        "symbol": symbol,
        "entry_price": entry,
        "stop_loss_price": updates.get("stop_loss_price", old_stop),
        "take_profit_price": updates.get("take_profit_price", old_target),
        "previous": {"stop_loss_price": old_stop, "take_profit_price": old_target},
        "message": f"Targets updated for {symbol}.{note}",
    }


_TEMPLATE_PHRASES = (
    "touches in 30d", "band position", "scanner rank", "direction changes",
    "demonstrated floor", "demonstrated resistance", "after charges",
)


def _research_gate(symbol: str, why_now: str) -> dict | None:
    """Block a BUY that hasn't been researched. Returns a rejection or None.

    Gate 1 — news was actually fetched for THIS symbol in THIS cycle.
    Gate 2 — why_now exists, is substantive, and isn't just the numbers again.
    """
    # Gate 1: did the agent look at the company's news this cycle?
    try:
        checked = db.query_one(
            "SELECT 1 FROM tool_calls WHERE session_id = ? AND cycle_number = ? "
            "AND tool_name IN ('get_stock_news', 'search_web', 'get_sector_news', 'get_fundamentals') "
            "AND parameters LIKE ?",
            (_session_id, _cycle_number, f'%"{symbol}"%'),
        )
        if not checked:
            return {
                "status": "rejected",
                "reason": (
                    f"BLOCKED: you have not researched {symbol} this cycle. Numbers alone "
                    f"are not a trade. Call get_stock_news('{symbol}') — and search_web or "
                    f"get_fundamentals if the news is unclear — to find out WHY it is at "
                    f"this price. A stock can look perfect on the chart and be falling for "
                    f"a reason the chart cannot show. Then retry."
                ),
            }
    except Exception as e:
        logger.warning(f"Research gate check failed for {symbol}: {e}")

    # Gate 2: is there a real qualitative story?
    text = (why_now or "").strip()
    if len(text) < 40:
        return {
            "status": "rejected",
            "reason": (
                f"BLOCKED: why_now is missing or too thin for {symbol}. In your own words: "
                f"why is this stock cheap TODAY, and why is that temporary? Name the cause "
                f"(sector selloff / market-wide dip / earnings reaction / policy headline / "
                f"no news) and why it reverses. This is the judgment the numbers cannot make."
            ),
        }
    stripped = text.lower()
    hits = sum(1 for p in _TEMPLATE_PHRASES if p in stripped)
    if hits >= 2:
        return {
            "status": "rejected",
            "reason": (
                f"BLOCKED: why_now for {symbol} just restates the metrics ({hits} numeric "
                f"phrases). The touch counts and band position are already recorded — they "
                f"are not a reason. Explain the SITUATION: what happened to this company or "
                f"its sector that put the price here, and why the market will re-rate it."
            ),
        }
    return None


def set_trading_context(executor, session_id: int, cycle_number: int, alert_mode: bool = False):
    """Inject executor + cycle context before each decision cycle.

    alert_mode=True when this cycle was triggered by a price alert. The pre-11:00
    AM observe-only block is skipped in alert mode — the whole point of alerts is
    to act immediately on a price target hit, including in the morning slot.
    """
    global _executor, _session_id, _cycle_number, _alert_mode
    _executor = executor
    _session_id = session_id
    _cycle_number = cycle_number
    _alert_mode = alert_mode


@register_tool(
    name="execute_trade",
    description=(
        "Execute a BUY or SELL trade. The result is returned immediately — "
        "if rejected, the reason includes the corrected parameters so you can "
        "retry in the same cycle. Use this for ALL BUY and SELL decisions. "
        "Do NOT put BUY/SELL in the final JSON — only HOLD goes there.\n\n"
        "On success: returns executed price, quantity, and trade value.\n"
        "On rejection: returns the reason and (for size errors) the maximum "
        "allowed quantity so you can call again with the correct quantity."
    ),
    parameters={
        "properties": {
            "action": {
                "type": "string",
                "enum": ["BUY", "SELL"],
                "description": "BUY to open a position, SELL to close one",
            },
            "symbol": {
                "type": "string",
                "description": "NSE symbol (e.g. RELIANCE, SBIN)",
            },
            "quantity": {
                "type": "integer",
                "description": "Whole number of shares. For BUY: use floor(max_trade_value / price) as upper bound.",
            },
            "stop_loss_price": {
                "type": "number",
                "description": "Stop-loss price for BUY. Required unless stop_loss rule is 0.",
            },
            "take_profit_price": {
                "type": "number",
                "description": "Take-profit price for BUY. Required unless take_profit rule is 0.",
            },
            "reason": {
                "type": "string",
                "description": "Why you are making this trade (2-4 sentences).",
            },
            "why_now": {
                "type": "string",
                "description": (
                    "REQUIRED for BUY. The NON-NUMERIC story, in your own words: WHY is "
                    "this stock available at this price today, and why will that reverse? "
                    "Name the actual cause (sector selloff, broad market dip, an earnings "
                    "reaction, a policy headline, no news at all) and say why it is "
                    "temporary rather than structural. Do NOT restate touch counts, band "
                    "position, or scanner score — those are already recorded. If you "
                    "cannot explain why the price is where it is, you are not ready to buy."
                ),
            },
            "thesis": {
                "type": "string",
                "description": "For BUY: what must happen for this trade to work. Omit for SELL.",
            },
        },
        "required": ["action", "symbol", "quantity", "reason"],
    },
)
def execute_trade(
    action: str,
    symbol: str,
    quantity: int,
    reason: str,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
    thesis: str = "",
    why_now: str = "",
) -> dict:
    if not _executor:
        return {"status": "error", "reason": "Executor not initialized — cannot execute trade"}

    # ── RESEARCH GATES (BUY only) ──────────────────────────────────────────
    # Numbers alone are not a trading decision. Before committing capital you
    # must have looked at what is happening to the company, and be able to say
    # why the price is where it is. Both are enforced, not merely requested.
    if action.upper() == "BUY":
        gate = _research_gate(symbol, why_now)
        if gate:
            return gate

    # Hard block: no trades during the 9:30 AM slot (before 11:00 AM IST)
    # Based on clock time, not cycle_count — so server restarts don't re-trigger this block.
    # Alert-triggered cycles bypass this block — the whole point of alerts is to act on a
    # price target the moment it hits, including in the morning slot.
    now_ist = datetime.now(_IST)
    if not _alert_mode and now_ist.hour in (9, 10):
        return {
            "status": "rejected",
            "reason": (
                "The 9:30 AM market open slot is observe-only (before 11:00 AM IST). "
                "Market open is volatile and misleading — use this time to research, "
                "scan indicators, read news, and build your plan. Trade from 11:00 AM onwards."
            ),
        }

    # Hard block: last 15 minutes of market (after 3:15 PM IST). This rule was
    # previously prompt-only — now enforced. EOD stop-loss exits bypass this
    # (they call executor.execute directly, not this tool).
    if now_ist.hour > 15 or (now_ist.hour == 15 and now_ist.minute >= 15):
        return {
            "status": "rejected",
            "reason": (
                "No trades in the last 15 minutes of market (after 3:15 PM IST). "
                "Closing auction volatility gives bad fills. Set a price alert "
                "for tomorrow instead."
            ),
        }

    decision = {
        "action": action.upper(),
        "symbol": symbol,
        "quantity": quantity,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "reason": (f"{reason} | WHY NOW: {why_now}" if why_now else reason),
        "thesis": thesis or why_now,
        "confidence": "high",
        "flags": [],
    }

    result = _executor.execute(decision)
    status = result.get("status")

    # Log to decisions table
    db.insert("decisions", {
        "session_id": _session_id,
        "cycle_number": _cycle_number,
        "action": action.upper() if status == "executed" else "TRADE_FAILED",
        "symbol": symbol,
        "quantity": quantity,
        "reason": reason if status == "executed" else f"[{status.upper()}] {result.get('reason', '')}",
        "confidence": "high",
        "flags": json.dumps(["TRADE_FAILED"] if status in ("rejected", "error") else []),
        "raw_json": json.dumps(decision),
        "decided_at": db.now_iso(),
    })

    if status == "executed":
        logger.info(f"execute_trade: {action.upper()} {symbol} x{result.get('quantity')} @ ₹{result.get('price')} executed")
        # Send Telegram alert
        try:
            from aaitrade.telegram_bot import get_bot
            bot = get_bot()
            if bot:
                bot.send_trade_alert(
                    action=action.upper(),
                    symbol=symbol,
                    quantity=result.get("quantity", quantity),
                    price=result.get("price", 0),
                    reason=reason,
                    pnl=result.get("pnl"),
                    mode=result.get("mode", "paper"),
                )
        except Exception:
            pass
    else:
        logger.warning(f"execute_trade: {action.upper()} {symbol} x{quantity} {status}: {result.get('reason')}")
        # Send Telegram rejection alert
        try:
            from aaitrade.telegram_bot import get_bot
            bot = get_bot()
            if bot:
                bot.send(
                    f"⚠️ *{action.upper()} Rejected*\n"
                    f"{symbol} ×{quantity}\n"
                    f"Reason: {result.get('reason', 'Unknown')}",
                    parse_mode=None,
                )
        except Exception:
            pass

    return result
