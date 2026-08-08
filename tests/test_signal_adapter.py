from datetime import date

import pandas as pd

from a_share_quant.signals.adapter import prediction_frame_to_records


def test_prediction_frame_is_normalized_into_unified_signal_records() -> None:
    predictions = pd.DataFrame(
        {
            "signal_date": [date(2026, 8, 7)] * 3,
            "symbol": ["000001", "000002", "000003"],
            "raw_score": [0.1, 0.5, 0.3],
            "strategy_id": ["qlib_lightgbm_alpha158"] * 3,
            "strategy_version": ["stage2-v1"] * 3,
            "model_version": ["lgb-v1"] * 3,
            "feature_version": ["alpha158_v1"] * 3,
        }
    )

    records = prediction_frame_to_records(
        predictions,
        data_version="dataset-v1",
        experiment_id="exp-1",
    )

    assert [record.symbol for record in records] == ["000002", "000003", "000001"]
    assert [record.rank for record in records] == [1, 2, 3]
    assert records[0].normalized_score == 100
    assert records[-1].normalized_score == 0
    assert records[0].date == date(2026, 8, 7)
    assert records[0].to_dict()["date"] == date(2026, 8, 7)

