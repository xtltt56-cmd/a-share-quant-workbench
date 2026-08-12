"""Conservative calibration for research-only probability and price intervals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ProbabilityCalibration:
    method: str
    status: str
    sample_count: int
    version: str


@dataclass(frozen=True)
class IntervalCalibration:
    status: str
    sample_count: int
    radius: float | None
    horizon_days: int
    as_of: datetime
    version: str = "conformal-v1"


class ProbabilityCalibrator:
    """Fit a probability map without permitting a premature production use."""

    _MINIMUM_SAMPLES = {"sigmoid": 500, "isotonic": 1000}

    def __init__(self, method: str = "isotonic") -> None:
        method = str(method).strip().lower()
        if method not in self._MINIMUM_SAMPLES:
            raise ValueError("method must be sigmoid or isotonic")
        self.method = method
        self.sample_count = 0
        self.status = "UNFITTED"
        self.version = f"{method}-v1"
        self._model: Any = None

    def fit(
        self,
        scores: Any,
        outcomes: Any,
        *,
        independent_samples: int | None = None,
    ) -> ProbabilityCalibrator:
        x = _finite_vector(scores, field="scores")
        y = _finite_vector(outcomes, field="outcomes")
        if len(x) != len(y):
            raise ValueError("scores and outcomes must have the same length")
        sample_count = int(independent_samples if independent_samples is not None else len(x))
        if sample_count < self._MINIMUM_SAMPLES[self.method]:
            raise ValueError(
                f"{self.method} calibration requires at least "
                f"{self._MINIMUM_SAMPLES[self.method]} independent samples"
            )
        if len(x) != sample_count and independent_samples is not None:
            raise ValueError("independent_samples cannot exceed the supplied rows")
        if np.any((y < 0) | (y > 1)) or len(np.unique(y)) < 2:
            raise ValueError("outcomes must contain both binary classes")
        if self.method == "isotonic":
            from sklearn.isotonic import IsotonicRegression

            model = IsotonicRegression(out_of_bounds="clip")
            model.fit(x, y)
        else:
            from sklearn.linear_model import LogisticRegression

            model = LogisticRegression(random_state=20260812, max_iter=2000)
            model.fit(x.reshape(-1, 1), y.astype(int))
        self._model = model
        self.sample_count = sample_count
        self.status = "CALIBRATED"
        return self

    def transform(self, scores: Any) -> np.ndarray:
        if self._model is None:
            raise ValueError("calibrator is not fitted")
        x = _finite_vector(scores, field="scores")
        if self.method == "isotonic":
            result = self._model.predict(x)
        else:
            result = self._model.predict_proba(x.reshape(-1, 1))[:, 1]
        return np.asarray(result, dtype="float64")


class RollingConformalCalibrator:
    """Estimate an 80th percentile absolute residual radius from matured data."""

    def __init__(self, *, minimum_samples: int = 500, quantile: float = 0.80) -> None:
        if minimum_samples < 1:
            raise ValueError("minimum_samples must be positive")
        if not 0 < quantile < 1:
            raise ValueError("quantile must be between zero and one")
        self.minimum_samples = int(minimum_samples)
        self.quantile = float(quantile)

    def fit(
        self,
        residuals: Any,
        *,
        horizon: int,
        as_of: datetime,
    ) -> IntervalCalibration:
        if int(horizon) not in {5, 10, 20}:
            raise ValueError("horizon must be one of 5, 10, or 20")
        cutoff = _aware_utc(as_of, field="as_of")
        eligible = _eligible_residuals(
            residuals,
            horizon=int(horizon),
            as_of=cutoff,
        )
        if len(eligible) < self.minimum_samples:
            return IntervalCalibration(
                status="UNCALIBRATED",
                sample_count=len(eligible),
                radius=None,
                horizon_days=int(horizon),
                as_of=cutoff,
            )
        dates = eligible["maturity_date"].dropna().sort_values().unique()
        if len(dates) > 252:
            eligible = eligible[eligible["maturity_date"].isin(dates[-252:])]
        if len(eligible) < self.minimum_samples:
            return IntervalCalibration(
                status="UNCALIBRATED",
                sample_count=len(eligible),
                radius=None,
                horizon_days=int(horizon),
                as_of=cutoff,
            )
        radius = float(
            eligible["absolute_residual"].quantile(self.quantile, interpolation="higher")
        )
        return IntervalCalibration(
            status="CALIBRATED",
            sample_count=len(eligible),
            radius=radius,
            horizon_days=int(horizon),
            as_of=cutoff,
        )


def _finite_vector(values: Any, *, field: str) -> np.ndarray:
    vector = np.asarray(values, dtype="float64").reshape(-1)
    if len(vector) == 0 or not np.isfinite(vector).all():
        raise ValueError(f"{field} must contain finite values")
    return vector


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _eligible_residuals(
    residuals: Any,
    *,
    horizon: int,
    as_of: datetime,
) -> pd.DataFrame:
    if isinstance(residuals, pd.DataFrame):
        required = {"absolute_residual", "horizon_days", "matured_at", "maturity_date"}
        missing = required.difference(residuals.columns)
        if missing:
            raise ValueError(f"missing calibration columns: {sorted(missing)}")
        frame = residuals.copy()
        frame["matured_at"] = pd.to_datetime(frame["matured_at"], utc=True, errors="raise")
        frame["maturity_date"] = pd.to_datetime(
            frame["maturity_date"], errors="raise"
        ).dt.date
        frame["absolute_residual"] = pd.to_numeric(
            frame["absolute_residual"], errors="raise"
        )
        frame = frame[
            (frame["horizon_days"].astype(int) == horizon)
            & (frame["matured_at"] <= pd.Timestamp(as_of))
            & (frame["maturity_date"] <= as_of.date())
        ]
    else:
        values = _finite_vector(residuals, field="residuals")
        frame = pd.DataFrame(
            {
                "absolute_residual": np.abs(values),
                "horizon_days": horizon,
                "matured_at": pd.Timestamp(as_of),
                "maturity_date": as_of.date(),
            }
        )
    frame = frame[np.isfinite(frame["absolute_residual"])].copy()
    frame = frame[frame["absolute_residual"] >= 0]
    return frame


__all__ = [
    "IntervalCalibration",
    "ProbabilityCalibration",
    "ProbabilityCalibrator",
    "RollingConformalCalibrator",
]
