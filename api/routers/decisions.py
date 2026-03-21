from fastapi import APIRouter, Depends, Query
import aiosqlite
from api.database import get_db, fetchall

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("")
async def list_decisions(
    session_id: int | None = Query(None),
    limit: int = Query(200, le=1000),
    db: aiosqlite.Connection = Depends(get_db),
):
    if session_id is not None:
        rows = await fetchall(
            db,
            """
            SELECT d.id, d.session_id, s.name as session_name,
                   d.cycle_number, d.action, d.symbol,
                   CAST(d.quantity AS REAL) as quantity,
                   d.reason, d.confidence, d.flags, d.raw_json, d.decided_at
            FROM decisions d
            LEFT JOIN sessions s ON s.id = d.session_id
            WHERE d.session_id = ?
            ORDER BY d.decided_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
    else:
        rows = await fetchall(
            db,
            """
            SELECT d.id, d.session_id, s.name as session_name,
                   d.cycle_number, d.action, d.symbol,
                   CAST(d.quantity AS REAL) as quantity,
                   d.reason, d.confidence, d.flags, d.raw_json, d.decided_at
            FROM decisions d
            LEFT JOIN sessions s ON s.id = d.session_id
            ORDER BY d.decided_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    return rows
