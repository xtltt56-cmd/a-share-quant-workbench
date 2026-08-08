from datetime import date

import pandas as pd

from a_share_quant.features.pit_store import PITFeatureStore


def test_asof_uses_announcement_visibility_not_report_period() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": "2026-03-31",
                "report_period": "2025-12-31",
                "announcement_date": "2026-03-30",
                "ingest_time": "2026-03-30T20:00:00+08:00",
                "roe": 0.12,
            },
            {
                "symbol": "000001",
                "trade_date": "2026-04-30",
                "report_period": "2026-03-31",
                "announcement_date": "2026-04-30",
                "ingest_time": "2026-04-30T20:00:00+08:00",
                "roe": 0.20,
            },
        ]
    )
    store = PITFeatureStore(rows)

    assert store.get_features_asof("000001", date(2026, 3, 30)).empty
    visible = store.get_features_asof("000001", date(2026, 4, 15))
    assert visible["report_period"].tolist() == [date(2025, 12, 31)]
    assert visible["roe"].tolist() == [0.12]

