from pathlib import Path

import pandas as pd

from a_share_quant.experiments.artifacts import (
    ExperimentArtifactWriter,
    FeatureCacheKey,
)


def test_experiment_writer_creates_reproducible_artifact_layout(tmp_path: Path) -> None:
    writer = ExperimentArtifactWriter(tmp_path, "exp-test")
    writer.write_config({"seed": 42, "strategy": "rule_multifactor"})
    writer.write_metadata({"git_commit": "abc123", "dataset_hash": "data-v1"})
    writer.write_metrics({"sharpe": 1.2})
    writer.write_predictions(pd.DataFrame({"signal_date": ["2025-01-01"], "symbol": ["000001"]}))
    writer.write_equity(pd.DataFrame({"date": ["2025-01-01"], "equity": [1.0]}))

    root = tmp_path / "exp-test"
    assert (root / "config.yaml").exists()
    assert (root / "metadata.json").exists()
    assert (root / "metrics.json").exists()
    assert (root / "predictions.parquet").exists()
    assert (root / "equity.parquet").exists()
    assert (root / "model").is_dir()
    assert (root / "report").is_dir()


def test_feature_cache_key_contains_all_dataset_identity_parts() -> None:
    key = FeatureCacheKey("alpha158_v1", "000001", "2025-01-01", "hash123")

    assert key.as_string() == "alpha158_v1|000001|2025-01-01|hash123"

