from datetime import date

import pandas as pd
import pytest

from a_share_quant.research.daily_candidates import (
    DailyDataStaleError,
    generate_official_signals,
    validate_daily_data_freshness,
)


def _bars(*, symbols: int = 31, source: str = "baostock") -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=270)
    rows: list[dict[str, object]] = []
    all_symbols = [f"{index:06d}" for index in range(1, symbols + 1)] + ["000300"]
    for index, symbol in enumerate(all_symbols):
        base = 8.0 + index
        drift = 0.0004 + index * 0.00001 if symbol != "000300" else 0.0002
        for day_index, current in enumerate(dates):
            close = base * (1 + drift) ** day_index
            rows.append(
                {
                    "symbol": symbol,
                    "date": current.date(),
                    "open": close * 0.995,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 2_000_000 + index * 100,
                    "amount": 30_000_000 + index * 100_000,
                    "source": source,
                }
            )
    return pd.DataFrame(rows)


def test_daily_candidates_require_real_history_and_return_ranked_signals() -> None:
    signals = generate_official_signals(_bars(), top_k=5)

    assert len(signals) == 5
    assert all(signal.data_mode == "historical" for signal in signals)
    assert all(signal.source == "baostock" for signal in signals)
    assert all(signal.signal_date == date(2026, 1, 14) for signal in signals)
    assert [signal.rank for signal in signals] == [1, 2, 3, 4, 5]
    assert all(signal.reference_price and signal.invalidation_price for signal in signals)
    assert all("60日均线" in signal.reasons[0] for signal in signals)


def test_daily_candidates_reject_fixture_or_insufficient_cross_section() -> None:
    with pytest.raises(ValueError, match="fixture|non-market"):
        generate_official_signals(_bars(source="fixture"))

    with pytest.raises(ValueError, match="cross-section"):
        generate_official_signals(_bars(symbols=5))


def test_daily_candidates_do_not_use_inverse_price_as_valuation() -> None:
    bars = _bars()
    first = generate_official_signals(bars, top_k=1)[0]
    bars.loc[bars["symbol"] == first.symbol, "close"] *= 100
    changed = generate_official_signals(bars, top_k=1)[0]

    # The initial free-data model has no fundamental valuation factor; a price
    # scale change alone must not turn into a valuation preference.
    assert changed.symbol == first.symbol


def test_daily_data_freshness_rejects_two_business_day_lag_before_close() -> None:
    with pytest.raises(DailyDataStaleError, match="日线数据截止"):
        validate_daily_data_freshness(
            date(2026, 8, 10),
            now=pd.Timestamp("2026-08-13 10:00", tz="Asia/Shanghai").to_pydatetime(),
        )


def test_daily_data_freshness_accepts_previous_complete_day_before_close() -> None:
    validate_daily_data_freshness(
        date(2026, 8, 12),
        now=pd.Timestamp("2026-08-13 10:00", tz="Asia/Shanghai").to_pydatetime(),
    )
