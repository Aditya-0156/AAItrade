"""Session manager — orchestrates the decision loop.

Handles session lifecycle: start, run decision cycles on schedule,
check stop/loss conditions, end-of-day processing, session completion.
Integrates: holiday calendar, pause/resume, Telegram notifications.
"""

from __future__ import annotations

import json
import logging
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
        logger.info(f"  Duration: {self.config.total_days} days")
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
            "status": "active",
            "started_at": db.now_iso(),
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

        # Notify via Telegram
        bot = get_bot()
        if bot:
            bot.send(
                f"🚀 *New Session Started*\n"
                f"ID: {self.session_id}\n"
                f"Mode: {self.config.execution_mode.value}/{self.config.trading_mode.value}\n"
                f"Capital: ₹{self.config.starting_capital:,.2f}\n"
                f"Duration: {self.config.total_days} days"
            )

        logger.info(f"Session {self.session_id} started successfully")

    def _init_clients(self):
        """Initialize API clients."""
        # Claude client (use model from config if specified)
        model = getattr(self.config, 'model', 'claude-sonnet-4-6')
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

        # Kite client (if keys available)
        if self.keys.kite_api_key and self.keys.kite_access_token:
            try:
                from kiteconnect import KiteConnect
                kite = KiteConnect(api_key=self.keys.kite_api_key)
                kite.set_access_token(self.keys.kite_access_token)

                from aaitrade.tools.market import set_kite_client as set_market_kite
                from aaitrade.tools.watchlist_tools import set_kite_client as set_watchlist_kite
                from aaitrade.executor import set_kite_client as set_executor_kite

                set_market_kite(kite)
                set_watchlist_kite(kite)
                set_executor_kite(kite)
                logger.info("Kite Connect initialized")
            except Exception as e:
                logger.warning(f"Kite Connect initialization failed: {e}")
                logger.warning("Market data tools will not work without Kite")

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

    def run(self):
        """Run the trading session — the main loop."""
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

                # Handle halted/completed
                if status in ("halted", "completed"):
                    logger.info(f"Session is {status}.")
                    break

                # Handle paused — just sleep and re-check
                if status == "paused":
                    logger.debug("Session paused, waiting...")
                    time.sleep(30)
                    continue

                if session["current_day"] > session["total_days"]:
                    logger.info("Session duration complete.")
                    self._complete_session()
                    break

                now = datetime.now(_IST)  # Always use IST for market hours

                # Holiday/weekend check
                if not is_trading_day(now.date()):
                    logger.info(f"{now.date()} is not a trading day (IST). Sleeping until tomorrow...")
                    self._sleep_until_tomorrow()
                    continue

                # Pre-market: fetch macro news at 9:00 AM IST
                if now.hour == 9 and now.minute < 5:
                    logger.info("Pre-market: fetching macro news...")
                    get_macro_news()

                # Market hours: 9:15 AM to 3:30 PM IST
                market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
                market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

                if market_open <= now <= market_close:
                    # Trading window (skip first and last 15 mins as per rules)
                    safe_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
                    safe_close = now.replace(hour=15, minute=15, second=0, microsecond=0)

                    if safe_open <= now <= safe_close:
                        self._run_cycle()

                    # Check for end-of-day (after 3:30 PM)
                elif now.hour == 15 and now.minute >= 30 and now.minute < 45:
                    self._end_of_day()

                # Wait for next interval
                sleep_seconds = self.config.decision_interval_minutes * 60
                logger.debug(f"Sleeping {self.config.decision_interval_minutes} minutes until next cycle...")
                time.sleep(sleep_seconds)

        except KeyboardInterrupt:
            logger.info("Session interrupted by user.")
            self._complete_session()

    def _sleep_until_tomorrow(self):
        """Sleep until 8:55 AM IST next day."""
        now = datetime.now(_IST)
        tomorrow_morning = (now + timedelta(days=1)).replace(hour=8, minute=55, second=0, microsecond=0)
        sleep_secs = (tomorrow_morning - now).total_seconds()
        if sleep_secs > 0:
            logger.info(f"Sleeping {sleep_secs / 3600:.1f} hours until next morning")
            time.sleep(sleep_secs)

    def _run_cycle(self):
        """Run a single decision cycle."""
        # Re-check session status (may have been paused/stopped via Telegram)
        session_check = db.query_one(
            "SELECT status FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session_check or session_check["status"] != "active":
            return

        self.cycle_count += 1
        logger.info(f"{'─' * 40}")
        logger.info(f"Decision cycle {self.cycle_count}")

        # Check stop-loss conditions before running
        session = db.query_one(
            "SELECT starting_capital, current_capital FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if session:
            drawdown = ((session["starting_capital"] - session["current_capital"]) / session["starting_capital"]) * 100
            if drawdown >= self.config.risk_rules.session_stop_loss:
                logger.critical(f"Session drawdown at {drawdown:.1f}% — halting session")
                self.executor._halt_session("Session stop-loss reached")
                bot = get_bot()
                if bot:
                    bot.send_halt_alert("Session stop-loss reached", self.session_id)
                return

        # Build system prompt and briefing
        system_prompt = self.context.build_system_prompt()
        briefing = self.context.build_briefing(self.cycle_count)

        # Get Claude's decisions (list — may contain multiple BUY/SELL/HOLDs)
        decisions = self.claude.make_decision(
            system_prompt=system_prompt,
            briefing=briefing,
            session_id=self.session_id,
            cycle_number=self.cycle_count,
        )

        logger.info(f"Received {len(decisions)} decision(s) from Claude")

        # Execute each decision in sequence
        bot = get_bot()
        for decision in decisions:
            logger.info(
                f"Decision: {decision.get('action', 'N/A')} "
                f"{decision.get('symbol', '')} "
                f"[{decision.get('confidence', '')}]"
            )
            logger.info(f"Reason: {decision.get('reason', 'N/A')}")

            # Check for HALT_SESSION flag — stop processing further decisions
            if "HALT_SESSION" in decision.get("flags", []):
                logger.warning("HALT_SESSION flag received — halting session")
                self.executor._halt_session(decision.get("reason", "Claude requested halt"))
                if bot:
                    bot.send_halt_alert(decision.get("reason", "Claude requested halt"), self.session_id)
                return

            result = self.executor.execute(decision)
            logger.info(f"Result: {result.get('status', 'unknown')}")

            if bot and result.get("status") == "executed":
                bot.send_trade_alert(
                    action=decision.get("action", ""),
                    symbol=decision.get("symbol", ""),
                    quantity=result.get("quantity", 0),
                    price=result.get("price", 0),
                    reason=decision.get("reason", ""),
                    pnl=result.get("pnl"),
                    mode=result.get("mode", "paper"),
                )

            if result.get("status") == "halted":
                logger.info("Session halted by executor.")
                if bot:
                    bot.send_halt_alert(result.get("reason", "Unknown"), self.session_id)
                return  # Stop processing further decisions if session halted

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

        # Increment day
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

    def _complete_session(self):
        """Mark the session as completed."""
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
