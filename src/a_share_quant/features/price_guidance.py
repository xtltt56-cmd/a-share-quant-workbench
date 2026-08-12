"""Causal OHLCV features used by the conservative price guidance engine."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from a_share_quant.data.normalization import normalize_symbol

_REQUIRED = {
    "symbol",
    "date",
    "high",
    "low",
    "close",
    "raw_close",
    "amount",
    "source",
}
_FORBIDDEN_SOURCES = {"fixture", "replay", "synthetic", "test", "test-data"}


@dataclass(frozen=True)
class PriceFeatures:
    symbol: str
    cutoff: date
    close: Decimal
    raw_close: Decimal
    high: Decimal
    low: Decimal
    atr14: Decimal
    ma20: Decimal
    ma60: Decimal
    support20: Decimal
    amount20: Decimal
    adjustment_factor: Decimal
    data_version: str
    feature_version: str = "price-features-v1"

    def to_actual(self, adjusted_price: Decimal | float | int | str) -> Decimal:
        return (Decimal(str(adjusted_price)) * self.adjustment_factor).quantize(Decimal("0.01"))


def build_price_features(
    bars: pd.DataFrame,
    *,
    cutoff: date | datetime | str,
    minimum_history_days: int = 252,
) -> PriceFeatures:
    """Build one cutoff-only feature snapshot, rejecting unsafe market data."""

    if not isinstance(bars, pd.DataFrame) or bars.empty:
        raise ValueError("price bars are required")
    missing = _REQUIRED.difference(bars.columns)
    if missing:
        raise ValueError(f"price bars are missing required fields: {', '.join(sorted(missing))}")
    cutoff_date = _as_date(cutoff)
    frame = bars.copy()
    frame["symbol"] = frame["symbol"].map(normalize_symbol)
    symbols = tuple(frame["symbol"].unique())
    if symbols != (symbols[0],) or not symbols:
        raise ValueError("price bars must contain one symbol")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    if frame["date"].isna().any():
        raise ValueError("price bars contain invalid dates")
    frame = frame.loc[frame["date"] <= cutoff_date].copy()
    if frame.empty:
        raise ValueError("price bars have no rows at cutoff")
    if frame["date"].duplicated().any():
        raise ValueError("price bars contain duplicate dates")
    frame = frame.sort_values("date").reset_index(drop=True)
    if len(frame) < minimum_history_days:
        raise ValueError(f"price guidance requires at least {minimum_history_days} rows")
    if frame["date"].iloc[-1] != cutoff_date:
        raise ValueError("price bars must contain the cutoff date")
    if frame["source"].astype(str).str.strip().str.casefold().isin(_FORBIDDEN_SOURCES).any():
        raise ValueError("price bars source is not an accepted market source")
    for field in ("high", "low", "close", "raw_close", "amount"):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    if frame[["high", "low", "close", "raw_close", "amount"]].isna().any().any():
        raise ValueError("price bars contain invalid numeric values")
    if (frame[["high", "low", "close", "raw_close"]] <= 0).any().any() or (
        frame["amount"] < 0
    ).any():
        raise ValueError("price bars contain non-positive values")
    if (frame["high"] < frame["low"]).any() or (frame["close"] > frame["high"]).any() or (
        frame["close"] < frame["low"]
    ).any():
        raise ValueError("price bars contain invalid OHLC range")

    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1]
    if pd.isna(atr14) or atr14 <= 0:
        raise ValueError("ATR14 is unavailable")
    latest = frame.iloc[-1]
    adjustment_factor = latest["raw_close"] / latest["close"]
    if not pd.notna(adjustment_factor) or adjustment_factor <= 0:
        raise ValueError("adjustment factor is invalid")
    canonical_rows = frame.loc[
        :, ["symbol", "date", "high", "low", "close", "raw_close", "amount", "source"]
    ]
    records = canonical_rows.astype({"date": str}).to_dict(orient="records")
    data_version = "sha256:" + hashlib.sha256(
        json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return PriceFeatures(
        symbol=str(latest["symbol"]),
        cutoff=cutoff_date,
        close=_decimal(latest["close"]),
        raw_close=_decimal(latest["raw_close"]),
        high=_decimal(latest["high"]),
        low=_decimal(latest["low"]),
        atr14=_decimal(atr14),
        ma20=_decimal(frame["close"].tail(20).mean()),
        ma60=_decimal(frame["close"].tail(60).mean()),
        support20=_decimal(frame["low"].tail(20).min()),
        amount20=_decimal(frame["amount"].tail(20).mean()),
        adjustment_factor=_decimal(adjustment_factor),
        data_version=data_version,
    )


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = ["PriceFeatures", "build_price_features"]
