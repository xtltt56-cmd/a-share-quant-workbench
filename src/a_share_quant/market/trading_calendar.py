"""Deterministic A-share session calendar with exchange-published closures."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

_CLOSED_2026 = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 2),
        *{date(2026, 2, day) for day in range(16, 24)},
        date(2026, 4, 6),
        *{date(2026, 5, day) for day in range(1, 6)},
        date(2026, 6, 19),
        date(2026, 9, 25),
        *{date(2026, 10, day) for day in range(1, 8)},
    }
)


class CalendarUnavailableError(ValueError):
    """Official session evidence is unavailable; callers must fail closed."""


class AShareTradingCalendar:
    """Resolve sessions without silently treating exchange holidays as open."""

    def __init__(self, *, closed_dates: set[date] | frozenset[date] | None = None) -> None:
        self._custom = closed_dates is not None
        self.closed_dates = frozenset(closed_dates) if self._custom else _CLOSED_2026

    def metadata(self) -> dict[str, object]:
        dates = ",".join(day.isoformat() for day in sorted(self.closed_dates))
        return {
            "version": "custom-v1" if self._custom else "ashare-exchange-2026-v1",
            "sha256": hashlib.sha256(dates.encode("ascii")).hexdigest(),
            "supported_years": None if self._custom else [2026],
            "source_urls": [] if self._custom else [
                "https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml",
                "https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html",
            ],
        }

    def is_session(self, day: date) -> bool:
        if not self._custom and day.year != 2026:
            raise CalendarUnavailableError(
                "exchange-published calendar is unavailable for this year"
            )
        return day.weekday() < 5 and day not in self.closed_dates

    def is_trading_day(self, day: date) -> bool:
        """Shared protocol used by the live scheduler."""

        return self.is_session(day)

    def latest_completed_session(self, now: datetime, *, close_time: time = time(15)) -> date:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("calendar clock must be timezone-aware")
        local = now.astimezone(ZoneInfo("Asia/Shanghai"))
        day = local.date()
        if self.is_session(day) and local.time() >= close_time:
            return day
        return self.previous_session(day)

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
