from datetime import date

import pandas as pd
import pytest

from a_share_quant.backtest.costs import AshareCostModel
from a_share_quant.backtest.fast import FastResearchEngine
from a_share_quant.contracts.stage3 import ExecutionSpec, SignalFrame
from a_share_quant.strategies import PortfolioSpec, TopKEqualWeight


def test_cost_model_rounds_to_lots_and_applies_side_specific_fees() -> None:
    model = AshareCostModel(
        commission_rate=0.0003,
        minimum_commission=5.0,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=5.0,
        lot_size=100,
    )

    assert model.fillable_quantity(199) == 100
    assert model.fillable_quantity(99) == 0
    buy = model.estimate(side="BUY", price=10.0, quantity=100)
    sell = model.estimate(side="SELL", price=10.0, quantity=100)

    assert buy.notional == pytest.approx(1000.0)
    assert buy.commission == pytest.approx(5.0)
    assert buy.stamp_duty == 0.0
    assert sell.stamp_duty == pytest.approx(0.5)
    assert sell.total > buy.total


def test_cost_model_rejects_invalid_orders_and_unknown_sides() -> None:
    model = AshareCostModel()
    with pytest.raises(ValueError, match="quantity"):
        model.estimate(side="BUY", price=10.0, quantity=0)
    with pytest.raises(ValueError, match="side"):
        model.estimate(side="HOLD", price=10.0, quantity=100)
    with pytest.raises(ValueError, match="lot_size"):
        AshareCostModel(lot_size=0)


def test_reference_engine_can_use_initial_capital_for_lot_rounded_costs() -> None:
    signals = SignalFrame.from_predictions(
        pd.DataFrame(
            {
                "signal_date": [date(2026, 8, 7)],
                "symbol": ["000001"],
                "raw_score": [1.0],
                "strategy_id": ["rule"],
                "strategy_version": ["v1"],
                "model_version": ["m1"],
                "feature_version": ["f1"],
            }
        ),
        experiment_id="cost-test",
        trading_dates=[date(2026, 8, 7), date(2026, 8, 10)],
        data_mode="historical",
    )
    execution = ExecutionSpec(
        signal_date=date(2026, 8, 7),
        execution_date=date(2026, 8, 10),
        execution_price_rule="open",
        slippage=0.0005,
        commission=0.0003,
        tax=0.0005,
        t_plus_one=True,
        limit_rule=True,
        suspension_rule=True,
        minimum_order_size=100,
        cash_constraint=True,
    )
    bars = pd.DataFrame(
        {
            "date": [date(2026, 8, 7), date(2026, 8, 10)],
            "symbol": ["000001", "000001"],
            "open": [10.0, 10.0],
            "close": [10.0, 10.1],
        }
    )

    result = FastResearchEngine(prefer_vectorbt=False).run(
        signals=signals,
        strategy=TopKEqualWeight(),
        portfolio_spec=PortfolioSpec(
            top_k=1,
            target_gross_exposure=0.01,
            max_single_position=1.0,
        ),
        execution_spec=execution,
        market_data=bars,
        initial_capital=100_000,
    )

    order = result.orders.iloc[0]
    assert int(order["filled_quantity"]) % 100 == 0
    assert order["estimated_cost"] > 0
