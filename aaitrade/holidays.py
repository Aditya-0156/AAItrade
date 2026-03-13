"""NSE holiday calendar — skip non-trading days.

Maintains a list of NSE holidays for the current year.
Checks weekends + holidays before running trading cycles.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

# NSE holidays for 2026 (update annually or fetch dynamically in Phase 2)
# Source: NSE India circular
NSE_HOLIDAYS_2026 = [
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 10),   # Maha Shivaratri
    date(2026, 3, 30),   # Holi
    date(2026, 3, 31),   # Id-Ul-Fitr (tentative)
    date(2026, 4, 2),    # Ram Navami
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 6, 7),    # Id-Ul-Adha (Bakri Id) (tentative)
    date(2026, 7, 7),    # Muharram (tentative)
    date(2026, 8, 15),   # Independence Day
    date(2026, 9, 5),    # Milad-un-Nabi (tentative)
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 10, 21),  # Dussehra (additional)
    date(2026, 11, 9),   # Diwali (Laxmi Pujan)
    date(2026, 11, 10),  # Diwali (Balipratipada)
    date(2026, 11, 30),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
]

# Keep a dict for multi-year support
_HOLIDAYS: dict[int, list[date]] = {
    2026: NSE_HOLIDAYS_2026,
}


def is_trading_day(check_date: date | None = None) -> bool:
    """Check if a given date is a trading day (not weekend, not holiday).

    Args:
        check_date: Date to check. Defaults to today.

    Returns:
        True if it's a valid trading day.
    """
    if check_date is None:
        check_date = date.today()

    # Weekends
    if check_date.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.debug(f"{check_date} is a weekend")
        return False

    # Holidays
    year_holidays = _HOLIDAYS.get(check_date.year, [])
    if check_date in year_holidays:
        logger.info(f"{check_date} is an NSE holiday")
        return False

    return True


def next_trading_day(from_date: date | None = None) -> date:
    """Find the next trading day from a given date."""
    if from_date is None:
        from_date = date.today()

    from datetime import timedelta
    candidate = from_date + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def add_holidays(year: int, holidays: list[date]):
    """Add holidays for a specific year (for future updates)."""
    _HOLIDAYS[year] = holidays
