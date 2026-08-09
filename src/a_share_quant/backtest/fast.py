"""Public fast-research engine selector and period contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from a_share_quant.contracts.modes import validate_data_mode

from .reference import ReferenceFastResearchEngine


def _as_date(value: date | str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid research date: {value!r}")
    return parsed.date()


@dataclass(frozen=True)
class ResearchPeriod:
    start_date: date | str
    end_date: date | str
    data_mode: str = "historical"

    def __post_init__(self) -> None:
        start = _as_date(self.start_date)
        end = _as_date(self.end_date)
        if end < start:
            raise ValueError("research period end_date must not precede start_date")
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
        object.__setattr__(self, "data_mode", validate_data_mode(self.data_mode))


class FastResearchEngine:
    """Prefer the optional VectorBT adapter, with a transparent fallback."""

    def __init__(
        self,
        *,
        prefer_vectorbt: bool = True,
        allow_reference_fallback: bool = True,
    ) -> None:
        self.prefer_vectorbt = prefer_vectorbt
        self.allow_reference_fallback = allow_reference_fallback

    def run(
        self,
        signals: Any = None,
        strategy: Any = None,
        portfolio_spec: Any = None,
        execution_spec: Any = None,
        market_data: Any = None,
        benchmark: Any = None,
        period: Any = None,
        current_positions: Any = None,
        **kwargs: Any,
    ):
        arguments = {
            "signals": signals,
            "strategy": strategy,
            "portfolio_spec": portfolio_spec,
            "execution_spec": execution_spec,
            "market_data": market_data,
            "benchmark": benchmark,
            "period": period,
            "current_positions": current_positions,
            **kwargs,
        }
        if self.prefer_vectorbt:
            from a_share_quant.integrations.vectorbt import VectorBTFastResearchEngine

            engine = VectorBTFastResearchEngine(
                allow_reference_fallback=self.allow_reference_fallback
            )
            return engine.run(**arguments)
        return ReferenceFastResearchEngine().run(**arguments)


TimePeriod = ResearchPeriod
