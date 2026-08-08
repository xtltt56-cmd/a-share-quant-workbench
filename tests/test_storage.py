from datetime import date

import pandas as pd

from a_share_quant.storage.market_store import MarketDataStore


def _bars(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "000001",
                "date": day,
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 100,
                "amount": close * 100,
                "amplitude_pct": 1.0,
                "change_pct": 0.5,
                "change_amount": 0.1,
                "turnover_pct": 1.0,
                "source": "test",
                "fetched_at": "2026-08-08T10:00:00+08:00",
                "data_version": "canonical-v1",
            }
            for day, close in rows
        ]
    )


def test_store_merges_duplicate_dates_atomically_and_records_manifest(tmp_path) -> None:
    store = MarketDataStore(root=tmp_path)

    store.write_daily_bars(_bars([("2026-08-07", 10.5), ("2026-08-08", 10.7)]))
    store.write_daily_bars(_bars([("2026-08-08", 10.9), ("2026-08-09", 11.0)]))

    result = store.read_daily_bars(symbol="000001")

    assert result["date"].tolist() == [date(2026, 8, 7), date(2026, 8, 8), date(2026, 8, 9)]
    assert result.loc[result["date"] == date(2026, 8, 8), "close"].item() == 10.9
    assert store.latest_date("000001") == date(2026, 8, 9)
    manifest = store.manifest_rows(dataset="daily_bars", symbol="000001")
    assert manifest[-1]["row_count"] == 3
    assert manifest[-1]["quality_status"] == "valid"
    assert not list((tmp_path / "lake" / "daily_bars").glob("*.tmp"))


def test_store_queries_date_range_through_duckdb(tmp_path) -> None:
    store = MarketDataStore(root=tmp_path)
    store.write_daily_bars(
        _bars([("2026-08-07", 10.5), ("2026-08-08", 10.7), ("2026-08-09", 11.0)])
    )

    result = store.read_daily_bars(
        symbol="000001", start_date=date(2026, 8, 8), end_date=date(2026, 8, 8)
    )

    assert len(result) == 1
    assert result.loc[0, "close"] == 10.7


def test_store_writes_historical_instrument_snapshot(tmp_path) -> None:
    store = MarketDataStore(root=tmp_path)
    instruments = pd.DataFrame(
        {
            "symbol": ["000001"],
            "name": ["平安银行"],
            "exchange": ["SZ"],
            "listed_date": [date(1991, 4, 3)],
            "is_st": [False],
            "is_delisting_risk": [False],
            "is_suspended": [False],
            "as_of": [date(2026, 8, 8)],
            "source": ["test"],
            "fetched_at": ["2026-08-08T10:00:00+08:00"],
            "data_version": ["canonical-v1"],
        }
    )

    store.write_instruments(instruments)

    result = store.read_instruments(as_of=date(2026, 8, 8))
    assert result.loc[0, "symbol"] == "000001"
    assert store.manifest_rows(dataset="instruments")[-1]["row_count"] == 1
