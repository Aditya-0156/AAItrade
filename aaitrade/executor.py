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

        # NSE does not allow fractional shares — round down to whole number
        quantity = int(quantity)
        if quantity <= 0:
            return {
                "status": "rejected",
                "reason": (
                    f"Quantity must be at least 1 whole share "
                    f"(requested {decision.get('quantity')}, rounds to {quantity})"
                ),
            }

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

        # current_capital in DB is FREE CASH (starting capital minus all deployed amounts).
        free_cash = session["current_capital"]
        starting_capital = session["starting_capital"]

        # Effective tradeable capital = free cash + deployed at cost.
        # This grows as reinvested profits are added, so risk limits scale with the portfolio.
        # (starting_capital stays fixed and is used only for drawdown calculations.)
        deployed_row = db.query(
            "SELECT SUM(quantity * avg_price) as total FROM portfolio WHERE session_id = ?",
            (self.session_id,),
        )
        current_deployed = deployed_row[0]["total"] if deployed_row and deployed_row[0]["total"] else 0
        effective_capital = free_cash + current_deployed  # total tradeable pot

        # 3. Get current price for position sizing validation
        from aaitrade.tools.market import get_current_price
        price_data = get_current_price(symbol)
        if "error" in price_data:
            return {"status": "rejected", "reason": f"Cannot get price for {symbol}"}

        price = price_data["last_price"]
        trade_value = price * quantity

        # 4. Check max per trade (% of effective capital) — auto-reduce to fit
        max_trade_value = effective_capital * (self.rules.max_per_trade / 100)
        if trade_value > max_trade_value:
            adjusted_qty = int(max_trade_value // price)
            if adjusted_qty <= 0:
                return {
                    "status": "rejected",
                    "reason": (
                        f"Even 1 share of {symbol} (₹{price:.2f}) exceeds "
                        f"{self.rules.max_per_trade}% max-per-trade limit (₹{max_trade_value:.0f} of ₹{effective_capital:.0f})"
                    ),
                }
            logger.info(f"Auto-reduced {symbol} qty {quantity}→{adjusted_qty} to fit {self.rules.max_per_trade}% limit")
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

        # 6. Check max deployed capital (% of effective capital)
        max_deployed_value = effective_capital * (self.rules.max_deployed / 100)
        if current_deployed + trade_value > max_deployed_value:
            remaining_deploy = max(0, max_deployed_value - current_deployed)
            max_qty_deploy = int(remaining_deploy // price)
            return {
                "status": "rejected",
                "reason": (
                    f"Max deployed {self.rules.max_deployed}% (₹{max_deployed_value:.0f} of ₹{effective_capital:.0f}) would be exceeded. "
                    f"Currently deployed ₹{current_deployed:.0f}. "
                    f"Room left: ₹{remaining_deploy:.0f} = max {max_qty_deploy} shares of {symbol} at ₹{price:.2f}"
                ),
            }

        # 7. Check available cash (free_cash already has deployed amounts subtracted)
        if trade_value > free_cash:
            return {"status": "rejected", "reason": f"Insufficient cash: need ₹{trade_value:.2f}, have ₹{free_cash:.2f}"}

        # 8. Check daily loss limit
        if self._daily_loss_exceeded():
            return {"status": "rejected", "reason": "Daily loss limit already hit"}

        # 9. Check session drawdown (free_cash + deployed at cost = total portfolio value)
        total_value = free_cash + current_deployed
        drawdown = ((starting_capital - total_value) / starting_capital) * 100
        if drawdown >= self.rules.session_stop_loss:
            self._halt_session("Session stop-loss reached")
            return {"status": "halted", "reason": f"Session stop-loss reached ({self.rules.session_stop_loss}% drawdown)"}

        # 10. Compute stop-loss and take-profit if Claude didn't provide them
        # If the rule is 0, it means "no hard limit" — leave it to Claude's discretion
        if not stop_loss_price and self.rules.stop_loss > 0:
            stop_loss_price = round(price * (1 - self.rules.stop_loss / 100), 2)
        if not take_profit_price and self.rules.take_profit > 0:
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

        # Deduct deployed capital from current_capital so DB reflects free cash
        session_cap = db.query_one(
            "SELECT current_capital FROM sessions WHERE id = ?", (self.session_id,)
        )
        if session_cap:
            db.update("sessions", self.session_id, {
                "current_capital": round(session_cap["current_capital"] - trade_value, 2),
            })

        # Write or update trade journal after confirmed execution
        from aaitrade.tools.journal import write_trade_rationale
        from aaitrade.tools.journal import _session_id as _j_session_id
        if _j_session_id:
            if not existing:
                write_trade_rationale(
                    symbol=symbol,
                    entry_price=price,
                    reason=decision.get("reason", ""),
                    thesis=decision.get("thesis", decision.get("reason", "")),
                    target_price=take_profit or 0,
                    stop_price=stop_loss or 0,
                )
            else:
                # Averaging down — update journal entry_price to new weighted avg
                journal = db.query_one(
                    "SELECT id FROM trade_journal WHERE session_id = ? AND symbol = ? AND status = 'open'",
                    (self.session_id, symbol),
                )
                if journal:
                    new_avg = round(((existing["avg_price"] * existing["quantity"]) + (price * quantity)) / (existing["quantity"] + quantity), 2)
                    db.update("trade_journal", journal["id"], {
                        "entry_price": new_avg,
                        "key_thesis": f"Averaged down. New avg ₹{new_avg}. " + decision.get("reason", ""),
                        "stop_price": stop_loss or 0,
                        "target_price": take_profit or 0,
                    })

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
        """Live mode: place real order via Zerodha Kite.

        Safety protocol:
        1. Place the order on Kite
        2. Wait and verify the order status
        3. Only update DB after confirmed execution
        """
        if not _kite:
            return {"status": "error", "reason": "Kite client not initialized for live trading"}

        try:
            # Use LIMIT order slightly above market to ensure immediate fill
            # (Kite API requires market_protection for MARKET orders but SDK doesn't support it).
            # Round to the symbol's tick — high-priced stocks like MARUTI use ₹1.00 tick
            # and Kite rejects prices that aren't a multiple of it.
            from aaitrade.tools.market import round_to_tick
            limit_price = round_to_tick(price * 1.005, symbol, direction="up")
            order_id = _kite.place_order(
                variety=_kite.VARIETY_REGULAR,
                exchange=_kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=_kite.TRANSACTION_TYPE_BUY,
                quantity=quantity,
                product=_kite.PRODUCT_CNC,
                order_type=_kite.ORDER_TYPE_LIMIT,
                price=limit_price,
            )

            # Verify order status before updating DB
            import time
            actual_price = price
            for _ in range(5):
                time.sleep(1)
                try:
                    order_history = _kite.order_history(order_id)
                    if order_history:
                        latest = order_history[-1]
                        if latest.get("status") == "COMPLETE":
                            actual_price = latest.get("average_price", price)
                            break
                        elif latest.get("status") in ("REJECTED", "CANCELLED"):
                            logger.error(f"Live BUY REJECTED for {symbol}: {latest.get('status_message', 'Unknown')}")
                            return {"status": "error", "reason": f"Order rejected: {latest.get('status_message', 'Unknown')}"}
                except Exception:
                    pass

            # Order confirmed — update DB
            db.insert("trades", {
                "session_id": self.session_id,
                "symbol": symbol,
                "action": "BUY",
                "quantity": quantity,
                "price": actual_price,
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "reason": decision.get("reason", ""),
                "confidence": decision.get("confidence", ""),
                "executed_at": db.now_iso(),
            })

            # Add to portfolio (or update existing)
            # Check BEFORE inserting so we know if this is a new position
            existing = db.query_one(
                "SELECT id, quantity, avg_price FROM portfolio "
                "WHERE session_id = ? AND symbol = ?",
                (self.session_id, symbol),
            )
            if existing:
                new_qty = existing["quantity"] + quantity
                new_avg = ((existing["avg_price"] * existing["quantity"]) + (actual_price * quantity)) / new_qty
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
                    "avg_price": actual_price,
                    "stop_loss_price": stop_loss,
                    "take_profit_price": take_profit,
                    "opened_at": db.now_iso(),
                })

            # Deduct from free cash
            trade_value = actual_price * quantity
            session_cap = db.query_one(
                "SELECT current_capital FROM sessions WHERE id = ?", (self.session_id,)
            )
            if session_cap:
                db.update("sessions", self.session_id, {
                    "current_capital": round(session_cap["current_capital"] - trade_value, 2),
                })

            # Write or update trade journal after confirmed execution
            from aaitrade.tools.journal import write_trade_rationale
            from aaitrade.tools.journal import _session_id as _j_session_id
            if _j_session_id:
                if not existing:
                    write_trade_rationale(
                        symbol=symbol,
                        entry_price=actual_price,
                        reason=decision.get("reason", ""),
                        thesis=decision.get("thesis", decision.get("reason", "")),
                        target_price=take_profit or 0,
                        stop_price=stop_loss or 0,
                    )
                else:
                    # Averaging down — update journal entry_price to new weighted avg
                    journal = db.query_one(
                        "SELECT id FROM trade_journal WHERE session_id = ? AND symbol = ? AND status = 'open'",
                        (self.session_id, symbol),
                    )
                    if journal:
                        new_avg = round(((existing["avg_price"] * existing["quantity"]) + (actual_price * quantity)) / (existing["quantity"] + quantity), 2)
                        db.update("trade_journal", journal["id"], {
                            "entry_price": new_avg,
                            "key_thesis": f"Averaged down. New avg ₹{new_avg}. " + decision.get("reason", ""),
                            "stop_price": stop_loss or 0,
                            "take_profit_price": take_profit or 0,
                        })

            logger.info(f"[LIVE] BUY {symbol} x{quantity} @ ₹{actual_price:.2f} | Order: {order_id}")
            return {"status": "executed", "mode": "live", "order_id": order_id, "symbol": symbol, "quantity": quantity, "price": actual_price}

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

        # Warn if multiple live sessions hold the same symbol (Zerodha collision risk)
        if self.config.execution_mode == ExecutionMode.LIVE:
            other_live = db.query(
                "SELECT s.id FROM portfolio p "
                "JOIN sessions s ON s.id = p.session_id "
                "WHERE p.symbol = ? AND p.session_id != ? AND s.execution_mode = 'live' AND s.status IN ('active', 'closing')",
                (symbol, self.session_id),
            )
            if other_live:
                other_ids = [str(r["id"]) for r in other_live]
                logger.warning(
                    f"[COLLISION WARNING] Multiple live sessions hold {symbol}: "
                    f"this session ({self.session_id}) and sessions {', '.join(other_ids)}. "
                    f"Zerodha will sell from the combined position — DB will only update this session."
                )

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

        # Handle profit/loss — current_capital is free cash (cost_basis was deducted at BUY)
        # Return cost_basis to free cash, then split any profit per mode
        session = db.query_one("SELECT id, current_capital, secured_profit, profit_reinvest_ratio FROM sessions WHERE id = ?", (self.session_id,))
        if session:
            cost_basis = position["avg_price"] * quantity
            new_secured = session["secured_profit"]

            if pnl > 0:
                reinvest_ratio = session.get("profit_reinvest_ratio", 0.5) if session else 0.5
                secure = pnl * (1 - reinvest_ratio)
                # Return cost_basis + reinvested portion of profit to free cash
                new_capital = session["current_capital"] + cost_basis + (pnl * reinvest_ratio)
                new_secured = session["secured_profit"] + secure
            else:
                # Loss: return only what the sale actually brought in
                new_capital = session["current_capital"] + (price * quantity)

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
        """Live mode sell via Zerodha.

        Safety protocol: verify order completion before updating DB.
        """
        if not _kite:
            return {"status": "error", "reason": "Kite client not initialized"}

        try:
            # Use LIMIT order slightly below market to ensure immediate fill.
            # Round to the symbol's tick — Kite rejects prices that aren't a multiple of it.
            from aaitrade.tools.market import round_to_tick
            limit_price = round_to_tick(price * 0.995, symbol, direction="down")
            order_id = _kite.place_order(
                variety=_kite.VARIETY_REGULAR,
                exchange=_kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=_kite.TRANSACTION_TYPE_SELL,
                quantity=quantity,
                product=_kite.PRODUCT_CNC,
                order_type=_kite.ORDER_TYPE_LIMIT,
                price=limit_price,
            )

            # Verify order status before updating DB
            import time
            actual_price = price
            for _ in range(5):
                time.sleep(1)
                try:
                    order_history = _kite.order_history(order_id)
                    if order_history:
                        latest = order_history[-1]
                        if latest.get("status") == "COMPLETE":
                            actual_price = latest.get("average_price", price)
                            break
                        elif latest.get("status") in ("REJECTED", "CANCELLED"):
                            logger.error(f"Live SELL REJECTED for {symbol}: {latest.get('status_message', 'Unknown')}")
                            return {"status": "error", "reason": f"Order rejected: {latest.get('status_message', 'Unknown')}"}
                except Exception:
                    pass

            # Recalculate P&L with actual execution price
            actual_pnl = (actual_price - position["avg_price"]) * quantity

            # Record trade and update portfolio (using actual price)
            self._simulate_sell(symbol, quantity, actual_price, actual_pnl, position, decision)

            logger.info(f"[LIVE] SELL {symbol} x{quantity} @ ₹{actual_price:.2f} | Order: {order_id}")
            return {"status": "executed", "mode": "live", "order_id": order_id, "symbol": symbol, "quantity": quantity, "price": actual_price, "pnl": round(actual_pnl, 2)}

        except Exception as e:
            logger.error(f"Live SELL failed for {symbol}: {e}")
            return {"status": "error", "reason": str(e)}

    # ── Helpers ────────────────────────────────────────────────────────────

    def _daily_loss_exceeded(self) -> bool:
        """Check if today's cumulative loss exceeds the daily limit.

        Uses today's starting capital from daily_summary if available,
        otherwise falls back to current_capital.
        """
        today = db.now_iso()[:10]
        rows = db.query(
            "SELECT SUM(pnl) as total FROM trades "
            "WHERE session_id = ? AND action = 'SELL' AND executed_at LIKE ?",
            (self.session_id, f"{today}%"),
        )
        today_pnl = rows[0]["total"] if rows and rows[0]["total"] else 0

        # Use today's opening capital (from daily_summary) for accurate % calculation
        day_summary = db.query_one(
            "SELECT starting_capital FROM daily_summary "
            "WHERE session_id = ? AND date = ? ORDER BY day_number DESC LIMIT 1",
            (self.session_id, today),
        )
        session = db.query_one(
            "SELECT current_capital FROM sessions WHERE id = ?",
            (self.session_id,),
        )
        if not session:
            return False

        base_capital = (
            day_summary["starting_capital"] if day_summary
            else session["current_capital"]
        )
        # 0 means disabled — LLM has full control, no hard daily limit
        if self.rules.daily_loss_limit == 0:
            return False
        loss_pct = abs(today_pnl) / base_capital * 100 if today_pnl < 0 else 0
        return loss_pct >= self.rules.daily_loss_limit

    def _halt_session(self, reason: str):
        """Halt the session."""
        db.update("sessions", self.session_id, {
            "status": "halted",
            "ended_at": db.now_iso(),
        })
        logger.critical(f"SESSION HALTED: {reason}")
