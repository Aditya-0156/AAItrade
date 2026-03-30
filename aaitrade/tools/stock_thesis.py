"""Persistent per-stock thesis log.

Tracks Claude's observations on any watchlist stock across sessions —
whether watching, holding, reviewing after a sale, or deciding to avoid.
Entries are date-stamped and stored in SQLite indefinitely.

Word limits are enforced here (not by asking Claude to summarise later,
which would cost an extra round-trip). Claude is told the limits upfront
in the system prompt so it writes concisely from the start.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from aaitrade import db
from aaitrade.tools import register_tool

_IST = timezone(timedelta(hours=5, minutes=30))

NOTE_WORD_LIMIT = 80       # per daily entry
SUMMARY_WORD_LIMIT = 200   # get_stock_thesis_summary soft cap (for reference in docs)


def _now_date() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


# ── update_stock_thesis ────────────────────────────────────────────────────────

@register_tool(
    name="update_stock_thesis",
    description=(
        "Append a dated observation to the persistent per-stock thesis log. "
        "Use this whenever you form or update a view on a watchlist stock — "
        "watching it, holding it, after selling, or deciding to avoid it. "
        "Entries survive across sessions so future-you can recall them. "
        f"HARD LIMIT: note must be {NOTE_WORD_LIMIT} words or fewer. "
        "Write only the key insight — signal, thesis state, risk factor. "
        "Notes longer than the limit are auto-truncated; write concisely first time."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE stock symbol (e.g. HDFCBANK)",
            },
            "note": {
                "type": "string",
                "description": (
                    f"Your observation. Hard limit: {NOTE_WORD_LIMIT} words. "
                    "Include: what you see, what you expect, what would change your mind."
                ),
            },
            "phase": {
                "type": "string",
                "enum": ["watching", "holding", "sold", "avoided"],
                "description": (
                    "watching = on radar, not held | "
                    "holding = currently in position | "
                    "sold = just exited | "
                    "avoided = considered but passed"
                ),
            },
        },
        "required": ["symbol", "note", "phase"],
    },
)
def update_stock_thesis(symbol: str, note: str, phase: str) -> dict:
    symbol = symbol.upper().strip()

    words = note.split()
    truncated = False
    if len(words) > NOTE_WORD_LIMIT:
        note = " ".join(words[:NOTE_WORD_LIMIT])
        truncated = True

    db.insert("stock_thesis_log", {
        "symbol": symbol,
        "date": _now_date(),
        "note": note,
        "phase": phase,
        "created_at": db.now_iso(),
    })

    result: dict = {"status": "saved", "symbol": symbol, "date": _now_date(), "phase": phase}
    if truncated:
        result["warning"] = (
            f"Note truncated to {NOTE_WORD_LIMIT} words. "
            "Be more concise — only the key insight."
        )
    return result


# ── get_stock_thesis ───────────────────────────────────────────────────────────

@register_tool(
    name="get_stock_thesis",
    description=(
        "Fetch dated thesis entries for a stock. "
        "Use before buying a stock you've watched or traded before — "
        "see what you thought last time and whether the thesis played out. "
        "Returns entries newest-first (reversed for reading)."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE stock symbol",
            },
            "from_date": {
                "type": "string",
                "description": "Return entries on/after this date (YYYY-MM-DD). Omit for last 30 days.",
            },
            "last_n": {
                "type": "integer",
                "description": "Return only the last N entries (default 10, max 30).",
            },
        },
        "required": ["symbol"],
    },
)
def get_stock_thesis(
    symbol: str,
    from_date: str | None = None,
    last_n: int = 10,
) -> dict:
    symbol = symbol.upper().strip()
    last_n = min(max(1, last_n), 30)

    if from_date:
        rows = db.query(
            "SELECT date, note, phase FROM stock_thesis_log "
            "WHERE symbol = ? AND date >= ? ORDER BY created_at DESC LIMIT ?",
            (symbol, from_date, last_n),
        )
    else:
        rows = db.query(
            "SELECT date, note, phase FROM stock_thesis_log "
            "WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
            (symbol, last_n),
        )

    if not rows:
        return {"symbol": symbol, "entries": [], "message": "No thesis history found."}

    # Return chronological order (oldest first)
    rows = list(reversed(rows))
    return {
        "symbol": symbol,
        "entries_returned": len(rows),
        "entries": [{"date": r["date"], "phase": r["phase"], "note": r["note"]} for r in rows],
    }


# ── get_stock_thesis_summary ───────────────────────────────────────────────────

@register_tool(
    name="get_stock_thesis_summary",
    description=(
        "Get a compact summary of your full thesis history for a stock. "
        "Shows first-seen date, phase timeline, total entries, and last 3 notes. "
        f"Capped at ~{SUMMARY_WORD_LIMIT} words. Use when history is long and "
        "you need the big picture, not every entry."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE stock symbol",
            },
        },
        "required": ["symbol"],
    },
)
def get_stock_thesis_summary(symbol: str) -> dict:
    symbol = symbol.upper().strip()

    all_rows = db.query(
        "SELECT date, note, phase FROM stock_thesis_log "
        "WHERE symbol = ? ORDER BY created_at ASC",
        (symbol,),
    )

    if not all_rows:
        return {"symbol": symbol, "message": "No thesis history found."}

    total = len(all_rows)
    first = all_rows[0]
    latest = all_rows[-1]

    phase_counts = Counter(r["phase"] for r in all_rows)
    phase_str = " | ".join(f"{k}: {v}" for k, v in phase_counts.most_common())

    recent_notes = [
        f"{r['date']} [{r['phase']}]: {r['note']}"
        for r in all_rows[-3:]
    ]

    return {
        "symbol": symbol,
        "total_entries": total,
        "first_seen": first["date"],
        "latest_entry": latest["date"],
        "phase_breakdown": phase_str,
        "last_3_notes": recent_notes,
    }
