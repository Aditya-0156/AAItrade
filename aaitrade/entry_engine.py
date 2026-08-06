"""Entry engine — pure logic for stalked entries and trailing exits.

WHY THIS EXISTS
───────────────
The entry audit (15 live buys, 15-minute reconstruction) found:
- 15/15 buys went against us after entry; median drawdown −1.28%
- the post-entry low arrived a median 27.5 HOURS after the buy
- a ≥0.75% better entry existed within days on 10/15 buys

Root cause: the model buys the FIRST TOUCH of a support level. A touch proves
sellers reached the level, not that buyers defended it. The fix is to separate
WHAT to buy (the model's job — it is right ~89% of the time) from WHEN to buy
(this engine's job, executed by the price monitor which sees every 30 seconds
of the tape, not four snapshots a day).

An entry plan fills one of two ways:
- DISCOUNT: price reaches level × (1 − discount). The discount is calibrated
  from this stock's own touch history — the median amount by which it
  overshoots the level before recovering. We buy the fall near its
  statistically likely bottom instead of its top.
- CONFIRMED: price touches the level, makes a low, then a HIGHER low, and
  reclaims the level. That is what an actually-defended floor looks like on
  the tape. We pay slightly more than the discount price in exchange for
  evidence.
Neither → the plan expires, or cancels if price runs away above the level.
A missed trade costs ~nothing at our win sizes; a bad entry costs real money.

The trail logic mirrors it on the exit side: crossing the target arms a
trailing stop instead of an instant market sell, so a GRASIM (+4.15% offered,
+0.98% taken) keeps running until the rise actually stops — while a floor at
the target guarantees we never give the target back.

Everything here is pure functions over (plan, bars, price) so the whole state
machine is unit-testable. The price monitor owns threads and IO; this owns
decisions.
"""

from __future__ import annotations

import logging
from statistics import median

logger = logging.getLogger(__name__)

# Trigger A: bounds for the calibrated discount. Below 0.4% the discount is
# noise; above 2.5% we are hoping for a crash, not timing a dip.
DISCOUNT_MIN_PCT = 0.4
DISCOUNT_MAX_PCT = 2.5
DISCOUNT_DEFAULT_PCT = 1.0   # used when the stock has too few touches to calibrate

# A "touch" of the level = trading within this margin of it.
TOUCH_MARGIN_PCT = 0.2

# Trigger B: how far above the level a confirmed reclaim may still be bought.
# Beyond this the entry edge is spent — better to let the plan keep stalking.
CHASE_CAP_PCT = 0.5

# The higher low must clear the touch extreme by this much to count as real.
HIGHER_LOW_MIN_PCT = 0.1

# Runaway: price sustained this far above the level means the dip never came.
RUNAWAY_PCT = 1.5

# Trailing exit: sell when price falls this far off the post-target high.
TRAIL_PCT = 0.4


def calibrate_discount(candles: list[dict], level: float) -> float:
    """How far below `level` does this stock typically overshoot before recovering?

    Looks at every prior touch of the level in `candles` (intraday or daily),
    measures the deepest low within the bars that follow each touch, and takes
    the median overshoot. Falls back to DISCOUNT_DEFAULT_PCT when there are
    fewer than 3 touches — one sample is an anecdote, not a distribution.
    """
    if not candles or level <= 0:
        return DISCOUNT_DEFAULT_PCT

    touch_line = level * (1 + TOUCH_MARGIN_PCT / 100)
    overshoots: list[float] = []
    i = 0
    n = len(candles)
    while i < n:
        if candles[i]["low"] <= touch_line:
            # follow this touch until price closes back above the level
            deepest = candles[i]["low"]
            j = i + 1
            while j < n and candles[j]["close"] < level:
                deepest = min(deepest, candles[j]["low"])
                j += 1
            if j < n:  # only completed recoveries teach us the overshoot
                overshoots.append(max(0.0, (level - deepest) / level * 100))
            i = j + 1
        else:
            i += 1

    if len(overshoots) < 3:
        return DISCOUNT_DEFAULT_PCT
    return round(min(max(median(overshoots), DISCOUNT_MIN_PCT), DISCOUNT_MAX_PCT), 2)


