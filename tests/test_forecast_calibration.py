from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from a_share_quant.advisory.contracts import ForecastRecord
from a_share_quant.research.calibration import (
    ProbabilityCalibrator,
    RollingConformalCalibrator,
)

NOW = datetime(2026, 8, 12, 8, tzinfo=timezone.utc)


def test_isotonic_requires_1000_independent_samples() -> None:
    with pytest.raises(ValueError, match="1000"):
        ProbabilityCalibrator("isotonic").fit(
            np.linspace(0.1, 0.9, 999),
            np.arange(999) % 2,
        )


def test_interval_is_uncalibrated_below_500() -> None:
    result = RollingConformalCalibrator().fit(
        np.linspace(0.01, 0.20, 499),
        horizon=5,
        as_of=NOW,
    )
    assert result.status == "UNCALIBRATED"
    assert result.radius is None


def test_future_maturities_are_excluded() -> None:
    matured = pd.DataFrame(
        {
            "absolute_residual": np.linspace(0.01, 0.10, 600),
            "horizon_days": [5] * 600,
            "matured_at": [NOW - timedelta(days=1)] * 500
            + [NOW + timedelta(days=1)] * 100,
            "maturity_date": [NOW.date()] * 500
            + [(NOW + timedelta(days=1)).date()] * 100,
        }
    )
    result = RollingConformalCalibrator().fit(matured, horizon=5, as_of=NOW)
    assert result.sample_count == 500
    assert result.status == "CALIBRATED"
    assert result.radius is not None


def test_forecast_record_carries_calibration_and_interval_evidence() -> None:
    record = ForecastRecord(
        forecast_id="forecast-calibrated",
        symbol="000001",
        generated_at=NOW,
        data_cutoff=NOW,
        reference_price="10",
        model_version="challenger-v1",
        data_version="daily-v1",
        feature_version="features-v1",
        horizon_days=5,
        maturity_date=NOW.date() + timedelta(days=5),
        benchmark_symbol="000300",
        minimum_edge="0.0062",
        calibration_version="isotonic-v1/conformal-v1",
        interval_lower="9.50",
        interval_upper="10.80",
        interval_status="CALIBRATED",
        artifact_sha256="a" * 64,
    )
    assert record.benchmark_symbol == "000300"
    assert record.interval_status == "CALIBRATED"
    assert record.artifact_sha256 == "a" * 64
