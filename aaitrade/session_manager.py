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
import threading
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
from aaitrade.price_monitor import PriceMonitor

logger = logging.getLogger(__name__)

# 4 cycles per day: ~9:30, ~11:00, ~12:30, ~14:00
# Interval = 90 minutes between cycles to cover the 9:30-15:15 window
DEFAULT_CYCLE_INTERVAL_MINUTES = 90

# Global cycle lock — tool modules hold per-session context in module globals,
# so only ONE session's decision cycle may run at a time across all threads.
# Without this, session A's tool calls can silently execute against session B's
# ID when the server runs multiple sessions (or an alert cycle interleaves).
_CYCLE_LOCK = threading.Lock()


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
        self._premarket_done_date: str | None = None  # guard: pre-market tasks once per day
        self._research_done_date: str | None = None   # guard: weekend research once per day

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
                "model": getattr(self.config, "model", None),
                "planning_model": getattr(self.config, "planning_model", None),
            }),
        })

        # Initialize clients early for live sessions so the broker account can be
        # read BEFORE the watchlist is built (we must know which symbols are the
        # user's own before offering any of them to the trading engine).
        excluded: set[str] = set()
        if self.config.execution_mode == ExecutionMode.LIVE:
            try:
                self._init_clients()
                self._clients_ready = True
                from aaitrade.tools.market import _kite
                if _kite and getattr(self.config, "exclude_user_symbols", False):
                    from aaitrade.exclusions import refresh_from_broker
                    result = refresh_from_broker(self.session_id, _kite, initial=True)
                    excluded = set(result.get("excluded", []))
                    if excluded:
                        logger.warning(
                            f"User's personal holdings excluded from trading: {', '.join(sorted(excluded))}"
                        )
            except Exception as e:
                logger.error(f"Could not snapshot personal holdings at start: {e}")

        # Load watchlist into DB, skipping the user's own symbols
        watchlist = load_watchlist(self.config.watchlist_path)
        skipped = []
        for entry in watchlist:
            if entry.symbol in excluded:
                skipped.append(entry.symbol)
                continue
            db.insert("watchlist", {
                "session_id": self.session_id,
                "symbol": entry.symbol,
                "company": entry.company,
                "sector": entry.sector,
                "notes": entry.notes,
                "added_at": db.now_iso(),
                "add_reason": "Seed watchlist",
            })

        logger.info(
            f"Loaded {len(watchlist) - len(skipped)} stocks into watchlist"
            + (f" (skipped user-owned: {', '.join(skipped)})" if skipped else "")
        )

        # Load tool registry
        load_all_tools()

        # Disable watchlist adjustment tools if not allowed
        if not self.config.allow_watchlist_adjustment:
            disable_tool("add_to_watchlist")
            disable_tool("remove_from_watchlist")

        # Inject session_id into tool modules that need it
        from aaitrade.tools import portfolio_tools, memory, journal, watchlist_tools, session_memory, session_analysis, price_alerts, pipeline
        pipeline.set_session_id(self.session_id)
        portfolio_tools.set_session_id(self.session_id)
        memory.set_session_id(self.session_id)
        journal.set_session_id(self.session_id)
        watchlist_tools.set_session_id(self.session_id)
        session_memory.set_session_id(self.session_id)
        session_analysis.set_session_id(self.session_id)
        price_alerts.set_alert_context(self.session_id, 0)

        # Initialize clients (already done above for live sessions)
        if not getattr(self, "_clients_ready", False):
            self._init_clients()
            self._clients_ready = True

        # Validate watchlist symbols against Kite instrument cache
        self._validate_watchlist()

        # Start price monitor (background thread for inter-cycle alerts)
        self._price_monitor = PriceMonitor(
            session_id=self.session_id,
            trigger_callback=self._on_alert_triggered,
            max_position_loss_pct=getattr(self.config.risk_rules, "max_position_loss_pct", 1.5),
        )

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

                # Give price monitor the Kite client for efficient batch quotes
                if hasattr(self, '_price_monitor'):
                    self._price_monitor.set_kite_client(kite)
            except Exception as e:
                if is_live:
                    # Don't refuse to start — Kite tokens die daily, and sessions
                    # are often started on weekends/evenings for the next open.
                    # The session runs (research, scans, briefings via yfinance);
                    # the pre-market token check nags via Telegram until /token
                    # arrives, and update_kite_token() injects it live.
                    logger.warning(
                        f"LIVE session starting WITHOUT a working Kite token: {e}. "
                        "Trading will fail until the token is updated — send /token "
                        "before market open."
                    )
                    bot = get_bot()
                    if bot:
                        bot.send(
                            "⚠️ LIVE session started with a dead Kite token. "
                            "Research and scans will run, but NO trades can execute "
                            "until you send /token <request_token> (before 9:15 AM).",
                            parse_mode=None,
                        )
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
                from aaitrade.tools.fiidii import set_tavily_client as set_fiidii_tavily
                set_fiidii_tavily(tavily_client)
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
    # CONVICTION trades rarely and researches deeply — it does not need to look
    # every 90 minutes. Two cycles: a deep research/decision cycle after the
    # open settles, and an afternoon position review. Alerts cover the rest.
    CONVICTION_SLOTS = [(11, 0), (14, 30)]
    CYCLE_WINDOW_MINUTES = 89  # A slot is valid to run up to 89 min after its start time
    CYCLE_DURATION_MINUTES = 5  # Max time a cycle takes — don't start if next slot is within this

    @property
    def _slots(self) -> list[tuple[int, int]]:
        from aaitrade.config import TradingMode
        if self.config.trading_mode == TradingMode.CONVICTION:
            return self.CONVICTION_SLOTS
        return self.CYCLE_SLOTS

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
                # DB timestamps are naive IST strings (db.now_iso). Attach IST
                # directly — .astimezone() would wrongly assume system-local time
                # (e.g. UTC on the server), shifting comparisons by 5.5 hours.
                last_run_dt = datetime.fromisoformat(last_run["last"])
                if last_run_dt.tzinfo is None:
                    last_run_dt = last_run_dt.replace(tzinfo=_IST)
                else:
                    last_run_dt = last_run_dt.astimezone(_IST)
            except Exception:
                pass

        for i, (h, m) in enumerate(self._slots):
            slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)

            # Slot hasn't started yet today
            if now < slot_time:
                continue

            # Already past this slot's window (next slot already started)
            window_end = slot_time + timedelta(minutes=self.CYCLE_WINDOW_MINUTES)
            if now > window_end:
                continue

            # Check if next slot is starting very soon — don't start a cycle that would overlap
            if i + 1 < len(self._slots):
                next_h, next_m = self._slots[i + 1]
                next_slot = now.replace(hour=next_h, minute=next_m, second=0, microsecond=0)
                if (next_slot - now).total_seconds() < self.CYCLE_DURATION_MINUTES * 60:
                    continue

            # Already ran during this slot today?
            if last_run_dt and last_run_dt.date() == today and last_run_dt >= slot_time:
                continue

            return (h, m)

        return None

    def _seconds_until_next_event(self, now: datetime) -> int:
        """Return seconds until the next scheduled event.

        Events, in daily order: cycle slots (9:30/11:00/12:30/14:00),
        end-of-day processing (15:30), and tomorrow's pre-market (8:55).
        Previously this only knew about cycle slots — after the 14:00 slot it
        slept straight to the next morning, so EOD processing NEVER ran.
        """
        today_str = now.strftime("%Y-%m-%d")
        candidates: list[tuple[datetime, str]] = []

        # Today's pre-market window — so the Kite token health check and its
        # Telegram warning land BEFORE the 9:15 open, not at the 9:30 cycle.
        premarket = now.replace(hour=8, minute=55, second=0, microsecond=0)
        if premarket > now and self._premarket_done_date != today_str:
            candidates.append((premarket, "pre-market checks"))

        for h, m in self._slots:
            slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot_time > now:
                candidates.append((slot_time, f"cycle slot {h:02d}:{m:02d}"))

        eod_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if eod_time > now and self._eod_done_date != today_str:
            candidates.append((eod_time, "end-of-day processing"))

        # Post-close market scan slot
        scan_time = now.replace(hour=15, minute=50, second=0, microsecond=0)
        if scan_time > now:
            try:
                from aaitrade.scanner import already_scanned_today
                if not already_scanned_today():
                    candidates.append((scan_time, "post-close market scan"))
            except Exception:
                pass

        tomorrow_morning = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
        candidates.append((tomorrow_morning, "tomorrow pre-market"))

        next_time, label = min(candidates, key=lambda c: c[0])
        secs = max(int((next_time - now).total_seconds()), 1)
        logger.info(f"Sleeping {secs // 3600}h {(secs % 3600) // 60}m until {label} ({next_time.strftime('%H:%M IST')})")
        return secs

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

        # Restore cycle_count from DB on recovery so Claude sees correct cycle number
        if self._recovered:
            last_cycle = db.query_one(
                "SELECT MAX(cycle_number) as cn FROM decisions WHERE session_id = ?",
                (self.session_id,),
            )
            if last_cycle and last_cycle["cn"]:
                self.cycle_count = last_cycle["cn"]
                logger.info(f"Recovered cycle_count from DB: {self.cycle_count}")

        # Lazy-init the price monitor here so it works for both new sessions
        # (start() already created one — keep it) and recovered sessions
        # (start() was skipped — create it now). session_id is guaranteed set by this point.
        if not hasattr(self, '_price_monitor'):
            self._price_monitor = PriceMonitor(
                session_id=self.session_id,
                trigger_callback=self._on_alert_triggered,
                max_position_loss_pct=getattr(self.config.risk_rules, "max_position_loss_pct", 1.5),
            )
            # Wire up the Kite client if it's already initialised
            from aaitrade.tools import market as _market
            if _market._kite is not None:
                self._price_monitor.set_kite_client(_market._kite)

        self._price_monitor.start()

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

                # Holiday/weekend check — but weekends aren't dead time:
                # run the research cycle in the evening to build next-session outlook
                if not is_trading_day(now.date()):
                    self._maybe_run_offday_research(now)
                    research_time = now.replace(hour=17, minute=30, second=0, microsecond=0)
                    if now < research_time and self._research_done_date != now.strftime("%Y-%m-%d"):
                        target = research_time
                        label = "off-day research (17:30 IST)"
                    else:
                        target = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
                        label = "tomorrow pre-market"
                    secs = max(int((target - now).total_seconds()), 60)
                    logger.info(f"{now.date()} is not a trading day (IST). Sleeping until {label}...")
                    self._interruptible_sleep(secs)
                    continue

                # Pre-market tasks: token health check, macro news, FII/DII prefetch,
                # portfolio sync. Guarded to once per day; runs on any wake after 8:30
                # (including restarts mid-day), not just a narrow 9:00-9:05 window.
                if now.hour >= 8 and not (now.hour == 8 and now.minute < 30):
                    self._pre_market_tasks(now)

                # End-of-day: any time after 15:30 (once per day, guarded in _end_of_day)
                eod_start = now.replace(hour=15, minute=30, second=0, microsecond=0)
                if now >= eod_start:
                    try:
                        self._end_of_day()
                    except Exception as e:
                        logger.error(f"EOD processing failed: {e}", exc_info=True)

                    # Post-close full-market scan (~15:50+; internally guarded
                    # so multiple sessions / reruns don't repeat it)
                    scan_time = now.replace(hour=15, minute=50, second=0, microsecond=0)
                    if now >= scan_time:
                        try:
                            from aaitrade.scanner import run_daily_scan
                            run_daily_scan()
                        except Exception as e:
                            logger.error(f"Daily scan failed: {e}", exc_info=True)

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
                    if hasattr(self, '_price_monitor'):
                        self._price_monitor.notify_cycle_start()
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
                    finally:
                        if hasattr(self, '_price_monitor'):
                            self._price_monitor.notify_cycle_end()

                # Sleep until the next event (cycle slot, EOD, or tomorrow pre-market)
                now = datetime.now(_IST)
                sleep_seconds = self._seconds_until_next_event(now)
                self._interruptible_sleep(sleep_seconds)

        except KeyboardInterrupt:
            logger.info("Session interrupted by user.")
            self._complete_session()
        finally:
            # Stop price monitor when session ends
            if hasattr(self, '_price_monitor'):
                self._price_monitor.stop()

    def _on_alert_triggered(self, triggered_alerts: list[dict]):
        """Called by PriceMonitor when price alerts fire. Runs an ad-hoc cycle."""
        symbols = [a["symbol"] for a in triggered_alerts]
        logger.info(f"Price alert triggered for: {', '.join(symbols)} — running ad-hoc cycle")

        # HARD LOSS CAP breaches are executed by Python FIRST — this rule is
        # not up for debate. Claude is then woken to see the result and re-plan.
        for alert in triggered_alerts:
            if alert.get("kind") != "loss_cap":
                continue
            try:
                result = self.executor.execute({
                    "action": "SELL",
                    "symbol": alert["symbol"],
                    "quantity": alert.get("quantity"),
                    "reason": alert["reason"],
                    "confidence": "high",
                    "flags": [],
                })
                logger.critical(
                    f"Loss-cap forced SELL {alert['symbol']}: {result.get('status')} "
                    f"(P&L ₹{result.get('pnl', 'n/a')})"
                )
                alert["reason"] += f" [Forced sell status: {result.get('status')}]"
            except Exception as e:
                logger.error(f"Loss-cap forced sell failed for {alert['symbol']}: {e}")
                alert["reason"] += f" [FORCED SELL FAILED: {e} — exit manually via execute_trade]"

        # Send Telegram notification
        bot = get_bot()
        if bot:
            lines = []
            for a in triggered_alerts:
                lines.append(
                    f"• {a['symbol']}: ₹{a['current_price']} hit "
                    f"{a['direction']} ₹{a['target_price']} — {a['reason']}"
                )
            bot.send(
                f"🔔 *Price Alert Triggered*\n" + "\n".join(lines) +
                "\n\nRunning ad-hoc decision cycle..."
            )

        # Run an ad-hoc cycle with alert context
        try:
            self._price_monitor.notify_cycle_start()
            self._run_alert_cycle(triggered_alerts)
        except Exception as e:
            logger.error(f"Alert-triggered cycle failed: {e}", exc_info=True)
            if bot:
                bot.send(f"⚠️ Alert cycle failed: {e}")
        finally:
            self._price_monitor.notify_cycle_end()

    def _run_alert_cycle(self, triggered_alerts: list[dict]):
        """Run a mini decision cycle triggered by price alerts."""
        # Re-check session status
        session_check = db.query_one(
            "SELECT status FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session_check or session_check["status"] not in ("active", "closing"):
            return

        with _CYCLE_LOCK:
            is_closing = session_check["status"] == "closing"
            self.cycle_count += 1
            logger.info(f"{'─' * 40}")
            logger.info(f"Alert-triggered cycle {self.cycle_count}")

            # Point all shared tool modules at this session before any tool runs
            self._set_tool_context()

            # Build system prompt and briefing with alert context
            system_prompt = self.context.build_system_prompt(closing_mode=is_closing)
            briefing = self.context.build_briefing(
                self.cycle_count,
                alert_trigger=triggered_alerts,
            )

            # Inject executor into execute_trade tool — alert_mode=True bypasses the
            # 9:30-AM observe-only block so morning alerts can actually trade.
            from aaitrade.tools.trading import set_trading_context
            set_trading_context(self.executor, self.session_id, self.cycle_count, alert_mode=True)

            # Inject cycle number into price_alerts tools
            from aaitrade.tools.price_alerts import set_alert_context
            set_alert_context(self.session_id, self.cycle_count)

            # Get Claude's decisions
            try:
                decisions = self.claude.make_decision(
                    system_prompt=system_prompt,
                    briefing=briefing,
                    session_id=self.session_id,
                    cycle_number=self.cycle_count,
                )
            finally:
                # Clear alert_mode so subsequent scheduled cycles get the normal gating
                set_trading_context(self.executor, self.session_id, self.cycle_count, alert_mode=False)

        logger.info(f"Alert cycle: received {len(decisions)} decision(s)")

        bot = get_bot()
        for decision in decisions:
            action = decision.get("action", "").upper()
            if action in ("BUY", "SELL"):
                continue  # Already executed via execute_trade tool
            label = "TRADED" if action == "CYCLE_COMPLETE" else action
            logger.info(f"Alert cycle end [{label}] — {decision.get('reason', 'N/A')[:100]}")
            if "HALT_SESSION" in decision.get("flags", []):
                self.executor._halt_session(decision.get("reason", "Claude requested halt"))
                if bot:
                    bot.send_halt_alert(decision.get("reason", "Claude requested halt"), self.session_id)

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

    def _interruptible_sleep(self, seconds: int):
        """Sleep in 60s chunks, waking early if the session is stopped/paused.

        A dashboard 'stop' used to take until the next morning to be noticed
        when the thread was in a multi-hour time.sleep().
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            chunk = min(60, max(1, int(deadline - time.monotonic())))
            time.sleep(chunk)
            session = db.query_one(
                "SELECT status FROM sessions WHERE id = ?", (self.session_id,)
            )
            if not session or session["status"] not in ("active", "closing"):
                return

    def _pre_market_tasks(self, now: datetime):
        """Once-per-day morning tasks: token health, macro news, FII/DII, portfolio sync."""
        today_str = now.strftime("%Y-%m-%d")
        if self._premarket_done_date == today_str:
            return
        self._premarket_done_date = today_str
        logger.info("Running pre-market tasks...")
        bot = get_bot()

        # 0. Record the monthly broker-API subscription (idempotent per month)
        try:
            from aaitrade.costs import ensure_monthly_subscription
            ensure_monthly_subscription(self.session_id)
        except Exception as e:
            logger.warning(f"Subscription expense record failed: {e}")

        # 1. Kite token health check — Kite tokens die every morning (~7:30 AM IST).
        #    Catching this BEFORE market open beats discovering it mid-trade.
        from aaitrade.kite_auth import check_token_health
        from aaitrade.tools.market import _kite
        is_live = self.config.execution_mode == ExecutionMode.LIVE
        if is_live or _kite is not None:
            ok, msg = check_token_health()
            if not ok:
                logger.warning(f"Pre-market token check FAILED: {msg}")
                if bot:
                    severity = "🚨 LIVE session" if is_live else "⚠️ Paper session (Kite data)"
                    bot.send(
                        f"{severity} {self.session_id}: Kite token is DEAD.\n"
                        f"{msg}\n"
                        f"Send /token <request_token> before 9:15 AM or "
                        f"{'trading will fail' if is_live else 'data falls back to yfinance'}.",
                        parse_mode=None,
                    )
            else:
                logger.info(f"Pre-market token check OK: {msg}")

        # 1b. Retire alerts that have sat unfired for a week — a forgotten
        #     alert that finally triggers pulls Claude into a stale thesis.
        try:
            from aaitrade.tools.price_alerts import expire_stale_alerts
            expire_stale_alerts(self.session_id)
        except Exception as e:
            logger.warning(f"Alert expiry failed: {e}")

        # 2. Macro news
        try:
            get_macro_news()
        except Exception as e:
            logger.error(f"Macro news fetch failed: {e}")

        # 2b. Scan catch-up: if the post-close scan never ran (server was down,
        # fresh deploy, holiday restart), run it now — yesterday's closes are
        # exactly what the morning briefing needs anyway.
        try:
            from aaitrade.scanner import run_daily_scan
            latest = db.query_one("SELECT MAX(scan_date) as d FROM scan_results")
            stale = True
            if latest and latest["d"]:
                # Fresh only if it came from the previous trading day (or today).
                # A 3-day threshold let a single failed post-close scan leave the
                # session trading on stale data for days — which is exactly what
                # happened when the Kite token died on 31 Jul.
                from aaitrade.holidays import is_trading_day
                scan_date = datetime.fromisoformat(latest["d"]).date()
                probe, prev_trading = now.date() - timedelta(days=1), None
                for _ in range(7):
                    if is_trading_day(probe):
                        prev_trading = probe
                        break
                    probe -= timedelta(days=1)
                stale = scan_date < (prev_trading or now.date())
            if stale:
                logger.info("Scan missing/stale — running catch-up scan now")
                run_daily_scan()
        except Exception as e:
            logger.warning(f"Scan catch-up failed: {e}")

        # 3. FII/DII flows prefetch (cached — briefing reads from cache)
        try:
            from aaitrade.tools.fiidii import get_fiidii_flows
            get_fiidii_flows()
        except Exception as e:
            logger.warning(f"FII/DII prefetch failed: {e}")

        # 3b. Refresh personal-holding exclusions — catches anything the user
        #     bought by hand since yesterday, and releases what they sold.
        if is_live and _kite and getattr(self.config, "exclude_user_symbols", False):
            try:
                from aaitrade.exclusions import refresh_from_broker
                result = refresh_from_broker(self.session_id, _kite)
                if result.get("added"):
                    syms = ", ".join(result["added"])
                    logger.warning(f"New personal holdings excluded from trading: {syms}")
                    # Drop them from the watchlist so they can't be scanned
                    with db.get_connection() as conn:
                        for sym in result["added"]:
                            conn.execute(
                                "UPDATE watchlist SET removed_at = ?, remove_reason = ? "
                                "WHERE session_id = ? AND symbol = ? AND removed_at IS NULL",
                                (db.now_iso(), "User holds this personally", self.session_id, sym),
                            )
                    if bot:
                        bot.send(
                            f"🔒 Excluded from system trading (you own these): {syms}",
                            parse_mode=None,
                        )
                if result.get("released"):
                    logger.info(f"Exclusions lifted: {', '.join(result['released'])}")
            except Exception as e:
                logger.error(f"Exclusion refresh failed: {e}")

        # 4. Portfolio reconciliation (live only) — READ-ONLY, never mutates
        if is_live:
            try:
                from aaitrade.portfolio_sync import sync_portfolio_with_kite
                if _kite:
                    report = sync_portfolio_with_kite(self.session_id, _kite)
                    warnings = report.get("warnings") or []
                    if warnings and bot:
                        lines = "\n".join(
                            f"• {w['symbol']}: system thinks {w['db_qty']}, broker has {w['broker_qty']}"
                            for w in warnings
                        )
                        bot.send(
                            f"⚠️ Portfolio mismatch (NOT auto-corrected):\n{lines}\n"
                            f"The system will not sell more than it owns, but investigate.",
                            parse_mode=None,
                        )
                    ext = report.get("external_holdings") or []
                    if ext:
                        logger.info(
                            "Your personal holdings the system ignores: "
                            + ", ".join(f"{e['symbol']} x{e['external_qty']}" for e in ext)
                        )
            except Exception as e:
                logger.error(f"Portfolio reconciliation failed: {e}")

    def _maybe_run_offday_research(self, now: datetime):
        """On weekends/holidays after 17:30 IST, run the research cycle once.

        Produces a 'next-session outlook' (weekend news, geopolitics, policy
        shifts, predicted Monday reactions) that gets injected into the next
        trading day's briefings. Sunday's run supersedes Saturday's.
        """
        today_str = now.strftime("%Y-%m-%d")
        if self._research_done_date == today_str:
            return
        if now.hour < 17 or (now.hour == 17 and now.minute < 30):
            return
        self._research_done_date = today_str

        try:
            from aaitrade.research import run_offday_research
            outlook = run_offday_research(
                self.claude, self.session_id,
                model=getattr(self.config, "planning_model", None),
            )
            if outlook:
                logger.info("Off-day research complete — outlook saved for next session")
                bot = get_bot()
                if bot:
                    bot.send(f"🔭 *Next-Session Outlook Ready*\n{outlook[:800]}")
        except Exception as e:
            logger.error(f"Off-day research failed: {e}", exc_info=True)

    def _set_tool_context(self):
        """Point every tool module's globals at THIS session.

        Must be called inside _CYCLE_LOCK at the start of every cycle — module
        globals are shared across all sessions in the process.
        """
        from aaitrade.tools import (
            portfolio_tools, memory, journal, watchlist_tools,
            session_memory, session_analysis, pipeline,
        )
        pipeline.set_session_id(self.session_id)
        portfolio_tools.set_session_id(self.session_id)
        memory.set_session_id(self.session_id)
        journal.set_session_id(self.session_id)
        watchlist_tools.set_session_id(self.session_id)
        session_memory.set_session_id(self.session_id)
        session_analysis.set_session_id(self.session_id)

    def _run_cycle(self, closing_mode: bool = False):
        """Run a single decision cycle."""
        # Re-check session status (may have been paused/stopped via dashboard or Telegram)
        session_check = db.query_one(
            "SELECT status FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session_check or session_check["status"] not in ("active", "closing"):
            return

        with _CYCLE_LOCK:
            self._run_cycle_locked(closing_mode)

    def _run_cycle_locked(self, closing_mode: bool):
        """Body of a decision cycle. Caller must hold _CYCLE_LOCK."""
        self.cycle_count += 1
        logger.info(f"{'─' * 40}")
        logger.info(f"Decision cycle {self.cycle_count}" + (" [CLOSING MODE]" if closing_mode else ""))

        # Point all shared tool modules at this session before any tool runs
        self._set_tool_context()

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

        # Inject cycle number into price_alerts tools
        from aaitrade.tools.price_alerts import set_alert_context
        set_alert_context(self.session_id, self.cycle_count)

        # Model tiering: the 9:30 planning cycle (pre-11:00, observe-only) runs
        # on the stronger planning model — that's where deep reasoning pays.
        # Execution cycles stay on the cheap model.
        model_override = None
        now_ist = datetime.now(_IST)
        planning_model = getattr(self.config, "planning_model", None)
        from aaitrade.config import TradingMode
        if planning_model and planning_model != self.config.model:
            if self.config.trading_mode == TradingMode.CONVICTION:
                # The 11:00 cycle IS the research cycle here — that is where the
                # deep model earns its cost. The afternoon review is mechanical.
                if now_ist.hour < 13:
                    model_override = planning_model
                    logger.info(f"Conviction research cycle — using {planning_model}")
            elif now_ist.hour < 11:
                model_override = planning_model
                logger.info(f"Planning cycle — using {planning_model}")

        # Get Claude's decisions (list — may contain multiple BUY/SELL/HOLDs)
        decisions = self.claude.make_decision(
            system_prompt=system_prompt,
            briefing=briefing,
            session_id=self.session_id,
            cycle_number=self.cycle_count,
            model_override=model_override,
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

            # CYCLE_COMPLETE = trades happened, HOLD = no trades — both are informational only
            label = "TRADED" if action == "CYCLE_COMPLETE" else action
            logger.info(f"Cycle end [{label}] — {decision.get('reason', 'N/A')[:100]}")

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

        # Score the weekend/off-day outlook prediction against reality (once,
        # then expire the bias row so it can't be double-scored)
        try:
            bias_row = db.query_one(
                "SELECT id, summary FROM news_cache "
                "WHERE category = 'outlook' AND key = 'bias' AND expires_at > ? "
                "ORDER BY fetched_at DESC LIMIT 1",
                (db.now_iso(),),
            )
            if bias_row:
                from aaitrade.tools.market import get_market_snapshot
                snap = get_market_snapshot()
                nifty_chg = snap.get("nifty_50", {}).get("change_percent")
                if nifty_chg is not None and "error" not in snap:
                    from aaitrade.lessons import record_prediction_result
                    record_prediction_result(self.session_id, bias_row["summary"], nifty_chg)
                db.update("news_cache", bias_row["id"], {"expires_at": db.now_iso()})
        except Exception as e:
            logger.warning(f"Prediction scoring failed: {e}")

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
        """EOD risk review — FLAG positions that closed beyond stop/target.

        This used to FORCE-SELL at the stop price, which was wrong twice over:
        (1) the market is closed at EOD, so live orders would just be rejected;
        (2) auto-selling a routine -3% dip is the whipsaw that the hold-through-
        noise strategy exists to avoid — the user confirmed tight auto-stops
        lost money until positions got breathing room.

        Real protection comes from the price monitor: intraday stop breaches
        wake Claude for a judged decision (panic vs catastrophe), and the hard
        loss cap (≈-7.5% on a standard position) force-exits true disasters.
        This EOD pass only makes sure tomorrow's first cycle starts with eyes
        on every breach — the monitor re-wakes Claude at 9:15 if it persists.
        """
        positions = db.query(
            "SELECT id, symbol, quantity, avg_price, stop_loss_price, take_profit_price "
            "FROM portfolio WHERE session_id = ?",
            (self.session_id,),
        )
        bot = get_bot()

        for pos in positions:
            from aaitrade.tools.market import get_current_price
            price_data = get_current_price(pos["symbol"])
            if "error" in price_data:
                logger.warning(
                    f"EOD review: could not fetch price for {pos['symbol']} — skipped. "
                    f"Error: {price_data.get('error')}"
                )
                continue

            current_price = price_data["last_price"]

            if pos["stop_loss_price"] and current_price <= pos["stop_loss_price"]:
                pct = (current_price - pos["avg_price"]) / pos["avg_price"] * 100
                logger.warning(
                    f"EOD review: {pos['symbol']} closed at ₹{current_price} — below stop "
                    f"₹{pos['stop_loss_price']} ({pct:+.1f}% vs entry). Decision at open."
                )
                if bot:
                    bot.send(
                        f"⚠️ EOD: {pos['symbol']} closed below its stop level "
                        f"(₹{current_price} vs stop ₹{pos['stop_loss_price']}, {pct:+.1f}%). "
                        f"The system will decide at tomorrow's open — panic dips get held, "
                        f"broken companies get exited.",
                        parse_mode=None,
                    )

            elif pos["take_profit_price"] and current_price >= pos["take_profit_price"]:
                logger.info(
                    f"EOD review: {pos['symbol']} closed at ₹{current_price} — above target "
                    f"₹{pos['take_profit_price']}. Profit-take decision at open."
                )
                if bot:
                    bot.send(
                        f"💰 EOD: {pos['symbol']} closed above its target "
                        f"(₹{current_price} vs ₹{pos['take_profit_price']}). "
                        f"Profit-take will be decided at tomorrow's open.",
                        parse_mode=None,
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
