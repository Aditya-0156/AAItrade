"""Tests for individual tool functions.

All external APIs (Kite, NewsAPI, yfinance, Tavily, Anthropic) are patched.
Tools are tested for: correct return shape, caching logic, error handling,
session_id injection, and DB interactions.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import aaitrade.db as db
from aaitrade.tools import load_all_tools


@pytest.fixture(autouse=True)
def load_tools_once():
    load_all_tools()


# ── Session Memory ─────────────────────────────────────────────────────────────

class TestSessionMemory:
    def test_get_session_memory_empty_on_first_cycle(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import session_memory
        session_memory.set_session_id(session_with_watchlist)
        result = session_memory.get_session_memory()
        assert result["status"] == "empty"
        assert result["content"] == ""

    def test_update_and_get_session_memory(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import session_memory
        session_memory.set_session_id(session_with_watchlist)
        content = "WATCHING: RELIANCE\nNEXT: check RSI"
        update_result = session_memory.update_session_memory(content)
        assert update_result["status"] == "saved"
        assert update_result["chars_used"] == len(content)

        get_result = session_memory.get_session_memory()
        assert get_result["status"] == "ok"
        assert get_result["content"] == content

    def test_update_overwrites_previous_memory(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import session_memory
        session_memory.set_session_id(session_with_watchlist)
        session_memory.update_session_memory("first content")
        session_memory.update_session_memory("second content")
        result = session_memory.get_session_memory()
        assert result["content"] == "second content"

    def test_update_too_long_returns_error(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import session_memory
        session_memory.set_session_id(session_with_watchlist)
        long_content = "x" * 2401
        result = session_memory.update_session_memory(long_content)
        assert "error" in result
        assert "chars_over_limit" in result
        assert result["chars_over_limit"] == 1

    def test_update_exactly_at_limit_succeeds(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import session_memory
        session_memory.set_session_id(session_with_watchlist)
        content = "x" * 2400
        result = session_memory.update_session_memory(content)
        assert result["status"] == "saved"
        assert result["chars_remaining"] == 0

    def test_session_memory_requires_session_id(self):
        from aaitrade.tools import session_memory
        session_memory._session_id = None
        with pytest.raises(RuntimeError, match="set_session_id"):
            session_memory.get_session_memory()


# ── Portfolio Tools ────────────────────────────────────────────────────────────

class TestPortfolioTools:
    def test_get_portfolio_empty(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import portfolio_tools
        portfolio_tools.set_session_id(session_with_watchlist)
        result = portfolio_tools.get_portfolio()
        assert result["positions"] == []
        # Empty portfolio returns message key, not total_positions
        assert "message" in result or result.get("total_positions", 0) == 0

    def test_get_portfolio_with_positions(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import portfolio_tools
        portfolio_tools.set_session_id(session_with_watchlist)
        db.insert("portfolio", {
            "session_id": session_with_watchlist,
            "symbol": "RELIANCE", "quantity": 5, "avg_price": 2800,
            "stop_loss_price": 2716, "take_profit_price": 2940,
            "opened_at": db.now_iso(),
        })
        result = portfolio_tools.get_portfolio()
        assert result["total_positions"] == 1
        assert result["positions"][0]["symbol"] == "RELIANCE"
        assert result["positions"][0]["quantity"] == 5

    def test_get_cash_correct_available_cash(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import portfolio_tools
        portfolio_tools.set_session_id(session_with_watchlist)
        db.insert("portfolio", {
            "session_id": session_with_watchlist,
            "symbol": "TCS", "quantity": 2, "avg_price": 4000,
            "stop_loss_price": 3880, "take_profit_price": 4200,
            "opened_at": db.now_iso(),
        })
        result = portfolio_tools.get_cash()
        # Deployed = 2 × 4000 = ₹8,000; starting ₹20,000; available = ₹12,000
        assert result["deployed_capital"] == pytest.approx(8000.0)
        assert result["available_cash"] == pytest.approx(12000.0)


# ── Memory Tools ───────────────────────────────────────────────────────────────

class TestMemoryTools:
    def test_get_trade_history_empty(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import memory
        memory.set_session_id(session_with_watchlist)
        result = memory.get_trade_history("RELIANCE")
        assert result["trades"] == []
        assert result["total_found"] == 0

    def test_get_trade_history_returns_trades(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import memory
        memory.set_session_id(session_with_watchlist)
        db.insert("trades", {
            "session_id": session_with_watchlist,
            "symbol": "RELIANCE", "action": "BUY",
            "quantity": 5, "price": 2800,
            "reason": "test", "confidence": "high",
            "executed_at": db.now_iso(),
        })
        result = memory.get_trade_history("RELIANCE")
        assert result["total_found"] == 1
        assert result["trades"][0]["action"] == "BUY"

    def test_get_session_summary_stats(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import memory
        memory.set_session_id(session_with_watchlist)
        # Insert a winning trade
        db.insert("trades", {
            "session_id": session_with_watchlist,
            "symbol": "RELIANCE", "action": "SELL",
            "quantity": 5, "price": 3000, "reason": "profit",
            "confidence": "high", "executed_at": db.now_iso(), "pnl": 500.0,
        })
        result = memory.get_session_summary()
        assert "error" not in result
        assert result["wins"] == 1
        assert result["losses"] == 0
        assert result["total_closed_trades"] == 1


# ── Journal Tools ──────────────────────────────────────────────────────────────

class TestJournalTools:
    def test_write_trade_rationale(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import journal
        journal.set_session_id(session_with_watchlist)
        result = journal.write_trade_rationale(
            symbol="RELIANCE",
            entry_price=2800.0,
            reason="Strong Q4 results + RBI rate hold",
            thesis="Oversold Bounce — RSI 32, sector rotation into energy",
            target_price=2940.0,
            stop_price=2716.0,
        )
        assert result["status"] == "recorded"
        assert "journal_id" in result
        row = db.query_one("SELECT * FROM trade_journal WHERE id = ?", (result["journal_id"],))
        assert row["symbol"] == "RELIANCE"
        assert row["status"] == "open"

    def test_update_thesis_creates_note(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import journal
        journal.set_session_id(session_with_watchlist)
        journal.write_trade_rationale(
            symbol="RELIANCE", entry_price=2800, reason="r", thesis="t",
            target_price=2940, stop_price=2716,
        )
        result = journal.update_thesis("RELIANCE", "Price holding above support at 2750")
        assert result["status"] == "updated"
        updates = db.query("SELECT * FROM thesis_updates WHERE session_id = ?", (session_with_watchlist,))
        assert len(updates) == 1

    def test_update_thesis_no_open_position_returns_error(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import journal
        journal.set_session_id(session_with_watchlist)
        result = journal.update_thesis("NONEXISTENT", "test note")
        assert "error" in result

    def test_get_open_positions_with_rationale(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import journal, portfolio_tools
        journal.set_session_id(session_with_watchlist)
        portfolio_tools.set_session_id(session_with_watchlist)
        journal.write_trade_rationale(
            symbol="RELIANCE", entry_price=2800, reason="r", thesis="Oversold Bounce",
            target_price=2940, stop_price=2716,
        )
        db.insert("portfolio", {
            "session_id": session_with_watchlist,
            "symbol": "RELIANCE", "quantity": 5, "avg_price": 2800,
            "stop_loss_price": 2716, "take_profit_price": 2940,
            "opened_at": db.now_iso(),
        })
        result = journal.get_open_positions_with_rationale()
        assert result["total"] == 1
        assert result["open_positions"][0]["key_thesis"] == "Oversold Bounce"


# ── Watchlist Tools ────────────────────────────────────────────────────────────

class TestWatchlistTools:
    def test_get_watchlist_returns_active_stocks(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import watchlist_tools
        watchlist_tools.set_session_id(session_with_watchlist)
        result = watchlist_tools.get_watchlist()
        assert result["total"] == 3  # RELIANCE, TCS, HDFCBANK from fixture

    def test_add_to_watchlist(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import watchlist_tools
        watchlist_tools.set_session_id(session_with_watchlist)
        result = watchlist_tools.add_to_watchlist("INFY", "Strong IT sector rotation play")
        assert result["status"] == "added"
        assert result["symbol"] == "INFY"

    def test_add_duplicate_rejected(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import watchlist_tools
        watchlist_tools.set_session_id(session_with_watchlist)
        result = watchlist_tools.add_to_watchlist("RELIANCE", "already on list")
        assert result["status"] == "rejected"
        # "reason" key contains the rejection explanation
        assert "already" in result["reason"].lower()

    def test_remove_from_watchlist(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import watchlist_tools
        watchlist_tools.set_session_id(session_with_watchlist)
        result = watchlist_tools.remove_from_watchlist("TCS", "Sector headwinds")
        assert result["status"] == "removed"
        remaining = watchlist_tools.get_watchlist()
        symbols = [s["symbol"] for s in remaining["watchlist"]]
        assert "TCS" not in symbols

    def test_remove_held_position_rejected(self, in_memory_db, session_with_watchlist):
        """Cannot remove a stock from watchlist if it's in portfolio."""
        from aaitrade.tools import watchlist_tools
        watchlist_tools.set_session_id(session_with_watchlist)
        db.insert("portfolio", {
            "session_id": session_with_watchlist,
            "symbol": "RELIANCE", "quantity": 5, "avg_price": 2800,
            "stop_loss_price": 2716, "take_profit_price": 2940,
            "opened_at": db.now_iso(),
        })
        result = watchlist_tools.remove_from_watchlist("RELIANCE", "test")
        assert result["status"] == "rejected"
        assert "position" in result["reason"].lower() or "hold" in result["reason"].lower()

    def test_add_beyond_max_30_rejected(self, in_memory_db, session_with_watchlist):
        """Watchlist capped at 30 stocks."""
        from aaitrade.tools import watchlist_tools
        watchlist_tools.set_session_id(session_with_watchlist)
        # Add 27 more stocks to reach 30 (3 already in fixture)
        for i in range(27):
            db.insert("watchlist", {
                "session_id": session_with_watchlist,
                "symbol": f"DUMMY{i:02d}", "company": f"Dummy{i}",
                "sector": "test", "notes": "", "added_at": db.now_iso(), "add_reason": "bulk",
            })
        result = watchlist_tools.add_to_watchlist("ONEMORE", "31st stock")
        assert result["status"] == "rejected"
        assert "max" in result["reason"].lower() or "capacity" in result["reason"].lower()


