from datetime import date

import pandas as pd
import pytest

from a_share_quant.data.providers.comparison import compare_daily_frames


def _frame(*, close: float = 10.0, source: str = "source") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000001", "000001"],
            "date": [date(2026, 8, 7), date(2026, 8, 10)],
            "close": [close, close + 0.1],
            "volume": [1000, 1100],
            "amount": [10000, 11000],
            "source": [source, source],
        }
    )


def test_provider_frame_comparison_reports_coverage_and_matches() -> None:
    comparison = compare_daily_frames(
        _frame(source="baostock"),
        _frame(source="akshare"),
        incumbent="baostock",
        candidate="akshare",
        as_of=date(2026, 8, 10),
    )

    assert comparison.coverage_ratio == pytest.approx(1.0)
    assert comparison.adjustment_match is True
    assert comparison.identifier_match is True
    assert comparison.suspension_match is True
    assert comparison.max_timestamp_drift_seconds == 0.0


def test_provider_frame_comparison_rejects_price_or_key_disagreement() -> None:
    candidate = _frame(close=11.0)
    candidate.loc[1, "symbol"] = "000002"

    comparison = compare_daily_frames(
        _frame(),
        candidate,
        incumbent="baostock",
        candidate="candidate",
        as_of="2026-08-10",
    )

    assert comparison.coverage_ratio == pytest.approx(0.5)
    assert comparison.adjustment_match is False
    assert comparison.identifier_match is False


def test_provider_frame_comparison_requires_canonical_columns() -> None:
    with pytest.raises(ValueError, match="required columns"):
        compare_daily_frames(
            _frame().drop(columns=["amount"]),
            _frame(),
            incumbent="a",
            candidate="b",
            as_of=date(2026, 8, 10),
        )
