"""Deterministic A-share session calendar with exchange-published closures."""

from __future__ import annotations

from datetime import date, timedelta

_CLOSED_2026 = frozenset(
    {
        date(2026, 1, 1), date(2026, 1, 2),
        *{date(2026, 2, day) for day in range(16, 24)},
        date(2026, 4, 6),
        *{date(2026, 5, day) for day in range(1, 6)},
        date(2026, 6, 19),
        date(2026, 9, 25),
        *{date(2026, 10, day) for day in range(1, 8)},
    }
)


class AShareTradingCalendar:
    """Resolve sessions without silently treating exchange holidays as open."""

    def __init__(self, *, closed_dates: set[date] | frozenset[date] | None = None) -> None:
        self._custom = closed_dates is not None
        self.closed_dates = frozenset(closed_dates) if self._custom else _CLOSED_2026

    def is_session(self, day: date) -> bool:
        if not self._custom and day.year != 2026:
            raise ValueError("exchange-published calendar is unavailable for this year")
        return day.weekday() < 5 and day not in self.closed_dates

    def next_session(self, day: date) -> date:
        candidate = day + timedelta(days=1)
        while not self.is_session(candidate):
            candidate += timedelta(days=1)
        return candidate

    def previous_session(self, day: date) -> date:
        candidate = day - timedelta(days=1)
        while not self.is_session(candidate):
            candidate -= timedelta(days=1)
        return candidate