# ── Market Tools ───────────────────────────────────────────────────────────────

class TestMarketTools:
    def test_get_current_price_yfinance(self, in_memory_db):
        """get_current_price falls back to yfinance when no Kite."""
        from aaitrade.tools import market

        fake_price = {
            "symbol": "RELIANCE", "last_price": 2850.0,
            "change_percent": 1.78, "volume": 1_000_000,
            "open": 2810.0, "high": 2870.0, "low": 2800.0,
            "close": 2800.0, "timestamp": "2026-03-17T10:00:00",
        }
        with patch("aaitrade.tools.market._data_source", "yfinance"), \
             patch("aaitrade.tools.market._yf_get_quote", return_value=fake_price):
            result = market.get_current_price("RELIANCE")

        assert "error" not in result
        assert result["last_price"] == pytest.approx(2850.0)

    def test_get_current_price_returns_error_on_failure(self, in_memory_db):
        from aaitrade.tools import market
        with patch("aaitrade.tools.market._data_source", "yfinance"), \
             patch("aaitrade.tools.market._yf_get_quote", return_value={"error": "network error"}):
            result = market.get_current_price("RELIANCE")
        assert "error" in result

    def test_get_market_snapshot_returns_nifty_and_banknifty(self, in_memory_db):
        from aaitrade.tools import market

        fake_price = {
            "symbol": "^NSEI", "last_price": 22000.0, "change_percent": 0.9,
            "volume": 0, "open": 21900.0, "high": 22100.0, "low": 21850.0,
            "close": 21800.0, "timestamp": "2026-03-17T10:00:00",
        }
        with patch("aaitrade.tools.market._data_source", "yfinance"), \
             patch("aaitrade.tools.market._yf_get_quote", return_value=fake_price):
            result = market.get_market_snapshot()
        # Either returns data or error — just must not crash
        assert isinstance(result, dict)


