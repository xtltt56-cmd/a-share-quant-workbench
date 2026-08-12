"""Immutable, paper-only contracts for deterministic price guidance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any

from a_share_quant.data.normalization import normalize_symbol

_PRICE_QUANTUM = Decimal("0.01")
_ALLOWED_FIELDS = frozenset(
    {
        "plan_id",
        "symbol",
        "plan_type",
        "guidance_level",
        "state",
        "calculation_date",
        "valid_for",
        "entry_lower",
        "entry_upper",
        "maximum_acceptable_price",
        "invalidation_price",
        "protection_price",
        "reduce_lower",
        "reduce_upper",
        "suggested_quantity",
        "evidence_cutoff",
        "model_version",
        "feature_version",
        "config_version",
        "data_version",
        "reason_codes",
        "manual_execution_required",
    }
)


class PricePlanType(str, Enum):
    DAILY_CANDIDATE = "DAILY_CANDIDATE"
    HOLDING = "HOLDING"


class GuidanceLevel(str, Enum):
    RESEARCH_REFERENCE = "RESEARCH_REFERENCE"
    CONDITIONS_MET = "CONDITIONS_MET"


class GuidanceState(str, Enum):
    RESEARCH_REFERENCE = "RESEARCH_REFERENCE"
    CONDITIONS_MET = "CONDITIONS_MET"
    WAIT_FOR_PRICE = "WAIT_FOR_PRICE"
    PRICE_TOO_HIGH = "PRICE_TOO_HIGH"
    INVALIDATED = "INVALIDATED"
    RISK_ALERT = "RISK_ALERT"
    NO_RELIABLE_GUIDANCE = "NO_RELIABLE_GUIDANCE"
    T_PLUS_ONE_BLOCKED = "T_PLUS_ONE_BLOCKED"
    REDUCE_WATCH = "REDUCE_WATCH"
    HOLD_WATCH = "HOLD_WATCH"


@dataclass(frozen=True)
class PriceGuidanceObservation:
    observation_id: str
    plan_id: str
    symbol: str
    observed_at: datetime
    quote_timestamp: datetime
    current_price: Decimal | float | int | str
    data_quality: str
    state: GuidanceState | str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in ("observation_id", "plan_id"):
            value = str(getattr(self, field)).strip()
            if not value:
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, field="observed_at"))
        object.__setattr__(
            self,
            "quote_timestamp",
            _utc(self.quote_timestamp, field="quote_timestamp"),
        )
        if self.observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("observed_at cannot be in the future")
        object.__setattr__(
            self,
            "current_price",
            _decimal(self.current_price, field="current_price"),
        )
        quality = str(self.data_quality).strip().upper()
        if quality not in {"GOOD", "STALE", "FAILED", "DEGRADED"}:
            raise ValueError("invalid data quality")
        object.__setattr__(self, "data_quality", quality)
        state = (
            self.state
            if isinstance(self.state, GuidanceState)
            else GuidanceState(str(self.state))
        )
        object.__setattr__(self, "state", state)
        reasons = tuple(str(reason).strip() for reason in self.reason_codes)
        if any(not reason for reason in reasons):
            raise ValueError("reason_codes cannot contain empty values")
        object.__setattr__(self, "reason_codes", reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "observed_at": self.observed_at.isoformat(),
            "quote_timestamp": self.quote_timestamp.isoformat(),
            "current_price": _format_decimal(self.current_price),
            "data_quality": self.data_quality,
            "state": self.state.value,
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PriceGuidanceObservation:
        try:
            values = dict(payload)
            values["observed_at"] = datetime.fromisoformat(values["observed_at"])
            values["quote_timestamp"] = datetime.fromisoformat(values["quote_timestamp"])
            values["state"] = GuidanceState(values["state"])
            values["reason_codes"] = tuple(values["reason_codes"])
            return cls(**values)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("price guidance observation is invalid") from exc


def _decimal(value: Any, *, field: str, quantize: bool = True) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    if quantize:
        parsed = parsed.quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date(value: date, *, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{field} must be a date")
    return value


@dataclass(frozen=True)
class PriceGuidancePlan:
    """A frozen next-session plan; it never places an order."""

    plan_id: str
    symbol: str
    plan_type: PricePlanType | str
    guidance_level: GuidanceLevel | str
    state: GuidanceState | str
    calculation_date: date
    valid_for: date
    entry_lower: Decimal | float | int | str | None
    entry_upper: Decimal | float | int | str | None
    maximum_acceptable_price: Decimal | float | int | str | None
    invalidation_price: Decimal | float | int | str | None
    protection_price: Decimal | float | int | str | None
    reduce_lower: Decimal | float | int | str | None
    reduce_upper: Decimal | float | int | str | None
    suggested_quantity: int
    evidence_cutoff: datetime
    model_version: str
    feature_version: str
    config_version: str
    data_version: str
    reason_codes: tuple[str, ...]
    manual_execution_required: bool = True

    def __post_init__(self) -> None:
        plan_id = str(self.plan_id).strip()
        if not plan_id:
            raise ValueError("plan_id is required")
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        for field, enum_type in (
            ("plan_type", PricePlanType),
            ("guidance_level", GuidanceLevel),
            ("state", GuidanceState),
        ):
            value = getattr(self, field)
            try:
                value = value if isinstance(value, enum_type) else enum_type(str(value).upper())
            except ValueError as exc:
                raise ValueError(f"invalid {field}") from exc
            object.__setattr__(self, field, value)
        calculation_date = _date(self.calculation_date, field="calculation_date")
        valid_for = _date(self.valid_for, field="valid_for")
        if valid_for <= calculation_date:
            raise ValueError("valid_for must be after calculation_date")
        object.__setattr__(self, "calculation_date", calculation_date)
        object.__setattr__(self, "valid_for", valid_for)
        for field in (
            "entry_lower",
            "entry_upper",
            "maximum_acceptable_price",
            "invalidation_price",
            "protection_price",
            "reduce_lower",
            "reduce_upper",
        ):
            object.__setattr__(self, field, _decimal(getattr(self, field), field=field))
        boundaries = (
            self.invalidation_price,
            self.entry_lower,
            self.entry_upper,
            self.maximum_acceptable_price,
        )
        if any(value is not None for value in boundaries):
            if any(value is None for value in boundaries):
                raise ValueError("price boundaries are inconsistent")
            invalidation, lower, upper, maximum = boundaries
            assert invalidation is not None
            assert lower is not None
            assert upper is not None
            assert maximum is not None
            if not invalidation < lower <= upper <= maximum:
                raise ValueError("price boundaries are inconsistent")
        if self.reduce_lower is not None and self.reduce_upper is not None:
            if self.reduce_lower > self.reduce_upper:
                raise ValueError("price boundaries are inconsistent")
        elif self.reduce_lower is not None or self.reduce_upper is not None:
            raise ValueError("price boundaries are inconsistent")
        if not isinstance(self.suggested_quantity, int) or isinstance(
            self.suggested_quantity, bool
        ):
            raise ValueError("suggested_quantity must be a non-negative integer")
        if self.suggested_quantity < 0:
            raise ValueError("suggested_quantity must be a non-negative integer")
        if self.guidance_level is GuidanceLevel.RESEARCH_REFERENCE and self.suggested_quantity:
            raise ValueError("research guidance quantity must be zero")
        if self.manual_execution_required is not True:
            raise ValueError("manual execution is required")
        object.__setattr__(
            self,
            "evidence_cutoff",
            _utc(self.evidence_cutoff, field="evidence_cutoff"),
        )
        for field in ("model_version", "feature_version", "config_version", "data_version"):
            value = str(getattr(self, field)).strip()
            if not value:
                raise ValueError(f"{field} is required")
            object.__setattr__(self, field, value)
        if isinstance(self.reason_codes, str):
            raise ValueError("reason_codes must be a sequence")
        reasons = tuple(str(reason).strip() for reason in self.reason_codes)
        if any(not reason for reason in reasons):
            raise ValueError("reason_codes cannot contain empty values")
        object.__setattr__(self, "reason_codes", reasons)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "plan_type": self.plan_type.value,
            "guidance_level": self.guidance_level.value,
            "state": self.state.value,
            "calculation_date": self.calculation_date.isoformat(),
            "valid_for": self.valid_for.isoformat(),
            "entry_lower": _format_decimal(self.entry_lower),
            "entry_upper": _format_decimal(self.entry_upper),
            "maximum_acceptable_price": _format_decimal(self.maximum_acceptable_price),
            "invalidation_price": _format_decimal(self.invalidation_price),
            "protection_price": _format_decimal(self.protection_price),
            "reduce_lower": _format_decimal(self.reduce_lower),
            "reduce_upper": _format_decimal(self.reduce_upper),
            "suggested_quantity": self.suggested_quantity,
            "evidence_cutoff": self.evidence_cutoff.isoformat(),
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "config_version": self.config_version,
            "data_version": self.data_version,
            "reason_codes": list(self.reason_codes),
            "manual_execution_required": self.manual_execution_required,
        }
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PriceGuidancePlan:
        if not isinstance(payload, dict):
            raise ValueError("price guidance plan is invalid")
        unknown = set(payload).difference(_ALLOWED_FIELDS)
        if unknown:
            raise ValueError("unknown fields in price guidance plan")
        if set(payload) != _ALLOWED_FIELDS - {"manual_execution_required"} and (
            "manual_execution_required" not in payload
        ):
            raise ValueError("price guidance plan is missing fields")
        try:
            values = dict(payload)
            values["plan_type"] = PricePlanType(values["plan_type"])
            values["guidance_level"] = GuidanceLevel(values["guidance_level"])
            values["state"] = GuidanceState(values["state"])
            values["calculation_date"] = date.fromisoformat(values["calculation_date"])
            values["valid_for"] = date.fromisoformat(values["valid_for"])
            values["evidence_cutoff"] = datetime.fromisoformat(values["evidence_cutoff"])
            values["reason_codes"] = tuple(values["reason_codes"])
            return cls(**values)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, ValueError) and str(exc) in {
                "research guidance quantity must be zero",
                "price boundaries are inconsistent",
            }:
                raise
            raise ValueError("price guidance plan is invalid") from exc


def _format_decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


__all__ = [
    "GuidanceLevel",
    "GuidanceState",
    "PriceGuidanceObservation",
    "PriceGuidancePlan",
    "PricePlanType",
]
