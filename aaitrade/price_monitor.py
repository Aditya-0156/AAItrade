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

    def __init__(self, session_id: int, trigger_callback, max_position_loss_pct: float = 1.5,
                 execute_callback=None):
        """
        Args:
            session_id: The session to monitor alerts for.
            trigger_callback: Function to call when an alert fires.
                              Signature: callback(triggered_alerts: list[dict]) -> None
                              Each dict has: id, symbol, target_price, direction, reason, current_price
            max_position_loss_pct: hard cap — a position losing more than this %
                              of effective capital triggers a FORCED exit.
            execute_callback: Mechanical execution path for entry-plan fills and
                              trailing exits — trades that need NO model decision.
                              Signature: callback(decision: dict, context: str) -> dict
                              (decision is an executor-shaped BUY/SELL dict.)
        """
        self.session_id = session_id
        self._trigger_callback = trigger_callback
        self._execute_callback = execute_callback
        self._max_position_loss_pct = max_position_loss_pct
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._cycle_running = threading.Event()  # Set when a scheduled cycle is in progress
        self._kite = None
        # (symbol, kind) -> date fired: each position stop/target wakes Claude
        # at most once per day so a hovering price doesn't spam ad-hoc cycles
        self._position_trigger_dates: dict[tuple[str, str], str] = {}
        # symbol -> (bars, fetched_at): throttled intraday tape for stalk plans
        self._bars_cache: dict[str, tuple[list, float]] = {}

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
            try:
                self._check_entry_plans()
            except Exception as e:
                logger.error(f"Entry-plan monitor error: {e}", exc_info=True)

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

        # Open positions with a stop-loss or take-profit are watched automatically —
        # a breach between cycles must not wait up to 90 minutes for the next slot.
        positions = db.query(
            "SELECT id, symbol, quantity, avg_price, stop_loss_price, take_profit_price, "
            "trail_high FROM portfolio WHERE session_id = ? AND quantity > 0 "
            "AND (stop_loss_price IS NOT NULL OR take_profit_price IS NOT NULL)",
            (self.session_id,),
        )

        if not alerts and not positions:
            return

        # Batch-fetch prices for all watched symbols in one call
        symbols = list({a["symbol"] for a in alerts} | {p["symbol"] for p in positions})
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

        # Effective capital for the hard loss cap (free cash + deployed at cost)
        cap_value = None
        try:
            sess = db.query_one(
                "SELECT current_capital FROM sessions WHERE id = ?", (self.session_id,)
            )
            deployed = db.query_one(
                "SELECT SUM(quantity * avg_price) as t FROM portfolio WHERE session_id = ?",
                (self.session_id,),
            )
            if sess:
                effective = sess["current_capital"] + ((deployed["t"] or 0) if deployed else 0)
                cap_value = effective * self._max_position_loss_pct / 100
        except Exception:
            pass

        # Check position stop-loss / take-profit breaches (auto-watched)
        today = now.strftime("%Y-%m-%d")
        for pos in positions:
            symbol = pos["symbol"]
            if symbol not in prices:
                continue
            current_price = prices[symbol]

            # HARD LOSS CAP — highest priority, forces an exit (not a debate)
            unrealized_loss = (pos["avg_price"] - current_price) * pos["quantity"]
            if cap_value and unrealized_loss >= cap_value:
                if self._position_trigger_dates.get((symbol, "loss_cap")) != today:
                    self._position_trigger_dates[(symbol, "loss_cap")] = today
                    logger.critical(
                        f"LOSS CAP BREACHED: {symbol} down ₹{unrealized_loss:.0f} "
                        f"(cap ₹{cap_value:.0f}) — forcing exit"
                    )
                    triggered.append({
                        "id": None,
                        "symbol": symbol,
                        "target_price": current_price,
                        "direction": "below",
                        "kind": "loss_cap",
                        "quantity": pos["quantity"],
                        "reason": (
                            f"HARD LOSS CAP: position lost ₹{unrealized_loss:.0f}, over the "
                            f"{self._max_position_loss_pct}% capital cap (₹{cap_value:.0f}). "
                            f"The system force-sold it — review and re-plan."
                        ),
                        "current_price": current_price,
                        "margin_pct": 0,
                    })
                continue  # loss-cap supersedes the ordinary stop check

            breach = None
            if pos["stop_loss_price"] and current_price <= pos["stop_loss_price"]:
                breach = ("stop_loss", pos["stop_loss_price"], "below",
                          f"AUTO: stop-loss ₹{pos['stop_loss_price']} breached on your "
                          f"{pos['quantity']}-share position (avg ₹{pos['avg_price']}). "
                          f"Decide NOW: exit, or hold with explicit reasoning.")
            elif pos["take_profit_price"]:
                # TRAILING EXIT — crossing the target no longer market-sells the
                # touch (the exit audit: every closed trade left money on the
                # table; GRASIM took +0.98% of an available +4.15%). Instead the
                # target arms a trail: ride the move, sell when it comes off
                # the high, never exit below ~the target. Mechanical — no model,
                # no API cost.
                try:
                    from aaitrade.entry_engine import evaluate_trail
                    verdict = evaluate_trail(pos, current_price)
                except Exception as e:
                    logger.error(f"Trail evaluation failed for {symbol}: {e}")
                    verdict = None
                if verdict:
                    if verdict["action"] in ("arm", "raise"):
                        db.update("portfolio", pos["id"], {"trail_high": verdict["trail_high"]})
                        if verdict["action"] == "arm":
                            logger.info(f"TRAIL ARMED: {symbol} crossed target "
                                        f"₹{pos['take_profit_price']} at ₹{current_price}")
                    elif verdict["action"] == "sell":
                        if self._execute_callback:
                            if self._position_trigger_dates.get((symbol, "trail")) != today:
                                self._position_trigger_dates[(symbol, "trail")] = today
                                self._execute_callback({
                                    "action": "SELL",
                                    "symbol": symbol,
                                    "quantity": pos["quantity"],
                                    "reason": verdict["reason"],
                                    "confidence": "high",
                                    "flags": [],
                                }, "trail_exit")
                        else:
                            # No mechanical path wired — degrade to the old
                            # behaviour of waking the model to decide.
                            breach = ("take_profit", pos["take_profit_price"], "above",
                                      verdict["reason"])

            if breach:
                kind, level, direction, reason = breach
                if self._position_trigger_dates.get((symbol, kind)) == today:
                    continue  # already woke Claude for this today
                self._position_trigger_dates[(symbol, kind)] = today
                logger.info(f"POSITION {kind.upper()} TRIGGERED: {symbol} @ ₹{current_price} (level ₹{level})")
                triggered.append({
                    "id": None,
                    "symbol": symbol,
                    "target_price": level,
                    "direction": direction,
                    "reason": reason,
                    "current_price": current_price,
                    "margin_pct": 0,
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

    # ── Entry-plan stalking ────────────────────────────────────────────────
    # The model files WHAT to buy (plan_entry); this loop decides WHEN, from
    # the actual tape. Fills are mechanical — no model call, no API cost.

    def _check_entry_plans(self):
        now = datetime.now(_IST)

        # Expire overdue plans at any time of day
        for plan in db.query(
            "SELECT id, symbol, expires_at FROM entry_plans "
            "WHERE session_id = ? AND status IN ('stalking', 'partial')",
            (self.session_id,),
        ):
            if plan["expires_at"] and plan["expires_at"] < now.strftime("%Y-%m-%dT%H:%M:%S"):
                db.update("entry_plans", plan["id"], {
                    "status": "expired", "resolved_at": db.now_iso(),
                })
                logger.info(f"Entry plan expired untriggered: {plan['symbol']}")

        # Fills only during the tradable window (skip the volatile open) and
        # never while a model cycle is running — the model may be acting on
        # the same symbol at this moment.
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=15, second=0, microsecond=0)
        if now < market_open or now > market_close:
            return
        if self._cycle_running.is_set() or not self._execute_callback:
            return
        session = db.query_one(
            "SELECT status FROM sessions WHERE id = ?", (self.session_id,)
        )
        if not session or session["status"] != "active":
            return

        plans = db.query(
            "SELECT * FROM entry_plans WHERE session_id = ? "
            "AND status IN ('stalking', 'partial')",
            (self.session_id,),
        )
        if not plans:
            return

        prices = self._fetch_prices([p["symbol"] for p in plans])
        if not prices:
            return

        from aaitrade.entry_engine import evaluate_entry_plan, split_quantities

        for plan in plans:
            ltp = prices.get(plan["symbol"])
            if not ltp:
                continue

            # The tape (15m bars) is only needed near the level — fetching it
            # for a stock 3% away every 30s would waste API budget.
            bars = []
            if plan["touched"] or ltp <= plan["level"] * 1.015:
                bars = self._fetch_intraday_bars(plan["symbol"])

            verdict = evaluate_entry_plan(plan, bars, ltp)
            if not verdict:
                continue

            if verdict["action"] == "touch":
                db.update("entry_plans", plan["id"], {
                    "touched": 1, "touch_low": verdict["touch_low"],
                })
                continue

            if verdict["action"] == "runaway":
                db.update("entry_plans", plan["id"], {
                    "status": "runaway", "resolved_at": db.now_iso(),
                })
                logger.info(f"Entry plan abandoned (runaway/breakdown): {plan['symbol']}")
                continue

            if verdict["action"] == "fill":
                self._fill_plan(plan, verdict, split_quantities)

    def _fill_plan(self, plan: dict, verdict: dict, split_quantities):
        """Execute a triggered plan through the mechanical callback."""
        remaining = plan["quantity"] - plan["filled_quantity"]
        if remaining <= 0:
            return
        trigger = verdict["trigger"]

        if plan["fill_mode"] == "split" and trigger == "confirmed" and plan["filled_quantity"] == 0:
            # Half now on the confirmation; the rest stays stalking for the
            # discount until expiry. Never fully miss, always keep an order
            # working at the better price.
            qty, rest = split_quantities(remaining)
        else:
            qty, rest = remaining, 0  # discount fills (and single mode) take it all

        result = self._execute_callback({
            "action": "BUY",
            "symbol": plan["symbol"],
            "quantity": qty,
            "reason": (plan["reason"] or "") + f" [entry plan #{plan['id']}: {trigger} trigger]",
            "stop_loss_price": plan["stop_loss_price"],
            "take_profit_price": plan["take_profit_price"],
            "confidence": "high",
            "flags": [],
        }, f"entry_plan_{trigger}")

        status = (result or {}).get("status")
        if status == "executed":
            filled = plan["filled_quantity"] + qty
            db.update("entry_plans", plan["id"], {
                "filled_quantity": filled,
                "fill_price": (result or {}).get("price") or verdict["price"],
                "trigger": trigger if not plan["trigger"] else "mixed",
                "status": "partial" if rest > 0 else "filled",
                "resolved_at": None if rest > 0 else db.now_iso(),
            })
            logger.info(
                f"ENTRY PLAN FILLED: {plan['symbol']} x{qty} via {trigger}"
                + (f" ({rest} still stalking the discount)" if rest else "")
            )
        else:
            # A rejection here is structural (cash, position count, risk rule).
            # Retrying every 30s would hammer the same wall — cancel and let
            # the model re-decide next cycle with the rejection in front of it.
            db.update("entry_plans", plan["id"], {
                "status": "cancelled", "resolved_at": db.now_iso(),
            })
            logger.warning(
                f"Entry plan for {plan['symbol']} triggered but execution was "
                f"rejected: {(result or {}).get('reason')} — plan cancelled."
            )

    def _fetch_intraday_bars(self, symbol: str) -> list[dict]:
        """Today's 15-minute bars, throttled to one fetch per 3 minutes per symbol."""
        cached = self._bars_cache.get(symbol)
        if cached and (time.time() - cached[1]) < 180:
            return cached[0]
        bars: list[dict] = []
        if self._kite:
            try:
                from aaitrade.tools.market import _instrument_token_cache, _kite_lock
                token = _instrument_token_cache.get(symbol)
                if token:
                    frm = datetime.now(_IST) - timedelta(days=2)
                    with _kite_lock:
                        raw = self._kite.historical_data(
                            token, frm, datetime.now(_IST), "15minute"
                        )
                    bars = [{"low": b["low"], "high": b["high"], "close": b["close"]}
                            for b in raw]
            except Exception as e:
                logger.warning(f"Intraday bars fetch failed for {symbol}: {e}")
        self._bars_cache[symbol] = (bars, time.time())
        return bars

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
