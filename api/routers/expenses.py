"""Expenses router — the real cost of running the machine.

Surfaces trading charges, Claude API spend, and subscriptions so the
dashboard can show net profit after everything, not gross P&L.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/expenses", tags=["expenses"])


class ManualExpenseRequest(BaseModel):
    label: str
    amount_inr: float
    category: str = "other"


@router.get("/summary")
async def expense_summary(session_id: Optional[int] = None):
    """Full expense breakdown + net profit after all costs."""
    from aaitrade.costs import expense_summary as _summary
    return _summary(session_id)


@router.get("/api-usage")
async def api_usage(session_id: Optional[int] = None, limit: int = 50):
    """Recent Claude API calls with token counts and cost."""
    from aaitrade import db

    limit = max(1, min(limit, 200))
    if session_id:
        rows = db.query(
            "SELECT * FROM api_usage WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
    else:
        rows = db.query("SELECT * FROM api_usage ORDER BY id DESC LIMIT ?", (limit,))
    return {"calls": rows, "count": len(rows)}


@router.get("/daily")
async def daily_costs(days: int = 14):
    """Per-day API cost and trading charges — for the expense chart."""
    from aaitrade import db

    days = max(1, min(days, 90))
    api_rows = db.query(
        "SELECT substr(created_at, 1, 10) AS day, ROUND(SUM(cost_inr), 2) AS api_cost "
        "FROM api_usage GROUP BY day ORDER BY day DESC LIMIT ?",
        (days,),
    )
    trade_rows = db.query(
        "SELECT substr(executed_at, 1, 10) AS day, ROUND(SUM(charges), 2) AS trading_charges, "
        "COUNT(*) AS trades FROM trades GROUP BY day ORDER BY day DESC LIMIT ?",
        (days,),
    )
    trade_map = {r["day"]: r for r in trade_rows}
    merged = []
    for r in api_rows:
        t = trade_map.pop(r["day"], None)
        merged.append({
            "day": r["day"],
            "api_cost": r["api_cost"] or 0,
            "trading_charges": (t["trading_charges"] if t else 0) or 0,
            "trades": (t["trades"] if t else 0),
        })
    for day, t in trade_map.items():
        merged.append({
            "day": day, "api_cost": 0,
            "trading_charges": t["trading_charges"] or 0, "trades": t["trades"],
        })
    merged.sort(key=lambda x: x["day"], reverse=True)
    return {"days": merged}


@router.get("/list")
async def list_expenses():
    """Subscriptions and manual expense entries."""
    from aaitrade import db
    return {"expenses": db.query("SELECT * FROM expenses ORDER BY id DESC LIMIT 100")}


@router.post("/manual")
async def add_manual_expense(req: ManualExpenseRequest):
    """Record a one-off expense (e.g. an API credit top-up)."""
    from aaitrade import db
    from datetime import datetime, timedelta, timezone

    if req.amount_inr <= 0:
        raise HTTPException(400, "amount_inr must be positive")

    ist = timezone(timedelta(hours=5, minutes=30))
    stamp = datetime.now(ist).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        expense_id = db.insert("expenses", {
            "session_id": None,
            "category": req.category,
            "label": req.label,
            "amount_inr": req.amount_inr,
            "period": stamp,  # unique per entry so repeats are allowed
            "created_at": db.now_iso(),
        })
    except Exception as e:
        raise HTTPException(500, f"Could not record expense: {e}")
    return {"id": expense_id, "label": req.label, "amount_inr": req.amount_inr}
