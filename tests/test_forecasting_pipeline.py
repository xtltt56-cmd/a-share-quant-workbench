from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from a_share_quant.research.forecasting import build_forecast_labels, train_challengers


def prices(count: int = 1400) -> pd.DataFrame:
    rows = []
    start = date(2020, 1, 1)
    for index in range(count):
        rows.append(
            {
                "symbol": f"{index % 40:06d}",
                "date": start + timedelta(days=index),
                "close": 10 + index * 0.001,
                "feature_momentum": (index % 17) / 17,
            }
        )
    return pd.DataFrame(rows)


def benchmark(count: int = 1400) -> pd.DataFrame:
    start = date(2020, 1, 1)
    return pd.DataFrame(
        {
            "date": [start + timedelta(days=index) for index in range(count)],
            "close": [3000 + index * 0.5 for index in range(count)],
        }
    )


def test_label_is_cost_adjusted_excess_return() -> None:
    result = build_forecast_labels(prices(400), benchmark(400), round_trip_cost=0.0012)
    row = result.iloc[0]
    assert row.excess_return_5 == row.forward_return_5 - row.benchmark_return_5 - 0.0012
    assert row.positive_edge_5 == (
        (row.forward_return_5 - row.benchmark_return_5) > 0.0062
    )


def test_folds_never_leak_future() -> None:
    result = train_challengers(prices(), benchmark(), artifact_root=None)
    assert all(
        fold.train_end < fold.validation_start <= fold.validation_end < fold.test_start
        for fold in result.folds
    )
    assert result.status == "RESEARCH_ONLY"


def test_research_artifact_metadata_is_deterministic(tmp_path) -> None:
    first = train_challengers(prices(), benchmark(), artifact_root=tmp_path / "one")
    second = train_challengers(prices(), benchmark(), artifact_root=tmp_path / "two")
    assert first.feature_schema == second.feature_schema
    assert first.random_seed == 20260812
    assert first.formal_eligible is False
