"""Causal benchmark regime labels."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MarketRegimeProvider:
    """Classify market regimes using trailing benchmark observations only."""

    return_window: int = 20
    volatility_window: int = 20
    bull_threshold: float = 0.05
    bear_threshold: float = -0.05

    def __post_init__(self) -> None:
        if self.return_window < 1 or self.volatility_window < 2:
            raise ValueError("regime windows must be positive")
        if self.bear_threshold >= self.bull_threshold:
            raise ValueError("bear threshold must be below bull threshold")

    def classify(self, benchmark: pd.DataFrame) -> pd.DataFrame:
        required = {"date", "close"}
        missing = sorted(required.difference(benchmark.columns))
        if missing:
            raise ValueError(f"benchmark is missing fields: {', '.join(missing)}")
        frame = benchmark[["date", "close"]].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame.dropna().sort_values("date", kind="stable").drop_duplicates("date")
        if frame.empty:
            raise ValueError("benchmark cannot be empty")
        frame["trailing_return"] = frame["close"].pct_change(self.return_window)
        daily_return = frame["close"].pct_change()
        frame["trailing_volatility"] = daily_return.rolling(
            self.volatility_window, min_periods=self.volatility_window
        ).std()
        frame["regime"] = np.select(
            [
                frame["trailing_return"] >= self.bull_threshold,
                frame["trailing_return"] <= self.bear_threshold,
            ],
            ["bull", "bear"],
            default="neutral",
        )
        prior_median = frame["trailing_volatility"].expanding(min_periods=2).median().shift(1)
        frame["volatility_regime"] = np.select(
            [
                frame["trailing_volatility"].notna() & prior_median.notna()
                & (frame["trailing_volatility"] > prior_median),
                frame["trailing_volatility"].notna() & prior_median.notna()
                & (frame["trailing_volatility"] <= prior_median),
            ],
            ["high", "low"],
            default="not_available",
        )
        frame["date"] = frame["date"].dt.date
        return frame[
            ["date", "regime", "volatility_regime", "trailing_return", "trailing_volatility"]
        ].reset_index(drop=True)


def attach_market_regime(signals: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    labels = MarketRegimeProvider().classify(benchmark)
    date_column = "signal_date" if "signal_date" in signals.columns else "date"
    current = signals.copy()
    current[date_column] = pd.to_datetime(current[date_column]).dt.date
    return current.merge(
        labels,
        left_on=date_column,
        right_on="date",
        how="left",
        suffixes=("", "_benchmark"),
    )
