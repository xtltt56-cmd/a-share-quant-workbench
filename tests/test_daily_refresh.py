from datetime import date

import pandas as pd

from a_share_quant.runtime.daily_refresh import refresh_daily_data_if_due


class FakeProvider:
    name = "baostock"

    def __init__(self) -> None:
        self.list_calls = 0

    def list_instruments(self, as_of=None):
        self.list_calls += 1
        return pd.DataFrame(
            {
                "symbol": ["000001"],
                "name": ["平安银行"],
                "as_of": [as_of],
                "source": ["baostock"],
            }
        )

    def get_daily_bars(self, symbol, start_date, end_date):
        return pd.DataFrame(
            [
                {
                    "symbol": symbol,
                    "date": end_date,
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 100,
                    "amount": 1000,
                    "source": "baostock",
                }
            ]
        )

    def close(self):
        return None


def test_daily_refresh_if_due_updates_local_lake(tmp_path) -> None:
    provider = FakeProvider()
    seed = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "date": date(2026, 8, 10),
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.2,
                "volume": 100,
                "amount": 1000,
                "source": "baostock",
            }
        ]
    )
    from a_share_quant.storage.market_store import MarketDataStore

    MarketDataStore(tmp_path).write_daily_bars(seed)

    summary = refresh_daily_data_if_due(
        tmp_path,
        end_date=date(2026, 8, 12),
        provider=provider,
    )

    assert summary.rows_written == 1
    assert summary.symbols_failed == 0
    assert provider.list_calls == 1


def test_daily_refresh_if_due_skips_when_all_files_are_current(tmp_path) -> None:
    provider = FakeProvider()
    refresh_daily_data_if_due(tmp_path, end_date=date(2026, 8, 12), provider=provider)
    provider.list_calls = 0

    summary = refresh_daily_data_if_due(
        tmp_path,
        end_date=date(2026, 8, 12),
        provider=provider,
    )

    assert summary.skipped is True
    assert provider.list_calls == 0
