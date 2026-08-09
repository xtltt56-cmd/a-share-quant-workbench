"""Permission-aware Tushare real-time adapter with lazy SDK import."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pydantic import SecretStr

from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.contracts.realtime import ProviderHealth, ProviderMetadata
from a_share_quant.data.realtime.normalization import (
    normalize_minute_bars,
    normalize_realtime_quotes,
)


class TushareRealTimeProvider:
    name = "tushare"

    def __init__(self, token: str | SecretStr | None = None) -> None:
        if isinstance(token, SecretStr):
            token = token.get_secret_value()
        if not token:
            raise ProviderConfigurationError("TUSHARE_TOKEN is required for real-time data")
        self._token = token
        self._client_instance: Any | None = None
        self._permission_health: ProviderHealth | None = None

    def _client(self) -> Any:
        if self._client_instance is None:
            try:
                module = importlib.import_module("tushare")
                self._client_instance = module.pro_api(self._token)
            except ImportError as exc:
                raise ProviderConfigurationError("Tushare is not installed") from exc
            except Exception as exc:
                raise ProviderRequestError("Tushare client initialization failed") from exc
        return self._client_instance

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider=self.name,
            market="A",
            frequencies=("1m", "5m", "15m", "30m", "60m"),
            authenticated=True,
            permissions=(),
        )

    def health_check(self) -> ProviderHealth:
        if self._permission_health is not None:
            return self._permission_health
        client = self._client()
        probe = getattr(client, "rt_min", None) or getattr(client, "rt_min_daily", None)
        if probe is None:
            self._permission_health = ProviderHealth(
                provider=self.name,
                connected=False,
                authenticated=True,
                permissions=(),
                status="MISSING_ENDPOINT",
                message="real-time endpoint unavailable",
            )
            return self._permission_health
        try:
            probe(ts_code="000001.SZ", freq="1MIN")
        except Exception:
            self._permission_health = ProviderHealth(
                provider=self.name,
                connected=False,
                authenticated=True,
                permissions=(),
                status="PERMISSION_DENIED",
                message="real-time permission unavailable",
            )
            return self._permission_health
        self._permission_health = ProviderHealth(
            provider=self.name,
            connected=True,
            authenticated=True,
            permissions=("1m", "5m", "15m", "30m", "60m"),
            status="READY",
        )
        return self._permission_health

    def _ensure_permission(self) -> None:
        health = self.health_check()
        if not health.connected:
            raise ProviderRequestError("Tushare real-time permission unavailable")

    def _fetch(self, symbol: str, frequency: str) -> pd.DataFrame:
        self._ensure_permission()
        client = self._client()
        ts_code = f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}"
        function = getattr(client, "rt_min", None) or getattr(client, "rt_min_daily", None)
        if function is None:
            raise ProviderRequestError("Tushare real-time endpoint unavailable")
        try:
            raw = function(ts_code=ts_code, freq=frequency.upper())
        except Exception as exc:
            raise ProviderRequestError("Tushare real-time request failed") from exc
        if not isinstance(raw, pd.DataFrame):
            raw = pd.DataFrame(raw)
        return raw

    def get_minute_bars(self, symbols, frequency: str) -> tuple:
        received = datetime.now(timezone.utc)
        bars = []
        for symbol in symbols:
            normalized = _normalize_provider_symbol(symbol)
            bars.extend(
                normalize_minute_bars(
                    self._fetch(normalized, frequency),
                    symbol=normalized,
                    frequency=frequency,
                    source=self.name,
                    received_at=received,
                )
            )
        return tuple(bars)

    def get_quotes(self, symbols) -> tuple:
        received = datetime.now(timezone.utc)
        quotes = []
        for symbol in symbols:
            normalized = _normalize_provider_symbol(symbol)
            raw = self._fetch(normalized, "1m")
            quotes.extend(
                normalize_realtime_quotes(
                    raw.assign(ts_code=normalized),
                    source=self.name,
                    received_at=received,
                )
            )
        return tuple(quotes)

    def get_index_snapshot(self, symbols) -> tuple:
        return self.get_quotes(symbols)

    def get_market_snapshot(self):
        raise ProviderRequestError("Tushare full-market snapshot requires an explicit symbol list")

    def get_market_status(self) -> str:
        return "UNKNOWN"


def _normalize_provider_symbol(symbol: str) -> str:
    text = str(symbol).split(".")[0]
    if len(text) != 6 or not text.isdigit():
        raise ValueError(f"invalid Tushare symbol: {symbol}")
    return text
