"""AKShare public real-time adapter with bounded retry and lazy import."""

from __future__ import annotations

import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.contracts.realtime import (
    DataQualityStatus,
    MarketSnapshot,
    ProviderHealth,
    ProviderMetadata,
)
from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.data.realtime.normalization import (
    normalize_minute_bars,
    normalize_realtime_quotes,
)
from a_share_quant.data.realtime.transport import TransportPolicy


class AKShareRealTimeProvider:
    name = "akshare"

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        retry_count: int = 3,
        delay_seconds: float = 0.5,
        use_system_proxy: bool = True,
        isolated_transport_authorized: bool = False,
    ) -> None:
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.retry_count = max(0, int(retry_count))
        self.delay_seconds = max(0.0, float(delay_seconds))
        self.transport_policy = TransportPolicy(
            use_system_proxy=use_system_proxy,
            isolated_transport_authorized=isolated_transport_authorized,
        )
        self._module: Any | None = None
        self._last_call_at: float | None = None

    def _client(self) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module("akshare")
            except ImportError as exc:
                raise ProviderConfigurationError("AKShare is not installed") from exc
        return self._module

    def _call(self, function_name: str, **kwargs: Any) -> Any:
        self.transport_policy.require_provider_transport()
        function = getattr(self._client(), function_name, None)
        if function is None:
            raise ProviderRequestError(f"AKShare endpoint unavailable: {function_name}")
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            self._wait_for_rate_limit()
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(function, **kwargs)
            try:
                return future.result(timeout=self.timeout_seconds)
            except FutureTimeoutError as exc:
                future.cancel()
                last_error = exc
            except Exception as exc:
                last_error = exc
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            if attempt < self.retry_count:
                time.sleep(self.delay_seconds * (2**attempt))
        raise ProviderRequestError(
            f"AKShare real-time request failed: {function_name}"
        ) from last_error

    def _wait_for_rate_limit(self) -> None:
        if self._last_call_at is not None:
            remaining = self.delay_seconds - (time.monotonic() - self._last_call_at)
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_at = time.monotonic()

    def metadata(self) -> ProviderMetadata:
        client = self._client()
        frequencies = ["snapshot"]
        if any(getattr(client, endpoint, None) is not None for endpoint in _MINUTE_ENDPOINTS):
            frequencies.append("1m")
        return ProviderMetadata(
            provider=self.name,
            market="A",
            frequencies=tuple(frequencies),
            authenticated=False,
            permissions=("snapshot", *frequencies[1:]),
        )

    def health_check(self) -> ProviderHealth:
        client = self._client()
        if getattr(client, "stock_zh_a_spot_em", None) is None:
            return ProviderHealth(
                provider=self.name,
                connected=False,
                authenticated=False,
                permissions=(),
                status="MISSING_ENDPOINT",
                message="public snapshot endpoint unavailable",
            )
        return ProviderHealth(
            provider=self.name,
            connected=True,
            authenticated=False,
            permissions=self.metadata().permissions,
            status="READY",
        )

    def get_market_snapshot(self) -> MarketSnapshot:
        received = datetime.now(timezone.utc)
        raw = self._call("stock_zh_a_spot_em")
        quotes = normalize_realtime_quotes(raw, source=self.name, received_at=received)
        if not quotes:
            raise ProviderRequestError("AKShare snapshot returned no valid quotes")
        quality = (
            DataQualityStatus.GOOD
            if all(quote.quality_flag is DataQualityStatus.GOOD for quote in quotes)
            else DataQualityStatus.DEGRADED
        )
        return MarketSnapshot(
            timestamp_exchange=max(quote.timestamp_exchange for quote in quotes),
            timestamp_received=received,
            quotes=quotes,
            source=self.name,
            quality_flag=quality,
            is_stale=False,
        )

    def get_quotes(self, symbols: list[str] | tuple[str, ...]) -> tuple:
        if getattr(self._client(), "stock_bid_ask_em", None) is not None:
            received = datetime.now(timezone.utc)
            quotes = []
            for symbol in symbols:
                normalized = normalize_symbol(symbol)
                raw = self._call("stock_bid_ask_em", symbol=normalized)
                frame = _single_stock_quote_frame(raw, symbol=normalized)
                quotes.extend(
                    normalize_realtime_quotes(
                        frame,
                        source=self.name,
                        received_at=received,
                    )
                )
            return tuple(quotes)
        requested = {normalize_symbol(symbol) for symbol in symbols}
        return tuple(
            quote for quote in self.get_market_snapshot().quotes if quote.symbol in requested
        )

    def get_index_snapshot(self, symbols: list[str] | tuple[str, ...]) -> tuple:
        if getattr(self._client(), "stock_zh_index_spot_em", None) is None:
            raise ProviderRequestError("AKShare index snapshot endpoint unavailable")
        received = datetime.now(timezone.utc)
        raw = self._call("stock_zh_index_spot_em")
        quotes = normalize_realtime_quotes(
            raw, source=self.name, received_at=received, market="INDEX"
        )
        requested = {normalize_symbol(symbol) for symbol in symbols}
        return tuple(quote for quote in quotes if quote.symbol in requested)

    def get_minute_bars(
        self,
        symbols: list[str] | tuple[str, ...],
        frequency: str,
    ) -> tuple:
        endpoint = next(
            (name for name in _MINUTE_ENDPOINTS if getattr(self._client(), name, None) is not None),
            None,
        )
        if endpoint is None:
            raise ProviderRequestError("AKShare minute endpoint unavailable")
        received = datetime.now(timezone.utc)
        result = []
        for symbol in symbols:
            normalized = normalize_symbol(symbol)
            try:
                raw = self._call(
                    endpoint,
                    symbol=normalized,
                    period=frequency,
                    adjust="",
                )
            except ProviderRequestError:
                raw = self._call(endpoint, symbol=normalized, period=frequency)
            result.extend(
                normalize_minute_bars(
                    raw,
                    symbol=normalized,
                    frequency=frequency,
                    source=self.name,
                    received_at=received,
                )
            )
        return tuple(result)

    def get_market_status(self) -> str:
        return "UNKNOWN"


_MINUTE_ENDPOINTS = ("stock_zh_a_hist_min_em", "stock_zh_a_minute")


def _single_stock_quote_frame(raw: Any, *, symbol: str) -> pd.DataFrame:
    """Convert AKShare official item/value quote output into canonical input columns."""

    if not isinstance(raw, pd.DataFrame) or not {"item", "value"}.issubset(raw.columns):
        raise ProviderRequestError("AKShare single-stock quote schema unavailable")
    values = dict(zip(raw["item"].astype(str), raw["value"], strict=False))
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "last": values.get("最新"),
                "open": values.get("今开"),
                "high": values.get("最高"),
                "low": values.get("最低"),
                "previous_close": values.get("昨收"),
                "volume": values.get("总手"),
                "amount": values.get("金额"),
                "bid1": values.get("buy_1"),
                "ask1": values.get("sell_1"),
                "change": values.get("涨跌"),
                "change_pct": values.get("涨幅"),
                "turnover_rate": values.get("换手"),
            }
        ]
    )
