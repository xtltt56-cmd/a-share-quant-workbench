from datetime import datetime, timezone

from a_share_quant.contracts.realtime import MinuteBar
from a_share_quant.storage.realtime_store import RealTimeStore


def _bar(*, is_final: bool = False) -> MinuteBar:
    return MinuteBar(
        symbol="000001",
        timestamp=datetime(2026, 8, 9, 2, 59, tzinfo=timezone.utc),
        frequency="1m",
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=100,
        amount=1010,
        source="replay",
        is_final=is_final,
        quality_flag="GOOD",
    )


def test_provisional_bar_is_not_visible_in_historical_layer() -> None:
    store = RealTimeStore()

    store.put_minute_bars([_bar(is_final=False)])

    assert len(store.provisional_bars()) == 1
    assert store.historical_bars() == ()


def test_eod_finalization_moves_only_validated_final_bars() -> None:
    store = RealTimeStore()
    store.put_minute_bars([_bar(is_final=False), _bar(is_final=True)])

    receipt = store.finalize_eod(
        trade_date="2026-08-09",
        reconciler=lambda bars: tuple(bar for bar in bars if bar.is_final),
    )

    assert receipt.finalized_count == 1
    assert len(store.historical_bars()) == 1
    assert store.provisional_bars() == ()


def test_eod_finalization_is_idempotent_for_same_trade_date() -> None:
    store = RealTimeStore()
    store.put_minute_bars([_bar(is_final=True)])
    first = store.finalize_eod(trade_date="2026-08-09", reconciler=lambda bars: bars)
    second = store.finalize_eod(trade_date="2026-08-09", reconciler=lambda bars: bars)

    assert first.finalized_count == 1
    assert second.finalized_count == 0
    assert len(store.historical_bars()) == 1
