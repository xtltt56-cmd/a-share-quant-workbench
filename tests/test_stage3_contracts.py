from datetime import date, datetime, timezone

import pandas as pd
import pytest

from a_share_quant.contracts.stage3 import ExecutionSpec, PortfolioTarget, SignalFrame
from a_share_quant.signals.adapter import prediction_frame_to_signal_frame


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [date(2026, 8, 7), date(2026, 8, 7)],
            "symbol": ["000001", "000002"],
            "strategy_id": ["rule", "rule"],
            "strategy_version": ["v1", "v1"],
            "model_version": ["m1", "m1"],
            "feature_version": ["f1", "f1"],
            "experiment_id": ["exp-1", "exp-1"],
            "raw_score": [0.8, 0.2],
            "normalized_score": [100.0, 0.0],
            "rank": [1, 2],
            "confidence": [1.0, 0.5],
            "signal_available_at": [
                datetime(2026, 8, 7, 15, 5, tzinfo=timezone.utc),
                datetime(2026, 8, 7, 15, 5, tzinfo=timezone.utc),
            ],
            "intended_execution_date": [date(2026, 8, 10), date(2026, 8, 10)],
        }
    )


def test_signal_frame_validates_and_round_trips_required_fields() -> None:
    signal_frame = SignalFrame.from_frame(_frame())

    restored = signal_frame.to_frame()

    assert list(restored.columns) == list(_frame().columns)
    assert restored.iloc[0]["symbol"] == "000001"
    assert restored.iloc[0]["intended_execution_date"] == date(2026, 8, 10)


def test_signal_frame_rejects_duplicate_keys_and_same_day_execution() -> None:
    duplicate = pd.concat([_frame(), _frame().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        SignalFrame.from_frame(duplicate)

    same_day = _frame()
    same_day["intended_execution_date"] = same_day["date"]
    with pytest.raises(ValueError, match="execution date"):
        SignalFrame.from_frame(same_day)


def test_portfolio_target_and_execution_spec_validate_as_immutable_contracts() -> None:
    target = PortfolioTarget(
        date=date(2026, 8, 10),
        symbol="000001",
        target_weight=0.15,
        source_strategy="rule-v1",
        rebalance_reason="top_k_entry",
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

    assert target.to_dict()["target_weight"] == 0.15
    assert execution.to_dict()["t_plus_one"] is True
    with pytest.raises(ValueError, match="execution_date"):
        ExecutionSpec(
            signal_date=date(2026, 8, 7),
            execution_date=date(2026, 8, 7),
            execution_price_rule="open",
            slippage=0,
            commission=0,
            tax=0,
            t_plus_one=True,
            limit_rule=True,
            suspension_rule=True,
            minimum_order_size=100,
            cash_constraint=True,
        )


def test_prediction_adapter_builds_explicit_stage3_timestamps() -> None:
    predictions = pd.DataFrame(
        {
            "signal_date": [date(2026, 8, 7), date(2026, 8, 7)],
            "symbol": ["000001", "000002"],
            "raw_score": [0.8, 0.2],
            "strategy_id": ["rule", "rule"],
            "strategy_version": ["v1", "v1"],
            "model_version": ["m1", "m1"],
            "feature_version": ["f1", "f1"],
        }
    )

    frame = prediction_frame_to_signal_frame(
        predictions,
        data_version="data-v1",
        experiment_id="exp-1",
        trading_dates=[date(2026, 8, 7), date(2026, 8, 10)],
    )

    assert isinstance(frame, SignalFrame)
    assert frame.to_frame().iloc[0]["intended_execution_date"] == date(2026, 8, 10)
