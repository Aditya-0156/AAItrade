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
    RiskRules,
    RISK_PROFILES,
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
        profit_reinvest_ratio: float | None = None,
        # Custom risk params (only used when trading_mode == "custom"):
        custom_stop_loss: float | None = None,
        custom_take_profit: float | None = None,
        custom_max_positions: int | None = None,
        custom_max_per_trade: float | None = None,
        custom_max_deployed: float | None = None,
        custom_daily_loss_limit: float | None = None,
    ) -> dict:
        """Start a new trading session in a background thread.

        Returns session info dict.
        """
        self._ensure_initialized()

        # For custom mode, use balanced as base config then override risk_rules
        config_trading_mode = trading_mode if trading_mode != "custom" else "balanced"

        # Build config — total_days=99999 for endless sessions
        config = SessionConfig(
            execution_mode=ExecutionMode(execution_mode),
            trading_mode=TradingMode(config_trading_mode),
            starting_capital=starting_capital,
            total_days=99999,  # Endless — user closes via dashboard
            watchlist_path=Path(watchlist_path),
            allow_watchlist_adjustment=allow_watchlist_adjustment,
            model=model,
        )

        # Override risk_rules for custom mode
        if trading_mode == "custom":
            base_rules = RISK_PROFILES[TradingMode.BALANCED]
            custom_rules = RiskRules(
                stop_loss=custom_stop_loss if custom_stop_loss is not None else base_rules.stop_loss,
                take_profit=custom_take_profit if custom_take_profit is not None else base_rules.take_profit,
                max_positions=custom_max_positions if custom_max_positions is not None else base_rules.max_positions,
                max_per_trade=custom_max_per_trade if custom_max_per_trade is not None else base_rules.max_per_trade,
                max_deployed=custom_max_deployed if custom_max_deployed is not None else base_rules.max_deployed,
                daily_loss_limit=custom_daily_loss_limit if custom_daily_loss_limit is not None else base_rules.daily_loss_limit,
                session_stop_loss=base_rules.session_stop_loss,
            )
            config.risk_rules = custom_rules

        # Set profit_reinvest_ratio if provided
        if profit_reinvest_ratio is not None:
            config.profit_reinvest_ratio = profit_reinvest_ratio

        manager = SessionManager(config, self._keys, name=name)
        manager.start()

        session_id = manager.session_id

        # Ensure the DB record reflects the actual trading_mode label (including "custom")
        if trading_mode == "custom":
            db.update("sessions", session_id, {"trading_mode": "custom"})

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
            "profit_reinvest_ratio": config.profit_reinvest_ratio,
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
        """Resume a paused session, or re-recover an active session whose thread died."""
        self._ensure_initialized()

        session = db.query_one(
            "SELECT id, status FROM sessions WHERE id = ?", (session_id,)
        )
        if not session:
            return {"error": f"Session {session_id} not found"}

        with self._lock:
            already_running = session_id in self._sessions

        # Allow resume for paused sessions, or active sessions not running in-memory
        if session["status"] == "paused":
            db.update("sessions", session_id, {"status": "active"})
        elif session["status"] == "active" and not already_running:
            # Thread died / failed recovery — re-recover without changing DB status
            pass
        else:
            return {"error": f"Session is {session['status']}, can only resume paused sessions"}

        # Start thread if not already running
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

        # For custom mode stored in DB, use balanced as the base
        db_trading_mode = session["trading_mode"]
        config_trading_mode = db_trading_mode if db_trading_mode != "custom" else "balanced"

        config = SessionConfig(
            execution_mode=ExecutionMode(session["execution_mode"]),
            trading_mode=TradingMode(config_trading_mode),
            starting_capital=session["starting_capital"],
            total_days=99999,
            watchlist_path=Path(session["watchlist_path"]),
        )

        # Restore profit_reinvest_ratio from DB
        if "profit_reinvest_ratio" in session and session["profit_reinvest_ratio"] is not None:
            config.profit_reinvest_ratio = session["profit_reinvest_ratio"]

        # Restore risk settings from DB (may have been changed mid-session)
        if session.get("stop_loss_pct") is not None:
            config.risk_rules = RiskRules(
                stop_loss=session["stop_loss_pct"],
                take_profit=session["take_profit_pct"],
                max_positions=session["max_positions"],
                max_per_trade=session["max_per_trade_pct"],
                max_deployed=session["max_deployed_pct"],
                daily_loss_limit=session["daily_loss_limit_pct"],
                session_stop_loss=config.risk_rules.session_stop_loss,
            )

        manager = SessionManager(config, self._keys, name=session["name"])
        manager.session_id = session_id
        manager._recovered = True

        # Try to init clients; if Kite token is bad, start thread anyway —
        # it will wait for market hours and by then the user should have updated the token.
        # update_kite_token() will push the new token live to the running session.
        try:
            manager._init_clients()
        except RuntimeError as e:
            if "Kite Connect" in str(e):
                logger.warning(
                    f"Session {session_id} ({session['name']}) starting with invalid Kite token: {e}. "
                    "Update the token in the dashboard before market open."
                )
                # Don't return — still start the thread. Token will be injected via update_kite_token().
            else:
                raise

        from aaitrade.tools import load_all_tools, disable_tool
        load_all_tools()
        if not config.allow_watchlist_adjustment:
            disable_tool("add_to_watchlist")
            disable_tool("remove_from_watchlist")

        # Set tool context
        from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools, session_memory, session_analysis
        portfolio_tools.set_session_id(session_id)
        memory.set_session_id(session_id)
        journal.set_session_id(session_id)
        watchlist_tools.set_session_id(session_id)
        session_memory.set_session_id(session_id)
        session_analysis.set_session_id(session_id)

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
        """Update Kite access token for all active sessions and persist to .env.

        If token is a request_token (from login URL), automatically exchange it for access_token.
        """
        self._ensure_initialized()

        # If token looks like a request_token, exchange it for access_token
        if len(token) < 50:  # request_tokens are typically 32 chars, access_tokens are longer
            try:
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key="9dz93b78apapfn1l")
                data = kite.generate_session(token, api_secret="071tnt5srh72p63b96mh8s8btw9gogyk")
                token = data['access_token']
                logger.info("Converted request_token to access_token")
            except Exception as e:
                return {"status": "error", "message": f"Failed to exchange request_token: {e}. Token may have expired (valid for 2 minutes after login)."}

        os.environ["KITE_ACCESS_TOKEN"] = token

        # Persist to .env file
        env_path = Path(__file__).parent.parent / ".env"
        try:
            # Read current .env
            env_content = ""
            if env_path.exists():
                with open(env_path, "r") as f:
                    env_content = f.read()

            # Replace or add KITE_ACCESS_TOKEN line
            lines = env_content.split("\n")
            found = False
            for i, line in enumerate(lines):
                if line.startswith("KITE_ACCESS_TOKEN="):
                    lines[i] = f"KITE_ACCESS_TOKEN={token}"
                    found = True
                    break
            if not found:
                lines.append(f"KITE_ACCESS_TOKEN={token}")

            # Write back
            with open(env_path, "w") as f:
                f.write("\n".join(lines))

            logger.info(f"Token persisted to .env file")
        except Exception as e:
            logger.error(f"Failed to persist token to .env: {e}")

        # Update live Kite client and validate token via profile call
        try:
            from kiteconnect import KiteConnect
            from aaitrade.tools.market import set_kite_client
            kite = KiteConnect(api_key="9dz93b78apapfn1l")
            kite.set_access_token(token)
            profile = kite.profile()
            logger.info(f"Kite token validated — logged in as {profile['user_name']} ({profile['email']})")
            set_kite_client(kite)
        except Exception as e:
            return {"status": "error", "message": f"Token invalid or Kite API error: {e}"}

        # Push token into all running session managers so they can trade immediately
        injected = []
        with self._lock:
            for sid, st in self._sessions.items():
                try:
                    from aaitrade.tools.market import _kite
                    st.manager.kite = _kite
                    if hasattr(st.manager, 'executor') and st.manager.executor:
                        from aaitrade.executor import set_kite_client as exec_set
                        exec_set(_kite)
                    injected.append(sid)
                except Exception:
                    pass

        # Also recover any live sessions that couldn't start due to bad token
        live_active = db.query(
            "SELECT id FROM sessions WHERE status IN ('active', 'paused', 'closing') AND execution_mode = 'live'",
        )
        recovered = []
        with self._lock:
            for row in live_active:
                if row["id"] not in self._sessions:
                    self._recover_session(row["id"])
                    recovered.append(row["id"])

        msg = "Token updated, applied live, and persisted to .env"
        if recovered:
            msg += f". Recovered sessions: {recovered}"
        return {"status": "ok", "message": msg}

    def update_session_settings(self, session_id: int, changes: dict) -> dict:
        """Update session risk settings in DB and in-memory config.

        Args:
            session_id: The session to update.
            changes: Dict of setting names to new values. Supports:
                stop_loss_pct, take_profit_pct, max_positions, max_per_trade_pct,
                max_deployed_pct, daily_loss_limit_pct, profit_reinvest_ratio,
                starting_capital, add_capital (special: adds to both starting + current).

        Returns:
            Dict with old and new settings.
        """
        self._ensure_initialized()

        session = db.query_one(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        if not session:
            return {"error": f"Session {session_id} not found"}
        if session["status"] not in ("active", "paused", "closing"):
            return {"error": f"Session is {session['status']}, cannot update settings"}

        # Snapshot old settings
        old_settings = {
            "starting_capital": session["starting_capital"],
            "current_capital": session["current_capital"],
            "stop_loss_pct": session["stop_loss_pct"],
            "take_profit_pct": session["take_profit_pct"],
            "max_positions": session["max_positions"],
            "max_per_trade_pct": session["max_per_trade_pct"],
            "max_deployed_pct": session["max_deployed_pct"],
            "daily_loss_limit_pct": session["daily_loss_limit_pct"],
            "profit_reinvest_ratio": session["profit_reinvest_ratio"],
        }

        # Build DB update dict
        db_updates = {}

        # Handle add_capital specially: add to both starting and current
        add_capital = changes.pop("add_capital", None)
        if add_capital is not None and add_capital != 0:
            db_updates["starting_capital"] = session["starting_capital"] + add_capital
            db_updates["current_capital"] = session["current_capital"] + add_capital

        # Map remaining changes directly to DB columns
        direct_fields = {
            "stop_loss_pct", "take_profit_pct", "max_positions",
            "max_per_trade_pct", "max_deployed_pct", "daily_loss_limit_pct",
            "profit_reinvest_ratio", "starting_capital",
        }
        for field_name, value in changes.items():
            if field_name in direct_fields:
                db_updates[field_name] = value

        if not db_updates:
            return {"error": "No valid settings to update"}

        # Apply DB updates
        db.update("sessions", session_id, db_updates)

        # Read back the new session state
        new_session = db.query_one(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        new_settings = {
            "starting_capital": new_session["starting_capital"],
            "current_capital": new_session["current_capital"],
            "stop_loss_pct": new_session["stop_loss_pct"],
            "take_profit_pct": new_session["take_profit_pct"],
            "max_positions": new_session["max_positions"],
            "max_per_trade_pct": new_session["max_per_trade_pct"],
            "max_deployed_pct": new_session["max_deployed_pct"],
            "daily_loss_limit_pct": new_session["daily_loss_limit_pct"],
            "profit_reinvest_ratio": new_session["profit_reinvest_ratio"],
        }

        # Update in-memory config if session is running
        with self._lock:
            st = self._sessions.get(session_id)
            if st:
                manager = st.manager
                config = manager.config

                # Rebuild RiskRules (frozen dataclass — must create new instance)
                new_rules = RiskRules(
                    stop_loss=new_settings["stop_loss_pct"],
                    take_profit=new_settings["take_profit_pct"],
                    max_positions=new_settings["max_positions"],
                    max_per_trade=new_settings["max_per_trade_pct"],
                    max_deployed=new_settings["max_deployed_pct"],
                    daily_loss_limit=new_settings["daily_loss_limit_pct"],
                    session_stop_loss=config.risk_rules.session_stop_loss,
                )
                config.risk_rules = new_rules
                config.starting_capital = new_settings["starting_capital"]
                config.profit_reinvest_ratio = new_settings["profit_reinvest_ratio"]

                # Executor caches self.rules at init — update it too
                if hasattr(manager, "executor"):
                    manager.executor.rules = new_rules

                logger.info(f"Session {session_id} in-memory config updated")

        logger.info(f"Session {session_id} settings updated: {db_updates}")
        return {"old_settings": old_settings, "new_settings": new_settings}

    def notify_claude_settings_change(
        self, session_id: int, old_settings: dict, new_settings: dict
    ):
        """Run a mini Claude call to notify the LLM about settings changes.

        This runs in a background thread so it doesn't block the API response.
        Claude gets a limited tool set (read-only portfolio + memory tools)
        and cannot BUY or SELL during this notification cycle.
        """
        with self._lock:
            st = self._sessions.get(session_id)

        if not st:
            logger.warning(
                f"Cannot notify Claude for session {session_id}: "
                "session not running in-memory"
            )
            return

        def _run_notification():
            try:
                manager = st.manager
                claude = manager.claude

                # Determine cycle number — use next after the latest recorded decision
                latest = db.query_one(
                    "SELECT MAX(cycle_number) as max_cycle FROM decisions WHERE session_id = ?",
                    (session_id,),
                )
                cycle_number = (latest["max_cycle"] or 0) + 1

                # Build human-readable diff of changes
                change_lines = []
                labels = {
                    "stop_loss_pct": ("Stop Loss", "%"),
                    "take_profit_pct": ("Take Profit", "%"),
                    "max_positions": ("Max Positions", ""),
                    "max_per_trade_pct": ("Max Per Trade", "%"),
                    "max_deployed_pct": ("Max Deployed Capital", "%"),
                    "daily_loss_limit_pct": ("Daily Loss Limit", "%"),
                    "profit_reinvest_ratio": ("Profit Reinvest Ratio", ""),
                    "starting_capital": ("Starting Capital", ""),
                    "current_capital": ("Current Capital", ""),
                }
                for key in labels:
                    old_val = old_settings.get(key)
                    new_val = new_settings.get(key)
                    if old_val != new_val:
                        label, suffix = labels[key]
                        if "capital" in key.lower():
                            change_lines.append(
                                f"- {label}: ₹{old_val:,.2f} → ₹{new_val:,.2f}"
                            )
                        elif suffix == "%":
                            change_lines.append(
                                f"- {label}: {old_val}{suffix} → {new_val}{suffix}"
                            )
                        else:
                            change_lines.append(
                                f"- {label}: {old_val} → {new_val}"
                            )

                if not change_lines:
                    logger.info("No actual setting changes to notify Claude about")
                    return

                changes_text = "\n".join(change_lines)

                # Build current settings summary for context
                setting_notes = {
                    "stop_loss_pct": "0 = DISABLED (no hard stop-loss limit — full LLM discretion)",
                    "take_profit_pct": "0 = DISABLED (no hard take-profit limit — full LLM discretion)",
                    "daily_loss_limit_pct": "0 = DISABLED (no daily loss circuit breaker — full LLM discretion)",
                }
                current_summary_lines = []
                for key, note in setting_notes.items():
                    val = new_settings.get(key)
                    if val == 0:
                        current_summary_lines.append(f"- {key}: {note}")
                    elif val is not None:
                        current_summary_lines.append(f"- {key}: {val}")
                current_summary = "\n".join(current_summary_lines) if current_summary_lines else ""

                notification_prompt = (
                    "SETTINGS CHANGE NOTIFICATION\n\n"
                    "The user has updated session settings. Here are the changes:\n"
                    f"{changes_text}\n\n"
                    "IMPORTANT — VALUE MEANINGS:\n"
                    "- A value of 0 for stop_loss_pct, take_profit_pct, or daily_loss_limit_pct "
                    "means that limit is DISABLED. There is NO hard percentage rule. "
                    "You have full discretion to hold, exit, or manage positions as you see fit.\n"
                    "- A non-zero value means that percentage is a hard cap enforced by the system.\n\n"
                    f"CURRENT ACTIVE SETTINGS AFTER THIS CHANGE:\n{current_summary}\n\n"
                    "Your existing positions and their current stop/take-profit "
                    "PRICES in the portfolio are unchanged.\n"
                    "You may now:\n"
                    "1. Review your open positions and update your thesis notes to reflect "
                    "the new risk framework (especially if any limit changed to/from 0)\n"
                    "2. Update your session memory to accurately reflect the current settings\n"
                    "3. Adjust your strategy notes for any implications\n\n"
                    "You are NOT allowed to BUY or SELL in this notification cycle. "
                    "Only review and update your notes.\n\n"
                    "When done, respond with a brief JSON confirmation:\n"
                    '{"action": "HOLD", "symbol": null, "quantity": null, '
                    '"stop_loss_price": null, "take_profit_price": null, '
                    '"reason": "Settings change acknowledged — [your summary]", '
                    '"confidence": "high", "flags": ["SETTINGS_UPDATE"]}'
                )

                system_prompt = (
                    "You are AAItrade's trading AI. The user has changed session "
                    "risk settings mid-session. Review the changes and update your "
                    "session memory and trade theses as needed. You CANNOT place "
                    "any BUY or SELL orders in this cycle — only review, reflect, "
                    "and update your notes. Use the tools provided to read your "
                    "current state and make updates."
                )

                # Only allow read/update tools — no trading actions
                allowed_tools = [
                    "get_portfolio",
                    "get_session_memory",
                    "update_session_memory",
                    "get_open_positions_with_rationale",
                    "update_thesis",
                ]

                from aaitrade.tools import get_tools_for_api, call_tool

                tools = get_tools_for_api(only=allowed_tools)
                messages = [{"role": "user", "content": notification_prompt}]

                # Run a mini tool-use loop (max 10 rounds — this is lightweight)
                for _round in range(10):
                    try:
                        response = claude.client.messages.create(
                            model=claude.model,
                            max_tokens=4096,
                            system=system_prompt,
                            tools=tools,
                            messages=messages,
                        )
                    except Exception as e:
                        logger.error(f"Settings notification Claude call failed: {e}")
                        return

                    if response.stop_reason == "tool_use":
                        tool_results = []
                        for block in response.content:
                            if block.type == "tool_use":
                                logger.info(
                                    f"Settings notification | Tool: {block.name}({block.input})"
                                )
                                result = call_tool(block.name, block.input)
                                # Save tool call to DB so it appears in activity feed
                                db.insert("tool_calls", {
                                    "session_id": session_id,
                                    "cycle_number": cycle_number,
                                    "tool_name": block.name,
                                    "parameters": json.dumps(block.input),
                                    "result_summary": str(result)[:300] if result else None,
                                    "called_at": db.now_iso(),
                                })
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": json.dumps(result),
                                })
                        messages.append({"role": "assistant", "content": response.content})
                        messages.append({"role": "user", "content": tool_results})

                    elif response.stop_reason == "end_turn":
                        # Extract text response for logging
                        text = ""
                        for block in response.content:
                            if hasattr(block, "text"):
                                text += block.text

                        # Save a HOLD decision so it shows in activity feed with SETTINGS_UPDATE flag
                        decision_reason = f"Settings updated by user: {changes_text}"
                        if text:
                            # Try to parse Claude's JSON response for the reason
                            try:
                                parsed = json.loads(text.strip())
                                decision_reason = parsed.get("reason", decision_reason)
                            except Exception:
                                pass
                        db.insert("decisions", {
                            "session_id": session_id,
                            "cycle_number": cycle_number,
                            "action": "HOLD",
                            "symbol": None,
                            "quantity": None,
                            "reason": decision_reason,
                            "confidence": "high",
                            "flags": json.dumps(["SETTINGS_UPDATE"]),
                            "raw_json": json.dumps({"action": "HOLD", "reason": decision_reason, "flags": ["SETTINGS_UPDATE"]}),
                            "decided_at": db.now_iso(),
                        })

                        logger.info(
                            f"Settings notification complete for session {session_id}: {text[:200]}"
                        )
                        return
                    else:
                        logger.warning(
                            f"Settings notification unexpected stop: {response.stop_reason}"
                        )
                        return

                logger.warning("Settings notification exhausted tool rounds")

            except Exception as e:
                logger.error(
                    f"Settings notification failed for session {session_id}: {e}",
                    exc_info=True,
                )

        # Run in background thread
        thread = threading.Thread(
            target=_run_notification,
            name=f"settings-notify-{session_id}",
            daemon=True,
        )
        thread.start()
        logger.info(f"Settings notification thread started for session {session_id}")

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
