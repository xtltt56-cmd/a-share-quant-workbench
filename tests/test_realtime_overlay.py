from datetime import datetime, timedelta, timezone

from a_share_quant.signals.realtime import OfficialModelSignal

NOW = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)


def _official() -> OfficialModelSignal:
    return OfficialModelSignal(
        signal_date=NOW.date(),
        symbol="000001",
        normalized_score=82.5,
        strategy_version="stage2-v1",
    )


def test_realtime_overlay_cannot_mutate_the_official_daily_score() -> None:
    from a_share_quant.contracts.realtime_overlay import RealtimeOverlay
    from a_share_quant.storage.official_signal_store import OfficialSignalStore
    from a_share_quant.storage.realtime_overlay_store import RealtimeOverlayStore

    official_store = OfficialSignalStore()
    official_store.put_signals([_official()])
    overlay_store = RealtimeOverlayStore()
    overlay_store.put_overlays(
        [
            RealtimeOverlay(
                symbol="000001",
                timestamp=NOW,
                last=10.2,
                vwap=10.1,
                volume_ratio=1.2,
                intraday_return=0.02,
                market_relative_strength=0.01,
                trigger_state="READY",
                risk_state="NORMAL",
                data_quality="GOOD",
            )
        ]
    )

    official = official_store.latest()
    overlay = overlay_store.overlays()
    payload = overlay[0].to_dict()

    assert official[0].normalized_score == 82.5
    assert overlay[0].trigger_state == "READY"
    assert {
        "symbol",
        "timestamp",
        "last",
        "vwap",
        "volume_ratio",
        "intraday_return",
        "market_relative_strength",
        "trigger_state",
        "risk_state",
        "data_quality",
    } <= set(payload)
    assert not ({"BUY", "order", "broker", "execution"} & set(payload))


def test_overlay_store_rejects_an_older_snapshot_without_touching_daily_signals() -> None:
    from a_share_quant.contracts.realtime_overlay import RealtimeOverlay
    from a_share_quant.storage.official_signal_store import OfficialSignalStore
    from a_share_quant.storage.realtime_overlay_store import RealtimeOverlayStore

    official_store = OfficialSignalStore()
    official_store.put_signals([_official()])
    overlays = RealtimeOverlayStore()
    newer = RealtimeOverlay(
        symbol="000001",
        timestamp=NOW,
        last=10.2,
        vwap=None,
        volume_ratio=None,
        intraday_return=None,
        market_relative_strength=None,
        trigger_state="WATCH",
        risk_state="NORMAL",
        data_quality="GOOD",
    )
    older = RealtimeOverlay(
        symbol="000001",
        timestamp=NOW - timedelta(minutes=1),
        last=9.9,
        vwap=None,
        volume_ratio=None,
        intraday_return=None,
        market_relative_strength=None,
        trigger_state="RISK",
        risk_state="RISK",
        data_quality="GOOD",
    )
    overlays.put_overlays([newer, older])

    assert overlays.overlays()[0].last == 10.2
    assert official_store.latest()[0].normalized_score == 82.5
