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
import os
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


def _is_test_mode() -> bool:
    return os.environ.get("AAITRADE_TEST_MODE") == "1"


# Test mode: market hours are calculated relative to startup time
_test_start_time: datetime | None = None


def _get_test_hours() -> tuple[datetime, datetime, datetime, datetime]:
    """Return (news_time, market_open, market_close, eod_time) for test mode.

    Starts 1 min from _test_start_time, runs for 15 min total.
    """
    global _test_start_time
    if _test_start_time is None:
        _test_start_time = datetime.now(_IST)
    base = _test_start_time
    news_time = base + timedelta(minutes=1)
    market_open = base + timedelta(minutes=2)
    market_close = base + timedelta(minutes=12)
    eod_time = market_close
    return news_time, market_open, market_close, eod_time


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

        self._set_tool_context(manager)

        logger.info(f"'{name}' recovered (id={session_id})")
        return manager

    def _run_sequential_loop(self):
        """Main loop: run one cycle per session sequentially, then sleep.

        Normal mode daily rhythm (IST):
        - Before 9:00: sleep 60s, wait for pre-market
        - 9:00+: fetch macro news (once per day)
        - 9:30-15:15: trading window — run cycles, then sleep `interval` min
        - 15:15+: run EOD (once per day), then sleep until tomorrow 8:55 AM

        Test mode (--test):
        - Market opens 2 min after startup, closes 10 min after open
        - Cycles every 5 min; holiday check disabled
        """
        from aaitrade.holidays import is_trading_day
        from aaitrade.telegram_bot import get_bot

        test_mode = _is_test_mode()
        if test_mode:
            news_time, test_open, test_close, _ = _get_test_hours()
            logger.info(f"TEST MODE active:")
            logger.info(f"  News fetch at:  {news_time.strftime('%H:%M:%S')}")
            logger.info(f"  Market open at: {test_open.strftime('%H:%M:%S')}")
            logger.info(f"  Market close at: {test_close.strftime('%H:%M:%S')}")
            logger.info(f"  Cycle interval: 5 min")

        logger.info("Starting sequential trading loop")

        # Guards: track what we've done today so we don't repeat
        _macro_fetched_date: str | None = None
        _eod_done_date: str | None = None

        try:
            while True:
                now = datetime.now(_IST)
                today_str = now.strftime("%Y-%m-%d")

                # ── Check if all sessions are done ──
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
                    bot = get_bot()
                    if bot:
                        bot.send("✅ All sessions completed. Trading run finished.")
                    break

                # ── Holiday / weekend check (disabled in test mode) ──
                if not test_mode and not is_trading_day(now.date()):
                    logger.info(f"{now.date()} is not a trading day. Sleeping until tomorrow...")
                    self._sleep_until_tomorrow(now)
                    continue

                # ── Compute market hours ──
                if test_mode:
                    news_time, safe_open, safe_close, _ = _get_test_hours()
                    news_ready = now >= news_time
                else:
                    safe_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
                    safe_close = now.replace(hour=15, minute=15, second=0, microsecond=0)
                    news_ready = now.hour >= 9

                # ── Pre-market: fetch macro news once per day ──
                if news_ready and _macro_fetched_date != today_str:
                    _macro_fetched_date = today_str
                    logger.info("Pre-market: fetching macro news...")
                    from aaitrade.tools.news import get_macro_news
                    try:
                        get_macro_news()
                        logger.info("Macro news fetched successfully.")
                    except Exception as e:
                        logger.error(f"Macro news fetch failed: {e}")

                # ── Before trading window: wait ──
                if now < safe_open:
                    sleep_secs = min(60 if not test_mode else 10, (safe_open - now).total_seconds())
                    if sleep_secs > 2:
                        logger.debug(f"Waiting for market open ({safe_open.strftime('%H:%M:%S')}). {sleep_secs:.0f}s...")
                        time.sleep(sleep_secs)
                    continue

                # ── Trading window ──
                if safe_open <= now <= safe_close:
                    # Run one cycle for each active session, sequentially
                    for name, manager in self._managers:
                        session = db.query_one(
                            "SELECT status, current_day, total_days FROM sessions WHERE id = ?",
                            (manager.session_id,),
                        )
                        if not session or session["status"] not in ("active", "closing"):
                            continue
                        if session["current_day"] > session["total_days"]:
                            manager._complete_session()
                            continue

                        # Set session-specific tool context
                        self._set_tool_context(manager)

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

                    # After all sessions done this cycle — sleep until next cycle
                    now_after = datetime.now(_IST)

                    if now_after < safe_close:
                        interval = 5 if test_mode else self._managers[0][1].config.decision_interval_minutes
                        # Don't sleep past market close
                        max_sleep = (safe_close - now_after).total_seconds()
                        sleep_secs = min(interval * 60, max_sleep)
                        logger.info(f"All sessions done this cycle. Sleeping {sleep_secs / 60:.0f} min until next cycle...")
                        time.sleep(sleep_secs)
                    continue

                # ── After market close: EOD processing ──
                if now > safe_close and _eod_done_date != today_str:
                    _eod_done_date = today_str
                    logger.info("Market closing — running end-of-day processing...")
                    for name, manager in self._managers:
                        self._set_tool_context(manager)
                        try:
                            manager._end_of_day()
                        except Exception as e:
                            logger.error(f"EOD failed for '{name}': {e}", exc_info=True)

                    # Check if any session just hit its last day — complete it
                    for name, manager in self._managers:
                        session = db.query_one(
                            "SELECT status, current_day, total_days FROM sessions WHERE id = ?",
                            (manager.session_id,),
                        )
                        if (session and session["status"] == "active"
                                and session["current_day"] > session["total_days"]):
                            self._set_tool_context(manager)
                            logger.info(f"Session '{name}' reached final day — completing...")
                            manager._complete_session()

                # Sleep until tomorrow (or exit in test mode)
                if test_mode:
                    logger.info("TEST MODE: Day complete. Exiting.")
                    break
                self._sleep_until_tomorrow(datetime.now(_IST))

        except KeyboardInterrupt:
            logger.info("Interrupted — sessions left active in DB for recovery on restart")

    @staticmethod
    def _set_tool_context(manager: SessionManager):
        """Set all tool modules to the given session's context."""
        from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools, session_memory, session_analysis
        portfolio_tools.set_session_id(manager.session_id)
        memory.set_session_id(manager.session_id)
        journal.set_session_id(manager.session_id)
        watchlist_tools.set_session_id(manager.session_id)
        session_memory.set_session_id(manager.session_id)
        session_analysis.set_session_id(manager.session_id)

    @staticmethod
    def _sleep_until_tomorrow(now: datetime):
        """Sleep until 8:55 AM IST next day."""
        tomorrow = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
        sleep_secs = (tomorrow - now).total_seconds()
        if sleep_secs > 0:
            logger.info(f"Sleeping {sleep_secs / 3600:.1f} hours until tomorrow morning...")
            time.sleep(sleep_secs)
            # Do NOT complete sessions — leave them active so they can be recovered

    def recover_active_sessions(self):
        """Find and recover all active/paused sessions from DB, then run the loop."""
        db.init_db()
        rows = db.query(
            "SELECT id, name, status FROM sessions WHERE status IN ('active', 'paused') ORDER BY id",
        )
        if not rows:
            logger.info("No active/paused sessions found in DB to recover.")
            return

        logger.info(f"Found {len(rows)} session(s) to recover")

        for row in rows:
            name = row["name"] or f"session-{row['id']}"
            session_id = row["id"]

            # Build a default config for recovery — use balanced defaults
            config = SessionConfig(
                execution_mode=ExecutionMode.PAPER,
                trading_mode=TradingMode.BALANCED,
                starting_capital=20000,
                total_days=14,
                watchlist_path=Path("config/watchlist_seed.yaml"),
            )

            manager = self._init_recovered(name, config, session_id)
            self._managers.append((name, manager))

        self._run_sequential_loop()

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
