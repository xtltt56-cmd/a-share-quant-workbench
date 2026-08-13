"""Cost-aware research forecasting and leakage-safe challenger metadata.

This module deliberately keeps every challenger in ``RESEARCH_ONLY`` mode.
It creates auditable labels and walk-forward folds, and may fit optional
research dependencies when they are installed, but it never changes the
production signal provider or champion alias.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class WalkForwardFold:
    """A chronological train/validation/test split with an embargo gap."""

    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date


@dataclass(frozen=True)
class ChallengerTrainingResult:
    status: str
    formal_eligible: bool
    random_seed: int
    feature_schema: tuple[str, ...]
    folds: tuple[WalkForwardFold, ...]
    artifacts: tuple[str, ...]


@dataclass(frozen=True)
class PortableLogisticModel:
    feature_schema: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float

    def predict_proba(self, values: Any) -> np.ndarray:
        matrix = np.asarray(values, dtype="float64")
        if matrix.ndim != 2 or matrix.shape[1] != len(self.coefficients):
            raise ValueError("model input does not match feature schema")
        logits = matrix @ np.asarray(self.coefficients) + self.intercept
        positive = 1.0 / (1.0 + np.exp(-np.clip(logits, -709, 709)))
        return np.column_stack((1.0 - positive, positive))


def build_forecast_labels(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    round_trip_cost: float,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    """Build forward, benchmark-relative and cost-adjusted labels.

    Forward prices use trading-row shifts per symbol.  Benchmark returns use
    the benchmark's own chronological rows, rather than calendar-day lookup,
    so weekends and market holidays cannot silently shorten a horizon.
    """

    if round_trip_cost < 0:
        raise ValueError("round_trip_cost cannot be negative")
    if not horizons or any(int(horizon) <= 0 for horizon in horizons):
        raise ValueError("horizons must contain positive integers")
    stock = _normalise_prices(prices, require_symbol=True)
    bench = _normalise_prices(benchmark, require_symbol=False)
    bench = bench.sort_values("date").drop_duplicates("date").reset_index(drop=True)

    stock = stock.sort_values(["symbol", "date"]).reset_index(drop=True)
    benchmark_returns: dict[int, pd.Series] = {}
    for horizon in horizons:
        horizon = int(horizon)
        future = stock.groupby("symbol", sort=False)["close"].shift(-horizon)
        stock[f"forward_return_{horizon}"] = future / stock["close"] - 1.0
        benchmark_future = bench["close"].shift(-horizon)
        benchmark_returns[horizon] = benchmark_future / bench["close"] - 1.0
        lookup = pd.Series(
            benchmark_returns[horizon].to_numpy(),
            index=bench["date"],
            dtype="float64",
        )
        stock[f"benchmark_return_{horizon}"] = stock["date"].map(lookup)
        excess = (
            stock[f"forward_return_{horizon}"]
            - stock[f"benchmark_return_{horizon}"]
            - float(round_trip_cost)
        )
        stock[f"excess_return_{horizon}"] = excess
        stock[f"positive_edge_{horizon}"] = (
            stock[f"forward_return_{horizon}"]
            - stock[f"benchmark_return_{horizon}"]
            > float(round_trip_cost) + 0.005
        )
    return stock


def train_challengers(
    prices: pd.DataFrame,
    benchmark: pd.DataFrame,
    *,
    artifact_root: str | Path | None,
    random_seed: int = 20260812,
    round_trip_cost: float = 0.0012,
    n_jobs: int = 1,
) -> ChallengerTrainingResult:
    """Fit optional research challengers and write deterministic metadata.

    A missing optional dependency or an under-populated fold is recorded as
    unavailable; it never gets promoted implicitly.  The returned result is
    therefore useful on a clean free-data installation as well as on a full
    research environment.
    """

    labels = build_forecast_labels(
        prices,
        benchmark,
        round_trip_cost=round_trip_cost,
    )
    feature_schema = tuple(
        sorted(column for column in labels.columns if column.startswith("feature_"))
    )
    if not feature_schema:
        labels["feature_return_5"] = (
            labels.groupby("symbol", sort=False)["close"].transform(
                lambda values: values / values.shift(5) - 1.0
            )
        )
        feature_schema = ("feature_return_5",)
    dates = [value for value in sorted(labels["date"].dropna().unique())]
    folds = _walk_forward_folds(dates)
    model_status, fitted_models = _fit_research_challengers(
        labels,
        feature_schema=feature_schema,
        folds=folds,
        random_seed=int(random_seed),
        n_jobs=int(n_jobs),
    )
    artifacts: list[str] = []
    if artifact_root is not None:
        logistic = fitted_models.get("logistic-baseline")
        if logistic is not None:
            artifacts.append(
                _write_logistic_model(
                    Path(artifact_root),
                    logistic,
                    feature_schema=feature_schema,
                    random_seed=int(random_seed),
                )
            )
        artifacts.append(
            _write_metadata(
                Path(artifact_root),
                random_seed=int(random_seed),
                feature_schema=feature_schema,
                folds=folds,
                model_status=model_status,
                round_trip_cost=float(round_trip_cost),
            )
        )
    return ChallengerTrainingResult(
        status="RESEARCH_ONLY",
        formal_eligible=False,
        random_seed=int(random_seed),
        feature_schema=feature_schema,
        folds=folds,
        artifacts=tuple(artifacts),
    )


def _normalise_prices(frame: pd.DataFrame, *, require_symbol: bool) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("price data cannot be empty")
    required = {"date", "close"} | ({"symbol"} if require_symbol else set())
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing price columns: {sorted(missing)}")
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date
    result["close"] = pd.to_numeric(result["close"], errors="raise")
    if (result["close"] <= 0).any() or not np.isfinite(result["close"]).all():
        raise ValueError("close prices must be finite and positive")
    if require_symbol:
        result["symbol"] = result["symbol"].astype(str).str.zfill(6)
    return result


def _walk_forward_folds(dates: list[date]) -> tuple[WalkForwardFold, ...]:
    """Use 756 train / 252 validation / 126 embargo / 126 test rows."""

    ordered = sorted(set(dates))
    train_size, validation_size, embargo, test_size = 756, 252, 126, 126
    minimum = train_size + validation_size + embargo + test_size
    if len(ordered) < minimum:
        return ()
    folds: list[WalkForwardFold] = []
    start = 0
    while start + minimum <= len(ordered):
        train_end = ordered[start + train_size - 1]
        validation_start = ordered[start + train_size]
        validation_end = ordered[start + train_size + validation_size - 1]
        test_start = ordered[start + train_size + validation_size + embargo]
        test_end = ordered[start + minimum - 1]
        folds.append(
            WalkForwardFold(
                train_end=train_end,
                validation_start=validation_start,
                validation_end=validation_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        start += test_size
    return tuple(folds)


def _fit_research_challengers(
    labels: pd.DataFrame,
    *,
    feature_schema: tuple[str, ...],
    folds: tuple[WalkForwardFold, ...],
    random_seed: int,
    n_jobs: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    statuses = {
        "logistic-baseline": "UNAVAILABLE",
        "lightgbm-challenger": "UNAVAILABLE",
        "qlib-double-ensemble": "METADATA_ONLY",
    }
    if not folds:
        return statuses, {}
    fitted_models: dict[str, Any] = {}
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        LogisticRegression = None  # type: ignore[assignment,misc]
    try:
        from lightgbm import LGBMClassifier
    except ImportError:
        LGBMClassifier = None  # type: ignore[assignment,misc]

    for fold in folds:
        train = labels[labels["date"] <= fold.train_end].dropna(
            subset=[
                *feature_schema,
                "forward_return_5",
                "benchmark_return_5",
                "positive_edge_5",
            ]
        )
        if train.empty or train["positive_edge_5"].nunique() < 2:
            continue
        x = train.loc[:, feature_schema].astype(float)
        y = train["positive_edge_5"].astype(int)
        if LogisticRegression is not None:
            try:
                model = LogisticRegression(
                    random_state=random_seed,
                    max_iter=2000,
                ).fit(x, y)
                statuses["logistic-baseline"] = "FITTED"
                fitted_models["logistic-baseline"] = model
            except (ValueError, TypeError):
                pass
        if LGBMClassifier is not None:
            try:
                model = LGBMClassifier(
                    random_state=random_seed,
                    n_jobs=max(1, n_jobs),
                    n_estimators=100,
                    verbosity=-1,
                ).fit(x, y)
                statuses["lightgbm-challenger"] = "FITTED"
                fitted_models["lightgbm-challenger"] = model
            except (ValueError, TypeError):
                pass
    return statuses, fitted_models


def _write_logistic_model(
    root: Path,
    model: Any,
    *,
    feature_schema: tuple[str, ...],
    random_seed: int,
) -> str:
    body = {
        "format_version": 1,
        "model_type": "portable-logistic",
        "candidate_id": "forecasting-v1",
        "status": "RESEARCH_ONLY",
        "feature_schema": list(feature_schema),
        "coefficients": [float(value) for value in model.coef_[0]],
        "intercept": float(model.intercept_[0]),
        "random_seed": random_seed,
    }
    destination = root / "models" / "challengers" / "forecasting-v1" / "logistic-model.json"
    _write_hashed_json(destination, body)
    return str(destination)


def load_challenger_model(path: str | Path) -> PortableLogisticModel:
    destination = Path(path)
    try:
        raw = destination.read_bytes()
        if not raw or len(raw) > 16_777_216:
            raise ValueError("model artifact integrity check failed")
        payload = json.loads(raw.decode("utf-8"))
        digest = payload.pop("sha256", None)
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if not isinstance(digest, str) or digest != hashlib.sha256(encoded).hexdigest():
            raise ValueError("model artifact integrity check failed")
        if payload.get("format_version") != 1 or payload.get("model_type") != "portable-logistic":
            raise ValueError("model artifact integrity check failed")
        schema = tuple(str(item) for item in payload["feature_schema"])
        coefficients = tuple(float(item) for item in payload["coefficients"])
        intercept = float(payload["intercept"])
        if not schema or len(schema) != len(coefficients):
            raise ValueError("model artifact integrity check failed")
        return PortableLogisticModel(schema, coefficients, intercept)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("model artifact integrity check failed") from exc


def _write_hashed_json(destination: Path, body: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload = json.dumps(
        {**body, "sha256": hashlib.sha256(encoded).hexdigest()},
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _write_metadata(
    root: Path,
    *,
    random_seed: int,
    feature_schema: tuple[str, ...],
    folds: tuple[WalkForwardFold, ...],
    model_status: dict[str, str],
    round_trip_cost: float,
) -> str:
    destination = root / "models" / "challengers" / "forecasting-v1" / "metadata.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {
        "format_version": 1,
        "candidate_id": "forecasting-v1",
        "random_seed": random_seed,
        "feature_schema": list(feature_schema),
        "round_trip_cost": round_trip_cost,
        "folds": [
            {
                key: value.isoformat()
                for key, value in asdict(fold).items()
            }
            for fold in folds
        ],
        "models": model_status,
        "formal_eligible": False,
        "status": "RESEARCH_ONLY",
        "reason": "promotion gates require mature shadow observations and manual approval",
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    artifact = {**body, "sha256": hashlib.sha256(encoded).hexdigest()}
    payload = json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2).encode()
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return str(destination)


__all__ = [
    "ChallengerTrainingResult",
    "WalkForwardFold",
    "build_forecast_labels",
    "load_challenger_model",
    "train_challengers",
]
