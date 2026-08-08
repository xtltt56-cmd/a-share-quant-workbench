from datetime import date

import pandas as pd

from a_share_quant.features.universe import HistoricalUniverse


def test_historical_query_includes_then_delisted_stock_and_excludes_future_listing() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "as_of": "2025-01-01",
                "listed_date": "2020-01-01",
                "delisted_date": "2025-06-30",
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.0,
            },
            {
                "symbol": "000002",
                "as_of": "2025-09-01",
                "listed_date": "2025-09-01",
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.0,
            },
        ]
    )
    universe = HistoricalUniverse(rows, exclude_new_days=60)

    before_delisting = universe.tradable_universe(date(2025, 3, 31))
    after_delisting = universe.tradable_universe(date(2025, 12, 31))

    assert before_delisting["symbol"].tolist() == ["000001"]
    assert after_delisting["symbol"].tolist() == ["000002"]

