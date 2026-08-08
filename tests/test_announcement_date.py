from datetime import date

import pandas as pd

from a_share_quant.config import PITConfig
from a_share_quant.features.pit_store import PITFeatureStore


def _row() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600000.SH",
                "trade_date": "2026-03-30",
                "report_period": "2025-12-31",
                "announcement_date": "2026-03-30",
                "ingest_time": "2026-03-30T18:00:00+08:00",
                "roe": 0.18,
            }
        ]
    )


def test_same_day_announcement_policy_is_explicit() -> None:
    same_day = PITFeatureStore(
        _row(),
        config=PITConfig(allow_same_day_announcement=True),
        trading_calendar=[date(2026, 3, 30), date(2026, 3, 31)],
    )
    next_day = PITFeatureStore(
        _row(),
        config=PITConfig(allow_same_day_announcement=False),
        trading_calendar=[date(2026, 3, 30), date(2026, 3, 31)],
    )

    assert not same_day.get_features_asof("600000", date(2026, 3, 30)).empty
    assert next_day.get_features_asof("600000", date(2026, 3, 30)).empty

