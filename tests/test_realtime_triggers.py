from datetime import datetime, timedelta

from a_share_quant.signals.realtime import (
    RealtimeSignalState,
    TriggerConfig,
    TriggerEngine,
)

NOW = datetime(2026, 8, 10, 2, 0).astimezone()


def _row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol": "000001",
        "timestamp_exchange": NOW - timedelta(seconds=5),
        "current_price": 10.0,
        "score": 82.0,
        "volume_ratio": 1.2,
        "intraday_return_pct": 2.0,
        "observation_low": 9.8,
        "observation_high": 10.2,
        "risk_level": 9.4,
        "target_low": 10.8,
        "target_high": 11.2,
        "data_quality": "GOOD",
    }
    row.update(updates)
    return row


def test_trigger_engine_emits_ready_monitor_signal_without_official_order() -> None:
    signal = TriggerEngine(TriggerConfig(ready_score_min=70)).evaluate(_row(), now=NOW)

    assert signal.state is RealtimeSignalState.READY
    assert signal.symbol == "000001"
    assert signal.suggested_position > 0
    assert signal.official_model_signal is False


def test_stale_data_is_a_circuit_breaker_for_ready_state() -> None:
    signal = TriggerEngine().evaluate(_row(data_quality="STALE"), now=NOW)

    assert signal.state is RealtimeSignalState.STALE_DATA
    assert signal.can_generate_ready is False


def test_overheated_and_risk_states_take_priority_over_ready() -> None:
    overheated = TriggerEngine().evaluate(
        _row(intraday_return_pct=8.5, volume_ratio=3.2), now=NOW
    )
    risk = TriggerEngine().evaluate(_row(current_price=9.2), now=NOW)

    assert overheated.state is RealtimeSignalState.OVERHEATED
    assert risk.state is RealtimeSignalState.RISK


def test_missing_score_waits_and_watch_is_not_ready() -> None:
    missing = TriggerEngine().evaluate(_row(score=None), now=NOW)
    watch = TriggerEngine().evaluate(_row(score=60), now=NOW)

    assert missing.state is RealtimeSignalState.WAIT
    assert watch.state is RealtimeSignalState.WATCH
    assert watch.can_generate_ready is False
