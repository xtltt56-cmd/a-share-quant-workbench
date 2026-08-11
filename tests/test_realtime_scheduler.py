from dataclasses import replace
from datetime import datetime, timedelta
from datetime import time as datetime_time
from zoneinfo import ZoneInfo

from a_share_quant.contracts.realtime import DataQualityStatus, MarketSnapshot, RealTimeQuote
from a_share_quant.runtime.scheduler import (
    GapRecoveryTracker,
    MarketHours,
    MarketSession,
    RealTimeScheduler,
    RetryPolicy,
    SessionResolver,
    StaticTradingCalendar,
)
from a_share_quant.storage.realtime_store import RealTimeStore

TZ = ZoneInfo("Asia/Shanghai")


def _snapshot(at: datetime) -> MarketSnapshot:
    quote = RealTimeQuote(
        symbol="000001",
        market="A",
        timestamp_exchange=at,
        timestamp_received=at,
        last=10,
        open=10,
        high=10.1,
        low=9.9,
        previous_close=9.8,
        volume=100,
        amount=1000,
        source="replay",
    )
    return MarketSnapshot(
        timestamp_exchange=at,
        timestamp_received=at,
        quotes=(quote,),
        source="replay",
    )


class FakeProvider:
    name = "fake"

    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[str] = []

    def get_market_snapshot(self) -> MarketSnapshot:
        self.calls.append("snapshot")
        return self.snapshot

    def get_quotes(self, symbols):
        self.calls.append("quotes")
        return self.snapshot.quotes

    def get_minute_bars(self, symbols, frequency):
        self.calls.append("bars")
        return ()


def test_scheduler_calls_provider_only_during_open_session() -> None:
    hours = MarketHours()
    resolver = SessionResolver(
        hours=hours,
        calendar=StaticTradingCalendar({datetime(2026, 8, 10).date()}),
    )
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    provider = FakeProvider(_snapshot(now))
    scheduler = RealTimeScheduler(
        provider=provider,
        store=RealTimeStore(),
        resolver=resolver,
        symbols=("000001",),
        clock=lambda: now,
    )

    tick = scheduler.run_once()

    assert tick.session is MarketSession.OPEN
    assert tick.updated is True
    assert provider.calls == ["snapshot", "bars"]


def test_scheduler_skips_lunch_break_and_nontrading_day() -> None:
    trading_day = datetime(2026, 8, 10).date()
    resolver = SessionResolver(
        hours=MarketHours(), calendar=StaticTradingCalendar({trading_day})
    )
    provider = FakeProvider(_snapshot(datetime(2026, 8, 10, 3, 0, tzinfo=TZ)))
    store = RealTimeStore()
    lunch_scheduler = RealTimeScheduler(
        provider=provider,
        store=store,
        resolver=resolver,
        clock=lambda: datetime(2026, 8, 10, 12, 0, tzinfo=TZ),
    )
    closed_scheduler = RealTimeScheduler(
        provider=provider,
        store=store,
        resolver=resolver,
        clock=lambda: datetime(2026, 8, 11, 10, 0, tzinfo=TZ),
    )

    lunch = lunch_scheduler.run_once()
    closed = closed_scheduler.run_once()

    assert lunch.session is MarketSession.LUNCH_BREAK
    assert closed.session is MarketSession.NON_TRADING
    assert lunch.updated is False and closed.updated is False
    assert provider.calls == []


def test_gap_recovery_reports_missing_intervals_and_uses_provider() -> None:
    tracker = GapRecoveryTracker(interval_seconds=60)
    first = datetime(2026, 8, 10, 1, 30, tzinfo=TZ)
    second = datetime(2026, 8, 10, 1, 32, tzinfo=TZ)

    assert tracker.observe("000001", first) == ()
    gap = tracker.observe("000001", second)

    assert [item.timestamp for item in gap] == [datetime(2026, 8, 10, 1, 31, tzinfo=TZ)]


def test_scheduler_rejects_future_quote_and_opens_stale_boundary() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    provider = FakeProvider(_snapshot(now.replace(hour=10, minute=1)))
    scheduler = RealTimeScheduler(
        provider=provider,
        store=RealTimeStore(),
        resolver=resolver,
        clock=lambda: now,
    )

    tick = scheduler.run_once()

    assert tick.updated is False
    assert tick.quality_status.value == "STALE"
    assert tick.error == "DATA_STALE"
    assert scheduler.circuit_breaker.state == "OPEN"


