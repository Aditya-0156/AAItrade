import os
import aiosqlite
from typing import AsyncGenerator

from pathlib import Path
_default_db = Path(__file__).resolve().parent.parent / "data" / "aaitrade.db"
DB_PATH = os.environ.get("AAITRADE_DB_PATH", str(_default_db))


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Yield a read-only async SQLite connection."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA query_only = ON")
        yield db


async def fetchall(db: aiosqlite.Connection, query: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT and return list of dicts."""
    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def fetchone(db: aiosqlite.Connection, query: str, params: tuple = ()) -> dict | None:
    """Execute a SELECT and return a single dict or None."""
    async with db.execute(query, params) as cursor:
        row = await cursor.fetchone()
        return dict(row) if row else None
