from datetime import datetime, timedelta, timezone

import pytest

from a_share_quant.contracts.realtime import RealTimeQuote
from a_share_quant.data.realtime.validation import (
    RealtimeCircuitBreaker,
    TimestampTracker,
    assess_quote_quality,
)

NOW = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)


def _quote(
    symbol: str = "000001",
    *,
    age_seconds: float = 1,
    source: str = "fixture",
) -> RealTimeQuote:
    exchange_time = NOW - timedelta(seconds=age_seconds)
    return RealTimeQuote(
        symbol=symbol,
        name=None,
        market="A",
        timestamp_exchange=exchange_time,
        timestamp_received=NOW,
        last=10.0,
        open=10.0,
        high=10.1,
        low=9.9,
        previous_close=10.0,
        volume=1,
        amount=10,
        bid1=None,
        ask1=None,
        change=0,
        change_pct=0,
        turnover_rate=None,
        source=source,
        quality_flag="GOOD",
        is_stale=False,
    )


def test_stale_data_is_marked_and_cannot_be_used_for_analysis() -> None:
    report = assess_quote_quality(
        [_quote(age_seconds=61)],
        expected_symbols=["000001"],
        now=NOW,
        stale_after_seconds=60,
    )

    assert report.status.value == "STALE"
    assert report.is_usable is False
    assert report.quotes[0].is_stale is True


def test_quality_report_marks_missing_symbols_as_degraded() -> None:
    report = assess_quote_quality(
        [_quote()],
        expected_symbols=["000001", "000002"],
        now=NOW,
        stale_after_seconds=60,
    )

    assert report.status.value == "DEGRADED"
    assert report.missing_symbols == ("000002",)
    assert report.can_generate_ready is False


def test_full_market_quality_quarantines_a_stale_subset_without_opening_breaker() -> None:
    breaker = RealtimeCircuitBreaker(stale_after_seconds=60)

    report = breaker.evaluate(
        [_quote("000001"), _quote("000002", age_seconds=61)],
        now=NOW,
    )

    assert report.status.value == "DEGRADED"
    assert report.is_usable is True
    assert report.quarantined_symbols == ("000002",)
    assert breaker.state == "CLOSED"


def test_stale_circuit_breaker_blocks_updates_until_good_data_returns() -> None:
    breaker = RealtimeCircuitBreaker(stale_after_seconds=60)

    stale = breaker.evaluate([_quote(age_seconds=61)], expected_symbols=["000001"], now=NOW)
    stale_state = breaker.state
    good = breaker.evaluate([_quote()], expected_symbols=["000001"], now=NOW)

    assert stale.is_usable is False
    assert stale_state == "OPEN"
    assert good.is_usable is True
    assert breaker.state == "CLOSED"


def test_timestamp_backwards_is_rejected() -> None:
    tracker = TimestampTracker()
    tracker.observe("000001", NOW, now=NOW)

    with pytest.raises(ValueError, match="backwards"):
        tracker.observe("000001", NOW - timedelta(seconds=1), now=NOW)


def test_timestamp_future_is_rejected() -> None:
    tracker = TimestampTracker()

    with pytest.raises(ValueError, match="future"):
        tracker.observe("000001", NOW + timedelta(seconds=2), now=NOW)


def test_future_exchange_quote_is_quarantined_and_never_good() -> None:
    report = assess_quote_quality(
        [_quote(age_seconds=-2)],
        expected_symbols=["000001"],
        now=NOW,
        stale_after_seconds=60,
    )

    assert report.status.value == "STALE"
    assert report.is_usable is False
    assert report.can_generate_ready is False
    assert report.quarantined_symbols == ("000001",)
    assert report.quotes[0].is_stale is True
