import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from a_share_quant.experiments.baseline_manifest import (
    BaselineManifest,
    freeze_stage2_baselines,
)
from a_share_quant.experiments.runner import config_hash


def _write_experiment(root: Path, experiment_id: str = "exp-1") -> Path:
    root.mkdir(parents=True)
    config = {"benchmark": "000300", "mode": "fixture", "seed": 42}
    with (root / "config.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle)
    for filename in ("predictions.parquet", "signals.parquet", "equity.parquet", "trades.parquet"):
        pd.DataFrame({"value": [1]}).to_parquet(root / filename, index=False)
    (root / "metrics.json").write_text("{}", encoding="utf-8")
    split = {
        "train": ["2020-01-01", "2021-01-01"],
        "validation": ["2021-01-02", "2021-06-01"],
        "test": ["2021-06-02", "2021-12-31"],
    }
    metadata = {
        "experiment_id": experiment_id,
        "strategy_id": "strategy-1",
        "strategy_version": "strategy-v1",
        "model_version": "model-v1",
        "feature_version": "feature-v1",
        "dataset_hash": "dataset-v1",
        "config_hash": config_hash(config),
        "git_commit": "stage2-commit",
        "seed": 42,
        "library_versions": {"pandas": "2.3.3"},
        "split": split,
        "artifacts": {
            "config": "config.yaml",
            "metadata": "metadata.json",
            "metrics": "metrics.json",
            "predictions": "predictions.parquet",
            "signals": "signals.parquet",
            "equity": "equity.parquet",
            "trades": "trades.parquet",
        },
    }
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return root


def test_manifest_freezes_required_provenance_and_verifies_files(tmp_path: Path) -> None:
    experiment = _write_experiment(tmp_path / "exp-1")
    output = tmp_path / "manifest.json"

    manifest = freeze_stage2_baselines(
        repo_root=tmp_path,
        artifact_roots=[experiment],
        output=output,
        baseline_commit="current-commit",
        transaction_cost_assumptions={"commission": 0.0003, "tax": 0.0005},
    )

    assert manifest.verify(repo_root=tmp_path)
    entry = manifest.entries[0]
    assert entry.strategy_id == "strategy-1"
    assert entry.prediction_artifact_path == "exp-1/predictions.parquet"
    assert entry.transaction_cost_assumptions["tax"] == 0.0005
    assert json.loads(output.read_text(encoding="utf-8"))["baseline_commit"] == "current-commit"


def test_manifest_rejects_missing_artifacts_and_is_immutable(tmp_path: Path) -> None:
    experiment = _write_experiment(tmp_path / "exp-1")
    output = tmp_path / "manifest.json"
    first = freeze_stage2_baselines(
        repo_root=tmp_path,
        artifact_roots=[experiment],
        output=output,
        baseline_commit="current-commit",
    )
    second = BaselineManifest.load(output)
    assert second.entries == first.entries

    (experiment / "predictions.parquet").unlink()
    with pytest.raises(ValueError, match="missing artifact"):
        freeze_stage2_baselines(
            repo_root=tmp_path,
            artifact_roots=[experiment],
            output=output,
            baseline_commit="different-commit",
        )


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    experiment = _write_experiment(tmp_path / "exp-1")
    metadata_path = experiment / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["artifacts"]["predictions"] = "../outside.parquet"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="outside repository"):
        freeze_stage2_baselines(
            repo_root=tmp_path,
            artifact_roots=[experiment],
            output=tmp_path / "manifest.json",
            baseline_commit="current-commit",
        )