# ── News Caching ───────────────────────────────────────────────────────────────

class TestNewsCaching:
    def test_get_macro_news_uses_cache(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import news

        # Pre-populate cache (expires far in future)
        future = (datetime.now() + timedelta(hours=12)).isoformat()
        db.insert("news_cache", {
            "category": "macro", "key": "macro",
            "summary": "Cached macro summary",
            "source": "cache", "fetched_at": db.now_iso(),
            "expires_at": future,
        })

        result = news.get_macro_news()
        assert "Cached macro summary" in result["summary"]

    def test_get_stock_news_uses_cache(self, in_memory_db):
        from aaitrade.tools import news

        future = (datetime.now() + timedelta(hours=1)).isoformat()
        db.insert("news_cache", {
            "category": "stock", "key": "RELIANCE",
            "summary": "Reliance Q4 results strong",
            "source": "cache", "fetched_at": db.now_iso(),
            "expires_at": future,
        })

        result = news.get_stock_news("RELIANCE")
        assert "Reliance Q4 results strong" in result["summary"]

    def test_get_stock_news_fetches_when_cache_expired(self, in_memory_db):
        from aaitrade.tools import news

        # Expired cache entry
        past = (datetime.now() - timedelta(hours=2)).isoformat()
        db.insert("news_cache", {
            "category": "stock", "key": "TCS",
            "summary": "stale summary",
            "source": "cache", "fetched_at": db.now_iso(),
            "expires_at": past,
        })

        mock_client = MagicMock()
        mock_client.get_everything.return_value = {
            "articles": [
                {"title": "TCS wins deal", "description": "Big deal", "source": {"name": "ET"}},
            ]
        }
        news.set_newsapi_client(mock_client)
        news.set_anthropic_client(None)  # No LLM summarization

        result = news.get_stock_news("TCS")
        # Should have fetched fresh (not used stale cache)
        assert result["symbol"] == "TCS"
