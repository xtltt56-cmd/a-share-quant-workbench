from datetime import datetime
from zoneinfo import ZoneInfo

from a_share_quant.contracts.realtime import MarketSnapshot, RealTimeQuote
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
