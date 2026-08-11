"""Replaceable fast and event-engine backtest adapters."""

from .contracts import BacktestResult
from .costs import AshareCostModel, FeeBreakdown
from .fast import FastResearchEngine, ResearchPeriod, TimePeriod

__all__ = [
    "AshareCostModel",
    "BacktestResult",
    "FastResearchEngine",
    "FeeBreakdown",
    "ResearchPeriod",
    "TimePeriod",
]
