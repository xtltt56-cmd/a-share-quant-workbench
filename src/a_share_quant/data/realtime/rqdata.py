"""Optional RQData real-time adapter; credentials never cross the boundary."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.contracts.realtime import ProviderHealth, ProviderMetadata
from a_share_quant.data.realtime.normalization import (
    normalize_minute_bars,
    normalize_realtime_quotes,
)


class RQDataRealTimeProvider:
    name = "rqdata"

    def __init__(
        self,
        *,
        username: str | SecretStr | None = None,
        password: str | SecretStr | None = None,
        config_path: str | Path | None = None,
    ) -> None:
        self._username = _secret_value(username)
        self._password = _secret_value(password)
        self._config_path = Path(config_path) if config_path else None
        if not self._config_path and not (self._username and self._password):
            raise ProviderConfigurationError(
                "RQData credentials or RQDATA_CONFIG_PATH are required"
            )
        self._module: Any | None = None
        self._health: ProviderHealth | None = None

    def _client(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            module = importlib.import_module("rqdatac")
            init = getattr(module, "init", None)
            if init is None:
                raise ProviderConfigurationError("RQData client init is unavailable")
            if self._username and self._password:
                init(self._username, self._password)
            elif self._config_path:
                init(config_file=str(self._config_path))
            self._module = module
            return module
        except ProviderConfigurationError:
            raise
        except ImportError as exc:
            raise ProviderConfigurationError("RQData client is not installed") from exc
        except Exception as exc:
            raise ProviderRequestError("RQData client initialization failed") from exc

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider=self.name,
            market="A",
            frequencies=("snapshot", "1m", "5m", "15m", "30m", "60m"),
            authenticated=True,
            permissions=(),
        )

    def health_check(self) -> ProviderHealth:
        if self._health is not None:
            return self._health
        client = self._client()
        function = getattr(client, "current_snapshot", None)
        if function is None:
            self._health = ProviderHealth(
                provider=self.name,
                connected=False,
                authenticated=True,
                permissions=(),
                status="MISSING_ENDPOINT",
                message="RQData snapshot endpoint unavailable",
            )
            return self._health
        try:
            function(["000001.XSHE"])
        except Exception:
            self._health = ProviderHealth(
                provider=self.name,
                connected=False,
                authenticated=True,
                permissions=(),
                status="PERMISSION_DENIED",
                message="RQData real-time permission unavailable",
            )
            return self._health
        self._health = ProviderHealth(
            provider=self.name,
            connected=True,
            authenticated=True,
            permissions=self.metadata().frequencies,
            status="READY",
        )
        return self._health

    def _snapshot(self, symbols) -> tuple:
        if not self.health_check().connected:
            raise ProviderRequestError("RQData real-time permission unavailable")
        client = self._client()
        raw = client.current_snapshot([_rq_symbol(symbol) for symbol in symbols])
        frame = _to_frame(raw)
        frame["symbol"] = [_strip_exchange(value) for value in frame.index]
        frame = frame.reset_index(drop=True)
        return normalize_realtime_quotes(
            frame,
            source=self.name,
            received_at=datetime.now(timezone.utc),
        )

    def get_quotes(self, symbols) -> tuple:
        return self._snapshot(symbols)

    def get_index_snapshot(self, symbols) -> tuple:
        return self._snapshot(symbols)

    def get_market_snapshot(self):
        raise ProviderRequestError("RQData full-market snapshot requires an explicit universe")

    def get_minute_bars(self, symbols, frequency: str) -> tuple:
        if not self.health_check().connected:
            raise ProviderRequestError("RQData real-time permission unavailable")
        client = self._client()
        function = getattr(client, "get_price", None)
        if function is None:
            raise ProviderRequestError("RQData minute endpoint unavailable")
        bars = []
        received = datetime.now(timezone.utc)
        for symbol in symbols:
            try:
                raw = function(
                    _rq_symbol(symbol),
                    frequency=frequency,
                    fields=["open", "high", "low", "close", "volume", "total_turnover"],
                )
            except Exception as exc:
                raise ProviderRequestError("RQData minute request failed") from exc
            bars.extend(
                normalize_minute_bars(
                    _to_frame(raw),
                    symbol=str(symbol),
                    frequency=frequency,
                    source=self.name,
                    received_at=received,
                )
            )
        return tuple(bars)

    def get_market_status(self) -> str:
        return "UNKNOWN"


def _secret_value(value: str | SecretStr | None) -> str | None:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value or None


def _rq_symbol(symbol: str) -> str:
    normalized = str(symbol).split(".")[0]
    return f"{normalized}.XSHG" if normalized.startswith("6") else f"{normalized}.XSHE"


def _strip_exchange(value: Any) -> str:
    return str(value).split(".")[0]


def _to_frame(raw: Any):
    import pandas as pd

    if isinstance(raw, pd.DataFrame):
        return raw.copy()
    if isinstance(raw, dict):
        return pd.DataFrame(raw)
    return pd.DataFrame(raw)
