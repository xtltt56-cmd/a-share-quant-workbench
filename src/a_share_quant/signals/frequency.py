"""Explicit model/data frequency contracts."""

from __future__ import annotations

from enum import Enum


class ModelFrequencyError(ValueError):
    """Raised when a model is invoked with a different frequency."""


class ModelFrequency(str, Enum):
    DAILY = "daily"
    INTRADAY_1M = "1m"
    INTRADAY_5M = "5m"
    INTRADAY_15M = "15m"
    INTRADAY_30M = "30m"
    INTRADAY_60M = "60m"

    @classmethod
    def parse(cls, value: str | ModelFrequency) -> ModelFrequency:
        if isinstance(value, cls):
            return value
        raw = str(value).lower()
        aliases = {"day": cls.DAILY, "d": cls.DAILY}
        if raw in aliases:
            return aliases[raw]
        try:
            return cls(raw)
        except ValueError as exc:
            raise ModelFrequencyError(f"unsupported frequency: {value}") from exc


def ensure_model_frequency(
    model_frequency: str | ModelFrequency, data_frequency: str | ModelFrequency
) -> bool:
    model = ModelFrequency.parse(model_frequency)
    data = ModelFrequency.parse(data_frequency)
    if model is ModelFrequency.DAILY and data is not ModelFrequency.DAILY:
        raise ModelFrequencyError(
            "DAILY models cannot be invoked with intraday data; use an explicit intraday model"
        )
    if model is not data:
        raise ModelFrequencyError(
            f"model frequency {model.value} does not match data frequency {data.value}"
        )
    return True
