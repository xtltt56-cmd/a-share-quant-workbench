"""Transparent, point-in-time intraday feature calculations."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_REQUIRED_COLUMNS = {
    "symbol",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}


@dataclass(frozen=True)
class IntradayFeatureEngine:
    """Calculate intraday features using only the current and prior observations.

    The engine deliberately does not fill missing provider fields.  Optional
    benchmark, industry, previous-close, and average-volume inputs stay NaN
    when unavailable so a caller can make an explicit data-quality decision.
    """

    feature_version: str = "intraday_v1"

    def compute(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = sorted(_REQUIRED_COLUMNS - set(frame.columns))
        if missing:
            raise ValueError(f"canonical columns are missing: {', '.join(missing)}")
        if frame.empty:
            return frame.copy()

        result = frame.copy()
        result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce")
        if result["timestamp"].isna().any():
            raise ValueError("timestamp must contain valid values")
        result["symbol"] = result["symbol"].astype(str)
        result = result.sort_values(["symbol", "timestamp"], kind="stable").reset_index(drop=True)
        numeric_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "previous_close",
            "avg_volume",
            "benchmark_return",
            "industry_return",
        ]
        for column in numeric_columns:
            if column in result:
                result[column] = pd.to_numeric(result[column], errors="coerce")

        grouped = result.groupby("symbol", sort=False, group_keys=False)
        session_open = grouped["open"].transform("first")
        session_high = grouped["high"].cummax()
        session_low = grouped["low"].cummin()
        first_close = grouped["close"].transform("first")
        cumulative_volume = grouped["volume"].cumsum()
        cumulative_amount = grouped["amount"].cumsum()

        result["price_vs_previous_close"] = self._relative_return(
            result["close"], result.get("previous_close")
        )
        result["price_vs_open"] = self._relative_return(result["close"], session_open)
        result["intraday_return"] = self._relative_return(result["close"], first_close)
        if "avg_volume" in result:
            denominator = result["avg_volume"].where(result["avg_volume"] > 0)
        else:
            denominator = grouped["volume"].transform(
                lambda values: values.expanding(min_periods=1).mean().shift(1)
            )
        result["volume_ratio"] = result["volume"].div(denominator.where(denominator > 0))
        result["vwap"] = cumulative_amount.div(cumulative_volume.where(cumulative_volume > 0))
        result["price_vs_vwap"] = self._relative_return(result["close"], result["vwap"])
        spread = session_high - session_low
        result["intraday_high_low_position"] = (result["close"] - session_low).div(
            spread.where(spread > 0)
        )
        for periods in (5, 15, 30):
            prior = grouped["close"].shift(periods)
            result[f"momentum_{periods}m"] = self._relative_return(result["close"], prior)
        one_bar_returns = grouped["close"].pct_change(fill_method=None)
        result["intraday_volatility"] = one_bar_returns.groupby(
            result["symbol"], sort=False
        ).transform(lambda values: values.rolling(20, min_periods=2).std())
        benchmark_return = result.get("benchmark_return")
        industry_return = result.get("industry_return")
        result["market_relative_strength"] = self._relative_strength(
            result["price_vs_previous_close"], benchmark_return
        )
        result["industry_relative_strength"] = self._relative_strength(
            result["price_vs_previous_close"], industry_return
        )
        return result

    @staticmethod
    def _relative_return(numerator: pd.Series, denominator: pd.Series | None) -> pd.Series:
        if denominator is None:
            return pd.Series(np.nan, index=numerator.index, dtype=float)
        safe_denominator = denominator.where(denominator > 0)
        return numerator.div(safe_denominator).sub(1.0)

    @staticmethod
    def _relative_strength(
        instrument_return: pd.Series, reference_return: pd.Series | None
    ) -> pd.Series:
        if reference_return is None:
            return pd.Series(np.nan, index=instrument_return.index, dtype=float)
        return instrument_return - reference_return
