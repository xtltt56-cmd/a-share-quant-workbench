import pandas as pd

from a_share_quant.analysis.breadth import (
    MarketTemperature,
    calculate_market_breadth,
)


def test_market_breadth_counts_direction_and_limit_states() -> None:
    result = calculate_market_breadth(
        pd.DataFrame(
            [
                {"symbol": "000001", "change_pct": 10.0, "amount": 100},
                {"symbol": "000002", "change_pct": 3.0, "amount": 200},
                {"symbol": "000003", "change_pct": 0.0, "amount": 50},
                {"symbol": "000004", "change_pct": -2.0, "amount": 80},
                {"symbol": "000005", "change_pct": -10.0, "amount": 90},
            ]
        )
    )

    assert result.total == 5
    assert result.up == 2
    assert result.flat == 1
    assert result.down == 2
    assert result.limit_up == 1
    assert result.limit_down == 1
    assert result.amount == 520
    assert result.temperature is MarketTemperature.NEUTRAL


def test_market_breadth_excludes_stale_and_missing_changes_without_fabrication() -> None:
    result = calculate_market_breadth(
        [
            {"symbol": "000001", "change_pct": 4.0, "amount": 100, "is_stale": False},
            {"symbol": "000002", "change_pct": None, "amount": 200, "is_stale": False},
            {"symbol": "000003", "change_pct": -4.0, "amount": 300, "is_stale": True},
        ]
    )

    assert result.total == 1
    assert result.up == 1
    assert result.down == 0
    assert result.amount == 100


def test_market_breadth_empty_market_is_unknown() -> None:
    result = calculate_market_breadth([])

    assert result.total == 0
    assert result.temperature is MarketTemperature.UNKNOWN
    assert result.breadth_score is None
