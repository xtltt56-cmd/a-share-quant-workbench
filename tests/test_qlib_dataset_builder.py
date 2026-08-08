from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")

from a_share_quant.features.universe import HistoricalUniverse
from a_share_quant.integrations.qlib.dataset_builder import QlibDatasetBuilder


def _daily_bars() -> tuple[pd.DataFrame, list[str], pd.DatetimeIndex]:
    dates = pd.bdate_range("2025-01-01", periods=100)
    symbols = ["000001", "000002", "000300"]
    rows: list[dict[str, object]] = []
    for offset, symbol in enumerate(symbols):
        close = 10 + offset + np.cumsum(
            np.random.default_rng(100 + offset).normal(0.03, 0.15, len(dates))
        )
        close = np.maximum(close, 1.0)
        for index, current in enumerate(dates):
            rows.append(
                {
                    "symbol": symbol,
                    "date": current.date(),
                    "open": close[index] * 0.99,
                    "high": close[index] * 1.01,
                    "low": close[index] * 0.98,
                    "close": close[index],
                    "volume": 100_000 + index,
                    "amount": (100_000 + index) * close[index],
                }
            )
    return pd.DataFrame(rows), symbols, dates


def test_qlib_alpha158_dataset_uses_forward_excess_return_label(tmp_path: Path) -> None:
    bars, symbols, dates = _daily_bars()
    universe_rows = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "as_of": dates[0].date(),
                "listed_date": "2020-01-01",
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.0,
            }
            for symbol in symbols
        ]
    )
    universe = HistoricalUniverse(
        universe_rows,
        exclude_new_days=0,
        min_average_amount=None,
        min_average_turnover_pct=None,
    )
    builder = QlibDatasetBuilder(tmp_path, kernels=1)

    artifact = builder.build(
        bars,
        universe=universe,
        symbols=symbols,
        benchmark="000300",
        start_date=dates[0].date(),
        end_date=dates[-1].date(),
        segments={
            "train": (dates[60].date(), dates[74].date()),
            "valid": (dates[75].date(), dates[84].date()),
            "test": (dates[85].date(), dates[94].date()),
        },
    )

    feature_columns = [name for group, name in artifact.frame.columns if group == "feature"]
    label_column = ("label", "forward_excess_return_5d")
    assert len(feature_columns) == 158
    assert artifact.label_name == "forward_excess_return_5d"
    assert label_column in artifact.frame.columns
    assert "000300" not in artifact.frame.index.get_level_values("instrument")
    assert artifact.frame[label_column].notna().any()
    assert not artifact.dataset.prepare("train").empty

    sample_date = dates[60]
    sample_symbol = "000001"
    sample = artifact.frame.loc[(sample_date, sample_symbol), label_column]
    sample_rows = bars.loc[
        (bars["date"] == sample_date.date()) | (bars["date"] == dates[65].date())
    ]
    close_by_symbol = sample_rows.set_index(["symbol", "date"])["close"]
    expected = (
        close_by_symbol.loc[(sample_symbol, dates[65].date())]
        / close_by_symbol.loc[(sample_symbol, sample_date.date())]
        - 1
        - (
            close_by_symbol.loc[("000300", dates[65].date())]
            / close_by_symbol.loc[("000300", sample_date.date())]
            - 1
        )
    )
    assert sample == pytest.approx(expected, abs=1e-6)
