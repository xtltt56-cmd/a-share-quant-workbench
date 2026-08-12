"""Immutable forecasts and matured outcomes used by the formal advisory layer."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from a_share_quant.contracts.modes import validate_data_mode
from a_share_quant.data.normalization import normalize_symbol

_RETURN = Decimal("0.000001")
_PRICE = Decimal("0.0001")
_ALLOWED_HORIZONS = frozenset({5, 10, 20})


def _decimal(value: Decimal | float | int | str, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _as_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


class AdvisoryState(str, Enum):
    BUY_CANDIDATE = "BUY_CANDIDATE"
    ADD_CANDIDATE = "ADD_CANDIDATE"
    HOLD = "HOLD"
    WATCH = "WATCH"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    BLOCKED = "BLOCKED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class ForecastRecord:
    """One immutable model forecast for exactly one post-close horizon."""

    forecast_id: str
    symbol: str
    generated_at: datetime
    data_cutoff: datetime
    reference_price: Decimal | float | int | str
    model_version: str
    data_version: str
    feature_version: str
    horizon_days: int
    maturity_date: date
    predicted_return: Decimal | float | int | str = Decimal("0")
    predicted_probability: Decimal | float | int | str = Decimal("0.5")
    predicted_rank: int = 1
    uncertainty: Decimal | float | int | str = Decimal("0.5")
    proposed_state: AdvisoryState | str = AdvisoryState.WATCH
    report_id: str = ""
    data_mode: str = "historical"
    benchmark_symbol: str = ""
    minimum_edge: Decimal | float | int | str = Decimal("0")
    calibration_version: str = ""
    interval_lower: Decimal | float | int | str | None = None
    interval_upper: Decimal | float | int | str | None = None
    interval_status: str = "UNCALIBRATED"
    artifact_sha256: str = ""

    def __post_init__(self) -> None:
        if not str(self.forecast_id).strip():
            raise ValueError("forecast_id is required")
        object.__setattr__(self, "forecast_id", str(self.forecast_id).strip())
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        generated_at = _as_utc(self.generated_at, field="generated_at")
        data_cutoff = _as_utc(self.data_cutoff, field="data_cutoff")
        if data_cutoff > generated_at:
            raise ValueError("data_cutoff cannot be later than generated_at")
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "data_cutoff", data_cutoff)
        if self.horizon_days not in _ALLOWED_HORIZONS:
            raise ValueError("horizon_days must be one of 5, 10, or 20")
        if not isinstance(self.maturity_date, date) or self.maturity_date <= data_cutoff.date():
            raise ValueError("maturity_date must be after the data cutoff")
        for field in ("model_version", "data_version", "feature_version"):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, str(getattr(self, field)).strip())
        reference_price = _decimal(self.reference_price, field="reference_price").quantize(_PRICE)
        if reference_price <= 0:
            raise ValueError("reference_price must be positive")
        object.__setattr__(self, "reference_price", reference_price)
        object.__setattr__(
            self,
            "predicted_return",
            _decimal(self.predicted_return, field="predicted_return").quantize(_RETURN),
        )
        probability = _decimal(self.predicted_probability, field="predicted_probability")
        if not Decimal("0") <= probability <= Decimal("1"):
            raise ValueError("predicted_probability must be between zero and one")
        object.__setattr__(self, "predicted_probability", probability.quantize(_RETURN))
        if not isinstance(self.predicted_rank, int) or isinstance(self.predicted_rank, bool):
            raise ValueError("predicted_rank must be a positive integer")
        if self.predicted_rank < 1:
            raise ValueError("predicted_rank must be a positive integer")
        uncertainty = _decimal(self.uncertainty, field="uncertainty")
        if not Decimal("0") <= uncertainty <= Decimal("1"):
            raise ValueError("uncertainty must be between zero and one")
        object.__setattr__(self, "uncertainty", uncertainty.quantize(_RETURN))
        proposed_state = (
            self.proposed_state
            if isinstance(self.proposed_state, AdvisoryState)
            else AdvisoryState(str(self.proposed_state).upper())
        )
        object.__setattr__(self, "proposed_state", proposed_state)
        object.__setattr__(self, "report_id", str(self.report_id).strip())
        object.__setattr__(self, "data_mode", validate_data_mode(self.data_mode))
        benchmark = str(self.benchmark_symbol).strip()
        object.__setattr__(
            self,
            "benchmark_symbol",
            normalize_symbol(benchmark) if benchmark else "",
        )
        minimum_edge = _decimal(self.minimum_edge, field="minimum_edge")
        if minimum_edge < 0:
            raise ValueError("minimum_edge cannot be negative")
        object.__setattr__(self, "minimum_edge", minimum_edge.quantize(_RETURN))
        object.__setattr__(self, "calibration_version", str(self.calibration_version).strip())
        interval_status = str(self.interval_status).strip().upper()
        if interval_status not in {"UNCALIBRATED", "CALIBRATED"}:
            raise ValueError("interval_status must be UNCALIBRATED or CALIBRATED")
        lower = _optional_decimal(self.interval_lower, field="interval_lower")
        upper = _optional_decimal(self.interval_upper, field="interval_upper")
        if (lower is None) != (upper is None):
            raise ValueError("interval_lower and interval_upper must be provided together")
        if lower is not None and (lower <= 0 or upper < lower):
            raise ValueError("forecast interval prices are invalid")
        if interval_status == "CALIBRATED" and lower is None:
            raise ValueError("calibrated interval requires lower and upper prices")
        object.__setattr__(
            self,
            "interval_lower",
            lower.quantize(_PRICE) if lower is not None else None,
        )
        object.__setattr__(
            self,
            "interval_upper",
            upper.quantize(_PRICE) if upper is not None else None,
        )
        object.__setattr__(self, "interval_status", interval_status)
        artifact = str(self.artifact_sha256).strip().lower()
        if artifact and not re.fullmatch(r"[0-9a-f]{64}", artifact):
            raise ValueError("artifact_sha256 must be a SHA-256 hex digest")
        object.__setattr__(self, "artifact_sha256", artifact)

    @classmethod
    def for_horizons(
        cls,
        *,
        symbol: str,
        generated_at: datetime,
        data_cutoff: datetime,
        reference_price: Decimal | float | int | str,
        model_version: str,
        data_version: str,
        feature_version: str,
        horizons: tuple[int, ...],
        maturity_dates: Mapping[int, date],
        predicted_returns: Mapping[int, Decimal | float | int | str] | None = None,
        predicted_probabilities: Mapping[int, Decimal | float | int | str] | None = None,
        predicted_ranks: Mapping[int, int] | None = None,
        uncertainties: Mapping[int, Decimal | float | int | str] | None = None,
        report_id: str = "",
        data_mode: str = "historical",
        benchmark_symbol: str = "",
        minimum_edge: Decimal | float | int | str = Decimal("0"),
        calibration_version: str = "",
        interval_lowers: Mapping[int, Decimal | float | int | str] | None = None,
        interval_uppers: Mapping[int, Decimal | float | int | str] | None = None,
        interval_status: str = "UNCALIBRATED",
        artifact_sha256: str = "",
    ) -> tuple[ForecastRecord, ...]:
        """Build the 5/10/20-day records with deterministic IDs for idempotency."""

        if not horizons or set(horizons).difference(_ALLOWED_HORIZONS):
            raise ValueError("horizons must be a non-empty subset of 5, 10, and 20")
        result: list[ForecastRecord] = []
        for horizon in horizons:
            if horizon not in maturity_dates:
                raise ValueError("maturity_dates must cover every requested horizon")
            identifier = _forecast_id(
                symbol=symbol,
                generated_at=generated_at,
                model_version=model_version,
                data_version=data_version,
                feature_version=feature_version,
                horizon=horizon,
            )
            result.append(
                cls(
                    forecast_id=identifier,
                    symbol=symbol,
                    generated_at=generated_at,
                    data_cutoff=data_cutoff,
                    reference_price=reference_price,
                    model_version=model_version,
                    data_version=data_version,
                    feature_version=feature_version,
                    horizon_days=horizon,
                    maturity_date=maturity_dates[horizon],
                    predicted_return=(predicted_returns or {}).get(horizon, Decimal("0")),
                    predicted_probability=(predicted_probabilities or {}).get(
                        horizon,
                        Decimal("0.5"),
                    ),
                    predicted_rank=(predicted_ranks or {}).get(horizon, 1),
                    uncertainty=(uncertainties or {}).get(horizon, Decimal("0.5")),
                    report_id=report_id,
                    data_mode=data_mode,
                    benchmark_symbol=benchmark_symbol,
                    minimum_edge=minimum_edge,
                    calibration_version=calibration_version,
                    interval_lower=(interval_lowers or {}).get(horizon),
                    interval_upper=(interval_uppers or {}).get(horizon),
                    interval_status=interval_status,
                    artifact_sha256=artifact_sha256,
                )
            )
        return tuple(result)


@dataclass(frozen=True)
class OutcomeRecord:
    forecast_id: str
    symbol: str
    horizon_days: int
    maturity_date: date
    matured_at: datetime
    realized_price: Decimal | float | int | str
    realized_return: Decimal | float | int | str

    def __post_init__(self) -> None:
        if not str(self.forecast_id).strip():
            raise ValueError("forecast_id is required")
        object.__setattr__(self, "forecast_id", str(self.forecast_id).strip())
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if self.horizon_days not in _ALLOWED_HORIZONS:
            raise ValueError("horizon_days must be one of 5, 10, or 20")
        if not isinstance(self.maturity_date, date):
            raise ValueError("maturity_date must be a date")
        object.__setattr__(self, "matured_at", _as_utc(self.matured_at, field="matured_at"))
        realized_price = _decimal(self.realized_price, field="realized_price").quantize(_PRICE)
        if realized_price <= 0:
            raise ValueError("realized_price must be positive")
        object.__setattr__(self, "realized_price", realized_price)
        object.__setattr__(
            self,
            "realized_return",
            _decimal(self.realized_return, field="realized_return").quantize(_RETURN),
        )


def _forecast_id(
    *,
    symbol: str,
    generated_at: datetime,
    model_version: str,
    data_version: str,
    feature_version: str,
    horizon: int,
) -> str:
    payload = "|".join(
        (
            normalize_symbol(symbol),
            _as_utc(generated_at, field="generated_at").isoformat(),
            str(model_version),
            str(data_version),
            str(feature_version),
            str(horizon),
        )
    )
    return f"forecast-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _optional_decimal(
    value: Decimal | float | int | str | None,
    *,
    field: str,
) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _decimal(value, field=field)
