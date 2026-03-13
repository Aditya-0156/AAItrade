"""Memory tools — trade history and session summary for Claude."""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

_session_id: int | None = None


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


def _require_session():
    if _session_id is None:
        raise RuntimeError("Session not initialized.")


@register_tool(
    name="get_trade_history",
    description=(
        "Get past trade decisions for a specific stock in the current session. "
        "Shows buy/sell actions with prices, P&L, and reasoning."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of past trades to return (default 5)",
            },
        },
        "required": ["symbol"],
    },
)
def get_trade_history(symbol: str, limit: int = 5) -> dict:
    _require_session()

    trades = db.query(
        "SELECT action, quantity, price, stop_loss_price, take_profit_price, "
        "reason, confidence, pnl, executed_at "
        "FROM trades WHERE session_id = ? AND symbol = ? "
        "ORDER BY executed_at DESC LIMIT ?",
        (_session_id, symbol, limit),
    )

    return {
        "symbol": symbol,
        "trades": trades,
        "total_found": len(trades),
    }


@register_tool(
    name="get_session_summary",
    description=(
        "Get running performance stats for the current session: total trades, "
        "win rate, total P&L, and trades made today."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_session_summary() -> dict:
    _require_session()

    session = db.query_one(
        "SELECT starting_capital, current_capital, secured_profit, current_day, total_days "
        "FROM sessions WHERE id = ?",
        (_session_id,),
    )

    if not session:
        return {"error": "Session not found."}

    # All trades in session
    all_trades = db.query(
        "SELECT pnl FROM trades WHERE session_id = ? AND action = 'SELL'",
        (_session_id,),
    )

    total_trades = len(all_trades)
    wins = sum(1 for t in all_trades if t["pnl"] and t["pnl"] > 0)
    losses = sum(1 for t in all_trades if t["pnl"] and t["pnl"] < 0)

    # Today's trades
    today = db.now_iso()[:10]  # YYYY-MM-DD
    today_trades = db.query(
        "SELECT COUNT(*) as count FROM trades "
        "WHERE session_id = ? AND executed_at LIKE ?",
        (_session_id, f"{today}%"),
    )
    trades_today = today_trades[0]["count"] if today_trades else 0

    # Today's P&L
    today_pnl_rows = db.query(
        "SELECT SUM(pnl) as total FROM trades "
        "WHERE session_id = ? AND action = 'SELL' AND executed_at LIKE ?",
        (_session_id, f"{today}%"),
    )
    today_pnl = today_pnl_rows[0]["total"] if today_pnl_rows and today_pnl_rows[0]["total"] else 0

    total_pnl = session["current_capital"] + session["secured_profit"] - session["starting_capital"]

    return {
        "session_day": f"{session['current_day']} of {session['total_days']}",
        "starting_capital": session["starting_capital"],
        "current_capital": session["current_capital"],
        "secured_profit": session["secured_profit"],
        "total_pnl": round(total_pnl, 2),
        "total_pnl_percent": round((total_pnl / session["starting_capital"]) * 100, 2),
        "total_closed_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / total_trades * 100), 1) if total_trades > 0 else 0,
        "trades_today": trades_today,
        "today_pnl": round(today_pnl, 2),
    }
