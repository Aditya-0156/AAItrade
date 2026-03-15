"""Telegram bot — notifications and commands for AAItrade.

Sends trade alerts, daily summaries, halt warnings.
Accepts commands: /status, /stop, /pause, /resume, /token, /sessions.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Callable

import requests

from aaitrade import db

logger = logging.getLogger(__name__)

# Telegram Bot API base URL
_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    """Lightweight Telegram bot using raw HTTP (no async dependency)."""

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._poll_thread: threading.Thread | None = None
        self._running = False
        self._command_handlers: dict[str, Callable] = {}
        self._last_update_id = 0

        # Register built-in commands
        self._register_defaults()

    def _api(self, method: str, **kwargs) -> dict | None:
        """Call a Telegram Bot API method."""
        url = _BASE.format(token=self.token, method=method)
        try:
            # Use poll_timeout + 2s buffer for getUpdates, 10s for everything else
            req_timeout = kwargs.get("timeout", 0) + 2 if "timeout" in kwargs else 10
            resp = requests.post(url, json=kwargs, timeout=req_timeout)
            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"Telegram API error: {data}")
                return None
            return data.get("result")
        except Exception as e:
            logger.warning(f"Telegram API call failed: {e}")
            return None

    # ── Sending Messages ────────────────────────────────────────────────────

    def send(self, text: str, parse_mode: str = "Markdown"):
        """Send a message to the configured chat."""
        self._api("sendMessage", chat_id=self.chat_id, text=text, parse_mode=parse_mode)

    def send_trade_alert(self, action: str, symbol: str, quantity: int, price: float,
                         reason: str = "", pnl: float | None = None, mode: str = "paper"):
        """Send a formatted trade alert."""
        emoji = "\u2705" if action == "BUY" else "\ud83d\udcb0" if pnl and pnl > 0 else "\ud83d\udd34"
        lines = [
            f"{emoji} *{action}* `{symbol}`",
            f"Qty: {quantity} @ \u20b9{price:,.2f}",
        ]
        if pnl is not None:
            lines.append(f"P&L: \u20b9{pnl:,.2f}")
        if reason:
            lines.append(f"Reason: {reason}")
        lines.append(f"_Mode: {mode}_")
        self.send("\n".join(lines))

    def send_daily_summary(self, summary_text: str):
        """Send end-of-day summary."""
        self.send(f"\ud83d\udcca *End of Day Summary*\n```\n{summary_text}\n```")

    def send_session_report(self, report_text: str):
        """Send final session report."""
        self.send(f"\ud83c\udfc1 *Session Complete*\n```\n{report_text}\n```")

    def send_halt_alert(self, reason: str, session_id: int):
        """Send halt warning."""
        self.send(
            f"\u26a0\ufe0f *SESSION HALTED*\n"
            f"Session: {session_id}\n"
            f"Reason: {reason}\n"
            f"_All trading stopped._"
        )

    # ── Command Handling ────────────────────────────────────────────────────

    def _register_defaults(self):
        """Register built-in command handlers."""
        self._command_handlers = {
            "/status": self._cmd_status,
            "/sessions": self._cmd_sessions,
            "/stop": self._cmd_stop,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/token": self._cmd_token,
            "/help": self._cmd_help,
        }

    def register_command(self, command: str, handler: Callable):
        """Register a custom command handler."""
        self._command_handlers[command] = handler

    def start_polling(self):
        """Start polling for commands in a background thread."""
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._running = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info("Telegram bot polling started")

    def stop_polling(self):
        """Stop polling."""
        self._running = False
        if self._poll_thread:
            self._poll_thread.join(timeout=5)

    def _poll_loop(self):
        """Long-poll for updates."""
        while self._running:
            try:
                updates = self._api(
                    "getUpdates",
                    offset=self._last_update_id + 1,
                    timeout=8,
                )
                if not updates:
                    continue
                for update in updates:
                    self._last_update_id = update["update_id"]
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))

                    # Only accept commands from the configured chat
                    if chat_id != self.chat_id:
                        continue

                    if text.startswith("/"):
                        self._handle_command(text)
            except Exception as e:
                logger.warning(f"Telegram poll error: {e}")
                import time
                time.sleep(5)

    def _handle_command(self, text: str):
        """Route a command to its handler."""
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]  # strip bot username if present
        args = parts[1] if len(parts) > 1 else ""

        handler = self._command_handlers.get(cmd)
        if handler:
            try:
                handler(args)
            except Exception as e:
                self.send(f"Command error: {e}")
                logger.error(f"Telegram command {cmd} failed: {e}")
        else:
            self.send(f"Unknown command: {cmd}\nType /help for available commands.")

    # ── Built-in Commands ───────────────────────────────────────────────────

    def _cmd_status(self, args: str):
        """Show status of active sessions."""
        sessions = db.query(
            "SELECT id, name, execution_mode, trading_mode, starting_capital, "
            "current_capital, secured_profit, current_day, total_days, status "
            "FROM sessions WHERE status IN ('active', 'paused') ORDER BY id"
        )
        if not sessions:
            self.send("No active sessions.")
            return

        lines = ["*Active Sessions*"]
        for s in sessions:
            total_pnl = s["current_capital"] + s["secured_profit"] - s["starting_capital"]
            pnl_pct = (total_pnl / s["starting_capital"]) * 100
            status_emoji = "\u25b6\ufe0f" if s["status"] == "active" else "\u23f8\ufe0f"
            label = s["name"] or f"Session {s['id']}"
            lines.append(
                f"\n{status_emoji} *{label}* (id={s['id']}, {s['status']})\n"
                f"  {s['execution_mode']}/{s['trading_mode']}\n"
                f"  Day {s['current_day']}/{s['total_days']}\n"
                f"  Capital: \u20b9{s['current_capital']:,.2f}\n"
                f"  P&L: \u20b9{total_pnl:,.2f} ({pnl_pct:+.1f}%)"
            )
        self.send("\n".join(lines))

    def _cmd_sessions(self, args: str):
        """List all sessions (including completed)."""
        sessions = db.query(
            "SELECT id, name, execution_mode, trading_mode, status, started_at FROM sessions ORDER BY id DESC LIMIT 10"
        )
        if not sessions:
            self.send("No sessions found.")
            return

        lines = ["*Recent Sessions*"]
        for s in sessions:
            label = s["name"] or f"session-{s['id']}"
            lines.append(f"  #{s['id']} {label} ({s['execution_mode']}/{s['trading_mode']}) - {s['status']} ({s['started_at'][:10]})")
        self.send("\n".join(lines))

    def _cmd_stop(self, args: str):
        """Stop a session. Usage: /stop <session_id>"""
        if not args.strip():
            self.send("Usage: /stop <session\\_id>")
            return

        try:
            sid = int(args.strip())
        except ValueError:
            self.send("Invalid session ID.")
            return

        session = db.query_one("SELECT status FROM sessions WHERE id = ?", (sid,))
        if not session:
            self.send(f"Session {sid} not found.")
            return

        if session["status"] not in ("active", "paused"):
            self.send(f"Session {sid} is already {session['status']}.")
            return

        db.update("sessions", sid, {"status": "halted", "ended_at": db.now_iso()})
        self.send(f"\u26d4 Session {sid} stopped.")

    def _cmd_pause(self, args: str):
        """Pause a session. Usage: /pause <session_id>"""
        if not args.strip():
            self.send("Usage: /pause <session\\_id>")
            return

        try:
            sid = int(args.strip())
        except ValueError:
            self.send("Invalid session ID.")
            return

        session = db.query_one("SELECT status FROM sessions WHERE id = ?", (sid,))
        if not session:
            self.send(f"Session {sid} not found.")
            return
        if session["status"] != "active":
            self.send(f"Session {sid} is {session['status']}, can only pause active sessions.")
            return

        db.update("sessions", sid, {"status": "paused"})
        self.send(f"\u23f8\ufe0f Session {sid} paused.")

    def _cmd_resume(self, args: str):
        """Resume a paused session. Usage: /resume <session_id>"""
        if not args.strip():
            self.send("Usage: /resume <session\\_id>")
            return

        try:
            sid = int(args.strip())
        except ValueError:
            self.send("Invalid session ID.")
            return

        session = db.query_one("SELECT status FROM sessions WHERE id = ?", (sid,))
        if not session:
            self.send(f"Session {sid} not found.")
            return
        if session["status"] != "paused":
            self.send(f"Session {sid} is {session['status']}, can only resume paused sessions.")
            return

        db.update("sessions", sid, {"status": "active"})
        self.send(f"\u25b6\ufe0f Session {sid} resumed.")

    def _cmd_token(self, args: str):
        """Update Kite access token live. Usage: /token <new_token>"""
        token = args.strip()
        if not token:
            self.send("Usage: /token <kite\\_access\\_token>")
            return

        # Update env
        os.environ["KITE_ACCESS_TOKEN"] = token

        # Update live Kite client immediately — no restart needed
        try:
            from aaitrade.tools.market import _kite, set_kite_client
            if _kite is not None:
                _kite.set_access_token(token)
                # Rebuild instrument cache with fresh token
                set_kite_client(_kite)
                self.send("\u2705 Kite token updated and applied live — no restart needed.")
            else:
                self.send("\u2705 Kite token saved. Kite client not active yet — will apply on next start.")
            logger.info("Kite access token updated live via Telegram")
        except Exception as e:
            self.send(f"\u26a0\ufe0f Token saved but live update failed: {e}")
            logger.error(f"Live token update failed: {e}")

    def _cmd_help(self, args: str):
        """Show available commands."""
        self.send(
            "*AAItrade Commands*\n"
            "/status - Show active sessions\n"
            "/sessions - List recent sessions\n"
            "/pause <id> - Pause a session\n"
            "/resume <id> - Resume a session\n"
            "/stop <id> - Stop a session\n"
            "/token <token> - Update Kite token\n"
            "/help - This message"
        )


# ── Module-level singleton ──────────────────────────────────────────────────

_bot: TelegramBot | None = None


def init_telegram(token: str | None = None, chat_id: str | None = None) -> TelegramBot | None:
    """Initialize the Telegram bot singleton."""
    global _bot
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.info("Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return None
    _bot = TelegramBot(token, chat_id)
    _bot.start_polling()
    return _bot


def get_bot() -> TelegramBot | None:
    """Get the Telegram bot instance."""
    return _bot
