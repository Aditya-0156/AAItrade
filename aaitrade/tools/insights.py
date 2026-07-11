"""Insight tools — Claude's durable, cross-session knowledge base.

Trade outcomes and prediction scores are recorded automatically. These tools
let Claude ADD generalizable insights (patterns it noticed working or failing)
and RECALL the full lesson history on demand. Insights are global — they
survive session resets and DB-scoped context, which makes them the most
valuable words in the database.
"""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)


@register_tool(
    name="save_insight",
    description=(
        "Save a durable trading insight that future cycles (and future "
        "sessions) will see. Use when you notice a REPEATABLE pattern, not a "
        "one-off event: 'oversold bounces fail when VIX > 20', 'PSU banks gap "
        "with FII flow reversals', 'my targets on pharma names were too "
        "greedy 3 times running'. One insight = one sentence, max 200 chars. "
        "Do NOT save routine trade notes (those are recorded automatically) "
        "or session plans (use update_session_memory)."
    ),
    parameters={
        "properties": {
            "insight": {
                "type": "string",
                "description": "The generalizable lesson, one sentence, max 200 chars",
            },
            "symbol": {
                "type": "string",
                "description": "Related symbol if stock-specific (optional)",
            },
        },
        "required": ["insight"],
    },
)
def save_insight(insight: str, symbol: str | None = None) -> dict:
    insight = insight.strip()[:200]
    if len(insight) < 15:
        return {"error": "Insight too short to be useful — state the pattern and its condition."}
    db.insert("lessons", {
        "session_id": None,  # global — survives across sessions
        "symbol": symbol,
        "category": "insight",
        "lesson": insight,
        "pnl": None,
        "created_at": db.now_iso(),
    })
    return {"status": "saved", "insight": insight}


@register_tool(
    name="get_lessons",
    description=(
        "Recall your accumulated trading knowledge: automatic post-trade "
        "records (what won, what lost, and why), your saved insights, and "
        "prediction scores. The 5 most recent already appear in your briefing "
        "— call this for deeper history, or filtered by symbol before trading "
        "a stock you've traded before."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Filter to one symbol (optional)",
            },
            "limit": {
                "type": "integer",
                "description": "Max lessons to return (default 15)",
            },
        },
        "required": [],
    },
)
def get_lessons(symbol: str | None = None, limit: int = 15) -> dict:
    limit = min(max(limit, 1), 40)
    if symbol:
        rows = db.query(
            "SELECT category, lesson, created_at FROM lessons "
            "WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        )
    else:
        rows = db.query(
            "SELECT category, lesson, created_at FROM lessons ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    return {
        "lessons": [
            f"[{r['category']} {r['created_at'][:10]}] {r['lesson']}" for r in rows
        ],
        "count": len(rows),
    }
