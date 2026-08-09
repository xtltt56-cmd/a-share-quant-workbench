"""Unified, Qlib-independent signal schema."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Protocol

import pandas as pd

from a_share_quant.data.normalization import normalize_symbol


@dataclass(frozen=True)
class SignalRecord:
    signal_date: date
    symbol: str
    strategy_id: str
    strategy_version: str
    raw_score: float
    normalized_score: float
    rank: int
    confidence: float | None
    model_version: str
    feature_version: str
    data_version: str
    experiment_id: str
    signal_available_at: datetime | pd.Timestamp | None = None
    intended_execution_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if not self.strategy_id or not self.strategy_version:
            raise ValueError("strategy_id and strategy_version are required")
        if not self.model_version or not self.feature_version or not self.data_version:
            raise ValueError("model, feature and data versions are required")
        if not self.experiment_id:
            raise ValueError("experiment_id is required")
        if not math.isfinite(float(self.raw_score)):
            raise ValueError("raw_score must be finite")
        if not 0 <= float(self.normalized_score) <= 100:
            raise ValueError("normalized_score must be between 0 and 100")
        if self.rank < 1:
            raise ValueError("rank must be positive")
        if self.confidence is not None and not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if (
            self.signal_available_at is not None
            and pd.Timestamp(self.signal_available_at).tzinfo is None
        ):
            raise ValueError("signal_available_at must be timezone-aware")
        if (
            self.intended_execution_date is not None
            and self.intended_execution_date <= self.signal_date
        ):
            raise ValueError("intended execution date must be after signal date")

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["date"] = self.signal_date
        return result

    @property
    def date(self) -> date:
        """Alias used by downstream tabular signal consumers."""

        return self.signal_date


class SignalProvider(Protocol):
    strategy_id: str

    def generate_signals(
        self,
        feature_frame: pd.DataFrame,
        *,
        signal_date: date,
        experiment_id: str,
    ) -> list[SignalRecord]:
        """Generate project-owned signals without exposing Qlib objects."""
