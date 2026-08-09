"""Immutable Stage 2 baseline manifest and provenance verification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from .runner import config_hash

REQUIRED_ARTIFACTS = (
    "config",
    "metadata",
    "metrics",
    "predictions",
    "signals",
    "equity",
    "trades",
)


def _canonical_path(path: Path, *, repo_root: Path) -> Path:
    root = Path(repo_root).resolve()
    candidate = Path(path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"artifact path is outside repository: {path}")
    return candidate


def _relative_path(path: Path, *, repo_root: Path) -> str:
    relative = _canonical_path(path, repo_root=repo_root).relative_to(Path(repo_root).resolve())
    return relative.as_posix()


def _required_path(root: Path, name: str, *, repo_root: Path) -> tuple[Path, str]:
    artifact_root = Path(root).resolve()
    candidate = _canonical_path(root / name, repo_root=repo_root)
    if candidate != artifact_root and artifact_root not in candidate.parents:
        raise ValueError(f"artifact path is outside repository or experiment root: {name}")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError(f"missing artifact: {candidate}")
    return candidate, candidate.relative_to(Path(repo_root).resolve()).as_posix()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BaselineManifestEntry:
    strategy_id: str
    strategy_version: str
    model_version: str
    feature_version: str
    experiment_id: str
    git_commit: str
    dataset_hash: str
    config_hash: str
    signal_artifact_path: str
    prediction_artifact_path: str
    train_period: list[str]
    validation_period: list[str]
    test_period: list[str]
    benchmark: str
    transaction_cost_assumptions: dict[str, Any]
    random_seed: int
    library_versions: dict[str, str]
    data_mode: str
    experiment_root: str
    config_artifact_path: str
    metadata_artifact_path: str
    metrics_artifact_path: str
    equity_artifact_path: str
    trades_artifact_path: str
    signals_hash: str
    predictions_hash: str

    @classmethod
    def from_experiment(
        cls,
        root: Path,
        *,
        repo_root: Path,
        benchmark: str | None,
        transaction_cost_assumptions: dict[str, Any],
    ) -> BaselineManifestEntry:
        experiment_root = _canonical_path(root, repo_root=repo_root)
        metadata_path, metadata_relative = _required_path(
            experiment_root, "metadata.json", repo_root=repo_root
        )
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid metadata: {metadata_path}") from exc
        config_name = metadata.get("artifacts", {}).get("config", "config.yaml")
        config_path, config_relative = _required_path(
            experiment_root, config_name, repo_root=repo_root
        )
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(f"invalid config: {config_path}") from exc
        expected_config_hash = str(metadata.get("config_hash", ""))
        actual_config_hash = config_hash(config)
        if expected_config_hash and expected_config_hash != actual_config_hash:
            raise ValueError(f"config hash mismatch: {experiment_root}")

        artifact_paths: dict[str, str] = {}
        resolved_artifacts: dict[str, Path] = {}
        metadata_artifacts = metadata.get("artifacts", {})
        for name in REQUIRED_ARTIFACTS:
            relative_name = metadata_artifacts.get(name, f"{name}.parquet")
            if name == "config":
                relative_name = metadata_artifacts.get(name, "config.yaml")
            if name == "metadata":
                relative_name = "metadata.json"
            path, relative = _required_path(experiment_root, relative_name, repo_root=repo_root)
            resolved_artifacts[name] = path
            artifact_paths[name] = relative

        split = metadata.get("split") or {}
        for period_name in ("train", "validation", "test"):
            period = split.get(period_name)
            if not isinstance(period, list) or len(period) != 2:
                raise ValueError(f"missing {period_name} period: {experiment_root}")

        entry_config = config.get("benchmark") if isinstance(config, dict) else None
        return cls(
            strategy_id=str(metadata["strategy_id"]),
            strategy_version=str(metadata["strategy_version"]),
            model_version=str(metadata["model_version"]),
            feature_version=str(metadata["feature_version"]),
            experiment_id=str(metadata["experiment_id"]),
            git_commit=str(metadata.get("git_commit", "unknown")),
            dataset_hash=str(metadata["dataset_hash"]),
            config_hash=actual_config_hash,
            signal_artifact_path=artifact_paths["signals"],
            prediction_artifact_path=artifact_paths["predictions"],
            train_period=[str(value) for value in split["train"]],
            validation_period=[str(value) for value in split["validation"]],
            test_period=[str(value) for value in split["test"]],
            benchmark=str(benchmark or entry_config or "unknown"),
            transaction_cost_assumptions=dict(transaction_cost_assumptions),
            random_seed=int(metadata.get("random_seed", metadata.get("seed", 0))),
            library_versions={
                str(key): str(value)
                for key, value in (metadata.get("library_versions") or {}).items()
            },
            data_mode=str(config.get("mode", "unknown")) if isinstance(config, dict) else "unknown",
            experiment_root=_relative_path(experiment_root, repo_root=repo_root),
            config_artifact_path=artifact_paths["config"],
            metadata_artifact_path=metadata_relative,
            metrics_artifact_path=artifact_paths["metrics"],
            equity_artifact_path=artifact_paths["equity"],
            trades_artifact_path=artifact_paths["trades"],
            signals_hash=_file_hash(resolved_artifacts["signals"]),
            predictions_hash=_file_hash(resolved_artifacts["predictions"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BaselineManifest:
    manifest_version: str
    baseline_commit: str
    immutable: bool
    entries: tuple[BaselineManifestEntry, ...]

    def __post_init__(self) -> None:
        if not self.immutable:
            raise ValueError("baseline manifest must be immutable")
        ids = [entry.experiment_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("baseline manifest contains duplicate experiment_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "baseline_commit": self.baseline_commit,
            "immutable": self.immutable,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def load(cls, path: Path) -> BaselineManifest:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            manifest_version=str(payload["manifest_version"]),
            baseline_commit=str(payload["baseline_commit"]),
            immutable=bool(payload["immutable"]),
            entries=tuple(BaselineManifestEntry(**entry) for entry in payload["entries"]),
        )

    def write(self, path: Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if output.exists():
            existing = BaselineManifest.load(output)
            if existing.to_dict() != self.to_dict():
                raise ValueError("baseline manifest is immutable and cannot be overwritten")
            return output
        output.write_text(content, encoding="utf-8", newline="\n")
        return output

    def verify(self, *, repo_root: Path) -> bool:
        root = Path(repo_root).resolve()
        for entry in self.entries:
            metadata_path = _canonical_path(root / entry.metadata_artifact_path, repo_root=root)
            if not metadata_path.exists():
                raise ValueError(f"missing artifact: {metadata_path}")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if str(metadata.get("experiment_id")) != entry.experiment_id:
                raise ValueError(f"experiment provenance mismatch: {entry.experiment_id}")
            if str(metadata.get("config_hash")) != entry.config_hash:
                raise ValueError(f"config hash mismatch: {entry.experiment_id}")
            config_path = _canonical_path(root / entry.config_artifact_path, repo_root=root)
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if config_hash(config) != entry.config_hash:
                raise ValueError(f"config hash mismatch: {entry.experiment_id}")
            for relative in (
                entry.signal_artifact_path,
                entry.prediction_artifact_path,
                entry.config_artifact_path,
                entry.metrics_artifact_path,
                entry.equity_artifact_path,
                entry.trades_artifact_path,
            ):
                candidate = _canonical_path(root / relative, repo_root=root)
                if not candidate.exists():
                    raise ValueError(f"missing artifact: {candidate}")
            if _file_hash(root / entry.signal_artifact_path) != entry.signals_hash:
                raise ValueError(f"signals hash mismatch: {entry.experiment_id}")
            if _file_hash(root / entry.prediction_artifact_path) != entry.predictions_hash:
                raise ValueError(f"predictions hash mismatch: {entry.experiment_id}")
        return True


def freeze_stage2_baselines(
    *,
    repo_root: Path,
    artifact_roots: list[Path],
    output: Path,
    baseline_commit: str,
    benchmark: str | None = "000300",
    transaction_cost_assumptions: dict[str, Any] | None = None,
) -> BaselineManifest:
    entries = tuple(
        BaselineManifestEntry.from_experiment(
            root,
            repo_root=repo_root,
            benchmark=benchmark,
            transaction_cost_assumptions=transaction_cost_assumptions or {},
        )
        for root in sorted(artifact_roots, key=lambda value: str(value))
    )
    manifest = BaselineManifest(
        manifest_version="stage3a-v1",
        baseline_commit=baseline_commit,
        immutable=True,
        entries=entries,
    )
    manifest.verify(repo_root=repo_root)
    safe_output = Path(output)
    if not safe_output.is_absolute():
        safe_output = Path(repo_root) / safe_output
    safe_output = _canonical_path(safe_output, repo_root=repo_root)
    manifest.write(safe_output)
    return manifest
