"""Strict contracts for public announcement and regulatory risk evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from urllib.parse import urlsplit

from a_share_quant.data.normalization import normalize_symbol


class EventRiskLevel(str, Enum):
    CLEAR = "CLEAR"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PublicRiskEvent:
    event_id: str
    symbol: str
    name: str
    title: str
    announced_at: datetime
    source: str
    source_url: str
    level: EventRiskLevel | str
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.event_id).strip():
            raise ValueError("event_id is required")
        object.__setattr__(self, "event_id", str(self.event_id).strip())
        if len(self.event_id) > 128:
            raise ValueError("event_id is too long")
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        maximum_lengths = {"name": 128, "title": 500, "source": 40, "source_url": 2048}
        for field, maximum_length in maximum_lengths.items():
            value = str(getattr(self, field)).strip()
            if not value:
                raise ValueError(f"{field} is required")
            if len(value) > maximum_length:
                raise ValueError(f"{field} is too long")
            object.__setattr__(self, field, value)
        url = urlsplit(self.source_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("source_url must be an HTTPS URL without credentials")
        if self.source.upper() == "CNINFO" and url.hostname != "static.cninfo.com.cn":
            raise ValueError("CNINFO event URL must use the official static host")
        object.__setattr__(
            self,
            "announced_at",
            _aware_utc(self.announced_at, field="announced_at"),
        )
        level = self.level if isinstance(self.level, EventRiskLevel) else EventRiskLevel(self.level)
        object.__setattr__(self, "level", level)
        reasons = tuple(dict.fromkeys(str(item).strip().upper() for item in self.reason_codes))
        if any(not item for item in reasons):
            raise ValueError("reason codes must be non-empty")
        object.__setattr__(self, "reason_codes", reasons)

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "name": self.name,
            "title": self.title,
            "announced_at": self.announced_at.isoformat(),
            "source": self.source,
            "source_url": self.source_url,
            "level": self.level.value,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class PublicRiskAssessment:
    symbol: str
    name: str
    level: EventRiskLevel | str
    reason_codes: tuple[str, ...]
    events: tuple[PublicRiskEvent, ...]
    checked_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "name", str(self.name).strip() or self.symbol)
        level = self.level if isinstance(self.level, EventRiskLevel) else EventRiskLevel(self.level)
        object.__setattr__(self, "level", level)
        reasons = tuple(dict.fromkeys(str(item).strip().upper() for item in self.reason_codes))
        if any(not item for item in reasons):
            raise ValueError("reason codes must be non-empty")
        object.__setattr__(self, "reason_codes", reasons)
        events = tuple(self.events)
        if any(event.symbol != self.symbol for event in events):
            raise ValueError("assessment events must match the assessment symbol")
        object.__setattr__(
            self,
            "events",
            tuple(sorted(events, key=lambda item: item.announced_at, reverse=True)),
        )
        object.__setattr__(self, "checked_at", _aware_utc(self.checked_at, field="checked_at"))

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "level": self.level.value,
            "reason_codes": list(self.reason_codes),
            "events": [event.to_dict() for event in self.events],
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass(frozen=True)
class PublicRiskSnapshot:
    source: str
    fetched_at: datetime
    window_start: date
    window_end: date
    status: str
    notice_zh: str
    assessments: tuple[PublicRiskAssessment, ...]

    def __post_init__(self) -> None:
        source = str(self.source).strip()
        if not source:
            raise ValueError("source is required")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "fetched_at", _aware_utc(self.fetched_at, field="fetched_at"))
        if not isinstance(self.window_start, date) or not isinstance(self.window_end, date):
            raise ValueError("risk window must use dates")
        if self.window_start > self.window_end:
            raise ValueError("risk window is inverted")
        status = str(self.status).strip().upper()
        if status not in {"FRESH", "PARTIAL"}:
            raise ValueError("successful public risk snapshot must be FRESH or PARTIAL")
        object.__setattr__(self, "status", status)
        notice = str(self.notice_zh).strip()
        if not notice:
            raise ValueError("notice_zh is required")
        object.__setattr__(self, "notice_zh", notice)
        assessments = tuple(self.assessments)
        symbols = [item.symbol for item in assessments]
        if len(symbols) != len(set(symbols)):
            raise ValueError("public risk assessments contain duplicate symbols")
        object.__setattr__(
            self,
            "assessments",
            tuple(sorted(assessments, key=lambda item: item.symbol)),
        )

    def by_symbol(self) -> dict[str, PublicRiskAssessment]:
        return {item.symbol: item for item in self.assessments}

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat(),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "status": self.status,
            "notice_zh": self.notice_zh,
            "assessments": [item.to_dict() for item in self.assessments],
        }
