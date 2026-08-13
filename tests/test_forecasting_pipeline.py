from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from a_share_quant.research.forecasting import (
    build_forecast_labels,
    load_challenger_model,
    train_challengers,
)


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


def test_fitted_logistic_model_is_integrity_checked_and_loadable(tmp_path) -> None:
    frame = prices()
    frame["close"] = 20 + ((frame.index % 19) - 9) * 0.08 + frame.index * 0.0002
    result = train_challengers(frame, benchmark(), artifact_root=tmp_path)

    model_paths = [path for path in result.artifacts if path.endswith("logistic-model.json")]
    assert model_paths
    model = load_challenger_model(model_paths[0])
    probabilities = model.predict_proba([[0.01], [-0.02]])
    assert probabilities.shape == (2, 2)
    assert ((probabilities >= 0) & (probabilities <= 1)).all()

    model_path = tmp_path / "models" / "challengers" / "forecasting-v1" / "logistic-model.json"
    tampered = model_path.read_text(encoding="utf-8").replace("0.0", "9.9", 1)
    model_path.write_text(tampered, encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        load_challenger_model(model_path)
