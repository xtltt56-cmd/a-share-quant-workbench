"""Provider protocol and sanitized real-time provider errors."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from a_share_quant.contracts.data import (
    ProviderConfigurationError,
    ProviderError,
    ProviderRequestError,
)
from a_share_quant.contracts.realtime import (
    MarketSnapshot,
    MinuteBar,
    ProviderHealth,
    ProviderMetadata,
    RealTimeQuote,
)


class RealTimeProviderError(ProviderError):
    """Base class for safe, operator-facing real-time errors."""


class RealTimeDataProvider(Protocol):
    name: str

    def get_market_snapshot(self) -> MarketSnapshot:
        ...

    def get_quotes(self, symbols: Sequence[str]) -> tuple[RealTimeQuote, ...]:
        ...

    def get_minute_bars(self, symbols: Sequence[str], frequency: str) -> tuple[MinuteBar, ...]:
        ...

    def get_index_snapshot(self, symbols: Sequence[str]) -> tuple[RealTimeQuote, ...]:
        ...

    def get_market_status(self) -> str:
        ...

    def health_check(self) -> ProviderHealth:
        ...

    def metadata(self) -> ProviderMetadata:
        ...


__all__ = [
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderRequestError",
    "RealTimeDataProvider",
    "RealTimeProviderError",
]
