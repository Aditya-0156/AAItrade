from fastapi import APIRouter, Depends, Query
import aiosqlite
from api.database import get_db, fetchall

router = APIRouter(prefix="/api/tool_calls", tags=["tool_calls"])


@router.get("")
async def list_tool_calls(
    session_id: int | None = Query(None),
    limit: int = Query(500, le=2000),
    db: aiosqlite.Connection = Depends(get_db),
):
    if session_id is not None:
        rows = await fetchall(
            db,
            """
            SELECT tc.id, tc.session_id, s.name as session_name,
                   tc.cycle_number, tc.tool_name, tc.parameters,
                   tc.result_summary, tc.called_at
            FROM tool_calls tc
            LEFT JOIN sessions s ON s.id = tc.session_id
            WHERE tc.session_id = ?
            ORDER BY tc.called_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
    else:
        rows = await fetchall(
            db,
            """
            SELECT tc.id, tc.session_id, s.name as session_name,
                   tc.cycle_number, tc.tool_name, tc.parameters,
                   tc.result_summary, tc.called_at
            FROM tool_calls tc
            LEFT JOIN sessions s ON s.id = tc.session_id
            ORDER BY tc.called_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    return rows
