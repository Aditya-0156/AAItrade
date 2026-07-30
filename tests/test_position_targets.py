"""update_position_targets — letting a winner run, safely.

The rules that matter: stops ratchet up only, extending a target demands
breakeven protection, and pushing past the usual 1-2% band must be earned.
"""

import pytest
from unittest.mock import patch

from aaitrade import db
from aaitrade.executor import Executor
from aaitrade.tools import trading

STRONG = ("Volume 3.2x the 20-day average and it just cleared 352 which capped it four "
          "separate times since May; the whole PSU insurance pack is bid today after the "
          "regulator's pricing circular, so there is room to the next shelf at 366.")
OK_EVIDENCE = "Broke above the level that capped it since May on triple average volume."


@pytest.fixture
def held(in_memory_db, balanced_config, session_with_watchlist):
    ex = Executor(balanced_config, session_with_watchlist)
    trading.set_trading_context(ex, session_with_watchlist, 1)
    db.insert("portfolio", {
        "session_id": session_with_watchlist, "symbol": "GICRE",
        "quantity": 100, "avg_price": 357.0,
        "stop_loss_price": 338.0, "take_profit_price": 362.0,
        "opened_at": db.now_iso(),
    })
    return session_with_watchlist


def _pos(sid):
    return db.query_one(
        "SELECT stop_loss_price, take_profit_price FROM portfolio "
        "WHERE session_id = ? AND symbol = 'GICRE'", (sid,))


class TestStopRatchet:
    def test_stop_can_move_up(self, held):
        r = trading.update_position_targets("GICRE", OK_EVIDENCE, stop_loss_price=357.0)
        assert r["status"] == "updated"
        assert _pos(held)["stop_loss_price"] == 357.0

    def test_stop_cannot_move_down(self, held):
        r = trading.update_position_targets("GICRE", OK_EVIDENCE, stop_loss_price=330.0)
        assert r["status"] == "rejected"
        assert "only move UP" in r["reason"]
        assert _pos(held)["stop_loss_price"] == 338.0


class TestExtendRequiresProtection:
    def test_extending_target_without_breakeven_stop_blocked(self, held):
        r = trading.update_position_targets("GICRE", OK_EVIDENCE, take_profit_price=363.5)
        assert r["status"] == "rejected"
        assert "breakeven" in r["reason"]
        assert _pos(held)["take_profit_price"] == 362.0, "must not change on rejection"

    def test_extending_with_breakeven_stop_allowed(self, held):
        r = trading.update_position_targets(
            "GICRE", OK_EVIDENCE, take_profit_price=363.5, stop_loss_price=357.0)
        assert r["status"] == "updated"
        p = _pos(held)
        assert p["take_profit_price"] == 363.5 and p["stop_loss_price"] == 357.0

    def test_lowering_target_to_bank_sooner_is_free(self, held):
        """Taking profit earlier is never blocked — only greed is."""
        r = trading.update_position_targets("GICRE", OK_EVIDENCE, take_profit_price=360.0)
        assert r["status"] == "updated"


class TestGoalMarginBand:
    def test_within_band_needs_only_normal_evidence(self, held):
        # 361.5 is ~1.3% above entry 357 — inside the usual band
        r = trading.update_position_targets(
            "GICRE", OK_EVIDENCE, take_profit_price=361.5, stop_loss_price=357.0)
        assert r["status"] == "updated"

    def test_beyond_band_needs_strong_evidence(self, held):
        # 366 is ~2.5% above entry — beyond the usual band
        r = trading.update_position_targets(
            "GICRE", OK_EVIDENCE, take_profit_price=366.0, stop_loss_price=357.0)
        assert r["status"] == "rejected"
        assert "needs real evidence" in r["reason"]

    def test_beyond_band_allowed_with_strong_evidence(self, held):
        """The user's rule: above 1-2% IS allowed when properly justified."""
        r = trading.update_position_targets(
            "GICRE", STRONG, take_profit_price=366.0, stop_loss_price=357.0)
        assert r["status"] == "updated"
        assert "above the usual" in r["message"]
        assert _pos(held)["take_profit_price"] == 366.0


class TestEvidenceQuality:
    def test_thin_evidence_rejected(self, held):
        r = trading.update_position_targets("GICRE", "going up", stop_loss_price=357.0)
        assert r["status"] == "rejected"

    def test_metric_restatement_rejected(self, held):
        junk = ("Entry has 9 touches in 30d demonstrated floor, band position 12%, "
                "net profit 1.4% after charges.")
        r = trading.update_position_targets("GICRE", junk, stop_loss_price=357.0)
        assert r["status"] == "rejected"
        assert "restates the metrics" in r["reason"]

    def test_unknown_position_rejected(self, held):
        r = trading.update_position_targets("INFY", STRONG, stop_loss_price=100.0)
        assert r["status"] == "rejected"
        assert "no position" in r["reason"]
