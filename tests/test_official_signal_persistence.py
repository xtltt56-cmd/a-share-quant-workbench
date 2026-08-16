import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from a_share_quant.runtime.official_daily import load_or_generate_official_store
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore


def _signal() -> OfficialModelSignal:
    return OfficialModelSignal(
        signal_date=date(2026, 8, 7),
        symbol="600000",
        name="浦发银行",
        normalized_score=83.25,
        strategy_version="initial-free-data-v1",
        model_version="rule-none-v1",
        feature_version="rule-features-v1",
        data_mode="historical",
        source="baostock",
        data_cutoff=date(2026, 8, 7),
        generated_at=datetime(2026, 8, 7, 8, 30, tzinfo=timezone.utc),
        rank=1,
        reasons=("收盘价位于60日均线上方", "20日平均成交额通过门槛"),
        reference_price=9.21,
        average_amount=520_000_000,
        invalidation_price=8.75,
    )


def _encoded_signal(*, data_mode: str = "historical") -> dict[str, object]:
    return {
        "signal_date": "2026-08-07",
        "symbol": "600000",
        "name": "浦发银行",
        "normalized_score": 83.25,
        "strategy_version": "initial-free-data-v1",
        "frequency": "daily",
        "model_version": "rule-none-v1",
        "feature_version": "rule-features-v1",
        "data_mode": data_mode,
        "source": "baostock",
        "data_cutoff": "2026-08-07",
        "generated_at": "2026-08-07T08:30:00+00:00",
        "rank": 1,
        "reasons": ["收盘价位于60日均线上方", "20日平均成交额通过门槛"],
        "reference_price": 9.21,
        "average_amount": 520_000_000.0,
        "invalidation_price": 8.75,
    }


def test_official_signal_store_persists_and_loads_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "signals" / "official-daily.json"
    signal = _signal()

    OfficialSignalStore(path=path).put_signals([signal])
    restarted = OfficialSignalStore(path=path)

    assert restarted.latest() == (signal,)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format_version"] == 1
    assert payload["data_mode"] == "historical"
    assert payload["operating_mode"] == "PAPER_ONLY"
    assert not list(path.parent.glob("*.tmp"))


def test_same_date_strategy_replaces_previous_candidate_set(tmp_path: Path) -> None:
    path = tmp_path / "official-daily.json"
    first = _signal()
    replacement = OfficialModelSignal(
        **{
            **first.__dict__,
            "symbol": "600001",
            "name": "邯郸发展",
            "normalized_score": 84.5,
            "rank": 1,
        }
    )

    store = OfficialSignalStore(path=path)
    store.put_signals((first,))
    store.put_signals((replacement,))

    assert store.latest() == (replacement,)
    persisted = OfficialSignalStore(path=path)
    assert persisted.latest() == (replacement,)


def test_official_signal_store_rejects_fixture_artifact_disguised_as_official(
    tmp_path: Path,
) -> None:
    path = tmp_path / "official-daily.json"
    payload = {
        "format_version": 1,
        "data_mode": "historical",
        "operating_mode": "PAPER_ONLY",
        "signals": [_encoded_signal(data_mode="fixture")],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="official signal artifact"):
        OfficialSignalStore(path=path)


def test_official_signal_store_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "official-daily.json"
    store = OfficialSignalStore(path=path)
    store.put_signals([_signal()])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unexpected"] = "unsafe"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="official signal artifact"):
        OfficialSignalStore(path=path)


def test_older_generated_signals_cannot_report_fresh_over_newer_artifact(tmp_path: Path) -> None:
    path = tmp_path / "official-daily.json"
    newer = _signal()
    newer = OfficialModelSignal(
        **{
            **newer.__dict__,
            "signal_date": date(2026, 8, 12),
            "data_cutoff": date(2026, 8, 12),
        }
    )
    OfficialSignalStore(path).put_signals((newer,))

    store = load_or_generate_official_store(path, generator=lambda: (_signal(),))

    assert store.latest() == (newer,)
    assert store.refresh_status == "UPDATE_REJECTED"
