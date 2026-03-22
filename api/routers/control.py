"""Control router — write endpoints for managing sessions from the dashboard.

These endpoints allow the dashboard to start/stop/pause/resume sessions,
update the Kite token, and manage settings without SSH access.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/control", tags=["control"])


# ── Request Models ────────────────────────────────────────────────────────


class StartSessionRequest(BaseModel):
    name: str = "dashboard-session"
    execution_mode: str = "paper"  # paper | live
    trading_mode: str = "balanced"  # safe | balanced | aggressive
    starting_capital: float = 20000.0
    watchlist_path: str = "config/watchlist_seed.yaml"
    allow_watchlist_adjustment: bool = True
    model: str = "claude-haiku-4-5-20251001"


class TokenUpdateRequest(BaseModel):
    token: str


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/sessions/start")
async def start_session(req: StartSessionRequest):
    """Start a new trading session."""
    from aaitrade.server import get_server

    if req.execution_mode not in ("paper", "live"):
        raise HTTPException(400, "execution_mode must be 'paper' or 'live'")
    if req.trading_mode not in ("safe", "balanced", "aggressive"):
        raise HTTPException(400, "trading_mode must be 'safe', 'balanced', or 'aggressive'")
    if req.starting_capital <= 0:
        raise HTTPException(400, "starting_capital must be positive")

    server = get_server()
    result = server.start_session(
        name=req.name,
        execution_mode=req.execution_mode,
        trading_mode=req.trading_mode,
        starting_capital=req.starting_capital,
        watchlist_path=req.watchlist_path,
        allow_watchlist_adjustment=req.allow_watchlist_adjustment,
        model=req.model,
    )

    if "error" in result:
        raise HTTPException(500, result["error"])
    return result


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: int):
    """Stop a session immediately."""
    from aaitrade.server import get_server

    result = get_server().stop_session(session_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/sessions/{session_id}/pause")
async def pause_session(session_id: int):
    """Pause a running session."""
    from aaitrade.server import get_server

    result = get_server().pause_session(session_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/sessions/{session_id}/resume")
async def resume_session(session_id: int):
    """Resume a paused session."""
    from aaitrade.server import get_server

    result = get_server().resume_session(session_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: int):
    """Initiate graceful closing mode — HOLD/SELL only, exit over 1-10 days."""
    from aaitrade.server import get_server

    result = get_server().close_session(session_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.post("/token")
async def update_token(req: TokenUpdateRequest):
    """Update Kite access token for all active sessions."""
    from aaitrade.server import get_server

    if not req.token.strip():
        raise HTTPException(400, "Token cannot be empty")

    result = get_server().update_kite_token(req.token.strip())
    return result


@router.get("/running")
async def get_running():
    """Get IDs of sessions with active background threads."""
    from aaitrade.server import get_server

    return {"running_session_ids": get_server().get_running_sessions()}


@router.post("/sessions/{session_id}/sync")
async def sync_portfolio(session_id: int):
    """Manually trigger portfolio sync with Zerodha for a live session."""
    from aaitrade.portfolio_sync import sync_portfolio_with_kite
    from aaitrade.tools.market import _kite

    if not _kite:
        raise HTTPException(400, "Kite client not initialized")

    result = sync_portfolio_with_kite(session_id, _kite)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@router.get("/presets")
async def get_presets():
    """Return available trading mode presets and their risk parameters."""
    from aaitrade.config import RISK_PROFILES, MODE_MANDATES, TradingMode

    presets = {}
    for mode in TradingMode:
        rules = RISK_PROFILES[mode]
        presets[mode.value] = {
            "mandate": MODE_MANDATES[mode],
            "max_per_trade": rules.max_per_trade,
            "stop_loss": rules.stop_loss,
            "take_profit": rules.take_profit,
            "max_positions": rules.max_positions,
            "max_deployed": rules.max_deployed,
            "daily_loss_limit": rules.daily_loss_limit,
            "session_stop_loss": rules.session_stop_loss,
        }
    return presets
