from datetime import date

import pytest

from a_share_quant.data.providers.registry import ProviderComparison, ProviderRegistry


def test_provider_comparison_must_pass_before_source_becomes_formal() -> None:
    registry = ProviderRegistry(formal_provider="akshare")

    with pytest.raises(ValueError, match="comparison evidence"):
        registry.promote("tushare")

    registry.record_comparison(
        ProviderComparison(
            incumbent="akshare",
            candidate="tushare",
            as_of=date(2026, 8, 8),
            coverage_ratio=0.99,
            max_timestamp_drift_seconds=20,
            adjustment_match=True,
            identifier_match=True,
            suspension_match=False,
        )
    )
    with pytest.raises(ValueError, match="comparison evidence"):
        registry.promote("tushare")

    registry.record_comparison(
        ProviderComparison(
            incumbent="akshare",
            candidate="tushare",
            as_of=date(2026, 8, 8),
            coverage_ratio=0.99,
            max_timestamp_drift_seconds=20,
            adjustment_match=True,
            identifier_match=True,
            suspension_match=True,
        )
    )

    registry.promote("tushare")

    assert registry.formal_provider == "tushare"


def test_provider_registry_persists_comparison_and_promotion_evidence(tmp_path) -> None:
    evidence_path = tmp_path / "provider-evidence.json"
    comparison = ProviderComparison(
        incumbent="akshare",
        candidate="baostock",
        as_of=date(2026, 8, 8),
        coverage_ratio=0.99,
        max_timestamp_drift_seconds=20,
        adjustment_match=True,
        identifier_match=True,
        suspension_match=True,
    )
    registry = ProviderRegistry(formal_provider="akshare", evidence_path=evidence_path)
    registry.record_comparison(comparison)

    restored = ProviderRegistry(formal_provider="akshare", evidence_path=evidence_path)
    assert restored.comparison_for("baostock") == comparison
    restored.promote("baostock")

    promoted = ProviderRegistry(formal_provider="baostock", evidence_path=evidence_path)
    assert promoted.formal_provider == "baostock"
    assert promoted.comparison_for("tushare") is None
