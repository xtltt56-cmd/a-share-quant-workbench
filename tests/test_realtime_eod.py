from datetime import date, datetime, timezone

from a_share_quant.contracts.realtime import MinuteBar
from a_share_quant.runtime.eod import EODPipeline
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.realtime_store import RealTimeStore


def _bar() -> MinuteBar:
    return MinuteBar(
        symbol="000001",
        timestamp=datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc),
        frequency="1m",
        open=10,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=100,
        amount=1010,
        source="replay",
        is_final=True,
    )


def test_eod_pipeline_orders_finalization_before_pit_signal_and_report() -> None:
    store = RealTimeStore()
    official_store = OfficialSignalStore()
    store.put_minute_bars([_bar()])
    events: list[str] = []
    signal = OfficialModelSignal(
        signal_date=date(2026, 8, 10),
        symbol="000001",
        normalized_score=80,
        strategy_version="daily_v1",
    )

    pipeline = EODPipeline(
        store=store,
        confirm_close=lambda _: events.append("confirm") or True,
        load_final_daily=lambda _: events.append("load") or ("daily",),
        reconcile=lambda provisional, daily: events.append("reconcile") or provisional,
        update_pit=lambda daily: events.append("pit"),
        generate_official_signals=lambda daily: events.append("signal") or (signal,),
        write_report=lambda signals: events.append("report"),
        official_signal_store=official_store,
    )

    result = pipeline.run(date(2026, 8, 10))

    assert result.status == "COMPLETED"
    assert result.official_signals == (signal,)
    assert official_store.latest() == (signal,)
    assert events == ["confirm", "load", "reconcile", "pit", "signal", "report"]
    assert len(store.historical_bars()) == 1


def test_eod_pipeline_failure_does_not_emit_official_signal() -> None:
    store = RealTimeStore()
    signal_calls: list[str] = []
    pipeline = EODPipeline(
        store=store,
        confirm_close=lambda _: True,
        load_final_daily=lambda _: (_ for _ in ()).throw(RuntimeError("network unavailable")),
        reconcile=lambda provisional, daily: provisional,
        update_pit=lambda daily: None,
        generate_official_signals=lambda daily: signal_calls.append("signal") or (),
        write_report=lambda signals: None,
    )

    result = pipeline.run(date(2026, 8, 10))

    assert result.status == "FAILED"
    assert result.official_signals == ()
    assert signal_calls == []
