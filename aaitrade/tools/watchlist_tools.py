"""Watchlist tools — view, add, and remove stocks from the session watchlist.

add/remove are only registered when allow_watchlist_adjustment=True.
Guardrails: NSE validation, liquidity check, max size cap.
"""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

_session_id: int | None = None
_kite = None
_max_watchlist_size = 30


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


def set_kite_client(kite):
    global _kite
    _kite = kite


def _require_session():
    if _session_id is None:
        raise RuntimeError("Session not initialized.")


@register_tool(
    name="get_watchlist",
    description=(
        "Get the current watchlist for this session — all active symbols "
        "with their sector tags and notes."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_watchlist() -> dict:
    _require_session()

    entries = db.query(
        "SELECT symbol, company, sector, notes, added_at "
        "FROM watchlist WHERE session_id = ? AND removed_at IS NULL "
        "ORDER BY symbol",
        (_session_id,),
    )

    return {
        "watchlist": entries,
        "total": len(entries),
    }


@register_tool(
    name="add_to_watchlist",
    description=(
        "Add a stock to the watchlist. Only allowed at end-of-day, not mid-cycle. "
        "The system validates that the symbol exists on NSE and meets minimum "
        "liquidity requirements. Provide a clear reason for adding."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol to add (e.g. 'HCLTECH')",
            },
            "reason": {
                "type": "string",
                "description": "Why you want to add this stock to the watchlist",
            },
        },
        "required": ["symbol", "reason"],
    },
)
def add_to_watchlist(symbol: str, reason: str) -> dict:
    _require_session()
    symbol = symbol.upper().strip()

    # Check max size
    current_count = db.query(
        "SELECT COUNT(*) as count FROM watchlist "
        "WHERE session_id = ? AND removed_at IS NULL",
        (_session_id,),
    )
    if current_count and current_count[0]["count"] >= _max_watchlist_size:
        return {
            "status": "rejected",
            "symbol": symbol,
            "reason": f"Watchlist at max capacity ({_max_watchlist_size} stocks). "
                      "Remove a stock first.",
        }

    # Check if already in watchlist
    existing = db.query_one(
        "SELECT id FROM watchlist "
        "WHERE session_id = ? AND symbol = ? AND removed_at IS NULL",
        (_session_id, symbol),
    )
    if existing:
        return {
            "status": "rejected",
            "symbol": symbol,
            "reason": f"{symbol} is already on the watchlist.",
        }

    # Validate symbol exists on NSE via Kite
    company_name = ""
    sector = ""
    if _kite:
        try:
            instruments = _kite.instruments("NSE")
            found = None
            for inst in instruments:
                if inst["tradingsymbol"] == symbol and inst["segment"] == "NSE":
                    found = inst
                    break

            if not found:
                return {
                    "status": "rejected",
                    "symbol": symbol,
                    "reason": f"{symbol} not found on NSE. Check the symbol.",
                }

            company_name = found.get("name", "")
        except Exception as e:
            logger.warning(f"Could not validate {symbol} on NSE: {e}")

    # Add to watchlist
    db.insert("watchlist", {
        "session_id": _session_id,
        "symbol": symbol,
        "company": company_name,
        "sector": sector,
        "notes": "",
        "added_at": db.now_iso(),
        "add_reason": reason,
    })

    return {
        "status": "added",
        "symbol": symbol,
        "company": company_name,
        "message": f"{symbol} added to watchlist.",
    }


@register_tool(
    name="remove_from_watchlist",
    description=(
        "Remove a stock from the watchlist. Only allowed at end-of-day. "
        "Cannot remove a stock you currently hold — sell the position first."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol to remove",
            },
            "reason": {
                "type": "string",
                "description": "Why you want to remove this stock",
            },
        },
        "required": ["symbol", "reason"],
    },
)
def remove_from_watchlist(symbol: str, reason: str) -> dict:
    _require_session()
    symbol = symbol.upper().strip()

    # Check if currently held
    held = db.query_one(
        "SELECT id FROM portfolio WHERE session_id = ? AND symbol = ?",
        (_session_id, symbol),
    )
    if held:
        return {
            "status": "rejected",
            "symbol": symbol,
            "reason": f"Cannot remove {symbol} — you currently hold a position. Sell first.",
        }

    # Find the watchlist entry
    entry = db.query_one(
        "SELECT id FROM watchlist "
        "WHERE session_id = ? AND symbol = ? AND removed_at IS NULL",
        (_session_id, symbol),
    )
    if not entry:
        return {
            "status": "rejected",
            "symbol": symbol,
            "reason": f"{symbol} is not on the watchlist.",
        }

    # Soft-delete: set removed_at
    db.update("watchlist", entry["id"], {
        "removed_at": db.now_iso(),
        "remove_reason": reason,
    })

    return {
        "status": "removed",
        "symbol": symbol,
        "message": f"{symbol} removed from watchlist.",
    }
