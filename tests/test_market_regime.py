from datetime import date, timedelta

import pandas as pd

from a_share_quant.analysis.regime import MarketRegimeProvider


def test_market_regime_uses_causal_rolling_benchmark_observations() -> None:
    dates = [date(2025, 1, 1) + timedelta(days=index) for index in range(8)]
    benchmark = pd.DataFrame(
        {
            "date": dates,
            "close": [100, 101, 102, 105, 106, 104, 102, 101],
        }
    )

    labels = MarketRegimeProvider(
        return_window=2, bull_threshold=0.02, bear_threshold=-0.02
    ).classify(benchmark)

    assert {"date", "regime", "volatility_regime"}.issubset(labels.columns)
    assert labels.loc[0, "regime"] == "neutral"
    assert labels.loc[3, "regime"] == "bull"
