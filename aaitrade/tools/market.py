"""Market data tools — prices, OHLCV, technical indicators.

Data source: Yahoo Finance (free, no API key) for paper trading.
Zerodha Kite Connect for live trading (injected at runtime).

NSE symbols are automatically suffixed with .NS for Yahoo Finance.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

import pandas as pd

_IST = timezone(timedelta(hours=5, minutes=30))

from aaitrade.tools import register_tool

logger = logging.getLogger(__name__)

# Global lock for all Kite API calls (thread-safe across sessions)
_kite_lock = threading.Lock()

# Kite client is injected at startup via set_kite_client() — used for live mode
_kite = None
# Data source: "yfinance" (default/free) or "kite" (live trading)
_data_source = "yfinance"
# Instrument token cache: symbol -> token, built once at startup
_instrument_token_cache: dict[str, int] = {}


def set_kite_client(kite):
    """Inject the authenticated KiteConnect instance. Switches data source to Kite."""
    global _kite, _data_source, _instrument_token_cache
    _kite = kite
    _data_source = "kite"
    # Download all NSE instruments once and cache symbol -> token
    try:
        instruments = kite.instruments("NSE")
        _instrument_token_cache = {
            inst["tradingsymbol"]: inst["instrument_token"]
            for inst in instruments
            if inst.get("segment") == "NSE"
        }
        logger.info(f"Market data source: Kite Connect ({len(_instrument_token_cache)} NSE instruments cached)")
    except Exception as e:
        logger.warning(f"Could not pre-cache NSE instruments: {e}. Will fetch on demand.")


def set_data_source(source: str):
    """Manually set data source ('yfinance' or 'kite')."""
    global _data_source
    _data_source = source


def _yf_symbol(symbol: str) -> str:
    """Convert NSE symbol to Yahoo Finance format (append .NS)."""
    if not symbol.endswith(".NS"):
        return f"{symbol}.NS"
    return symbol


# ── Yahoo Finance helpers ─────────────────────────────────────────────────


def _yf_get_quote(symbol: str) -> dict:
    """Get current quote via yfinance."""
    import yfinance as yf
    ticker = yf.Ticker(_yf_symbol(symbol))
    info = ticker.fast_info
    hist = ticker.history(period="2d")

    if hist.empty:
        return {"error": f"No data found for {symbol}"}

    last_price = float(hist["Close"].iloc[-1])
    prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else last_price

    return {
        "symbol": symbol,
        "last_price": round(last_price, 2),
        "change_percent": round(((last_price - prev_close) / prev_close) * 100, 2),
        "volume": int(hist["Volume"].iloc[-1]),
        "open": round(float(hist["Open"].iloc[-1]), 2),
        "high": round(float(hist["High"].iloc[-1]), 2),
        "low": round(float(hist["Low"].iloc[-1]), 2),
        "close": round(float(prev_close), 2),
        "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _yf_get_history(symbol: str, days: int) -> dict:
    """Get historical OHLCV via yfinance."""
    import yfinance as yf
    ticker = yf.Ticker(_yf_symbol(symbol))
    # Fetch extra days to account for weekends/holidays
    hist = ticker.history(period=f"{days + 15}d")

    if hist.empty:
        return {"error": f"No data found for {symbol}"}

    hist = hist.tail(days)

    return {
        "symbol": symbol,
        "interval": "day",
        "candles": [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            }
            for idx, row in hist.iterrows()
        ],
    }


def _yf_market_snapshot() -> dict:
    """Get Nifty 50 and Bank Nifty via yfinance."""
    import yfinance as yf

    result = {}
    for name, yf_sym in [("nifty_50", "^NSEI"), ("bank_nifty", "^NSEBANK")]:
        try:
            ticker = yf.Ticker(yf_sym)
            hist = ticker.history(period="2d")
            if not hist.empty:
                last = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else last
                result[name] = {
                    "last_price": round(last, 2),
                    "change_percent": round(((last - prev) / prev) * 100, 2),
                }
            else:
                result[name] = {"error": "No data"}
        except Exception as e:
            result[name] = {"error": str(e)}

    result["timestamp"] = datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S")
    result["source"] = "yahoo_finance"
    return result


# ── Kite helpers ──────────────────────────────────────────────────────────


def _kite_get_quote(symbol: str) -> dict:
    """Get current quote via Kite."""
    instrument = f"NSE:{symbol}"
    try:
        quote = _kite.quote(instrument)
    except Exception as e:
        return {"error": f"Kite quote failed for {symbol}: {e}", "symbol": symbol}
    if instrument not in quote:
        return {"error": f"Symbol {symbol} not found in Kite response", "symbol": symbol}
    data = quote[instrument]
    ohlc = data.get("ohlc", {})
    close = ohlc.get("close", 0) or 1  # Avoid division by zero
    return {
        "symbol": symbol,
        "last_price": data.get("last_price"),
        "change_percent": round(
            ((data.get("last_price", 0) - close) / close) * 100, 2,
        ),
        "volume": data.get("volume"),
        "open": ohlc.get("open"),
        "high": ohlc.get("high"),
        "low": ohlc.get("low"),
        "close": ohlc.get("close"),
        "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _kite_get_history(symbol: str, days: int) -> dict:
    """Get historical OHLCV via Kite."""
    # Use cached token; fall back to live fetch if cache missed
    token = _instrument_token_cache.get(symbol)
    if token is None:
        try:
            instruments = _kite.instruments("NSE")
            for inst in instruments:
                if inst["tradingsymbol"] == symbol:
                    token = inst["instrument_token"]
                    _instrument_token_cache[symbol] = token
                    break
        except Exception as e:
            return {"error": f"Could not fetch instrument list: {e}"}

    if token is None:
        return {"error": f"Symbol {symbol} not found on NSE"}

    to_date = datetime.now(_IST)
    from_date = to_date - timedelta(days=days + 10)

    try:
        candles = _kite.historical_data(
            instrument_token=token,
            from_date=from_date.strftime("%Y-%m-%d"),
            to_date=to_date.strftime("%Y-%m-%d"),
            interval="day",
        )
    except Exception as e:
        return {"error": f"Kite historical data failed for {symbol}: {e}"}

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


def _kite_market_snapshot() -> dict:
    """Get Nifty 50 and Bank Nifty via Kite."""
    try:
        indices = _kite.quote(["NSE:NIFTY 50", "NSE:NIFTY BANK"])
    except Exception as e:
        return {"error": f"Kite market snapshot failed: {e}"}
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
        "nifty_50": {"last_price": nifty.get("last_price"), "change_percent": nifty_change},
        "bank_nifty": {"last_price": banknifty.get("last_price"), "change_percent": banknifty_change},
        "timestamp": datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "kite",
    }


# ── Tools ─────────────────────────────────────────────────────────────────


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
    try:
        if _data_source == "kite" and _kite:
            return _kite_get_quote(symbol)
        return _yf_get_quote(symbol)
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
    days = min(days, 60)
    try:
        if _data_source == "kite" and _kite:
            return _kite_get_history(symbol, days)
        return _yf_get_history(symbol, days)
    except Exception as e:
        logger.error(f"get_price_history failed for {symbol}: {e}")
        return {"error": str(e), "symbol": symbol}


def _compute_indicators_one(symbol: str) -> dict:
    """Compute indicators for a single symbol. Returns a dict or error dict."""
    try:
        history = get_price_history(symbol, days=60)
        if "error" in history:
            return {"symbol": symbol, "error": history["error"]}

        candles = history["candles"]
        if len(candles) < 20:
            return {"symbol": symbol, "error": f"insufficient data ({len(candles)} days)"}

        df = pd.DataFrame(candles)
        df["close"] = pd.to_numeric(df["close"])
        df["volume"] = pd.to_numeric(df["volume"])

        # RSI 14
        try:
            import pandas_ta as ta
            rsi_series = ta.rsi(df["close"], length=14)
            rsi = round(float(rsi_series.iloc[-1]), 1) if rsi_series is not None else None
        except ImportError:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi = round(float((100 - (100 / (1 + rs))).iloc[-1]), 1)

        ma_20 = round(float(df["close"].rolling(20).mean().iloc[-1]), 2)
        ma_50 = round(float(df["close"].rolling(50).mean().iloc[-1]), 2) if len(df) >= 50 else None
        price = round(float(df["close"].iloc[-1]), 2)

        avg_vol = float(df["volume"].rolling(20).mean().iloc[-1])
        vol_ratio = round(float(df["volume"].iloc[-1]) / avg_vol, 2) if avg_vol > 0 else None

        if rsi is None:       rsi_sig = "?"
        elif rsi > 70:        rsi_sig = "overbought"
        elif rsi < 30:        rsi_sig = "oversold"
        else:                 rsi_sig = "neutral"

        ma20_pos = "above" if price > ma_20 else "below"
        ma50_pos = ("above" if price > ma_50 else "below") if ma_50 else "-"

        vol_sig = "high" if vol_ratio and vol_ratio > 1.5 else "normal" if vol_ratio and vol_ratio > 0.7 else "low"

        return {
            "symbol": symbol, "price": price,
            "rsi": rsi, "rsi_sig": rsi_sig,
            "ma20": ma_20, "ma20_pos": ma20_pos,
            "ma50": ma_50 or "-", "ma50_pos": ma50_pos,
            "vol_ratio": vol_ratio, "vol_sig": vol_sig,
        }
    except Exception as e:
        logger.error(f"get_indicators failed for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


@register_tool(
    name="get_indicators",
    description=(
        "Get technical indicators for up to 5 NSE stocks in one call: RSI(14), "
        "20-day MA, 50-day MA, and volume ratio vs 20-day average. "
        "Returns a compact table — one row per symbol. "
        "Always batch multiple symbols into a single call instead of calling one at a time."
    ),
    parameters={
        "properties": {
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of NSE symbols, e.g. ['RELIANCE', 'INFY', 'TCS']. Max 5.",
            },
        },
        "required": ["symbols"],
    },
)
def get_indicators(symbols: list) -> dict:
    symbols = symbols[:5]
    rows = [_compute_indicators_one(s) for s in symbols]

    # Build compact pipe-table so keys are not repeated per row
    header = "SYMBOL|PRICE|RSI|RSI_SIG|MA20|MA20_POS|MA50|MA50_POS|VOL_RATIO|VOL_SIG"
    lines = [header]
    errors = []
    for r in rows:
        if "error" in r:
            errors.append(f"{r['symbol']}: {r['error']}")
            continue
        lines.append(
            f"{r['symbol']}|{r['price']}|{r['rsi']}|{r['rsi_sig']}|"
            f"{r['ma20']}|{r['ma20_pos']}|{r['ma50']}|{r['ma50_pos']}|"
            f"{r['vol_ratio']}|{r['vol_sig']}"
        )

    result: dict = {"table": "\n".join(lines)}
    if errors:
        result["errors"] = errors
    return result


@register_tool(
    name="get_multiple_prices",
    description=(
        "Get current live quotes for up to 5 NSE stocks at once. More efficient "
        "than calling get_current_price multiple times. Returns a dict with "
        "symbol keys, each containing last_price, change_percent, etc."
    ),
    parameters={
        "properties": {
            "symbols": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of NSE symbols (e.g. ['RELIANCE', 'INFY', 'TCS']). Max 5.",
            },
        },
        "required": ["symbols"],
    },
)
def get_multiple_prices(symbols: list[str]) -> dict:
    symbols = symbols[:5]  # Hard cap at 5
    results = {}
    if _data_source == "kite" and _kite:
        # Batch Kite quote — single API call for all symbols
        try:
            instruments = [f"NSE:{s}" for s in symbols]
            with _kite_lock:
                quotes = _kite.quote(instruments)
            for symbol in symbols:
                key = f"NSE:{symbol}"
                if key in quotes:
                    data = quotes[key]
                    ohlc = data.get("ohlc", {})
                    close = ohlc.get("close", 0) or 1
                    results[symbol] = {
                        "symbol": symbol,
                        "last_price": data.get("last_price"),
                        "change_percent": round(
                            ((data.get("last_price", 0) - close) / close) * 100, 2,
                        ),
                        "volume": data.get("volume"),
                        "open": ohlc.get("open"),
                        "high": ohlc.get("high"),
                        "low": ohlc.get("low"),
                        "close": ohlc.get("close"),
                    }
                else:
                    results[symbol] = {"error": f"Symbol {symbol} not found", "symbol": symbol}
        except Exception as e:
            for symbol in symbols:
                results[symbol] = {"error": str(e), "symbol": symbol}
    else:
        # Fallback: call individually for yfinance
        for symbol in symbols:
            results[symbol] = get_current_price(symbol)

    results["timestamp"] = datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S")
    return results


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
    try:
        if _data_source == "kite" and _kite:
            return _kite_market_snapshot()
        return _yf_market_snapshot()
    except Exception as e:
        logger.error(f"get_market_snapshot failed: {e}")
        return {"error": str(e)}


@register_tool(
    name="get_global_context",
    description=(
        "Get the global macro backdrop: overnight US markets (S&P 500, NASDAQ, Dow), "
        "Asian markets (Nikkei, Hang Seng, Shanghai), commodities (Brent crude, gold), "
        "USD/INR rate, and India VIX. Call this at the start of Cycle 1 to understand "
        "global risk sentiment before making any decisions. Also useful when Indian "
        "markets are moving unusually — check this to see if the cause is global."
    ),
    parameters={"properties": {}, "required": []},
)
def get_global_context() -> dict:
    """Fetch global indices, commodities, and currency via yfinance."""
    try:
        import yfinance as yf

        tickers = {
            "S&P 500":     "^GSPC",
            "NASDAQ":      "^IXIC",
            "Dow Jones":   "^DJI",
            "Nikkei 225":  "^N225",
            "Hang Seng":   "^HSI",
            "Shanghai":    "000001.SS",
            "Brent Crude": "BZ=F",
            "Gold":        "GC=F",
            "USD/INR":     "USDINR=X",
            "India VIX":   "^INDIAVIX",
        }

        result = {}
        for name, ticker in tickers.items():
            try:
                data = yf.Ticker(ticker).fast_info
                price = getattr(data, "last_price", None) or getattr(data, "regularMarketPrice", None)
                prev_close = getattr(data, "previous_close", None)
                if price and prev_close and prev_close > 0:
                    change_pct = ((price - prev_close) / prev_close) * 100
                    result[name] = {
                        "price": round(price, 2),
                        "change_pct": round(change_pct, 2),
                    }
                elif price:
                    result[name] = {"price": round(price, 2), "change_pct": None}
            except Exception:
                result[name] = {"error": "unavailable"}

        result["timestamp"] = datetime.now(_IST).strftime("%Y-%m-%dT%H:%M:%S")
        return result

    except Exception as e:
        logger.error(f"get_global_context failed: {e}")
        return {"error": str(e)}
