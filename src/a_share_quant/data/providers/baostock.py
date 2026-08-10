"""Optional BaoStock adapter with lazy login and canonical normalization."""

from __future__ import annotations

import importlib
from datetime import date
from typing import Any

import pandas as pd

from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.data.normalization import (
    normalize_daily_bars,
    normalize_instruments,
    normalize_symbol,
)


class BaoStockDataProvider:
    """Read-only daily-data provider for the public BaoStock service.

    BaoStock uses a process-level login session but does not require a user
    credential. The SDK remains optional and is imported only when this
    provider is selected.
    """

    name = "baostock"
    _daily_fields = "date,code,open,high,low,close,volume,amount,pctChg"

    def __init__(self, *, adjustflag: str = "2") -> None:
        if adjustflag not in {"1", "2", "3"}:
            raise ValueError("adjustflag must be 1, 2, or 3")
        self.adjustflag = adjustflag
        self._module: Any | None = None
        self._logged_in = False

    def _client(self) -> Any:
        if self._module is None:
            try:
                self._module = importlib.import_module("baostock")
            except ImportError as exc:
                raise ProviderConfigurationError(
                    "BaoStock is not installed; install the data-free profile"
                ) from exc
        if not self._logged_in:
            try:
                response = self._module.login()
            except Exception as exc:
                raise ProviderRequestError("BaoStock login failed") from exc
            if getattr(response, "error_code", "") != "0":
                raise ProviderRequestError("BaoStock login failed")
            self._logged_in = True
        return self._module

    def close(self) -> None:
        if self._module is None or not self._logged_in:
            return
        try:
            self._module.logout()
        except Exception:
            # Logout is best effort; no provider payload is exposed to callers.
            pass
        finally:
            self._logged_in = False

    def list_instruments(self, as_of: date | str | None = None) -> pd.DataFrame:
        day = _format_date(as_of or date.today())
        try:
            result = self._client().query_stock_basic()
            raw = _result_frame(result)
        except (ProviderConfigurationError, ProviderRequestError):
            raise
        except Exception as exc:
            raise ProviderRequestError("BaoStock request failed: query_stock_basic") from exc

        if raw.empty:
            return normalize_instruments(raw, source=self.name, as_of=day)
        renamed = raw.rename(
            columns={
                "code_name": "name",
                "ipoDate": "listed_date",
                "tradeStatus": "trade_status",
            }
        )
        if "type" in renamed.columns:
            renamed = renamed.loc[renamed["type"].astype(str).eq("1")].copy()
        if "status" in renamed.columns:
            renamed = renamed.loc[renamed["status"].astype(str).eq("1")].copy()
        if "trade_status" in renamed.columns:
            renamed["is_suspended"] = renamed["trade_status"].astype(str).ne("1")
        return normalize_instruments(renamed, source=self.name, as_of=day)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        normalized = normalize_symbol(symbol)
        try:
            result = self._client().query_history_k_data_plus(
                code=_baostock_code(normalized),
                fields=self._daily_fields,
                start_date=_format_date(start_date),
                end_date=_format_date(end_date),
                frequency="d",
                adjustflag=self.adjustflag,
            )
            raw = _result_frame(result)
        except (ProviderConfigurationError, ProviderRequestError):
            raise
        except Exception as exc:
            raise ProviderRequestError(
                "BaoStock request failed: query_history_k_data_plus"
            ) from exc
        return normalize_daily_bars(raw, symbol=normalized, source=self.name)


def _result_frame(result: Any) -> pd.DataFrame:
    if getattr(result, "error_code", "") != "0":
        raise ProviderRequestError("BaoStock request failed")
    fields = list(getattr(result, "fields", ()))
    rows: list[list[Any]] = []
    try:
        while result.next():
            rows.append(result.get_row_data())
    except Exception as exc:
        raise ProviderRequestError("BaoStock response parsing failed") from exc
    return pd.DataFrame(rows, columns=fields)


def _format_date(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value).replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")


def _baostock_code(symbol: str) -> str:
    exchange = (
        "sh"
        if symbol.startswith("6")
        else "bj"
        if symbol.startswith(("4", "8", "9"))
        else "sz"
    )
    return f"{exchange}.{symbol}"
