from fastapi import APIRouter, Depends, Query
import aiosqlite
from api.database import get_db, fetchall

router = APIRouter(prefix="/api/summary", tags=["summary"])


@router.get("")
async def list_summary(
    session_id: int | None = Query(None),
    db: aiosqlite.Connection = Depends(get_db),
):
    if session_id is not None:
        rows = await fetchall(
            db,
            """
            SELECT ds.id, ds.session_id, s.name as session_name,
                   ds.day_number, ds.date, ds.starting_capital,
                   ds.ending_capital, ds.secured_profit, ds.trades_made,
                   ds.wins, ds.losses, ds.total_pnl, ds.summary_text
            FROM daily_summary ds
            LEFT JOIN sessions s ON s.id = ds.session_id
            WHERE ds.session_id = ?
            ORDER BY ds.day_number ASC
            """,
            (session_id,),
        )
    else:
        rows = await fetchall(
            db,
            """
            SELECT ds.id, ds.session_id, s.name as session_name,
                   ds.day_number, ds.date, ds.starting_capital,
                   ds.ending_capital, ds.secured_profit, ds.trades_made,
                   ds.wins, ds.losses, ds.total_pnl, ds.summary_text
            FROM daily_summary ds
            LEFT JOIN sessions s ON s.id = ds.session_id
            ORDER BY ds.session_id ASC, ds.day_number ASC
            """,
        )
    return rows
