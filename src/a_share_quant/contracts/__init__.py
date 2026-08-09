"""Stable contracts shared by providers, storage and future research engines."""

from .data import (
    CANONICAL_DAILY_COLUMNS,
    CANONICAL_INSTRUMENT_COLUMNS,
    DataValidationError,
    MarketDataProvider,
    ProviderConfigurationError,
    ProviderError,
    ProviderRequestError,
)

__all__ = [
    "CANONICAL_DAILY_COLUMNS",
    "CANONICAL_INSTRUMENT_COLUMNS",
    "DataValidationError",
    "MarketDataProvider",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderRequestError",
    "ExecutionSpec",
    "PortfolioTarget",
    "SignalFrame",
    "TimeSemantics",
    "next_trading_date",
    "validate_execution_date",
]


def __getattr__(name: str):
    """Load Stage 3 contracts lazily to avoid the data-contract import cycle."""

    if name in {"ExecutionSpec", "PortfolioTarget", "SignalFrame"}:
        from .stage3 import ExecutionSpec, PortfolioTarget, SignalFrame

        return {
            "ExecutionSpec": ExecutionSpec,
            "PortfolioTarget": PortfolioTarget,
            "SignalFrame": SignalFrame,
        }[name]
    if name in {"TimeSemantics", "next_trading_date", "validate_execution_date"}:
        from .timing import TimeSemantics, next_trading_date, validate_execution_date

        return {
            "TimeSemantics": TimeSemantics,
            "next_trading_date": next_trading_date,
            "validate_execution_date": validate_execution_date,
        }[name]
    raise AttributeError(name)
