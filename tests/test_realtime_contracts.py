from datetime import datetime, timedelta, timezone

import pytest

from a_share_quant.contracts.realtime import (
    DataQualityStatus,
    MinuteBar,
    RealTimeQuote,
)

UTC_NOW = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)


def test_realtime_quote_schema_allows_optional_provider_fields_and_serializes() -> None:
    quote = RealTimeQuote(
        symbol="000001.SZ",
        name="平安银行",
        market="A",
        timestamp_exchange=UTC_NOW - timedelta(seconds=1),
        timestamp_received=UTC_NOW,
        last=10.5,
        open=10.0,
        high=10.8,
        low=9.9,
        previous_close=10.2,
        volume=1000,
        amount=10500,
        bid1=None,
        ask1=None,
        change=0.3,
        change_pct=2.94,
        turnover_rate=None,
        source="fixture",
        quality_flag="GOOD",
        is_stale=False,
    )

    payload = quote.to_dict()

    assert quote.symbol == "000001"
    assert payload["bid1"] is None
    assert quote.data_age_seconds(now=UTC_NOW) == pytest.approx(1.0)
    assert quote.quality_flag is DataQualityStatus.GOOD


def test_realtime_quote_schema_rejects_invalid_prices_and_naive_timestamps() -> None:
    with pytest.raises(ValueError, match="positive"):
        RealTimeQuote(
            symbol="000001",
            name=None,
            market="A",
            timestamp_exchange=UTC_NOW,
            timestamp_received=UTC_NOW,
            last=0,
            open=None,
            high=None,
            low=None,
            previous_close=None,
            volume=None,
            amount=None,
            bid1=None,
            ask1=None,
            change=None,
            change_pct=None,
            turnover_rate=None,
            source="fixture",
            quality_flag="GOOD",
            is_stale=False,
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        RealTimeQuote(
            symbol="000001",
            name=None,
            market="A",
            timestamp_exchange=UTC_NOW.replace(tzinfo=None),
            timestamp_received=UTC_NOW,
            last=None,
            open=None,
            high=None,
            low=None,
            previous_close=None,
            volume=None,
            amount=None,
            bid1=None,
            ask1=None,
            change=None,
            change_pct=None,
            turnover_rate=None,
            source="fixture",
            quality_flag="DEGRADED",
            is_stale=False,
        )


def test_minute_bar_schema_distinguishes_provisional_and_final_bars() -> None:
    bar = MinuteBar(
        symbol="600000.SH",
        timestamp=datetime(2026, 8, 9, 2, 59, tzinfo=timezone.utc),
        frequency="1m",
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=100,
        amount=1010,
        source="replay",
        is_final=False,
        quality_flag="GOOD",
    )

    assert bar.symbol == "600000"
    assert bar.is_provisional is True
    assert bar.to_dict()["is_final"] is False


def test_minute_bar_schema_rejects_impossible_ohlc_and_negative_volume() -> None:
    with pytest.raises(ValueError, match="OHLC"):
        MinuteBar(
            symbol="000001",
            timestamp=UTC_NOW,
            frequency="1m",
            open=10.0,
            high=9.0,
            low=9.5,
            close=9.7,
            volume=100,
            amount=1000,
            source="fixture",
            is_final=True,
            quality_flag="GOOD",
        )

    with pytest.raises(ValueError, match="negative"):
        MinuteBar(
            symbol="000001",
            timestamp=UTC_NOW,
            frequency="1m",
            open=10.0,
            high=10.5,
            low=9.5,
            close=10.0,
            volume=-1,
            amount=1000,
            source="fixture",
            is_final=True,
            quality_flag="GOOD",
        )
