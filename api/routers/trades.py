from fastapi import APIRouter, Depends, Query
import aiosqlite
from api.database import get_db, fetchall

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
async def list_trades(
    session_id: int | None = Query(None),
    db: aiosqlite.Connection = Depends(get_db),
):
    if session_id is not None:
        rows = await fetchall(
            db,
            """
            SELECT t.id, t.session_id, s.name as session_name,
                   t.symbol, t.action, CAST(t.quantity AS REAL) as quantity,
                   t.price, t.stop_loss_price, t.take_profit_price,
                   t.reason, t.confidence, t.executed_at, t.pnl
            FROM trades t
            LEFT JOIN sessions s ON s.id = t.session_id
            WHERE t.session_id = ?
            ORDER BY t.executed_at DESC
            """,
            (session_id,),
        )
    else:
        rows = await fetchall(
            db,
            """
            SELECT t.id, t.session_id, s.name as session_name,
                   t.symbol, t.action, CAST(t.quantity AS REAL) as quantity,
                   t.price, t.stop_loss_price, t.take_profit_price,
                   t.reason, t.confidence, t.executed_at, t.pnl
            FROM trades t
            LEFT JOIN sessions s ON s.id = t.session_id
            ORDER BY t.executed_at DESC
            """,
        )
    return rows
