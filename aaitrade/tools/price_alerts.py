"""Price alert tools — Claude sets/removes/views price alerts.

Claude calls these to tell the price monitor what to watch between
scheduled cycles. When a target is hit, the monitor wakes Claude
for an ad-hoc mini-cycle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from aaitrade.tools import register_tool
from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger(__name__)

_session_id: int | None = None
_cycle_number: int | None = None


def set_alert_context(session_id: int, cycle_number: int):
    """Inject session context — called before each decision cycle."""
    global _session_id, _cycle_number
    _session_id = session_id
    _cycle_number = cycle_number


@register_tool(
    name="set_price_alert",
    description=(
        "Set a price alert that wakes you up between scheduled cycles. "
        "When the stock hits the target price (±margin%), you get an ad-hoc "
        "cycle to act on it immediately — no need to wait for the next "
        "scheduled slot.\n\n"
        "Use this when:\n"
        "- You want to BUY a stock but it hasn't dipped enough yet\n"
        "- You want to SELL at a target profit price\n"
        "- You see a stock approaching a key level and want to react fast\n\n"
        "The alert only fires between scheduled cycles (not within 10 min "
        "of one). You can have multiple alerts active at once."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE symbol (e.g. RELIANCE, SBIN)",
            },
            "target_price": {
                "type": "number",
                "description": "The price level to watch for",
            },
            "direction": {
                "type": "string",
                "enum": ["above", "below"],
                "description": (
                    "'above' = trigger when price >= target (for sell/take-profit). "
                    "'below' = trigger when price <= target (for buy-the-dip)."
                ),
            },
            "margin_pct": {
                "type": "number",
                "description": (
                    "Trigger within this % of the target price. "
                    "E.g. 0.2 means trigger at target ± 0.2%. Default 0.2."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Why you're setting this alert (shown when it triggers)",
            },
        },
        "required": ["symbol", "target_price", "direction", "reason"],
    },
)
def set_price_alert(
    symbol: str,
    target_price: float,
    direction: str,
    reason: str,
    margin_pct: float = 0.2,
) -> dict:
    if not _session_id:
        return {"status": "error", "reason": "Session not initialized"}

    if direction not in ("above", "below"):
        return {"status": "error", "reason": "direction must be 'above' or 'below'"}

    if margin_pct < 0 or margin_pct > 5:
        return {"status": "error", "reason": "margin_pct must be between 0 and 5"}

    # Check for duplicate active alerts on same symbol+direction
    existing = db.query(
        "SELECT id, target_price FROM price_alerts "
        "WHERE session_id = ? AND symbol = ? AND direction = ? AND status = 'active'",
        (_session_id, symbol, direction),
    )
    if existing:
        # Update existing alert instead of creating duplicate
        db.update("price_alerts", existing[0]["id"], {
            "target_price": target_price,
            "margin_pct": margin_pct,
            "reason": reason,
            "cycle_number": _cycle_number,
            "created_at": db.now_iso(),
        })
        return {
            "status": "updated",
            "alert_id": existing[0]["id"],
            "symbol": symbol,
            "target_price": target_price,
            "direction": direction,
            "margin_pct": margin_pct,
            "message": f"Updated existing {direction} alert for {symbol} to ₹{target_price} (±{margin_pct}%)",
        }

    alert_id = db.insert("price_alerts", {
        "session_id": _session_id,
        "symbol": symbol,
        "target_price": target_price,
        "direction": direction,
        "margin_pct": margin_pct,
        "reason": reason,
        "status": "active",
        "created_at": db.now_iso(),
        "cycle_number": _cycle_number,
    })

    return {
        "status": "created",
        "alert_id": alert_id,
        "symbol": symbol,
        "target_price": target_price,
        "direction": direction,
        "margin_pct": margin_pct,
        "message": f"Alert set: wake me when {symbol} goes {direction} ₹{target_price} (±{margin_pct}%)",
    }


@register_tool(
    name="remove_price_alert",
    description=(
        "Remove/cancel an active price alert. Use get_price_alerts first "
        "to see alert IDs. You can also pass symbol to cancel all alerts for that stock."
    ),
    parameters={
        "properties": {
            "alert_id": {
                "type": "integer",
                "description": "Specific alert ID to cancel (from get_price_alerts)",
            },
            "symbol": {
                "type": "string",
                "description": "Cancel ALL active alerts for this symbol. Use instead of alert_id to bulk-cancel.",
            },
        },
        "required": [],
    },
)
def remove_price_alert(
    alert_id: int | None = None,
    symbol: str | None = None,
) -> dict:
    if not _session_id:
        return {"status": "error", "reason": "Session not initialized"}

    if alert_id:
        alert = db.query_one(
            "SELECT id, symbol FROM price_alerts WHERE id = ? AND session_id = ? AND status = 'active'",
            (alert_id, _session_id),
        )
        if not alert:
            return {"status": "error", "reason": f"No active alert with ID {alert_id}"}
        db.update("price_alerts", alert_id, {"status": "cancelled"})
        return {"status": "cancelled", "alert_id": alert_id, "symbol": alert["symbol"]}

    elif symbol:
        alerts = db.query(
            "SELECT id FROM price_alerts WHERE session_id = ? AND symbol = ? AND status = 'active'",
            (_session_id, symbol),
        )
        if not alerts:
            return {"status": "error", "reason": f"No active alerts for {symbol}"}
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE price_alerts SET status = 'cancelled' "
                "WHERE session_id = ? AND symbol = ? AND status = 'active'",
                (_session_id, symbol),
            )
        return {"status": "cancelled", "count": len(alerts), "symbol": symbol}

    return {"status": "error", "reason": "Provide either alert_id or symbol"}


@register_tool(
    name="get_price_alerts",
    description=(
        "View all your active price alerts. Shows what the price monitor "
        "is currently watching for you between cycles."
    ),
    parameters={
        "properties": {
            "include_triggered": {
                "type": "boolean",
                "description": "Also show recently triggered alerts (default false)",
            },
        },
        "required": [],
    },
)
def get_price_alerts(include_triggered: bool = False) -> dict:
    if not _session_id:
        return {"status": "error", "reason": "Session not initialized"}

    if include_triggered:
        alerts = db.query(
            "SELECT id, symbol, target_price, direction, margin_pct, reason, status, "
            "created_at, triggered_at FROM price_alerts "
            "WHERE session_id = ? AND status IN ('active', 'triggered') "
            "ORDER BY created_at DESC",
            (_session_id,),
        )
    else:
        alerts = db.query(
            "SELECT id, symbol, target_price, direction, margin_pct, reason, status, "
            "created_at FROM price_alerts "
            "WHERE session_id = ? AND status = 'active' "
            "ORDER BY created_at DESC",
            (_session_id,),
        )

    return {
        "active_count": sum(1 for a in alerts if a["status"] == "active"),
        "alerts": [dict(a) for a in alerts],
    }
