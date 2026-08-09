"""Unified portfolio turnover definition shared by research engines."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class TurnoverBreakdown:
    """Raw two-sided weight turnover and its prior-gross normalized form."""

    raw: float
    normalized: float

    @property
    def raw_turnover(self) -> float:
        return self.raw

    @property
    def normalized_turnover(self) -> float:
        return self.normalized


def compute_turnover(
    previous_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
) -> TurnoverBreakdown:
    """Compute ``sum(abs(target - previous))`` once for every engine.

    Normalized turnover is the raw two-sided turnover divided by the prior
    gross exposure. For an initially empty portfolio, initial capital (1.0)
    is the denominator, so a 60% initial allocation reports 0.60.
    """

    symbols = set(previous_weights).union(target_weights)
    raw = 0.0
    previous_gross = 0.0
    for symbol in symbols:
        previous = float(previous_weights.get(symbol, 0.0))
        target = float(target_weights.get(symbol, 0.0))
        if not math.isfinite(previous) or not math.isfinite(target):
            raise ValueError("turnover weights must be finite")
        raw += abs(target - previous)
        previous_gross += abs(previous)
    normalized = raw / (previous_gross if previous_gross > 1e-12 else 1.0)
    return TurnoverBreakdown(raw=raw, normalized=normalized)
