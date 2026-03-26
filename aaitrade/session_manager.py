"""Session manager — orchestrates the decision loop.

Handles session lifecycle: start, run decision cycles on schedule,
check stop/loss conditions, end-of-day processing, session completion.
Integrates: holiday calendar, pause/resume, closing mode, Telegram notifications.

Sessions are endless by default — they run until the user initiates closing
mode from the dashboard. Closing mode allows only HOLD/SELL actions and
exits positions over 1-10 market days.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# IST = UTC+5:30
_IST = timezone(timedelta(hours=5, minutes=30))

from aaitrade import db
from aaitrade.config import SessionConfig, ExecutionMode, load_watchlist, APIKeys
from aaitrade.claude_client import ClaudeClient
from aaitrade.context_builder import ContextBuilder
from aaitrade.executor import Executor
from aaitrade.holidays import is_trading_day
from aaitrade.reporter import Reporter
from aaitrade.telegram_bot import get_bot
from aaitrade.tools import load_all_tools, disable_tool
from aaitrade.tools.news import get_macro_news

logger = logging.getLogger(__name__)

# 4 cycles per day: ~9:30, ~11:00, ~12:30, ~14:00
# Interval = 90 minutes between cycles to cover the 9:30-15:15 window
DEFAULT_CYCLE_INTERVAL_MINUTES = 90


class SessionManager:
    """Manages a complete trading session."""

    def __init__(self, config: SessionConfig, keys: APIKeys, name: str | None = None):
        self.config = config
        self.keys = keys
        self.name = name  # human-readable name (e.g. "balanced-14d")
        self.session_id: int | None = None
        self.cycle_count = 0
        self._recovered = False  # set True by multi_session recovery
        self._eod_done_date: str | None = None  # guard: run EOD at most once per calendar day

    def start(self):
        """Initialize and start a new trading session."""
        logger.info("=" * 60)
        logger.info(f"Starting AAItrade session")
        logger.info(f"  Mode: {self.config.execution_mode.value} + {self.config.trading_mode.value}")
        logger.info(f"  Capital: ₹{self.config.starting_capital:,.2f}")
        logger.info(f"  Duration: Endless (user-controlled)")
        logger.info("=" * 60)

        # Initialize database
        db.init_db()

        # Create session record
        self.session_id = db.insert("sessions", {
            "name": self.name,
            "execution_mode": self.config.execution_mode.value,
            "trading_mode": self.config.trading_mode.value,
            "starting_capital": self.config.starting_capital,
            "current_capital": self.config.starting_capital,
            "secured_profit": 0,
            "total_days": self.config.total_days,
            "current_day": 1,
            "watchlist_path": str(self.config.watchlist_path),
            "allow_watchlist_adjustment": int(self.config.allow_watchlist_adjustment),
            "profit_reinvest_ratio": self.config.profit_reinvest_ratio,
            "status": "active",
            "started_at": db.now_iso(),
            # Risk settings
            "stop_loss_pct": self.config.risk_rules.stop_loss,
            "take_profit_pct": self.config.risk_rules.take_profit,
            "max_positions": self.config.risk_rules.max_positions,
            "max_per_trade_pct": self.config.risk_rules.max_per_trade,
            "max_deployed_pct": self.config.risk_rules.max_deployed,
            "daily_loss_limit_pct": self.config.risk_rules.daily_loss_limit,
            "config_json": json.dumps({
                "execution_mode": self.config.execution_mode.value,
                "trading_mode": self.config.trading_mode.value,
                "starting_capital": self.config.starting_capital,
                "total_days": self.config.total_days,
                "decision_interval_minutes": self.config.decision_interval_minutes,
            }),
        })

        # Load watchlist into DB
        watchlist = load_watchlist(self.config.watchlist_path)
        for entry in watchlist:
            db.insert("watchlist", {
                "session_id": self.session_id,
                "symbol": entry.symbol,
                "company": entry.company,
                "sector": entry.sector,
                "notes": entry.notes,
                "added_at": db.now_iso(),
                "add_reason": "Seed watchlist",
            })

        logger.info(f"Loaded {len(watchlist)} stocks into watchlist")

        # Load tool registry
        load_all_tools()

        # Disable watchlist adjustment tools if not allowed
        if not self.config.allow_watchlist_adjustment:
            disable_tool("add_to_watchlist")
            disable_tool("remove_from_watchlist")

        # Inject session_id into tool modules that need it
        from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools, session_memory
        portfolio_tools.set_session_id(self.session_id)
        memory.set_session_id(self.session_id)
        journal.set_session_id(self.session_id)
        watchlist_tools.set_session_id(self.session_id)
        session_memory.set_session_id(self.session_id)

        # Initialize clients
        self._init_clients()

        # Validate watchlist symbols against Kite instrument cache
        self._validate_watchlist()

        # Notify via Telegram
        bot = get_bot()
        if bot:
            bot.send(
                f"🚀 *New Session Started*\n"
                f"ID: {self.session_id}\n"
                f"Mode: {self.config.execution_mode.value}/{self.config.trading_mode.value}\n"
                f"Capital: ₹{self.config.starting_capital:,.2f}\n"
                f"Duration: Endless (close from dashboard)"
            )

        logger.info(f"Session {self.session_id} started successfully")

    def _validate_watchlist(self):
        """Check watchlist symbols against Kite instrument cache. Log warnings for invalid ones."""
        from aaitrade.tools.market import _instrument_token_cache
        if not _instrument_token_cache:
            logger.warning("Kite instrument cache not available — skipping watchlist validation")
            return

        entries = db.query(
            "SELECT id, symbol FROM watchlist WHERE session_id = ? AND removed_at IS NULL",
            (self.session_id,),
        )
        invalid = []
        for entry in entries:
            if entry["symbol"] not in _instrument_token_cache:
                invalid.append(entry["symbol"])
                logger.warning(f"Watchlist symbol '{entry['symbol']}' NOT found in Kite NSE instruments!")

        if invalid:
            logger.warning(
                f"{len(invalid)} watchlist symbol(s) not found on Kite: {', '.join(invalid)}. "
                f"These will fail at trade time. Fix watchlist_seed.yaml."
            )
            bot = get_bot()
            if bot:
                bot.send(
                    f"⚠️ {len(invalid)} watchlist symbols not found on Kite: "
                    f"{', '.join(invalid)}. Fix config/watchlist_seed.yaml.",
                    parse_mode=None,
                )
        else:
            logger.info(f"All {len(entries)} watchlist symbols validated against Kite ✓")

    def _init_clients(self):
        """Initialize API clients."""
        # Claude client (use model from config if specified)
        model = getattr(self.config, 'model', 'claude-haiku-4-5-20251001')
        self.claude = ClaudeClient(
            api_key=self.keys.anthropic,
            model=model,
            max_tool_rounds=self.config.max_tool_calls_per_cycle,
        )

        # Context builder
        self.context = ContextBuilder(self.config, self.session_id)

        # Executor
        self.executor = Executor(self.config, self.session_id)

        # Reporter
        self.reporter = Reporter(self.config, self.session_id, self.claude)

        # Kite client — required for live mode, optional for paper (falls back to yfinance)
        is_live = self.config.execution_mode.value == "live"

        if is_live and (not self.keys.kite_api_key or not self.keys.kite_access_token):
            raise RuntimeError(
                "KITE_API_KEY and KITE_ACCESS_TOKEN must be set in .env for live trading. "
                "Run refresh_token.py to get a fresh token."
            )

        if self.keys.kite_api_key and self.keys.kite_access_token:
            try:
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=self.keys.kite_api_key, timeout=15)
                kite.set_access_token(self.keys.kite_access_token)

                # Validate token works before proceeding
                profile = kite.profile()
                logger.info(f"Kite Connect initialized — logged in as {profile['user_name']}")

                from aaitrade.tools.market import set_kite_client as set_market_kite
                from aaitrade.tools.watchlist_tools import set_kite_client as set_watchlist_kite
                from aaitrade.executor import set_kite_client as set_executor_kite

                set_market_kite(kite)
                set_watchlist_kite(kite)
                set_executor_kite(kite)
            except Exception as e:
                if is_live:
                    raise RuntimeError(f"Kite Connect initialization failed: {e}. Check .env keys.")
                else:
                    logger.warning(
                        f"Kite Connect unavailable (token expired?): {e}. "
                        "Paper session will use yfinance for market data."
                    )

        # NewsAPI client
        if self.keys.newsapi:
            try:
                from newsapi import NewsApiClient
                newsapi_client = NewsApiClient(api_key=self.keys.newsapi)
                from aaitrade.tools.news import set_newsapi_client
                set_newsapi_client(newsapi_client)
                logger.info("NewsAPI initialized")
            except Exception as e:
                logger.warning(f"NewsAPI initialization failed: {e}")

        # Tavily client
        if self.keys.tavily:
            try:
                from tavily import TavilyClient
                tavily_client = TavilyClient(api_key=self.keys.tavily)
                from aaitrade.tools.search import set_tavily_client
                set_tavily_client(tavily_client)
                logger.info("Tavily search initialized")
            except Exception as e:
                logger.warning(f"Tavily initialization failed: {e}")

        # Anthropic client for news summarization (Haiku)
        if self.keys.anthropic:
            import anthropic
            haiku_client = anthropic.Anthropic(api_key=self.keys.anthropic)
            from aaitrade.tools.news import set_anthropic_client
            set_anthropic_client(haiku_client)
            from aaitrade.tools.session_memory import set_anthropic_client as set_memory_anthropic_client
            set_memory_anthropic_client(haiku_client)

        # HuggingFace summarizer for large tool outputs
        from aaitrade.summarizer import init_summarizer
        hf_token = os.environ.get("HF_API_TOKEN", "")
        if hf_token:
            init_summarizer(hf_token)
        else:
            logger.info("HuggingFace summarizer not configured (no HF_API_TOKEN in .env)")

    # Fixed cycle slots: (hour, minute) in IST
    CYCLE_SLOTS = [(9, 30), (11, 0), (12, 30), (14, 0)]
    CYCLE_WINDOW_MINUTES = 89  # A slot is valid to run up to 89 min after its start time
    CYCLE_DURATION_MINUTES = 5  # Max time a cycle takes — don't start if next slot is within this

    def _get_due_slot(self, now: datetime) -> tuple[int, int] | None:
        """Return the slot (hour, min) that is due to run right now, or None.

        A slot is due if:
        - Its scheduled time has passed today
        - It hasn't run yet today (last cycle ran before this slot's time)
        - We're still within its 89-min window (before the next slot starts)
        - The next slot isn't starting within CYCLE_DURATION_MINUTES
        """
        today = now.date()
        interval = self.config.decision_interval_minutes  # default 90

        # Get last cycle run time from DB (stored as ISO string in decisions table)
        last_run = db.query_one(
            "SELECT MAX(decided_at) as last FROM decisions WHERE session_id = ?",
            (self.session_id,),
        )
        last_run_dt = None
        if last_run and last_run["last"]:
            try:
                last_run_dt = datetime.fromisoformat(last_run["last"]).astimezone(_IST)
            except Exception:
                pass

        for i, (h, m) in enumerate(self.CYCLE_SLOTS):
            slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)

            # Slot hasn't started yet today
            if now < slot_time:
                continue

            # Already past this slot's window (next slot already started)
            window_end = slot_time + timedelta(minutes=self.CYCLE_WINDOW_MINUTES)
            if now > window_end:
                continue

            # Check if next slot is starting very soon — don't start a cycle that would overlap
            if i + 1 < len(self.CYCLE_SLOTS):
                next_h, next_m = self.CYCLE_SLOTS[i + 1]
                next_slot = now.replace(hour=next_h, minute=next_m, second=0, microsecond=0)
                if (next_slot - now).total_seconds() < self.CYCLE_DURATION_MINUTES * 60:
                    continue

            # Already ran during this slot today?
            if last_run_dt and last_run_dt.date() == today and last_run_dt >= slot_time:
                continue

            return (h, m)

        return None

    def _seconds_until_next_slot(self, now: datetime) -> int:
        """Return seconds to sleep until the next cycle slot or market open."""
        today = now.date()

        # Find next slot today
        for h, m in self.CYCLE_SLOTS:
            slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot_time > now:
                secs = int((slot_time - now).total_seconds())
                logger.info(f"Sleeping {secs // 60}m {secs % 60}s until next cycle slot ({h:02d}:{m:02d} IST)...")
                return max(secs, 1)

        # All slots done today — sleep until tomorrow 9:00 AM
        logger.info("All cycles done for today. Sleeping until tomorrow 9:00 AM IST...")
        return self._sleep_until_tomorrow(dry_run=True)

    def run(self):
        """Run the trading session — the main loop.

        Sessions run endlessly until:
        - User stops/halts from dashboard → status becomes 'halted'
        - User initiates closing mode → status becomes 'closing' → exits positions → 'completed'
        - Session stop-loss hit → 'halted'

        Cycle scheduling:
        - Fixed slots: 9:30, 11:00, 12:30, 14:00 IST
        - Each slot has a 89-min window to run (covers the full gap between slots)
        - On restart: checks which slot is currently due and runs it if it hasn't run yet today
        - Tracks last cycle time via decisions table — survives restarts
        """
        logger.info("Session running. Waiting for market hours...")

        try:
            while True:
                session = db.query_one(
                    "SELECT status, current_day, total_days FROM sessions WHERE id = ?",
                    (self.session_id,),
                )
                if not session:
                    logger.info("Session record not found.")
                    break

                status = session["status"]

                # Handle halted/completed — exit the loop
                if status in ("halted", "completed"):
                    logger.info(f"Session is {status}.")
                    break

                # Handle paused — just sleep and re-check
                if status == "paused":
                    logger.debug("Session paused, waiting...")
                    time.sleep(30)
                    continue

                is_closing = (status == "closing")
                now = datetime.now(_IST)

                # Holiday/weekend check
                if not is_trading_day(now.date()):
                    logger.info(f"{now.date()} is not a trading day (IST). Sleeping until tomorrow...")
                    self._sleep_until_tomorrow()
                    continue

                # Pre-market: fetch macro news + portfolio sync at 9:00-9:05 AM IST
                if now.hour == 9 and now.minute < 5:
                    logger.info("Pre-market: fetching macro news...")
                    try:
                        get_macro_news()
                    except Exception as e:
                        logger.error(f"Macro news fetch failed: {e}")

                    if self.config.execution_mode == ExecutionMode.LIVE:
                        try:
                            from aaitrade.portfolio_sync import sync_portfolio_with_kite
                            from aaitrade.tools.market import _kite
                            if _kite:
                                sync_result = sync_portfolio_with_kite(self.session_id, _kite)
                                if sync_result.get("discrepancies"):
                                    bot = get_bot()
                                    if bot:
                                        n = len(sync_result["discrepancies"])
                                        bot.send(f"📊 Portfolio sync: {n} discrepancy(ies) corrected")
                        except Exception as e:
                            logger.error(f"Portfolio sync failed: {e}")

                # End-of-day: after 3:30 PM
                eod_start = now.replace(hour=15, minute=30, second=0, microsecond=0)
                eod_end   = now.replace(hour=15, minute=45, second=0, microsecond=0)
                if eod_start <= now <= eod_end:
                    try:
                        self._end_of_day()
                    except Exception as e:
                        logger.error(f"EOD processing failed: {e}", exc_info=True)

                    if is_closing:
                        positions = db.query(
                            "SELECT COUNT(*) as cnt FROM portfolio WHERE session_id = ? AND quantity > 0",
                            (self.session_id,),
                        )
                        if not positions or positions[0]["cnt"] == 0:
                            logger.info("Closing mode: all positions exited. Completing session.")
                            self._complete_session()
                            break

                # Check if a cycle slot is due right now
                due_slot = self._get_due_slot(now)
                if due_slot:
                    h, m = due_slot
                    logger.info(f"Running cycle for slot {h:02d}:{m:02d} IST")
                    try:
                        pre_cycle_state = self._snapshot_state()
                        self._run_cycle(closing_mode=is_closing)
                    except Exception as e:
                        logger.error(f"Cycle failed (restoring pre-cycle state): {e}", exc_info=True)
                        try:
                            self._restore_state(pre_cycle_state)
                            logger.info("Pre-cycle state restored successfully")
                        except Exception as restore_err:
                            logger.error(f"State restoration also failed: {restore_err}")
                        bot = get_bot()
                        if bot:
                            bot.send(f"⚠️ Cycle error in session {self.session_id}: {e}. State restored.")

                # Sleep until next slot (or tomorrow if all slots done)
                now = datetime.now(_IST)
                sleep_seconds = self._seconds_until_next_slot(now)
                time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            logger.info("Session interrupted by user.")
            self._complete_session()

    def _snapshot_state(self) -> dict:
        """Capture session state before a cycle for recovery purposes."""
        session = db.query_one(
            "SELECT current_capital, secured_profit FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        positions = db.query(
            "SELECT id, symbol, quantity, avg_price, stop_loss_price, take_profit_price "
            "FROM portfolio WHERE session_id = ?",
            (self.session_id,),
        )
        return {
            "current_capital": session["current_capital"] if session else 0,
            "secured_profit": session["secured_profit"] if session else 0,
            "positions": [dict(p) for p in positions],
            "cycle_count": self.cycle_count,
        }

    def _restore_state(self, snapshot: dict):
        """Restore session to a previous snapshot state."""
        # Restore session capital
        db.update("sessions", self.session_id, {
            "current_capital": snapshot["current_capital"],
            "secured_profit": snapshot["secured_profit"],
        })

        # Restore positions — delete any new ones, revert modified ones
        current_positions = db.query(
            "SELECT id, symbol FROM portfolio WHERE session_id = ?",
            (self.session_id,),
        )
        snapshot_ids = {p["id"] for p in snapshot["positions"]}

        # Remove positions that didn't exist in snapshot
        for pos in current_positions:
            if pos["id"] not in snapshot_ids:
                with db.get_connection() as conn:
                    conn.execute("DELETE FROM portfolio WHERE id = ?", (pos["id"],))

        # Restore snapshot positions
        for snap_pos in snapshot["positions"]:
            existing = db.query_one(
                "SELECT id FROM portfolio WHERE id = ?", (snap_pos["id"],)
            )
            if existing:
                db.update("portfolio", snap_pos["id"], {
                    "quantity": snap_pos["quantity"],
                    "avg_price": snap_pos["avg_price"],
                    "stop_loss_price": snap_pos["stop_loss_price"],
                    "take_profit_price": snap_pos["take_profit_price"],
                })
            else:
                # Position was deleted during the failed cycle — re-insert
                db.insert("portfolio", {
                    "session_id": self.session_id,
                    "symbol": snap_pos["symbol"],
                    "quantity": snap_pos["quantity"],
                    "avg_price": snap_pos["avg_price"],
                    "stop_loss_price": snap_pos["stop_loss_price"],
                    "take_profit_price": snap_pos["take_profit_price"],
                    "opened_at": db.now_iso(),
                })

        self.cycle_count = snapshot["cycle_count"]
        logger.info("State snapshot restored")

    def _sleep_until_tomorrow(self, dry_run: bool = False) -> int:
        """Sleep until 8:55 AM IST next day. Returns seconds to sleep."""
        now = datetime.now(_IST)
        tomorrow_morning = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
        sleep_secs = int((tomorrow_morning - now).total_seconds())
        if sleep_secs > 0 and not dry_run:
            logger.info(f"Sleeping {sleep_secs / 3600:.1f} hours until next morning")
            time.sleep(sleep_secs)
        return max(sleep_secs, 1)

    def _run_cycle(self, closing_mode: bool = False):
        """Run a single decision cycle."""
        # Re-check session status (may have been paused/stopped via dashboard or Telegram)
        session_check = db.query_one(
            "SELECT status FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session_check or session_check["status"] not in ("active", "closing"):
            return

        self.cycle_count += 1
        logger.info(f"{'─' * 40}")
        logger.info(f"Decision cycle {self.cycle_count}" + (" [CLOSING MODE]" if closing_mode else ""))

        # Check stop-loss conditions before running
        session = db.query_one(
            "SELECT starting_capital, current_capital FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if session:
            # Drawdown = starting capital vs (free cash + deployed positions at cost)
            deployed = db.query(
                "SELECT SUM(quantity * avg_price) as total FROM portfolio WHERE session_id = ?",
                (self.session_id,),
            )
            deployed_value = deployed[0]["total"] if deployed and deployed[0]["total"] else 0
            total_value = session["current_capital"] + deployed_value
            drawdown = ((session["starting_capital"] - total_value) / session["starting_capital"]) * 100
            if drawdown >= self.config.risk_rules.session_stop_loss:
                logger.critical(f"Session drawdown at {drawdown:.1f}% — halting session")
                self.executor._halt_session("Session stop-loss reached")
                bot = get_bot()
                if bot:
                    bot.send_halt_alert("Session stop-loss reached", self.session_id)
                return

        # Build system prompt and briefing
        system_prompt = self.context.build_system_prompt(closing_mode=closing_mode)
        briefing = self.context.build_briefing(self.cycle_count)

        # Inject executor into execute_trade tool so it can run trades during Claude's reasoning
        from aaitrade.tools.trading import set_trading_context
        set_trading_context(self.executor, self.session_id, self.cycle_count)

        # Get Claude's decisions (list — may contain multiple BUY/SELL/HOLDs)
        decisions = self.claude.make_decision(
            system_prompt=system_prompt,
            briefing=briefing,
            session_id=self.session_id,
            cycle_number=self.cycle_count,
        )

        logger.info(f"Received {len(decisions)} decision(s) from Claude")

        # BUY/SELL trades are executed by Claude via the execute_trade tool during its
        # reasoning loop. The final JSON only carries HOLD decisions and session flags.
        bot = get_bot()
        for decision in decisions:
            action = decision.get("action", "").upper()

            # Skip BUY/SELL — already executed via execute_trade tool call
            if action in ("BUY", "SELL"):
                logger.debug(f"Skipping {action} {decision.get('symbol')} from final JSON — handled by execute_trade tool")
                continue

            logger.info(f"Decision: {action} [{decision.get('confidence', '')}] — {decision.get('reason', 'N/A')[:80]}")

            # Check for HALT_SESSION flag
            if "HALT_SESSION" in decision.get("flags", []):
                logger.warning("HALT_SESSION flag received — halting session")
                self.executor._halt_session(decision.get("reason", "Claude requested halt"))
                if bot:
                    bot.send_halt_alert(decision.get("reason", "Claude requested halt"), self.session_id)
                return

    def _end_of_day(self):
        """Run end-of-day processing — guarded to fire at most once per calendar day."""
        today_str = datetime.now(_IST).strftime("%Y-%m-%d")
        if self._eod_done_date == today_str:
            logger.debug("EOD already processed today, skipping duplicate call.")
            return
        self._eod_done_date = today_str
        logger.info("End of day — generating summary...")

        # Check auto stop-loss on open positions (paper mode)
        self._check_stop_loss_triggers()

        # Generate EOD report
        summary = self.reporter.generate_daily_summary()

        # Send via Telegram
        bot = get_bot()
        if bot and summary:
            bot.send_daily_summary(summary)

        # Increment day counter (for tracking purposes — sessions are endless)
        session = db.query_one(
            "SELECT current_day FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if session:
            db.update("sessions", self.session_id, {
                "current_day": session["current_day"] + 1,
            })

    def _check_stop_loss_triggers(self):
        """Check if any open positions hit their stop-loss or take-profit."""
        positions = db.query(
            "SELECT id, symbol, quantity, avg_price, stop_loss_price, take_profit_price "
            "FROM portfolio WHERE session_id = ?",
            (self.session_id,),
        )

        for pos in positions:
            from aaitrade.tools.market import get_current_price
            price_data = get_current_price(pos["symbol"])
            if "error" in price_data:
                logger.warning(
                    f"EOD stop-loss check: could not fetch price for {pos['symbol']} "
                    f"(held: {pos['quantity']} shares @ avg ₹{pos['avg_price']}). "
                    f"Stop-loss/take-profit NOT evaluated — position stays open. "
                    f"Error: {price_data.get('error')}"
                )
                bot = get_bot()
                if bot:
                    bot.send(
                        f"⚠️ EOD: Could not fetch price for *{pos['symbol']}*. "
                        f"Stop-loss not evaluated — position remains open."
                    )
                continue

            current_price = price_data["last_price"]

            # Stop-loss hit
            if pos["stop_loss_price"] and current_price <= pos["stop_loss_price"]:
                logger.warning(f"STOP-LOSS triggered for {pos['symbol']} at ₹{current_price}")
                decision = {
                    "action": "SELL",
                    "symbol": pos["symbol"],
                    "quantity": pos["quantity"],
                    "reason": f"Stop-loss triggered at ₹{current_price} (stop was ₹{pos['stop_loss_price']})",
                    "confidence": "high",
                    "flags": [],
                }
                result = self.executor.execute(decision)

                bot = get_bot()
                if bot and result.get("status") == "executed":
                    bot.send_trade_alert(
                        action="SELL", symbol=pos["symbol"],
                        quantity=pos["quantity"], price=current_price,
                        reason="Stop-loss triggered", pnl=result.get("pnl"),
                        mode=result.get("mode", "paper"),
                    )

            # Take-profit hit
            elif pos["take_profit_price"] and current_price >= pos["take_profit_price"]:
                logger.info(f"TAKE-PROFIT triggered for {pos['symbol']} at ₹{current_price}")
                decision = {
                    "action": "SELL",
                    "symbol": pos["symbol"],
                    "quantity": pos["quantity"],
                    "reason": f"Take-profit triggered at ₹{current_price} (target was ₹{pos['take_profit_price']})",
                    "confidence": "high",
                    "flags": [],
                }
                result = self.executor.execute(decision)

                bot = get_bot()
                if bot and result.get("status") == "executed":
                    bot.send_trade_alert(
                        action="SELL", symbol=pos["symbol"],
                        quantity=pos["quantity"], price=current_price,
                        reason="Take-profit triggered", pnl=result.get("pnl"),
                        mode=result.get("mode", "paper"),
                    )

    def _close_all_positions(self):
        """Force-close all open positions at end of session."""
        positions = db.query(
            "SELECT id, symbol, quantity, avg_price FROM portfolio WHERE session_id = ? AND quantity > 0",
            (self.session_id,),
        )
        if not positions:
            return

        logger.info(f"Closing {len(positions)} open position(s) at session end...")
        for pos in positions:
            from aaitrade.tools.market import get_current_price
            price_data = get_current_price(pos["symbol"])
            price = price_data.get("last_price", pos["avg_price"]) if "error" not in price_data else pos["avg_price"]

            decision = {
                "action": "SELL",
                "symbol": pos["symbol"],
                "quantity": pos["quantity"],
                "reason": "Session ended — closing all positions",
                "confidence": "high",
                "flags": [],
            }
            result = self.executor.execute(decision)
            logger.info(f"  Closed {pos['symbol']}: {result.get('status')} @ ₹{price:.2f}")

            bot = get_bot()
            if bot and result.get("status") == "executed":
                bot.send_trade_alert(
                    action="SELL", symbol=pos["symbol"],
                    quantity=pos["quantity"], price=price,
                    reason="Session ended — all positions closed",
                    pnl=result.get("pnl"),
                    mode=result.get("mode", "paper"),
                )

    def _complete_session(self):
        """Mark the session as completed.

        Does NOT force-sell open positions. Performance is calculated using
        current market prices for any remaining holdings (mark-to-market).
        """
        # Calculate mark-to-market value of open positions for the final report
        positions = db.query(
            "SELECT id, symbol, quantity, avg_price FROM portfolio WHERE session_id = ? AND quantity > 0",
            (self.session_id,),
        )
        if positions:
            unrealized_pnl = 0
            for pos in positions:
                from aaitrade.tools.market import get_current_price
                price_data = get_current_price(pos["symbol"])
                if "error" not in price_data:
                    current_price = price_data["last_price"]
                    unrealized_pnl += (current_price - pos["avg_price"]) * pos["quantity"]
                else:
                    logger.warning(f"Could not get price for {pos['symbol']} at session end — using avg_price for P&L")

            logger.info(
                f"Session ending with {len(positions)} open position(s). "
                f"Unrealized P&L: ₹{unrealized_pnl:,.2f}"
            )

        db.update("sessions", self.session_id, {
            "status": "completed",
            "ended_at": db.now_iso(),
        })
        logger.info("Session completed.")

        # Final summary
        report = self.reporter.generate_session_report()

        # Send via Telegram
        bot = get_bot()
        if bot and report:
            bot.send_session_report(report)
