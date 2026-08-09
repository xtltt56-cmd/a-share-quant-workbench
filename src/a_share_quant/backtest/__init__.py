"""Replaceable fast and event-engine backtest adapters."""

from .contracts import BacktestResult
from .fast import FastResearchEngine, ResearchPeriod, TimePeriod

__all__ = ["BacktestResult", "FastResearchEngine", "ResearchPeriod", "TimePeriod"]
