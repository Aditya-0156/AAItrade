"""Tests for the full-market scanner scoring engine (pure math, no network)."""

import random

import pytest

from aaitrade.scanner import score_symbol, latest_scan_block
from aaitrade import db


def _candles_oscillating(n=120, low_band=1420, high_band=1480, volume=2_000_000, seed=7):
    """Synthetic stock bouncing in a band, currently near the floor."""
    rng = random.Random(seed)
    candles = []
    for i in range(n):
        lo = rng.uniform(low_band - 2, low_band + 15)
        hi = rng.uniform(high_band - 15, high_band + 2)
        close = rng.uniform(lo + 5, hi - 5)
        candles.append({
            "date": f"d{i}", "open": close, "high": round(hi, 2),
            "low": round(lo, 2), "close": round(close, 2), "volume": volume,
        })
    # Force the last close near the bottom of the band (a fresh dip)
    candles[-1]["close"] = low_band + 8
    candles[-1]["low"] = low_band + 2
    candles[-6]["close"] = high_band - 10  # was higher 5 days ago
    return candles


def _candles_falling_knife(n=120, start=2000, volume=2_000_000):
    """Straight-line decline — no floor."""
    candles = []
    price = start
    for i in range(n):
        price *= 0.995
        candles.append({
            "date": f"d{i}", "open": price, "high": round(price * 1.002, 2),
            "low": round(price * 0.998, 2), "close": round(price, 2), "volume": volume,
        })
    return candles


class TestScoring:
    def test_oscillating_dip_scores_high(self):
        result = score_symbol("GOODSTOCK", _candles_oscillating())
        assert result is not None
        assert result["score"] > 50
        assert result["entry_touches"] >= 3
        assert result["target_touches"] >= 3
        assert result["shape"] == "OSCILLATING"
        assert result["gap_pct"] >= 0.8

    def test_falling_knife_rejected(self):
        assert score_symbol("KNIFE", _candles_falling_knife()) is None

    def test_illiquid_rejected(self):
        candles = _candles_oscillating(volume=1_000)  # tiny turnover
        assert score_symbol("ILLIQUID", candles) is None

    def test_price_out_of_range_rejected(self):
        candles = _candles_oscillating(low_band=6000, high_band=6300)
        assert score_symbol("TOOEXPENSIVE", candles) is None

    def test_top_of_band_rejected(self):
        candles = _candles_oscillating()
        # Move the last close to the top of the band
        candles[-1]["close"] = 1478
        candles[-1]["low"] = 1470
        assert score_symbol("LOCALHIGH", candles) is None

    def test_insufficient_data_rejected(self):
        assert score_symbol("NEWLISTING", _candles_oscillating(n=20)) is None


class TestScanBlock:
    def test_empty_scan_block(self, in_memory_db):
        assert "No scan available" in latest_scan_block()

    def test_scan_block_renders(self, in_memory_db):
        db.insert("scan_results", {
            "scan_date": "2026-07-10", "symbol": "RELIANCE", "rank": 1,
            "score": 78.5, "close": 1430.0, "entry_level": 1422.0,
            "entry_touches": 6, "target_level": 1465.0, "target_touches": 4,
            "gap_pct": 2.4, "band_pos": 18.0, "shape": "OSCILLATING",
            "turnover_cr": 250.0, "created_at": db.now_iso(),
        })
        block = latest_scan_block()
        assert "RELIANCE" in block
        assert "1422" in block
        assert "verify" in block.lower()
