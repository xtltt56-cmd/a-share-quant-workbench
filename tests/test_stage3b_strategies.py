from datetime import date

import pandas as pd
import pytest

from a_share_quant.backtest.turnover import compute_turnover
from a_share_quant.contracts.stage3 import ExecutionSpec, SignalFrame
from a_share_quant.strategies import (
    EveryNDays,
    PortfolioSpec,
    PortfolioStrategy,
    RankWeighted,
    RebalancePolicy,
    TopKDropout,
    TopKEqualWeight,
    TopKScoreWeight,
)

SIGNAL_DATE = date(2026, 8, 7)
EXECUTION_DATE = date(2026, 8, 10)


def _execution() -> ExecutionSpec:
    return ExecutionSpec(
        signal_date=SIGNAL_DATE,
        execution_date=EXECUTION_DATE,
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


def _signals(scores: list[float], *, data_mode: str = "fixture") -> SignalFrame:
    symbols = [f"00000{index}" for index in range(1, len(scores) + 1)]
    frame = pd.DataFrame(
        {
            "date": [SIGNAL_DATE] * len(scores),
            "symbol": symbols,
            "strategy_id": ["lgbm"] * len(scores),
            "strategy_version": ["stage3b-v1"] * len(scores),
            "model_version": ["lgbm-v1"] * len(scores),
            "feature_version": ["alpha158-v1"] * len(scores),
            "experiment_id": ["fixture-exp"] * len(scores),
            "raw_score": scores,
            "normalized_score": [50.0] * len(scores),
            "rank": list(range(1, len(scores) + 1)),
            "confidence": [1.0] * len(scores),
            "signal_available_at": [
                pd.Timestamp("2026-08-07 15:05", tz="Asia/Shanghai")
            ]
            * len(scores),
            "intended_execution_date": [EXECUTION_DATE] * len(scores),
            "data_mode": [data_mode] * len(scores),
        }
    )
    return SignalFrame.from_frame(frame)


def test_topk_equal_weight() -> None:
    targets = TopKEqualWeight().generate_targets(
        _signals([4.0, 3.0, 2.0]),
        PortfolioSpec(top_k=2, target_gross_exposure=0.6, max_single_position=1.0),
        _execution(),
    )

    assert [target.symbol for target in targets] == ["000001", "000002"]
    assert [target.target_weight for target in targets] == pytest.approx([0.3, 0.3])


def test_portfolio_strategy_protocol() -> None:
    assert isinstance(TopKEqualWeight(), PortfolioStrategy)


def test_topk_ties() -> None:
    targets = TopKEqualWeight().generate_targets(
        _signals([1.0, 1.0, 0.0]),
        PortfolioSpec(top_k=2, target_gross_exposure=0.4, max_single_position=1.0),
        _execution(),
    )

    assert [target.symbol for target in targets] == ["000001", "000002"]


def test_topk_missing_scores() -> None:
    signals = _signals([1.0, 1.0, 0.0]).to_frame().assign(raw_score=[1.0, 1.0, float("nan")])
    targets = TopKEqualWeight().generate_targets(
        signals,
        PortfolioSpec(top_k=3, target_gross_exposure=0.6, max_single_position=1.0),
        _execution(),
    )

    assert [target.symbol for target in targets] == ["000001", "000002"]


def test_topk_ties_are_deterministic_and_missing_scores_are_skipped() -> None:
    signals = _signals([1.0, 1.0, 0.0])
    # A raw DataFrame is accepted defensively by the strategy boundary so a
    # malformed upstream frame cannot turn into a position.
    signals = signals.to_frame().assign(raw_score=[1.0, 1.0, float("nan")])
    targets = TopKEqualWeight().generate_targets(
        signals,
        PortfolioSpec(top_k=2, target_gross_exposure=0.4, max_single_position=1.0),
        _execution(),
    )

    assert [target.symbol for target in targets] == ["000001", "000002"]


def test_topk_position_cap() -> None:
    targets = TopKEqualWeight().generate_targets(
        _signals([4.0, 3.0, 2.0]),
        PortfolioSpec(
            top_k=3,
            target_gross_exposure=0.9,
            max_single_position=0.1,
            max_positions=3,
            cash_buffer=0.1,
        ),
        _execution(),
    )

    assert [target.target_weight for target in targets] == pytest.approx([0.1] * 3)


def test_topk_cash_residual() -> None:
    targets = TopKEqualWeight().generate_targets(
        _signals([4.0, 3.0, 2.0]),
        PortfolioSpec(
            top_k=3,
            target_gross_exposure=0.9,
            max_single_position=0.1,
            max_positions=3,
            cash_buffer=0.1,
        ),
        _execution(),
    )

    assert sum(target.target_weight for target in targets) == pytest.approx(0.3)


def test_score_weight() -> None:
    targets = TopKScoreWeight().generate_targets(
        _signals([-10.0, 0.0, 1_000_000.0]),
        PortfolioSpec(top_k=3, target_gross_exposure=0.6, max_single_position=0.2),
        _execution(),
    )

    weights = {target.symbol: target.target_weight for target in targets}
    assert set(weights) == {"000001", "000002", "000003"}
    assert all(0 <= weight <= 0.2 for weight in weights.values())
    assert weights["000003"] == pytest.approx(0.2)


def test_score_weight_outlier() -> None:
    targets = TopKScoreWeight().generate_targets(
        _signals([-10.0, 0.0, 1_000_000.0]),
        PortfolioSpec(top_k=3, target_gross_exposure=0.6, max_single_position=0.2),
        _execution(),
    )

    assert max(target.target_weight for target in targets) == pytest.approx(0.2)


def test_score_weight_cap() -> None:
    targets = TopKScoreWeight().generate_targets(
        _signals([1.0, 2.0]),
        PortfolioSpec(
            top_k=2,
            target_gross_exposure=1.0,
            max_single_position=0.2,
            cash_buffer=0.0,
        ),
        _execution(),
    )

    assert all(target.target_weight <= 0.2 for target in targets)


def test_score_weight_zero_scores_fall_back_to_equal_weight() -> None:
    targets = TopKScoreWeight().generate_targets(
        _signals([0.0, 0.0]),
        PortfolioSpec(top_k=2, target_gross_exposure=0.4, max_single_position=1.0),
        _execution(),
    )

    assert [target.target_weight for target in targets] == pytest.approx([0.2, 0.2])


def test_rank_weight() -> None:
    linear = RankWeighted(weight_mode="linear").generate_targets(
        _signals([4.0, 3.0, 2.0]),
        PortfolioSpec(top_k=3, target_gross_exposure=0.6, max_single_position=1.0),
        _execution(),
    )
    inverse = RankWeighted(weight_mode="inverse_rank").generate_targets(
        _signals([4.0, 3.0, 2.0]),
        PortfolioSpec(top_k=3, target_gross_exposure=0.6, max_single_position=1.0),
        _execution(),
    )

    assert linear[0].target_weight > linear[1].target_weight > linear[2].target_weight
    assert inverse[0].target_weight > inverse[1].target_weight > inverse[2].target_weight
    assert sum(target.target_weight for target in inverse) == pytest.approx(0.6)


def test_topk_dropout() -> None:
    targets = TopKDropout().generate_targets(
        _signals([5.0, 4.0, 3.0, 2.0]),
        PortfolioSpec(
            top_k=2,
            n_drop=1,
            target_gross_exposure=0.4,
            max_single_position=1.0,
        ),
        _execution(),
        current_positions={"000003": 0.2, "000004": 0.2},
    )

    symbols = {target.symbol for target in targets}
    assert "000001" in symbols
    assert len(symbols) == 2


def test_dropout_turnover() -> None:
    strategy = TopKDropout()
    current = {"000001": 0.2, "000002": 0.2}
    targets = strategy.generate_targets(
        _signals([5.0, 4.0, 3.0]),
        PortfolioSpec(
            top_k=2,
            n_drop=1,
            target_gross_exposure=0.4,
            max_single_position=1.0,
        ),
        _execution(),
        current_positions=current,
    )
    next_weights = {target.symbol: target.target_weight for target in targets}

    assert compute_turnover(current, next_weights).raw == pytest.approx(0.0)


def test_rebalance_policy() -> None:
    daily = RebalancePolicy.daily()
    every_five = EveryNDays(5)
    weekly = RebalancePolicy.weekly()
    previous = date(2026, 8, 3)

    assert daily.should_rebalance(date(2026, 8, 4), previous)
    assert not every_five.should_rebalance(date(2026, 8, 7), previous)
    assert every_five.should_rebalance(date(2026, 8, 8), previous)
    assert weekly.should_rebalance(date(2026, 8, 10), previous)


def test_turnover_definition() -> None:
    breakdown = compute_turnover({"000001": 0.3}, {"000001": 0.2, "000002": 0.1})

    assert breakdown.raw == pytest.approx(0.2)
    assert breakdown.normalized == pytest.approx(2 / 3)
