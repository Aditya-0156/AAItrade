"""Tests for drawdown calculation and get_cash tool.

Verifies that:
1. get_cash reports total_pnl correctly (free cash + deployed + secured - starting)
2. Executor drawdown check uses correct total_value (free cash + deployed)
3. Deployed capital is NEVER counted as a loss
4. Drawdown only triggers at the configured threshold (40%)
"""

from unittest.mock import patch

import pytest

import aaitrade.db as db
from aaitrade.config import ExecutionMode, SessionConfig, TradingMode
from aaitrade.executor import Executor
from aaitrade.tools import portfolio_tools
from tests.conftest import make_price


# ── Helpers ─────────────────────────────────────────────────────────────────────

def make_session(in_memory_db, starting=20000.0, current_capital=None, secured=0.0):
    """Create a session and return its id."""
    if current_capital is None:
        current_capital = starting
    return db.insert("sessions", {
        "name": "test-session",
        "execution_mode": "paper",
        "trading_mode": "balanced",
        "starting_capital": starting,
        "current_capital": current_capital,
        "secured_profit": secured,
        "total_days": 5,
        "current_day": 1,
        "watchlist_path": "config/watchlist_seed.yaml",
        "allow_watchlist_adjustment": 1,
        "status": "active",
        "started_at": db.now_iso(),
        "config_json": "{}",
    })


def add_position(session_id, symbol, qty, avg_price, stop=None, take=None):
    """Add an open position to portfolio."""
    db.insert("portfolio", {
        "session_id": session_id,
        "symbol": symbol,
        "quantity": qty,
        "avg_price": avg_price,
        "stop_loss_price": stop or avg_price * 0.97,
        "take_profit_price": take or avg_price * 1.05,
        "opened_at": db.now_iso(),
    })


def add_watchlist(session_id, symbol):
    db.insert("watchlist", {
        "session_id": session_id,
        "symbol": symbol,
        "company": symbol,
        "sector": "Test",
        "notes": "",
        "added_at": db.now_iso(),
        "add_reason": "seed",
    })


# ── get_cash tests ───────────────────────────────────────────────────────────────

