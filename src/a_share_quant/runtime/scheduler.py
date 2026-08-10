"""Trading-session-aware real-time polling with a freshness circuit breaker."""

from __future__ import annotations

import time as time_module
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from a_share_quant.contracts.realtime import DataQualityStatus, MinuteBar
from a_share_quant.data.realtime.base import RealTimeDataProvider
from a_share_quant.data.realtime.validation import (
    RealtimeCircuitBreaker,
    TimestampTracker,
    assess_quote_quality,
)
from a_share_quant.storage.realtime_store import RealTimeStore


class MarketSession(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    OPEN = "OPEN"
    LUNCH_BREAK = "LUNCH_BREAK"
    CLOSED = "CLOSED"
    NON_TRADING = "NON_TRADING"


@dataclass(frozen=True)
class MarketHours:
    timezone: str = "Asia/Shanghai"
    pre_market_start: time = time(9, 0)
    open_start: time = time(9, 30)
    morning_end: time = time(11, 30)
    afternoon_start: time = time(13, 0)
    close_end: time = time(15, 0)

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> MarketHours:
        parsed = dict(values)
        for field in (
            "pre_market_start",
            "open_start",
            "morning_end",
            "afternoon_start",
            "close_end",
        ):
            value = parsed.get(field)
            if isinstance(value, str):
                hour, minute = value.split(":", maxsplit=1)
                parsed[field] = time(int(hour), int(minute))
        return cls(**parsed)


class TradingCalendar(Protocol):
    def is_trading_day(self, value: date) -> bool:
        ...


class StaticTradingCalendar:
    """Small injectable calendar for tests and offline replay.

    When no explicit set is supplied, weekdays are treated as trading days;
    production callers can replace this object with a holiday-aware calendar.
    """

    def __init__(self, trading_days: Iterable[date] | None = None) -> None:
        self._trading_days = None if trading_days is None else frozenset(trading_days)

    def is_trading_day(self, value: date) -> bool:
        if self._trading_days is not None:
            return value in self._trading_days
        return value.weekday() < 5


class SessionResolver:
    def __init__(
        self,
        *,
        hours: MarketHours | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self.hours = hours or MarketHours()
        self.calendar = calendar or StaticTradingCalendar()
        self.timezone = ZoneInfo(self.hours.timezone)

    def resolve(self, now: datetime) -> MarketSession:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        local = now.astimezone(self.timezone)
        if not self.calendar.is_trading_day(local.date()):
            return MarketSession.NON_TRADING
        current = local.timetz().replace(tzinfo=None)
        if current < self.hours.pre_market_start or current >= self.hours.close_end:
            return MarketSession.CLOSED
        if current < self.hours.open_start:
            return MarketSession.PRE_MARKET
        if current < self.hours.morning_end:
            return MarketSession.OPEN
        if current < self.hours.afternoon_start:
            return MarketSession.LUNCH_BREAK
        return MarketSession.OPEN if current < self.hours.close_end else MarketSession.CLOSED


@dataclass(frozen=True)
class SchedulerTick:
    timestamp: datetime
    session: MarketSession
    requested: bool
    updated: bool
    quote_count: int = 0
    bar_count: int = 0
    quality_status: DataQualityStatus | None = None
    skip_reason: str = ""
    error: str = ""


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    multiplier: float = 2.0
    max_backoff_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_backoff_seconds < 0 or self.multiplier < 1:
            raise ValueError("backoff must be non-negative and multiplier must be at least one")
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("max_backoff_seconds must cover the initial delay")


@dataclass(frozen=True)
class DataGap:
    symbol: str
    timestamp: datetime
    frequency: str


class GapRecoveryTracker:
    def __init__(self, *, interval_seconds: int = 60, frequency: str = "1m") -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.interval = timedelta(seconds=interval_seconds)
        self.frequency = frequency
        self._last_seen: dict[str, datetime] = {}

    def observe(self, symbol: str, timestamp: datetime) -> tuple[DataGap, ...]:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        previous = self._last_seen.get(symbol)
        self._last_seen[symbol] = timestamp
        if previous is None or timestamp <= previous + self.interval:
            return ()
        gaps: list[DataGap] = []
        current = previous + self.interval
        while current < timestamp:
            gaps.append(DataGap(symbol=symbol, timestamp=current, frequency=self.frequency))
            current += self.interval
        return tuple(gaps)

    def observe_bars(self, bars: Iterable[MinuteBar]) -> tuple[DataGap, ...]:
        gaps: list[DataGap] = []
        for bar in sorted(bars, key=lambda item: (item.symbol, item.timestamp)):
            gaps.extend(self.observe(bar.symbol, bar.timestamp))
        return tuple(gaps)

    def recover(
        self,
        provider: RealTimeDataProvider,
        *,
        symbols: Sequence[str],
        frequency: str | None = None,
    ) -> tuple[MinuteBar, ...]:
        return tuple(provider.get_minute_bars(symbols, frequency or self.frequency))


class RealTimeScheduler:
    """Poll provider data only during an open A-share session."""

    def __init__(
        self,
        *,
        provider: RealTimeDataProvider,
        store: RealTimeStore,
        resolver: SessionResolver | None = None,
        symbols: Sequence[str] = (),
        frequency: str = "1m",
        clock: Callable[[], datetime] | None = None,
        stale_after_seconds: float = 60.0,
        circuit_breaker: RealtimeCircuitBreaker | None = None,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.resolver = resolver or SessionResolver()
        self.symbols = tuple(symbols)
        self.frequency = frequency
        self.clock = clock or (lambda: datetime.now(self.resolver.timezone))
        self.circuit_breaker = circuit_breaker or RealtimeCircuitBreaker(
            stale_after_seconds=stale_after_seconds
        )
        self.timestamp_tracker = TimestampTracker()
        self.retry_policy = retry_policy or RetryPolicy()
        self.sleeper = sleeper or time_module.sleep

    def run_once(self) -> SchedulerTick:
        now = self.clock()
        session = self.resolver.resolve(now)
        if session is not MarketSession.OPEN:
            return SchedulerTick(
                timestamp=now,
                session=session,
                requested=False,
                updated=False,
                skip_reason=f"market session is {session.value}",
            )
        try:
            snapshot = self._retry(self.provider.get_market_snapshot)
            quotes = snapshot.quotes
            try:
                for quote in quotes:
                    self.timestamp_tracker.observe(
                        quote.symbol,
                        quote.timestamp_exchange,
                        now=now,
                    )
            except ValueError:
                self.circuit_breaker.state = "OPEN"
                return SchedulerTick(
                    timestamp=now,
                    session=session,
                    requested=True,
                    updated=False,
                    quality_status=DataQualityStatus.STALE,
                    error="DATA_STALE",
                )
            report = self.circuit_breaker.evaluate(
                quotes,
                expected_symbols=self.symbols,
                now=now,
            )
            usable_quotes = tuple(quote for quote in report.quotes if not quote.is_stale)
            self.store.put_quotes(usable_quotes)
            bars = (
                tuple(
                    self._retry(
                        lambda: self.provider.get_minute_bars(self.symbols, self.frequency)
                    )
                )
                if self.symbols
                else ()
            )
            self.store.put_minute_bars(bars)
            return SchedulerTick(
                timestamp=now,
                session=session,
                requested=True,
                updated=True,
                quote_count=len(usable_quotes),
                bar_count=len(bars),
                quality_status=report.status,
            )
        except Exception as exc:  # provider boundary: do not leak payloads
            self.circuit_breaker.state = "OPEN"
            return SchedulerTick(
                timestamp=now,
                session=session,
                requested=True,
                updated=False,
                quality_status=DataQualityStatus.FAILED,
                error=type(exc).__name__,
            )

    def _retry(self, operation: Callable[[], Any]) -> Any:
        delay = self.retry_policy.initial_backoff_seconds
        last_error: Exception | None = None
        for attempt in range(self.retry_policy.max_attempts):
            try:
                return operation()
            except Exception as exc:  # retry provider boundary without exposing payloads
                last_error = exc
                if attempt + 1 >= self.retry_policy.max_attempts:
                    break
                self.sleeper(delay)
                delay = min(
                    self.retry_policy.max_backoff_seconds,
                    delay * self.retry_policy.multiplier,
                )
        assert last_error is not None
        raise last_error

    def assess_current_data(self) -> Any:
        """Re-check cached quotes before a caller evaluates monitor triggers."""

        return assess_quote_quality(
            self.store.quotes(),
            expected_symbols=self.symbols,
            now=self.clock(),
            stale_after_seconds=self.circuit_breaker.stale_after_seconds,
        )
