"""Session memory tool — Claude's self-maintained persistent memory.

Claude reads this at the start of each cycle to recall its running context,
and writes it at the end of each cycle to record what happened and next goals.

Size: 2880 characters (~720 tokens). If Claude writes more, Claude Haiku
auto-compresses it — removing redundant/obsolete info while preserving all
critical trade data (prices, stops, targets, RSI, capital). Claude never
gets a rejection error.
One row per session — always overwritten (not appended).
"""

from __future__ import annotations

import logging

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

_session_id: int | None = None
_anthropic_client = None

MAX_MEMORY_CHARS = 2880  # 20% more than original 2400


def set_session_id(session_id: int):
    global _session_id
    _session_id = session_id


def set_anthropic_client(client):
    global _anthropic_client
    _anthropic_client = client


def _require_session():
    if _session_id is None:
        raise RuntimeError("session_memory tools used before set_session_id() called")


def _compress_with_haiku(content: str, max_chars: int) -> str:
    """Use Claude Haiku to compress session memory intelligently.

    Haiku understands trading context and will:
    - Preserve ALL critical data: entry prices, stop prices, target prices,
      RSI values, capital amounts, position sizes, settings
    - Remove: redundant explanations, repeated thesis points, outdated
      market observations that are no longer actionable
    - Output dense, information-rich memory under max_chars
    """
    if not _anthropic_client:
        logger.warning("Anthropic client not set for session memory compression — hard truncating")
        return content[:max_chars]

    try:
        response = _anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=(
                "You are a trading memory compressor. Your job is to compress session memory "
                "for an AI stock trader while preserving all critical information.\n\n"
                "ALWAYS KEEP (verbatim):\n"
                "- All entry prices, stop prices, target prices\n"
                "- All RSI values and MA values\n"
                "- All capital figures (deployed %, free cash, ₹ amounts)\n"
                "- All settings (stop_loss_pct, take_profit_pct, daily_loss_limit_pct)\n"
                "- All position sizes and symbols\n"
                "- Next-cycle goals and watchlist stocks\n\n"
                "REMOVE OR SHORTEN:\n"
                "- Repeated explanations of the same thesis\n"
                "- Verbose market descriptions already captured in numbers\n"
                "- Outdated observations (e.g. 'market was weak at 10am' with no ongoing relevance)\n"
                "- Filler phrases and redundant qualifiers\n\n"
                "Output ONLY the compressed memory text. No preamble. No explanation."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Compress this trading session memory to under {max_chars} characters. "
                    f"Current length: {len(content)} chars. Target: {max_chars} chars.\n\n"
                    f"{content}"
                ),
            }],
        )
        compressed = response.content[0].text.strip()
        if len(compressed) > max_chars:
            compressed = compressed[:max_chars]
        logger.info(f"Session memory compressed by Haiku: {len(content)} → {len(compressed)} chars")
        return compressed
    except Exception as e:
        logger.warning(f"Haiku compression failed: {e} — hard truncating")
        return content[:max_chars]


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
        "Max 2880 characters. If your content is longer it will be automatically "
        "compressed by AI — no need to manually shorten it. "
        "This replaces your previous memory entirely."
    ),
    parameters={
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "Your updated memory. Max 2880 characters (~720 tokens). "
                    "Suggested format: POSITIONS: ..., CAPITAL: ..., STRATEGY: ..., "
                    "WATCHLIST: ..., NEXT CYCLE GOALS: ..., PATTERNS: ..."
                ),
            },
        },
        "required": ["content"],
    },
)
def update_session_memory(content: str) -> dict:
    _require_session()

    if len(content) > MAX_MEMORY_CHARS:
        content = _compress_with_haiku(content, MAX_MEMORY_CHARS)

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
