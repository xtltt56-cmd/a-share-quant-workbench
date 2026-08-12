from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from a_share_quant.advisory.holding_guidance import HoldingPriceGuidanceEngine
from a_share_quant.advisory.price_contracts import (
    GuidanceLevel,
    GuidanceState,
    PriceGuidancePlan,
    PricePlanType,
)


def plan(**overrides: object) -> PriceGuidancePlan:
    values: dict[str, object] = {
        "plan_id": "holding-000001-20260812",
        "symbol": "000001",
        "plan_type": PricePlanType.HOLDING,
        "guidance_level": GuidanceLevel.RESEARCH_REFERENCE,
        "state": GuidanceState.RESEARCH_REFERENCE,
        "calculation_date": date(2026, 8, 12),
        "valid_for": date(2026, 8, 13),
        "entry_lower": Decimal("10.70"),
        "entry_upper": Decimal("10.90"),
        "maximum_acceptable_price": Decimal("10.90"),
        "invalidation_price": Decimal("9.50"),
        "protection_price": Decimal("9.50"),
        "reduce_lower": Decimal("12.00"),
        "reduce_upper": Decimal("13.00"),
        "suggested_quantity": 0,
        "evidence_cutoff": datetime(2026, 8, 12, 8, tzinfo=timezone.utc),
        "model_version": "rule-v1",
        "feature_version": "price-features-v1",
        "config_version": "price-guidance-rule-v1",
        "data_version": "sha256:bars",
        "reason_codes": ("RESEARCH_ONLY",),
    }
    values.update(overrides)
    return PriceGuidancePlan(**values)


def test_protection_never_moves_down() -> None:
    result = HoldingPriceGuidanceEngine().evaluate(
        {
            "symbol": "000001",
            "total_quantity": 1000,
            "available_quantity": 700,
            "average_cost": "10.00",
        },
        plan(protection_price="9.70"),
        current_price="10.20",
    )
    assert result.protection_price == Decimal("9.70")
    assert result.state == "HOLD_WATCH"


def test_breach_with_zero_available_is_t1_blocked() -> None:
    result = HoldingPriceGuidanceEngine().evaluate(
        {
            "symbol": "000001",
            "total_quantity": 1000,
            "available_quantity": 0,
            "average_cost": "10.00",
        },
        plan(),
        current_price="9.40",
    )
    assert result.state == "T_PLUS_ONE_BLOCKED"
    assert result.suggested_sell_quantity == 0


def test_breach_with_available_quantity_is_risk_alert_but_manual() -> None:
    result = HoldingPriceGuidanceEngine().evaluate(
        {
            "symbol": "000001",
            "total_quantity": 1000,
            "available_quantity": 300,
            "average_cost": "10.00",
        },
        plan(),
        current_price="9.40",
    )
    assert result.state == "RISK_ALERT"
    assert result.suggested_sell_quantity == 300
    assert result.manual_execution_required is True


def test_reduce_watch_requires_risk_context() -> None:
    result = HoldingPriceGuidanceEngine().evaluate(
        {
            "symbol": "000001",
            "total_quantity": 1000,
            "available_quantity": 1000,
            "average_cost": "10.00",
        },
        plan(),
        current_price="12.50",
        edge_weakened=False,
        overheated=False,
        portfolio_risk=False,
    )
    assert result.state == "HOLD_WATCH"
