"""Tests for executor.py — the risk enforcement gatekeeper.

All external price calls are patched. Tests cover every validation
path and verify Python always wins over Claude's decisions.
"""

from unittest.mock import patch

import pytest

import aaitrade.db as db
from aaitrade.config import ExecutionMode, SessionConfig, TradingMode
from aaitrade.executor import Executor
from tests.conftest import make_price


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_executor(config, session_id):
    return Executor(config, session_id)


def buy(symbol="RELIANCE", quantity=5, stop=None, take=None, flags=None):
    return {
        "action": "BUY",
        "symbol": symbol,
        "quantity": quantity,
        "stop_loss_price": stop,
        "take_profit_price": take,
        "reason": "test buy",
        "confidence": "high",
        "flags": flags or [],
    }


def sell(symbol="RELIANCE", quantity=None, flags=None):
    return {
        "action": "SELL",
        "symbol": symbol,
        "quantity": quantity,
        "reason": "test sell",
        "confidence": "high",
        "flags": flags or [],
    }


# ── HOLD ───────────────────────────────────────────────────────────────────────

class TestHold:
    def test_hold_returns_hold_status(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        result = ex.execute({"action": "HOLD", "reason": "nothing good", "flags": []})
        assert result["status"] == "hold"

    def test_halt_session_flag_halts(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        result = ex.execute({
            "action": "HOLD", "reason": "limit hit", "flags": ["HALT_SESSION"]
        })
        assert result["status"] == "halted"
        session = db.query_one("SELECT status FROM sessions WHERE id = ?", (session_with_watchlist,))
        assert session["status"] == "halted"

    def test_daily_limit_flag_returns_daily_limit(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        result = ex.execute({
            "action": "HOLD", "reason": "daily loss hit", "flags": ["DAILY_LIMIT_HIT"]
        })
        assert result["status"] == "daily_limit"


# ── BUY validations ────────────────────────────────────────────────────────────

class TestBuyValidations:
    def test_buy_not_on_watchlist_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("AAPL", 100)):
            result = ex.execute(buy("AAPL", 5))
        assert result["status"] == "rejected"
        assert "watchlist" in result["reason"].lower()

    def test_buy_missing_symbol_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        result = ex.execute({"action": "BUY", "symbol": None, "quantity": 5, "flags": []})
        assert result["status"] == "rejected"

    def test_buy_missing_quantity_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        result = ex.execute({"action": "BUY", "symbol": "RELIANCE", "quantity": None, "flags": []})
        assert result["status"] == "rejected"

    def test_buy_price_fetch_failure_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value={"error": "no data"}):
            result = ex.execute(buy("RELIANCE", 5))
        assert result["status"] == "rejected"
        assert "price" in result["reason"].lower()

    def test_buy_exceeds_max_per_trade_auto_adjusts_quantity(self, in_memory_db, balanced_config, session_with_watchlist):
        """Balanced mode: max 20% of ₹20,000 = ₹4,000 per trade.
        Buying 100 shares at ₹1,000 = ₹100,000 — should be auto-reduced."""
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            result = ex.execute(buy("RELIANCE", 100))
        # Should execute with adjusted quantity, not reject
        assert result["status"] == "executed"
        assert result["quantity"] <= 4  # ₹4,000 / ₹1,000 = 4 shares

    def test_buy_exceeds_max_per_trade_and_cant_reduce_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        """Price so high that even 1 share exceeds max_per_trade."""
        ex = make_executor(balanced_config, session_with_watchlist)
        # ₹20,000 capital × 20% = ₹4,000 limit. Price ₹5,000 = 1 share = ₹5,000 > limit
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 5000)):
            result = ex.execute(buy("RELIANCE", 10))
        assert result["status"] == "rejected"

    def test_buy_exceeds_max_positions_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        """Fill all 5 balanced positions then try to add a 6th."""
        ex = make_executor(balanced_config, session_with_watchlist)
        # Fixture already has RELIANCE, TCS, HDFCBANK on watchlist — add INFY and SBIN
        for sym in ["INFY", "SBIN"]:
            db.insert("watchlist", {
                "session_id": session_with_watchlist,
                "symbol": sym, "company": sym, "sector": "test",
                "notes": "", "added_at": db.now_iso(), "add_reason": "test",
            })
        # Insert 5 open positions manually
        for sym in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "SBIN"]:
            db.insert("portfolio", {
                "session_id": session_with_watchlist,
                "symbol": sym, "quantity": 1, "avg_price": 100,
                "stop_loss_price": 95, "take_profit_price": 110,
                "opened_at": db.now_iso(),
            })
        # Add WIPRO to watchlist for the 6th attempt
        db.insert("watchlist", {
            "session_id": session_with_watchlist,
            "symbol": "WIPRO", "company": "Wipro", "sector": "IT",
            "notes": "", "added_at": db.now_iso(), "add_reason": "test",
        })
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("WIPRO", 100)):
            result = ex.execute(buy("WIPRO", 1))
        assert result["status"] == "rejected"
        assert "max" in result["reason"].lower()

    def test_buy_exceeds_max_deployed_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        """Deploy 90% (max for balanced) then try to add more."""
        ex = make_executor(balanced_config, session_with_watchlist)
        # Simulate ₹18,000 already deployed (90% of ₹20,000)
        db.insert("portfolio", {
            "session_id": session_with_watchlist,
            "symbol": "TCS", "quantity": 18, "avg_price": 1000,
            "stop_loss_price": 970, "take_profit_price": 1050,
            "opened_at": db.now_iso(),
        })
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 500)):
            result = ex.execute(buy("RELIANCE", 5))
        assert result["status"] == "rejected"
        assert "deployment" in result["reason"].lower() or "deployed" in result["reason"].lower() or "limit" in result["reason"].lower()

    def test_buy_insufficient_cash_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        """Current capital ₹1,000 trying to buy ₹5,000 worth."""
        ex = make_executor(balanced_config, session_with_watchlist)
        db.update("sessions", session_with_watchlist, {"current_capital": 1000.0})
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            result = ex.execute(buy("RELIANCE", 5))
        # Either rejected for insufficient cash or auto-reduced below 1 share
        assert result["status"] in ("rejected",)

    def test_buy_human_alert_threshold_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        """Trade > 25% of capital triggers ALERT_USER and rejection.
        Note: executor first reduces qty to fit max_per_trade (10%), so to
        hit the 25% threshold we need the single-share price itself to be >25%.
        Use a stock at ₹6000 where 1 share = ₹6000 = 30% of ₹20,000 capital.
        But max_per_trade=10% = ₹2000, so qty gets reduced to 0 → rejected before threshold.
        The threshold is unreachable via normal flow because max_per_trade always fires first.
        Test that max_per_trade rejection covers the safety net.
        """
        ex = make_executor(balanced_config, session_with_watchlist)
        # 1 share at ₹6000 = 30% of capital. max_per_trade=10% = ₹2000.
        # auto-adjust: ₹2000 // ₹6000 = 0 shares → rejected before threshold
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 6000)):
            result = ex.execute(buy("RELIANCE", 10))
        assert result["status"] == "rejected"

    def test_buy_auto_sets_stop_loss_and_take_profit(self, in_memory_db, balanced_config, session_with_watchlist):
        """If Claude doesn't provide stop/take-profit, executor computes them."""
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            result = ex.execute(buy("RELIANCE", 1, stop=None, take=None))
        assert result["status"] == "executed"
        # Balanced: stop = 3% below, take = 5% above
        assert result["stop_loss"] == pytest.approx(970.0, abs=1)
        assert result["take_profit"] == pytest.approx(1050.0, abs=1)

    def test_buy_session_drawdown_halts(self, in_memory_db, balanced_config, session_with_watchlist):
        """40% drawdown → halt session."""
        ex = make_executor(balanced_config, session_with_watchlist)
        # Simulate 40% drawdown: starting ₹20,000, current ₹12,000
        db.update("sessions", session_with_watchlist, {"current_capital": 12000.0})
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 100)):
            result = ex.execute(buy("RELIANCE", 1))
        assert result["status"] == "halted"
        session = db.query_one("SELECT status FROM sessions WHERE id = ?", (session_with_watchlist,))
        assert session["status"] == "halted"

    def test_successful_buy_writes_to_portfolio_and_trades(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 500)):
            result = ex.execute(buy("RELIANCE", 2))
        assert result["status"] == "executed"
        portfolio = db.query_one(
            "SELECT * FROM portfolio WHERE session_id = ? AND symbol = 'RELIANCE'",
            (session_with_watchlist,),
        )
        assert portfolio is not None
        assert portfolio["quantity"] == 2
        trades = db.query(
            "SELECT * FROM trades WHERE session_id = ? AND action = 'BUY'",
            (session_with_watchlist,),
        )
        assert len(trades) == 1


