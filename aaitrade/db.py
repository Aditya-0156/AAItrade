"""SQLite database layer for AAItrade.

All persistent state — sessions, trades, portfolio, journal, news cache,
decisions — is stored here. Phase 1 uses SQLite; schema is designed to
migrate cleanly to Postgres later.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "aaitrade.db"


def _ensure_dir():
    DB_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_connection():
    """Yield a SQLite connection with row_factory set."""
    _ensure_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_mode  TEXT NOT NULL,          -- paper / live
    trading_mode    TEXT NOT NULL,          -- safe / balanced / aggressive
    starting_capital REAL NOT NULL,
    current_capital REAL NOT NULL,
    secured_profit  REAL NOT NULL DEFAULT 0,
    total_days      INTEGER NOT NULL,
    current_day     INTEGER NOT NULL DEFAULT 1,
    watchlist_path  TEXT NOT NULL,
    allow_watchlist_adjustment INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'active',   -- active / halted / completed
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    config_json     TEXT                    -- full SessionConfig serialized
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    symbol          TEXT NOT NULL,
    action          TEXT NOT NULL,          -- BUY / SELL
    quantity        INTEGER NOT NULL,
    price           REAL NOT NULL,
    stop_loss_price REAL,
    take_profit_price REAL,
    reason          TEXT,
    confidence      TEXT,
    executed_at     TEXT NOT NULL,
    pnl             REAL                    -- filled on SELL
);

CREATE TABLE IF NOT EXISTS portfolio (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    symbol          TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    avg_price       REAL NOT NULL,
    stop_loss_price REAL,
    take_profit_price REAL,
    opened_at       TEXT NOT NULL,
    UNIQUE(session_id, symbol)
);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    cycle_number    INTEGER NOT NULL,
    action          TEXT NOT NULL,
    symbol          TEXT,
    quantity        INTEGER,
    reason          TEXT,
    confidence      TEXT,
    flags           TEXT,                   -- JSON array
    raw_json        TEXT NOT NULL,          -- full Claude output
    decided_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    symbol          TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    reason          TEXT NOT NULL,
    news_cited      TEXT,                   -- JSON array of strings
    key_thesis      TEXT NOT NULL,
    target_price    REAL,
    stop_price      REAL,
    status          TEXT NOT NULL DEFAULT 'open',  -- open / closed
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    exit_reason     TEXT,
    exit_price      REAL,
    pnl             REAL
);

CREATE TABLE IF NOT EXISTS thesis_updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_id      INTEGER NOT NULL REFERENCES trade_journal(id),
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    symbol          TEXT NOT NULL,
    note            TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,          -- stock / sector / macro
    key             TEXT NOT NULL,          -- symbol, sector name, or 'macro'
    summary         TEXT NOT NULL,
    source          TEXT,
    fetched_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    query           TEXT NOT NULL,
    result          TEXT NOT NULL,
    searched_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    day_number      INTEGER NOT NULL,
    date            TEXT NOT NULL,
    starting_capital REAL NOT NULL,
    ending_capital  REAL NOT NULL,
    secured_profit  REAL NOT NULL,
    trades_made     INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    total_pnl       REAL NOT NULL DEFAULT 0,
    summary_text    TEXT,
    UNIQUE(session_id, day_number)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    cycle_number    INTEGER NOT NULL,
    tool_name       TEXT NOT NULL,
    parameters      TEXT,                   -- JSON
    result_summary  TEXT,
    called_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    symbol          TEXT NOT NULL,
    company         TEXT,
    sector          TEXT,
    notes           TEXT,
    added_at        TEXT NOT NULL,
    removed_at      TEXT,                   -- null if still active
    add_reason      TEXT,
    remove_reason   TEXT,
    UNIQUE(session_id, symbol, added_at)
);
"""


# ── Helper Functions ───────────────────────────────────────────────────────────


def insert(table: str, data: dict[str, Any]) -> int:
    """Insert a row and return its id."""
    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    with get_connection() as conn:
        cursor = conn.execute(sql, list(data.values()))
        return cursor.lastrowid


def update(table: str, row_id: int, data: dict[str, Any]):
    """Update a row by id."""
    sets = ", ".join(f"{k} = ?" for k in data.keys())
    sql = f"UPDATE {table} SET {sets} WHERE id = ?"
    with get_connection() as conn:
        conn.execute(sql, [*data.values(), row_id])


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return list of dicts."""
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    """Run a SELECT and return first row as dict, or None."""
    with get_connection() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def now_iso() -> str:
    """Return current time as ISO 8601 string."""
    return datetime.now().isoformat()
