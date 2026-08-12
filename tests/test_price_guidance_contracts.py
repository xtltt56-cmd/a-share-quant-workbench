from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from a_share_quant.advisory.price_contracts import (
    GuidanceLevel,
    GuidanceState,
    PriceGuidancePlan,
    PricePlanType,
)


def valid_plan(**overrides: object) -> PriceGuidancePlan:
    values: dict[str, object] = {
        "plan_id": "plan-000001-20260812",
        "symbol": "000001",
        "plan_type": PricePlanType.DAILY_CANDIDATE,
        "guidance_level": GuidanceLevel.RESEARCH_REFERENCE,
        "state": GuidanceState.RESEARCH_REFERENCE,
        "calculation_date": date(2026, 8, 12),
        "valid_for": date(2026, 8, 13),
        "entry_lower": Decimal("10.70"),
        "entry_upper": Decimal("10.90"),
        "maximum_acceptable_price": Decimal("10.90"),
        "invalidation_price": Decimal("10.50"),
        "protection_price": None,
        "reduce_lower": None,
        "reduce_upper": None,
        "suggested_quantity": 0,
        "evidence_cutoff": datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc),
        "model_version": "rule-v1",
        "feature_version": "price-features-v1",
        "config_version": "price-guidance-rule-v1",
        "data_version": "bars-20260812-sha256",
        "reason_codes": ("RESEARCH_ONLY",),
    }
    values.update(overrides)
    return PriceGuidancePlan(**values)


def test_research_plan_is_never_executable() -> None:
    with pytest.raises(ValueError, match="research guidance quantity must be zero"):
        valid_plan(guidance_level="RESEARCH_REFERENCE", suggested_quantity=100)


def test_price_order_is_strict() -> None:
    with pytest.raises(ValueError, match="price boundaries are inconsistent"):
        valid_plan(invalidation_price="10.80", entry_lower="10.70")


def test_plan_round_trip_is_stable() -> None:
    plan = valid_plan()
    assert PriceGuidancePlan.from_dict(plan.to_dict()) == plan


def test_unknown_fields_and_naive_cutoff_are_rejected() -> None:
    payload = valid_plan().to_dict()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        PriceGuidancePlan.from_dict(payload)

    with pytest.raises(ValueError, match="timezone-aware"):
        valid_plan(evidence_cutoff=datetime(2026, 8, 12, 8, 0))


def test_conditions_met_can_have_quantity_but_manual_execution_remains_required() -> None:
    plan = valid_plan(
        guidance_level=GuidanceLevel.CONDITIONS_MET,
        state=GuidanceState.CONDITIONS_MET,
        suggested_quantity=100,
    )
    assert plan.suggested_quantity == 100
    assert plan.manual_execution_required is True
