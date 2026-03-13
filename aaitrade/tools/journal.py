"""Trade journal tools — rationale records and thesis tracking.

Every BUY creates a rationale record. Every cycle reviews open positions
and updates thesis notes. This gives Claude continuity of reasoning
across decision cycles.
"""

from __future__ import annotations

import json
import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

_session_id: int | None = None


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


def _require_session():
    if _session_id is None:
        raise RuntimeError("Session not initialized.")


@register_tool(
    name="write_trade_rationale",
    description=(
        "Record the rationale for a BUY decision in the trade journal. "
        "Must be called every time you open a new position. Records why you "
        "bought, what news supported it, your thesis, and target/stop prices."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE symbol being bought",
            },
            "entry_price": {
                "type": "number",
                "description": "The price at which the position is being opened",
            },
            "reason": {
                "type": "string",
                "description": "2-4 sentences explaining why you are buying",
            },
            "news_cited": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of news headlines that support this decision",
            },
            "thesis": {
                "type": "string",
                "description": "Your key thesis — what needs to happen for this trade to work",
            },
            "target_price": {
                "type": "number",
                "description": "Target price for take-profit",
            },
            "stop_price": {
                "type": "number",
                "description": "Stop-loss price",
            },
        },
        "required": ["symbol", "entry_price", "reason", "thesis", "target_price", "stop_price"],
    },
)
def write_trade_rationale(
    symbol: str,
    entry_price: float,
    reason: str,
    thesis: str,
    target_price: float,
    stop_price: float,
    news_cited: list[str] | None = None,
) -> dict:
    _require_session()

    journal_id = db.insert("trade_journal", {
        "session_id": _session_id,
        "symbol": symbol,
        "entry_price": entry_price,
        "reason": reason,
        "news_cited": json.dumps(news_cited or []),
        "key_thesis": thesis,
        "target_price": target_price,
        "stop_price": stop_price,
        "status": "open",
        "opened_at": db.now_iso(),
    })

    return {
        "status": "recorded",
        "journal_id": journal_id,
        "symbol": symbol,
        "message": f"Trade rationale for {symbol} recorded successfully.",
    }


@register_tool(
    name="get_open_positions_with_rationale",
    description=(
        "Get all open positions with their full trade journal records — "
        "original rationale, thesis, target/stop, and all thesis update notes. "
        "Use this at the start of each cycle to review your positions."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_open_positions_with_rationale() -> dict:
    _require_session()

    journals = db.query(
        "SELECT id, symbol, entry_price, reason, news_cited, key_thesis, "
        "target_price, stop_price, opened_at "
        "FROM trade_journal WHERE session_id = ? AND status = 'open'",
        (_session_id,),
    )

    positions = []
    for j in journals:
        # Get thesis updates for this journal entry
        updates = db.query(
            "SELECT note, updated_at FROM thesis_updates "
            "WHERE journal_id = ? ORDER BY updated_at ASC",
            (j["id"],),
        )

        # Get current portfolio info
        portfolio_row = db.query_one(
            "SELECT quantity, avg_price FROM portfolio "
            "WHERE session_id = ? AND symbol = ?",
            (_session_id, j["symbol"]),
        )

        positions.append({
            "journal_id": j["id"],
            "symbol": j["symbol"],
            "entry_price": j["entry_price"],
            "quantity": portfolio_row["quantity"] if portfolio_row else 0,
            "reason": j["reason"],
            "news_cited": json.loads(j["news_cited"]) if j["news_cited"] else [],
            "key_thesis": j["key_thesis"],
            "target_price": j["target_price"],
            "stop_price": j["stop_price"],
            "opened_at": j["opened_at"],
            "thesis_updates": [
                {"note": u["note"], "date": u["updated_at"]}
                for u in updates
            ],
        })

    return {
        "open_positions": positions,
        "total": len(positions),
    }


@register_tool(
    name="update_thesis",
    description=(
        "Add a thesis update note for an open position. Call this each cycle "
        "after reviewing a position — record whether the original thesis "
        "is still valid, any new developments, or if conditions have changed."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE symbol of the open position",
            },
            "note": {
                "type": "string",
                "description": "Your thesis update — is the thesis intact, weakened, or broken?",
            },
        },
        "required": ["symbol", "note"],
    },
)
def update_thesis(symbol: str, note: str) -> dict:
    _require_session()

    # Find the open journal entry for this symbol
    journal = db.query_one(
        "SELECT id FROM trade_journal "
        "WHERE session_id = ? AND symbol = ? AND status = 'open'",
        (_session_id, symbol),
    )

    if not journal:
        return {"error": f"No open journal entry found for {symbol}"}

    db.insert("thesis_updates", {
        "journal_id": journal["id"],
        "session_id": _session_id,
        "symbol": symbol,
        "note": note,
        "updated_at": db.now_iso(),
    })

    return {
        "status": "updated",
        "symbol": symbol,
        "message": f"Thesis update recorded for {symbol}.",
    }


@register_tool(
    name="get_closed_trade_history",
    description=(
        "Get past closed trades with their original thesis and outcome — "
        "did the thesis play out? Useful for learning from past decisions."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE symbol (optional — omit to get all closed trades)",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5)",
            },
        },
        "required": [],
    },
)
def get_closed_trade_history(symbol: str | None = None, limit: int = 5) -> dict:
    _require_session()

    if symbol:
        journals = db.query(
            "SELECT symbol, entry_price, exit_price, reason, key_thesis, "
            "exit_reason, pnl, opened_at, closed_at "
            "FROM trade_journal WHERE session_id = ? AND symbol = ? AND status = 'closed' "
            "ORDER BY closed_at DESC LIMIT ?",
            (_session_id, symbol, limit),
        )
    else:
        journals = db.query(
            "SELECT symbol, entry_price, exit_price, reason, key_thesis, "
            "exit_reason, pnl, opened_at, closed_at "
            "FROM trade_journal WHERE session_id = ? AND status = 'closed' "
            "ORDER BY closed_at DESC LIMIT ?",
            (_session_id, limit),
        )

    return {
        "closed_trades": journals,
        "total": len(journals),
    }
