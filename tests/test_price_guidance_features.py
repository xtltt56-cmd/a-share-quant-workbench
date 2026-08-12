from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from a_share_quant.features.price_guidance import build_price_features


def bars(*, count: int = 260, raw_close: float | None = None, close: float = 10.0) -> pd.DataFrame:
    start = date(2025, 8, 1)
    rows = []
    for index in range(count):
        day = start + timedelta(days=index)
        value = close + index * 0.01
        rows.append(
            {
                "symbol": "000001",
                "date": day,
                "open": value,
                "high": value + 0.2,
                "low": value - 0.2,
                "close": value,
                "raw_close": raw_close if raw_close is not None else value,
                "volume": 100000,
                "amount": 10000000,
                "source": "baostock",
            }
        )
    return pd.DataFrame(rows)


def test_future_row_cannot_change_cutoff_features() -> None:
    cutoff = date(2026, 4, 15)
    first = build_price_features(bars(), cutoff=cutoff)
    future = bars()
    future.loc[len(future)] = {
        "symbol": "000001",
        "date": cutoff + timedelta(days=1),
        "open": 999,
        "high": 999,
        "low": 999,
        "close": 999,
        "raw_close": 999,
        "volume": 100000,
        "amount": 10000000,
        "source": "baostock",
    }
    changed = build_price_features(future, cutoff=cutoff)
    assert changed == first


def test_adjustment_factor_maps_to_actual_price() -> None:
    frame = bars(raw_close=11.0, close=10.0)
    frame["close"] = 10.0
    frame["open"] = 10.0
    frame["high"] = 10.2
    frame["low"] = 9.8
    result = build_price_features(frame, cutoff=date(2026, 4, 15))
    assert result.to_actual("10") == 11


def test_251_rows_are_rejected() -> None:
    with pytest.raises(ValueError, match="252"):
        build_price_features(bars(count=251), cutoff=date(2026, 4, 15))


def test_invalid_rows_and_test_sources_fail_closed() -> None:
    invalid = bars()
    invalid.loc[0, "low"] = invalid.loc[0, "high"] + 1
    with pytest.raises(ValueError, match="OHLC"):
        build_price_features(invalid, cutoff=date(2026, 4, 15))

    fixture = bars()
    fixture["source"] = "fixture"
    with pytest.raises(ValueError, match="source"):
        build_price_features(fixture, cutoff=date(2026, 4, 15))
