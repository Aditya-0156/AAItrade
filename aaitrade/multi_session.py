"""Multi-session runner — manages multiple trading sessions sequentially.

Loads session configs from a YAML file and runs them one at a time.
Each cycle: session 1 trades → session 2 trades → session 3 trades → all sleep.

Smart restart logic:
- If a session name already exists in DB as active/paused → recover it
- If a session name is new → start it fresh
- Sessions removed from YAML are left alone in DB (not touched)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
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

_IST = timezone(timedelta(hours=5, minutes=30))


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
        decision_interval_minutes=int(entry.get("interval", 60)),
        model=entry.get("model", "claude-haiku-4-5-20251001"),
    )


class MultiSessionRunner:
    """Runs multiple trading sessions sequentially (no threads)."""

    def __init__(self, keys: APIKeys):
        self.keys = keys
        self._managers: list[tuple[str, SessionManager]] = []

    def start_from_config(self, config_path: str | Path):
        """Initialize all sessions from YAML config, then run the sequential loop."""
        db.init_db()
        configs = load_multi_config(config_path)
        if not configs:
            logger.error("No sessions defined in config")
            return

        logger.info(f"Loading {len(configs)} sessions from config")

        # Initialize all sessions
        for entry in configs:
            name = entry.get("name", f"session-{len(self._managers) + 1}")
            config = _build_config(entry)

            existing = db.query_one(
                "SELECT id, status FROM sessions WHERE name = ? AND status IN ('active', 'paused') ORDER BY id DESC LIMIT 1",
                (name,),
            )

            if existing:
                logger.info(f"'{name}' found in DB (id={existing['id']}, status={existing['status']}) — recovering")
                manager = self._init_recovered(name, config, existing["id"])
            else:
                logger.info(f"'{name}' not in DB — starting fresh")
                manager = self._init_new(name, config)

            self._managers.append((name, manager))

        # Run the sequential loop
        self._run_sequential_loop()

    def _init_new(self, name: str, config: SessionConfig) -> SessionManager:
        """Initialize a new session (creates DB record, loads tools)."""
        manager = SessionManager(config, self.keys, name=name)
        manager.start()
        return manager

    def _init_recovered(self, name: str, config: SessionConfig, session_id: int) -> SessionManager:
        """Initialize a recovered session from DB."""
        manager = SessionManager(config, self.keys, name=name)
        manager.session_id = session_id
        manager._recovered = True
        manager._init_clients()

        from aaitrade.tools import load_all_tools, disable_tool
        load_all_tools()
        if not manager.config.allow_watchlist_adjustment:
            disable_tool("add_to_watchlist")
            disable_tool("remove_from_watchlist")

        from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools, session_memory
        portfolio_tools.set_session_id(manager.session_id)
        memory.set_session_id(manager.session_id)
        journal.set_session_id(manager.session_id)
        watchlist_tools.set_session_id(manager.session_id)
        session_memory.set_session_id(manager.session_id)

        logger.info(f"'{name}' recovered (id={session_id})")
        return manager

    def _run_sequential_loop(self):
        """Main loop: run one cycle per session sequentially, then sleep."""
        from aaitrade.holidays import is_trading_day
        from aaitrade.telegram_bot import get_bot

        logger.info("Starting sequential trading loop")

        try:
            while True:
                now = datetime.now(_IST)

                # Check if all sessions are done
                all_done = True
                for name, manager in self._managers:
                    session = db.query_one(
                        "SELECT status, current_day, total_days FROM sessions WHERE id = ?",
                        (manager.session_id,),
                    )
                    if session and session["status"] == "active" and session["current_day"] <= session["total_days"]:
                        all_done = False
                        break

                if all_done:
                    logger.info("All sessions completed.")
                    break

                # Holiday/weekend check
                if not is_trading_day(now.date()):
                    logger.info(f"{now.date()} is not a trading day. Sleeping until tomorrow...")
                    tomorrow = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
                    sleep_secs = (tomorrow - now).total_seconds()
                    if sleep_secs > 0:
                        logger.info(f"Sleeping {sleep_secs / 3600:.1f} hours")
                        time.sleep(sleep_secs)
                    continue

                # Pre-market: fetch macro news at 9:00-9:05 AM IST
                if now.hour == 9 and now.minute < 5:
                    logger.info("Pre-market: fetching macro news...")
                    from aaitrade.tools.news import get_macro_news
                    try:
                        get_macro_news()
                    except Exception as e:
                        logger.error(f"Macro news fetch failed: {e}")

                # Trading window: 9:30 AM to 3:15 PM IST
                safe_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
                safe_close = now.replace(hour=15, minute=15, second=0, microsecond=0)

                if safe_open <= now <= safe_close:
                    # Run one cycle for each active session, sequentially
                    for name, manager in self._managers:
                        session = db.query_one(
                            "SELECT status, current_day, total_days FROM sessions WHERE id = ?",
                            (manager.session_id,),
                        )
                        if not session or session["status"] != "active":
                            continue
                        if session["current_day"] > session["total_days"]:
                            manager._complete_session()
                            continue

                        # Set session-specific tool context
                        from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools, session_memory
                        portfolio_tools.set_session_id(manager.session_id)
                        memory.set_session_id(manager.session_id)
                        journal.set_session_id(manager.session_id)
                        watchlist_tools.set_session_id(manager.session_id)
                        session_memory.set_session_id(manager.session_id)

                        logger.info(f"── Running cycle for '{name}' (session {manager.session_id}) ──")
                        try:
                            manager._run_cycle()
                        except Exception as e:
                            logger.error(f"Cycle failed for '{name}': {e}", exc_info=True)
                            bot = get_bot()
                            if bot:
                                bot.send(f"⚠️ Cycle error in '{name}': {e}")

                        # Small pause between sessions to be nice to APIs
                        time.sleep(5)

                # End of day: 3:30 - 3:45 PM IST
                elif now.hour == 15 and 30 <= now.minute < 45:
                    for name, manager in self._managers:
                        try:
                            manager._end_of_day()
                        except Exception as e:
                            logger.error(f"EOD failed for '{name}': {e}", exc_info=True)

                # Sleep until next check
                now = datetime.now(_IST)
                market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
                market_close = now.replace(hour=15, minute=45, second=0, microsecond=0)

                if market_open <= now <= market_close:
                    # Use the first session's interval (they should all be the same)
                    interval = self._managers[0][1].config.decision_interval_minutes
                    logger.info(f"All sessions done this cycle. Sleeping {interval} min until next cycle...")
                    time.sleep(interval * 60)
                else:
                    logger.debug("Outside market hours, checking again in 60s...")
                    time.sleep(60)

        except KeyboardInterrupt:
            logger.info("Interrupted — completing all sessions")
            for name, manager in self._managers:
                try:
                    manager._complete_session()
                except Exception:
                    pass

    def wait(self):
        """No-op for compatibility — sequential loop runs in main thread."""
        pass

    def get_active_sessions(self) -> list[str]:
        """Return names of active sessions."""
        active = []
        for name, manager in self._managers:
            session = db.query_one(
                "SELECT status FROM sessions WHERE id = ?",
                (manager.session_id,),
            )
            if session and session["status"] == "active":
                active.append(name)
        return active
