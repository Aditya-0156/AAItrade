"""Entry engine — the state machine that times entries and trails exits.

Numbers in these tests come from the live audit: median post-entry drawdown
-1.28%, low ~27h after entry, GRASIM sold +0.98% with +4.15% available.
"""

import pytest

from aaitrade.entry_engine import (
    calibrate_discount, evaluate_entry_plan, evaluate_trail, split_quantities,
    DISCOUNT_DEFAULT_PCT, DISCOUNT_MIN_PCT, DISCOUNT_MAX_PCT, TRAIL_PCT,
)


def bar(low, high=None, close=None):
    return {"low": low, "high": high or low * 1.004, "close": close or low * 1.002}


def plan(level=1000.0, discount=1.0, touched=0, touch_low=None, stop=None):
    return {
        "level": level, "discount_pct": discount, "touched": touched,
        "touch_low": touch_low, "stop_loss_price": stop,
    }


class TestCalibrateDiscount:
    def test_median_overshoot_of_prior_touches(self):
        # Three touches of 1000, overshooting to 992, 988, 990 before recovery
        candles = []
        for deep in (992, 988, 990):
            candles += [bar(1005), bar(1000.5), bar(deep, close=995), bar(998, close=1002), bar(1006)]
        d = calibrate_discount(candles, 1000.0)
        assert d == pytest.approx(1.0, abs=0.15)  # median overshoot ≈ 1.0%

    def test_too_few_touches_falls_back_to_default(self):
        candles = [bar(1050), bar(1040), bar(1000.5, close=1005), bar(1050)]
        assert calibrate_discount(candles, 1000.0) == DISCOUNT_DEFAULT_PCT

    def test_clamped_to_bounds(self):
        # Massive overshoots (5%) must clamp to the max
        candles = []
        for _ in range(4):
            candles += [bar(1005), bar(950, close=960), bar(980, close=1002), bar(1006)]
        assert calibrate_discount(candles, 1000.0) <= DISCOUNT_MAX_PCT
        assert calibrate_discount(candles, 1000.0) >= DISCOUNT_MIN_PCT

    def test_no_data(self):
        assert calibrate_discount([], 1000.0) == DISCOUNT_DEFAULT_PCT


class TestDiscountTrigger:
    def test_fills_at_the_overshoot(self):
        """The user's exact scenario: buys at 980, stock goes to 950. The plan
        waits and fills lower instead of paying the top of the dip."""
        v = evaluate_entry_plan(plan(level=980.0, discount=1.5), [], 965.0)
        assert v == {"action": "fill", "trigger": "discount", "price": 965.0}

    def test_no_fill_above_discount(self):
        v = evaluate_entry_plan(plan(level=980.0, discount=1.5), [], 975.0)
        assert v is None or v["action"] == "touch"

    def test_never_fills_below_the_stop(self):
        """A fall through the stop is the structural break the stop exists
        for — buying it would be catching the knife the plan was avoiding."""
        v = evaluate_entry_plan(plan(level=1000.0, discount=2.0, stop=985.0), [], 979.0)
        assert v == {"action": "runaway"}


class TestConfirmedTrigger:
    def test_higher_low_and_reclaim_fills(self):
        """Touch 995 → low prints → two bars with higher lows → price back
        above the level = the floor was actually defended."""
        p = plan(level=1000.0, discount=2.5, touched=1, touch_low=995.0)
        bars = [bar(1002), bar(995), bar(997.5), bar(998.5)]
        v = evaluate_entry_plan(p, bars, 1001.5)
        assert v == {"action": "fill", "trigger": "confirmed", "price": 1001.5}

    def test_whipsaw_back_to_same_price_without_higher_low_does_not_fill(self):
        """The user's objection to a blind delay: price returns to the same
        number but the path shows lower lows — that is distribution, no fill."""
        p = plan(level=1000.0, discount=2.5, touched=1, touch_low=990.0)
        bars = [bar(1002), bar(995), bar(992), bar(990.2)]  # falling lows
        v = evaluate_entry_plan(p, bars, 1001.0)
        assert v is None

    def test_reclaimed_too_far_waits(self):
        """Price back 0.8% above the level — the entry edge is spent; keep
        stalking for a retest rather than chase."""
        p = plan(level=1000.0, discount=2.5, touched=1, touch_low=995.0)
        bars = [bar(1002), bar(995), bar(998), bar(999)]
        assert evaluate_entry_plan(p, bars, 1008.0) is None

    def test_no_tape_no_confirmation(self):
        p = plan(level=1000.0, discount=2.5, touched=1, touch_low=995.0)
        assert evaluate_entry_plan(p, [], 1001.0) is None


class TestTouchAndRunaway:
    def test_first_touch_records_state(self):
        v = evaluate_entry_plan(plan(level=1000.0), [], 1001.0)
        assert v == {"action": "touch", "touch_low": 1001.0}

    def test_touch_low_ratchets_down(self):
        p = plan(level=1000.0, touched=1, touch_low=998.0)
        v = evaluate_entry_plan(p, [], 996.0)
        assert v == {"action": "touch", "touch_low": 996.0}

    def test_untouch_runaway_cancels(self):
        v = evaluate_entry_plan(plan(level=1000.0), [], 1016.0)
        assert v == {"action": "runaway"}

    def test_brief_pop_above_level_keeps_stalking(self):
        assert evaluate_entry_plan(plan(level=1000.0), [], 1008.0) is None


