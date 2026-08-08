from datetime import date

import numpy as np
import pandas as pd

from a_share_quant.experiments.pipeline import build_rule_features, make_fixture_data


def test_fixture_data_has_a_benchmark_and_point_in_time_universe() -> None:
    bars, universe = make_fixture_data(periods=80)

    assert {"000001", "000002", "000300"}.issubset(set(bars["symbol"]))
    assert universe["as_of"].eq(date(2020, 1, 1)).all()
    assert universe["listed_date"].lt(universe["as_of"]).all()


def test_rule_features_use_only_current_or_previous_observations() -> None:
    dates = pd.bdate_range("2025-01-01", periods=45)
    rows = []
    for symbol, base in (("000001", 10.0), ("000002", 12.0), ("000300", 100.0)):
        for index, current in enumerate(dates):
            close = base + index
            rows.append(
                {
                    "symbol": symbol,
                    "date": current.date(),
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 1_000 + index,
                    "amount": close * (1_000 + index),
                }
            )
    bars = pd.DataFrame(rows)
    features = build_rule_features(bars, benchmark="000300")
    target = features.loc[
        (features["symbol"] == "000001") & (features["date"] == dates[30].date())
    ].iloc[0]
    assert np.isfinite(target["momentum"])

    changed = bars.copy()
    changed.loc[
        (changed["symbol"] == "000001") & (changed["date"] > dates[30].date()), "close"
    ] = 10_000
    changed_features = build_rule_features(changed, benchmark="000300")
    changed_target = changed_features.loc[
        (changed_features["symbol"] == "000001") & (changed_features["date"] == dates[30].date())
    ].iloc[0]
    assert changed_target["momentum"] == target["momentum"]
    assert changed_target["trend"] == target["trend"]
