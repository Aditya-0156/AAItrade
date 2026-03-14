"""Tests for the database layer (db.py)."""

import pytest
import aaitrade.db as db


class TestSchema:
    def test_all_tables_created(self, in_memory_db):
        cursor = in_memory_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        expected = {
            "sessions", "trades", "portfolio", "decisions",
            "trade_journal", "thesis_updates", "news_cache",
            "search_cache", "daily_summary", "tool_calls",
            "watchlist", "session_memory",
        }
        assert expected.issubset(tables)


class TestInsertAndQuery:
    def test_insert_returns_id(self, in_memory_db):
        row_id = db.insert("sessions", {
            "name": "test",
            "execution_mode": "paper",
            "trading_mode": "safe",
            "starting_capital": 10000,
            "current_capital": 10000,
            "secured_profit": 0,
            "total_days": 5,
            "current_day": 1,
            "watchlist_path": "x",
            "allow_watchlist_adjustment": 1,
            "status": "active",
            "started_at": db.now_iso(),
            "config_json": "{}",
        })
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_query_one_returns_dict(self, in_memory_db, session_id):
        row = db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        assert row is not None
        assert row["id"] == session_id
        assert row["trading_mode"] == "balanced"

    def test_query_one_returns_none_for_missing(self, in_memory_db):
        row = db.query_one("SELECT * FROM sessions WHERE id = ?", (99999,))
        assert row is None

    def test_query_returns_list(self, in_memory_db, session_id):
        rows = db.query("SELECT * FROM sessions WHERE id = ?", (session_id,))
        assert isinstance(rows, list)
        assert len(rows) == 1

    def test_update_modifies_row(self, in_memory_db, session_id):
        db.update("sessions", session_id, {"current_capital": 12000.0})
        row = db.query_one("SELECT current_capital FROM sessions WHERE id = ?", (session_id,))
        assert row["current_capital"] == 12000.0

    def test_now_iso_format(self, in_memory_db):
        ts = db.now_iso()
        # Should be parseable ISO 8601
        from datetime import datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None


class TestConstraints:
    def test_session_memory_unique_per_session(self, in_memory_db, session_id):
        """Only one memory row per session."""
        db.insert("session_memory", {
            "session_id": session_id,
            "content": "first",
            "updated_at": db.now_iso(),
            "cycle_number": 1,
        })
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.insert("session_memory", {
                "session_id": session_id,
                "content": "second",
                "updated_at": db.now_iso(),
                "cycle_number": 2,
            })

    def test_watchlist_soft_delete_pattern(self, in_memory_db, session_with_watchlist):
        """Watchlist uses removed_at for soft delete, not hard delete."""
        rows = db.query(
            "SELECT symbol FROM watchlist WHERE session_id = ? AND removed_at IS NULL",
            (session_with_watchlist,),
        )
        symbols = {r["symbol"] for r in rows}
        assert "RELIANCE" in symbols

        # Soft-delete RELIANCE
        row = db.query_one(
            "SELECT id FROM watchlist WHERE session_id = ? AND symbol = ?",
            (session_with_watchlist, "RELIANCE"),
        )
        db.update("watchlist", row["id"], {"removed_at": db.now_iso()})

        rows_after = db.query(
            "SELECT symbol FROM watchlist WHERE session_id = ? AND removed_at IS NULL",
            (session_with_watchlist,),
        )
        symbols_after = {r["symbol"] for r in rows_after}
        assert "RELIANCE" not in symbols_after
        assert "TCS" in symbols_after
