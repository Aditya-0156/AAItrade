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
        "Get available cash, secured profit, and current drawdown % for the session. "
        "drawdown_pct = (starting_capital - total_portfolio_value) / starting_capital × 100. "
        "Use this to check your real drawdown before flagging HALT_SESSION."
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

    # current_capital in DB is already FREE CASH (deployed amounts are deducted at BUY time).
    # available_cash = current_capital directly; do NOT subtract deployed again.
    available = session["current_capital"]

    # total_value = free_cash + deployed_at_cost + secured_profit
    total_value = session["current_capital"] + deployed + session["secured_profit"]
    drawdown_pct = round(
        (session["starting_capital"] - total_value) / session["starting_capital"] * 100, 2
    )
    effective_capital = available + deployed  # tradeable pot — grows with reinvested profits
    deployed_pct = round(deployed / effective_capital * 100, 1) if effective_capital else 0
    return {
        "starting_capital": session["starting_capital"],
        "effective_capital": round(effective_capital, 2),  # use this as base for position sizing
        "available_cash": round(available, 2),
        "deployed_capital": round(deployed, 2),
        "deployed_pct": deployed_pct,
        "secured_profit": session["secured_profit"],
        "total_portfolio_value": round(total_value, 2),
        "total_pnl": round(total_value - session["starting_capital"], 2),
        "drawdown_pct": drawdown_pct,
    }
