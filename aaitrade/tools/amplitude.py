"""Amplitude analysis — can this stock actually deliver the move you want,
and how far will it go against you first?

The lesson that produced this tool: the system bought WELSPUNLIV for a 1.4%
target. That stock moves 3.5% on an average DAY and its median drop over any
10-day window is -4.5%. The target was smaller than the noise, so the trade
was unwinnable in either direction — it would be stopped out by ordinary
fluctuation long before a 1.4% gain could stick.

Three measurements decide whether a big-target trade is even possible:

  ATR      — how far the stock travels per day. A stock moving 1.7%/day cannot
             plausibly hand you 10% in five days; one moving 3.5% can.
  MAE      — Maximum Adverse Excursion: how far it typically goes AGAINST you
             before it works. This is where the stop belongs. A stop tighter
             than MAE is a donation to volatility.
  HIT RATE — from this stock's own history, how often a move of the size you
             want actually arrived inside your holding window.

Reward must exceed MAE, or the stop gets hit before the target. That single
ratio separates a real opportunity from an expensive one.
"""

from __future__ import annotations

import logging
import statistics

from aaitrade.tools import register_tool

logger = logging.getLogger(__name__)


def _atr_pct(candles: list[dict], window: int = 20) -> float | None:
    """Average True Range as % of price — the stock's daily 'speed'."""
    if len(candles) < window + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        prev_close = candles[i - 1]["close"]
        tr = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - prev_close),
            abs(candles[i]["low"] - prev_close),
        )
        if candles[i]["close"] > 0:
            trs.append(tr / candles[i]["close"] * 100)
    return round(statistics.mean(trs[-window:]), 2) if trs else None


def _excursions(candles: list[dict], horizon: int) -> tuple[list[float], list[float]]:
    """For every past day: the best rise and worst fall over the next `horizon` days."""
    rises, falls = [], []
    for i in range(len(candles) - horizon):
        base = candles[i]["close"]
        if base <= 0:
            continue
        fwd = candles[i + 1: i + 1 + horizon]
        rises.append((max(c["high"] for c in fwd) - base) / base * 100)
        falls.append((min(c["low"] for c in fwd) - base) / base * 100)
    return rises, falls


def analyse_amplitude(candles: list[dict], target_pct: float, horizon_days: int) -> dict:
    """Core maths. Returns the viability picture for a target over a horizon."""
    if len(candles) < 60:
        return {"error": f"insufficient history ({len(candles)} days)"}

    atr = _atr_pct(candles)
    rises, falls = _excursions(candles, horizon_days)
    if not rises:
        return {"error": "not enough history for this horizon"}

    hit_rate = round(sum(1 for r in rises if r >= target_pct) / len(rises) * 100)
    median_rise = round(statistics.median(rises), 1)
    median_fall = round(statistics.median(falls), 1)   # negative
    worst_fall = round(min(falls), 1)
    mae = abs(median_fall)

    # Can the stock physically travel this far in the time allowed?
    reachable = None
    if atr:
        # A stock rarely trends one way every day; ~55% net travel is generous.
        plausible_travel = atr * horizon_days * 0.55
        reachable = target_pct <= plausible_travel

    reward_to_mae = round(target_pct / mae, 2) if mae > 0 else None

    verdict, note = "VIABLE", ""
    if reachable is False:
        verdict = "TOO_SLOW"
        note = (f"Moves only {atr}%/day, so {target_pct}% in {horizon_days} days would "
                f"need near-uninterrupted one-way movement. Pick a faster stock or a "
                f"smaller target.")
    elif reward_to_mae is not None and reward_to_mae < 1.0:
        verdict = "TARGET_INSIDE_NOISE"
        note = (f"Typical drop against you over {horizon_days} days is {median_fall}%, "
                f"BIGGER than your {target_pct}% target. You would be stopped out by "
                f"ordinary fluctuation before the gain arrives. This is the mistake that "
                f"cost money on WELSPUNLIV.")
    elif hit_rate < 30:
        verdict = "RARELY_DELIVERS"
        note = (f"This stock produced a +{target_pct}% move within {horizon_days} days only "
                f"{hit_rate}% of the time historically. The setup may be fine; the odds are not.")
    elif reward_to_mae is not None and reward_to_mae < 1.5:
        verdict = "THIN_EDGE"
        note = (f"Target {target_pct}% vs typical adverse move {mae}% — workable but tight. "
                f"Demand strong evidence, and size down.")
    else:
        note = (f"Target is {reward_to_mae}x the typical move against you, and this stock "
                f"delivered it {hit_rate}% of the time in the past. Structurally sound.")

    return {
        "atr_pct_per_day": atr,
        "horizon_days": horizon_days,
        "target_pct": target_pct,
        "hit_rate_pct": hit_rate,
        "median_upside_pct": median_rise,
        "median_adverse_pct": median_fall,
        "worst_adverse_pct": worst_fall,
        "suggested_stop_pct": round(mae * 1.4, 1) if mae else None,
        "reward_to_adverse_ratio": reward_to_mae,
        "physically_reachable": reachable,
        "verdict": verdict,
        "note": note,
    }


@register_tool(
    name="analyse_amplitude",
    description=(
        "Before committing to a big-target trade, ask this: can the stock "
        "actually MOVE that far in the time you have, and how far does it "
        "normally go against you first?\n\n"
        "Returns ATR (daily travel), the historical hit rate for your exact "
        "target over your exact horizon, the median and worst adverse move "
        "(where your stop must sit), and the reward-to-adverse ratio.\n\n"
        "Verdicts: VIABLE / THIN_EDGE / TOO_SLOW (stock can't travel that far "
        "in time) / TARGET_INSIDE_NOISE (your target is smaller than the "
        "normal drop against you — an unwinnable trade) / RARELY_DELIVERS.\n\n"
        "MANDATORY before every buy in this session. A perfect chart on a stock "
        "that only moves 1.5%/day cannot produce a 10% win in a week."
    ),
    parameters={
        "properties": {
            "symbol": {"type": "string", "description": "NSE symbol"},
            "target_pct": {
                "type": "number",
                "description": "The gain you intend to capture, in % (e.g. 7 for 7%)",
            },
            "horizon_days": {
                "type": "integer",
                "description": "Trading days you are willing to hold (e.g. 10). Default 10.",
            },
        },
        "required": ["symbol", "target_pct"],
    },
)
def analyse_amplitude_tool(symbol: str, target_pct: float, horizon_days: int = 10) -> dict:
    try:
        from aaitrade.tools.market import _get_history_cached
        history = _get_history_cached(symbol)
        if "error" in history:
            return {"symbol": symbol, "error": history["error"]}
        result = analyse_amplitude(history["candles"], target_pct, max(2, min(horizon_days, 40)))
        result["symbol"] = symbol
        return result
    except Exception as e:
        logger.error(f"analyse_amplitude failed for {symbol}: {e}", exc_info=True)
        return {"symbol": symbol, "error": str(e)}
