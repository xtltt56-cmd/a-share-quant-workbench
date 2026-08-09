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
