"""Full-market scanner — nightly band-fit scoring across the NSE 500.

The watchlist problem: 30 stocks can't supply 20-30 quality setups a month.
This module scans the whole liquid market every evening after close, runs the
SAME math analyze_levels uses (band position, floor touches, target gap,
oscillation shape), and ranks everything 0-100. The morning briefing carries
the top 15 — Python does breadth for zero tokens, Claude does depth.

Data source: Kite historical API when available (licensed, reliable,
~3 req/sec → ~4 min for 500 symbols), yfinance fallback otherwise.
Universe: Nifty 500 constituents (downloaded and cached; falls back to the
cached copy, then to the session watchlist).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aaitrade import db

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

_UNIVERSE_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
_UNIVERSE_CACHE = Path(__file__).resolve().parent.parent / "config" / "universe_nse500.csv"

# Liquidity / practicality filters
_MIN_PRICE = 50.0
_MAX_PRICE = 5000.0
_MIN_TURNOVER_CR = 2.0     # 20-day avg daily turnover, in ₹ crore
_MIN_GAP_PCT = 0.8         # entry→target must clear transaction costs
_MAX_BAND_POS = 45.0       # skip stocks in the upper half of their band

_scan_lock = threading.Lock()


# ── Universe ──────────────────────────────────────────────────────────────


def get_universe() -> list[str]:
    """Return the scan universe (NSE 500 symbols). Best-effort download,
    cached fallback, watchlist as last resort."""
    try:
        import requests
        resp = requests.get(
            _UNIVERSE_URL, timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        if resp.status_code == 200 and "Symbol" in resp.text[:200]:
            _UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _UNIVERSE_CACHE.write_text(resp.text)
            logger.info("Universe list refreshed from NSE")
    except Exception as e:
        logger.warning(f"Universe download failed (will use cache): {e}")

    if _UNIVERSE_CACHE.exists():
        csv_text = _UNIVERSE_CACHE.read_text()

        # Feed the knowledge layer: full-market company/sector structure
        try:
            from aaitrade.knowledge import refresh_stock_universe
            refresh_stock_universe(csv_text)
        except Exception as e:
            logger.warning(f"stock_universe refresh failed: {e}")

        symbols = []
        for line in csv_text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 3:
                sym = parts[2].strip().strip('"')
                if sym and sym.isascii():
                    symbols.append(sym)
        if len(symbols) > 50:
            return symbols

    # Last resort: whatever is on any active session's watchlist
    rows = db.query(
        "SELECT DISTINCT symbol FROM watchlist WHERE removed_at IS NULL"
    )
    logger.warning(f"No universe file — falling back to watchlist ({len(rows)} symbols)")
    return [r["symbol"] for r in rows]


# ── Scoring ───────────────────────────────────────────────────────────────


def score_symbol(symbol: str, candles: list[dict]) -> dict | None:
    """Score one stock's band-fit setup from ~120 daily candles.

    Returns a result dict, or None if the stock fails hard filters.
    """
    if len(candles) < 40:
        return None

    from aaitrade.tools.levels import _find_levels, _oscillation

    last30 = candles[-30:]
    last14 = candles[-14:]
    close = last30[-1]["close"]

    if not (_MIN_PRICE <= close <= _MAX_PRICE):
        return None

    # Liquidity: 20-day average turnover in ₹ crore
    last20 = candles[-20:]
    turnover_cr = sum(c["close"] * c["volume"] for c in last20) / len(last20) / 1e7
    if turnover_cr < _MIN_TURNOVER_CR:
        return None

    # Band position
    band_high = max(c["high"] for c in last14)
    band_low = min(c["low"] for c in last14)
    band_range = band_high - band_low
    if band_range <= 0:
        return None
    band_pos = (close - band_low) / band_range * 100
    if band_pos > _MAX_BAND_POS:
        return None  # not near a local low — not our setup

    # Shape: reject falling knives outright
    shape = _oscillation(last14)
    if shape["shape"] == "FALLING_KNIFE":
        return None

    # Levels
    supports, resistances = _find_levels(last30, close)
    if not supports or not resistances:
        return None
    floor = supports[0]
    # Target = the NEAREST resistance that still clears the cost floor.
    # Taking resistances[0] blindly rejects good setups where a minor level
    # sits just above price but a real target exists slightly higher.
    ceiling = next(
        (r for r in resistances
         if (r["level"] - close) / close * 100 >= _MIN_GAP_PCT),
        None,
    )
    if ceiling is None:
        return None  # no reachable target clears transaction costs
    gap_pct = (ceiling["level"] - close) / close * 100

    # ── Composite score (0-100) ──
    score = 0.0
    # Deeper in the band = better (up to 30)
    score += max(0.0, (45 - band_pos)) / 45 * 30
    # Floor strength: touches capped at 8 (up to 25)
    score += min(floor["touches"], 8) / 8 * 25
    # Target quality: sweet spot 0.8-2.5% gap with real touches (up to 20)
    gap_component = 20.0 if gap_pct <= 2.5 else max(5.0, 20 - (gap_pct - 2.5) * 5)
    score += gap_component * min(ceiling["touches"], 6) / 6
    # Oscillation (up to 15)
    score += 15 if shape["shape"] == "OSCILLATING" else 5
    # Dip freshness: arrived at the low recently, not parked there (up to 10)
    close_5d_ago = candles[-6]["close"] if len(candles) >= 6 else close
    today_low = last30[-1]["low"]
    if close < close_5d_ago and (today_low - floor["level"]) / close * 100 < 1.5:
        score += 10

    return {
        "symbol": symbol,
        "score": round(score, 1),
        "close": round(close, 2),
        "entry_level": floor["level"],
        "entry_touches": floor["touches"],
        "target_level": ceiling["level"],
        "target_touches": ceiling["touches"],
        "gap_pct": round(gap_pct, 2),
        "band_pos": round(band_pos, 1),
        "shape": shape["shape"],
        "turnover_cr": round(turnover_cr, 1),
    }


# ── Data fetch ────────────────────────────────────────────────────────────


def _fetch_candles(symbol: str) -> list[dict] | None:
    """~120 daily candles for one symbol via the active market data source."""
    try:
        from aaitrade.tools.market import get_price_history
        history = get_price_history(symbol, days=120)
        if "error" in history:
            return None
        return history["candles"]
    except Exception:
        return None


# ── Runner ────────────────────────────────────────────────────────────────


def already_scanned_today() -> bool:
    today = datetime.now(_IST).strftime("%Y-%m-%d")
    row = db.query_one(
        "SELECT COUNT(*) as n FROM scan_results WHERE scan_date = ?", (today,)
    )
    return bool(row and row["n"] > 0)


def run_daily_scan(max_symbols: int = 520) -> dict:
    """Scan the universe, rank, persist top results. Safe to call repeatedly —
    returns early if today's scan already exists. Takes ~3-8 minutes."""
    with _scan_lock:
        if already_scanned_today():
            return {"status": "already_done"}

        started = time.monotonic()
        today = datetime.now(_IST).strftime("%Y-%m-%d")
        universe = get_universe()[:max_symbols]
        logger.info(f"Daily scan starting: {len(universe)} symbols")

        from aaitrade.tools.market import _data_source
        throttle = 0.35 if _data_source == "kite" else 0.0  # Kite: 3 req/s limit

        results = []
        errors = 0
        for symbol in universe:
            candles = _fetch_candles(symbol)
            if throttle:
                time.sleep(throttle)
            if not candles:
                errors += 1
                continue
            scored = score_symbol(symbol, candles)
            if scored:
                results.append(scored)

        results.sort(key=lambda r: r["score"], reverse=True)
        top = results[:40]  # persist a deep bench; briefing shows 15

        now = db.now_iso()
        for rank, r in enumerate(top, start=1):
            try:
                db.insert("scan_results", {
                    "scan_date": today, "rank": rank, "created_at": now, **r,
                })
            except Exception:
                pass  # UNIQUE(scan_date, symbol) — rerun safety

        elapsed = time.monotonic() - started
        logger.info(
            f"Daily scan done in {elapsed / 60:.1f}m: {len(universe)} scanned, "
            f"{len(results)} passed filters, top score "
            f"{top[0]['score'] if top else 'n/a'}, {errors} fetch errors"
        )

        # Telegram: one-line evening digest
        try:
            from aaitrade.telegram_bot import get_bot
            bot = get_bot()
            if bot and top:
                lines = [
                    f"{r['rank']}. {r['symbol']} score {r['score']} | entry ₹{r['entry_level']} → target ₹{r['target_level']} (+{r['gap_pct']}%)"
                    for r in top[:5]
                ]
                bot.send("🔎 Nightly scan — top setups:\n" + "\n".join(lines), parse_mode=None)
        except Exception:
            pass

        return {"status": "ok", "scanned": len(universe), "passed": len(results),
                "saved": len(top), "minutes": round(elapsed / 60, 1)}


def latest_scan_block(limit: int = 15) -> str:
    """Briefing-ready table of the most recent scan's top setups."""
    latest = db.query_one("SELECT MAX(scan_date) as d FROM scan_results")
    if not latest or not latest["d"]:
        return "No scan available yet."
    rows = db.query(
        "SELECT * FROM scan_results WHERE scan_date = ? ORDER BY rank LIMIT ?",
        (latest["d"], limit),
    )
    if not rows:
        return "No scan available yet."
    lines = [
        f"(computed {latest['d']} post-close — verify with analyze_levels + news before acting)",
        "RANK|SYMBOL|SCORE|CLOSE|ENTRY(touches)|TARGET(touches)|GAP%|BAND_POS|SHAPE",
    ]
    for r in rows:
        lines.append(
            f"{r['rank']}|{r['symbol']}|{r['score']}|{r['close']}|"
            f"{r['entry_level']}({r['entry_touches']})|{r['target_level']}({r['target_touches']})|"
            f"{r['gap_pct']}|{r['band_pos']}|{r['shape']}"
        )
    return "\n".join(lines)
