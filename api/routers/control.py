"""Control router — write endpoints for managing sessions from the dashboard.

These endpoints allow the dashboard to start/stop/pause/resume sessions,
update the Kite token, and manage settings without SSH access.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/control", tags=["control"])


# ── Request Models ────────────────────────────────────────────────────────


class StartSessionRequest(BaseModel):
    name: str = "dashboard-session"
    execution_mode: str = "paper"  # paper | live
    trading_mode: str = "balanced"  # safe | balanced | aggressive | custom
    starting_capital: float = 20000.0
    watchlist_path: str = "config/watchlist_seed.yaml"
    allow_watchlist_adjustment: bool = True
    model: str = "claude-haiku-4-5-20251001"
    profit_reinvest_ratio: float = 0.5
    # Custom mode risk params (only used when trading_mode == "custom")
    custom_stop_loss: Optional[float] = None
    custom_take_profit: Optional[float] = None
    custom_max_positions: Optional[int] = None
    custom_max_per_trade: Optional[float] = None
    custom_max_deployed: Optional[float] = None
    custom_daily_loss_limit: Optional[float] = None


class TokenUpdateRequest(BaseModel):
    token: str


class ReinvestUpdateRequest(BaseModel):
    ratio: float  # 0.0 to 1.0


class SessionSettingsUpdateRequest(BaseModel):
    starting_capital: Optional[float] = None
    add_capital: Optional[float] = None  # Add this amount to both starting and current capital
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    max_positions: Optional[int] = None
    max_per_trade_pct: Optional[float] = None
    max_deployed_pct: Optional[float] = None
    daily_loss_limit_pct: Optional[float] = None
    profit_reinvest_ratio: Optional[float] = None
    notify_claude: bool = True  # Whether to run a mini Claude call to notify about changes


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/sessions/start")
async def start_session(req: StartSessionRequest):
    """Start a new trading session."""
    from aaitrade.server import get_server

    if req.execution_mode not in ("paper", "live"):
        raise HTTPException(400, "execution_mode must be 'paper' or 'live'")
    if req.trading_mode not in ("safe", "balanced", "aggressive", "custom"):
        raise HTTPException(400, "trading_mode must be 'safe', 'balanced', 'aggressive', or 'custom'")
    if req.starting_capital <= 0:
        raise HTTPException(400, "starting_capital must be positive")
    if not 0.0 <= req.profit_reinvest_ratio <= 1.0:
        raise HTTPException(400, "profit_reinvest_ratio must be between 0.0 and 1.0")

    server = get_server()
    result = server.start_session(
        name=req.name,
        execution_mode=req.execution_mode,
        trading_mode=req.trading_mode,
        starting_capital=req.starting_capital,
        watchlist_path=req.watchlist_path,
        allow_watchlist_adjustment=req.allow_watchlist_adjustment,
        model=req.model,
        profit_reinvest_ratio=req.profit_reinvest_ratio,
        custom_stop_loss=req.custom_stop_loss,
        custom_take_profit=req.custom_take_profit,
        custom_max_positions=req.custom_max_positions,
        custom_max_per_trade=req.custom_max_per_trade,
        custom_max_deployed=req.custom_max_deployed,
        custom_daily_loss_limit=req.custom_daily_loss_limit,
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


@router.post("/sessions/{session_id}/reinvest")
async def update_reinvest_ratio(session_id: int, req: ReinvestUpdateRequest):
    """Update the profit reinvest ratio for a session (live-changeable)."""
    from aaitrade import db

    if not 0.0 <= req.ratio <= 1.0:
        raise HTTPException(status_code=400, detail="ratio must be between 0.0 and 1.0")
    session = db.query_one("SELECT id, status FROM sessions WHERE id = ?", (session_id,))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.update("sessions", session_id, {"profit_reinvest_ratio": req.ratio})
    return {"session_id": session_id, "profit_reinvest_ratio": req.ratio}


@router.put("/sessions/{session_id}/settings")
async def update_session_settings(session_id: int, req: SessionSettingsUpdateRequest):
    """Update risk settings and capital for a running session.

    Supports partial updates — only non-None fields are applied.
    If notify_claude is True (default), a background mini-cycle notifies Claude
    about the changes so it can update its memory and trade theses.
    """
    from aaitrade import db
    from aaitrade.server import get_server

    # ── Validate session exists and is in a modifiable state ──
    session = db.query_one(
        "SELECT id, status FROM sessions WHERE id = ?", (session_id,)
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["status"] not in ("active", "paused", "closing"):
        raise HTTPException(
            status_code=400,
            detail=f"Session is '{session['status']}' — can only update active/paused/closing sessions",
        )

    # ── Validate individual field values ──
    if req.stop_loss_pct is not None and req.stop_loss_pct < 0:
        raise HTTPException(status_code=400, detail="stop_loss_pct must be >= 0 (0 = disabled, LLM decides)")
    if req.take_profit_pct is not None and req.take_profit_pct < 0:
        raise HTTPException(status_code=400, detail="take_profit_pct must be >= 0 (0 = disabled, LLM decides)")
    if req.max_positions is not None and req.max_positions < 1:
        raise HTTPException(status_code=400, detail="max_positions must be at least 1")
    if req.max_per_trade_pct is not None and not (0 < req.max_per_trade_pct <= 100):
        raise HTTPException(status_code=400, detail="max_per_trade_pct must be between 0 and 100")
    if req.max_deployed_pct is not None and not (0 < req.max_deployed_pct <= 100):
        raise HTTPException(status_code=400, detail="max_deployed_pct must be between 0 and 100")
    if req.daily_loss_limit_pct is not None and req.daily_loss_limit_pct < 0:
        raise HTTPException(status_code=400, detail="daily_loss_limit_pct must be >= 0 (0 = disabled, LLM decides)")
    if req.profit_reinvest_ratio is not None and not (0.0 <= req.profit_reinvest_ratio <= 1.0):
        raise HTTPException(status_code=400, detail="profit_reinvest_ratio must be between 0.0 and 1.0")
    if req.starting_capital is not None and req.starting_capital <= 0:
        raise HTTPException(status_code=400, detail="starting_capital must be positive")
    if req.add_capital is not None and req.add_capital == 0:
        raise HTTPException(status_code=400, detail="add_capital must be non-zero")

    # ── Build changes dict from non-None fields ──
    changes = {}
    field_names = [
        "starting_capital", "add_capital", "stop_loss_pct", "take_profit_pct",
        "max_positions", "max_per_trade_pct", "max_deployed_pct",
        "daily_loss_limit_pct", "profit_reinvest_ratio",
    ]
    for field_name in field_names:
        value = getattr(req, field_name)
        if value is not None:
            changes[field_name] = value

    if not changes:
        raise HTTPException(status_code=400, detail="No settings provided to update")

    # ── Apply changes via server ──
    server = get_server()
    result = server.update_session_settings(session_id, changes)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # ── Optionally notify Claude in a background thread ──
    if req.notify_claude:
        server.notify_claude_settings_change(
            session_id,
            result["old_settings"],
            result["new_settings"],
        )

    return {
        "session_id": session_id,
        "old_settings": result["old_settings"],
        "new_settings": result["new_settings"],
        "claude_notified": req.notify_claude,
    }


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
    from aaitrade.config import RISK_PROFILES, MODE_MANDATES, PROFIT_REINVEST_RATIO, TradingMode

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
            "suggested_reinvest_ratio": PROFIT_REINVEST_RATIO[mode],
        }
    return presets
