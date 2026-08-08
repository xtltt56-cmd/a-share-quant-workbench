from datetime import date, timedelta

import pandas as pd
import pytest

from a_share_quant.experiments.evaluator import EvaluationConfig, FairPortfolioEvaluator


def _bars(symbols: list[str], *, blocked: set[str] | None = None) -> pd.DataFrame:
    blocked = blocked or set()
    dates = [date(2025, 1, 1) + timedelta(days=index) for index in range(5)]
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for index, current in enumerate(dates):
            close = 10 + symbol_index + index * 0.5
            rows.append(
                {
                    "symbol": symbol,
                    "date": current,
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close * (1.1 if index == 1 and symbol == "000001" else 1),
                    "volume": 1000,
                    "amount": 10000,
                    "is_suspended": symbol in blocked and index == 1,
                    "is_limit_up": symbol in blocked and index == 1,
                    "is_limit_down": False,
                }
            )
    return pd.DataFrame(rows)


def _predictions(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "signal_date": [date(2025, 1, 1)] * len(symbols),
            "symbol": symbols,
            "raw_score": list(range(len(symbols), 0, -1)),
        }
    )


def test_t_plus_one_and_costs_are_reflected_in_equity_and_trades() -> None:
    bars = _bars(["000001", "000300"])
    result = FairPortfolioEvaluator(
        EvaluationConfig(
            max_positions=1,
            max_single_position=1.0,
            max_gross_exposure=1.0,
            commission_rate=0.001,
            stamp_duty_rate=0.001,
            transfer_fee_rate=0.0001,
            slippage_bps=10,
        )
    ).evaluate(_predictions(["000001"]), bars, benchmark="000300")

    assert result.equity.loc[0, "equity"] == pytest.approx(1.0)
    assert result.trades.loc[0, "execution_date"] == date(2025, 1, 2)
    assert result.trades.loc[0, "side"] == "BUY"
    assert result.trades.loc[0, "fees"] > 0
    assert result.equity.loc[1, "equity"] > result.equity.loc[0, "equity"]


def test_limit_up_and_suspension_make_buy_unavailable() -> None:
    bars = _bars(["000001", "000002", "000300"], blocked={"000001", "000002"})
    result = FairPortfolioEvaluator(
        EvaluationConfig(max_positions=2, max_single_position=0.5, max_gross_exposure=1.0)
    ).evaluate(_predictions(["000001", "000002"]), bars, benchmark="000300")

    assert result.trades.empty
    assert result.equity["equity"].eq(1.0).all()


def test_position_limits_are_applied_before_order_generation() -> None:
    symbols = [f"00000{index}" for index in range(1, 10)]
    symbols.append("000300")
    bars = _bars(symbols)
    result = FairPortfolioEvaluator(
        EvaluationConfig(max_positions=2, max_single_position=0.15, max_gross_exposure=0.3)
    ).evaluate(_predictions(symbols[:-1]), bars, benchmark="000300")

    buys = result.trades.loc[result.trades["side"] == "BUY"]
    assert len(buys) <= 2
    assert buys["target_weight"].sum() <= 0.3 + 1e-12
    assert buys["target_weight"].max() <= 0.15 + 1e-12
