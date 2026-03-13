"""Multi-session runner — manages concurrent trading sessions.

Loads session configs from a YAML file and runs them in parallel threads.

Smart restart logic:
- If a session name already exists in DB as active/paused → recover it
- If a session name is new → start it fresh
- Sessions removed from YAML are left alone in DB (not touched)

This means you can add sessions to the YAML and restart the process —
existing sessions resume from where they left off, new ones start fresh.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from aaitrade import db
from aaitrade.config import (
    APIKeys,
    ExecutionMode,
    SessionConfig,
    TradingMode,
)
from aaitrade.session_manager import SessionManager

logger = logging.getLogger(__name__)


def load_multi_config(path: str | Path) -> list[dict[str, Any]]:
    """Load multi-session config from YAML."""
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("sessions", [])


def _build_config(entry: dict[str, Any]) -> SessionConfig:
    """Build a SessionConfig from a YAML session entry."""
    return SessionConfig(
        execution_mode=ExecutionMode(entry.get("execution", "paper")),
        trading_mode=TradingMode(entry.get("mode", "balanced")),
        starting_capital=float(entry.get("capital", 10000)),
        total_days=int(entry.get("days", 14)),
        watchlist_path=Path(entry.get("watchlist", "config/watchlist_seed.yaml")),
        allow_watchlist_adjustment=entry.get("watchlist_adjust", True),
        decision_interval_minutes=int(entry.get("interval", 15)),
    )


class MultiSessionRunner:
    """Runs multiple trading sessions concurrently."""

    def __init__(self, keys: APIKeys):
        self.keys = keys
        self._threads: dict[str, threading.Thread] = {}
        self._managers: dict[str, SessionManager] = {}

    def start_from_config(self, config_path: str | Path):
        """Start or recover sessions from a multi-session YAML config.

        For each session in the YAML:
        - If a session with that name exists in DB as active/paused → recover it
        - Otherwise → start a fresh session
        """
        db.init_db()
        configs = load_multi_config(config_path)
        if not configs:
            logger.error("No sessions defined in config")
            return

        logger.info(f"Loading {len(configs)} sessions from config")

        for entry in configs:
            name = entry.get("name", f"session-{len(self._threads) + 1}")
            config = _build_config(entry)

            # Check if this named session is already active/paused in DB
            existing = db.query_one(
                "SELECT id, status FROM sessions WHERE name = ? AND status IN ('active', 'paused') ORDER BY id DESC LIMIT 1",
                (name,),
            )

            if existing:
                logger.info(f"'{name}' found in DB (id={existing['id']}, status={existing['status']}) — recovering")
                self._recover_named(name, config, existing["id"], existing["status"])
            else:
                logger.info(f"'{name}' not in DB — starting fresh")
                self._start_session(name, config)

    def recover_active_sessions(self):
        """Recover ALL active/paused sessions from DB (used with --recover flag).

        This is the crash recovery path — finds every session marked active/paused
        regardless of name and resumes them all.
        """
        db.init_db()
        active = db.query(
            "SELECT id, name, execution_mode, trading_mode, starting_capital, "
            "total_days, watchlist_path, allow_watchlist_adjustment, status "
            "FROM sessions WHERE status IN ('active', 'paused')"
        )

        if not active:
            logger.info("No sessions to recover")
            return

        logger.info(f"Recovering {len(active)} sessions from DB")

        for s in active:
            name = s["name"] or f"recovered-{s['id']}"
            config = SessionConfig(
                execution_mode=ExecutionMode(s["execution_mode"]),
                trading_mode=TradingMode(s["trading_mode"]),
                starting_capital=s["starting_capital"],
                total_days=s["total_days"],
                watchlist_path=Path(s["watchlist_path"]),
                allow_watchlist_adjustment=bool(s["allow_watchlist_adjustment"]),
            )
            self._recover_named(name, config, s["id"], s["status"])

    def _recover_named(self, name: str, config: SessionConfig, session_id: int, status: str):
        """Recover a specific session by its DB id."""
        manager = SessionManager(config, self.keys, name=name)
        manager.session_id = session_id
        manager._recovered = True
        self._managers[name] = manager

        thread = threading.Thread(
            target=self._run_recovered,
            args=(name, manager),
            daemon=True,
        )
        thread.start()
        self._threads[name] = thread
        logger.info(f"Recovering '{name}' (id={session_id}, status={status})")

    def _run_recovered(self, name: str, manager: SessionManager):
        """Resume a recovered session without creating a new DB record."""
        try:
            manager._init_clients()

            from aaitrade.tools import load_all_tools, disable_tool
            load_all_tools()
            if not manager.config.allow_watchlist_adjustment:
                disable_tool("add_to_watchlist")
                disable_tool("remove_from_watchlist")

            from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools
            portfolio_tools.set_session_id(manager.session_id)
            memory.set_session_id(manager.session_id)
            journal.set_session_id(manager.session_id)
            watchlist_tools.set_session_id(manager.session_id)

            logger.info(f"'{name}' recovered — entering run loop")
            manager.run()
        except Exception as e:
            logger.error(f"Session '{name}' crashed: {e}", exc_info=True)

    def _start_session(self, name: str, config: SessionConfig):
        """Start a brand new session in its own thread."""
        manager = SessionManager(config, self.keys, name=name)
        self._managers[name] = manager

        thread = threading.Thread(
            target=self._run_new,
            args=(name, manager),
            daemon=True,
        )
        thread.start()
        self._threads[name] = thread

    def _run_new(self, name: str, manager: SessionManager):
        """Start and run a new session (thread target)."""
        try:
            manager.start()
            manager.run()
        except Exception as e:
            logger.error(f"Session '{name}' crashed: {e}", exc_info=True)

    def wait(self):
        """Block until all session threads complete."""
        for name, thread in self._threads.items():
            thread.join()
            logger.info(f"Session '{name}' thread exited")

    def get_active_sessions(self) -> list[str]:
        """Return names of sessions with live threads."""
        return [name for name, t in self._threads.items() if t.is_alive()]
