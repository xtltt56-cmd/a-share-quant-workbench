"""Paper-only real-time trigger states, separate from official daily signals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from math import isfinite
from typing import Any

import pandas as pd

from a_share_quant.contracts.modes import validate_data_mode
from a_share_quant.contracts.realtime import DataQualityStatus
from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.signals.frequency import ModelFrequency


class RealtimeSignalState(str, Enum):
    WAIT = "WAIT"
    WATCH = "WATCH"
    READY = "READY"
    OVERHEATED = "OVERHEATED"
    RISK = "RISK"
    STALE_DATA = "STALE_DATA"


@dataclass(frozen=True)
class TriggerConfig:
    ready_score_min: float = 70.0
    watch_score_min: float = 55.0
    overheated_return_pct: float = 8.0
    overheated_volume_ratio: float = 3.0
    max_data_age_seconds: float = 60.0
    min_volume_ratio: float = 0.8
    max_single_position: float = 0.15

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> TriggerConfig:
        return cls(
            **{
                field: values[field]
                for field in cls.__dataclass_fields__
                if field in values
            }
        )


@dataclass(frozen=True)
class OfficialModelSignal:
    """A daily model output, never produced by the intraday trigger engine."""

    signal_date: date
    symbol: str
    normalized_score: float
    strategy_version: str
    frequency: ModelFrequency = ModelFrequency.DAILY
    name: str = ""
    model_version: str = "unknown"
    feature_version: str = "unknown"
    data_mode: str = "historical"
    source: str = "unknown"
    data_cutoff: date | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rank: int | None = None
    reasons: tuple[str, ...] = ()
    reference_price: float | None = None
    average_amount: float | None = None
    invalidation_price: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if self.frequency is not ModelFrequency.DAILY:
            raise ValueError("official model signals must be DAILY")
        if not 0 <= float(self.normalized_score) <= 100:
            raise ValueError("normalized_score must be between 0 and 100")
        if not self.strategy_version:
            raise ValueError("strategy_version is required")
        mode = validate_data_mode(self.data_mode)
        if mode == "fixture":
            raise ValueError("fixture data cannot become an official model signal")
        object.__setattr__(self, "data_mode", mode)
        source = str(self.source).strip()
        if source.casefold() in {"fixture", "replay", "synthetic", "test", "test-data"}:
            raise ValueError("non-market data cannot become an official model signal")
        if not source:
            raise ValueError("source is required")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "name", str(self.name).strip())
        for version_field in ("model_version", "feature_version"):
            value = str(getattr(self, version_field)).strip()
            if not value:
                raise ValueError(f"{version_field} is required")
            object.__setattr__(self, version_field, value)
        cutoff = self.data_cutoff or self.signal_date
        if not isinstance(cutoff, date) or cutoff > self.signal_date:
            raise ValueError("data_cutoff cannot be after signal_date")
        object.__setattr__(self, "data_cutoff", cutoff)
        generated_at = self.generated_at
        if generated_at.tzinfo is None or generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        object.__setattr__(self, "generated_at", generated_at.astimezone(timezone.utc))
        if self.rank is not None and (
            not isinstance(self.rank, int) or isinstance(self.rank, bool) or self.rank < 1
        ):
            raise ValueError("rank must be a positive integer")
        object.__setattr__(
            self,
            "reasons",
            tuple(reason for reason in (str(item).strip() for item in self.reasons) if reason),
        )
        for numeric_field in ("reference_price", "average_amount", "invalidation_price"):
            value = getattr(self, numeric_field)
            if value is None:
                continue
            number = float(value)
            if not isfinite(number) or number <= 0:
                raise ValueError(f"{numeric_field} must be positive and finite")
            object.__setattr__(self, numeric_field, number)


@dataclass(frozen=True)
class RealtimeMonitorSignal:
    symbol: str
    state: RealtimeSignalState
    score: float | None
    current_price: float | None
    observation_low: float | None
    observation_high: float | None
    support: float | None
    risk_level: float | None
    target_low: float | None
    target_high: float | None
    risk_reward_ratio: float | None
    suggested_position: float
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    evaluated_at: datetime
    can_generate_ready: bool
    official_model_signal: bool = False


class TriggerEngine:
    """Evaluate explainable monitor states without placing or scheduling orders."""

    def __init__(self, config: TriggerConfig | None = None) -> None:
        self.config = config or TriggerConfig()

    def evaluate(
        self,
        row: Mapping[str, Any] | pd.Series,
        *,
        now: datetime | None = None,
    ) -> RealtimeMonitorSignal:
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        get = row.get if isinstance(row, Mapping) else row.get
        symbol = normalize_symbol(str(get("symbol", "UNKNOWN")))
        timestamp = _parse_timestamp(get("timestamp_exchange"))
        quality = str(get("data_quality", "GOOD")).upper()
        is_stale = bool(get("is_stale", False)) or quality in {
            DataQualityStatus.STALE.value,
            DataQualityStatus.FAILED.value,
            "DATA_STALE",
            "DATA_UNAVAILABLE",
        }
        risks: list[str] = []
        reasons: list[str] = []
        if timestamp is None:
            is_stale = True
            risks.append("missing exchange timestamp")
        elif (reference - timestamp).total_seconds() > self.config.max_data_age_seconds:
            is_stale = True
            risks.append("quote exceeds freshness threshold")
        elif timestamp > reference:
            is_stale = True
            risks.append("quote timestamp is in the future")

        current_price = _number(get("current_price", get("last")))
        score = _number(get("score", get("normalized_score")))
        observation_low = _number(get("observation_low"))
        observation_high = _number(get("observation_high"))
        support = _number(get("support", observation_low))
        risk_level = _number(get("risk_level", get("stop_loss")))
        target_low = _number(get("target_low"))
        target_high = _number(get("target_high"))
        volume_ratio = _number(get("volume_ratio"))
        return_pct = _number(get("intraday_return_pct", get("change_pct"))) or 0.0

        if is_stale:
            state = RealtimeSignalState.STALE_DATA
        elif current_price is None or score is None:
            state = RealtimeSignalState.WAIT
            risks.append("required price or score is unavailable")
        elif bool(get("risk_flag", False)) or (
            risk_level is not None and current_price <= risk_level
        ):
            state = RealtimeSignalState.RISK
            risks.append("price reached the configured risk level")
        elif (
            return_pct >= self.config.overheated_return_pct
            and volume_ratio is not None
            and volume_ratio >= self.config.overheated_volume_ratio
        ):
            state = RealtimeSignalState.OVERHEATED
            risks.append("price and volume exceed the overheated thresholds")
        elif score >= self.config.ready_score_min and self._in_observation_zone(
            current_price, observation_low, observation_high
        ) and (volume_ratio is None or volume_ratio >= self.config.min_volume_ratio):
            state = RealtimeSignalState.READY
            reasons.append("score and observation-zone conditions are satisfied")
        elif score >= self.config.watch_score_min:
            state = RealtimeSignalState.WATCH
            reasons.append("score is in the watch range")
        else:
            state = RealtimeSignalState.WAIT

        risk_reward = _risk_reward(current_price, risk_level, target_low)
        suggested_position = (
            min(
                self.config.max_single_position,
                max(
                    0.0,
                    (score - self.config.ready_score_min)
                    / max(1.0, 100 - self.config.ready_score_min),
                )
                * self.config.max_single_position,
            )
            if state is RealtimeSignalState.READY and score is not None
            else 0.0
        )
        return RealtimeMonitorSignal(
            symbol=symbol,
            state=state,
            score=score,
            current_price=current_price,
            observation_low=observation_low,
            observation_high=observation_high,
            support=support,
            risk_level=risk_level,
            target_low=target_low,
            target_high=target_high,
            risk_reward_ratio=risk_reward,
            suggested_position=suggested_position,
            reasons=tuple(reasons),
            risks=tuple(risks),
            evaluated_at=reference,
            can_generate_ready=state is RealtimeSignalState.READY and not is_stale,
            official_model_signal=bool(get("official_model_signal", False)),
        )

    @staticmethod
    def _in_observation_zone(price: float, low: float | None, high: float | None) -> bool:
        return low is None or high is None or low <= price <= high


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = pd.Timestamp(value).to_pydatetime()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _risk_reward(price: float | None, risk: float | None, target: float | None) -> float | None:
    if price is None or risk is None or target is None or price <= risk:
        return None
    return (target - price) / (price - risk)
