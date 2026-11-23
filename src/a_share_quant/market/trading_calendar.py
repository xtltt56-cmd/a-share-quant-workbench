"""Deterministic A-share session calendar with exchange-published closures."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo


class CalendarUnavailableError(ValueError):
    """Official session evidence is unavailable; callers must fail closed."""


@lru_cache(maxsize=1)
def _exchange_calendar() -> tuple[str, str, tuple[int, ...], tuple[str, ...], frozenset[date]]:
    try:
        raw = files(__package__).joinpath("exchange_calendar.json").read_bytes()
        payload = json.loads(raw)
        years = payload["years"]
        sources: list[str] = []
        closures: set[date] = set()
        version = payload["version"]
        if not isinstance(version, str) or not version or not years:
            raise ValueError("invalid exchange calendar metadata")
        for year, record in years.items():
            year_sources = record["source_urls"]
            year_closures = [date.fromisoformat(value) for value in record["closed_dates"]]
            if (
                str(int(year)) != year
                or not isinstance(year_sources, list)
                or not year_sources
                or not all(isinstance(url, str) and url.startswith("https://")
                           for url in year_sources)
                or any(day.year != int(year) for day in year_closures)
                or len(set(year_closures)) != len(year_closures)
            ):
                raise ValueError("invalid exchange calendar year")
            sources.extend(year_sources)
            closures.update(year_closures)
        return (
            version,
            hashlib.sha256(raw).hexdigest(),
            tuple(sorted(map(int, years))),
            tuple(dict.fromkeys(sources)),
            frozenset(closures),
        )
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise CalendarUnavailableError("packaged exchange calendar is unavailable") from exc


class AShareTradingCalendar:
    """Resolve sessions without silently treating exchange holidays as open."""

    def __init__(self, *, closed_dates: set[date] | frozenset[date] | None = None) -> None:
        if closed_dates is None:
            (
                self._version, self._digest, self._years, self._sources, self.closed_dates
            ) = _exchange_calendar()
        else:
            self.closed_dates = frozenset(closed_dates)
            dates = ",".join(day.isoformat() for day in sorted(self.closed_dates))
            self._version = "custom-v1"
            self._digest = hashlib.sha256(dates.encode("ascii")).hexdigest()
            self._years = None
            self._sources = ()

    def metadata(self) -> dict[str, object]:
        return {
            "version": self._version,
            "sha256": self._digest,
            "supported_years": list(self._years) if self._years is not None else None,
            "source_urls": list(self._sources),
        }

    def coverage(self, as_of: date, *, limit: int = 20) -> dict[str, object]:
        """Count verified upcoming sessions up to the operator warning threshold."""
        next_day = as_of
        for count in range(limit):
            try:
                next_day = self.next_session(next_day)
            except CalendarUnavailableError:
                return {"lookahead_sessions": count, "coverage_warning": True}
        return {"lookahead_sessions": limit, "coverage_warning": False}

    def is_session(self, day: date) -> bool:
        if self._years is not None and day.year not in self._years:
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

    def prediction_deadline(self, signal_date: date) -> datetime:
        """Close the prospective capture window at the next exchange opening."""
        return datetime.combine(self.next_session(signal_date), time(9, 30),
                                tzinfo=ZoneInfo("Asia/Shanghai"))

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
