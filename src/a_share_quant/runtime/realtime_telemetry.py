"""Fail-closed real-time quality gating and credential-safe telemetry.

The dashboard must not infer that a provider is live merely because one HTTP
request completed.  This module records only operational counters and
timestamps, never provider exception text, request URLs, credentials, or raw
payloads.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import ceil, isfinite
from typing import Any

from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote
from a_share_quant.data.realtime.validation import assess_quote_quality


@dataclass(frozen=True)
class LiveDataQualityReport:
    """Quality state for the current provider snapshot."""

    status: DataQualityStatus
    schema_pass: bool
    continuous_updates: bool
    distinct_update_count: int
    reason: str


class LiveDataQualityGate:
    """Require two fresh, schema-valid, distinct snapshots before GOOD."""

    def __init__(
        self,
        *,
        stale_after_seconds: float = 60.0,
        minimum_distinct_updates: int = 2,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if minimum_distinct_updates < 2:
            raise ValueError("minimum_distinct_updates must be at least two")
        self.stale_after_seconds = float(stale_after_seconds)
        self.minimum_distinct_updates = int(minimum_distinct_updates)
        self._last_signature: tuple[tuple[object, ...], ...] | None = None
        self._distinct_update_count = 0
        self._last_exchange_timestamps: dict[str, datetime] = {}

    def evaluate(
        self,
        quotes: Iterable[RealTimeQuote],
        *,
        provider_connected: bool,
        circuit_breaker_open: bool,
        expected_symbols: Iterable[str] = (),
        now: datetime | None = None,
    ) -> LiveDataQualityReport:
        """Assess a snapshot without accepting an HTTP success as live data."""

        materialized = tuple(quotes)
        if not provider_connected:
            self._reset_continuity()
            return self._report(
                status=DataQualityStatus.FAILED,
                schema_pass=False,
                reason="provider is not connected",
            )
        if circuit_breaker_open:
            self._reset_continuity()
            return self._report(
                status=DataQualityStatus.FAILED,
                schema_pass=False,
                reason="real-time circuit breaker is open",
            )

        reference = _aware(now)
        baseline = assess_quote_quality(
            materialized,
            expected_symbols=expected_symbols,
            now=reference,
            stale_after_seconds=self.stale_after_seconds,
        )
        if baseline.status in {DataQualityStatus.FAILED, DataQualityStatus.STALE}:
            self._reset_continuity()
            return self._report(
                status=baseline.status,
                schema_pass=False,
                reason=baseline.reason,
            )

        schema_pass = baseline.status is DataQualityStatus.GOOD and _schema_passes(
            baseline.quotes,
            now=reference,
        )
        if not schema_pass:
            self._reset_continuity()
            return self._report(
                status=DataQualityStatus.DEGRADED,
                schema_pass=False,
                reason="quote schema or expected-symbol validation did not pass",
            )
        if _timestamps_move_backwards(
            baseline.quotes,
            previous=self._last_exchange_timestamps,
        ):
            self._reset_continuity()
            return self._report(
                status=DataQualityStatus.STALE,
                schema_pass=False,
                reason="exchange timestamp moved backwards",
            )

        signature = _update_signature(baseline.quotes)
        if signature != self._last_signature:
            self._last_signature = signature
            self._distinct_update_count += 1
        for quote in baseline.quotes:
            self._last_exchange_timestamps[quote.symbol] = quote.timestamp_exchange
        if self._distinct_update_count < self.minimum_distinct_updates:
            return self._report(
                status=DataQualityStatus.DEGRADED,
                schema_pass=True,
                reason="awaiting a distinct continuous provider update",
            )
        return self._report(
            status=DataQualityStatus.GOOD,
            schema_pass=True,
            reason="provider, schema, freshness, continuity, and breaker checks passed",
        )

    def _reset_continuity(self) -> None:
        self._last_signature = None
        self._distinct_update_count = 0
        self._last_exchange_timestamps.clear()

    def _report(
        self,
        *,
        status: DataQualityStatus,
        schema_pass: bool,
        reason: str,
    ) -> LiveDataQualityReport:
        return LiveDataQualityReport(
            status=status,
            schema_pass=schema_pass,
            continuous_updates=self._distinct_update_count
            >= self.minimum_distinct_updates,
            distinct_update_count=self._distinct_update_count,
            reason=reason,
        )


def _schema_passes(
    quotes: Iterable[RealTimeQuote],
    *,
    now: datetime,
) -> bool:
    """Check the normalized contract fields required for a live quote."""

    checked = tuple(quotes)
    return bool(checked) and all(
        quote.quality_flag is DataQualityStatus.GOOD
        and quote.last is not None
        and quote.last > 0
        and quote.timestamp_exchange.tzinfo is not None
        and quote.timestamp_received.tzinfo is not None
        and quote.timestamp_exchange <= now
        and quote.timestamp_received <= now
        and bool(quote.source)
        for quote in checked
    )


def _update_signature(
    quotes: Iterable[RealTimeQuote],
) -> tuple[tuple[object, ...], ...]:
    """Return a data-only batch identity used to detect real changes."""

    return tuple(
        sorted(
            (
                quote.symbol,
                quote.timestamp_exchange.isoformat(),
                quote.last,
                quote.volume,
                quote.amount,
            )
            for quote in quotes
        )
    )


def _timestamps_move_backwards(
    quotes: Iterable[RealTimeQuote],
    *,
    previous: dict[str, datetime],
) -> bool:
    latest = dict(previous)
    for quote in quotes:
        prior = latest.get(quote.symbol)
        if prior is not None and quote.timestamp_exchange < prior:
            return True
        latest[quote.symbol] = quote.timestamp_exchange
    return False


@dataclass(frozen=True)
class ProviderTelemetrySnapshot:
    """Sanitized provider counters suitable for logs, reports, and the UI."""

    provider: str | None
    connect_time: datetime | None
    first_quote_time: datetime | None
    last_quote_time: datetime | None
    quote_count: int
    error_count: int
    fallback_count: int
    average_latency_ms: float | None
    p95_latency_ms: float | None
    last_latency_ms: float | None
    stale_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for field in ("connect_time", "first_quote_time", "last_quote_time"):
            value = getattr(self, field)
            payload[field] = value.isoformat() if value is not None else None
        return payload


class ProviderTelemetry:
    """Keep bounded, payload-free telemetry for one workbench process."""

    def __init__(self, *, max_latency_samples: int = 256) -> None:
        if max_latency_samples < 1:
            raise ValueError("max_latency_samples must be positive")
        self._latencies: deque[float] = deque(maxlen=max_latency_samples)
        self._provider: str | None = None
        self._connect_time: datetime | None = None
        self._first_quote_time: datetime | None = None
        self._last_quote_time: datetime | None = None
        self._quote_count = 0
        self._error_count = 0
        self._fallback_count = 0
        self._stale_count = 0

    def record_success(
        self,
        *,
        provider: str,
        quotes: Iterable[RealTimeQuote],
        latency_ms: float,
        observed_at: datetime | None = None,
    ) -> None:
        if not provider:
            raise ValueError("provider is required")
        if not isfinite(float(latency_ms)) or float(latency_ms) < 0:
            raise ValueError("latency_ms must be a finite non-negative number")
        observed = _aware(observed_at)
        materialized = tuple(quotes)
        self._provider = provider
        self._connect_time = self._connect_time or observed
        if materialized:
            self._first_quote_time = self._first_quote_time or observed
            self._last_quote_time = observed
        self._quote_count += len(materialized)
        self._stale_count += sum(
            quote.is_stale or quote.quality_flag is not DataQualityStatus.GOOD
            for quote in materialized
        )
        self._latencies.append(float(latency_ms))

    def record_failure(
        self,
        error: BaseException | None = None,
        *,
        observed_at: datetime | None = None,
    ) -> None:
        """Count an error without retaining the exception or its message."""

        del error
        _aware(observed_at)
        self._error_count += 1

    def record_fallback(self, *, observed_at: datetime | None = None) -> None:
        _aware(observed_at)
        self._fallback_count += 1

    def snapshot(self) -> ProviderTelemetrySnapshot:
        latencies = tuple(self._latencies)
        average = round(sum(latencies) / len(latencies), 3) if latencies else None
        p95 = _p95(latencies)
        return ProviderTelemetrySnapshot(
            provider=self._provider,
            connect_time=self._connect_time,
            first_quote_time=self._first_quote_time,
            last_quote_time=self._last_quote_time,
            quote_count=self._quote_count,
            error_count=self._error_count,
            fallback_count=self._fallback_count,
            average_latency_ms=average,
            p95_latency_ms=p95,
            last_latency_ms=latencies[-1] if latencies else None,
            stale_count=self._stale_count,
        )


def _aware(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return result


def _p95(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(len(ordered) * 0.95) - 1]


__all__ = [
    "LiveDataQualityGate",
    "LiveDataQualityReport",
    "ProviderTelemetry",
    "ProviderTelemetrySnapshot",
]
