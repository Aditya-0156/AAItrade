"""Executor — validates Claude decisions against risk rules and executes trades.

This is the gatekeeper. Every Claude decision passes through validation
before execution. Python always wins — if Claude's output conflicts with
a hard risk rule, the executor overrides it.

Two execution modes:
- Paper: simulate_order() — log the trade, no real money
- Live: place_order() — place via Zerodha Kite API
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from aaitrade import db
from aaitrade.config import SessionConfig, ExecutionMode

logger = logging.getLogger(__name__)

_kite = None


def set_kite_client(kite):
    global _kite
    _kite = kite


class Executor:
    """Validates and executes trade decisions."""

    def __init__(self, config: SessionConfig, session_id: int):
        self.config = config
        self.session_id = session_id
        self.rules = config.risk_rules

    def execute(self, decision: dict) -> dict:
        """Validate and execute a Claude decision.

        Returns a result dict with status and details.
        """
        action = decision.get("action", "").upper()

        # Handle flags first
        flags = decision.get("flags", [])
        if "HALT_SESSION" in flags:
            self._halt_session(decision.get("reason", "Claude requested halt"))
            return {"status": "halted", "reason": decision.get("reason")}

        if "DAILY_LIMIT_HIT" in flags:
            logger.warning("Daily loss limit hit — no more trades today.")
            return {"status": "daily_limit", "reason": decision.get("reason")}

        if action == "HOLD":
            return {"status": "hold", "reason": decision.get("reason", "")}

        if action == "BUY":
            return self._execute_buy(decision)
        elif action == "SELL":
            return self._execute_sell(decision)
        else:
            logger.warning(f"Unknown action: {action}")
            return {"status": "error", "reason": f"Unknown action: {action}"}

    # ── BUY ────────────────────────────────────────────────────────────────

    def _execute_buy(self, decision: dict) -> dict:
        symbol = decision.get("symbol")
        quantity = decision.get("quantity")
        stop_loss_price = decision.get("stop_loss_price")
        take_profit_price = decision.get("take_profit_price")

        if not symbol or not quantity:
            return {"status": "rejected", "reason": "BUY missing symbol or quantity"}

        # ── Validation Checklist ──

        # 1. Is symbol on watchlist?
        on_watchlist = db.query_one(
            "SELECT id FROM watchlist "
            "WHERE session_id = ? AND symbol = ? AND removed_at IS NULL",
            (self.session_id, symbol),
        )
        if not on_watchlist:
            return {"status": "rejected", "reason": f"{symbol} is not on the watchlist"}

        # 2. Get current session state
        session = db.query_one(
            "SELECT current_capital, starting_capital, secured_profit FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session:
            return {"status": "rejected", "reason": "Session not found"}

        current_capital = session["current_capital"]

        # 3. Get current price for position sizing validation
        from aaitrade.tools.market import get_current_price
        price_data = get_current_price(symbol)
        if "error" in price_data:
            return {"status": "rejected", "reason": f"Cannot get price for {symbol}"}

        price = price_data["last_price"]
        trade_value = price * quantity

        # 4. Check max per trade
        max_trade_value = current_capital * (self.rules.max_per_trade / 100)
        if trade_value > max_trade_value:
            # Reduce quantity to fit within limit
            adjusted_qty = int(max_trade_value // price)
            if adjusted_qty <= 0:
                return {"status": "rejected", "reason": f"Trade value ₹{trade_value:.2f} exceeds {self.rules.max_per_trade}% limit and cannot be reduced"}
            logger.info(f"Reduced {symbol} quantity from {quantity} to {adjusted_qty} to fit risk limits")
            quantity = adjusted_qty
            trade_value = price * quantity

        # 5. Check max positions
        open_positions = db.query(
            "SELECT COUNT(*) as count FROM portfolio WHERE session_id = ?",
            (self.session_id,),
        )
        pos_count = open_positions[0]["count"] if open_positions else 0
        if pos_count >= self.rules.max_positions:
            return {"status": "rejected", "reason": f"Already at max {self.rules.max_positions} positions"}

        # 6. Check max deployed capital
        deployed = db.query(
            "SELECT SUM(quantity * avg_price) as total FROM portfolio WHERE session_id = ?",
            (self.session_id,),
        )
        current_deployed = deployed[0]["total"] if deployed and deployed[0]["total"] else 0
        max_deployed_value = current_capital * (self.rules.max_deployed / 100)
        if current_deployed + trade_value > max_deployed_value:
            return {"status": "rejected", "reason": f"Total deployment would exceed {self.rules.max_deployed}% limit"}

        # 7. Check available cash
        available = current_capital - current_deployed
        if trade_value > available:
            return {"status": "rejected", "reason": f"Insufficient cash: need ₹{trade_value:.2f}, have ₹{available:.2f}"}

        # 8. Check daily loss limit
        if self._daily_loss_exceeded():
            return {"status": "rejected", "reason": "Daily loss limit already hit"}

        # 9. Check session drawdown
        drawdown = ((session["starting_capital"] - current_capital) / session["starting_capital"]) * 100
        if drawdown >= self.rules.session_stop_loss:
            self._halt_session("Session stop-loss reached")
            return {"status": "halted", "reason": "Session stop-loss reached (20% drawdown)"}

        # 10. Check human alert threshold
        trade_pct = (trade_value / current_capital) * 100
        if trade_pct > self.rules.human_alert_threshold:
            logger.critical(f"ALERT: Trade {symbol} x{quantity} = ₹{trade_value:.2f} is {trade_pct:.1f}% of capital!")
            return {"status": "rejected", "reason": f"Trade exceeds {self.rules.human_alert_threshold}% alert threshold"}

        # 11. Compute stop-loss and take-profit if Claude didn't provide them
        if not stop_loss_price:
            stop_loss_price = round(price * (1 - self.rules.stop_loss / 100), 2)
        if not take_profit_price:
            take_profit_price = round(price * (1 + self.rules.take_profit / 100), 2)

        # ── All checks passed — execute ──

        if self.config.execution_mode == ExecutionMode.PAPER:
            return self._simulate_buy(symbol, quantity, price, stop_loss_price, take_profit_price, decision)
        else:
            return self._live_buy(symbol, quantity, price, stop_loss_price, take_profit_price, decision)

    def _simulate_buy(self, symbol, quantity, price, stop_loss, take_profit, decision) -> dict:
        """Paper mode: record the trade without placing a real order."""
        # Record trade
        db.insert("trades", {
            "session_id": self.session_id,
            "symbol": symbol,
            "action": "BUY",
            "quantity": quantity,
            "price": price,
            "stop_loss_price": stop_loss,
            "take_profit_price": take_profit,
            "reason": decision.get("reason", ""),
            "confidence": decision.get("confidence", ""),
            "executed_at": db.now_iso(),
        })

        # Add to portfolio (or update if adding to existing position)
        existing = db.query_one(
            "SELECT id, quantity, avg_price FROM portfolio "
            "WHERE session_id = ? AND symbol = ?",
            (self.session_id, symbol),
        )
        if existing:
            new_qty = existing["quantity"] + quantity
            new_avg = ((existing["avg_price"] * existing["quantity"]) + (price * quantity)) / new_qty
            db.update("portfolio", existing["id"], {
                "quantity": new_qty,
                "avg_price": round(new_avg, 2),
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
            })
        else:
            db.insert("portfolio", {
                "session_id": self.session_id,
                "symbol": symbol,
                "quantity": quantity,
                "avg_price": price,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "opened_at": db.now_iso(),
            })

        trade_value = price * quantity
        logger.info(f"[PAPER] BUY {symbol} x{quantity} @ ₹{price:.2f} = ₹{trade_value:.2f}")

        return {
            "status": "executed",
            "mode": "paper",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "trade_value": round(trade_value, 2),
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

    def _live_buy(self, symbol, quantity, price, stop_loss, take_profit, decision) -> dict:
        """Live mode: place real order via Zerodha Kite."""
        if not _kite:
            return {"status": "error", "reason": "Kite client not initialized for live trading"}

        try:
            order_id = _kite.place_order(
                variety=_kite.VARIETY_REGULAR,
                exchange=_kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=_kite.TRANSACTION_TYPE_BUY,
                quantity=quantity,
                product=_kite.PRODUCT_CNC,
                order_type=_kite.ORDER_TYPE_MARKET,
            )

            # Record trade
            db.insert("trades", {
                "session_id": self.session_id,
                "symbol": symbol,
                "action": "BUY",
                "quantity": quantity,
                "price": price,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "reason": decision.get("reason", ""),
                "confidence": decision.get("confidence", ""),
                "executed_at": db.now_iso(),
            })

            # Add to portfolio
            db.insert("portfolio", {
                "session_id": self.session_id,
                "symbol": symbol,
                "quantity": quantity,
                "avg_price": price,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "opened_at": db.now_iso(),
            })

            logger.info(f"[LIVE] BUY {symbol} x{quantity} @ ₹{price:.2f} | Order: {order_id}")
            return {"status": "executed", "mode": "live", "order_id": order_id, "symbol": symbol, "quantity": quantity, "price": price}

        except Exception as e:
            logger.error(f"Live BUY failed for {symbol}: {e}")
            return {"status": "error", "reason": str(e)}

    # ── SELL ───────────────────────────────────────────────────────────────

    def _execute_sell(self, decision: dict) -> dict:
        symbol = decision.get("symbol")
        if not symbol:
            return {"status": "rejected", "reason": "SELL missing symbol"}

        # Check we actually hold this
        position = db.query_one(
            "SELECT id, quantity, avg_price FROM portfolio "
            "WHERE session_id = ? AND symbol = ?",
            (self.session_id, symbol),
        )
        if not position:
            return {"status": "rejected", "reason": f"No position in {symbol} to sell"}

        quantity = decision.get("quantity") or position["quantity"]  # default: sell all

        # Get current price
        from aaitrade.tools.market import get_current_price
        price_data = get_current_price(symbol)
        if "error" in price_data:
            return {"status": "rejected", "reason": f"Cannot get price for {symbol}"}

        price = price_data["last_price"]
        pnl = (price - position["avg_price"]) * quantity

        if self.config.execution_mode == ExecutionMode.PAPER:
            return self._simulate_sell(symbol, quantity, price, pnl, position, decision)
        else:
            return self._live_sell(symbol, quantity, price, pnl, position, decision)

    def _simulate_sell(self, symbol, quantity, price, pnl, position, decision) -> dict:
        """Paper mode sell."""
        # Record trade
        db.insert("trades", {
            "session_id": self.session_id,
            "symbol": symbol,
            "action": "SELL",
            "quantity": quantity,
            "price": price,
            "reason": decision.get("reason", ""),
            "confidence": decision.get("confidence", ""),
            "executed_at": db.now_iso(),
            "pnl": round(pnl, 2),
        })

        # Update portfolio
        remaining = position["quantity"] - quantity
        if remaining <= 0:
            # Fully closed — remove from portfolio
            with db.get_connection() as conn:
                conn.execute("DELETE FROM portfolio WHERE id = ?", (position["id"],))
        else:
            db.update("portfolio", position["id"], {"quantity": remaining})

        # Handle profit/loss
        session = db.query_one("SELECT id, current_capital, secured_profit FROM sessions WHERE id = ?", (self.session_id,))
        if session:
            new_capital = session["current_capital"] + pnl
            new_secured = session["secured_profit"]

            if pnl > 0:
                # Profit handling based on mode
                reinvest_ratio = self.config.profit_reinvest_ratio
                reinvest = pnl * reinvest_ratio
                secure = pnl * (1 - reinvest_ratio)
                new_capital = session["current_capital"] + reinvest
                new_secured = session["secured_profit"] + secure
            else:
                new_capital = session["current_capital"] + pnl

            db.update("sessions", self.session_id, {
                "current_capital": round(new_capital, 2),
                "secured_profit": round(new_secured, 2),
            })

        # Close journal entry
        journal = db.query_one(
            "SELECT id FROM trade_journal WHERE session_id = ? AND symbol = ? AND status = 'open'",
            (self.session_id, symbol),
        )
        if journal and remaining <= 0:
            db.update("trade_journal", journal["id"], {
                "status": "closed",
                "closed_at": db.now_iso(),
                "exit_reason": decision.get("reason", ""),
                "exit_price": price,
                "pnl": round(pnl, 2),
            })

        logger.info(f"[PAPER] SELL {symbol} x{quantity} @ ₹{price:.2f} | P&L: ₹{pnl:.2f}")
        return {
            "status": "executed",
            "mode": "paper",
            "symbol": symbol,
            "quantity": quantity,
            "price": price,
            "pnl": round(pnl, 2),
        }

    def _live_sell(self, symbol, quantity, price, pnl, position, decision) -> dict:
        """Live mode sell via Zerodha."""
        if not _kite:
            return {"status": "error", "reason": "Kite client not initialized"}

        try:
            order_id = _kite.place_order(
                variety=_kite.VARIETY_REGULAR,
                exchange=_kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=_kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                product=_kite.PRODUCT_CNC,
                order_type=_kite.ORDER_TYPE_MARKET,
            )

            # Record trade and update portfolio (same as paper)
            self._simulate_sell(symbol, quantity, price, pnl, position, decision)

            logger.info(f"[LIVE] SELL {symbol} x{quantity} @ ₹{price:.2f} | Order: {order_id}")
            return {"status": "executed", "mode": "live", "order_id": order_id, "symbol": symbol, "quantity": quantity, "price": price, "pnl": round(pnl, 2)}

        except Exception as e:
            logger.error(f"Live SELL failed for {symbol}: {e}")
            return {"status": "error", "reason": str(e)}

    # ── Helpers ────────────────────────────────────────────────────────────

    def _daily_loss_exceeded(self) -> bool:
        """Check if today's cumulative loss exceeds the daily limit."""
        today = db.now_iso()[:10]
        rows = db.query(
            "SELECT SUM(pnl) as total FROM trades "
            "WHERE session_id = ? AND action = 'SELL' AND executed_at LIKE ?",
            (self.session_id, f"{today}%"),
        )
        today_pnl = rows[0]["total"] if rows and rows[0]["total"] else 0

        session = db.query_one(
            "SELECT current_capital FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session:
            return False

        loss_pct = abs(today_pnl) / session["current_capital"] * 100 if today_pnl < 0 else 0
        return loss_pct >= self.rules.daily_loss_limit

    def _halt_session(self, reason: str):
        """Halt the session."""
        db.update("sessions", self.session_id, {
            "status": "halted",
            "ended_at": db.now_iso(),
        })
        logger.critical(f"SESSION HALTED: {reason}")
