"""Server-side session runner — manages trading sessions as background threads.

This module bridges the FastAPI dashboard and the trading engine. It runs
sessions in background threads and provides control methods (start, stop,
pause, resume, close) that the API endpoints call.

The server is a singleton — one instance manages all active sessions.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aaitrade import db
from aaitrade.config import (
    APIKeys,
    ExecutionMode,
    SessionConfig,
    TradingMode,
    load_watchlist,
)
from aaitrade.session_manager import SessionManager
from aaitrade.telegram_bot import get_bot, init_telegram

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class SessionThread:
    """Tracks a running session and its thread."""
    session_id: int
    name: str
    manager: SessionManager
    thread: threading.Thread


class TradingServer:
    """Manages multiple trading sessions in background threads.

    Designed to be used alongside the FastAPI server — both share the
    same process and SQLite database.
    """

    def __init__(self):
        self._sessions: dict[int, SessionThread] = {}
        self._lock = threading.Lock()
        self._keys: APIKeys | None = None
        self._initialized = False

    def initialize(self, keys: APIKeys | None = None):
        """Initialize the server with API keys and DB."""
        if self._initialized:
            return
        db.init_db()
        self._keys = keys or APIKeys.from_env()

        # Initialize Telegram bot
        bot = init_telegram()
        if bot:
            logger.info("Telegram bot initialized for server")

        self._initialized = True
        logger.info("Trading server initialized")

    def _ensure_initialized(self):
        if not self._initialized:
            self.initialize()

    # ── Session Lifecycle ───────────────────────────────────────────────

    def start_session(
        self,
        name: str,
        execution_mode: str = "paper",
        trading_mode: str = "balanced",
        starting_capital: float = 20000.0,
        watchlist_path: str = "config/watchlist_seed.yaml",
        allow_watchlist_adjustment: bool = True,
        model: str = "claude-haiku-4-5-20251001",
    ) -> dict:
        """Start a new trading session in a background thread.

        Returns session info dict.
        """
        self._ensure_initialized()

        # Build config — total_days=99999 for endless sessions
        config = SessionConfig(
            execution_mode=ExecutionMode(execution_mode),
            trading_mode=TradingMode(trading_mode),
            starting_capital=starting_capital,
            total_days=99999,  # Endless — user closes via dashboard
            watchlist_path=Path(watchlist_path),
            allow_watchlist_adjustment=allow_watchlist_adjustment,
            model=model,
        )

        manager = SessionManager(config, self._keys, name=name)
        manager.start()

        session_id = manager.session_id

        # Run in background thread
        thread = threading.Thread(
            target=self._run_session_safe,
            args=(manager,),
            name=f"session-{session_id}",
            daemon=True,
        )

        with self._lock:
            self._sessions[session_id] = SessionThread(
                session_id=session_id,
                name=name,
                manager=manager,
                thread=thread,
            )

        thread.start()
        logger.info(f"Session {session_id} ({name}) started in background thread")

        return {
            "session_id": session_id,
            "name": name,
            "status": "active",
            "execution_mode": execution_mode,
            "trading_mode": trading_mode,
            "starting_capital": starting_capital,
        }

    def _run_session_safe(self, manager: SessionManager):
        """Wrapper that catches exceptions and marks session as errored."""
        try:
            manager.run()
        except Exception as e:
            logger.error(f"Session {manager.session_id} crashed: {e}", exc_info=True)
            # Mark session as halted with error info
            try:
                db.update("sessions", manager.session_id, {
                    "status": "halted",
                    "ended_at": db.now_iso(),
                })
            except Exception:
                pass
            bot = get_bot()
            if bot:
                bot.send(f"Session {manager.session_id} crashed: {e}")
        finally:
            with self._lock:
                self._sessions.pop(manager.session_id, None)

    def stop_session(self, session_id: int) -> dict:
        """Stop a session immediately (halts it)."""
        self._ensure_initialized()

        session = db.query_one(
            "SELECT id, status FROM sessions WHERE id = ?", (session_id,)
        )
        if not session:
            return {"error": f"Session {session_id} not found"}
        if session["status"] not in ("active", "paused", "closing"):
            return {"error": f"Session {session_id} is already {session['status']}"}

        db.update("sessions", session_id, {
            "status": "halted",
            "ended_at": db.now_iso(),
        })
        logger.info(f"Session {session_id} stopped via dashboard")
        return {"session_id": session_id, "status": "halted"}

    def pause_session(self, session_id: int) -> dict:
        """Pause a running session."""
        self._ensure_initialized()

        session = db.query_one(
            "SELECT id, status FROM sessions WHERE id = ?", (session_id,)
        )
        if not session:
            return {"error": f"Session {session_id} not found"}
        if session["status"] != "active":
            return {"error": f"Session is {session['status']}, can only pause active sessions"}

        db.update("sessions", session_id, {"status": "paused"})
        return {"session_id": session_id, "status": "paused"}

    def resume_session(self, session_id: int) -> dict:
        """Resume a paused session."""
        self._ensure_initialized()

        session = db.query_one(
            "SELECT id, status FROM sessions WHERE id = ?", (session_id,)
        )
        if not session:
            return {"error": f"Session {session_id} not found"}
        if session["status"] != "paused":
            return {"error": f"Session is {session['status']}, can only resume paused sessions"}

        db.update("sessions", session_id, {"status": "active"})

        # If the thread isn't running, restart it
        with self._lock:
            if session_id not in self._sessions:
                self._recover_session(session_id)

        return {"session_id": session_id, "status": "active"}

    def close_session(self, session_id: int) -> dict:
        """Initiate graceful closing mode for a session.

        Sets status to 'closing' — the session manager will switch to
        HOLD/SELL only mode and exit positions over 1-10 market days.
        """
        self._ensure_initialized()

        session = db.query_one(
            "SELECT id, status FROM sessions WHERE id = ?", (session_id,)
        )
        if not session:
            return {"error": f"Session {session_id} not found"}
        if session["status"] not in ("active", "paused"):
            return {"error": f"Session is {session['status']}, can only close active/paused sessions"}

        db.update("sessions", session_id, {"status": "closing"})

        # If paused, the thread may have stopped — restart it in closing mode
        with self._lock:
            if session_id not in self._sessions:
                self._recover_session(session_id)

        logger.info(f"Session {session_id} entering closing mode")
        return {"session_id": session_id, "status": "closing"}

    def _recover_session(self, session_id: int):
        """Recover a session that isn't running in a thread."""
        session = db.query_one(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        if not session:
            return

        config = SessionConfig(
            execution_mode=ExecutionMode(session["execution_mode"]),
            trading_mode=TradingMode(session["trading_mode"]),
            starting_capital=session["starting_capital"],
            total_days=99999,
            watchlist_path=Path(session["watchlist_path"]),
        )

        manager = SessionManager(config, self._keys, name=session["name"])
        manager.session_id = session_id
        manager._recovered = True
        manager._init_clients()

        from aaitrade.tools import load_all_tools, disable_tool
        load_all_tools()
        if not config.allow_watchlist_adjustment:
            disable_tool("add_to_watchlist")
            disable_tool("remove_from_watchlist")

        # Set tool context
        from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools, session_memory
        portfolio_tools.set_session_id(session_id)
        memory.set_session_id(session_id)
        journal.set_session_id(session_id)
        watchlist_tools.set_session_id(session_id)
        session_memory.set_session_id(session_id)

        thread = threading.Thread(
            target=self._run_session_safe,
            args=(manager,),
            name=f"session-{session_id}",
            daemon=True,
        )
        self._sessions[session_id] = SessionThread(
            session_id=session_id,
            name=session["name"] or f"session-{session_id}",
            manager=manager,
            thread=thread,
        )
        thread.start()
        logger.info(f"Session {session_id} recovered and restarted")

    def recover_all_active(self):
        """Recover all active/paused/closing sessions from DB."""
        self._ensure_initialized()

        rows = db.query(
            "SELECT id FROM sessions WHERE status IN ('active', 'paused', 'closing') ORDER BY id"
        )
        for row in rows:
            with self._lock:
                if row["id"] not in self._sessions:
                    self._recover_session(row["id"])

        logger.info(f"Recovered {len(rows)} session(s)")

    def update_kite_token(self, token: str) -> dict:
        """Update Kite access token for all active sessions."""
        self._ensure_initialized()

        os.environ["KITE_ACCESS_TOKEN"] = token

        # Update live Kite client
        try:
            from aaitrade.tools.market import _kite, set_kite_client
            if _kite is not None:
                _kite.set_access_token(token)
                set_kite_client(_kite)
                return {"status": "ok", "message": "Token updated and applied live"}
            else:
                return {"status": "ok", "message": "Token saved, will apply on next session start"}
        except Exception as e:
            return {"status": "error", "message": f"Token saved but live update failed: {e}"}

    def get_running_sessions(self) -> list[int]:
        """Return IDs of sessions with active threads."""
        with self._lock:
            return list(self._sessions.keys())


# Module-level singleton
_server: TradingServer | None = None


def get_server() -> TradingServer:
    """Get or create the trading server singleton."""
    global _server
    if _server is None:
        _server = TradingServer()
    return _server
