"""Market data tools — prices, OHLCV, technical indicators.

Data source: Zerodha Kite Connect API.
Indicators computed locally via pandas-ta.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from aaitrade.tools import register_tool

logger = logging.getLogger(__name__)

# Kite client is injected at startup via set_kite_client()
_kite = None


def set_kite_client(kite):
    """Inject the authenticated KiteConnect instance."""
    global _kite
    _kite = kite


def _require_kite():
    if _kite is None:
        raise RuntimeError("Kite client not initialized. Call set_kite_client() first.")


# ── Tools ──────────────────────────────────────────────────────────────────────


@register_tool(
    name="get_current_price",
    description=(
        "Get the current live quote for an NSE stock. Returns last price, "
        "change percentage, volume, open, high, low, and close."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol (e.g. 'RELIANCE', 'INFY')",
            },
        },
        "required": ["symbol"],
    },
)
def get_current_price(symbol: str) -> dict:
    _require_kite()
    instrument = f"NSE:{symbol}"
    try:
        quote = _kite.quote(instrument)
        data = quote[instrument]
        ohlc = data.get("ohlc", {})
        return {
            "symbol": symbol,
            "last_price": data.get("last_price"),
            "change_percent": round(
                ((data.get("last_price", 0) - ohlc.get("close", 1)) / ohlc.get("close", 1)) * 100,
                2,
            ),
            "volume": data.get("volume"),
            "open": ohlc.get("open"),
            "high": ohlc.get("high"),
            "low": ohlc.get("low"),
            "close": ohlc.get("close"),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"get_current_price failed for {symbol}: {e}")
        return {"error": str(e), "symbol": symbol}


@register_tool(
    name="get_price_history",
    description=(
        "Get historical OHLCV candles for an NSE stock. Returns daily candles "
        "for the requested number of days."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol (e.g. 'RELIANCE')",
            },
            "days": {
                "type": "integer",
                "description": "Number of past trading days to fetch (max 60)",
            },
        },
        "required": ["symbol", "days"],
    },
)
def get_price_history(symbol: str, days: int = 30) -> dict:
    _require_kite()
    days = min(days, 60)  # cap at 60 to control token usage
    instrument = f"NSE:{symbol}"

    try:
        # Get instrument token
        instruments = _kite.instruments("NSE")
        token = None
        for inst in instruments:
            if inst["tradingsymbol"] == symbol:
                token = inst["instrument_token"]
                break

        if token is None:
            return {"error": f"Symbol {symbol} not found on NSE"}

        to_date = datetime.now()
        from_date = to_date - timedelta(days=days + 10)  # extra buffer for non-trading days

        candles = _kite.historical_data(
            instrument_token=token,
            from_date=from_date.strftime("%Y-%m-%d"),
            to_date=to_date.strftime("%Y-%m-%d"),
            interval="day",
        )

        # Return only the last N candles
        candles = candles[-days:]

        return {
            "symbol": symbol,
            "interval": "day",
            "candles": [
                {
                    "date": c["date"].strftime("%Y-%m-%d") if hasattr(c["date"], "strftime") else str(c["date"]),
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                }
                for c in candles
            ],
        }
    except Exception as e:
        logger.error(f"get_price_history failed for {symbol}: {e}")
        return {"error": str(e), "symbol": symbol}


@register_tool(
    name="get_indicators",
    description=(
        "Get pre-computed technical indicators for an NSE stock: RSI (14), "
        "20-day MA, 50-day MA, VWAP approximation, and volume ratio vs "
        "20-day average. Indicators are computed from recent price history."
    ),
    parameters={
        "properties": {
            "symbol": {
                "type": "string",
                "description": "NSE trading symbol (e.g. 'RELIANCE')",
            },
        },
        "required": ["symbol"],
    },
)
def get_indicators(symbol: str) -> dict:
    _require_kite()

    try:
        # Fetch 60 days of history to compute indicators
        history = get_price_history(symbol, days=60)
        if "error" in history:
            return history

        candles = history["candles"]
        if len(candles) < 20:
            return {"error": f"Insufficient data for {symbol} (need 20+ days)", "symbol": symbol}

        df = pd.DataFrame(candles)
        df["close"] = pd.to_numeric(df["close"])
        df["high"] = pd.to_numeric(df["high"])
        df["low"] = pd.to_numeric(df["low"])
        df["volume"] = pd.to_numeric(df["volume"])

        # RSI 14
        try:
            import pandas_ta as ta
            rsi_series = ta.rsi(df["close"], length=14)
            rsi = round(float(rsi_series.iloc[-1]), 1) if rsi_series is not None else None
        except ImportError:
            # Fallback: compute RSI manually
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi_val = 100 - (100 / (1 + rs))
            rsi = round(float(rsi_val.iloc[-1]), 1)

        # Moving averages
        ma_20 = round(float(df["close"].rolling(20).mean().iloc[-1]), 2)
        ma_50 = round(float(df["close"].rolling(50).mean().iloc[-1]), 2) if len(df) >= 50 else None

        # Current price
        current_price = float(df["close"].iloc[-1])

        # Volume ratio vs 20-day average
        avg_vol_20 = float(df["volume"].rolling(20).mean().iloc[-1])
        current_vol = float(df["volume"].iloc[-1])
        vol_ratio = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else None

        # RSI interpretation
        rsi_signal = "neutral"
        if rsi is not None:
            if rsi > 70:
                rsi_signal = "overbought"
            elif rsi < 30:
                rsi_signal = "oversold"

        # Price vs MAs
        ma_20_signal = "above" if current_price > ma_20 else "below"
        ma_50_signal = None
        if ma_50:
            ma_50_signal = "above" if current_price > ma_50 else "below"

        return {
            "symbol": symbol,
            "current_price": current_price,
            "rsi_14": rsi,
            "rsi_signal": rsi_signal,
            "ma_20": ma_20,
            "ma_20_position": ma_20_signal,
            "ma_50": ma_50,
            "ma_50_position": ma_50_signal,
            "volume_ratio_20d": vol_ratio,
            "volume_signal": (
                "high" if vol_ratio and vol_ratio > 1.5
                else "normal" if vol_ratio and vol_ratio > 0.7
                else "low"
            ),
        }
    except Exception as e:
        logger.error(f"get_indicators failed for {symbol}: {e}")
        return {"error": str(e), "symbol": symbol}


@register_tool(
    name="get_market_snapshot",
    description=(
        "Get a snapshot of the overall Indian market: Nifty 50, Bank Nifty "
        "levels and change, and market advance/decline breadth."
    ),
    parameters={
        "properties": {},
        "required": [],
    },
)
def get_market_snapshot() -> dict:
    _require_kite()

    try:
        indices = _kite.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"])

        nifty = indices.get("NSE:NIFTY 50", {})
        banknifty = indices.get("NSE:NIFTY BANK", {})

        nifty_ohlc = nifty.get("ohlc", {})
        banknifty_ohlc = banknifty.get("ohlc", {})

        nifty_change = 0
        if nifty_ohlc.get("close"):
            nifty_change = round(
                ((nifty.get("last_price", 0) - nifty_ohlc["close"]) / nifty_ohlc["close"]) * 100, 2
            )

        banknifty_change = 0
        if banknifty_ohlc.get("close"):
            banknifty_change = round(
                ((banknifty.get("last_price", 0) - banknifty_ohlc["close"]) / banknifty_ohlc["close"]) * 100, 2
            )

        return {
            "nifty_50": {
                "last_price": nifty.get("last_price"),
                "change_percent": nifty_change,
            },
            "bank_nifty": {
                "last_price": banknifty.get("last_price"),
                "change_percent": banknifty_change,
            },
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"get_market_snapshot failed: {e}")
        return {"error": str(e)}
