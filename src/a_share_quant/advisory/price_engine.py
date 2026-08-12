"""Deterministic, conservative price guidance and paper position sizing."""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from a_share_quant.advisory.price_contracts import (
    GuidanceLevel,
    GuidanceState,
    PriceGuidancePlan,
    PricePlanType,
)
from a_share_quant.data.market_rules import resolve_security_rule
from a_share_quant.features.price_guidance import PriceFeatures


class PriceGuidanceValidationError(ValueError):
    """A conservative plan was rejected, with safe diagnostic reason codes."""

    def __init__(self, message: str, *, reason_codes: tuple[str, ...]) -> None:
        super().__init__(message)
        self.reason_codes = tuple(reason_codes)


class PriceGuidanceEngine:
    def __init__(self, *, config_version: str = "price-guidance-rule-v1") -> None:
        self.config_version = config_version

    def candidate_plan(
        self,
        features: PriceFeatures,
        *,
        promoted: bool,
        previous_protection: Decimal | float | int | str | None = None,
        calculation_date: Any | None = None,
        valid_for: Any | None = None,
        model_version: str = "rule-v1",
    ) -> PriceGuidancePlan:
        rule = resolve_security_rule(features.symbol, {})
        close = features.raw_close
        atr = features.to_actual(features.atr14)
        ma20 = features.to_actual(features.ma20)
        ma60 = features.to_actual(features.ma60)
        support = features.to_actual(features.support20)
        anchor = min(close, ma20 + Decimal("0.50") * atr)
        lower = anchor - Decimal("0.50") * atr
        upper = anchor + Decimal("0.25") * atr
        maximum = min(upper, close * Decimal("1.01"))
        invalidation = max(
            support - Decimal("0.25") * atr,
            ma60 - Decimal("0.50") * atr,
            close - Decimal("2.00") * atr,
        )
        protection = (
            max(Decimal(str(previous_protection)), invalidation)
            if previous_protection is not None
            else invalidation
        )
        lower = _round_up(lower, rule.tick_size)
        invalidation = _round_up(invalidation, rule.tick_size)
        upper = _round_down(upper, rule.tick_size)
        maximum = _round_down(maximum, rule.tick_size)
        protection = _round_up(protection, rule.tick_size)
        reasons: list[str] = []
        if not invalidation < lower <= upper <= maximum:
            if invalidation >= lower:
                reasons.append("INVALIDATION_NOT_BELOW_ENTRY")
            if lower > upper:
                reasons.append("ENTRY_RANGE_INVERTED")
            if upper > maximum:
                reasons.append("ENTRY_ABOVE_MAXIMUM")
            if not reasons:
                reasons.append("PRICE_BOUNDARIES_INCONSISTENT")
        risk_distance = (close - protection) / close
        if not Decimal("0.02") <= risk_distance <= Decimal("0.12"):
            reason = (
                "RISK_DISTANCE_TOO_LOW"
                if risk_distance < Decimal("0.02")
                else "RISK_DISTANCE_TOO_HIGH"
            )
            reasons.append(reason)
        if reasons:
            boundary_failure = any(
                reason.startswith(("INVALIDATION_", "ENTRY_", "PRICE_"))
                for reason in reasons
            )
            message = (
                "price boundaries are inconsistent"
                if boundary_failure
                else "risk distance is outside configured bounds"
            )
            raise PriceGuidanceValidationError(
                message,
                reason_codes=tuple(reasons),
            )
        calculation = calculation_date or features.cutoff
        valid = valid_for or calculation + timedelta(days=1)
        level = GuidanceLevel.CONDITIONS_MET if promoted else GuidanceLevel.RESEARCH_REFERENCE
        return PriceGuidancePlan(
            plan_id=f"price-{features.symbol}-{features.cutoff.isoformat()}",
            symbol=features.symbol,
            plan_type=PricePlanType.DAILY_CANDIDATE,
            guidance_level=level,
            state=(GuidanceState.CONDITIONS_MET if promoted else GuidanceState.RESEARCH_REFERENCE),
            calculation_date=calculation,
            valid_for=valid,
            entry_lower=lower,
            entry_upper=upper,
            maximum_acceptable_price=maximum,
            invalidation_price=invalidation,
            protection_price=protection,
            reduce_lower=ma20 + Decimal("2") * atr,
            reduce_upper=ma20 + Decimal("3") * atr,
            suggested_quantity=0,
            evidence_cutoff=_cutoff_utc(calculation),
            model_version=model_version,
            feature_version=features.feature_version,
            config_version=self.config_version,
            data_version=features.data_version,
            reason_codes=("RESEARCH_ONLY",) if not promoted else ("FORMAL_MODEL_PROMOTED",),
        )

    def position_size(
        self,
        *,
        equity: Decimal | float | int | str,
        cash: Decimal | float | int | str,
        price: Decimal | float | int | str,
        invalidation: Decimal | float | int | str,
        industry_value: Decimal | float | int | str,
    ) -> int:
        equity_d = Decimal(str(equity))
        cash_d = Decimal(str(cash))
        price_d = Decimal(str(price))
        invalidation_d = Decimal(str(invalidation))
        industry_d = Decimal(str(industry_value))
        if min(equity_d, cash_d, price_d, invalidation_d) <= 0 or invalidation_d >= price_d:
            return 0
        risk_cap = equity_d * Decimal("0.005") / (price_d - invalidation_d)
        position_cap = equity_d * Decimal("0.05") / price_d
        industry_cap = max(Decimal("0"), equity_d * Decimal("0.20") - industry_d) / price_d
        cash_cap = cash_d / price_d
        raw = min(risk_cap, position_cap, industry_cap, cash_cap)
        return max(0, int(raw // 100) * 100)


def _round_up(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _round_down(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _cutoff_utc(value: Any):
    from datetime import datetime, timezone

    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


__all__ = ["PriceGuidanceEngine", "PriceGuidanceValidationError"]
