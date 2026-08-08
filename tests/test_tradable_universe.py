from datetime import date

import pandas as pd

from a_share_quant.features.universe import HistoricalUniverse


def test_tradable_universe_filters_status_new_stock_and_liquidity() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "as_of": "2026-03-31",
                "listed_date": "2020-01-01",
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.2,
            },
            {
                "symbol": "000002",
                "as_of": "2026-03-31",
                "listed_date": "2026-02-15",
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.2,
            },
            {
                "symbol": "000003",
                "as_of": "2026-03-31",
                "listed_date": "2020-01-01",
                "is_st": True,
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.2,
            },
            {
                "symbol": "000004",
                "as_of": "2026-03-31",
                "listed_date": "2020-01-01",
                "is_suspended": True,
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.2,
            },
            {
                "symbol": "000005",
                "as_of": "2026-03-31",
                "listed_date": "2020-01-01",
                "is_delisting_risk": True,
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.2,
            },
            {
                "symbol": "000006",
                "as_of": "2026-03-31",
                "listed_date": "2020-01-01",
                "average_amount": 5_000_000,
                "average_turnover_pct": 1.2,
            },
        ]
    )
    universe = HistoricalUniverse(
        rows,
        exclude_new_days=60,
        min_average_amount=10_000_000,
        min_average_turnover_pct=0.5,
    )

    result = universe.tradable_universe(date(2026, 3, 31))

    assert result["symbol"].tolist() == ["000001"]
    assert result.loc[0, "universe_date"] == date(2026, 3, 31)


def test_interval_statuses_are_applied_on_the_requested_date() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "600000",
                "as_of": "2026-01-01",
                "listed_date": "2020-01-01",
                "st_effective_date": "2026-03-01",
                "st_end_date": "2026-04-01",
                "suspension_effective_date": "2026-03-15",
                "suspension_end_date": "2026-03-20",
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.2,
            }
        ]
    )
    universe = HistoricalUniverse(rows)

    assert not universe.tradable_universe(date(2026, 2, 28)).empty
    assert universe.tradable_universe(date(2026, 3, 10)).empty
    assert universe.tradable_universe(date(2026, 3, 16)).empty
    assert not universe.tradable_universe(date(2026, 4, 1)).empty


def test_liquidity_can_be_supplied_as_a_separate_historical_table() -> None:
    rows = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "as_of": "2026-03-30",
                "listed_date": "2020-01-01",
            },
            {
                "symbol": "000002",
                "as_of": "2026-03-30",
                "listed_date": "2020-01-01",
            },
        ]
    )
    liquidity = pd.DataFrame(
        [
            {
                "symbol": "000001",
                "as_of": "2026-03-30",
                "average_amount": 20_000_000,
                "average_turnover_pct": 1.0,
            },
            {
                "symbol": "000002",
                "as_of": "2026-03-30",
                "average_amount": 1_000_000,
                "average_turnover_pct": 1.0,
            },
        ]
    )
    universe = HistoricalUniverse(
        rows,
        min_average_amount=10_000_000,
        min_average_turnover_pct=0.5,
    )

    result = universe.tradable_universe(date(2026, 3, 30), liquidity=liquidity)

    assert result["symbol"].tolist() == ["000001"]

