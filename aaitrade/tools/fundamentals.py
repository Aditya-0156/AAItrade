"""Basic fundamentals snapshot tool.

Returns P/E ratio, market cap, 52-week range, sector, and dividend yield
for an NSE stock. Helps Claude assess whether a stock's decline is
fundamental (earnings collapsed) or temporary (macro-driven selloff).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade.tools import register_tool
from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)


def _check_cache(symbol: str) -> dict | None:
    """Return cached fundamentals if fresh (< 24 hours)."""
    row = db.query_one(
        "SELECT summary, fetched_at FROM news_cache "
        "WHERE category = 'fundamentals' AND key = ? "
        "ORDER BY fetched_at DESC LIMIT 1",
        (symbol,),
    )
    if row:
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=_IST)
            age_hours = (datetime.now(_IST) - fetched).total_seconds() / 3600
            if age_hours < 24:
                import json
                return json.loads(row["summary"])
        except Exception:
            pass
    return None


def _write_cache(symbol: str, data: dict):
    """Cache fundamentals data."""
    import json
    now = db.now_iso()
    expires = (datetime.now(_IST) + timedelta(hours=24)).isoformat()
    db.insert("news_cache", {
        "category": "fundamentals",
        "key": symbol,
        "summary": json.dumps(data),
        "fetched_at": now,
        "expires_at": expires,
    })


@register_tool(
    name="get_fundamentals",
    description=(
        "Get a basic fundamentals snapshot for an NSE stock: P/E ratio, market cap, "
        "52-week range, sector, and dividend yield. Use this to check if a stock's "
        "decline is fundamental (earnings collapsed, P/E still high) or if it's genuinely "
        "cheap (low P/E, solid earnings, just beaten down by macro). Cached 24 hours."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol (e.g. 'RELIANCE', 'MARUTI')",
            },
        },
        "required": ["symbol"],
    },
)
def get_fundamentals(symbol: str) -> dict:
    # Check cache first
    cached = _check_cache(symbol)
    if cached:
        cached["source"] = "cache"
        return cached

    try:
        import yfinance as yf
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info

        if not info or info.get("quoteType") is None:
            return {"symbol": symbol, "error": "No data found", "source": "error"}

        def safe_get(key, default=None):
            val = info.get(key, default)
            if val is not None:
                try:
                    return round(float(val), 2)
                except (ValueError, TypeError):
                    return val
            return default

        # Market cap in crores (divide by 10M)
        raw_mcap = info.get("marketCap")
        mcap_cr = round(raw_mcap / 1e7, 0) if raw_mcap else None

        result = {
            "symbol": symbol,
            "name": info.get("shortName", symbol),
            "sector": info.get("sector", "-"),
            "industry": info.get("industry", "-"),
            "pe_ratio": safe_get("trailingPE"),
            "forward_pe": safe_get("forwardPE"),
            "market_cap_cr": mcap_cr,
            "book_value": safe_get("bookValue"),
            "dividend_yield_pct": round(safe_get("dividendYield", 0) * 100, 2) if info.get("dividendYield") else 0,
            "fifty_two_week_high": safe_get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": safe_get("fiftyTwoWeekLow"),
            "source": "yfinance",
        }

        _write_cache(symbol, result)
        return result

    except Exception as e:
        logger.error(f"get_fundamentals failed for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e), "source": "error"}