def test_scheduler_stores_only_fresh_quotes_from_partially_stale_full_market() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    fresh = _snapshot(now).quotes[0]
    stale = RealTimeQuote(
        symbol="000002",
        market="A",
        timestamp_exchange=now.replace(hour=9, minute=58),
        timestamp_received=now,
        last=9.5,
        open=9.4,
        high=9.6,
        low=9.3,
        previous_close=9.4,
        volume=100,
        amount=950,
        source="replay",
    )
    provider = FakeProvider(
        MarketSnapshot(
            timestamp_exchange=now,
            timestamp_received=now,
            quotes=(fresh, stale),
            source="replay",
        )
    )
    store = RealTimeStore()
    scheduler = RealTimeScheduler(
        provider=provider,
        store=store,
        resolver=resolver,
        clock=lambda: now,
        stale_after_seconds=60,
    )

    tick = scheduler.run_once()

    assert tick.updated is True
    assert tick.quality_status.value == "DEGRADED"
    assert tick.quote_count == 1
    assert [quote.symbol for quote in store.quotes()] == ["000001"]
    assert scheduler.circuit_breaker.state == "CLOSED"


def test_scheduler_quarantines_one_backward_quote_in_a_mixed_snapshot() -> None:
    first_time = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    second_time = first_time + timedelta(seconds=15)
    first_one = _snapshot(first_time).quotes[0]
    first_two = replace(first_one, symbol="000002", last=20.0)
    second_one = replace(
        first_one,
        timestamp_exchange=first_time - timedelta(seconds=1),
        timestamp_received=second_time,
        last=10.2,
    )
    second_two = replace(
        first_two,
        timestamp_exchange=second_time,
        timestamp_received=second_time,
        last=20.2,
    )
    first = MarketSnapshot(
        timestamp_exchange=first_time,
        timestamp_received=first_time,
        quotes=(first_one, first_two),
        source="replay",
    )
    second = MarketSnapshot(
        timestamp_exchange=second_time,
        timestamp_received=second_time,
        quotes=(second_one, second_two),
        source="replay",
    )
    provider = FakeProvider(first)
    snapshots = iter((first, second))
    provider.get_market_snapshot = lambda: next(snapshots)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({first_time.date()}),
    )
    times = iter((first_time, second_time))
    scheduler = RealTimeScheduler(
        provider=provider,
        store=RealTimeStore(),
        resolver=resolver,
        symbols=("000001", "000002"),
        clock=lambda: next(times),
    )

    first_tick = scheduler.run_once()
    second_tick = scheduler.run_once()

    assert first_tick.updated is True
    assert second_tick.updated is True
    assert second_tick.quality_status is DataQualityStatus.DEGRADED
    assert second_tick.quote_count == 1
    assert scheduler.circuit_breaker.state == "CLOSED"


def test_scheduler_retries_provider_with_bounded_backoff() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    provider = FakeProvider(_snapshot(now))
    original = provider.get_market_snapshot
    attempts = 0

    def flaky_snapshot():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("temporary provider error")
        return original()

    provider.get_market_snapshot = flaky_snapshot
    sleeps: list[float] = []
    scheduler = RealTimeScheduler(
        provider=provider,
        store=RealTimeStore(),
        resolver=resolver,
        symbols=(),
        clock=lambda: now,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.1),
        sleeper=sleeps.append,
    )

    tick = scheduler.run_once()

    assert tick.updated is True
    assert attempts == 3
    assert sleeps == [0.1, 0.2]


def test_market_hours_mapping_and_calendar_validation_are_explicit() -> None:
    hours = MarketHours.from_mapping(
        {
            "timezone": "Asia/Shanghai",
            "pre_market_start": "08:45",
            "open_start": "09:30",
            "morning_end": "11:30",
            "afternoon_start": "13:00",
            "close_end": "15:00",
        }
    )
    resolver = SessionResolver(hours=hours, calendar=StaticTradingCalendar())

    assert hours.pre_market_start == datetime_time(8, 45)
    assert resolver.resolve(datetime(2026, 8, 10, 9, 0, tzinfo=TZ)) is MarketSession.PRE_MARKET
    assert resolver.resolve(datetime(2026, 8, 10, 7, 30, tzinfo=TZ)) is MarketSession.CLOSED

    try:
        resolver.resolve(datetime(2026, 8, 10, 9, 30))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive timestamps must be rejected")


def test_gap_tracker_validates_bars_and_can_recover_from_provider() -> None:
    tracker = GapRecoveryTracker(interval_seconds=60)
    provider = FakeProvider(_snapshot(datetime(2026, 8, 10, 1, 30, tzinfo=TZ)))

    try:
        tracker.observe("000001", datetime(2026, 8, 10, 1, 30))
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("naive gap timestamps must be rejected")
    assert tracker.observe_bars([]) == ()
    assert tracker.recover(provider, symbols=("000001",)) == ()
