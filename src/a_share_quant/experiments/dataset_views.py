"""Explicit train/validation/test views for optimization isolation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd


def _period(value: date | str) -> pd.Timestamp:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid split date: {value!r}")
    return parsed.normalize()


@dataclass(frozen=True)
class DatasetView:
    """Data plus non-overlapping date boundaries used by model selection."""

    frame: pd.DataFrame
    train: tuple[date | str, date | str]
    validation: tuple[date | str, date | str]
    test: tuple[date | str, date | str]

    def __post_init__(self) -> None:
        if not isinstance(self.frame, pd.DataFrame) or self.frame.empty:
            raise ValueError("DatasetView requires a non-empty DataFrame")
        date_column = "signal_date" if "signal_date" in self.frame.columns else "date"
        if date_column not in self.frame.columns:
            raise ValueError("DatasetView requires date or signal_date")
        boundaries = [
            (_period(period[0]), _period(period[1]))
            for period in (self.train, self.validation, self.test)
        ]
        if any(start > end for start, end in boundaries):
            raise ValueError("split start must not be after split end")
        if boundaries[0][1] >= boundaries[1][0] or boundaries[1][1] >= boundaries[2][0]:
            raise ValueError("train, validation, and test splits must not overlap")

    @property
    def date_column(self) -> str:
        return "signal_date" if "signal_date" in self.frame.columns else "date"

    def _select(self, period: tuple[date | str, date | str]) -> pd.DataFrame:
        values = pd.to_datetime(self.frame[self.date_column])
        start, end = (_period(period[0]), _period(period[1]))
        mask = values.between(start, end, inclusive="both")
        return self.frame.loc[mask].copy()

    def train_frame(self) -> pd.DataFrame:
        return self._select(self.train)

    def validation_frame(self) -> pd.DataFrame:
        return self._select(self.validation)

    def test_frame(self) -> pd.DataFrame:
        return self._select(self.test)

    def optimization_frame(self) -> pd.DataFrame:
        """Return only TRAIN + VALIDATION for fitting or weight search."""

        return pd.concat([self.train_frame(), self.validation_frame()], ignore_index=True)

    def optimization_signature(self) -> str:
        frame = self.optimization_frame().sort_index(axis=1).sort_values(
            by=[self.date_column], kind="stable"
        )
        payload: dict[str, Any] = {
            "columns": list(frame.columns),
            "rows": frame.astype(object).where(frame.notna(), None).to_dict("records"),
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
