import pytest

from a_share_quant.research.drift import DriftMonitor
from a_share_quant.research.governance import ModelGovernance, ValidationBundle
from tests.test_model_governance import _card


def test_drift_monitor_warns_or_blocks_large_distribution_shift() -> None:
    monitor = DriftMonitor(warn_psi=0.10, block_psi=0.25)

    stable = monitor.assess([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    shifted = monitor.assess([1, 2, 3, 4, 5], [50, 51, 52, 53, 54])

    assert stable.status == "PASS"
    assert shifted.status == "BLOCKED"
    assert shifted.psi > stable.psi


def test_drift_monitor_rejects_empty_or_non_finite_inputs() -> None:
    monitor = DriftMonitor()
    with pytest.raises(ValueError, match="empty"):
        monitor.assess([], [1])
    with pytest.raises(ValueError, match="finite"):
        monitor.assess([1, float("nan")], [1, 2])


def test_model_governance_can_record_an_explicit_rollback() -> None:
    governance = ModelGovernance()
    governance.register_challenger(_card(), ValidationBundle.all_passed("bundle-v2"))
    request = governance.request_promotion("lightgbm-v2")
    governance.approve_promotion(request.request_id, approved_by="local_user")

    decision = governance.rollback(to_candidate_id=None, approved_by="local_user")

    assert decision.accepted is True
    assert decision.champion_id is None
    assert governance.champion_id is None
