from fastapi import APIRouter, Depends, Query
import aiosqlite
from api.database import get_db, fetchall

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    session_id: int | None = Query(None),
    status: str | None = Query(None, description="Filter by status: active, triggered, cancelled"),
    db: aiosqlite.Connection = Depends(get_db),
):
    filters = []
    params = []

    if session_id is not None:
        filters.append("session_id = ?")
        params.append(session_id)

    if status:
        filters.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    rows = await fetchall(
        db,
        f"""
        SELECT id, session_id, symbol, target_price, direction,
               margin_pct, reason, status, created_at, triggered_at, cycle_number
        FROM price_alerts
        {where}
        ORDER BY created_at DESC
        """,
        params,
    )
    return rows
