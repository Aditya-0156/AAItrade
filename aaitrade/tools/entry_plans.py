"""Entry plans — the model decides WHAT to buy; the monitor decides WHEN.

Replaces buy-at-first-touch, which the entry audit showed cost a median 1.28%
per trade (15/15 buys drew down after entry; the low came ~27h later). The
model files a plan against a level; the price monitor stalks it every 30
seconds and fills on either a calibrated discount below the level or a
confirmed defence of it. See aaitrade/entry_engine.py for the trigger logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade.tools import register_tool
from aaitrade import db
from aaitrade.entry_engine import calibrate_discount, DISCOUNT_DEFAULT_PCT
from aaitrade.textclean import clean_model_text

_IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger(__name__)

_session_id: int | None = None
_cycle_number: int | None = None

MAX_VALID_DAYS = 10
DEFAULT_VALID_DAYS = 3
# A level further than this from the current price is a watchlist note,
# not an entry plan — the stalk would sit dead for weeks.
MAX_LEVEL_DISTANCE_PCT = 5.0


def set_session_context(session_id: int, cycle_number: int):
    global _session_id, _cycle_number
    _session_id = session_id
    _cycle_number = cycle_number


@register_tool(
    name="plan_entry",
    description=(
        "File an entry plan instead of buying at market. This is how you buy dips "
        "WITHOUT buying the top of the dip: you pick the stock, the level, and the "
        "size; the price monitor (which watches every 30 seconds, all day) executes "
        "when the level is actually worth buying —\n"
        "- DISCOUNT fill: price overshoots the level by its historically typical "
        "amount (calibrated per stock). You buy the fall near its usual bottom.\n"
        "- CONFIRMED fill: price touches the level, prints a higher low, and "
        "reclaims it — the floor was actually defended, so you buy the evidence.\n"
        "If neither happens the plan expires quietly. A missed entry costs almost "
        "nothing; a 1.3% early entry costs more than a typical win.\n\n"
        "Research rules are the same as a direct buy: you must have researched the "
        "symbol this cycle, and why_now must be your own words. The plan replaces "
        "any earlier active plan for the same symbol."
    ),
    parameters={
        "properties": {
            "symbol": {"type": "string", "description": "NSE symbol"},
            "level": {"type": "number", "description": "The support level to stalk (within 5% of current price)"},
            "quantity": {"type": "integer", "description": "Shares to buy when triggered"},
            "why_now": {
                "type": "string",
                "description": "Your researched case for this trade, in your own words — same bar as a direct buy",
            },
            "stop_loss_price": {"type": "number", "description": "Stop for the position once filled"},
            "take_profit_price": {"type": "number", "description": "Target for the position once filled"},
            "valid_days": {
                "type": "integer",
                "description": f"Days the plan stays live (default {DEFAULT_VALID_DAYS}, max {MAX_VALID_DAYS})",
            },
            "fill_mode": {
                "type": "string",
                "enum": ["split", "single"],
                "description": "split (default): half on confirmation, half on discount. single: all on first trigger.",
            },
        },
        "required": ["symbol", "level", "quantity", "why_now"],
    },
)
def plan_entry(symbol: str, level: float, quantity: int, why_now: str,
               stop_loss_price: float | None = None,
               take_profit_price: float | None = None,
               valid_days: int = DEFAULT_VALID_DAYS,
               fill_mode: str = "split") -> dict:
    if _session_id is None:
        return {"status": "error", "reason": "Session not initialised"}
    symbol = symbol.upper().strip()
    why_now = clean_model_text(why_now)

    if quantity < 1:
        return {"status": "rejected", "reason": "quantity must be at least 1"}
    if level <= 0:
        return {"status": "rejected", "reason": "level must be a positive price"}

    # Same research bar as a direct buy — a plan is a deferred buy, not a
    # way around the homework.
    from aaitrade.tools.trading import _research_gate
    gate = _research_gate(symbol, why_now)
    if gate:
        return gate

    # The level must be near the market — else this is a wish, not a plan.
    try:
        from aaitrade.tools.market import get_current_price
        quote = get_current_price(symbol)
        ltp = quote.get("price") or quote.get("last_price") or 0
    except Exception:
        ltp = 0
    if ltp:
        distance = abs(ltp - level) / ltp * 100
        if distance > MAX_LEVEL_DISTANCE_PCT:
            return {
                "status": "rejected",
                "reason": (
                    f"Level ₹{level} is {distance:.1f}% away from the current price "
                    f"₹{ltp} — too far to stalk. Use the watchlist/alerts for distant "
                    f"levels and file the plan when price approaches."
                ),
            }
        if level > ltp * 1.005:
            return {
                "status": "rejected",
                "reason": (
                    f"Level ₹{level} is ABOVE the current price ₹{ltp}. An entry plan "
                    f"stalks a support below/at the market. If price is already under "
                    f"your level, the level you actually believe in is lower."
                ),
            }

    # Calibrate the discount from this stock's own touch history.
    discount = DISCOUNT_DEFAULT_PCT
    try:
        from aaitrade.tools.market import get_price_history
        hist = get_price_history(symbol, days=30)
        candles = hist.get("candles") or []
        if candles:
            discount = calibrate_discount(candles, level)
    except Exception as e:
        logger.warning(f"Discount calibration failed for {symbol}: {e}")

    # The discount fill must sit above the stop — a fall that deep is the
    # structural break the stop exists for, not an entry.
    if stop_loss_price and stop_loss_price > 0:
        stop_gap_pct = (level - stop_loss_price) / level * 100
        if stop_gap_pct <= 0:
            return {"status": "rejected", "reason": "stop_loss_price must be below the level"}
        discount = min(discount, round(stop_gap_pct / 2, 2))

    valid_days = max(1, min(int(valid_days or DEFAULT_VALID_DAYS), MAX_VALID_DAYS))
    fill_mode = fill_mode if fill_mode in ("split", "single") else "split"

    # One live plan per symbol — a new plan replaces the old thinking.
    for old in db.query(
        "SELECT id FROM entry_plans WHERE session_id = ? AND symbol = ? "
        "AND status IN ('stalking', 'partial')",
        (_session_id, symbol),
    ):
        db.update("entry_plans", old["id"], {
            "status": "cancelled", "resolved_at": db.now_iso(),
        })

    expires = (datetime.now(_IST) + timedelta(days=valid_days)).strftime("%Y-%m-%dT%H:%M:%S")
    plan_id = db.insert("entry_plans", {
        "session_id": _session_id, "symbol": symbol, "level": round(level, 2),
        "quantity": quantity, "discount_pct": discount,
        "stop_loss_price": stop_loss_price, "take_profit_price": take_profit_price,
        "reason": why_now, "status": "stalking", "fill_mode": fill_mode,
        "created_at": db.now_iso(), "expires_at": expires,
        "cycle_number": _cycle_number,
    })
    return {
        "status": "created",
        "plan_id": plan_id,
        "symbol": symbol,
        "level": round(level, 2),
        "discount_fill_at": round(level * (1 - discount / 100), 2),
        "discount_pct": discount,
        "fill_mode": fill_mode,
        "expires": expires[:10],
        "message": (
            f"Stalking {symbol}: {quantity} shares near ₹{level}. Fills on a dip to "
            f"₹{level * (1 - discount / 100):.2f} ({discount}% overshoot, calibrated "
            f"from this stock's history) or on a confirmed defence of the level. "
            f"You will see the outcome in a later briefing — do NOT also buy it directly."
        ),
    }


@register_tool(
    name="get_entry_plans",
    description=(
        "Your live entry plans and recent outcomes — what the monitor is stalking "
        "for you, what filled, what expired untouched. Check this before filing a "
        "new plan or buying anything directly."
    ),
    parameters={"properties": {}, "required": []},
)
def get_entry_plans() -> dict:
    if _session_id is None:
        return {"status": "error", "reason": "Session not initialised"}
    live = db.query(
        "SELECT id, symbol, level, quantity, discount_pct, filled_quantity, status, "
        "touched, expires_at FROM entry_plans WHERE session_id = ? "
        "AND status IN ('stalking', 'partial') ORDER BY created_at DESC",
        (_session_id,),
    )
    recent = db.query(
        "SELECT symbol, level, status, trigger, fill_price, resolved_at FROM entry_plans "
        "WHERE session_id = ? AND status NOT IN ('stalking', 'partial') "
        "ORDER BY resolved_at DESC LIMIT 8",
        (_session_id,),
    )
    return {
        "active": [dict(r) for r in live],
        "recent_outcomes": [dict(r) for r in recent],
        "message": f"{len(live)} plan(s) being stalked." if live else "No active plans.",
    }


@register_tool(
    name="cancel_entry_plan",
    description="Cancel an active entry plan (by plan_id from get_entry_plans, or by symbol) when the thesis no longer holds.",
    parameters={
        "properties": {
            "plan_id": {"type": "integer", "description": "Plan ID to cancel"},
            "symbol": {"type": "string", "description": "Cancel all active plans for this symbol"},
        },
        "required": [],
    },
)
def cancel_entry_plan(plan_id: int | None = None, symbol: str | None = None) -> dict:
    if _session_id is None:
        return {"status": "error", "reason": "Session not initialised"}
    if plan_id:
        row = db.query_one(
            "SELECT id, symbol FROM entry_plans WHERE id = ? AND session_id = ? "
            "AND status IN ('stalking', 'partial')",
            (plan_id, _session_id),
        )
        if not row:
            return {"status": "rejected", "reason": f"No active plan #{plan_id}"}
        db.update("entry_plans", row["id"], {"status": "cancelled", "resolved_at": db.now_iso()})
        return {"status": "cancelled", "symbol": row["symbol"]}
    if symbol:
        symbol = symbol.upper().strip()
        rows = db.query(
            "SELECT id FROM entry_plans WHERE session_id = ? AND symbol = ? "
            "AND status IN ('stalking', 'partial')",
            (_session_id, symbol),
        )
        for r in rows:
            db.update("entry_plans", r["id"], {"status": "cancelled", "resolved_at": db.now_iso()})
        return {"status": "cancelled", "count": len(rows), "symbol": symbol}
    return {"status": "rejected", "reason": "Provide plan_id or symbol"}


def plans_briefing_block(session_id: int) -> str:
    """Compact plan status for the cycle briefing."""
    live = db.query(
        "SELECT symbol, level, quantity, discount_pct, status, filled_quantity, expires_at "
        "FROM entry_plans WHERE session_id = ? AND status IN ('stalking', 'partial')",
        (session_id,),
    )
    resolved = db.query(
        "SELECT symbol, status, trigger, fill_price FROM entry_plans "
        "WHERE session_id = ? AND resolved_at >= datetime('now', '-1 day') "
        "AND status NOT IN ('stalking', 'partial')",
        (session_id,),
    )
    if not live and not resolved:
        return ""
    lines = []
    for p in live:
        part = f" ({p['filled_quantity']}/{p['quantity']} filled)" if p["status"] == "partial" else ""
        lines.append(
            f"  {p['symbol']}: stalking ₹{p['level']} (fills ≤₹{p['level'] * (1 - p['discount_pct'] / 100):.2f} "
            f"or on confirmed defence){part}, until {p['expires_at'][:10]}"
        )
    for p in resolved:
        if p["status"] in ("filled", "partial"):
            lines.append(f"  {p['symbol']}: FILLED at ₹{p['fill_price']} via {p['trigger']} — position is live")
        else:
            lines.append(f"  {p['symbol']}: plan {p['status']} — re-decide if still interested")
    return "ENTRY PLANS (the monitor stalks these for you):\n" + "\n".join(lines)
