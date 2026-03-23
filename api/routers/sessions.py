from fastapi import APIRouter, Depends, HTTPException
import aiosqlite
from api.database import get_db, fetchall, fetchone

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("")
async def list_sessions(db: aiosqlite.Connection = Depends(get_db)):
    rows = await fetchall(
        db,
        """
        SELECT id, name, execution_mode, trading_mode, starting_capital,
               current_capital, secured_profit, total_days, current_day,
               profit_reinvest_ratio, status, started_at, ended_at
        FROM sessions
        ORDER BY started_at DESC
        """,
    )
    return rows


@router.get("/{session_id}")
async def get_session(session_id: int, db: aiosqlite.Connection = Depends(get_db)):
    row = await fetchone(
        db,
        """
        SELECT id, name, execution_mode, trading_mode, starting_capital,
               current_capital, secured_profit, total_days, current_day,
               profit_reinvest_ratio, status, started_at, ended_at,
               stop_loss_pct, take_profit_pct, max_positions, max_per_trade_pct,
               max_deployed_pct, daily_loss_limit_pct
        FROM sessions WHERE id = ?
        """,
        (session_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return row
