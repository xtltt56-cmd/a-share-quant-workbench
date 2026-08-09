from datetime import datetime

import pandas as pd
import pytest

from a_share_quant.features.intraday import IntradayFeatureEngine


def _frame(include_optional: bool = True) -> pd.DataFrame:
    rows = []
    for minute, close in enumerate((10.0, 10.1, 10.2, 10.3, 10.4, 10.5), start=30):
        row = {
            "symbol": "000001",
            "timestamp": datetime(2026, 8, 10, 9, minute),
            "open": 10.0,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 100 + minute,
            "amount": close * (100 + minute),
            "previous_close": 9.8,
        }
        if include_optional:
            row.update({"avg_volume": 100.0, "benchmark_return": 0.01, "industry_return": 0.02})
        rows.append(row)
    return pd.DataFrame(rows)


def test_intraday_features_are_transparent_and_past_only() -> None:
    frame = _frame()
    computed = IntradayFeatureEngine().compute(frame)

    assert computed.loc[0, "price_vs_previous_close"] == pytest.approx(10 / 9.8 - 1)
    assert computed.loc[0, "price_vs_open"] == pytest.approx(0)
    assert computed.loc[0, "volume_ratio"] == pytest.approx(1.3)
    assert computed.loc[5, "momentum_5m"] == pytest.approx(10.5 / 10.0 - 1)
    assert computed.loc[5, "market_relative_strength"] == pytest.approx(10.5 / 9.8 - 1 - 0.01)
    assert computed.loc[5, "industry_relative_strength"] == pytest.approx(10.5 / 9.8 - 1 - 0.02)
    assert {
        "vwap",
        "price_vs_vwap",
        "intraday_high_low_position",
        "intraday_volatility",
    }.issubset(computed.columns)

    extended = pd.concat(
        [frame, frame.iloc[[-1]].assign(timestamp=datetime(2026, 8, 10, 9, 36), close=99.0)],
        ignore_index=True,
    )
    extended_features = IntradayFeatureEngine().compute(extended)
    pd.testing.assert_frame_equal(
        computed.reset_index(drop=True),
        extended_features.iloc[: len(frame)].reset_index(drop=True),
        check_dtype=False,
    )


def test_intraday_features_do_not_fabricate_optional_inputs() -> None:
    computed = IntradayFeatureEngine().compute(_frame(include_optional=False))

    assert pd.isna(computed.loc[0, "volume_ratio"])
    assert pd.isna(computed.loc[0, "market_relative_strength"])
    assert pd.isna(computed.loc[0, "industry_relative_strength"])


def test_intraday_features_require_canonical_columns() -> None:
    with pytest.raises(ValueError, match="canonical columns"):
        IntradayFeatureEngine().compute(_frame().drop(columns=["close"]))
