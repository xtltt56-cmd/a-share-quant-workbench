from datetime import date

import pandas as pd

from a_share_quant.config import PITConfig
from a_share_quant.features.pit_store import PITFeatureStore


def test_financial_data_becomes_visible_on_next_trading_day() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001.SZ",
                "trade_date": "2026-03-31",
                "report_period": "2025-12-31",
                "announcement_date": "2026-03-30",
                "ingest_time": "2026-03-30T20:00:00+08:00",
                "roe": 0.12,
            }
        ]
    )
    store = PITFeatureStore(
        rows,
        config=PITConfig(),
        trading_calendar=[date(2026, 3, 30), date(2026, 3, 31)],
    )

    assert store.get_features_asof("000001", date(2026, 3, 29)).empty
    assert store.get_features_asof("000001", date(2026, 3, 30)).empty

    visible = store.get_features_asof("000001", date(2026, 3, 31))
    assert visible.loc[0, "trade_date"] == date(2026, 3, 31)
    assert visible.loc[0, "report_period"] == date(2025, 12, 31)
    assert visible.loc[0, "roe"] == 0.12
    assert visible.loc[0, "effective_date"] == date(2026, 3, 31)

