"""CONVICTION mode: big-target research trading.

Covers the amplitude maths that would have prevented the WELSPUNLIV mistake,
the multi-day pipeline, and the mode's own risk profile.
"""

import pytest

from aaitrade import db
from aaitrade.config import TradingMode, RISK_PROFILES, SessionConfig, ExecutionMode
from aaitrade.tools.amplitude import analyse_amplitude
from aaitrade.tools import pipeline


def _candles(n, start, daily_move_pct, drift_pct=0.0, seed=3):
    """Synthetic series with a controllable daily amplitude."""
    import random
    rng = random.Random(seed)
    out, price = [], start
    for i in range(n):
        price *= (1 + drift_pct / 100)
        move = rng.uniform(-daily_move_pct, daily_move_pct) / 100
        close = price * (1 + move)
        out.append({
            "date": f"d{i}", "open": price,
            "high": round(max(price, close) * (1 + daily_move_pct / 200), 2),
            "low": round(min(price, close) * (1 - daily_move_pct / 200), 2),
            "close": round(close, 2), "volume": 1_000_000,
        })
        price = close
    return out


class TestAmplitude:
    def test_target_inside_noise_is_caught(self):
        """The WELSPUNLIV failure: 1.4% target on a stock that swings ~4% against you."""
        c = _candles(200, 160, daily_move_pct=3.5)
        r = analyse_amplitude(c, target_pct=1.4, horizon_days=10)
        assert r["verdict"] == "TARGET_INSIDE_NOISE"
        assert r["reward_to_adverse_ratio"] < 1.0

    def test_same_stock_bigger_target_is_viable(self):
        """Same stock, right-sized target — the whole point of this session."""
        c = _candles(200, 160, daily_move_pct=3.5)
        r = analyse_amplitude(c, target_pct=8, horizon_days=10)
        assert r["verdict"] in ("VIABLE", "THIN_EDGE")
        assert r["reward_to_adverse_ratio"] > 1.0

    def test_slow_stock_cannot_deliver_big_move_fast(self):
        c = _candles(200, 700, daily_move_pct=0.6)
        r = analyse_amplitude(c, target_pct=10, horizon_days=5)
        assert r["verdict"] == "TOO_SLOW"
        assert r["physically_reachable"] is False

    def test_suggested_stop_sits_outside_normal_noise(self):
        c = _candles(200, 160, daily_move_pct=3.5)
        r = analyse_amplitude(c, target_pct=8, horizon_days=10)
        assert r["suggested_stop_pct"] > abs(r["median_adverse_pct"])

    def test_insufficient_history_handled(self):
        assert "error" in analyse_amplitude(_candles(20, 100, 2.0), 5, 10)


class TestConvictionRiskProfile:
    def test_percentage_based_and_scales_with_capital(self):
        r = RISK_PROFILES[TradingMode.CONVICTION]
        assert r.max_per_trade == 40.0 and r.max_positions == 6
        assert r.stop_loss == 10.0
        for capital in (100_000, 200_000):
            assert capital * r.max_per_trade / 100 == capital * 0.4

    def test_hard_cap_sits_beyond_the_stop(self):
        """The stop must fire before the force-exit, even at full size."""
        r = RISK_PROFILES[TradingMode.CONVICTION]
        loss_at_stop_pct_of_capital = (r.max_per_trade / 100) * (r.stop_loss / 100) * 100
        assert loss_at_stop_pct_of_capital < r.max_position_loss_pct

    def test_take_profit_is_discretionary(self):
        assert RISK_PROFILES[TradingMode.CONVICTION].take_profit == 0.0


class TestPipeline:
    @pytest.fixture
    def wired(self, in_memory_db, session_with_watchlist):
        pipeline.set_session_id(session_with_watchlist)
        return session_with_watchlist

    def test_candidate_created_and_advanced(self, wired):
        r = pipeline.update_candidate("TATAPOWER", "watching", "Down 9% on a sector selloff.")
        assert r["status"] == "created"
        r2 = pipeline.update_candidate(
            "TATAPOWER", "researching", "Q2 results intact; the fall is sector-wide.",
            thesis="Sector selloff, business unaffected", conviction=7,
        )
        assert r2["status"] == "updated" and r2["stage_changed"] is True

    def test_findings_accumulate_across_cycles(self, wired):
        pipeline.update_candidate("X", "watching", "First observation")
        pipeline.update_candidate("X", "researching", "Second observation")
        rows = pipeline.get_pipeline()["candidates"]
        assert "First observation" in rows[0]["findings"]
        assert "Second observation" in rows[0]["findings"]

    def test_pipeline_shows_only_live_work(self, wired):
        pipeline.update_candidate("A", "ready", "done researching")
        pipeline.update_candidate("B", "entered", "bought")
        pipeline.update_candidate("C", "rejected", "structural damage — avoid")
        syms = {c["symbol"] for c in pipeline.get_pipeline()["candidates"]}
        assert syms == {"A"}, "entered/rejected are history, not working set"

    def test_ready_sorts_above_watching(self, wired):
        pipeline.update_candidate("WATCH", "watching", "n")
        pipeline.update_candidate("READY", "ready", "n", conviction=6)
        assert pipeline.get_pipeline()["candidates"][0]["symbol"] == "READY"

    def test_invalid_stage_rejected(self, wired):
        assert pipeline.update_candidate("X", "maybe", "n")["status"] == "rejected"

    def test_rejection_reason_is_retained(self, wired):
        pipeline.update_candidate("BAD", "rejected", "Lost their biggest customer — structural.")
        rows = pipeline.get_pipeline(stage="rejected")["candidates"]
        assert "biggest customer" in rows[0]["findings"]


class TestConvictionPrompt:
    def test_uses_its_own_prompt(self, in_memory_db, session_with_watchlist):
        from aaitrade.context_builder import ContextBuilder
        cfg = SessionConfig(
            execution_mode=ExecutionMode.LIVE, trading_mode=TradingMode.CONVICTION,
            starting_capital=100000, total_days=99999,
            watchlist_path="config/watchlist_seed.yaml",
        )
        p = ContextBuilder(cfg, session_with_watchlist).build_system_prompt()
        assert "AAItrade Conviction" in p
        assert "analyse_amplitude" in p
        assert "smallest loss available" in p
        assert "Never 1-2%" in p

    def test_other_modes_keep_the_original_prompt(self, in_memory_db, session_with_watchlist, balanced_config):
        from aaitrade.context_builder import ContextBuilder
        p = ContextBuilder(balanced_config, session_with_watchlist).build_system_prompt()
        assert "AAItrade Conviction" not in p
