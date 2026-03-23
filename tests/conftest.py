"""Shared fixtures for AAItrade tests.

Uses an in-memory SQLite DB for every test — no disk writes, no state leakage.
All external API calls (Kite, NewsAPI, Tavily, Anthropic) are patched at the
module level so tests run fully offline.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from aaitrade.config import (
    APIKeys,
    ExecutionMode,
    SessionConfig,
    TradingMode,
)


# ── In-memory DB fixture ───────────────────────────────────────────────────────

@pytest.fixture
def in_memory_db(monkeypatch):
    """Replace aaitrade.db with an in-memory SQLite connection for every test."""
    import aaitrade.db as db_module

    _conn = sqlite3.connect(":memory:")
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def fake_get_connection():
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise

    monkeypatch.setattr(db_module, "get_connection", fake_get_connection)

    # Create schema
    db_module.init_db()

    yield _conn

    _conn.close()


# ── Config fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def safe_config():
    return SessionConfig(
        execution_mode=ExecutionMode.PAPER,
        trading_mode=TradingMode.SAFE,
        starting_capital=10_000.0,
        total_days=10,
        watchlist_path="config/watchlist_seed.yaml",
        decision_interval_minutes=240,
        profit_reinvest_ratio=0.0,  # safe: secure all profit
    )


@pytest.fixture
def balanced_config():
    return SessionConfig(
        execution_mode=ExecutionMode.PAPER,
        trading_mode=TradingMode.BALANCED,
        starting_capital=20_000.0,
        total_days=10,
        watchlist_path="config/watchlist_seed.yaml",
        decision_interval_minutes=240,
        profit_reinvest_ratio=0.5,  # balanced: 50/50
    )


@pytest.fixture
def aggressive_config():
    return SessionConfig(
        execution_mode=ExecutionMode.PAPER,
        trading_mode=TradingMode.AGGRESSIVE,
        starting_capital=20_000.0,
        total_days=10,
        watchlist_path="config/watchlist_seed.yaml",
        decision_interval_minutes=240,
        profit_reinvest_ratio=1.0,  # aggressive: reinvest all
    )


@pytest.fixture
def api_keys():
    return APIKeys(
        anthropic="test-anthropic-key",
        kite_api_key="test-kite-key",
        kite_api_secret="test-kite-secret",
        kite_access_token="test-kite-token",
        newsapi="test-newsapi-key",
        tavily="test-tavily-key",
        telegram_bot_token="",
        telegram_chat_id="",
    )


# ── Session fixture (creates a session row and returns session_id) ─────────────

@pytest.fixture
def session_id(in_memory_db, balanced_config):
    import aaitrade.db as db
    sid = db.insert("sessions", {
        "name": "test-session",
        "execution_mode": balanced_config.execution_mode.value,
        "trading_mode": balanced_config.trading_mode.value,
        "starting_capital": balanced_config.starting_capital,
        "current_capital": balanced_config.starting_capital,
        "secured_profit": 0,
        "total_days": balanced_config.total_days,
        "current_day": 1,
        "watchlist_path": str(balanced_config.watchlist_path),
        "allow_watchlist_adjustment": 1,
        "profit_reinvest_ratio": balanced_config.profit_reinvest_ratio,
        "status": "active",
        "started_at": db.now_iso(),
        "config_json": "{}",
    })
    return sid


@pytest.fixture
def session_with_watchlist(in_memory_db, session_id):
    """Session with RELIANCE and TCS on watchlist."""
    import aaitrade.db as db
    for symbol, company, sector in [
        ("RELIANCE", "Reliance Industries", "Energy"),
        ("TCS", "Tata Consultancy Services", "IT"),
        ("HDFCBANK", "HDFC Bank", "Banking"),
    ]:
        db.insert("watchlist", {
            "session_id": session_id,
            "symbol": symbol,
            "company": company,
            "sector": sector,
            "notes": "",
            "added_at": db.now_iso(),
            "add_reason": "Seed",
        })
    return session_id


# ── Fake price helper ──────────────────────────────────────────────────────────

def make_price(symbol, price=1000.0, change_pct=1.0):
    return {
        "symbol": symbol,
        "last_price": price,
        "change_percent": change_pct,
        "volume": 1_000_000,
        "open": price * 0.99,
        "high": price * 1.01,
        "low": price * 0.98,
        "close": price,
        "timestamp": "2026-03-17T10:00:00",
    }
