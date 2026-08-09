from decimal import Decimal
from pathlib import Path

from a_share_quant.advisory.risk import PortfolioRiskSnapshot, RiskEngine, RiskPolicy


def test_risk_engine_sizes_initial_position_with_cash_and_single_name_caps() -> None:
    policy = RiskPolicy.conservative()
    assessment = RiskEngine(policy).assess_new_position(
        portfolio=PortfolioRiskSnapshot(
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            drawdown=Decimal("0"),
            positions=(),
        ),
        symbol="000001",
        industry="银行",
        price=Decimal("10"),
        invalidation_price=Decimal("9.50"),
        liquidity_amount=Decimal("100000000"),
        regime="NORMAL",
    )

    assert assessment.allowed is True
    assert assessment.target_weight == Decimal("0.0400")
    assert assessment.suggested_quantity == 400


def test_drawdown_protection_blocks_new_buys_before_risk_budget_is_used() -> None:
    assessment = RiskEngine(RiskPolicy.conservative()).assess_new_position(
        portfolio=PortfolioRiskSnapshot(
            equity=Decimal("100000"),
            cash=Decimal("80000"),
            drawdown=Decimal("0.09"),
            positions=(),
        ),
        symbol="000001",
        industry="银行",
        price=Decimal("10"),
        invalidation_price=Decimal("9.50"),
        liquidity_amount=Decimal("100000000"),
        regime="NORMAL",
    )

    assert assessment.allowed is False
    assert "DRAWDOWN_NEW_BUY_FREEZE" in assessment.reason_codes


def test_advisory_risk_policy_loads_the_conservative_v1_caps() -> None:
    policy = RiskPolicy.from_yaml(Path("config/risk.yaml"))

    assert policy.normal_gross_cap == Decimal("0.70")
    assert policy.absolute_single_name_cap == Decimal("0.08")
    assert policy.minimum_cash_reserve == Decimal("0.30")
