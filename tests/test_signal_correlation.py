from datetime import date, timedelta

import pandas as pd
import pytest

from a_share_quant.analysis.correlation import compare_signal_models


def test_model_correlation_reports_score_rank_and_top20_overlap() -> None:
    rows = []
    for day_index in range(3):
        current = date(2025, 1, 1) + timedelta(days=day_index)
        for rank, symbol in enumerate(("000001", "000002", "000003"), start=1):
            rows.extend(
                [
                    {
                        "signal_date": current,
                        "symbol": symbol,
                        "strategy_id": "a",
                        "raw_score": float(10 - rank),
                        "portfolio_return": float(day_index + 1) / 100,
                    },
                    {
                        "signal_date": current,
                        "symbol": symbol,
                        "strategy_id": "b",
                        "raw_score": float(20 - rank),
                        "portfolio_return": float(day_index + 1) / 100,
                    },
                ]
            )

    pairs = compare_signal_models(pd.DataFrame(rows), top_k=2)

    assert len(pairs) == 1
    assert pairs[0]["score_correlation"] == pytest.approx(1.0)
    assert pairs[0]["rank_correlation"] == pytest.approx(1.0)
    assert pairs[0]["top_k_overlap"] == pytest.approx(1.0)
    assert pairs[0]["portfolio_return_correlation"] == pytest.approx(1.0)
