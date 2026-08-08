"""Data-provider and canonical market-data contracts.

The contract deliberately contains no AKShare, Tushare, Qlib or broker imports.
That keeps the strategy and storage layers independent from external providers.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd

CANONICAL_DAILY_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "amplitude_pct",
    "change_pct",
    "change_amount",
    "turnover_pct",
    "source",
    "fetched_at",
    "data_version",
)

CANONICAL_INSTRUMENT_COLUMNS = (
    "symbol",
    "name",
    "exchange",
    "listed_date",
    "is_st",
    "is_delisting_risk",
    "is_suspended",
    "as_of",
    "source",
    "fetched_at",
    "data_version",
)


class DataValidationError(ValueError):
    """Raised when an external or local frame violates a canonical schema."""


class ProviderError(RuntimeError):
    """Base class for provider failures that can be safely reported to operators."""


class ProviderConfigurationError(ProviderError):
    """Raised before network access when a provider is not configured."""


class ProviderRequestError(ProviderError):
    """Raised when an external provider request fails."""


class MarketDataProvider(Protocol):
    """Provider boundary used by ingestion and future feature stores."""

    name: str

    def list_instruments(self, as_of: date | str | None = None) -> pd.DataFrame:
        """Return a canonical instrument snapshot visible at ``as_of``."""

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Return a canonical daily-bar frame for one normalized symbol."""
