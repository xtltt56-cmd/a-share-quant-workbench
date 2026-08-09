"""Provider-neutral contracts for real-time market monitoring.

The contracts deliberately contain no provider SDK imports. Optional data
sources translate into these objects before the runtime, feature, or dashboard
layers can consume them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Any

from a_share_quant.data.normalization import normalize_symbol


class DataQualityStatus(str, Enum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    FAILED = "FAILED"

    @classmethod
    def parse(cls, value: str | DataQualityStatus) -> DataQualityStatus:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).upper())
        except ValueError as exc:
            raise ValueError(f"invalid data quality status: {value}") from exc


def _ensure_aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _optional_number(value: float | int | None, *, field: str, positive: bool = False) -> None:
    if value is None:
        return
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{field} must be finite")
    if positive and numeric <= 0:
        raise ValueError(f"{field} must be positive")
    if not positive and field in {"volume", "amount"} and numeric < 0:
        raise ValueError(f"{field} cannot be negative")


@dataclass(frozen=True)
class RealTimeQuote:
    """A normalized quote snapshot; absent provider fields remain ``None``."""

    symbol: str
    market: str
    timestamp_exchange: datetime
    timestamp_received: datetime
    last: float | None
    source: str
    name: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    bid1: float | None = None
    ask1: float | None = None
    change: float | None = None
    change_pct: float | None = None
    turnover_rate: float | None = None
    quality_flag: DataQualityStatus | str = DataQualityStatus.GOOD
    is_stale: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "quality_flag", DataQualityStatus.parse(self.quality_flag))
        _ensure_aware(self.timestamp_exchange, field="timestamp_exchange")
        _ensure_aware(self.timestamp_received, field="timestamp_received")
        if not self.market or not self.source:
            raise ValueError("market and source are required")
        for field in ("last", "open", "high", "low", "previous_close"):
            _optional_number(getattr(self, field), field=field, positive=True)
        for field in ("volume", "amount"):
            _optional_number(getattr(self, field), field=field)
        for field in ("bid1", "ask1", "change", "change_pct", "turnover_rate"):
            _optional_number(getattr(self, field), field=field)
        if self.high is not None and self.low is not None and self.high < self.low:
            raise ValueError("high must not be lower than low")
        if self.is_stale and self.quality_flag is DataQualityStatus.GOOD:
            object.__setattr__(self, "quality_flag", DataQualityStatus.STALE)

    def data_age_seconds(self, *, now: datetime | None = None) -> float:
        reference = _ensure_aware(now or datetime.now(timezone.utc), field="now")
        return max(0.0, (reference - self.timestamp_exchange).total_seconds())

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["quality_flag"] = self.quality_flag.value
        result["timestamp_exchange"] = self.timestamp_exchange.isoformat()
        result["timestamp_received"] = self.timestamp_received.isoformat()
        return result


@dataclass(frozen=True)
class MinuteBar:
    """A normalized intraday bar with an explicit final/provisional flag."""

    symbol: str
    timestamp: datetime
    frequency: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    source: str
    is_final: bool
    quality_flag: DataQualityStatus | str = DataQualityStatus.GOOD

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "quality_flag", DataQualityStatus.parse(self.quality_flag))
        _ensure_aware(self.timestamp, field="timestamp")
        if not self.frequency or self.frequency.lower() not in {"1m", "5m", "15m", "30m", "60m"}:
            raise ValueError("frequency must be one of 1m, 5m, 15m, 30m, 60m")
        if not self.source:
            raise ValueError("source is required")
        for field in ("open", "high", "low", "close"):
            _optional_number(getattr(self, field), field=field, positive=True)
        _optional_number(self.volume, field="volume")
        _optional_number(self.amount, field="amount")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")

    @property
    def is_provisional(self) -> bool:
        return not self.is_final

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["quality_flag"] = self.quality_flag.value
        result["timestamp"] = self.timestamp.isoformat()
        return result


@dataclass(frozen=True)
class MarketSnapshot:
    timestamp_exchange: datetime
    timestamp_received: datetime
    quotes: tuple[RealTimeQuote, ...]
    source: str
    quality_flag: DataQualityStatus | str = DataQualityStatus.GOOD
    is_stale: bool = False

    def __post_init__(self) -> None:
        _ensure_aware(self.timestamp_exchange, field="timestamp_exchange")
        _ensure_aware(self.timestamp_received, field="timestamp_received")
        object.__setattr__(self, "quotes", tuple(self.quotes))
        object.__setattr__(self, "quality_flag", DataQualityStatus.parse(self.quality_flag))
        if not self.source:
            raise ValueError("source is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_exchange": self.timestamp_exchange.isoformat(),
            "timestamp_received": self.timestamp_received.isoformat(),
            "quotes": [quote.to_dict() for quote in self.quotes],
            "source": self.source,
            "quality_flag": self.quality_flag.value,
            "is_stale": self.is_stale,
        }


@dataclass(frozen=True)
class ProviderMetadata:
    provider: str
    market: str
    frequencies: tuple[str, ...]
    authenticated: bool
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    connected: bool
    authenticated: bool
    permissions: tuple[str, ...]
    status: str
    latency_ms: float | None = None
    last_update: datetime | None = None
    error_count: int = 0
    message: str = ""


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    authenticated: bool
    market: str
    frequency: str
    latency_ms: float | None
    last_update: datetime | None
    permissions: tuple[str, ...]
    status: str
    message: str = ""


@dataclass(frozen=True)
class ProviderSwitchEvent:
    source_from: str
    source_to: str
    reason: str
    timestamp: datetime