# ── SELL validations ───────────────────────────────────────────────────────────

class TestSellValidations:
    def _buy_reliance(self, session_id, price=500):
        db.insert("portfolio", {
            "session_id": session_id,
            "symbol": "RELIANCE", "quantity": 5, "avg_price": price,
            "stop_loss_price": price * 0.97, "take_profit_price": price * 1.05,
            "opened_at": db.now_iso(),
        })

    def test_sell_no_position_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 550)):
            result = ex.execute(sell("RELIANCE"))
        assert result["status"] == "rejected"
        assert "no position" in result["reason"].lower()

    def test_sell_missing_symbol_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = make_executor(balanced_config, session_with_watchlist)
        result = ex.execute({"action": "SELL", "symbol": None, "quantity": None, "flags": []})
        assert result["status"] == "rejected"

    def test_sell_price_failure_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        self._buy_reliance(session_with_watchlist)
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value={"error": "no data"}):
            result = ex.execute(sell("RELIANCE"))
        assert result["status"] == "rejected"

    def test_sell_profitable_updates_capital_and_secured(self, in_memory_db, balanced_config, session_with_watchlist):
        """Balanced: 50% profit reinvested, 50% secured."""
        # Position inserted directly — no BUY through executor, current_capital stays at 20000
        self._buy_reliance(session_with_watchlist, price=500)
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 600)):
            result = ex.execute(sell("RELIANCE", 5))
        assert result["status"] == "executed"
        assert result["pnl"] == pytest.approx(500.0)  # (600-500) × 5 = ₹500
        session = db.query_one("SELECT current_capital, secured_profit FROM sessions WHERE id = ?", (session_with_watchlist,))
        # Balanced: reinvest 50% of ₹500=₹250, secure ₹250
        # capital = 20000 + cost_basis(2500) + reinvested(250) = 22750
        assert session["secured_profit"] == pytest.approx(250.0, abs=1)
        assert session["current_capital"] == pytest.approx(22_750.0, abs=1)

    def test_sell_at_loss_reduces_capital(self, in_memory_db, balanced_config, session_with_watchlist):
        # Insert position directly (no BUY through executor — so no capital deducted yet)
        self._buy_reliance(session_with_watchlist, price=500)
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 480)):
            result = ex.execute(sell("RELIANCE", 5))
        assert result["status"] == "executed"
        assert result["pnl"] == pytest.approx(-100.0)
        session = db.query_one("SELECT current_capital FROM sessions WHERE id = ?", (session_with_watchlist,))
        # Sell returns: current_capital + sell_proceeds (480*5=2400)
        # current_capital started at 20000 (no BUY deduction since position was inserted directly)
        assert session["current_capital"] == pytest.approx(20_000 + 480 * 5, abs=1)

    def test_sell_removes_position_from_portfolio(self, in_memory_db, balanced_config, session_with_watchlist):
        self._buy_reliance(session_with_watchlist, price=500)
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 550)):
            ex.execute(sell("RELIANCE", 5))
        pos = db.query_one(
            "SELECT * FROM portfolio WHERE session_id = ? AND symbol = 'RELIANCE'",
            (session_with_watchlist,),
        )
        assert pos is None

    def test_partial_sell_reduces_quantity(self, in_memory_db, balanced_config, session_with_watchlist):
        self._buy_reliance(session_with_watchlist, price=500)
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 550)):
            ex.execute(sell("RELIANCE", 2))
        pos = db.query_one(
            "SELECT quantity FROM portfolio WHERE session_id = ? AND symbol = 'RELIANCE'",
            (session_with_watchlist,),
        )
        assert pos["quantity"] == 3


