from datetime import date
from pathlib import Path

import pandas as pd

from a_share_quant.experiments.evaluator import FairEvaluationResult
from a_share_quant.experiments.runner import ExperimentRunner, ExperimentSpec
from a_share_quant.experiments.splits import TimeSplit


def test_experiment_runner_writes_reproducible_metadata_and_artifacts(tmp_path: Path) -> None:
    split = TimeSplit(
        train=(date(2020, 1, 1), date(2021, 12, 31)),
        validation=(date(2022, 1, 1), date(2022, 6, 30)),
        test=(date(2022, 7, 1), date(2022, 12, 31)),
    )
    evaluation = FairEvaluationResult(
        predictions=pd.DataFrame(
            {
                "signal_date": [date(2022, 7, 1), date(2022, 7, 1)],
                "symbol": ["000001", "000002"],
                "raw_score": [0.9, 0.1],
                "forward_return": [0.02, -0.01],
            }
        ),
        equity=pd.DataFrame(
            {
                "date": [date(2022, 7, 1), date(2022, 7, 2)],
                "equity": [1.0, 1.01],
                "benchmark_equity": [1.0, 1.002],
            }
        ),
        trades=pd.DataFrame(
            {
                "execution_date": [date(2022, 7, 4)],
                "side": ["BUY"],
                "symbol": ["000001"],
                "notional": [0.15],
                "fees": [0.0001],
            }
        ),
        metrics={"cumulative_return": 0.01},
    )
    signals = evaluation.predictions.assign(normalized_score=[100.0, 0.0], rank=[1, 2])
    spec = ExperimentSpec(
        experiment_id="stage2_rule_oos",
        strategy_id="rule_multifactor",
        strategy_version="rule-v1",
        model_version="rule-v1",
        feature_version="rule_features_v1",
        data_version="canonical-v1",
        dataset_hash="dataset-abc",
        seed=42,
        config={"label": "forward_excess_return_5d", "split": split.as_dict()},
        params={"top_k": 10},
        split=split,
        walk_forward=(split,),
    )

    paths = ExperimentRunner(root=tmp_path, repo_root=tmp_path).write(
        spec,
        evaluation,
        model={"model": "fixture"},
        signals=signals,
    )

    assert paths.root == tmp_path / "stage2_rule_oos"
    assert paths.model is not None and paths.model.exists()
    assert paths.trades.exists()
    assert paths.signals is not None and paths.signals.exists()
    metadata = pd.read_json(paths.metadata, typ="series")
    assert metadata["dataset_hash"] == "dataset-abc"
    assert metadata["feature_version"] == "rule_features_v1"
    assert metadata["config_hash"]
    assert metadata["git_commit"] == "unknown"
    assert metadata["split"]["test"] == ["2022-07-01", "2022-12-31"]
    assert metadata["artifacts"]["model"] == "model/model.pkl"
    assert metadata["artifacts"]["signals"] == "signals.parquet"


def test_experiment_spec_rejects_unsafe_or_invalid_identifiers() -> None:
    try:
        ExperimentSpec(
            experiment_id="../escape",
            strategy_id="rule",
            strategy_version="v1",
            model_version="v1",
            feature_version="v1",
            data_version="v1",
            dataset_hash="hash",
            seed=1,
            config={},
            params={},
            split=TimeSplit(
                train=(date(2020, 1, 1), date(2020, 1, 2)),
                validation=(date(2020, 1, 3), date(2020, 1, 4)),
                test=(date(2020, 1, 5), date(2020, 1, 6)),
            ),
        )
    except ValueError as exc:
        assert "experiment_id" in str(exc)
    else:
        raise AssertionError("unsafe experiment id must be rejected")
