"""Session analysis tool — comprehensive P&L breakdown with trade context.

Gives Claude a full picture of the session's performance: what was bought,
what was sold, what worked, what didn't, and why. Designed for Claude to
call before major decisions so it can learn from the session's history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade import db
from aaitrade.tools import register_tool

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_session_id: int | None = None


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


def _require_session():
    if _session_id is None:
        raise RuntimeError("Session not initialized.")


@register_tool(
    name="get_session_analysis",
    description=(
        "Comprehensive breakdown of this session's performance. "
        "Returns: session overview (capital, total P&L, win/loss stats), "
        "every closed trade with entry reason, exit reason, thesis, and P&L, "
        "every open position with cost basis and days held. "
        "Use this before major decisions to understand what has worked and "
        "what hasn't — learn from the session's own history."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_session_analysis() -> dict:
    _require_session()

    # Session overview
    session = db.query_one(
        "SELECT starting_capital, current_capital, secured_profit, current_day "
        "FROM sessions WHERE id = ?",
        (_session_id,),
    )
    if not session:
        return {"error": "Session not found."}

    # Deployed capital
    deployed_row = db.query_one(
        "SELECT COALESCE(SUM(quantity * avg_price), 0) as deployed "
        "FROM portfolio WHERE session_id = ?",
        (_session_id,),
    )
    deployed = deployed_row["deployed"] if deployed_row else 0
    total_value = session["current_capital"] + deployed + session["secured_profit"]
    total_pnl = total_value - session["starting_capital"]

    # ── Closed trades with full context ──────────────────────────────────
    closed_trades = db.query(
        "SELECT symbol, entry_price, exit_price, reason, key_thesis, "
        "exit_reason, pnl, opened_at, closed_at "
        "FROM trade_journal WHERE session_id = ? AND status = 'closed' "
        "ORDER BY closed_at DESC",
        (_session_id,),
    )

    closed_analysis = []
    total_realized = 0
    wins = 0
    losses = 0

    for t in closed_trades:
        pnl = t["pnl"] or 0
        total_realized += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1

        pnl_pct = None
        if t["entry_price"] and t["exit_price"]:
            pnl_pct = round(
                (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 100, 2
            )

        closed_analysis.append({
            "symbol": t["symbol"],
            "entry_price": t["entry_price"],
            "exit_price": t["exit_price"],
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "outcome": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN"),
            "entry_reason": t["reason"],
            "entry_thesis": t["key_thesis"],
            "exit_reason": t["exit_reason"],
            "held_from": t["opened_at"],
            "held_until": t["closed_at"],
        })

    # ── Open positions with cost basis and days held ─────────────────────
    open_positions = db.query(
        "SELECT p.symbol, p.quantity, p.avg_price, p.opened_at, "
        "j.key_thesis, j.target_price, j.stop_price, j.reason "
        "FROM portfolio p "
        "LEFT JOIN trade_journal j ON j.session_id = p.session_id "
        "AND j.symbol = p.symbol AND j.status = 'open' "
        "WHERE p.session_id = ?",
        (_session_id,),
    )

    open_analysis = []
    total_cost_basis = 0
    now_ist = datetime.now(_IST)

    for pos in open_positions:
        cost = pos["quantity"] * pos["avg_price"]
        total_cost_basis += cost

        try:
            opened = datetime.fromisoformat(pos["opened_at"]).replace(tzinfo=_IST)
            days_held = (now_ist - opened).days + 1
        except Exception:
            days_held = None

        open_analysis.append({
            "symbol": pos["symbol"],
            "quantity": pos["quantity"],
            "avg_price": round(pos["avg_price"], 2),
            "cost_basis": round(cost, 2),
            "days_held": days_held,
            "entry_reason": pos["reason"],
            "entry_thesis": pos["key_thesis"],
            "target_price": pos["target_price"],
            "stop_price": pos["stop_price"],
        })

    # ── Summary stats ────────────────────────────────────────────────────
    avg_win = (
        round(sum(t["pnl"] for t in closed_analysis if t["pnl"] > 0) / wins, 2)
        if wins else 0
    )
    avg_loss = (
        round(sum(t["pnl"] for t in closed_analysis if t["pnl"] < 0) / losses, 2)
        if losses else 0
    )

    return {
        "session_overview": {
            "starting_capital": session["starting_capital"],
            "free_cash": round(session["current_capital"], 2),
            "deployed_capital": round(deployed, 2),
            "secured_profit": round(session["secured_profit"], 2),
            "total_portfolio_value": round(total_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(
                total_pnl / session["starting_capital"] * 100, 2
            ),
            "day": session["current_day"],
        },
        "closed_trades": {
            "total": len(closed_analysis),
            "wins": wins,
            "losses": losses,
            "win_rate": (
                round(wins / len(closed_analysis) * 100, 1)
                if closed_analysis else 0
            ),
            "total_realized_pnl": round(total_realized, 2),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "trades": closed_analysis,
        },
        "open_positions": {
            "total": len(open_analysis),
            "total_cost_basis": round(total_cost_basis, 2),
            "positions": open_analysis,
        },
    }
