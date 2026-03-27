"""FII/DII institutional flow data tool.

Fetches daily FII (Foreign Institutional Investor) and DII (Domestic Institutional
Investor) net buy/sell data. FII selling = persistent headwind, DII buying = floor
support. Critical context for understanding why markets are moving.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade.tools import register_tool
from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

_tavily_client = None


def set_tavily_client(client):
    global _tavily_client
    _tavily_client = client


def _check_cache() -> dict | None:
    """Return cached FII/DII data if fresh (< 4 hours old)."""
    row = db.query_one(
        "SELECT summary, fetched_at FROM news_cache "
        "WHERE category = 'fiidii' AND key = 'daily' "
        "ORDER BY fetched_at DESC LIMIT 1",
    )
    if row:
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=_IST)
            age_hours = (datetime.now(_IST) - fetched).total_seconds() / 3600
            if age_hours < 4:
                return {"summary": row["summary"], "source": "cache"}
        except Exception:
            pass
    return None


def _write_cache(summary: str):
    """Cache the FII/DII summary."""
    now = db.now_iso()
    expires = (datetime.now(_IST) + timedelta(hours=4)).isoformat()
    db.insert("news_cache", {
        "category": "fiidii",
        "key": "daily",
        "summary": summary,
        "fetched_at": now,
        "expires_at": expires,
    })


@register_tool(
    name="get_fiidii_flows",
    description=(
        "Get today's FII and DII net buy/sell data for Indian equity markets. "
        "FII net selling = headwind (oversold bounces may fail due to persistent selling). "
        "DII net buying = floor support. Data is from previous trading day during market hours "
        "(NSE publishes after close). Cached for 4 hours."
    ),
    parameters={"properties": {}, "required": []},
)
def get_fiidii_flows() -> dict:
    # Check cache first
    cached = _check_cache()
    if cached:
        return cached

    if not _tavily_client:
        return {"summary": "FII/DII data unavailable (Tavily not configured)", "source": "error"}

    try:
        today = datetime.now(_IST).strftime("%Y-%m-%d")
        response = _tavily_client.search(
            query=f"FII DII activity data today India equity cash segment {today}",
            search_depth="basic",
            max_results=3,
            include_answer=True,
        )

        answer = response.get("answer", "")
        if not answer:
            results = response.get("results", [])
            answer = results[0].get("content", "")[:500] if results else "No FII/DII data found"

        summary = f"FII/DII flows ({today}): {answer}"
        _write_cache(summary)

        return {
            "summary": summary,
            "source": "tavily",
            "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    except Exception as e:
        logger.error(f"get_fiidii_flows failed: {e}")
        return {"summary": f"FII/DII data fetch failed: {e}", "source": "error"}
