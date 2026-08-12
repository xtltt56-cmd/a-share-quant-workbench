"""Read-only intraday state machine for a frozen price guidance plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from a_share_quant.advisory.price_contracts import GuidanceState, PriceGuidancePlan


@dataclass(frozen=True)
class PriceGuidanceObservationResult:
    plan_id: str
    symbol: str
    state: GuidanceState
    current_price: Decimal | None
    entry_lower: Decimal | None
    entry_upper: Decimal | None
    maximum_acceptable_price: Decimal | None
    invalidation_price: Decimal | None
    quote_timestamp: datetime | None
    observed_at: datetime
    data_quality: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "state": self.state.value,
            "current_price": _format(self.current_price),
            "entry_lower": _format(self.entry_lower),
            "entry_upper": _format(self.entry_upper),
            "maximum_acceptable_price": _format(self.maximum_acceptable_price),
            "invalidation_price": _format(self.invalidation_price),
            "quote_timestamp": self.quote_timestamp.isoformat() if self.quote_timestamp else None,
            "observed_at": self.observed_at.isoformat(),
            "data_quality": self.data_quality,
            "reason_codes": list(self.reason_codes),
            "manual_execution_required": True,
        }


class PriceGuidanceOverlay:
    def evaluate(
        self,
        plan: PriceGuidancePlan,
        quote: Mapping[str, Any],
        *,
        now: datetime,
    ) -> PriceGuidanceObservationResult:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if now.date() > plan.valid_for:
            raise ValueError("plan is no longer valid")
        quality = str(quote.get("data_quality", "FAILED")).upper()
        quote_timestamp = _timestamp(quote.get("quote_timestamp"))
        observed_at = _timestamp(quote.get("observed_at")) or now
        if observed_at > now:
            raise ValueError("observed_at is in the future")
        if quote_timestamp is not None and quote_timestamp > now:
            raise ValueError("quote timestamp is in the future")
        raw_price = quote.get("current_price", quote.get("last"))
        price = None if raw_price is None else Decimal(str(raw_price))
        if price is not None and (not price.is_finite() or price <= 0):
            raise ValueError("current price must be positive")
        if quality != "GOOD" or price is None or quote_timestamp is None:
            state = GuidanceState.NO_RELIABLE_GUIDANCE
            reasons = ("DATA_QUALITY_NOT_GOOD",)
        elif plan.invalidation_price is not None and price < plan.invalidation_price:
            state = GuidanceState.INVALIDATED
            reasons = ("PRICE_BELOW_INVALIDATION",)
        elif plan.maximum_acceptable_price is not None and price > plan.maximum_acceptable_price:
            state = GuidanceState.PRICE_TOO_HIGH
            reasons = ("PRICE_ABOVE_MAXIMUM",)
        elif (
            plan.entry_lower is not None
            and plan.entry_upper is not None
            and plan.entry_lower <= price <= plan.entry_upper
        ):
            state = (
                GuidanceState.CONDITIONS_MET
                if plan.guidance_level.value == "CONDITIONS_MET"
                else GuidanceState.RESEARCH_REFERENCE
            )
            reasons = ("IN_ENTRY_RANGE",)
        else:
            state = GuidanceState.WAIT_FOR_PRICE
            reasons = ("WAIT_FOR_ENTRY_RANGE",)
        return PriceGuidanceObservationResult(
            plan_id=plan.plan_id,
            symbol=plan.symbol,
            state=state,
            current_price=price,
            entry_lower=plan.entry_lower,
            entry_upper=plan.entry_upper,
            maximum_acceptable_price=plan.maximum_acceptable_price,
            invalidation_price=plan.invalidation_price,
            quote_timestamp=quote_timestamp,
            observed_at=observed_at,
            data_quality=quality,
            reason_codes=reasons,
        )


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _format(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


__all__ = ["PriceGuidanceObservationResult", "PriceGuidanceOverlay"]
