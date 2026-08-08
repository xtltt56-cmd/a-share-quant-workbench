"""Versioned experiment artifact layout and feature-cache identity."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


@dataclass(frozen=True)
class FeatureCacheKey:
    feature_version: str
    symbol: str
    signal_date: date | str
    dataset_hash: str

    def as_string(self) -> str:
        return "|".join(
            (
                self.feature_version,
                self.symbol,
                pd.to_datetime(self.signal_date).date().isoformat(),
                self.dataset_hash,
            )
        )


class ExperimentArtifactWriter:
    def __init__(self, root: Path, experiment_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", experiment_id):
            raise ValueError("experiment_id contains unsafe path characters")
        self.root = Path(root) / experiment_id
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "model").mkdir(exist_ok=True)
        (self.root / "report").mkdir(exist_ok=True)

    def write_config(self, config: dict[str, Any]) -> Path:
        path = self.root / "config.yaml"
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=True)
        return path

    def write_metadata(self, metadata: dict[str, Any]) -> Path:
        return self._write_json("metadata.json", metadata)

    def write_metrics(self, metrics: dict[str, Any]) -> Path:
        return self._write_json("metrics.json", metrics)

    def write_predictions(self, predictions: pd.DataFrame) -> Path:
        path = self.root / "predictions.parquet"
        predictions.to_parquet(path, index=False)
        return path

    def write_equity(self, equity: pd.DataFrame) -> Path:
        path = self.root / "equity.parquet"
        equity.to_parquet(path, index=False)
        return path

    def write_trades(self, trades: pd.DataFrame) -> Path:
        path = self.root / "trades.parquet"
        trades.to_parquet(path, index=False)
        return path

    def write_model(self, model: Any, *, filename: str = "model.pkl") -> Path:
        if Path(filename).name != filename:
            raise ValueError("model filename must be a single safe filename")
        path = self.model_path(filename)
        with path.open("wb") as handle:
            import pickle

            pickle.dump(model, handle)
        return path

    def model_path(self, filename: str = "model.pkl") -> Path:
        if Path(filename).name != filename:
            raise ValueError("model filename must be a single safe filename")
        return self.root / "model" / filename

    def report_path(self, filename: str) -> Path:
        if Path(filename).name != filename:
            raise ValueError("report filename must be a single safe filename")
        return self.root / "report" / filename

    def _write_json(self, filename: str, value: dict[str, Any]) -> Path:
        path = self.root / filename
        with path.open("w", encoding="utf-8") as handle:
            json.dump(_json_safe(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
        return path


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value
