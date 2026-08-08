from datetime import date

import pandas as pd
import pytest

from a_share_quant.contracts.data import DataValidationError
from a_share_quant.features.pit_store import PITFeatureStore


def test_ingest_time_cannot_make_an_unannounced_row_visible() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": "2026-03-31",
                "report_period": "2025-12-31",
                "announcement_date": "2026-03-30",
                "effective_date": "2026-03-31",
                "ingest_time": "2026-03-29T08:00:00+08:00",
                "roe": 0.12,
            }
        ]
    )
    store = PITFeatureStore(rows)

    assert store.get_features_asof("000001", date(2026, 3, 30)).empty
    assert not store.get_features_asof("000001", date(2026, 3, 31)).empty


def test_unknown_announcement_date_is_rejected_by_default() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "trade_date": "2026-03-31",
                "report_period": "2025-12-31",
                "announcement_date": None,
                "ingest_time": "2026-03-30T20:00:00+08:00",
                "roe": 0.12,
            }
        ]
    )

    with pytest.raises(DataValidationError, match="announcement_date"):
        PITFeatureStore(rows)

