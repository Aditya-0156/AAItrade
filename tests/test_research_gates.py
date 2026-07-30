"""Research gates — a BUY must be researched and explained, not just computed.

These enforce that the agent behaves like a trader (why is it cheap?) rather
than a calculator (the touch count is 7).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from aaitrade import db
from aaitrade.executor import Executor
from aaitrade.tools import trading
from tests.conftest import make_price


GOOD_WHY = ("Whole PSU banking pack sold off on a rate-cut headline today; nothing "
            "company-specific, order book and guidance unchanged, so the discount "
            "should close as the sector re-rates over the next week.")


def _log_news_call(session_id, cycle, symbol):
    db.insert("tool_calls", {
        "session_id": session_id, "cycle_number": cycle,
        "tool_name": "get_stock_news",
        "parameters": json.dumps({"symbol": symbol}),
        "result_summary": "{}", "called_at": db.now_iso(),
    })


_IST = timezone(timedelta(hours=5, minutes=30))
_TRADING_HOURS = datetime(2026, 7, 30, 12, 0, tzinfo=_IST)  # inside the trade window


@pytest.fixture
def wired(in_memory_db, balanced_config, session_with_watchlist):
    """Executor wired up, with the clock pinned inside the trading window so
    the 9:30-11:00 observe-only block doesn't depend on when tests run."""
    ex = Executor(balanced_config, session_with_watchlist)
    trading.set_trading_context(ex, session_with_watchlist, 1)
    fake_dt = MagicMock(wraps=datetime)
    fake_dt.now.return_value = _TRADING_HOURS
    with patch.object(trading, "datetime", fake_dt):
        yield session_with_watchlist


class TestNewsGate:
    def test_buy_blocked_without_research(self, wired):
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            r = trading.execute_trade("BUY", "RELIANCE", 2, reason="looks good", why_now=GOOD_WHY)
        assert r["status"] == "rejected"
        assert "not researched" in r["reason"].lower()

    def test_buy_allowed_after_news_check(self, wired):
        _log_news_call(wired, 1, "RELIANCE")
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            r = trading.execute_trade("BUY", "RELIANCE", 2, reason="dip buy", why_now=GOOD_WHY)
        assert r["status"] == "executed"

    def test_research_on_another_symbol_does_not_count(self, wired):
        _log_news_call(wired, 1, "TCS")
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            r = trading.execute_trade("BUY", "RELIANCE", 2, reason="x", why_now=GOOD_WHY)
        assert r["status"] == "rejected"

    def test_sell_is_not_gated(self, wired):
        db.insert("portfolio", {
            "session_id": wired, "symbol": "RELIANCE", "quantity": 3, "avg_price": 990.0,
            "stop_loss_price": None, "take_profit_price": None, "opened_at": db.now_iso(),
        })
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1010)):
            r = trading.execute_trade("SELL", "RELIANCE", 3, reason="target hit")
        assert r["status"] == "executed", "exits must never be blocked by research gates"


class TestWhyNowQuality:
    def test_missing_why_now_blocked(self, wired):
        _log_news_call(wired, 1, "RELIANCE")
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            r = trading.execute_trade("BUY", "RELIANCE", 2, reason="good setup")
        assert r["status"] == "rejected"
        assert "why_now" in r["reason"]

    def test_too_short_why_now_blocked(self, wired):
        _log_news_call(wired, 1, "RELIANCE")
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            r = trading.execute_trade("BUY", "RELIANCE", 2, reason="x", why_now="dip")
        assert r["status"] == "rejected"

    def test_metric_restatement_blocked(self, wired):
        """The exact failure seen in production: numbers dressed up as reasoning."""
        _log_news_call(wired, 1, "RELIANCE")
        template = ("Strong oscillation (5 direction changes), entry has 7 touches in 30d "
                    "(demonstrated floor), band position 23% bottom third, net profit 1.24% "
                    "after charges, scanner rank #1.")
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            r = trading.execute_trade("BUY", "RELIANCE", 2, reason="x", why_now=template)
        assert r["status"] == "rejected"
        assert "restates the metrics" in r["reason"]

    def test_why_now_is_recorded_on_the_trade(self, wired):
        _log_news_call(wired, 1, "RELIANCE")
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1000)):
            trading.execute_trade("BUY", "RELIANCE", 2, reason="dip buy", why_now=GOOD_WHY)
        t = db.query_one("SELECT reason FROM trades WHERE symbol = 'RELIANCE'")
        assert "WHY NOW:" in t["reason"] and "PSU banking pack" in t["reason"]


class TestScannerAutoAdd:
    def test_scanner_pick_auto_added(self, in_memory_db, balanced_config, session_with_watchlist):
        """A scanner-vetted symbol shouldn't cost a wasted round trip."""
        db.insert("scan_results", {
            "scan_date": "2026-07-30", "symbol": "GICRE", "rank": 1, "score": 93.2,
            "close": 357.0, "entry_level": 356.7, "entry_touches": 5,
            "target_level": 362.1, "target_touches": 17, "gap_pct": 1.4,
            "band_pos": 7.8, "shape": "OSCILLATING", "turnover_cr": 50.0,
            "created_at": db.now_iso(),
        })
        ex = Executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("GICRE", 357)):
            r = ex.execute({"action": "BUY", "symbol": "GICRE", "quantity": 10,
                            "reason": "scanner pick", "confidence": "high", "flags": []})
        assert r["status"] == "executed"
        assert db.query_one(
            "SELECT 1 FROM watchlist WHERE session_id = ? AND symbol = 'GICRE' AND removed_at IS NULL",
            (session_with_watchlist,),
        )

    def test_unvetted_unknown_symbol_still_rejected(self, in_memory_db, balanced_config, session_with_watchlist):
        ex = Executor(balanced_config, session_with_watchlist)
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RANDOMCO", 100)):
            r = ex.execute({"action": "BUY", "symbol": "RANDOMCO", "quantity": 10,
                            "reason": "x", "confidence": "high", "flags": []})
        assert r["status"] == "rejected"
        assert "add_to_watchlist" in r["reason"]
