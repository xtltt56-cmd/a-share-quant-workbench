from datetime import date, datetime, timezone

from a_share_quant.runtime.official_daily import load_or_generate_official_store
from a_share_quant.signals.realtime import OfficialModelSignal


def _signal(*, signal_date: date, symbol: str = "000001") -> OfficialModelSignal:
    return OfficialModelSignal(
        signal_date=signal_date,
        symbol=symbol,
        name="平安银行",
        normalized_score=80.0,
        strategy_version="initial-free-data-v1",
        model_version="rule-ranking-v1",
        feature_version="rule-features-v1-no-valuation",
        data_mode="historical",
        source="baostock",
        data_cutoff=signal_date,
        generated_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
        rank=1,
        reasons=("测试候选",),
        reference_price=10.0,
        average_amount=100_000_000.0,
        invalidation_price=9.3,
    )


def test_bootstrap_writes_candidates_when_artifact_is_empty(monkeypatch, tmp_path) -> None:
    expected = (_signal(signal_date=date(2026, 8, 10)),)
    monkeypatch.setattr(
        "a_share_quant.runtime.official_daily.generate_from_data_root",
        lambda *args, **kwargs: expected,
    )

    store = load_or_generate_official_store(
        tmp_path / "signals.json",
        repo_root=tmp_path,
    )

    assert store.latest() == expected
    assert (tmp_path / "signals.json").is_file()


def test_bootstrap_keeps_existing_signal_when_generation_fails(monkeypatch, tmp_path) -> None:
    path = tmp_path / "signals.json"
    expected = (_signal(signal_date=date(2026, 8, 9)),)
    load_or_generate_official_store(path, repo_root=tmp_path, generator=lambda: expected)
    monkeypatch.setattr(
        "a_share_quant.runtime.official_daily.generate_from_data_root",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("insufficient history")),
    )

    store = load_or_generate_official_store(path, repo_root=tmp_path)

    assert store.latest() == expected

