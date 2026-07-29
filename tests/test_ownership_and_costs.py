"""Ownership isolation + expense tracking tests.

The ownership tests protect real money: the same Zerodha account holds the
user's personally-bought shares (e.g. 254 HDFCBANK). The system must never
adopt them, never sell them, and never count them.
"""

from unittest.mock import MagicMock, patch

import pytest

from aaitrade import db
from aaitrade.executor import Executor
from aaitrade.portfolio_sync import sync_portfolio_with_kite
from aaitrade.costs import (
    compute_call_cost_inr,
    record_api_usage,
    ensure_monthly_subscription,
    expense_summary,
)
from tests.conftest import make_price


def _live_session(sid):
    db.update("sessions", sid, {"execution_mode": "live"})
    return sid


def _fake_kite(holdings):
    kite = MagicMock()
    kite.holdings.return_value = holdings
    return kite


class TestPersonalHoldingsAreNeverAdopted:
    def test_user_shares_not_added_to_portfolio(self, in_memory_db, session_with_watchlist):
        """The exact real-world case: 254 hand-bought HDFCBANK, which IS on the watchlist."""
        _live_session(session_with_watchlist)
        kite = _fake_kite([
            {"tradingsymbol": "HDFCBANK", "quantity": 220, "t1_quantity": 34, "average_price": 743.89},
        ])

        report = sync_portfolio_with_kite(session_with_watchlist, kite)

        positions = db.query(
            "SELECT * FROM portfolio WHERE session_id = ?", (session_with_watchlist,)
        )
        assert positions == [], "user's personal shares must NEVER enter the system portfolio"
        assert report["read_only"] is True
        ext = {e["symbol"]: e["external_qty"] for e in report["external_holdings"]}
        assert ext["HDFCBANK"] == 254  # 220 settled + 34 T1

    def test_system_position_not_deleted_when_broker_shows_more(self, in_memory_db, session_with_watchlist):
        """System owns 10; user also owns 254 of the same stock. Nothing changes."""
        _live_session(session_with_watchlist)
        db.insert("portfolio", {
            "session_id": session_with_watchlist, "symbol": "HDFCBANK",
            "quantity": 10, "avg_price": 800.0,
            "stop_loss_price": 776.0, "take_profit_price": 840.0,
            "opened_at": db.now_iso(),
        })
        kite = _fake_kite([
            {"tradingsymbol": "HDFCBANK", "quantity": 264, "t1_quantity": 0, "average_price": 745.0},
        ])

        report = sync_portfolio_with_kite(session_with_watchlist, kite)

        pos = db.query_one(
            "SELECT quantity, avg_price FROM portfolio WHERE session_id = ? AND symbol = 'HDFCBANK'",
            (session_with_watchlist,),
        )
        assert pos["quantity"] == 10, "system quantity must not be overwritten by broker total"
        assert pos["avg_price"] == 800.0, "system avg price must not be overwritten"
        assert report["external_holdings"][0]["external_qty"] == 254
        assert report["warnings"] == []

    def test_under_backed_position_warns_but_does_not_mutate(self, in_memory_db, session_with_watchlist):
        _live_session(session_with_watchlist)
        db.insert("portfolio", {
            "session_id": session_with_watchlist, "symbol": "TCS",
            "quantity": 5, "avg_price": 3000.0,
            "stop_loss_price": None, "take_profit_price": None,
            "opened_at": db.now_iso(),
        })
        kite = _fake_kite([{"tradingsymbol": "TCS", "quantity": 2, "t1_quantity": 0, "average_price": 3000.0}])

        report = sync_portfolio_with_kite(session_with_watchlist, kite)

        assert report["status"] == "warnings"
        assert report["warnings"][0]["type"] == "under_backed"
        pos = db.query_one(
            "SELECT quantity FROM portfolio WHERE session_id = ? AND symbol = 'TCS'",
            (session_with_watchlist,),
        )
        assert pos["quantity"] == 5, "must not silently rewrite quantity"

    def test_paper_mode_skips(self, in_memory_db, session_with_watchlist):
        result = sync_portfolio_with_kite(session_with_watchlist, _fake_kite([]))
        assert result["status"] == "skipped"


