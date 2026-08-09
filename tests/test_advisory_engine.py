from datetime import date, datetime, timezone
from decimal import Decimal

from a_share_quant.advisory.contracts import AdvisoryState, ForecastRecord
from a_share_quant.advisory.engine import AdvisoryContext, AdvisoryEngine
from a_share_quant.advisory.risk import PortfolioRiskSnapshot, RiskPolicy


def _forecast(
    *,
    predicted_return: Decimal = Decimal("0.05"),
    uncertainty: Decimal = Decimal("0.10"),
) -> ForecastRecord:
    generated_at = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    return ForecastRecord(
        forecast_id="forecast-1",
        symbol="000001",
        generated_at=generated_at,
        data_cutoff=generated_at,
        reference_price=Decimal("10"),
        model_version="champion-v1",
        data_version="data-v1",
        feature_version="features-v1",
        horizon_days=5,
        maturity_date=date(2026, 8, 15),
        predicted_return=predicted_return,
        predicted_probability=Decimal("0.65"),
        predicted_rank=1,
        uncertainty=uncertainty,
    )


def _context(**overrides) -> AdvisoryContext:
    values = {
        "forecast": _forecast(),
        "portfolio": PortfolioRiskSnapshot(
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            drawdown=Decimal("0"),
            positions=(),
        ),
        "data_quality": "GOOD",
        "tradeable": True,
        "market_regime": "NORMAL",
        "market_price": Decimal("10"),
        "invalidation_price": Decimal("9.50"),
        "industry": "银行",
        "liquidity_amount": Decimal("100000000"),
        "event_risk": False,
    }
    values.update(overrides)
    return AdvisoryContext(**values)


def test_stale_data_and_risk_off_produce_blocked_not_buy() -> None:
    engine = AdvisoryEngine(RiskPolicy.conservative())

    stale = engine.evaluate(_context(data_quality="STALE"))
    risk_off = engine.evaluate(_context(market_regime="RISK_OFF"))

    assert stale.state is AdvisoryState.BLOCKED
    assert stale.action_zh == "暂不操作"
    assert risk_off.state is AdvisoryState.BLOCKED


def test_valid_forecast_produces_a_capped_explainable_buy_candidate() -> None:
    decision = AdvisoryEngine(RiskPolicy.conservative()).evaluate(_context())

    assert decision.state is AdvisoryState.BUY_CANDIDATE
    assert decision.target_weight == Decimal("0.0400")
    assert decision.suggested_quantity == 400
    assert decision.evidence_cutoff == "2026-08-10T08:00:00+00:00"
    assert "失效" in decision.explanation_zh
    assert decision.cancel_conditions
