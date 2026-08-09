from datetime import date, timedelta

import pandas as pd

from a_share_quant.experiments.dataset_views import DatasetView


def test_optimization_view_excludes_test_and_signature_is_test_invariant() -> None:
    dates = [date(2025, 1, 1) + timedelta(days=index) for index in range(6)]
    frame = pd.DataFrame(
        {
            "date": dates,
            "raw_score": [1, 2, 3, 4, 5, 6],
            "forward_return": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06],
        }
    )
    view = DatasetView(
        frame,
        train=(dates[0], dates[2]),
        validation=(dates[3], dates[3]),
        test=(dates[4], dates[5]),
    )

    before = view.optimization_signature()
    mutated = frame.copy()
    mutated.loc[mutated["date"] >= dates[4], "forward_return"] = 999.0
    after = DatasetView(
        mutated,
        train=(dates[0], dates[2]),
        validation=(dates[3], dates[3]),
        test=(dates[4], dates[5]),
    ).optimization_signature()

    assert set(view.optimization_frame()["date"]) == set(dates[:4])
    assert set(view.test_frame()["date"]) == set(dates[4:])
    assert before == after
