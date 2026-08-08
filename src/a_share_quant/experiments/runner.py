"""Reproducible fixed-OOS and Walk-Forward experiment execution artifacts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import pickle
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .artifacts import ExperimentArtifactWriter
from .evaluator import FairEvaluationResult
from .splits import TimeSplit


@dataclass(frozen=True)
class ExperimentSpec:
    """Immutable identity and split contract for one comparable experiment."""

    experiment_id: str
    strategy_id: str
    strategy_version: str
    model_version: str
    feature_version: str
    data_version: str
    dataset_hash: str
    seed: int
    config: Mapping[str, Any]
    params: Mapping[str, Any]
    split: TimeSplit
    walk_forward: tuple[TimeSplit, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.experiment_id):
            raise ValueError("experiment_id contains unsafe path characters")
        for field_name in (
            "strategy_id",
            "strategy_version",
            "model_version",
            "feature_version",
            "data_version",
            "dataset_hash",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
        if int(self.seed) < 0:
            raise ValueError("seed must be non-negative")


@dataclass(frozen=True)
class ExperimentRunPaths:
    root: Path
    config: Path
    metadata: Path
    metrics: Path
    predictions: Path
    equity: Path
    trades: Path
    model: Path | None


class ExperimentRunner:
    """Persist one fixed-OOS or Walk-Forward result as a self-describing bundle."""

    def __init__(self, *, root: Path, repo_root: Path) -> None:
        self.root = Path(root)
        self.repo_root = Path(repo_root)

    def write(
        self,
        spec: ExperimentSpec,
        evaluation: FairEvaluationResult,
        *,
        model: Any | None = None,
        model_filename: str = "model.pkl",
        extra_metadata: Mapping[str, Any] | None = None,
    ) -> ExperimentRunPaths:
        writer = ExperimentArtifactWriter(self.root, spec.experiment_id)
        config_path = writer.write_config(dict(spec.config))
        predictions_path = writer.write_predictions(evaluation.predictions)
        equity_path = writer.write_equity(evaluation.equity)
        trades_path = writer.write_trades(evaluation.trades)
        metrics_path = writer.write_metrics(evaluation.metrics)
        model_path = (
            writer.write_model(model, filename=model_filename)
            if model is not None
            else None
        )

        metadata = build_experiment_metadata(
            spec,
            repo_root=self.repo_root,
            artifacts={
                "config": _relative_name(config_path, writer.root),
                "metadata": "metadata.json",
                "metrics": _relative_name(metrics_path, writer.root),
                "predictions": _relative_name(predictions_path, writer.root),
                "equity": _relative_name(equity_path, writer.root),
                "trades": _relative_name(trades_path, writer.root),
                "model": _relative_name(model_path, writer.root) if model_path else None,
            },
        )
        if extra_metadata:
            metadata["extra"] = dict(extra_metadata)
        metadata_path = writer.write_metadata(metadata)
        return ExperimentRunPaths(
            root=writer.root,
            config=config_path,
            metadata=metadata_path,
            metrics=metrics_path,
            predictions=predictions_path,
            equity=equity_path,
            trades=trades_path,
            model=model_path,
        )


def build_experiment_metadata(
    spec: ExperimentSpec,
    *,
    repo_root: Path,
    artifacts: Mapping[str, str | None],
) -> dict[str, Any]:
    """Build the audit record needed to reproduce and compare a run."""

    return {
        "experiment_id": spec.experiment_id,
        "strategy_id": spec.strategy_id,
        "strategy_version": spec.strategy_version,
        "model_version": spec.model_version,
        "feature_version": spec.feature_version,
        "data_version": spec.data_version,
        "dataset_hash": spec.dataset_hash,
        "config_hash": config_hash(spec.config),
        "seed": int(spec.seed),
        "params": dict(spec.params),
        "split": spec.split.as_dict(),
        "walk_forward": [window.as_dict() for window in spec.walk_forward],
        "git_commit": _git_commit(repo_root),
        "library_versions": _library_versions(),
        "artifacts": dict(artifacts),
    }


def config_hash(config: Mapping[str, Any]) -> str:
    canonical = json.dumps(_json_safe(dict(config)), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _relative_name(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _git_commit(repo_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = completed.stdout.strip()
    return value or "unknown"


def _library_versions() -> dict[str, str]:
    packages = {
        "a_share_quant": "a-share-quant",
        "akshare": "akshare",
        "duckdb": "duckdb",
        "pandas": "pandas",
        "numpy": "numpy",
        "pyarrow": "pyarrow",
        "qlib": "pyqlib",
        "lightgbm": "lightgbm",
    }
    versions: dict[str, str] = {}
    for label, package in packages.items():
        try:
            versions[label] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[label] = "not-installed"
    return versions


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if hasattr(value, "item"):
        return _json_safe(value.item())
    return value


def _write_model(model: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(model, handle)
