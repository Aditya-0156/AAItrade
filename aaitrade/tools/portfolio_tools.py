"""Portfolio tools — holdings, cash, and P&L queries for Claude."""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

# Session ID is set at startup
_session_id: int | None = None


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


def _require_session():
    if _session_id is None:
        raise RuntimeError("Session not initialized.")


@register_tool(
    name="get_portfolio",
    description=(
        "Get all current holdings in the portfolio with average buy price, "
        "current value, and unrealised P&L for each position."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_portfolio() -> dict:
    _require_session()

    positions = db.query(
        "SELECT symbol, quantity, avg_price, stop_loss_price, take_profit_price, opened_at "
        "FROM portfolio WHERE session_id = ?",
        (_session_id,),
    )

    if not positions:
        return {"positions": [], "message": "No open positions."}

    return {
        "positions": [
            {
                "symbol": p["symbol"],
                "quantity": p["quantity"],
                "avg_price": p["avg_price"],
                "stop_loss": p["stop_loss_price"],
                "take_profit": p["take_profit_price"],
                "opened_at": p["opened_at"],
            }
            for p in positions
        ],
        "total_positions": len(positions),
    }


@register_tool(
    name="get_cash",
    description=(
        "Get available cash and secured profit breakdown for the current session."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_cash() -> dict:
    _require_session()

    session = db.query_one(
        "SELECT current_capital, secured_profit, starting_capital FROM sessions WHERE id = ?",
        (_session_id,),
    )

    if not session:
        return {"error": "Session not found."}

    # Calculate deployed capital
    positions = db.query(
        "SELECT SUM(quantity * avg_price) as deployed FROM portfolio WHERE session_id = ?",
        (_session_id,),
    )
    deployed = positions[0]["deployed"] if positions and positions[0]["deployed"] else 0

    available = session["current_capital"] - deployed

    return {
        "starting_capital": session["starting_capital"],
        "current_capital": session["current_capital"],
        "available_cash": round(available, 2),
        "deployed_capital": round(deployed, 2),
        "secured_profit": session["secured_profit"],
        "total_pnl": round(session["current_capital"] + session["secured_profit"] - session["starting_capital"], 2),
    }
