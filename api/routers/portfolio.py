from fastapi import APIRouter, Depends, Query
import aiosqlite
from api.database import get_db, fetchall

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("")
async def list_portfolio(
    session_id: int | None = Query(None),
    db: aiosqlite.Connection = Depends(get_db),
):
    if session_id is not None:
        rows = await fetchall(
            db,
            """
            SELECT p.id, p.session_id, s.name as session_name,
                   p.symbol, CAST(p.quantity AS REAL) as quantity,
                   p.avg_price, p.stop_loss_price, p.take_profit_price, p.opened_at
            FROM portfolio p
            LEFT JOIN sessions s ON s.id = p.session_id
            WHERE p.session_id = ?
            ORDER BY p.opened_at DESC
            """,
            (session_id,),
        )
    else:
        rows = await fetchall(
            db,
            """
            SELECT p.id, p.session_id, s.name as session_name,
                   p.symbol, CAST(p.quantity AS REAL) as quantity,
                   p.avg_price, p.stop_loss_price, p.take_profit_price, p.opened_at
            FROM portfolio p
            LEFT JOIN sessions s ON s.id = p.session_id
            ORDER BY p.opened_at DESC
            """,
        )
    return rows
