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

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

_executor = None
_session_id: int | None = None
_cycle_number: int | None = None


def set_trading_context(executor, session_id: int, cycle_number: int):
    """Inject executor + cycle context before each decision cycle."""
    global _executor, _session_id, _cycle_number
    _executor = executor
    _session_id = session_id
    _cycle_number = cycle_number


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
) -> dict:
    if not _executor:
        return {"status": "error", "reason": "Executor not initialized — cannot execute trade"}

    decision = {
        "action": action.upper(),
        "symbol": symbol,
        "quantity": quantity,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "reason": reason,
        "thesis": thesis,
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
