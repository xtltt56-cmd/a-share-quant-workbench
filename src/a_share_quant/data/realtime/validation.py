"""Real-time freshness, timestamp and circuit-breaker validation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote
from a_share_quant.data.normalization import normalize_symbol


@dataclass(frozen=True)
class QuoteQualityReport:
    status: DataQualityStatus
    quotes: tuple[RealTimeQuote, ...]
    missing_symbols: tuple[str, ...]
    quarantined_symbols: tuple[str, ...]
    is_usable: bool
    can_generate_ready: bool
    reason: str


def assess_quote_quality(
    quotes: Iterable[RealTimeQuote],
    *,
    expected_symbols: Iterable[str] = (),
    now: datetime | None = None,
    stale_after_seconds: float = 60.0,
) -> QuoteQualityReport:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive")
    expected = tuple(dict.fromkeys(normalize_symbol(symbol) for symbol in expected_symbols))
    checked: list[RealTimeQuote] = []
    stale = False
    quarantined: list[str] = []
    for quote in quotes:
        age = quote.data_age_seconds(now=reference)
        if age > stale_after_seconds or quote.is_stale:
            stale = True
            checked.append(
                replace(quote, is_stale=True, quality_flag=DataQualityStatus.STALE)
            )
        else:
            checked.append(quote)
    received_symbols = {quote.symbol for quote in checked if not quote.is_stale}
    missing = tuple(symbol for symbol in expected if symbol not in received_symbols)
    if not checked:
        status = DataQualityStatus.FAILED
        reason = "no quotes received"
    elif stale:
        status = DataQualityStatus.STALE
        reason = "one or more quotes exceeded the configured freshness threshold"
    elif missing or quarantined:
        status = DataQualityStatus.DEGRADED
        reason = "expected symbols or validated fields are missing"
    else:
        status = DataQualityStatus.GOOD
        reason = "all received quotes passed freshness checks"
    return QuoteQualityReport(
        status=status,
        quotes=tuple(checked),
        missing_symbols=missing,
        quarantined_symbols=tuple(quarantined),
        is_usable=status in {DataQualityStatus.GOOD, DataQualityStatus.DEGRADED},
        can_generate_ready=status is DataQualityStatus.GOOD and not missing and not quarantined,
        reason=reason,
    )


class TimestampTracker:
    """Reject timestamps that move backwards or arrive from the future."""

    def __init__(self, *, future_tolerance_seconds: float = 0.0) -> None:
        self.future_tolerance_seconds = max(0.0, future_tolerance_seconds)
        self._last_seen: dict[str, datetime] = {}

    def observe(self, symbol: str, timestamp: datetime, *, now: datetime | None = None) -> None:
        normalized = normalize_symbol(symbol)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if (timestamp - reference).total_seconds() > self.future_tolerance_seconds:
            raise ValueError("timestamp is in the future")
        previous = self._last_seen.get(normalized)
        if previous is not None and timestamp < previous:
            raise ValueError("timestamp moved backwards")
        self._last_seen[normalized] = timestamp


class RealtimeCircuitBreaker:
    """Open when freshness is unsafe and close only after good data returns."""

    def __init__(self, *, stale_after_seconds: float = 60.0) -> None:
        self.stale_after_seconds = stale_after_seconds
        self.state = "CLOSED"

    def evaluate(
        self,
        quotes: Iterable[RealTimeQuote],
        *,
        expected_symbols: Iterable[str] = (),
        now: datetime | None = None,
    ) -> QuoteQualityReport:
        report = assess_quote_quality(
            quotes,
            expected_symbols=expected_symbols,
            now=now,
            stale_after_seconds=self.stale_after_seconds,
        )
        if report.status in {DataQualityStatus.STALE, DataQualityStatus.FAILED}:
            self.state = "OPEN"
        elif report.status is DataQualityStatus.GOOD:
            self.state = "CLOSED"
        return report