class TestGetCash:
    """Verify get_cash returns correct values including total_pnl."""

    def test_no_positions_no_pnl(self, in_memory_db):
        """Fresh session with no positions: total_pnl = 0."""
        sid = make_session(in_memory_db, starting=20000.0, current_capital=20000.0, secured=0.0)
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        assert result["starting_capital"] == 20000.0
        assert result["current_capital"] == 20000.0
        assert result["deployed_capital"] == 0.0
        assert result["secured_profit"] == 0.0
        assert result["total_portfolio_value"] == 20000.0
        assert result["total_pnl"] == 0.0

    def test_deployed_capital_not_counted_as_loss(self, in_memory_db):
        """
        CRITICAL: When ₹5,000 is deployed in positions, total_pnl must still be 0.
        Before the fix, this would show -₹5,000 loss (the bug).
        """
        # Simulate: bought ₹5,000 worth, cash dropped to ₹15,000
        sid = make_session(in_memory_db, starting=20000.0, current_capital=15000.0)
        add_position(sid, "RELIANCE", qty=5, avg_price=1000.0)  # 5 × ₹1000 = ₹5,000
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        assert result["current_capital"] == 15000.0
        assert result["deployed_capital"] == 5000.0
        assert result["available_cash"] == 10000.0
        assert result["total_portfolio_value"] == 20000.0   # 15000 + 5000 + 0
        assert result["total_pnl"] == 0.0                  # NOT -5000 (the old bug)

    def test_realized_profit_reflected(self, in_memory_db):
        """Secured profit from a winning sell shows in total_pnl."""
        # Sold a stock for ₹200 profit, safe mode secured it all
        sid = make_session(in_memory_db, starting=20000.0, current_capital=18000.0, secured=200.0)
        add_position(sid, "TCS", qty=1, avg_price=1800.0)  # 1 × ₹1800 deployed
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        # total = 18000 (cash) + 1800 (deployed) + 200 (secured) = 20000
        assert result["total_portfolio_value"] == 20000.0
        assert result["total_pnl"] == 0.0

    def test_real_loss_reflected(self, in_memory_db):
        """Actual realized loss (from a sell at a loss) shows correctly."""
        # Sold at a ₹300 loss: cash is now ₹19,700, no positions
        sid = make_session(in_memory_db, starting=20000.0, current_capital=19700.0, secured=0.0)
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        assert result["total_portfolio_value"] == 19700.0
        assert result["total_pnl"] == -300.0

    def test_multiple_positions(self, in_memory_db):
        """Multiple open positions all included in total_portfolio_value."""
        # Starting ₹20,000 → bought 3 positions totalling ₹8,000 → cash ₹12,000
        sid = make_session(in_memory_db, starting=20000.0, current_capital=12000.0)
        add_position(sid, "ONGC", qty=4, avg_price=261.8)       # ₹1,047.2
        add_position(sid, "ADANIPORTS", qty=1, avg_price=1357.1) # ₹1,357.1
        add_position(sid, "HDFCBANK", qty=5, avg_price=800.8)    # ₹4,004.0
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        deployed = (4 * 261.8) + (1 * 1357.1) + (5 * 800.8)
        assert abs(result["deployed_capital"] - deployed) < 0.1
        assert abs(result["total_portfolio_value"] - (12000.0 + deployed)) < 0.1
        assert abs(result["total_pnl"] - (12000.0 + deployed - 20000.0)) < 0.1

    def test_simulated_session1_balanced(self, in_memory_db):
        """
        Reproduce session 1 (balanced-5d) exact numbers from DB.
        current_capital=14359.25, deployed=5546.6 (ADANIPORTS+ASIANPAINT×0.8+TCS)
        total = 14359.25 + 5546.6 = 19905.85 → pnl = -94.15 (tiny loss, not -7000)
        """
        sid = make_session(in_memory_db, starting=20000.0, current_capital=14359.25, secured=0.0)
        # Exact values from live DB
        add_position(sid, "ADANIPORTS", qty=1,   avg_price=1357.6)   # ₹1357.60
        add_position(sid, "ASIANPAINT", qty=1,   avg_price=1792.8)   # 0.8×2241 = ₹1792.80 (stored as unit cost)
        add_position(sid, "TCS",        qty=1,   avg_price=2396.2)   # ₹2396.20
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        # total = 14359.25 + 5546.6 = 19905.85 → pnl = -94.15
        assert result["total_pnl"] > -500   # NOT -₹7,000 (the old bug)
        assert result["total_pnl"] < 100    # not magically profitable either

    def test_simulated_session2_aggressive(self, in_memory_db):
        """
        Reproduce session 2 (aggressive-5d): ₹11,285 cash + ₹8,753 deployed = ₹20,038.
        total_pnl should be ~+₹38.50 (the ONGC profit).
        """
        sid = make_session(in_memory_db, starting=20000.0, current_capital=11285.4, secured=0.0)
        add_position(sid, "ADANIPORTS", qty=1, avg_price=1357.1)
        add_position(sid, "BAJFINANCE", qty=4, avg_price=848.0)
        add_position(sid, "HDFCBANK", qty=5, avg_price=800.8)
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        deployed = 1357.1 + (4 * 848.0) + (5 * 800.8)
        total = 11285.4 + deployed
        assert abs(result["total_portfolio_value"] - total) < 0.1
        # total should be ~₹20,038 (small profit from ONGC sell)
        assert result["total_pnl"] > 0     # profitable
        assert result["total_pnl"] < 200   # not wildly off

    def test_simulated_session3_safe(self, in_memory_db):
        """
        Reproduce session 3 (safe-5d): ₹14,181 cash + ₹5,769 deployed + ₹44.50 secured.
        total_pnl = ₹14,181 + ₹5,769 + ₹44.50 - ₹20,000 = -₹5.50 (essentially zero).
        """
        sid = make_session(in_memory_db, starting=20000.0, current_capital=14180.7, secured=44.5)
        add_position(sid, "ASIANPAINT", qty=1, avg_price=2199.8)
        add_position(sid, "ICICIBANK", qty=2, avg_price=1261.0)
        add_position(sid, "ONGC", qty=4, avg_price=261.8)
        portfolio_tools.set_session_id(sid)

        result = portfolio_tools.get_cash()

        deployed = 2199.8 + (2 * 1261.0) + (4 * 261.8)
        expected_total = 14180.7 + deployed + 44.5
        expected_pnl = expected_total - 20000.0

        assert abs(result["total_portfolio_value"] - expected_total) < 0.1
        assert abs(result["total_pnl"] - expected_pnl) < 0.1
        # pnl should be tiny (< ₹50 either way), NOT -₹7,000
        assert abs(result["total_pnl"]) < 50


