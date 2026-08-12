from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from a_share_quant.advisory.price_contracts import (
    GuidanceLevel,
    GuidanceState,
    PriceGuidanceObservation,
    PriceGuidancePlan,
    PricePlanType,
)
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore


def plan() -> PriceGuidancePlan:
    return PriceGuidancePlan(
        plan_id="plan-000001-20260812",
        symbol="000001",
        plan_type=PricePlanType.DAILY_CANDIDATE,
        guidance_level=GuidanceLevel.RESEARCH_REFERENCE,
        state=GuidanceState.RESEARCH_REFERENCE,
        calculation_date=date(2026, 8, 12),
        valid_for=date(2026, 8, 13),
        entry_lower=Decimal("10.70"),
        entry_upper=Decimal("10.90"),
        maximum_acceptable_price=Decimal("10.90"),
        invalidation_price=Decimal("10.50"),
        protection_price=None,
        reduce_lower=None,
        reduce_upper=None,
        suggested_quantity=0,
        evidence_cutoff=datetime(2026, 8, 12, 8, tzinfo=timezone.utc),
        model_version="rule-v1",
        feature_version="price-features-v1",
        config_version="price-guidance-rule-v1",
        data_version="sha256:bars",
        reason_codes=("RESEARCH_ONLY",),
    )


def observation() -> PriceGuidanceObservation:
    return PriceGuidanceObservation(
        observation_id="obs-000001-20260812-1",
        plan_id=plan().plan_id,
        symbol="000001",
        observed_at=datetime(2026, 8, 12, 2, tzinfo=timezone.utc),
        quote_timestamp=datetime(2026, 8, 12, 1, 59, tzinfo=timezone.utc),
        current_price=Decimal("10.80"),
        data_quality="GOOD",
        state=GuidanceState.RESEARCH_REFERENCE,
        reason_codes=("IN_ENTRY_RANGE",),
    )


def test_store_round_trip_and_append_only_observations(tmp_path) -> None:
    store = PriceGuidanceStore(tmp_path / "guidance.json")
    store.replace_plans((plan(),))
    store.append_observation(observation())
    reopened = PriceGuidanceStore(store.path)
    assert reopened.plans() == (plan(),)
    assert reopened.observations(plan().plan_id) == (observation(),)


@pytest.mark.parametrize("fault", ["tamper", "truncate", "future", "unknown_field", "duplicate"])
def test_invalid_artifact_is_rejected(tmp_path, fault: str) -> None:
    path = tmp_path / "guidance.json"
    store = PriceGuidanceStore(path)
    store.replace_plans((plan(),))
    store.append_observation(observation())
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = payload["body"]
    if fault == "tamper":
        body["plans"][0]["symbol"] = "000002"
    elif fault == "truncate":
        path.write_text(path.read_text(encoding="utf-8")[:20], encoding="utf-8")
    elif fault == "future":
        body["observations"][0]["observed_at"] = "2099-01-01T00:00:00+00:00"
    elif fault == "unknown_field":
        body["plans"][0]["unexpected"] = True
    elif fault == "duplicate":
        body["observations"].append(body["observations"][0])
    if fault != "truncate":
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact is invalid"):
        PriceGuidanceStore(path)


def test_store_rejects_observation_for_unknown_plan(tmp_path) -> None:
    store = PriceGuidanceStore(tmp_path / "guidance.json")
    with pytest.raises(ValueError, match="unknown plan"):
        store.append_observation(
            observation().__class__(
                observation_id="obs-x",
                plan_id="missing",
                symbol="000001",
                observed_at=observation().observed_at,
                quote_timestamp=observation().quote_timestamp,
                current_price=observation().current_price,
                data_quality="GOOD",
                state=GuidanceState.WAIT_FOR_PRICE,
                reason_codes=(),
            )
        )
