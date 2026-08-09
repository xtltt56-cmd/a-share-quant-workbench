"""Optional VectorBT integration exports."""

from a_share_quant.backtest.fast import FastResearchEngine, ResearchPeriod, TimePeriod

from .adapter import (
    OptionalDependencyError,
    VectorBTAvailability,
    VectorBTFastResearchEngine,
    check_vectorbt_available,
)

__all__ = [
    "OptionalDependencyError",
    "VectorBTAvailability",
    "VectorBTFastResearchEngine",
    "check_vectorbt_available",
    "FastResearchEngine",
    "ResearchPeriod",
    "TimePeriod",
]
