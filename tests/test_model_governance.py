from datetime import datetime, timezone

import pytest

from a_share_quant.research.governance import (
    EconomicHypothesis,
    ModelCard,
    ModelGovernance,
    ValidationBundle,
)
from a_share_quant.research.validation import assess_uncertainty


def _card() -> ModelCard:
    return ModelCard(
        candidate_id="lightgbm-v2",
        model_family="LightGBM",
        intended_use="A股日频多因子方向预测",
        horizons=(5, 10, 20),
        hypotheses=(
            EconomicHypothesis(
                factor_name="momentum",
                mechanism="行为偏差与信息扩散",
                expected_direction="positive",
                required_fields=("close", "volume"),
                decay_horizon_days=20,
                failure_modes=("市场急剧反转",),
            ),
        ),
        created_at=datetime(2026, 8, 10, 8, tzinfo=timezone.utc),
    )


def test_challenger_cannot_self_promote_and_requires_explicit_approval() -> None:
    governance = ModelGovernance()
    governance.register_challenger(_card(), ValidationBundle.all_passed("bundle-v2"))

    request = governance.request_promotion("lightgbm-v2")

    assert governance.champion_id is None
    assert request.status == "PENDING_APPROVAL"
    decision = governance.approve_promotion(request.request_id, approved_by="local_user")
    assert decision.accepted is True
    assert governance.champion_id == "lightgbm-v2"


def test_governance_rejects_incomplete_validation_and_wide_uncertainty_abstains() -> None:
    governance = ModelGovernance()
    incomplete = ValidationBundle.all_passed("bundle-v2", drift_pass=False)
    governance.register_challenger(_card(), incomplete)

    with pytest.raises(ValueError, match="validation"):
        governance.request_promotion("lightgbm-v2")

    decision = assess_uncertainty(
        expected_excess_return=0.02,
        confidence_interval_low=-0.03,
        confidence_interval_high=0.07,
        minimum_material_edge=0.01,
    )
    assert decision.abstain is True
