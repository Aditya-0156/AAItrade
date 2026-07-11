"""Market regime detection — RISK_ON / NEUTRAL / RISK_OFF, computed not prompted.

Claude shouldn't burn tokens deriving "is this a risk-off day?" from raw
numbers every cycle — Python scores it once and the briefing carries one line.

Signals (each adds to a risk score):
- India VIX level and 5-day slope
- Nifty vs its 20- and 50-day moving averages
- Overnight S&P 500 move
Score >= 4 → RISK_OFF, <= 1 → RISK_ON, else NEUTRAL.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60 * 60  # regime doesn't flip minute-to-minute
_cache: dict = {}
_cache_time: float = 0.0
_lock = threading.Lock()


def _compute() -> dict:
    import yfinance as yf

    score = 0
    reasons: list[str] = []

    # India VIX
    try:
        vix_hist = yf.Ticker("^INDIAVIX").history(period="10d")
        if not vix_hist.empty:
            vix = float(vix_hist["Close"].iloc[-1])
            vix_5d_ago = float(vix_hist["Close"].iloc[-6]) if len(vix_hist) >= 6 else vix
            rising = vix > vix_5d_ago * 1.08
            if vix > 20:
                score += 2
                reasons.append(f"VIX {vix:.1f} elevated{' & rising' if rising else ''}")
            elif vix > 16:
                score += 1
                reasons.append(f"VIX {vix:.1f}{' rising' if rising else ''}")
            else:
                reasons.append(f"VIX {vix:.1f} calm")
            if rising and vix > 16:
                score += 1
    except Exception as e:
        logger.warning(f"Regime: VIX fetch failed: {e}")

    # Nifty vs MA20 / MA50
    try:
        nifty = yf.Ticker("^NSEI").history(period="80d")
        if len(nifty) >= 50:
            closes = nifty["Close"]
            price = float(closes.iloc[-1])
            ma20 = float(closes.rolling(20).mean().iloc[-1])
            ma50 = float(closes.rolling(50).mean().iloc[-1])
            if price < ma20:
                score += 1
                reasons.append("Nifty below MA20")
            if price < ma50:
                score += 1
                reasons.append("Nifty below MA50")
            if price >= ma20 and price >= ma50:
                reasons.append("Nifty above both MAs")
    except Exception as e:
        logger.warning(f"Regime: Nifty fetch failed: {e}")

    # Overnight US close
    try:
        spx = yf.Ticker("^GSPC").history(period="5d")
        if len(spx) >= 2:
            last = float(spx["Close"].iloc[-1])
            prev = float(spx["Close"].iloc[-2])
            chg = (last - prev) / prev * 100
            if chg < -1.0:
                score += 1
                reasons.append(f"S&P {chg:+.1f}% overnight")
            elif chg > 1.0:
                score -= 1
                reasons.append(f"S&P {chg:+.1f}% overnight")
            else:
                reasons.append(f"S&P {chg:+.1f}%")
    except Exception as e:
        logger.warning(f"Regime: S&P fetch failed: {e}")

    if score >= 4:
        regime = "RISK_OFF"
        guidance = "size down 40%, prefer exits over entries, demand extra confirmation"
    elif score <= 1:
        regime = "RISK_ON"
        guidance = "normal sizing, dips are buyable"
    else:
        regime = "NEUTRAL"
        guidance = "normal rules, be selective"

    return {
        "regime": regime,
        "score": score,
        "reasons": reasons,
        "guidance": guidance,
        "computed_at": datetime.now(_IST).strftime("%H:%M IST"),
    }


def get_market_regime() -> dict:
    """Cached regime snapshot. Never raises — returns UNKNOWN on total failure."""
    global _cache, _cache_time
    with _lock:
        if _cache and time.monotonic() - _cache_time < _CACHE_TTL_SECONDS:
            return _cache
    try:
        result = _compute()
    except Exception as e:
        logger.error(f"Regime computation failed: {e}")
        result = {"regime": "UNKNOWN", "score": 0, "reasons": [str(e)], "guidance": "regime unavailable"}
    with _lock:
        _cache = result
        _cache_time = time.monotonic()
    return result


def format_regime_line(r: dict | None = None) -> str:
    """One briefing-ready line."""
    r = r or get_market_regime()
    return f"{r['regime']} ({'; '.join(r['reasons'][:4])}) → {r['guidance']}"
