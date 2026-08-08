from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("qlib")

from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP

from a_share_quant.integrations.qlib.dataset_builder import QlibDatasetArtifact
from a_share_quant.integrations.qlib.model_runner import QlibModelRunner


def _simple_artifact() -> QlibDatasetArtifact:
    dates = [date(2025, 1, 1) + timedelta(days=index) for index in range(60)]
    symbols = ["000001", "000002", "000003", "000004"]
    index = pd.MultiIndex.from_product([dates, symbols], names=["datetime", "instrument"])
    values = np.arange(len(index), dtype=float)
    frame = pd.DataFrame(
        {
            ("feature", "f0"): np.sin(values / 5),
            ("feature", "f1"): np.cos(values / 7),
            ("feature", "f2"): values % 11,
            ("feature", "f3"): values / 100,
            ("label", "forward_excess_return_5d"): np.sin(values / 9) / 10,
        },
        index=index,
    )
    dataset = DatasetH(
        handler=DataHandlerLP.from_df(frame),
        segments={
            "train": (dates[0], dates[35]),
            "valid": (dates[36], dates[47]),
            "test": (dates[48], dates[59]),
        },
    )
    return QlibDatasetArtifact(
        dataset=dataset,
        frame=frame,
        provider=None,
        feature_version="test_features_v1",
        label_name="forward_excess_return_5d",
        benchmark="000300",
    )


def test_lightgbm_runner_returns_versioned_predictions_and_is_deterministic() -> None:
    artifact = _simple_artifact()
    params = {
        "num_boost_round": 8,
        "early_stopping_rounds": 3,
        "verbosity": -1,
        "num_threads": 1,
        "seed": 42,
    }
    runner = QlibModelRunner(seed=42)

    first = runner.run(
        artifact,
        model_name="lightgbm",
        strategy_id="qlib_lightgbm_alpha158",
        strategy_version="stage2-v1",
        model_version="lgb-test-v1",
        params=params,
    )
    second = runner.run(
        artifact,
        model_name="lightgbm",
        strategy_id="qlib_lightgbm_alpha158",
        strategy_version="stage2-v1",
        model_version="lgb-test-v1",
        params=params,
    )

    required = {
        "signal_date",
        "symbol",
        "raw_score",
        "strategy_id",
        "strategy_version",
        "model_version",
        "feature_version",
    }
    assert required.issubset(first.predictions.columns)
    assert first.predictions["model_version"].eq("lgb-test-v1").all()
    assert first.predictions["feature_version"].eq("test_features_v1").all()
    pd.testing.assert_series_equal(
        first.predictions["raw_score"],
        second.predictions["raw_score"],
        check_names=False,
    )


def test_double_ensemble_runner_uses_official_model_and_emits_predictions() -> None:
    artifact = _simple_artifact()
    result = QlibModelRunner(seed=42).run(
        artifact,
        model_name="double_ensemble",
        strategy_id="qlib_double_ensemble_alpha158",
        strategy_version="stage2-v1",
        model_version="de-test-v1",
        params={
            "num_models": 2,
            "epochs": 4,
            "bins_sr": 2,
            "bins_fs": 2,
            "sample_ratios": [0.8, 0.6],
            "sub_weights": [1, 1],
            "decay": 0.9,
            "early_stopping_rounds": 2,
            "verbosity": -1,
            "num_threads": 1,
            "seed": 42,
        },
    )

    assert not result.predictions.empty
    assert result.predictions["strategy_id"].eq("qlib_double_ensemble_alpha158").all()
    assert result.predictions["raw_score"].map(np.isfinite).all()

