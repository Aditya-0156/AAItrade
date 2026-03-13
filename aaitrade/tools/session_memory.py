"""Session memory tool — Claude's self-maintained persistent memory.

Claude reads this at the start of each cycle to recall its running context,
and writes it at the end of each cycle to record what happened and next goals.

Fixed size: 2400 characters (~600 tokens). Claude manages its own summarization.
One row per session — always overwritten (not appended).
"""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

_session_id: int | None = None

MAX_MEMORY_CHARS = 2400  # ~600 tokens — enforced hard limit


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


def _require_session():
    if _session_id is None:
        raise RuntimeError("session_memory tools used before set_session_id() called")


@register_tool(
    name="get_session_memory",
    description=(
        "Read your persistent session memory — a running narrative you maintain "
        "across cycles. Contains: stocks you're watching and why, recent decisions "
        "and outcomes, next-cycle goals, patterns you've noticed, stocks to avoid. "
        "Call this at the START of each cycle to recall your context from last cycle."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_session_memory() -> dict:
    _require_session()
    row = db.query_one(
        "SELECT content, updated_at, cycle_number FROM session_memory WHERE session_id = ?",
        (_session_id,),
    )
    if not row:
        return {
            "status": "empty",
            "content": "",
            "message": "No session memory yet — this is your first cycle. Write memory at end of this cycle.",
        }
    return {
        "status": "ok",
        "content": row["content"],
        "last_updated": row["updated_at"],
        "last_cycle": row["cycle_number"],
    }


@register_tool(
    name="update_session_memory",
    description=(
        "Overwrite your session memory with updated content. Call this at the END "
        "of each cycle to record: what you observed, decisions made and why, "
        "stocks you want to watch next cycle, your goals, and any patterns noticed. "
        "Max 2400 characters — if your content is longer, summarize it first. "
        "This replaces your previous memory entirely."
    ),
    parameters={
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "Your updated memory. Max 2400 characters (~600 tokens). "
                    "Suggested format: WATCHING: ..., RECENT DECISIONS: ..., "
                    "NEXT CYCLE GOALS: ..., AVOID: ..., PATTERNS: ..."
                ),
            },
        },
        "required": ["content"],
    },
)
def update_session_memory(content: str) -> dict:
    _require_session()

    if len(content) > MAX_MEMORY_CHARS:
        return {
            "error": (
                f"Memory too long: {len(content)} chars, limit is {MAX_MEMORY_CHARS}. "
                f"Please summarize your content and call again."
            ),
            "chars_over_limit": len(content) - MAX_MEMORY_CHARS,
        }

    # Get current cycle number from decisions table
    last = db.query_one(
        "SELECT MAX(cycle_number) as cn FROM decisions WHERE session_id = ?",
        (_session_id,),
    )
    cycle_num = last["cn"] if last and last["cn"] else 0

    existing = db.query_one(
        "SELECT id FROM session_memory WHERE session_id = ?",
        (_session_id,),
    )
    if existing:
        db.update("session_memory", existing["id"], {
            "content": content,
            "updated_at": db.now_iso(),
            "cycle_number": cycle_num,
        })
    else:
        db.insert("session_memory", {
            "session_id": _session_id,
            "content": content,
            "updated_at": db.now_iso(),
            "cycle_number": cycle_num,
        })

    return {
        "status": "saved",
        "chars_used": len(content),
        "chars_remaining": MAX_MEMORY_CHARS - len(content),
    }
