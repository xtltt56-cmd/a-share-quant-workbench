import json
from datetime import datetime, timedelta, timezone

from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote

NOW = datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc)


def _quote(
    *,
    observed_at: datetime = NOW,
    last: float = 10.0,
    volume: float = 100.0,
    quality: str = "GOOD",
    stale: bool = False,
) -> RealTimeQuote:
    return RealTimeQuote(
        symbol="000001",
        market="A",
        timestamp_exchange=observed_at,
        timestamp_received=observed_at,
        last=last,
        open=10.0,
        high=max(10.1, last),
        low=9.9,
        previous_close=10.0,
        volume=volume,
        amount=last * volume,
        source="fixture",
        quality_flag=quality,
        is_stale=stale,
    )


def test_live_quality_requires_two_distinct_valid_updates_before_good() -> None:
    from a_share_quant.runtime.realtime_telemetry import LiveDataQualityGate

    gate = LiveDataQualityGate(stale_after_seconds=60, minimum_distinct_updates=2)
    first = gate.evaluate(
        [_quote()],
        provider_connected=True,
        circuit_breaker_open=False,
        expected_symbols=("000001",),
        now=NOW,
    )
    second_time = NOW + timedelta(seconds=15)
    second = gate.evaluate(
        [_quote(observed_at=second_time, last=10.1, volume=120)],
        provider_connected=True,
        circuit_breaker_open=False,
        expected_symbols=("000001",),
        now=second_time,
    )

    assert first.status is DataQualityStatus.DEGRADED
    assert first.schema_pass is True
    assert first.continuous_updates is False
    assert first.distinct_update_count == 1
    assert second.status is DataQualityStatus.GOOD
    assert second.continuous_updates is True
    assert second.distinct_update_count == 2


def test_live_quality_fails_closed_for_provider_breaker_and_stale_input() -> None:
    from a_share_quant.runtime.realtime_telemetry import LiveDataQualityGate

    gate = LiveDataQualityGate(stale_after_seconds=60)
    disconnected = gate.evaluate(
        [_quote()],
        provider_connected=False,
        circuit_breaker_open=False,
        now=NOW,
    )
    breaker_open = gate.evaluate(
        [_quote()],
        provider_connected=True,
        circuit_breaker_open=True,
        now=NOW,
    )
    stale = gate.evaluate(
        [_quote(observed_at=NOW - timedelta(seconds=61))],
        provider_connected=True,
        circuit_breaker_open=False,
        now=NOW,
    )

    assert disconnected.status is DataQualityStatus.FAILED
    assert breaker_open.status is DataQualityStatus.FAILED
    assert stale.status is DataQualityStatus.STALE
    assert stale.schema_pass is False


def test_live_quality_rejects_future_or_backward_exchange_timestamps() -> None:
    from a_share_quant.runtime.realtime_telemetry import LiveDataQualityGate

    gate = LiveDataQualityGate(stale_after_seconds=60)
    future = gate.evaluate(
        [_quote(observed_at=NOW + timedelta(seconds=1))],
        provider_connected=True,
        circuit_breaker_open=False,
        now=NOW,
    )
    first = gate.evaluate(
        [_quote()],
        provider_connected=True,
        circuit_breaker_open=False,
        now=NOW,
    )
    backward = gate.evaluate(
        [_quote(observed_at=NOW - timedelta(seconds=1), last=10.1)],
        provider_connected=True,
        circuit_breaker_open=False,
        now=NOW,
    )

    assert future.status is DataQualityStatus.DEGRADED
    assert future.schema_pass is False
    assert first.status is DataQualityStatus.DEGRADED
    assert backward.status is DataQualityStatus.STALE
    assert backward.continuous_updates is False


def test_provider_telemetry_tracks_latency_fallbacks_and_never_keeps_error_payloads() -> None:
    from a_share_quant.runtime.realtime_telemetry import ProviderTelemetry

    telemetry = ProviderTelemetry(max_latency_samples=4)
    telemetry.record_success(
        provider="akshare",
        quotes=[_quote()],
        latency_ms=10,
        observed_at=NOW,
    )
    telemetry.record_success(
        provider="akshare",
        quotes=[_quote(observed_at=NOW + timedelta(seconds=15), volume=110)],
        latency_ms=30,
        observed_at=NOW + timedelta(seconds=15),
    )
    telemetry.record_failure(RuntimeError("credential=secret"), observed_at=NOW)
    telemetry.record_fallback(observed_at=NOW)

    payload = telemetry.snapshot().to_dict()

    assert payload["provider"] == "akshare"
    assert payload["quote_count"] == 2
    assert payload["error_count"] == 1
    assert payload["fallback_count"] == 1
    assert payload["average_latency_ms"] == 20.0
    assert payload["p95_latency_ms"] == 30.0
    assert payload["last_latency_ms"] == 30.0
    assert "credential=secret" not in json.dumps(payload)
