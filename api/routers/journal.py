from fastapi import APIRouter, Depends, Query, HTTPException
import aiosqlite
from api.database import get_db, fetchall, fetchone

router = APIRouter(tags=["journal"])


@router.get("/api/journal")
async def list_journal(
    session_id: int | None = Query(None),
    db: aiosqlite.Connection = Depends(get_db),
):
    if session_id is not None:
        rows = await fetchall(
            db,
            """
            SELECT j.id, j.session_id, s.name as session_name,
                   j.symbol, j.entry_price, j.reason, j.news_cited,
                   j.key_thesis, j.target_price, j.stop_price, j.status,
                   j.opened_at, j.closed_at, j.exit_reason, j.exit_price, j.pnl
            FROM trade_journal j
            LEFT JOIN sessions s ON s.id = j.session_id
            WHERE j.session_id = ?
            ORDER BY j.opened_at DESC
            """,
            (session_id,),
        )
    else:
        rows = await fetchall(
            db,
            """
            SELECT j.id, j.session_id, s.name as session_name,
                   j.symbol, j.entry_price, j.reason, j.news_cited,
                   j.key_thesis, j.target_price, j.stop_price, j.status,
                   j.opened_at, j.closed_at, j.exit_reason, j.exit_price, j.pnl
            FROM trade_journal j
            LEFT JOIN sessions s ON s.id = j.session_id
            ORDER BY j.opened_at DESC
            """,
        )
    return rows


@router.get("/api/journal/{journal_id}/updates")
async def get_journal_updates(
    journal_id: int,
    db: aiosqlite.Connection = Depends(get_db),
):
    journal = await fetchone(db, "SELECT id FROM trade_journal WHERE id = ?", (journal_id,))
    if not journal:
        raise HTTPException(status_code=404, detail="Journal entry not found")

    rows = await fetchall(
        db,
        """
        SELECT tu.id, tu.journal_id, tu.session_id, s.name as session_name,
               tu.symbol, tu.note, tu.updated_at
        FROM thesis_updates tu
        LEFT JOIN sessions s ON s.id = tu.session_id
        WHERE tu.journal_id = ?
        ORDER BY tu.updated_at ASC
        """,
        (journal_id,),
    )
    return rows


@router.get("/api/memory/{session_id}")
async def get_session_memory(
    session_id: int,
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await fetchone(
        db,
        """
        SELECT id, session_id, content, updated_at, cycle_number
        FROM session_memory
        WHERE session_id = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (session_id,),
    )
    return row or {"session_id": session_id, "content": None, "updated_at": None, "cycle_number": None}
