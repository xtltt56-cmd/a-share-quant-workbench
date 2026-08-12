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
from a_share_quant.advisory.price_overlay import PriceGuidanceOverlay


def plan(**overrides: object) -> PriceGuidancePlan:
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
        "evidence_cutoff": datetime(2026, 8, 12, 8, tzinfo=timezone.utc),
        "model_version": "rule-v1",
        "feature_version": "price-features-v1",
        "config_version": "price-guidance-rule-v1",
        "data_version": "sha256:bars",
        "reason_codes": ("RESEARCH_ONLY",),
    }
    values.update(overrides)
    return PriceGuidancePlan(**values)


def quote(price: str, quality: str = "GOOD") -> dict[str, object]:
    return {
        "symbol": "000001",
        "current_price": Decimal(price),
        "data_quality": quality,
        "quote_timestamp": datetime(2026, 8, 13, 1, tzinfo=timezone.utc),
        "observed_at": datetime(2026, 8, 13, 1, 1, tzinfo=timezone.utc),
    }


@pytest.mark.parametrize(
    ("price", "quality", "state"),
    [
        ("10.50", "GOOD", "WAIT_FOR_PRICE"),
        ("10.80", "GOOD", "RESEARCH_REFERENCE"),
        ("11.10", "GOOD", "PRICE_TOO_HIGH"),
        ("10.20", "GOOD", "INVALIDATED"),
        ("10.80", "STALE", "NO_RELIABLE_GUIDANCE"),
    ],
)
def test_overlay(price: str, quality: str, state: str) -> None:
    result = PriceGuidanceOverlay().evaluate(
        plan(),
        quote(price, quality),
        now=datetime(2026, 8, 13, 1, 2, tzinfo=timezone.utc),
    )
    assert result.state.value == state
    assert result.maximum_acceptable_price == plan().maximum_acceptable_price


def test_overlay_does_not_move_frozen_boundaries() -> None:
    result = PriceGuidanceOverlay().evaluate(
        plan(),
        quote("10.85"),
        now=datetime(2026, 8, 13, 1, 2, tzinfo=timezone.utc),
    )
    assert result.entry_lower == Decimal("10.70")
    assert result.entry_upper == Decimal("10.90")
    assert result.maximum_acceptable_price == Decimal("10.90")


def test_overlay_rejects_future_quote_and_expired_plan() -> None:
    with pytest.raises(ValueError, match="future"):
        PriceGuidanceOverlay().evaluate(
            plan(),
            {**quote("10.80"), "quote_timestamp": datetime(2026, 8, 13, 2, tzinfo=timezone.utc)},
            now=datetime(2026, 8, 13, 1, tzinfo=timezone.utc),
        )
    expired = plan(valid_for=date(2026, 8, 13))
    with pytest.raises(ValueError, match="valid"):
        PriceGuidanceOverlay().evaluate(
            expired,
            quote("10.80"),
            now=datetime(2026, 8, 14, 1, tzinfo=timezone.utc),
        )