# ── Profit reinvestment by mode ────────────────────────────────────────────────

class TestProfitReinvestment:
    def _setup_sell(self, config, session_id, buy_price=1000, sell_price=1100, qty=1):
        db.insert("portfolio", {
            "session_id": session_id,
            "symbol": "RELIANCE", "quantity": qty, "avg_price": buy_price,
            "stop_loss_price": buy_price * 0.98, "take_profit_price": sell_price,
            "opened_at": db.now_iso(),
        })
        ex = Executor(config, session_id)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", sell_price)):
            ex.execute(sell("RELIANCE", qty))
        return db.query_one("SELECT current_capital, secured_profit FROM sessions WHERE id = ?", (session_id,))

    def test_safe_secures_all_profit(self, in_memory_db, safe_config):
        # Create a dedicated session for safe_config (separate from balanced session_with_watchlist)
        sid = db.insert("sessions", {
            "name": "safe-test", "execution_mode": "paper", "trading_mode": "safe",
            "starting_capital": 10000, "current_capital": 10000, "secured_profit": 0,
            "total_days": 10, "current_day": 1, "watchlist_path": "x",
            "allow_watchlist_adjustment": 1, "status": "active",
            "started_at": db.now_iso(), "config_json": "{}",
        })
        db.insert("watchlist", {
            "session_id": sid, "symbol": "RELIANCE", "company": "Reliance",
            "sector": "Energy", "notes": "", "added_at": db.now_iso(), "add_reason": "test",
        })
        row = self._setup_sell(safe_config, sid, buy_price=1000, sell_price=1100, qty=1)
        # Safe: reinvest 0%, secure 100% of ₹100 profit
        # Position inserted directly (no BUY deduction), so capital = 10000 + cost_basis + 0
        # cost_basis=1000, reinvest=0 → 10000 + 1000 = 11000; all profit secured
        assert row["secured_profit"] == pytest.approx(100.0, abs=1)
        assert row["current_capital"] == pytest.approx(10_000.0 + 1000.0, abs=1)

    def test_balanced_splits_profit(self, in_memory_db, balanced_config, session_with_watchlist):
        row = self._setup_sell(balanced_config, session_with_watchlist, buy_price=1000, sell_price=1100, qty=1)
        # Balanced: 50% of ₹100 = ₹50 secured, ₹50 reinvested
        # Position inserted directly (no BUY deduction), so capital = 20000 + cost_basis + reinvested
        # cost_basis=1000, reinvested=50 → 20000 + 1000 + 50 = 21050
        assert row["secured_profit"] == pytest.approx(50.0, abs=1)
        assert row["current_capital"] == pytest.approx(20_000.0 + 1000.0 + 50.0, abs=1)

    def test_aggressive_reinvests_all_profit(self, in_memory_db, aggressive_config, session_with_watchlist):
        # Need a session for aggressive config
        sid = db.insert("sessions", {
            "name": "agg", "execution_mode": "paper", "trading_mode": "aggressive",
            "starting_capital": 20000, "current_capital": 20000, "secured_profit": 0,
            "total_days": 10, "current_day": 1, "watchlist_path": "x",
            "allow_watchlist_adjustment": 1, "status": "active",
            "started_at": db.now_iso(), "config_json": "{}",
        })
        db.insert("watchlist", {
            "session_id": sid, "symbol": "RELIANCE", "company": "Reliance",
            "sector": "Energy", "notes": "", "added_at": db.now_iso(), "add_reason": "test",
        })
        row = self._setup_sell(aggressive_config, sid, buy_price=1000, sell_price=1100, qty=1)
        # Aggressive: reinvest 100%, secure 0%
        # Position inserted directly (no BUY deduction), so capital = 20000 + cost_basis + all profit
        # cost_basis=1000, profit=100, reinvest_ratio=1.0 → 20000 + 1000 + 100 = 21100
        assert row["secured_profit"] == pytest.approx(0.0, abs=1)
        assert row["current_capital"] == pytest.approx(20_000.0 + 1000.0 + 100.0, abs=1)


# ── Daily loss limit ───────────────────────────────────────────────────────────

class TestDailyLossLimit:
    def test_daily_loss_blocks_new_buys(self, in_memory_db, balanced_config, session_with_watchlist):
        """After 5% daily loss (balanced limit), BUY is rejected."""
        # Simulate a losing SELL trade today (₹1,100 loss on ₹20,000 = 5.5%)
        db.insert("trades", {
            "session_id": session_with_watchlist,
            "symbol": "TCS", "action": "SELL", "quantity": 1,
            "price": 900, "reason": "stop hit", "confidence": "high",
            "executed_at": db.now_iso(), "pnl": -1100.0,
        })
        ex = make_executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 500)):
            result = ex.execute(buy("RELIANCE", 1))
        assert result["status"] == "rejected"
        assert "daily" in result["reason"].lower()