class TestSellQuantityClamp:
    def test_cannot_sell_more_than_system_owns(self, in_memory_db, balanced_config, session_with_watchlist):
        """Claude asking for 254 when the system owns 10 must sell only 10."""
        ex = Executor(balanced_config, session_with_watchlist)
        db.insert("portfolio", {
            "session_id": session_with_watchlist, "symbol": "HDFCBANK",
            "quantity": 10, "avg_price": 800.0,
            "stop_loss_price": None, "take_profit_price": None,
            "opened_at": db.now_iso(),
        })
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("HDFCBANK", 810)):
            result = ex.execute({
                "action": "SELL", "symbol": "HDFCBANK", "quantity": 254,
                "reason": "test", "confidence": "high", "flags": [],
            })
        assert result["status"] == "executed"
        assert result["quantity"] == 10, "must clamp to system-owned quantity"
        trade = db.query_one(
            "SELECT quantity FROM trades WHERE session_id = ? AND action = 'SELL'",
            (session_with_watchlist,),
        )
        assert trade["quantity"] == 10

    def test_zero_or_negative_quantity_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = Executor(balanced_config, session_with_watchlist)
        db.insert("portfolio", {
            "session_id": session_with_watchlist, "symbol": "TCS",
            "quantity": 5, "avg_price": 3000.0,
            "stop_loss_price": None, "take_profit_price": None,
            "opened_at": db.now_iso(),
        })
        result = ex.execute({
            "action": "SELL", "symbol": "TCS", "quantity": -3,
            "reason": "test", "confidence": "high", "flags": [],
        })
        assert result["status"] == "rejected"


class TestCostTracking:
    def test_haiku_cost_math(self):
        # 1M input + 1M output on Haiku 4.5 = $1 + $5 = $6 → ₹528 at 88
        cost = compute_call_cost_inr("claude-haiku-4-5", 1_000_000, 1_000_000)
        assert cost == pytest.approx(6.0 * 88.0, rel=0.01)

    def test_cache_reads_are_cheap(self):
        full = compute_call_cost_inr("claude-sonnet-5", 1_000_000, 0)
        cached = compute_call_cost_inr("claude-sonnet-5", 0, 0, cache_read_tokens=1_000_000)
        assert cached == pytest.approx(full * 0.1, rel=0.01)

    def test_unknown_model_falls_back(self):
        assert compute_call_cost_inr("some-future-model", 1_000_000, 0) > 0

    def test_record_and_summarise(self, in_memory_db, session_with_watchlist):
        record_api_usage(session_with_watchlist, 1, "claude-haiku-4-5",
                         {"input": 100_000, "output": 20_000, "cache_read": 500_000, "cache_write": 0})
        ensure_monthly_subscription(session_with_watchlist)
        ensure_monthly_subscription(session_with_watchlist)  # idempotent

        s = expense_summary(session_with_watchlist)
        assert s["api_calls"] == 1
        assert s["api_cost"] > 0
        assert s["subscriptions"] == 500.0, "subscription must be recorded exactly once per month"
        # No trades yet → net profit is just negative expenses
        assert s["net_profit_after_all_expenses"] == pytest.approx(
            -(s["api_cost"] + s["subscriptions"]), rel=0.01
        )

    def test_net_profit_excludes_double_counting_charges(self, in_memory_db, session_with_watchlist):
        # A sell whose pnl is ALREADY net of its charges
        db.insert("trades", {
            "session_id": session_with_watchlist, "symbol": "TCS", "action": "SELL",
            "quantity": 5, "price": 3100.0, "reason": "t", "confidence": "high",
            "executed_at": db.now_iso(), "pnl": 480.0, "charges": 20.0,
        })
        s = expense_summary(session_with_watchlist)
        assert s["realised_pnl_net_of_charges"] == 480.0
        assert s["trading_charges"] == 20.0
        # charges must NOT be subtracted again from realised pnl
        assert s["net_profit_after_all_expenses"] == pytest.approx(480.0, abs=0.01)
