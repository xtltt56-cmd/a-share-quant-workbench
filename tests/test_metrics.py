from datetime import date, timedelta

import pandas as pd
import pytest

from a_share_quant.experiments.metrics import compute_comparison_metrics


def test_metrics_include_prediction_quality_and_risk_statistics() -> None:
    rows = []
    for day_index in range(4):
        current = date(2025, 1, 1) + timedelta(days=day_index)
        for symbol, score, realized in (
            ("000001", 3.0, 0.03),
            ("000002", 2.0, 0.01),
            ("000003", 1.0, -0.01),
        ):
            rows.append(
                {
                    "signal_date": current,
                    "symbol": symbol,
                    "raw_score": score,
                    "forward_return": realized,
                    "forward_excess_return": realized - 0.005,
                }
            )
    predictions = pd.DataFrame(rows)
    equity = pd.DataFrame(
        {
            "date": [date(2025, 1, 1) + timedelta(days=index) for index in range(4)],
            "equity": [1.0, 1.02, 1.01, 1.04],
            "benchmark_equity": [1.0, 1.01, 1.01, 1.02],
            "turnover": [0.0, 0.4, 0.2, 0.3],
        }
    )

    metrics = compute_comparison_metrics(predictions, equity, top_k=1)

    for key in (
        "ic",
        "rank_ic",
        "icir",
        "top_k_return",
        "excess_return",
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "turnover",
        "hit_rate",
    ):
        assert key in metrics
    assert metrics["top_k_return"] == pytest.approx(0.03)
    assert metrics["max_drawdown"] < 0
    assert set(metrics["stability_by_year"]) == {"2025"}

