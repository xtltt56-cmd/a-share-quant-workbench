from datetime import date

import pandas as pd

from a_share_quant.data.pipeline import IncrementalUpdater
from a_share_quant.storage.market_store import MarketDataStore


def _bars(symbol: str, rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
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
                "source": "fake",
                "fetched_at": "2026-08-08T10:00:00+08:00",
                "data_version": "canonical-v1",
            }
            for day, close in rows
        ]
    )


class FakeProvider:
    name = "fake"

    def __init__(self, fail_symbol: str | None = None) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self.fail_symbol = fail_symbol

    def list_instruments(self, as_of: date | None = None) -> pd.DataFrame:
        snapshot_date = as_of or date(2026, 8, 8)
        return pd.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "name": ["测试一", "测试二"],
                "exchange": ["SZ", "SZ"],
                "listed_date": [date(2000, 1, 1), date(2001, 1, 1)],
                "is_st": [False, False],
                "is_delisting_risk": [False, False],
                "is_suspended": [False, False],
                "as_of": [snapshot_date, snapshot_date],
                "source": [self.name, self.name],
                "fetched_at": ["2026-08-08T10:00:00+08:00"] * 2,
                "data_version": ["canonical-v1"] * 2,
            }
        )

    def get_daily_bars(self, symbol: str, start_date: date, end_date: date) -> pd.DataFrame:
        self.calls.append((symbol, start_date, end_date))
        if symbol == self.fail_symbol:
            raise RuntimeError("simulated provider failure")
        return _bars(symbol, [("2026-08-08", 10.8)])


def test_incremental_updater_starts_after_latest_saved_date(tmp_path) -> None:
    store = MarketDataStore(root=tmp_path)
    store.write_daily_bars(_bars("000001", [("2026-08-07", 10.5)]))
    provider = FakeProvider()

    summary = IncrementalUpdater(provider=provider, store=store).run(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 8)
    )

    assert provider.calls == [
        ("000001", date(2026, 8, 8), date(2026, 8, 8)),
        ("000002", date(2026, 8, 1), date(2026, 8, 8)),
    ]
    assert summary.symbols_updated == 2
    assert summary.symbols_failed == 0
    assert store.latest_date("000001") == date(2026, 8, 8)


def test_incremental_updater_skips_completed_symbol_and_is_idempotent(tmp_path) -> None:
    store = MarketDataStore(root=tmp_path)
    store.write_daily_bars(_bars("000001", [("2026-08-08", 10.5)]))
    store.write_daily_bars(_bars("000002", [("2026-08-08", 11.5)]))
    provider = FakeProvider()

    summary = IncrementalUpdater(provider=provider, store=store).run(
        start_date=date(2026, 8, 1), end_date=date(2026, 8, 8)
    )

    assert provider.calls == []
    assert summary.symbols_skipped == 2


def test_incremental_updater_keeps_other_symbols_when_one_fails(tmp_path) -> None:
    store = MarketDataStore(root=tmp_path)
    provider = FakeProvider(fail_symbol="000001")

    summary = IncrementalUpdater(provider=provider, store=store).run(
        start_date=date(2026, 8, 8), end_date=date(2026, 8, 8)
    )

    assert summary.symbols_failed == 1
    assert summary.symbols_updated == 1
    assert store.latest_date("000002") == date(2026, 8, 8)
    assert store.latest_date("000001") is None
