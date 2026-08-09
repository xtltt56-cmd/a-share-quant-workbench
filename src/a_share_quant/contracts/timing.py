"""Point-in-time and execution-date contracts for Stage 3."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime

import pandas as pd


def _timestamp(value: datetime | pd.Timestamp, *, name: str) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError(f"{name} must be a valid timestamp")
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class TimeSemantics:
    """The ordered timestamps associated with one signal decision."""

    data_available_at: datetime | pd.Timestamp
    signal_at: datetime | pd.Timestamp
    decision_at: datetime | pd.Timestamp
    order_at: datetime | pd.Timestamp
    execution_at: datetime | pd.Timestamp

    def validate(self) -> bool:
        values = [
            _timestamp(self.data_available_at, name="data_available_at"),
            _timestamp(self.signal_at, name="signal_at"),
            _timestamp(self.decision_at, name="decision_at"),
            _timestamp(self.order_at, name="order_at"),
            _timestamp(self.execution_at, name="execution_at"),
        ]
        if any(left > right for left, right in zip(values, values[1:])):
            raise ValueError("time semantics must be ordered")
        if values[-1] <= values[1]:
            raise ValueError("execution_at must be after signal_at")
        return True

    def to_dict(self) -> dict[str, str]:
        self.validate()
        return {key: _timestamp(value, name=key).isoformat() for key, value in asdict(self).items()}


def _as_date(value: date | str | pd.Timestamp) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid date: {value!r}")
    return parsed.date()


def next_trading_date(
    signal_date: date | str | pd.Timestamp,
    trading_dates: Iterable[date | str | pd.Timestamp],
) -> date:
    """Return the first supplied session strictly after ``signal_date``."""

    current = _as_date(signal_date)
    sessions = sorted({_as_date(value) for value in trading_dates})
    for session in sessions:
        if session > current:
            return session
    raise ValueError(f"no next trading date after {current.isoformat()}")


def validate_execution_date(
    signal_date: date | str | pd.Timestamp,
    execution_date: date | str | pd.Timestamp,
    *,
    t_plus_one: bool = True,
) -> bool:
    """Validate that execution cannot occur on the signal bar.

    ``t_plus_one`` is retained in the public contract so callers must declare
    their policy. This project always rejects same-bar execution, even when a
    caller opts out of the A-share sell restriction.
    """

    signal = _as_date(signal_date)
    execution = _as_date(execution_date)
    if execution <= signal:
        raise ValueError(
            "execution date must be after signal date "
            "(execution_date must be after signal_date)"
        )
    if t_plus_one and execution <= signal:
        raise ValueError(
            "T+1 execution date must be after signal date "
            "(execution_date must be after signal_date)"
        )
    return True
