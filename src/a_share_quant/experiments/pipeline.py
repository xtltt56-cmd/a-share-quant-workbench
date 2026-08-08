"""Data loading and deterministic feature preparation for Stage 2 runs."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.features.universe import HistoricalUniverse


def load_local_daily_bars(
    data_root: Path,
    *,
    symbols: list[str] | None = None,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> pd.DataFrame:
    paths = sorted((Path(data_root) / "lake" / "daily_bars").glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"no local daily bars found under {Path(data_root) / 'lake'}")
    frame = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    if symbols:
        allowed = {normalize_symbol(symbol) for symbol in symbols}
        frame = frame.loc[frame["symbol"].isin(allowed)]
    if start_date is not None:
        frame = frame.loc[frame["date"] >= _as_date(start_date)]
    if end_date is not None:
        frame = frame.loc[frame["date"] <= _as_date(end_date)]
    if frame.empty:
        raise ValueError("local daily bars are empty after filters")
    return frame.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)


def load_historical_universe(data_root: Path) -> HistoricalUniverse:
    paths = sorted((Path(data_root) / "lake" / "instruments").glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(
            f"no historical instrument snapshots under {Path(data_root) / 'lake'}"
        )
    snapshots = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    if "average_amount" not in snapshots:
        snapshots["average_amount"] = 20_000_000.0
    if "average_turnover_pct" not in snapshots:
        snapshots["average_turnover_pct"] = 1.0
    return HistoricalUniverse(
        snapshots,
        exclude_new_days=60,
        min_average_amount=10_000_000,
        min_average_turnover_pct=0.5,
    )


def build_rule_features(daily_bars: pd.DataFrame, *, benchmark: str) -> pd.DataFrame:
    """Build causal market features; every rolling window includes data <= signal date."""

    frame = daily_bars.copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    frame = frame.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    benchmark_symbol = normalize_symbol(benchmark)
    benchmark_close = (
        frame.loc[frame["symbol"] == benchmark_symbol]
        .set_index("date")["close"]
        .sort_index()
    )
    if benchmark_close.empty:
        raise ValueError(f"benchmark is missing from daily bars: {benchmark_symbol}")

    rows: list[pd.DataFrame] = []
    for symbol, group in frame.groupby("symbol", sort=True):
        current = group.copy().sort_values("date", kind="stable")
        close = pd.to_numeric(current["close"], errors="coerce")
        volume = pd.to_numeric(current["volume"], errors="coerce")
        amount = pd.to_numeric(current.get("amount", close * volume), errors="coerce")
        returns = close.pct_change()
        current["money_flow"] = returns.shift(1) * amount.shift(1)
        current["momentum"] = close.pct_change(20)
        current["trend"] = close / close.rolling(20, min_periods=20).mean() - 1
        stock_return = close.pct_change(20)
        benchmark_return = benchmark_close.reindex(current["date"]).pct_change(20)
        current["relative_strength"] = stock_return.to_numpy() - benchmark_return.to_numpy()
        current["volume_turnover"] = volume / volume.rolling(20, min_periods=20).mean() - 1
        current["quality"] = returns.rolling(20, min_periods=20).mean() / returns.rolling(
            20, min_periods=20
        ).std(ddof=0)
        current["valuation"] = 1 / close
        current["volatility_risk"] = returns.rolling(20, min_periods=20).std(ddof=0)
        rows.append(current)
    result = pd.concat(rows, ignore_index=True)
    return result.loc[result["symbol"] != benchmark_symbol].reset_index(drop=True)


def make_fixture_data(
    *,
    start_date: date | str = "2020-01-01",
    periods: int = 700,
    symbols: tuple[str, ...] = ("000001", "000002", "000003", "000004"),
    benchmark: str = "000300",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if periods < 80:
        raise ValueError("fixture requires at least 80 trading dates")
    dates = pd.bdate_range(_as_date(start_date), periods=periods)
    all_symbols = (*symbols, normalize_symbol(benchmark))
    rows: list[dict[str, object]] = []
    for symbol_index, symbol in enumerate(all_symbols):
        rng = np.random.default_rng(10_000 + symbol_index)
        base = 10.0 + symbol_index * 2.0
        returns = rng.normal(0.0004 + symbol_index * 0.00005, 0.012, periods)
        closes = base * np.cumprod(1 + returns)
        volumes = rng.integers(900_000, 1_100_000, periods)
        for index, current in enumerate(dates):
            close = float(max(closes[index], 1.0))
            open_price = close * (1 - float(returns[index]) / 2)
            rows.append(
                {
                    "symbol": symbol,
                    "date": current.date(),
                    "open": open_price,
                    "high": max(open_price, close) * 1.01,
                    "low": min(open_price, close) * 0.99,
                    "close": close,
                    "volume": int(volumes[index]),
                    "amount": float(volumes[index] * close),
                    "is_suspended": False,
                    "is_limit_up": False,
                    "is_limit_down": False,
                }
            )
    universe = pd.DataFrame(
        [
            {
                "symbol": symbol,
                "name": symbol,
                "listed_date": date(2010, 1, 1),
                "as_of": dates[0].date(),
                "average_amount": 20_000_000.0,
                "average_turnover_pct": 1.0,
                "is_st": False,
                "is_delisting_risk": False,
                "is_suspended": False,
            }
            for symbol in symbols
        ]
    )
    return pd.DataFrame(rows), universe


def _as_date(value: date | str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid date: {value!r}")
    return parsed.date()
