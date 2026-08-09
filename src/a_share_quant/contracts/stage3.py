"""Shared SignalFrame, PortfolioTarget, and ExecutionSpec contracts."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from typing import Any

import numpy as np
import pandas as pd

from a_share_quant.data.normalization import normalize_symbol

from .timing import next_trading_date, validate_execution_date

SIGNAL_FRAME_COLUMNS = (
    "date",
    "symbol",
    "strategy_id",
    "strategy_version",
    "model_version",
    "feature_version",
    "experiment_id",
    "raw_score",
    "normalized_score",
    "rank",
    "confidence",
    "signal_available_at",
    "intended_execution_date",
)


def _as_date(value: Any, *, name: str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{name} must contain valid dates")
    return parsed.date()


def _as_timestamps(values: pd.Series, *, name: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        raise ValueError(f"{name} must contain valid timestamps")
    for value in parsed:
        if pd.Timestamp(value).tzinfo is None:
            raise ValueError(f"{name} must be timezone-aware")
    return parsed


def _default_available_at(value: date) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(value, time(15, 5)), tz="Asia/Shanghai")


def _default_next_business_day(value: date) -> date:
    return (pd.Timestamp(value) + pd.offsets.BDay(1)).date()


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


@dataclass(frozen=True)
class SignalFrame:
    """Validated tabular signal contract shared by all Stage 3 adapters."""

    frame: pd.DataFrame
    t_plus_one: bool = True

    def __post_init__(self) -> None:
        validated = self._validate(self.frame, t_plus_one=self.t_plus_one)
        object.__setattr__(self, "frame", validated)

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, *, t_plus_one: bool = True) -> SignalFrame:
        return cls(frame=frame, t_plus_one=t_plus_one)

    @classmethod
    def from_predictions(
        cls,
        predictions: pd.DataFrame,
        *,
        experiment_id: str | None = None,
        trading_dates: list[date] | None = None,
        signal_available_at: datetime | pd.Timestamp | None = None,
        t_plus_one: bool = True,
    ) -> SignalFrame:
        """Convert a Stage 2 prediction frame while making timing explicit."""

        current = predictions.copy()
        if "date" not in current.columns:
            if "signal_date" not in current.columns:
                raise ValueError("predictions require date or signal_date")
            current = current.rename(columns={"signal_date": "date"})
        if experiment_id is not None and "experiment_id" not in current.columns:
            current["experiment_id"] = experiment_id
        if "experiment_id" not in current.columns:
            raise ValueError("predictions require experiment_id")

        current["date"] = current["date"].map(lambda value: _as_date(value, name="date"))
        if "raw_score" not in current.columns:
            raise ValueError("predictions require raw_score")
        current["raw_score"] = pd.to_numeric(current["raw_score"], errors="coerce")
        if current["raw_score"].isna().any() or not np.isfinite(current["raw_score"]).all():
            raise ValueError("raw_score must be finite")
        if "normalized_score" not in current.columns or "rank" not in current.columns:
            current = _add_score_fields(current)
        if "confidence" not in current.columns:
            current["confidence"] = current.groupby("date", sort=False)["raw_score"].rank(
                ascending=False, method="first", pct=True
            )
            current["confidence"] = 1.0 - current["confidence"] + 1.0 / current.groupby(
                "date", sort=False
            )["raw_score"].transform("count")
            current["confidence"] = current["confidence"].clip(0, 1)
        if "signal_available_at" not in current.columns:
            if signal_available_at is not None:
                current["signal_available_at"] = signal_available_at
            else:
                current["signal_available_at"] = current["date"].map(_default_available_at)
        if "intended_execution_date" not in current.columns:
            sessions = trading_dates or sorted(current["date"].unique().tolist())
            intended: list[date] = []
            for value in current["date"]:
                try:
                    intended.append(next_trading_date(value, sessions))
                except ValueError:
                    intended.append(_default_next_business_day(value))
            current["intended_execution_date"] = intended
        return cls.from_frame(current, t_plus_one=t_plus_one)

    @staticmethod
    def _validate(frame: pd.DataFrame, *, t_plus_one: bool) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("SignalFrame requires a pandas DataFrame")
        current = frame.copy()
        if "date" not in current.columns and "signal_date" in current.columns:
            current = current.rename(columns={"signal_date": "date"})
        missing = [column for column in SIGNAL_FRAME_COLUMNS if column not in current.columns]
        if missing:
            raise ValueError(f"SignalFrame missing fields: {', '.join(missing)}")
        if current.empty:
            raise ValueError("SignalFrame cannot be empty")
        current["date"] = current["date"].map(lambda value: _as_date(value, name="date"))
        current["intended_execution_date"] = current["intended_execution_date"].map(
            lambda value: _as_date(value, name="intended_execution_date")
        )
        current["signal_available_at"] = _as_timestamps(
            current["signal_available_at"], name="signal_available_at"
        )
        if current[list(SIGNAL_FRAME_COLUMNS)].isna().any().any():
            raise ValueError("SignalFrame fields cannot be null")
        for field in (
            "strategy_id",
            "strategy_version",
            "model_version",
            "feature_version",
            "experiment_id",
        ):
            if not current[field].astype(str).str.strip().all():
                raise ValueError(f"{field} is required")
        current["symbol"] = current["symbol"].map(normalize_symbol)
        for field in ("raw_score", "normalized_score", "rank", "confidence"):
            current[field] = pd.to_numeric(current[field], errors="coerce")
        if current[["raw_score", "normalized_score", "rank", "confidence"]].isna().any().any():
            raise ValueError("SignalFrame numeric fields must be finite")
        numeric = current[["raw_score", "normalized_score", "rank", "confidence"]]
        if not np.isfinite(numeric).all().all():
            raise ValueError("SignalFrame numeric fields must be finite")
        if not current["normalized_score"].between(0, 100).all():
            raise ValueError("normalized_score must be between 0 and 100")
        if not (current["rank"] >= 1).all() or not (current["rank"] % 1 == 0).all():
            raise ValueError("rank must be a positive integer")
        if not current["confidence"].between(0, 1).all():
            raise ValueError("confidence must be between 0 and 1")
        duplicate_key = ["date", "symbol", "strategy_id", "experiment_id"]
        if current.duplicated(duplicate_key).any():
            raise ValueError("SignalFrame contains duplicate signal keys")
        for signal_date, execution_date in current[
            ["date", "intended_execution_date"]
        ].itertuples(index=False, name=None):
            validate_execution_date(signal_date, execution_date, t_plus_one=t_plus_one)
        return current.reset_index(drop=True)

    def to_frame(self) -> pd.DataFrame:
        return self.frame.copy()

    def to_dicts(self) -> list[dict[str, Any]]:
        return [
            {key: _json_value(value) for key, value in row.items()}
            for row in self.frame.to_dict("records")
        ]


def _add_score_fields(frame: pd.DataFrame) -> pd.DataFrame:
    current = frame.copy()
    current["rank"] = 0
    current["normalized_score"] = 50.0
    for _, index in current.groupby("date", sort=False).groups.items():
        group = current.loc[index].sort_values(["raw_score", "symbol"], ascending=[False, True])
        scores = group["raw_score"]
        minimum, maximum = float(scores.min()), float(scores.max())
        normalized = 50.0 if minimum == maximum else (scores - minimum) / (maximum - minimum) * 100
        current.loc[group.index, "rank"] = range(1, len(group) + 1)
        current.loc[group.index, "normalized_score"] = normalized.to_numpy()
    return current


@dataclass(frozen=True)
class PortfolioTarget:
    date: date | str
    symbol: str
    target_weight: float
    source_strategy: str
    rebalance_reason: str
    target_quantity: float | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "date", _as_date(self.date, name="date"))
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if not self.source_strategy or not self.rebalance_reason:
            raise ValueError("source_strategy and rebalance_reason are required")
        if not math.isfinite(float(self.target_weight)) or not 0 <= float(self.target_weight) <= 1:
            raise ValueError("target_weight must be between 0 and 1")
        if self.target_quantity is not None and (
            not math.isfinite(float(self.target_quantity)) or float(self.target_quantity) < 0
        ):
            raise ValueError("target_quantity must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {key: _json_value(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class ExecutionSpec:
    signal_date: date | str
    execution_date: date | str
    execution_price_rule: str
    slippage: float
    commission: float
    tax: float
    t_plus_one: bool
    limit_rule: bool
    suspension_rule: bool
    minimum_order_size: float
    cash_constraint: bool

    def __post_init__(self) -> None:
        signal = _as_date(self.signal_date, name="signal_date")
        execution = _as_date(self.execution_date, name="execution_date")
        object.__setattr__(self, "signal_date", signal)
        object.__setattr__(self, "execution_date", execution)
        if not self.execution_price_rule.strip():
            raise ValueError("execution_price_rule is required")
        validate_execution_date(signal, execution, t_plus_one=self.t_plus_one)
        for name in ("slippage", "commission", "tax"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if not math.isfinite(float(self.minimum_order_size)) or self.minimum_order_size <= 0:
            raise ValueError("minimum_order_size must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {key: _json_value(value) for key, value in asdict(self).items()}
