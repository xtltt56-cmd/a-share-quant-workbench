import pytest

from a_share_quant.promotion import PromotionGate, PromotionState, PromotionStateMachine


def _checks() -> dict[str, bool]:
    return {name: True for name in PromotionGate.required_checks()}


def test_promotion_gate_requires_structural_checks_and_sequential_transition() -> None:
    machine = PromotionStateMachine()

    decision = machine.transition(PromotionState.SIGNAL_VALIDATED, _checks())

    assert decision.accepted is True
    assert machine.state is PromotionState.SIGNAL_VALIDATED
    with pytest.raises(ValueError, match="sequential"):
        machine.transition(PromotionState.WALK_FORWARD_PASS, _checks())


def test_promotion_gate_rejects_failed_checks_and_live_state() -> None:
    machine = PromotionStateMachine()
    checks = _checks()
    checks["no_pit_leak"] = False
    decision = machine.transition(PromotionState.SIGNAL_VALIDATED, checks)

    assert decision.accepted is False
    assert machine.state is PromotionState.RESEARCH
    with pytest.raises(ValueError, match="LIVE"):
        PromotionState.parse("LIVE")
