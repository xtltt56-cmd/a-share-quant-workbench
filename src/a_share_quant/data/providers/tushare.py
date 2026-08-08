"""Optional Tushare adapter.

The module is imported only after a non-empty token has been supplied.
"""

from __future__ import annotations

import importlib
from datetime import date
from typing import Any

import pandas as pd
from pydantic import SecretStr

from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.data.normalization import (
    normalize_daily_bars,
    normalize_instruments,
    normalize_symbol,
)


class TushareDataProvider:
    name = "tushare"

    def __init__(self, token: str | SecretStr | None = None) -> None:
        if isinstance(token, SecretStr):
            token = token.get_secret_value()
        if not token:
            raise ProviderConfigurationError(
                "TUSHARE_TOKEN is required before using the Tushare provider"
            )
        self._token = token
        self._client_instance: Any | None = None

    def _client(self) -> Any:
        if self._client_instance is not None:
            return self._client_instance
        try:
            module = importlib.import_module("tushare")
        except ImportError as exc:
            raise ProviderConfigurationError(
                "Tushare is not installed; install the data-extra profile"
            ) from exc
        try:
            self._client_instance = module.pro_api(self._token)
        except Exception as exc:
            raise ProviderRequestError("Tushare client initialization failed") from exc
        return self._client_instance

    def list_instruments(self, as_of: date | str | None = None) -> pd.DataFrame:
        try:
            raw = self._client().stock_basic(exchange="", list_status="L")
        except ProviderRequestError:
            raise
        except Exception as exc:
            raise ProviderRequestError("Tushare request failed: stock_basic") from exc
        return normalize_instruments(raw, source=self.name, as_of=as_of)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        normalized = normalize_symbol(symbol)
        ts_code = f"{normalized}.{_exchange_for_tushare(normalized)}"
        try:
            raw = self._client().daily(
                ts_code=ts_code,
                start_date=_format_tushare_date(start_date),
                end_date=_format_tushare_date(end_date),
            )
        except Exception as exc:
            raise ProviderRequestError("Tushare request failed: daily") from exc
        return normalize_daily_bars(raw, symbol=normalized, source=self.name)


def _format_tushare_date(value: date | str) -> str:
    return value.strftime("%Y%m%d") if isinstance(value, date) else str(value).replace("-", "")


def _exchange_for_tushare(symbol: str) -> str:
    if symbol.startswith("6"):
        return "SH"
    if symbol.startswith(("4", "8", "9")):
        return "BJ"
    return "SZ"
