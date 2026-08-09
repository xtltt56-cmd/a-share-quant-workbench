from datetime import date

import pandas as pd

from a_share_quant.contracts.stage3 import SignalFrame
from a_share_quant.strategies.ensemble import EqualRankEnsemble


def _frame(strategy_id: str, scores: list[float]) -> SignalFrame:
    frame = pd.DataFrame(
        {
            "date": [date(2026, 8, 7)] * 3,
            "symbol": ["000001", "000002", "000003"],
            "strategy_id": [strategy_id] * 3,
            "strategy_version": ["v1"] * 3,
            "model_version": [strategy_id] * 3,
            "feature_version": ["f1"] * 3,
            "experiment_id": ["fixture-exp"] * 3,
            "raw_score": scores,
            "normalized_score": [100.0, 50.0, 0.0],
            "rank": [1, 2, 3],
            "confidence": [1.0, 0.5, 0.1],
            "signal_available_at": [
                pd.Timestamp("2026-08-07 15:05", tz="Asia/Shanghai")
            ]
            * 3,
            "intended_execution_date": [date(2026, 8, 10)] * 3,
            "data_mode": ["fixture"] * 3,
        }
    )
    return SignalFrame.from_frame(frame)


def test_equal_rank_ensemble_fixture() -> None:
    result = EqualRankEnsemble().combine(
        [
            _frame("lgbm", [3.0, 2.0, 1.0]),
            _frame("double_ensemble", [2.0, 3.0, 1.0]),
            _frame("rule", [1.0, 3.0, 2.0]),
        ],
        data_mode="fixture",
    )

    assert result.data_mode == "fixture"
    assert result.to_frame()["strategy_id"].eq("equal_rank_ensemble_fixture").all()
    assert result.to_frame().set_index("symbol").loc["000001", "rank"] == 1
