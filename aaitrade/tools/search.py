"""Web search tool — free-form search via Tavily API.

Gives Claude the ability to search for anything not covered by news tools.
Max 2 calls per decision cycle (enforced by system prompt + session manager).
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


@register_tool(
    name="search_web",
    description=(
        "Search the web for any information relevant to trading decisions. "
        "Returns a clean, summarized result. Use sparingly — max 2 calls per "
        "decision cycle. Best for specific queries like 'Trump tariff India "
        "impact on IT sector' or 'Infosys Q3 2025 guidance details'."
    ),
    parameters={
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query — be specific for best results",
            },
        },
        "required": ["query"],
    },
)
def search_web(query: str) -> dict:
    if not _tavily_client:
        return {"query": query, "result": "Tavily search not configured.", "source": "error"}

    try:
        response = _tavily_client.search(
            query=query,
            search_depth="basic",
            max_results=3,
            include_answer=True,
        )

        answer = response.get("answer", "")
        results = response.get("results", [])

        # Build concise summary from results
        sources = []
        for r in results[:3]:
            sources.append({
                "title": r.get("title", ""),
                "snippet": r.get("content", "")[:200],
                "url": r.get("url", ""),
            })

        # Cache the search
        db.insert("search_cache", {
            "query": query,
            "result": answer or str(sources),
            "searched_at": db.now_iso(),
        })

        return {
            "query": query,
            "answer": answer,
            "sources": sources,
            "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
        }
    except Exception as e:
        logger.error(f"search_web failed for query '{query}': {e}")
        return {"query": query, "result": f"Search failed: {e}", "source": "error"}
