"""AKShare adapter with lazy imports and sanitized provider errors."""

from __future__ import annotations

import importlib
import time
from datetime import date
from typing import Any

import pandas as pd

from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.data.normalization import (
    normalize_daily_bars,
    normalize_instruments,
    normalize_symbol,
)


class AKShareDataProvider:
    name = "akshare"

    def __init__(
        self,
        *,
        adjust: str = "qfq",
        timeout_seconds: int = 30,
        retry_count: int = 3,
        delay_seconds: float = 0.25,
    ) -> None:
        self.adjust = adjust
        self.timeout_seconds = timeout_seconds
        self.retry_count = max(0, retry_count)
        self.delay_seconds = max(0.0, delay_seconds)
        self._module: Any | None = None
        self._last_call_at: float | None = None

    def _client(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            self._module = importlib.import_module("akshare")
        except ImportError as exc:
            raise ProviderConfigurationError(
                "AKShare is not installed; install the project default data dependency"
            ) from exc
        return self._module

    def _call(self, function_name: str, **kwargs: Any) -> Any:
        client = self._client()
        function = getattr(client, function_name, None)
        if function is None:
            raise ProviderRequestError(f"AKShare endpoint is unavailable: {function_name}")
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            self._wait_for_rate_limit()
            try:
                return function(**kwargs)
            except Exception as exc:
                last_error = exc
                if attempt < self.retry_count:
                    time.sleep(self.delay_seconds * (2**attempt))
        # Do not include provider payloads, URLs, headers or credentials in the message.
        raise ProviderRequestError(f"AKShare request failed: {function_name}") from last_error

    def _wait_for_rate_limit(self) -> None:
        if self._last_call_at is not None:
            elapsed = time.monotonic() - self._last_call_at
            remaining = self.delay_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_at = time.monotonic()

    def list_instruments(self, as_of: date | str | None = None) -> pd.DataFrame:
        raw = self._call("stock_info_a_code_name")
        return normalize_instruments(raw, source=self.name, as_of=as_of)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        start = _format_akshare_date(start_date)
        end = _format_akshare_date(end_date)
        primary_symbol = normalize_symbol(symbol)
        try:
            raw = self._call(
                "stock_zh_a_hist",
                symbol=primary_symbol,
                period="daily",
                start_date=start,
                end_date=end,
                adjust=self.adjust,
                timeout=self.timeout_seconds,
            )
        except ProviderRequestError:
            try:
                raw = self._call(
                    "stock_zh_a_hist_tx",
                    symbol=_format_tencent_symbol(primary_symbol),
                    start_date=start,
                    end_date=end,
                    adjust=self.adjust,
                    timeout=self.timeout_seconds,
                )
                raw = _normalize_tencent_stock_volume(raw, symbol=primary_symbol)
            except ProviderRequestError as fallback_error:
                raise ProviderRequestError(
                    "AKShare daily history unavailable from primary and fallback endpoints"
                ) from fallback_error
        return normalize_daily_bars(raw, symbol=primary_symbol, source=self.name)

    def get_index_daily_bars(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Fetch an index benchmark through AKShare's index endpoint."""

        primary_symbol = normalize_symbol(symbol)
        try:
            raw = self._call(
                "index_zh_a_hist",
                symbol=primary_symbol,
                period="daily",
                start_date=_format_akshare_date(start_date),
                end_date=_format_akshare_date(end_date),
            )
        except ProviderRequestError:
            # Eastmoney's index endpoint expects the CSI namespace for CSI300.
            raw = self._call(
                "stock_zh_index_daily_em",
                symbol=_format_index_symbol(primary_symbol),
                start_date=_format_akshare_date(start_date),
                end_date=_format_akshare_date(end_date),
            )
        return normalize_daily_bars(raw, symbol=primary_symbol, source=self.name)


def _normalize_tencent_stock_volume(raw: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
    """Correct Tencent fallback rows that expose Shenzhen 000 volume in lots.

    AKShare 1.18.83 treats every ``sz000`` identifier as if it were an index and
    skips its normal lot-to-share conversion.  Ordinary 000-series A shares are
    therefore 100 times too small while Shanghai rows are already expressed in
    shares.  The amount/volume/close ratio keeps this adapter compatible if an
    upstream release fixes the conversion: already-normalized rows are left
    unchanged instead of being multiplied twice.
    """

    if not symbol.startswith("000") or raw.empty:
        return raw
    required = {"close", "volume", "amount"}
    if not required.issubset(raw.columns):
        return raw
    close = pd.to_numeric(raw["close"], errors="coerce")
    volume = pd.to_numeric(raw["volume"], errors="coerce")
    amount = pd.to_numeric(raw["amount"], errors="coerce")
    valid = close.gt(0) & volume.gt(0) & amount.gt(0)
    if not valid.any():
        return raw
    notional_per_reported_share = (amount[valid] / (volume[valid] * close[valid])).median()
    if pd.isna(notional_per_reported_share) or notional_per_reported_share < 20:
        return raw
    result = raw.copy()
    result["volume"] = volume * 100
    return result


def _format_akshare_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")


def _format_tencent_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    exchange = "sh" if normalized.startswith("6") else "sz"
    return f"{exchange}{normalized}"


def _format_index_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized == "000300":
        return "csi000300"
    return normalized
