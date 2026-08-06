"""Deterministic price-level analysis — the six entry checks, computed in Python.

Claude used to eyeball raw OHLCV JSON and count level touches by hand — slow,
token-hungry, and error-prone. This tool computes the answers directly:

- visit-frequency counts for any entry/target level
- auto-detected support/resistance bands (3+ touches) near the current price
- band position (is price in the bottom third of the 14-day range?)
- oscillation vs falling-knife shape detection
- 90-day range consistency
- net-profit check after real transaction costs

One call replaces ~4 tool calls and a page of mental arithmetic per candidate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aaitrade.tools import register_tool

_IST = timezone(timedelta(hours=5, minutes=30))

logger = logging.getLogger(__name__)

# Bin width for clustering touches into levels (% of price)
_LEVEL_BIN_PCT = 0.5


def _touches(candles: list[dict], level: float) -> dict:
    """Count how many days the price actually interacted with a level."""
    traded_through = sum(1 for c in candles if c["low"] <= level <= c["high"])
    closed_at_or_below = sum(1 for c in candles if c["close"] <= level)
    closed_at_or_above = sum(1 for c in candles if c["close"] >= level)
    return {
        "traded_through": traded_through,
        "closed_at_or_below": closed_at_or_below,
        "closed_at_or_above": closed_at_or_above,
    }


def _find_levels(candles: list[dict], current: float) -> tuple[list, list]:
    """Cluster daily lows/highs into price bands; return (supports, resistances).

    A band is real if it was touched on 3+ separate days. Supports are bands
    below the current price, resistances above. Sorted nearest-first.
    """
    if not candles or current <= 0:
        return [], []
    bin_size = current * _LEVEL_BIN_PCT / 100
    bins: dict[int, int] = {}
    for c in candles:
        for price in (c["low"], c["high"], c["close"]):
            b = int(price / bin_size)
            bins[b] = bins.get(b, 0) + 1

    supports, resistances = [], []
    seen: set[int] = set()
    for b, count in bins.items():
        if b in seen or count < 3:
            continue
        seen.add(b)
        level = round((b + 0.5) * bin_size, 2)
        entry = {"level": level, "touches": count}
        if level < current:
            supports.append(entry)
        elif level > current:
            resistances.append(entry)

    supports.sort(key=lambda x: current - x["level"])
    resistances.sort(key=lambda x: x["level"] - current)
    return supports[:3], resistances[:3]


def estimate_time_to_target(candles: list[dict], entry: float, target: float,
                            lookback: int = 120, max_wait: int = 20) -> dict:
    """Empirically: when this stock has been at the entry level before, how
    often did it reach the target, and how many days did it take?

    Answers the two questions a static level check cannot: will it get there,
    and how long will my capital be tied up? Measured from the stock's own
    history, not from theory.
    """
    seg = candles[-lookback:] if len(candles) >= lookback else candles
    if len(seg) < 30 or entry <= 0 or target <= entry:
        return {"samples": 0, "hit_rate": None, "median_days": None,
                "note": "Not enough history to estimate."}

    entry_zone = entry * 1.005  # "at or below entry"
    days_taken, misses = [], 0
    i = 0
    while i < len(seg) - 1:
        if seg[i]["low"] <= entry_zone:
            hit = None
            for j in range(i + 1, min(i + 1 + max_wait, len(seg))):
                if seg[j]["high"] >= target:
                    hit = j - i
                    break
            if hit is not None:
                days_taken.append(hit)
                i += hit  # move past this episode so we don't double-count
            else:
                misses += 1
                i += max_wait  # this episode failed; skip its window
        i += 1

    samples = len(days_taken) + misses
    if samples == 0:
        return {"samples": 0, "hit_rate": None, "median_days": None,
                "note": "Price has not visited this entry level recently."}

    hit_rate = round(len(days_taken) / samples * 100)
    median_days = None
    if days_taken:
        s = sorted(days_taken)
        median_days = s[len(s) // 2]

    if not days_taken:
        note = (f"In {samples} past visits to this level, the target was NEVER reached "
                f"within {max_wait} days. The target may be too far.")
    else:
        note = (f"Historically: from this level the target was reached {hit_rate}% of the "
                f"time ({len(days_taken)}/{samples}), typically in ~{median_days} trading days.")
    return {"samples": samples, "hit_rate": hit_rate,
            "median_days": median_days, "max_wait": max_wait, "note": note}


def trend_context(candles: list[dict]) -> dict:
    """Multi-horizon read, weighted toward the recent.

    Recent behaviour (1 week, 1 month) decides whether the setup is live —
    it is what actually drives a 0.5-1.5% bounce. The 3- and 6-month picture
    does NOT veto the trade (a small target is reachable even in a stock
    that is lower than it was months ago); it sets RISK and EXPECTED TIME:
    a stock still trending down can hit the target but is likelier to need
    a stop, and to take longer getting there.
    """
    closes = [c["close"] for c in candles]
    now = closes[-1]

    def ret(n: int) -> float | None:
        return round((now - closes[-n]) / closes[-n] * 100, 1) if len(closes) >= n else None

    ret_1w, ret_1m, ret_3m, ret_6m = ret(5), ret(22), ret(66), ret(132)

    def band_pos(window: int) -> float | None:
        if len(candles) < window:
            return None
        seg = candles[-window:]
        hi = max(c["high"] for c in seg)
        lo = min(c["low"] for c in seg)
        return round((now - lo) / (hi - lo) * 100, 1) if hi > lo else 50.0

    pos_20, pos_60, pos_120 = band_pos(20), band_pos(60), band_pos(120)

    # ── Recent regime (primary — drives the bounce) ──
    if ret_1m is not None and ret_1m <= -8:
        recent = "SHARP_RECENT_FALL"
    elif ret_1m is not None and ret_1m >= 6:
        recent = "RECENT_STRENGTH"
    else:
        recent = "RECENT_STABLE"

    # ── Long-horizon regime (secondary — risk + expected time) ──
    if ret_3m is None:
        longer, risk = "UNKNOWN", "normal"
    elif ret_3m >= 8:
        longer, risk = "UPTREND", "low"
    elif ret_3m <= -20:
        longer, risk = "STEEP_DOWNTREND", "high"
    elif ret_3m <= -8:
        longer, risk = "DOWNTREND", "elevated"
    else:
        longer, risk = "SIDEWAYS", "normal"

    # Combined verdict — recent leads, long-horizon qualifies
    if recent == "RECENT_STRENGTH" and longer == "UPTREND":
        verdict = "UPTREND_PULLBACK"
        note = "Strong on both horizons — a pullback inside an uptrend. Best-quality setup."
    elif longer in ("DOWNTREND", "STEEP_DOWNTREND") and recent == "SHARP_RECENT_FALL":
        verdict = "ACCELERATING_DECLINE"
        note = ("Falling on BOTH horizons — down over months AND dropping hard this month. "
                "The bounce is least reliable here and the next leg down is the real risk. "
                "Take it only with a specific reason the fall is ending, and size small.")
    elif longer in ("DOWNTREND", "STEEP_DOWNTREND"):
        verdict = "DOWNTREND_STABILISING"
        note = (f"Down {abs(ret_3m):.0f}% over 3 months but steady recently "
                f"(1m {ret_1m}%, 1w {ret_1w}%). A small target is still very reachable — "
                f"the long decline mainly means wider stops and less margin for error. "
                f"Tradeable with discipline; do not size up.")
    elif recent == "SHARP_RECENT_FALL":
        verdict = "SHARP_DIP_IN_RANGE"
        note = ("Longer-term trend is intact; this is a sharp recent drop inside it. "
                "Classic mean-reversion candidate IF the cause is not company-specific.")
    else:
        verdict = "STABLE_RANGE"
        note = "Steady on both horizons — genuine range behaviour, dips mean-revert."

    return {
        "ret_1w": ret_1w, "ret_1m": ret_1m, "ret_3m": ret_3m, "ret_6m": ret_6m,
        "pos_20d": pos_20, "pos_60d": pos_60, "pos_120d": pos_120,
        "recent_regime": recent, "long_regime": longer, "risk_level": risk,
        "verdict": verdict, "note": note,
    }


def _oscillation(candles: list[dict]) -> dict:
    """Shape of the recent chart: bouncing band or straight-line fall?"""
    closes = [c["close"] for c in candles]
    if len(closes) < 5:
        return {"shape": "insufficient_data", "direction_changes": 0}

    direction_changes = 0
    prev_dir = 0
    for i in range(1, len(closes)):
        d = 1 if closes[i] > closes[i - 1] else (-1 if closes[i] < closes[i - 1] else 0)
        if d != 0 and prev_dir != 0 and d != prev_dir:
            direction_changes += 1
        if d != 0:
            prev_dir = d

    total_change_pct = (closes[-1] - closes[0]) / closes[0] * 100
    down_days = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
    down_ratio = down_days / (len(closes) - 1)

    if total_change_pct < -4 and direction_changes <= 3 and down_ratio > 0.65:
        shape = "FALLING_KNIFE"  # straight-line down, no floor demonstrated
    elif direction_changes >= 4:
        shape = "OSCILLATING"    # healthy band behaviour
    else:
        shape = "TRENDING"
    return {
        "shape": shape,
        "direction_changes": direction_changes,
        "change_pct_14d": round(total_change_pct, 2),
    }


@register_tool(
    name="analyze_levels",
    description=(
        "THE core pre-trade tool. Computes, deterministically, everything the "
        "six entry checks need for one stock: current price vs 14-day band "
        "(bottom-third test), auto-detected support/resistance levels with "
        "touch counts (3+ touches = real level), visit-frequency validation "
        "of YOUR entry and target prices, oscillation-vs-falling-knife shape, "
        "90-day range context, and net profit AFTER transaction costs for the "
        "proposed trade. Call this INSTEAD of manually counting candles from "
        "get_price_history. If entry/target are omitted it suggests the "
        "nearest valid levels. Trust these numbers — they are computed, not "
        "estimated."
    ),
    parameters={
        "properties": {
            "symbol": {"type": "string", "description": "NSE symbol, e.g. 'RELIANCE'"},
            "entry_price": {
                "type": "number",
                "description": "Your intended buy price (optional — omit to get suggestions)",
            },
            "target_price": {
                "type": "number",
                "description": "Your intended sell target (optional)",
            },
            "position_value": {
                "type": "number",
                "description": "Approx position size in ₹ for the cost check (default 5000)",
            },
        },
        "required": ["symbol"],
    },
)
def analyze_levels(
    symbol: str,
    entry_price: float | None = None,
    target_price: float | None = None,
    position_value: float = 5000,
) -> dict:
    try:
        from aaitrade.tools.market import _get_history_cached
        history = _get_history_cached(symbol)
        if "error" in history:
            return {"symbol": symbol, "error": history["error"]}

        candles = history["candles"]
        if len(candles) < 15:
            return {"symbol": symbol, "error": f"insufficient data ({len(candles)} days)"}

        last30 = candles[-30:]
        last14 = candles[-14:]
        last90 = candles[-90:]
        current = last30[-1]["close"]

        # 14-day band position
        band_high = max(c["high"] for c in last14)
        band_low = min(c["low"] for c in last14)
        band_range = band_high - band_low
        band_pos = round((current - band_low) / band_range * 100, 1) if band_range > 0 else 50.0

        # 90-day context
        hi90 = max(c["high"] for c in last90)
        lo90 = min(c["low"] for c in last90)

        supports, resistances = _find_levels(last30, current)
        shape = _oscillation(last14)
        trend = trend_context(candles)

        result: dict = {
            "symbol": symbol,
            "current_price": current,
            "band_14d": {
                "low": round(band_low, 2),
                "high": round(band_high, 2),
                "position_pct": band_pos,  # 0 = at low, 100 = at high
                "in_bottom_third": band_pos <= 33.4,
            },
            "range_90d": {"low": round(lo90, 2), "high": round(hi90, 2)},
            "trend_context": trend,  # ← is the 14d band a range, or a step down?
            # Top 4 each — beyond that is context bloat: every extra row is
            # re-read by every later round of the cycle at token cost.
            "supports_30d": supports[:4],        # 3+ touch bands below current
            "resistances_30d": resistances[:4],  # 3+ touch bands above current
            "shape_14d": shape,
        }

        # Suggest levels when not provided: nearest demonstrated support/resistance
        if entry_price is None and supports:
            entry_price = supports[0]["level"]
            result["suggested_entry"] = entry_price
        if target_price is None and resistances:
            target_price = resistances[0]["level"]
            result["suggested_target"] = target_price

        # Validate the proposed trade
        checks = []
        if entry_price:
            ev = _touches(last30, entry_price)
            entry_ok = ev["traded_through"] >= 3
            checks.append(f"ENTRY ₹{entry_price}: {ev['traded_through']} touches in 30d "
                          f"→ {'PASS' if entry_ok else 'FAIL (<3 — not a demonstrated floor)'}")
            result["entry_validation"] = {**ev, "level": entry_price, "passes": entry_ok}
        if target_price:
            tv = _touches(last30, target_price)
            target_ok = tv["traded_through"] >= 3
            checks.append(f"TARGET ₹{target_price}: {tv['traded_through']} touches in 30d "
                          f"→ {'PASS' if target_ok else 'FAIL (<3 — unrealistic target)'}")
            result["target_validation"] = {**tv, "level": target_price, "passes": target_ok}

        checks.append(f"BAND POSITION: {band_pos}% of 14d range "
                      f"→ {'PASS (bottom third)' if band_pos <= 33.4 else 'FAIL (not a local low)'}")
        checks.append(f"SHAPE: {shape['shape']} ({shape['direction_changes']} direction changes) "
                      f"→ {'FAIL — no floor yet, skip' if shape['shape'] == 'FALLING_KNIFE' else 'PASS'}")

        # Multi-horizon CONTEXT — deliberately not a pass/fail. Recent
        # behaviour drives the bounce; the longer view sets risk and timing.
        # Judge the situation; do not obey the percentage.
        checks.append(
            f"CONTEXT [{trend['verdict']}, risk {trend['risk_level']}]: "
            f"1w {trend['ret_1w']}%, 1m {trend['ret_1m']}%, 3m {trend['ret_3m']}%, "
            f"6m {trend['ret_6m']}% | position in range: 20d {trend['pos_20d']}%, "
            f"60d {trend['pos_60d']}%. {trend['note']}"
        )

        # Net-profit check after real costs
        if entry_price and target_price and entry_price > 0:
            from aaitrade.executor import transaction_costs
            qty = max(int(position_value // entry_price), 1)
            buy_cost = transaction_costs("BUY", entry_price, qty)
            sell_cost = transaction_costs("SELL", target_price, qty)
            gross = (target_price - entry_price) * qty
            net = gross - buy_cost - sell_cost
            invested = entry_price * qty
            result["profit_check"] = {
                "quantity": qty,
                "invested": round(invested, 2),
                "gross_profit": round(gross, 2),
                "total_charges": round(buy_cost + sell_cost, 2),
                "net_profit": round(net, 2),
                "net_pct": round(net / invested * 100, 2) if invested else 0,
            }
            checks.append(
                f"NET PROFIT: ₹{net:.0f} ({net / invested * 100:.2f}%) after ₹{buy_cost + sell_cost:.0f} charges "
                f"→ {'PASS' if net > invested * 0.003 else 'FAIL — costs eat this trade, raise target or size'}"
            )

        # How long has this exact move taken before, and did it complete?
        if entry_price and target_price:
            timing = estimate_time_to_target(candles, entry_price, target_price)
            result["time_to_target"] = timing
            checks.append(f"TIMING: {timing['note']}")

        result["checklist"] = checks
        return result

    except Exception as e:
        logger.error(f"analyze_levels failed for {symbol}: {e}", exc_info=True)
        return {"symbol": symbol, "error": str(e)}
