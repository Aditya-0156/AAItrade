"""News tools — stock, sector, and macro news fetching + summarization.

Source: NewsAPI.org
Summarization: Claude Haiku (cheap, fast) for long articles.
Caching: stock news 1h, sector news 2h, macro news all day.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aaitrade.tools import register_tool
from aaitrade import db

logger = logging.getLogger(__name__)

# NewsAPI client injected at startup
_newsapi = None
# Anthropic client for summarization
_anthropic_client = None

# Rate-limit tracking: NewsAPI free tier = 100 req/day; stay 10 below as buffer
_newsapi_call_count = 0
_newsapi_call_date: str | None = None
_NEWSAPI_DAILY_CAP = 90


def set_newsapi_client(client):
    global _newsapi
    _newsapi = client


def set_anthropic_client(client):
    global _anthropic_client
    _anthropic_client = client


def _newsapi_check_and_count() -> bool:
    """Return True if a NewsAPI call is allowed; False if daily cap is reached.

    Resets the counter automatically at the start of each new calendar day.
    """
    global _newsapi_call_count, _newsapi_call_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _newsapi_call_date != today:
        _newsapi_call_count = 0
        _newsapi_call_date = today
    if _newsapi_call_count >= _NEWSAPI_DAILY_CAP:
        return False
    _newsapi_call_count += 1
    return True


def _summarize_articles(articles: list[dict], context: str = "") -> str:
    """Return concise article titles+descriptions — no LLM call needed."""
    if not articles:
        return "No relevant news found."

    lines = []
    for a in articles[:5]:
        title = a.get("title", "No title")
        desc = a.get("description", "") or ""
        # Truncate description to 80 chars to keep input small
        if desc and len(desc) > 80:
            desc = desc[:80] + "..."
        lines.append(f"- {title}" + (f" — {desc}" if desc else ""))

    return "\n".join(lines)


def _check_cache(category: str, key: str) -> str | None:
    """Check if cached news exists and is still valid."""
    row = db.query_one(
        "SELECT summary, expires_at FROM news_cache "
        "WHERE category = ? AND key = ? ORDER BY fetched_at DESC LIMIT 1",
        (category, key),
    )
    if row and row["expires_at"] > datetime.now().isoformat():
        return row["summary"]
    return None


def _write_cache(category: str, key: str, summary: str, hours: int):
    """Write summarized news to cache."""
    db.insert("news_cache", {
        "category": category,
        "key": key,
        "summary": summary,
        "source": "newsapi",
        "fetched_at": db.now_iso(),
        "expires_at": (datetime.now() + timedelta(hours=hours)).isoformat(),
    })


# ── Tools ──────────────────────────────────────────────────────────────────────


@register_tool(
    name="get_stock_news",
    description=(
        "Get recent news for a specific NSE stock, summarized into key bullet points. "
        "Results are cached for 1 hour."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol (e.g. 'RELIANCE', 'INFY')",
            },
            "hours": {
                "type": "integer",
                "description": "Look back this many hours for news (default 24, max 72)",
            },
        },
        "required": ["symbol"],
    },
)
def get_stock_news(symbol: str, hours: int = 24) -> dict:
    hours = min(hours, 72)

    # Check cache first
    cached = _check_cache("stock", symbol)
    if cached:
        return {"symbol": symbol, "summary": cached, "source": "cache"}

    if not _newsapi:
        return {"symbol": symbol, "summary": "NewsAPI not configured.", "source": "error"}

    if not _newsapi_check_and_count():
        return {
            "symbol": symbol,
            "summary": (
                "NewsAPI daily call limit reached. "
                "Use the web search tool (search_web) if you need current news for this stock."
            ),
            "source": "rate_limited",
        }

    try:
        from_date = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        result = _newsapi.get_everything(
            q=f'"{symbol}" OR "{_symbol_to_company(symbol)}"',
            from_param=from_date,
            language="en",
            sort_by="relevancy",
            page_size=5,
        )
        articles = result.get("articles", [])
        summary = _summarize_articles(articles, context=f"Stock: {symbol}")
        _write_cache("stock", symbol, summary, hours=1)

        return {"symbol": symbol, "summary": summary, "articles_found": len(articles)}
    except Exception as e:
        logger.error(f"get_stock_news failed for {symbol}: {e}")
        return {"symbol": symbol, "summary": f"News fetch failed: {e}", "source": "error"}


@register_tool(
    name="get_sector_news",
    description=(
        "Get recent news for a market sector (e.g. 'banking', 'IT', 'pharma'), "
        "summarized into key bullet points. Results cached for 2 hours."
    ),
    parameters={
        "properties": {
            "sector": {
                "type": "string",
                "description": "Sector name (e.g. 'banking', 'IT', 'pharma', 'auto', 'energy')",
            },
        },
        "required": ["sector"],
    },
)
def get_sector_news(sector: str) -> dict:
    cached = _check_cache("sector", sector)
    if cached:
        return {"sector": sector, "summary": cached, "source": "cache"}

    if not _newsapi:
        return {"sector": sector, "summary": "NewsAPI not configured.", "source": "error"}

    if not _newsapi_check_and_count():
        return {
            "sector": sector,
            "summary": "NewsAPI daily call limit reached. Use search_web for sector news if needed.",
            "source": "rate_limited",
        }

    try:
        result = _newsapi.get_everything(
            q=f"India {sector} sector stocks market",
            from_param=(datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S"),
            language="en",
            sort_by="relevancy",
            page_size=5,
        )
        articles = result.get("articles", [])
        summary = _summarize_articles(articles, context=f"Sector: {sector}")
        _write_cache("sector", sector, summary, hours=2)

        return {"sector": sector, "summary": summary, "articles_found": len(articles)}
    except Exception as e:
        logger.error(f"get_sector_news failed for {sector}: {e}")
        return {"sector": sector, "summary": f"News fetch failed: {e}", "source": "error"}


@register_tool(
    name="get_macro_news",
    description=(
        "Get today's macro/global news summary — geopolitics, central bank decisions, "
        "commodity prices, tariffs, major economic events. Pre-fetched at market open "
        "and cached all day. You should NOT call this tool — it is already included "
        "in your briefing."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_macro_news() -> dict:
    cached = _check_cache("macro", "macro")
    if cached:
        return {"summary": cached, "source": "cache"}

    if not _newsapi:
        return {"summary": "NewsAPI not configured.", "source": "error"}

    if not _newsapi_check_and_count():
        return {
            "summary": "NewsAPI daily call limit reached. Macro news unavailable from this source.",
            "source": "rate_limited",
        }

    try:
        result = _newsapi.get_top_headlines(
            category="business",
            language="en",
            page_size=10,
        )
        articles = result.get("articles", [])
        summary = _summarize_articles(
            articles,
            context="Focus on: geopolitics, central bank policy, tariffs, commodities, "
                    "and events that could impact Indian stock markets.",
        )
        _write_cache("macro", "macro", summary, hours=12)

        return {"summary": summary, "articles_found": len(articles)}
    except Exception as e:
        logger.error(f"get_macro_news failed: {e}")
        return {"summary": f"Macro news fetch failed: {e}", "source": "error"}


# ── Helpers ────────────────────────────────────────────────────────────────────

# Simple symbol → company name mapping for better news search
_COMPANY_MAP = {
    "RELIANCE": "Reliance Industries",
    "HDFCBANK": "HDFC Bank",
    "ICICIBANK": "ICICI Bank",
    "SBIN": "State Bank of India",
    "INFY": "Infosys",
    "TCS": "Tata Consultancy",
    "WIPRO": "Wipro",
    "TATAMOTORS": "Tata Motors",
    "MARUTI": "Maruti Suzuki",
    "SUNPHARMA": "Sun Pharma",
    "DRREDDY": "Dr Reddy",
    "TITAN": "Titan Company",
    "ASIANPAINT": "Asian Paints",
    "BAJFINANCE": "Bajaj Finance",
    "HINDUNILVR": "Hindustan Unilever",
    "NTPC": "NTPC",
    "ONGC": "ONGC",
    "TATASTEEL": "Tata Steel",
    "ADANIPORTS": "Adani Ports",
    "BHARTIARTL": "Bharti Airtel",
    "ZOMATO": "Zomato",
    "SWIGGY": "Swiggy",
    "PAYTM": "Paytm",
    "NYKAA": "Nykaa",
    "POLICYBZR": "Policybazaar",
    "DELHIVERY": "Delhivery",
    "OLAELEC": "Ola Electric",
    "IXIGO": "Ixigo",
    "NAZARA": "Nazara Technologies",
    "HONASA": "Mamaearth",
}


def _symbol_to_company(symbol: str) -> str:
    return _COMPANY_MAP.get(symbol, symbol)
