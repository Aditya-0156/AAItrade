"""Tests for holidays.py — NSE trading calendar."""

from datetime import date

import pytest

from aaitrade.holidays import is_trading_day, next_trading_day, NSE_HOLIDAYS_2026


class TestIsTradingDay:
    def test_monday_is_trading_day(self):
        # 2026-03-16 is a Monday
        assert is_trading_day(date(2026, 3, 16)) is True

    def test_saturday_is_not_trading_day(self):
        # 2026-03-14 is a Saturday
        assert is_trading_day(date(2026, 3, 14)) is False

    def test_sunday_is_not_trading_day(self):
        # 2026-03-15 is a Sunday
        assert is_trading_day(date(2026, 3, 15)) is False

    def test_republic_day_is_holiday(self):
        # Republic Day = Jan 26
        assert is_trading_day(date(2026, 1, 26)) is False

    def test_independence_day_is_holiday(self):
        # Independence Day = Aug 15
        assert is_trading_day(date(2026, 8, 15)) is False

    def test_christmas_is_holiday(self):
        # Christmas = Dec 25
        assert is_trading_day(date(2026, 12, 25)) is False

    def test_regular_weekday_not_on_holiday_list(self):
        # Tuesday March 17, 2026 — not a holiday
        assert is_trading_day(date(2026, 3, 17)) is True

    def test_holiday_list_not_empty(self):
        assert len(NSE_HOLIDAYS_2026) >= 10


class TestNextTradingDay:
    def test_next_trading_day_from_friday_is_monday(self):
        # Friday March 13, 2026 → Monday March 16, 2026
        result = next_trading_day(date(2026, 3, 13))
        assert result == date(2026, 3, 16)

    def test_next_trading_day_from_saturday_is_monday(self):
        result = next_trading_day(date(2026, 3, 14))
        assert result == date(2026, 3, 16)

    def test_next_trading_day_from_sunday_is_monday(self):
        result = next_trading_day(date(2026, 3, 15))
        assert result == date(2026, 3, 16)

    def test_next_trading_day_skips_holiday(self):
        # Day before Republic Day (Jan 25) → should skip Jan 26 (holiday)
        result = next_trading_day(date(2026, 1, 25))
        assert result != date(2026, 1, 26)
        assert is_trading_day(result) is True
