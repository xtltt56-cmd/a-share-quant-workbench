from datetime import date

import pandas as pd
import pytest

from a_share_quant.backtest.contracts import BacktestResult
from a_share_quant.backtest.fast import FastResearchEngine, ResearchPeriod
from a_share_quant.contracts.stage3 import ExecutionSpec, SignalFrame
from a_share_quant.integrations.vectorbt import (
    OptionalDependencyError,
    VectorBTFastResearchEngine,
    check_vectorbt_available,
)
from a_share_quant.strategies import PortfolioSpec, TopKEqualWeight


def _signals() -> SignalFrame:
    frame = pd.DataFrame(
        {
            "date": [date(2026, 8, 7), date(2026, 8, 10)],
            "symbol": ["000001", "000001"],
            "strategy_id": ["lgbm", "lgbm"],
            "strategy_version": ["stage3b-v1"] * 2,
            "model_version": ["lgbm-v1"] * 2,
            "feature_version": ["alpha158-v1"] * 2,
            "experiment_id": ["fixture-exp"] * 2,
            "raw_score": [1.0, 1.1],
            "normalized_score": [100.0, 100.0],
            "rank": [1, 1],
            "confidence": [1.0, 1.0],
            "signal_available_at": [
                pd.Timestamp("2026-08-07 15:05", tz="Asia/Shanghai"),
                pd.Timestamp("2026-08-10 15:05", tz="Asia/Shanghai"),
            ],
            "intended_execution_date": [date(2026, 8, 10), date(2026, 8, 11)],
            "data_mode": ["fixture", "fixture"],
        }
    )
    return SignalFrame.from_frame(frame)


def _execution() -> ExecutionSpec:
    return ExecutionSpec(
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


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11)],
            "symbol": ["000001"] * 3,
            "open": [10.0, 10.2, 10.4],
            "close": [10.1, 10.3, 10.5],
            "is_suspended": [False] * 3,
            "is_limit_up": [False] * 3,
            "is_limit_down": [False] * 3,
        }
    )


def test_fast_research_engine_contract() -> None:
    result = FastResearchEngine(prefer_vectorbt=False).run(
        signals=_signals(),
        strategy=TopKEqualWeight(),
        portfolio_spec=PortfolioSpec(top_k=1, target_gross_exposure=0.5, max_single_position=1.0),
        execution_spec=_execution(),
        market_data=_bars(),
        period=ResearchPeriod(date(2026, 8, 7), date(2026, 8, 11), data_mode="fixture"),
    )

    assert isinstance(result, BacktestResult)
    assert result.data_mode == "fixture"
    assert {"nav", "returns", "benchmark_returns", "positions", "orders", "trades"}.issubset(
        result.schema()
    )
    assert result.engine == "reference-fast"


def test_vectorbt_optional_dependency_is_explicit_and_non_blocking() -> None:
    availability = check_vectorbt_available()
    engine = VectorBTFastResearchEngine()

    assert availability.available == engine.is_available()
    if not availability.available:
        with pytest.raises(OptionalDependencyError):
            engine.run(
                signals=_signals(),
                strategy=TopKEqualWeight(),
                portfolio_spec=PortfolioSpec(top_k=1),
                execution_spec=_execution(),
                market_data=_bars(),
            )


def test_vectorbt_fallback_records_approximation_warning() -> None:
    result = VectorBTFastResearchEngine(allow_reference_fallback=True).run(
        signals=_signals(),
        strategy=TopKEqualWeight(),
        portfolio_spec=PortfolioSpec(top_k=1),
        execution_spec=_execution(),
        market_data=_bars(),
    )

    assert any("approximation" in warning.lower() for warning in result.warnings)


def test_vectorbt_no_same_bar_execution() -> None:
    result = FastResearchEngine(prefer_vectorbt=False).run(
        signals=_signals(),
        strategy=TopKEqualWeight(),
        portfolio_spec=PortfolioSpec(top_k=1),
        execution_spec=_execution(),
        market_data=_bars(),
    )
    orders = result.orders

    assert not orders.empty
    assert (pd.to_datetime(orders["execution_date"]).dt.date > orders["signal_date"]).all()
    assert not (orders["signal_date"] == orders["execution_date"]).any()


def test_vectorbt_execution_alignment() -> None:
    result = FastResearchEngine(prefer_vectorbt=False).run(
        signals=_signals(),
        strategy=TopKEqualWeight(),
        portfolio_spec=PortfolioSpec(top_k=1),
        execution_spec=_execution(),
        market_data=_bars(),
    )

    assert result.orders.iloc[0]["execution_date"] == date(2026, 8, 10)


def test_backtest_result_schema_and_data_mode_validation() -> None:
    result = BacktestResult(
        nav=pd.DataFrame({"date": [date(2026, 8, 10)], "nav": [1.0]}),
        returns=pd.Series([0.0]),
        benchmark_returns=pd.Series([0.0]),
        positions=pd.DataFrame(),
        orders=pd.DataFrame(),
        trades=pd.DataFrame(),
        turnover=pd.DataFrame(),
        transaction_cost=pd.Series([0.0]),
        metrics={"total_return": 0.0},
        data_mode="fixture",
        engine="test",
        engine_version="1",
    )

    assert result.schema_version == "stage3b-backtest-result-v1"
    assert result.to_dict()["data_mode"] == "fixture"
    with pytest.raises(ValueError, match="data_mode"):
        BacktestResult(
            nav=pd.DataFrame(),
            returns=pd.Series(dtype=float),
            benchmark_returns=None,
            positions=pd.DataFrame(),
            orders=pd.DataFrame(),
            trades=pd.DataFrame(),
            turnover=pd.DataFrame(),
            transaction_cost=pd.Series(dtype=float),
            metrics={},
            data_mode="live",
        )
