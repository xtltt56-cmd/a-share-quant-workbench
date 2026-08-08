"""Time-only split utilities; random shuffles are intentionally unavailable."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd


@dataclass(frozen=True)
class TimeSplit:
    train: tuple[date, date]
    validation: tuple[date, date]
    test: tuple[date, date]

    def as_dict(self) -> dict[str, tuple[date, date]]:
        return {
            "train": self.train,
            "validation": self.validation,
            "test": self.test,
        }


def fixed_time_split(
    dates: Iterable[date | str | datetime],
    *,
    train_ratio: float,
    validation_ratio: float,
    test_ratio: float,
) -> TimeSplit:
    normalized = _normalize_dates(dates)
    _validate_ratios(train_ratio, validation_ratio, test_ratio)
    if len(normalized) < 3:
        raise ValueError("fixed time split requires at least three dates")
    train_end = max(1, int(len(normalized) * train_ratio))
    validation_end = train_end + max(1, int(len(normalized) * validation_ratio))
    if validation_end >= len(normalized):
        validation_end = len(normalized) - 1
        train_end = min(train_end, validation_end - 1)
    return TimeSplit(
        train=(normalized[0], normalized[train_end - 1]),
        validation=(normalized[train_end], normalized[validation_end - 1]),
        test=(normalized[validation_end], normalized[-1]),
    )


def rolling_walk_forward(
    dates: Iterable[date | str | datetime],
    *,
    min_train_dates: int,
    validation_dates: int,
    test_dates: int,
    step_dates: int,
) -> list[TimeSplit]:
    normalized = _normalize_dates(dates)
    if min_train_dates < 1 or validation_dates < 1 or test_dates < 1 or step_dates < 1:
        raise ValueError("Walk-Forward window sizes must be positive")
    windows: list[TimeSplit] = []
    start = 0
    while start + min_train_dates + validation_dates + test_dates <= len(normalized):
        train_end = start + min_train_dates
        validation_end = train_end + validation_dates
        test_end = validation_end + test_dates
        windows.append(
            TimeSplit(
                train=(normalized[0], normalized[train_end - 1]),
                validation=(normalized[train_end], normalized[validation_end - 1]),
                test=(normalized[validation_end], normalized[test_end - 1]),
            )
        )
        start += step_dates
    return windows


def _normalize_dates(values: Iterable[date | str | datetime]) -> list[date]:
    parsed = []
    for value in values:
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            raise ValueError(f"invalid split date: {value!r}")
        parsed.append(timestamp.date())
    return sorted(set(parsed))


def _validate_ratios(train: float, validation: float, test: float) -> None:
    if min(train, validation, test) <= 0 or abs(train + validation + test - 1) > 1e-9:
        raise ValueError("time split ratios must be positive and sum to 1")