# ── Executor drawdown tests ─────────────────────────────────────────────────────

class TestExecutorDrawdown:
    """Verify the executor drawdown check uses free_cash + deployed correctly."""

    def _make_executor_config(self):
        return SessionConfig(
            execution_mode=ExecutionMode.PAPER,
            trading_mode=TradingMode.BALANCED,
            starting_capital=20000.0,
            total_days=5,
            watchlist_path="config/watchlist_seed.yaml",
        )

    def test_deployed_capital_not_triggering_halt(self, in_memory_db):
        """
        ₹8,000 deployed in positions + ₹12,000 cash = ₹20,000 total.
        Drawdown = 0% → should NOT halt.
        """
        config = self._make_executor_config()
        sid = make_session(in_memory_db, starting=20000.0, current_capital=12000.0)
        add_watchlist(sid, "RELIANCE")
        add_position(sid, "HDFCBANK", qty=10, avg_price=800.0)  # ₹8,000 deployed
        ex = Executor(config, sid)

        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 500)):
            result = ex.execute({
                "action": "BUY", "symbol": "RELIANCE", "quantity": 1,
                "stop_loss_price": None, "take_profit_price": None,
                "reason": "test", "confidence": "high", "flags": [],
            })

        assert result["status"] != "halted", \
            f"Session halted incorrectly — deployed capital was treated as loss. Result: {result}"

    def test_real_40pct_loss_halts(self, in_memory_db):
        """
        Actual ₹8,000 loss (cash only, no positions): drawdown = 40% → halt.
        """
        config = self._make_executor_config()
        # ₹12,000 cash, no positions = ₹8,000 real loss = 40% drawdown
        sid = make_session(in_memory_db, starting=20000.0, current_capital=12000.0)
        add_watchlist(sid, "RELIANCE")
        ex = Executor(config, sid)

        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 100)):
            result = ex.execute({
                "action": "BUY", "symbol": "RELIANCE", "quantity": 1,
                "stop_loss_price": None, "take_profit_price": None,
                "reason": "test", "confidence": "high", "flags": [],
            })

        assert result["status"] == "halted", \
            f"Session should have halted at 40% drawdown. Result: {result}"

    def test_39pct_loss_does_not_halt(self, in_memory_db):
        """Just below 40% threshold: should NOT halt."""
        config = self._make_executor_config()
        # ₹12,200 cash, no positions = ₹7,800 loss = 39% drawdown
        sid = make_session(in_memory_db, starting=20000.0, current_capital=12200.0)
        add_watchlist(sid, "RELIANCE")
        ex = Executor(config, sid)

        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 100)):
            result = ex.execute({
                "action": "BUY", "symbol": "RELIANCE", "quantity": 1,
                "stop_loss_price": None, "take_profit_price": None,
                "reason": "test", "confidence": "high", "flags": [],
            })

        assert result["status"] != "halted", \
            f"Session should not halt at 39% drawdown. Result: {result}"
