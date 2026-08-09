import pytest

from a_share_quant.promotion import PromotionGate, PromotionState, PromotionStateMachine


def _checks() -> dict[str, bool]:
    return {name: True for name in PromotionGate.required_checks()}


def test_fixture_cannot_promote_to_production() -> None:
    machine = PromotionStateMachine()

    decision = machine.transition("SIGNAL_VALIDATED", _checks(), data_mode="fixture")

    assert decision.accepted
    assert machine.state is PromotionState.SIGNAL_VALIDATED_FIXTURE
    with pytest.raises(ValueError, match="fixture"):
        machine.transition("FAST_BACKTEST_PASS", _checks(), data_mode="fixture")


def test_fixture_promotion_can_never_reach_paper_or_live() -> None:
    machine = PromotionStateMachine(PromotionState.SIGNAL_VALIDATED_FIXTURE)

    with pytest.raises(ValueError, match="fixture"):
        machine.transition("PAPER_TRADING", _checks(), data_mode="fixture")
    with pytest.raises(ValueError, match="LIVE"):
        machine.transition("LIVE", _checks(), data_mode="fixture")
