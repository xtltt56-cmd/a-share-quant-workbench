from datetime import date, timedelta

import pandas as pd

from a_share_quant.analysis.signal_quality import SignalQualityAnalyzer


def _predictions() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_index in range(12):
        current = date(2025, 1, 1) + timedelta(days=day_index)
        regime = "bull" if day_index < 6 else "bear"
        for rank, symbol in enumerate(("000001", "000002", "000003", "000004"), start=1):
            rows.append(
                {
                    "signal_date": current,
                    "symbol": symbol,
                    "strategy_id": "model-a",
                    "raw_score": float(5 - rank),
                    "forward_return": float(0.04 - rank * 0.01),
                    "forward_excess_return": float(0.03 - rank * 0.01),
                    "regime": regime,
                    "sector": "finance" if rank < 3 else "technology",
                }
            )
    return pd.DataFrame(rows)


def test_signal_quality_reports_ic_rank_ic_quantiles_time_and_regime() -> None:
    result = SignalQualityAnalyzer(top_k=2, quantiles=2, min_group_count=3).analyze(
        _predictions()
    )

    model = result["models"]["model-a"]
    assert model["overall"]["ic_mean"] > 0.99
    assert model["overall"]["rank_ic_mean"] > 0.99
    assert model["overall"]["positive_ic_ratio"] == 1.0
    assert model["overall"]["icir"] > 10
    assert set(model["by_period"]) == {"month", "quarter", "year"}
    assert set(model["by_regime"]) == {"bull", "bear"}
    assert model["quantiles"]["monotonicity_score"] > 0.9
    assert "WEAK_SIGNAL_MONOTONICITY" not in model["diagnostics"]
    assert model["top_k"]["mean_return"] > 0
    assert "rank_stability" in model["stability"]
    assert "cross_period_score_correlation" in model["stability"]
    assert "score_distribution_drift" in model["stability"]


def test_signal_quality_marks_weak_monotonicity_and_missing_regime() -> None:
    frame = _predictions()
    frame["forward_return"] = [0.0, 0.01, 0.03, 0.04] * 12
    frame = frame.drop(columns=["regime"])

    result = SignalQualityAnalyzer(top_k=2, quantiles=2, min_group_count=3).analyze(frame)

    model = result["models"]["model-a"]
    assert "WEAK_SIGNAL_MONOTONICITY" in model["diagnostics"]
    assert model["regime_metadata"]["available"] is False
