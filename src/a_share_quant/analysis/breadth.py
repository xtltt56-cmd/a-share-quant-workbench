"""Market breadth and temperature calculations for real-time monitoring."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any

import pandas as pd

from a_share_quant.contracts.realtime import RealTimeQuote


class MarketTemperature(str, Enum):
    UNKNOWN = "UNKNOWN"
    COLD = "COLD"
    COOL = "COOL"
    NEUTRAL = "NEUTRAL"
    WARM = "WARM"
    HOT = "HOT"


@dataclass(frozen=True)
class MarketBreadth:
    total: int
    up: int
    down: int
    flat: int
    limit_up: int
    limit_down: int
    amount: float
    median_change_pct: float | None
    up_ratio: float | None
    strong_ratio: float | None
    breadth_score: float | None
    temperature: MarketTemperature


def calculate_market_breadth(
    quotes: Iterable[RealTimeQuote | Mapping[str, Any]] | pd.DataFrame,
    *,
    limit_pct: float = 9.8,
    strong_pct: float = 3.0,
) -> MarketBreadth:
    """Count only fresh, valid change observations.

    Missing changes and stale quotes are excluded instead of being treated as
    flat.  ``limit_pct`` is intentionally configurable because ST and special
    board rules are not inferable from a bare quote snapshot.
    """

    if limit_pct <= 0 or strong_pct < 0:
        raise ValueError("limit_pct must be positive and strong_pct cannot be negative")
    records = quotes.to_dict("records") if isinstance(quotes, pd.DataFrame) else quotes
    changes: list[float] = []
    amounts: list[float] = []
    for record in records:
        value = _field(record, "change_pct")
        if value is None:
            last = _field(record, "last")
            previous_close = _field(record, "previous_close")
            if last is not None and previous_close not in (None, 0):
                value = (float(last) / float(previous_close) - 1.0) * 100
        if _field(record, "is_stale", False) or value is None:
            continue
        try:
            change = float(value)
        except (TypeError, ValueError):
            continue
        if not isfinite(change):
            continue
        changes.append(change)
        amount = _field(record, "amount", 0) or 0
        try:
            amounts.append(max(0.0, float(amount)))
        except (TypeError, ValueError):
            amounts.append(0.0)

    total = len(changes)
    up = sum(value > 0 for value in changes)
    down = sum(value < 0 for value in changes)
    flat = total - up - down
    limit_up = sum(value >= limit_pct for value in changes)
    limit_down = sum(value <= -limit_pct for value in changes)
    amount = float(sum(amounts))
    if not total:
        return MarketBreadth(
            0,
            0,
            0,
            0,
            0,
            0,
            amount,
            None,
            None,
            None,
            None,
            MarketTemperature.UNKNOWN,
        )
    up_ratio = up / total
    breadth_score = (up - down) / total * 50 + 50
    strong_ratio = sum(value >= strong_pct for value in changes) / total
    return MarketBreadth(
        total=total,
        up=up,
        down=down,
        flat=flat,
        limit_up=limit_up,
        limit_down=limit_down,
        amount=amount,
        median_change_pct=float(pd.Series(changes).median()),
        up_ratio=up_ratio,
        strong_ratio=strong_ratio,
        breadth_score=breadth_score,
        temperature=_temperature(breadth_score, up_ratio, limit_up / total, limit_down / total),
    )


def _field(record: RealTimeQuote | Mapping[str, Any], name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _temperature(
    score: float,
    up_ratio: float,
    limit_up_ratio: float,
    limit_down_ratio: float,
) -> MarketTemperature:
    if limit_down_ratio >= 0.3 or score < 30:
        return MarketTemperature.COLD
    if score < 45 or up_ratio < 0.4:
        return MarketTemperature.COOL
    if score < 60:
        return MarketTemperature.NEUTRAL
    if score < 75:
        return MarketTemperature.WARM
    return (
        MarketTemperature.HOT
        if limit_up_ratio >= 0.03 or score >= 85
        else MarketTemperature.WARM
    )
