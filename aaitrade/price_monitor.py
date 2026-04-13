"""Price monitor — background thread that watches price alerts between cycles.

Polls prices every 30 seconds for symbols with active alerts. When a target
is hit, triggers an ad-hoc Claude cycle so it can act immediately.

Timing guards:
- Won't trigger within 10 minutes of a scheduled cycle slot
- Won't trigger if a cycle is currently running
- Won't trigger outside market hours (9:15 AM - 3:30 PM IST)
- Automatically pauses when session is paused/halted
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger(__name__)

# How often to poll prices (seconds)
POLL_INTERVAL = 30

# Don't trigger alerts within this many minutes of a scheduled cycle
GUARD_MINUTES = 10

# Scheduled cycle slots (must match session_manager.CYCLE_SLOTS)
CYCLE_SLOTS = [(9, 30), (11, 0), (12, 30), (14, 0)]


class PriceMonitor:
    """Background thread that monitors price alerts and triggers ad-hoc cycles."""

    def __init__(self, session_id: int, trigger_callback):
        """
        Args:
            session_id: The session to monitor alerts for.
            trigger_callback: Function to call when an alert fires.
                              Signature: callback(triggered_alerts: list[dict]) -> None
                              Each dict has: id, symbol, target_price, direction, reason, current_price
        """
        self.session_id = session_id
        self._trigger_callback = trigger_callback
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._cycle_running = threading.Event()  # Set when a scheduled cycle is in progress
        self._kite = None

    def set_kite_client(self, kite):
        """Inject Kite client for price fetching."""
        self._kite = kite

    def start(self):
        """Start the monitoring thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Price monitor already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"price-monitor-{self.session_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Price monitor started for session {self.session_id}")

    def stop(self):
        """Stop the monitoring thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            logger.info(f"Price monitor stopped for session {self.session_id}")

    def notify_cycle_start(self):
        """Called by session_manager when a scheduled cycle begins."""
        self._cycle_running.set()

    def notify_cycle_end(self):
        """Called by session_manager when a scheduled cycle ends."""
        self._cycle_running.clear()

    def _run_loop(self):
        """Main polling loop — runs in background thread."""
        logger.info("Price monitor loop started")
        while not self._stop_event.is_set():
            try:
                self._check_alerts()
            except Exception as e:
                logger.error(f"Price monitor error: {e}", exc_info=True)

            # Sleep in small increments so stop_event is responsive
            for _ in range(POLL_INTERVAL):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _check_alerts(self):
        """Check all active alerts against current prices."""
        now = datetime.now(_IST)

        # Only run during market hours (9:15 AM - 3:30 PM IST)
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now < market_open or now > market_close:
            return

        # Don't check if a scheduled cycle is currently running
        if self._cycle_running.is_set():
            return

        # Check session status — pause if not active
        session = db.query_one(
            "SELECT status FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session or session["status"] not in ("active", "closing"):
            return

        # Don't trigger if we're within GUARD_MINUTES of a scheduled cycle
        if self._near_scheduled_cycle(now):
            return

        # Get active alerts
        alerts = db.query(
            "SELECT id, symbol, target_price, direction, margin_pct, reason "
            "FROM price_alerts "
            "WHERE session_id = ? AND status = 'active'",
            (self.session_id,),
        )
        if not alerts:
            return

        # Batch-fetch prices for all alerted symbols
        symbols = list({a["symbol"] for a in alerts})
        prices = self._fetch_prices(symbols)
        if not prices:
            return

        # Check which alerts have triggered
        triggered = []
        for alert in alerts:
            symbol = alert["symbol"]
            if symbol not in prices:
                continue

            current_price = prices[symbol]
            target = alert["target_price"]
            margin = target * (alert["margin_pct"] / 100)

            hit = False
            if alert["direction"] == "above":
                hit = current_price >= (target - margin)
            elif alert["direction"] == "below":
                hit = current_price <= (target + margin)

            if hit:
                logger.info(
                    f"ALERT TRIGGERED: {symbol} {alert['direction']} ₹{target} "
                    f"(current: ₹{current_price}, margin: ±{alert['margin_pct']}%)"
                )
                # Mark as triggered in DB
                db.update("price_alerts", alert["id"], {
                    "status": "triggered",
                    "triggered_at": db.now_iso(),
                })
                triggered.append({
                    "id": alert["id"],
                    "symbol": symbol,
                    "target_price": target,
                    "direction": alert["direction"],
                    "reason": alert["reason"],
                    "current_price": current_price,
                    "margin_pct": alert["margin_pct"],
                })

        # Fire callback if any alerts triggered
        if triggered:
            # Re-check timing guard (a scheduled cycle may have started while we were fetching)
            if self._cycle_running.is_set() or self._near_scheduled_cycle(datetime.now(_IST)):
                logger.info(
                    f"{len(triggered)} alert(s) triggered but too close to scheduled cycle — "
                    "skipping ad-hoc cycle (alerts stay triggered, Claude will see them next cycle)"
                )
                return

            try:
                self._trigger_callback(triggered)
            except Exception as e:
                logger.error(f"Alert trigger callback failed: {e}", exc_info=True)

    def _near_scheduled_cycle(self, now: datetime) -> bool:
        """Check if we're within GUARD_MINUTES of any scheduled cycle slot."""
        for h, m in CYCLE_SLOTS:
            slot_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
            diff = abs((now - slot_time).total_seconds()) / 60  # minutes

            if diff <= GUARD_MINUTES:
                return True

        return False

    def _fetch_prices(self, symbols: list[str]) -> dict[str, float]:
        """Fetch current prices for multiple symbols. Returns {symbol: price}."""
        if self._kite:
            return self._fetch_prices_kite(symbols)
        return self._fetch_prices_yfinance(symbols)

    def _fetch_prices_kite(self, symbols: list[str]) -> dict[str, float]:
        """Batch fetch prices via Kite API (single API call for all symbols)."""
        try:
            instruments = [f"NSE:{s}" for s in symbols]
            from aaitrade.tools.market import _kite_lock
            with _kite_lock:
                quotes = self._kite.quote(instruments)

            result = {}
            for symbol in symbols:
                key = f"NSE:{symbol}"
                if key in quotes and quotes[key].get("last_price"):
                    result[symbol] = quotes[key]["last_price"]
            return result
        except Exception as e:
            logger.warning(f"Price monitor Kite fetch failed: {e}")
            return {}

    def _fetch_prices_yfinance(self, symbols: list[str]) -> dict[str, float]:
        """Fetch prices via yfinance (fallback for paper mode)."""
        try:
            import yfinance as yf

            result = {}
            for symbol in symbols:
                ticker = yf.Ticker(f"{symbol}.NS")
                info = ticker.fast_info
                price = getattr(info, "last_price", None)
                if price:
                    result[symbol] = price
            return result
        except Exception as e:
            logger.warning(f"Price monitor yfinance fetch failed: {e}")
            return {}