class TestSplit:
    def test_split_halves(self):
        assert split_quantities(29) == (14, 15)

    def test_tiny_orders_do_not_split(self):
        assert split_quantities(3) == (3, 0)


class TestTrail:
    def pos(self, target=1020.0, high=None):
        return {"take_profit_price": target, "trail_high": high}

    def test_arms_when_target_crossed(self):
        assert evaluate_trail(self.pos(), 1021.0) == {"action": "arm", "trail_high": 1021.0}

    def test_no_arm_below_target(self):
        assert evaluate_trail(self.pos(), 1015.0) is None

    def test_high_ratchets_up(self):
        assert evaluate_trail(self.pos(high=1021.0), 1030.0) == {"action": "raise", "trail_high": 1030.0}

    def test_sells_when_move_comes_off_the_high(self):
        """GRASIM case: target 1020, ran to 1062 — the trail keeps us in the
        rise and sells only when it actually stops."""
        v = evaluate_trail(self.pos(high=1062.0), 1062.0 * (1 - TRAIL_PCT / 100) - 0.5)
        assert v["action"] == "sell"

    def test_holds_while_within_trail(self):
        assert evaluate_trail(self.pos(high=1062.0), 1060.0) is None

    def test_never_gives_back_below_target(self):
        """High barely above target: trail stop would sit below the target,
        but the floor guarantees ~target is the worst exit."""
        v = evaluate_trail(self.pos(target=1020.0, high=1020.5), 1018.5)
        assert v["action"] == "sell"

    def test_no_target_no_trail(self):
        assert evaluate_trail({"take_profit_price": None, "trail_high": None}, 1050.0) is None


class TestPlanEntryTool:
    """plan_entry — the model-facing side of the engine."""

    @pytest.fixture
    def ctx(self, in_memory_db, session_with_watchlist):
        from aaitrade import db
        from aaitrade.tools import entry_plans, trading
        entry_plans.set_session_context(session_with_watchlist, 1)
        # the research gate lives in trading and reads ITS module context
        trading.set_trading_context(None, session_with_watchlist, 1)
        # satisfy the research gate
        db.insert("tool_calls", {
            "session_id": session_with_watchlist, "cycle_number": 1,
            "tool_name": "get_stock_news", "parameters": '{"symbol": "RELIANCE"}',
            "result_summary": "{}", "called_at": db.now_iso(),
        })
        return session_with_watchlist

    def _why(self):
        return ("Sector-wide dip on FII selling, no company news; floor at 980 has "
                "held four times this month and Q1 numbers were a clean beat.")

    def test_plan_created_with_calibrated_discount(self, ctx):
        from unittest.mock import patch
        from aaitrade import db
        from aaitrade.tools.entry_plans import plan_entry
        from tests.conftest import make_price
        candles = []
        for deep in (972, 968, 970):
            candles += [
                {"low": 990, "high": 995, "close": 992},
                {"low": deep, "high": 985, "close": 976},
                {"low": 978, "high": 984, "close": 982},
            ]
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 982)), \
             patch("aaitrade.tools.market.get_price_history", return_value={"candles": candles}):
            r = plan_entry("RELIANCE", 980.0, 10, self._why(), stop_loss_price=940.0)
        assert r["status"] == "created"
        row = db.query_one("SELECT * FROM entry_plans WHERE symbol='RELIANCE'")
        assert row["status"] == "stalking"
        assert row["discount_pct"] > 0

    def test_unresearched_plan_rejected(self, in_memory_db, session_with_watchlist):
        from aaitrade.tools import entry_plans
        entry_plans.set_session_context(session_with_watchlist, 1)
        r = entry_plans.plan_entry("RELIANCE", 980.0, 10, self._why())
        assert r["status"] == "rejected"

    def test_level_too_far_rejected(self, ctx):
        from unittest.mock import patch
        from aaitrade.tools.entry_plans import plan_entry
        from tests.conftest import make_price
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 1100)):
            r = plan_entry("RELIANCE", 980.0, 10, self._why())
        assert r["status"] == "rejected"
        assert "away" in r["reason"]

    def test_new_plan_replaces_old(self, ctx):
        from unittest.mock import patch
        from aaitrade import db
        from aaitrade.tools.entry_plans import plan_entry
        from tests.conftest import make_price
        with patch("aaitrade.tools.market.get_current_price", return_value=make_price("RELIANCE", 982)), \
             patch("aaitrade.tools.market.get_price_history", return_value={"candles": []}):
            plan_entry("RELIANCE", 980.0, 10, self._why())
            plan_entry("RELIANCE", 975.0, 12, self._why())
        rows = db.query("SELECT level, status FROM entry_plans WHERE symbol='RELIANCE' ORDER BY id")
        assert [r["status"] for r in rows] == ["cancelled", "stalking"]
        assert rows[1]["level"] == 975.0
