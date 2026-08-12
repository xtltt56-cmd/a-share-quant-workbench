from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from a_share_quant.advisory.price_contracts import GuidanceLevel, GuidanceState, PricePlanType
from a_share_quant.advisory.price_engine import PriceGuidanceEngine
from a_share_quant.features.price_guidance import PriceFeatures


def feature_snapshot() -> PriceFeatures:
    return PriceFeatures(
        symbol="000001",
        cutoff=date(2026, 8, 12),
        close=Decimal("10"),
        raw_close=Decimal("10"),
        high=Decimal("10.2"),
        low=Decimal("9.8"),
        atr14=Decimal("0.4"),
        ma20=Decimal("9.9"),
        ma60=Decimal("9.4"),
        support20=Decimal("9.2"),
        amount20=Decimal("20000000"),
        adjustment_factor=Decimal("1"),
        data_version="sha256:bars",
    )


def test_v1_formula_is_deterministic_and_research_quantity_is_zero() -> None:
    plan = PriceGuidanceEngine().candidate_plan(feature_snapshot(), promoted=False)
    assert (plan.entry_lower, plan.entry_upper, plan.maximum_acceptable_price) == (
        Decimal("9.80"),
        Decimal("10.10"),
        Decimal("10.10"),
    )
    assert plan.invalidation_price == Decimal("9.20")
    assert plan.suggested_quantity == 0
    assert plan.guidance_level is GuidanceLevel.RESEARCH_REFERENCE


def test_promoted_plan_is_still_manual_and_has_valid_state() -> None:
    plan = PriceGuidanceEngine().candidate_plan(feature_snapshot(), promoted=True)
    assert plan.plan_type is PricePlanType.DAILY_CANDIDATE
    assert plan.state is GuidanceState.CONDITIONS_MET
    assert plan.manual_execution_required is True


def test_position_size_obeys_all_caps_and_round_lot() -> None:
    engine = PriceGuidanceEngine()
    assert engine.position_size(
        equity="100000",
        cash="30000",
        price="10",
        invalidation="9.5",
        industry_value="18000",
    ) == 200


def test_invalid_risk_distance_is_blocked() -> None:
    with pytest.raises(ValueError, match="risk distance"):
        PriceGuidanceEngine().candidate_plan(
            feature_snapshot(), previous_protection="9.95", promoted=False
        )
