"""Run official Qlib model classes and normalize their prediction output."""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .calendar_adapter import QlibDependencyError
from .dataset_builder import QlibDatasetArtifact


@dataclass(frozen=True)
class ModelRunResult:
    model: Any
    predictions: pd.DataFrame
    strategy_id: str
    strategy_version: str
    model_version: str
    feature_version: str

    def save_model(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump(self.model, handle)


class QlibModelRunner:
    """Shared runner so baselines use the same DatasetH and split contract."""

    def __init__(
        self,
        *,
        seed: int = 42,
        experiment_db: Path = Path("data/qlib_mlflow.db"),
    ) -> None:
        self.seed = seed
        self.experiment_db = Path(experiment_db)

    def run(
        self,
        artifact: QlibDatasetArtifact,
        *,
        model_name: str,
        strategy_id: str,
        strategy_version: str,
        model_version: str,
        params: dict[str, Any] | None = None,
        segment: str = "test",
    ) -> ModelRunResult:
        try:
            import qlib
            from qlib.contrib.model.double_ensemble import DEnsembleModel
            from qlib.contrib.model.gbdt import LGBModel
            from qlib.workflow import R
        except ImportError as exc:  # pragma: no cover - exercised without research extra
            raise QlibDependencyError(
                "Qlib model dependencies are not installed; install the research extra"
            ) from exc

        provider_uri = (
            artifact.provider.root
            if artifact.provider is not None
            else Path.cwd()
        )
        had_active_recorder = _has_active_qlib_recorder(R)
        if not had_active_recorder:
            qlib.init(
                provider_uri=str(provider_uri),
                region="cn",
                expression_cache=None,
                dataset_cache=None,
                kernels=1,
                exp_manager={
                    "class": "MLflowExpManager",
                    "module_path": "qlib.workflow.expm",
                    "kwargs": {
                        "uri": _sqlite_uri(self.experiment_db),
                        "default_exp_name": "a_share_quant_stage2",
                    },
                },
            )

        model_params = dict(params or {})
        seed = int(model_params.get("seed", self.seed))
        np.random.seed(seed)
        try:
            if model_name in {"lightgbm", "qlib_lightgbm_alpha158"}:
                model = LGBModel(**model_params)
                model.fit(artifact.dataset, verbose_eval=0)
            elif model_name in {"double_ensemble", "qlib_double_ensemble_alpha158"}:
                model = DEnsembleModel(**model_params)
                model.fit(artifact.dataset)
            else:
                raise ValueError(f"unsupported Qlib model: {model_name}")

            raw_predictions = model.predict(artifact.dataset, segment=segment)
        finally:
            if not had_active_recorder and _has_active_qlib_recorder(R):
                R.end_exp()
        predictions = _prediction_frame(
            raw_predictions,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            model_version=model_version,
            feature_version=artifact.feature_version,
        )
        return ModelRunResult(
            model=model,
            predictions=predictions,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            model_version=model_version,
            feature_version=artifact.feature_version,
        )


def _prediction_frame(
    raw_predictions: pd.Series,
    *,
    strategy_id: str,
    strategy_version: str,
    model_version: str,
    feature_version: str,
) -> pd.DataFrame:
    if not isinstance(raw_predictions.index, pd.MultiIndex):
        raise ValueError("Qlib predictions must use datetime/instrument MultiIndex")
    date_level = raw_predictions.index.get_level_values("datetime")
    symbol_level = raw_predictions.index.get_level_values("instrument")
    result = pd.DataFrame(
        {
            "signal_date": pd.to_datetime(date_level).date,
            "symbol": symbol_level.astype(str),
            "raw_score": pd.to_numeric(raw_predictions.to_numpy(), errors="coerce"),
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "model_version": model_version,
            "feature_version": feature_version,
        }
    )
    if result["raw_score"].isna().any():
        raise ValueError("Qlib model generated non-finite predictions")
    return result.sort_values(
        ["signal_date", "raw_score", "symbol"], ascending=[True, False, True]
    ).reset_index(drop=True)


def _sqlite_uri(path: Path) -> str:
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{resolved.as_posix()}"


def _has_active_qlib_recorder(recorder: Any) -> bool:
    provider = getattr(recorder, "_provider", None)
    manager = getattr(provider, "exp_manager", None)
    return getattr(manager, "active_experiment", None) is not None