def evaluate_entry_plan(plan: dict, bars: list[dict], ltp: float) -> dict | None:
    """Decide whether a stalking plan should act right now.

    plan: row from entry_plans (needs level, discount_pct, touched, touch_low,
          stop_loss_price).
    bars: today's (+ prior session's) intraday bars, oldest first. May be
          empty — Trigger B simply stays unavailable without a tape.
    ltp:  last traded price.

    Returns None (keep stalking) or a dict:
      {"action": "fill", "trigger": "discount"|"confirmed", "price": ltp}
      {"action": "runaway"}
      {"action": "touch", "touch_low": x}   — state update only, no trade
    """
    level = plan["level"]
    if level <= 0 or ltp <= 0:
        return None

    # Trigger A — the overshoot arrived. Never fill below the stop: a fall
    # that deep is the structural break the stop exists for, not a discount.
    discount_price = level * (1 - plan["discount_pct"] / 100)
    stop = plan.get("stop_loss_price")
    if ltp <= discount_price and (not stop or ltp > stop):
        return {"action": "fill", "trigger": "discount", "price": ltp}
    if stop and ltp <= stop:
        return {"action": "runaway"}  # broke straight through — abandon, don't buy

    touch_line = level * (1 + TOUCH_MARGIN_PCT / 100)

    # Runaway — the dip never came and price left without us. Only after a
    # sustained move; a brief pop above the level keeps the plan alive.
    if not plan["touched"] and ltp >= level * (1 + RUNAWAY_PCT / 100):
        return {"action": "runaway"}

    # Trigger B — confirmed defence. Checked BEFORE touch tracking because a
    # reclaim sits just above the level, inside the touch margin — the touch
    # branch would swallow it. Requires: a recorded touch, a tape, a higher
    # low after the extreme, and price back above the level (but not so far
    # above that the edge is gone).
    if plan["touched"] and bars and ltp > level:
        if ltp > level * (1 + CHASE_CAP_PCT / 100):
            return None  # reclaimed too fast/too far — wait for a retest
        extreme = plan.get("touch_low") or level
        # bars after the extreme was printed
        after = _bars_after_low(bars, extreme)
        if len(after) >= 2:
            recent_low = min(b["low"] for b in after[-2:])
            if recent_low > extreme * (1 + HIGHER_LOW_MIN_PCT / 100):
                return {"action": "fill", "trigger": "confirmed", "price": ltp}

    # Touch tracking — start/extend the extreme. No trade on a touch: a touch
    # is sellers reaching the level, not buyers defending it.
    if ltp <= touch_line:
        prev_low = plan.get("touch_low")
        new_low = min(ltp, prev_low) if prev_low else ltp
        if not plan["touched"] or (prev_low and new_low < prev_low):
            return {"action": "touch", "touch_low": new_low}

    return None


def _bars_after_low(bars: list[dict], extreme: float) -> list[dict]:
    """Bars strictly after the bar that printed (or came nearest to) the extreme."""
    low_idx = 0
    best = float("inf")
    for i, b in enumerate(bars):
        if b["low"] < best:
            best = b["low"]
            low_idx = i
    return bars[low_idx + 1:]


def split_quantities(total: int) -> tuple[int, int]:
    """(confirmed_half, discount_half) for split-mode fills. Small orders
    don't split — charges on a tiny second leg outweigh the timing edge."""
    if total < 4:
        return total, 0
    first = total // 2
    return first, total - first


def evaluate_trail(position: dict, ltp: float) -> dict | None:
    """Trailing exit for a position whose target has been crossed.

    position: needs take_profit_price, trail_high (may be None).
    Returns None, {"action": "arm"|"raise", "trail_high": x}, or
    {"action": "sell", "reason": ...}.

    Guarantee: once the trail is armed, we never exit below ~the target.
    The floor converts "target reached" from a sell signal into the WORST
    acceptable outcome, with the upside left open.
    """
    target = position.get("take_profit_price")
    if not target or target <= 0 or ltp <= 0:
        return None

    high = position.get("trail_high")

    # Not armed yet — arms the first time price crosses the target.
    if high is None:
        if ltp >= target:
            return {"action": "arm", "trail_high": ltp}
        return None

    # Armed: ratchet the high, never lower it.
    if ltp > high:
        return {"action": "raise", "trail_high": ltp}

    trail_stop = high * (1 - TRAIL_PCT / 100)
    floor = target * 0.999
    sell_at = max(trail_stop, floor)
    if ltp <= sell_at:
        gave_back = (high - ltp) / high * 100
        return {
            "action": "sell",
            "reason": (
                f"TRAIL EXIT: target {target} was crossed, price ran to {high} and has "
                f"come off {gave_back:.2f}% from that high. Selling at market to keep "
                f"the extra move while never giving back below the target."
            ),
        }
    return None
