"""Optional BaoStock adapter with lazy login and canonical normalization."""

from __future__ import annotations

import importlib
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from a_share_quant.contracts.data import (
    RESEARCH_HISTORY_COLUMNS,
    DataValidationError,
    ProviderConfigurationError,
    ProviderRequestError,
)
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
    _daily_fields = (
        "date,code,open,high,low,close,volume,amount,pctChg,tradestatus"
    )
    _research_fields = (
        "date,code,open,high,low,close,volume,amount,tradestatus,isST"
    )
    research_return_version = "baostock-forward-return-v1"

    def __init__(self, *, adjustflag: str = "3") -> None:
        if adjustflag not in {"1", "2", "3"}:
            raise ValueError("adjustflag must be 1, 2, or 3")
        self.adjustflag = adjustflag
        self.daily_data_version = {
            "1": "baostock-back-adjusted-v1",
            "2": "baostock-forward-adjusted-v1",
            "3": "baostock-unadjusted-v1",
        }[adjustflag]
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
            client = self._client()
            result = client.query_stock_basic()
            raw = _result_frame(result)
            query_all_stock = getattr(client, "query_all_stock", None)
            daily_status = (
                _result_frame(query_all_stock(day))
                if query_all_stock is not None
                else pd.DataFrame()
            )
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
        if not daily_status.empty and {"code", "tradeStatus"}.issubset(daily_status.columns):
            status_by_code = daily_status.set_index("code")["tradeStatus"].astype(str)
            renamed["is_suspended"] = renamed["code"].map(status_by_code).fillna("0").ne("1")
        return normalize_instruments(renamed, source=self.name, as_of=day)

    def list_research_instruments(self, as_of: date | str | None = None) -> pd.DataFrame:
        """Return ordinary A-share history, including inactive and delisted rows.

        ``list_instruments`` intentionally describes the currently usable
        execution universe.  Research requires the historical membership
        universe, so this method does not filter ``status=0`` rows.
        """

        try:
            day = _validate_date(as_of or date.today(), field="as_of")
            client = self._client()
            raw = _result_frame(client.query_stock_basic())
            if raw.empty:
                return _empty_research_instruments()

            required = {"code", "type", "status"}
            missing = sorted(required.difference(raw.columns))
            if missing:
                raise ProviderRequestError(
                    "BaoStock response missing research instrument fields"
                )

            daily_status = pd.DataFrame()
            query_all_stock = getattr(client, "query_all_stock", None)
            if query_all_stock is not None:
                daily_status = _result_frame(query_all_stock(day.isoformat()))
        except (ProviderConfigurationError, ProviderRequestError):
            raise
        except Exception as exc:
            raise ProviderRequestError(
                "BaoStock request failed: query_stock_basic"
            ) from exc

        ordinary = raw.loc[raw["type"].map(_status_text).eq("1")].copy()
        if ordinary.empty:
            return _empty_research_instruments()

        status_by_code = _status_by_code(daily_status)
        rows: list[dict[str, Any]] = []
        for _, source_row in ordinary.iterrows():
            try:
                symbol = normalize_symbol(source_row["code"])
                status = _status_text(source_row["status"])
                if status not in {"0", "1"}:
                    raise ValueError("invalid status")
                listed_date = _optional_date(source_row.get("ipoDate"), field="listed_date")
                delisted_date = _optional_date(
                    source_row.get("outDate"), field="delisted_date"
                )
            except Exception as exc:
                raise ProviderRequestError(
                    "BaoStock response contains invalid research instrument fields"
                ) from exc

            name = _first_value(source_row, "code_name", "name")
            try:
                st_column = _find_column(source_row.to_frame().T, ("isST", "is_st"))
                is_st = (
                    _parse_flag(source_row[st_column])
                    if st_column is not None
                    else "ST" in str(name).upper()
                )
            except (TypeError, ValueError) as exc:
                raise ProviderRequestError(
                    "BaoStock response contains invalid research instrument fields"
                ) from exc
            trade_status = status_by_code.get(str(source_row["code"]).strip())
            if trade_status is None:
                trade_status = _status_text(source_row.get("tradeStatus", "1"))
            is_suspended = trade_status != "1"
            rows.append(
                {
                    "symbol": symbol,
                    "name": str(name),
                    "exchange": _exchange_for_symbol(symbol),
                    "listed_date": listed_date,
                    "delisted_date": delisted_date,
                    "status": status,
                    "is_st": bool(is_st),
                    "is_suspended": bool(is_suspended),
                    "as_of": day,
                    "source": self.name,
                    "fetched_at": _utc_now(),
                    "data_version": "baostock-instrument-history-v1",
                }
            )
        return pd.DataFrame(rows)

    def get_research_history(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Return unadjusted execution bars plus a separate research return label.

        BaoStock's forward-adjusted close is used only to derive
        ``research_return``.  It is never included in the returned frame, so
        callers cannot accidentally use adjusted prices for execution.
        """

        try:
            normalized = normalize_symbol(symbol)
        except Exception as exc:
            raise ProviderRequestError("invalid BaoStock symbol") from exc
        try:
            start = _validate_date(start_date, field="start_date")
            end = _validate_date(end_date, field="end_date")
        except Exception as exc:
            raise ProviderRequestError("invalid BaoStock history date") from exc
        if start > end:
            raise ProviderRequestError("invalid BaoStock history date range")

        code = _baostock_code(normalized)
        execution_raw = self._query_history(
            code=code,
            fields=self._research_fields,
            start_date=start,
            end_date=end,
            adjustflag="3",
        )
        adjusted_raw = self._query_history(
            code=code,
            fields=self._research_fields,
            start_date=start,
            end_date=end,
            adjustflag="2",
        )

        try:
            execution_info = _history_status_frame(execution_raw)
            adjusted_info = _history_status_frame(adjusted_raw)
            execution_dates = execution_info["date"]
            execution_duplicate = execution_dates.duplicated(keep=False)
            adjusted_dates = adjusted_info["date"]
            adjusted_duplicate = adjusted_dates.duplicated(keep=False)
            execution_for_normalization = execution_raw.rename(
                columns={"pctChg": "change_pct"}
            )
            execution = normalize_daily_bars(
                execution_for_normalization,
                symbol=normalized,
                source=self.name,
                data_version="baostock-unadjusted-v1",
            )
        except ProviderRequestError:
            raise
        except (DataValidationError, ValueError, TypeError) as exc:
            raise ProviderRequestError(
                "BaoStock response validation failed: research history"
            ) from exc

        if execution.empty:
            return pd.DataFrame(columns=list(RESEARCH_HISTORY_COLUMNS))

        execution_status = execution_info.drop_duplicates(
            subset=["date"], keep="last"
        ).loc[:, ["date", "trade_status", "is_st"]].copy()
        execution = execution.merge(execution_status, on="date", how="left", validate="one_to_one")
        execution["trade_status"] = execution["trade_status"].fillna("").astype(str)
        execution["is_st"] = execution["is_st"].fillna(False).astype(bool)
        execution["tradable"] = (
            execution["trade_status"].eq("1") & ~execution["is_st"]
        ).astype(bool)
        execution["research_return"] = float("nan")
        execution["research_return_version"] = self.research_return_version
        execution["research_usable"] = True
        execution["unusable_reason"] = ""

        reasons: dict[int, list[str]] = {index: [] for index in execution.index}
        if execution_duplicate.any():
            for index in execution.index:
                reasons[index].append("duplicate execution date")
        if adjusted_duplicate.any():
            for index in execution.index:
                reasons[index].append("duplicate research date")

        adjusted_work = adjusted_info.loc[
            :, [
                "date",
                "trade_status",
                "is_st",
                "_trade_status_valid",
                "_is_st_valid",
            ]
        ].copy()
        if "close" in adjusted_raw.columns:
            adjusted_work["adjusted_close"] = pd.to_numeric(
                adjusted_raw["close"], errors="coerce"
            ).to_numpy()
        else:
            adjusted_work["adjusted_close"] = float("nan")
        adjusted_work = adjusted_work.sort_values("date", kind="stable").reset_index(drop=True)
        adjusted_work["previous_close"] = adjusted_work["adjusted_close"].shift(1)
        adjusted_work["research_return"] = (
            adjusted_work["adjusted_close"] / adjusted_work["previous_close"] - 1
        )
        adjusted_by_date = (
            adjusted_work.drop_duplicates(subset=["date"], keep=False)
            .set_index("date")
            if not adjusted_work.empty
            else adjusted_work.set_index("date")
        )
        adjusted_date_set = set(adjusted_work["date"])
        execution_date_set = set(execution["date"])
        adjusted_axis_complete = (
            not adjusted_duplicate.any() and adjusted_date_set == execution_date_set
        )
        if not adjusted_axis_complete:
            for index in execution.index:
                reasons[index].append("research date axis mismatch")

        execution_status_by_date = execution_info.drop_duplicates(
            subset=["date"], keep=False
        ).set_index("date")
        for index, row in execution.iterrows():
            day = row["date"]
            adjusted_row = (
                adjusted_by_date.loc[day] if day in adjusted_by_date.index else None
            )
            if adjusted_row is None:
                reasons[index].append("missing research date")
                continue
            if not _same_state(row, adjusted_row):
                reasons[index].append("status conflict")
                execution.at[index, "tradable"] = False
            if not _valid_state(adjusted_row):
                reasons[index].append("invalid history status")
                execution.at[index, "tradable"] = False

            adjusted_close = adjusted_row["adjusted_close"]
            previous_close = adjusted_row["previous_close"]
            if pd.isna(adjusted_close) or adjusted_close <= 0:
                reasons[index].append("non-numeric adjusted close")
            elif pd.isna(previous_close) or previous_close <= 0:
                reasons[index].append("missing previous adjusted close")
            else:
                execution.at[index, "research_return"] = float(
                    adjusted_row["research_return"]
                )

            if day in execution_status_by_date.index and not _valid_state(
                execution_status_by_date.loc[day]
            ):
                reasons[index].append("invalid history status")
                execution.at[index, "tradable"] = False

        if not adjusted_axis_complete:
            execution["research_return"] = float("nan")

        for index, row_reasons in reasons.items():
            if row_reasons:
                execution.at[index, "research_usable"] = False
                execution.at[index, "unusable_reason"] = "; ".join(
                    dict.fromkeys(row_reasons)
                )

        return execution.loc[:, list(RESEARCH_HISTORY_COLUMNS)]

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        normalized = normalize_symbol(symbol)
        return self._get_daily_bars(
            normalized,
            start_date=start_date,
            end_date=end_date,
            code=_baostock_code(normalized),
        )

    def get_index_daily_bars(
        self,
        symbol: str,
        start_date: date | str,
        end_date: date | str,
    ) -> pd.DataFrame:
        """Fetch an index series with BaoStock's index exchange mapping."""

        normalized = normalize_symbol(symbol)
        return self._get_daily_bars(
            normalized,
            start_date=start_date,
            end_date=end_date,
            code=_baostock_index_code(normalized),
        )

    def _get_daily_bars(
        self,
        normalized: str,
        *,
        start_date: date | str,
        end_date: date | str,
        code: str,
    ) -> pd.DataFrame:
        try:
            raw = self._query_history(
                code=code,
                fields=self._daily_fields,
                start_date=start_date,
                end_date=end_date,
                adjustflag=self.adjustflag,
            )
        except (ProviderConfigurationError, ProviderRequestError):
            raise
        except Exception as exc:
            raise ProviderRequestError(
                "BaoStock request failed: query_history_k_data_plus"
            ) from exc
        trade_status_column = _find_column(
            raw, ("tradestatus", "tradeStatus", "trade_status")
        )
        if trade_status_column is not None:
            raw = raw.loc[
                raw[trade_status_column].astype(str).str.strip().eq("1")
            ].copy()
        raw = raw.rename(columns={"pctChg": "change_pct"})
        return normalize_daily_bars(
            raw,
            symbol=normalized,
            source=self.name,
            data_version=self.daily_data_version,
        )

    def _query_history(
        self,
        *,
        code: str,
        fields: str,
        start_date: date | str,
        end_date: date | str,
        adjustflag: str,
    ) -> pd.DataFrame:
        """Call BaoStock history with a fixed, testable request shape."""

        try:
            result = self._client().query_history_k_data_plus(
                code=code,
                fields=fields,
                start_date=_format_date(start_date),
                end_date=_format_date(end_date),
                frequency="d",
                adjustflag=adjustflag,
            )
            return _result_frame(result)
        except (ProviderConfigurationError, ProviderRequestError):
            raise
        except Exception as exc:
            raise ProviderRequestError(
                "BaoStock request failed: query_history_k_data_plus"
            ) from exc


def _empty_research_instruments() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol",
            "name",
            "exchange",
            "listed_date",
            "delisted_date",
            "status",
            "is_st",
            "is_suspended",
            "as_of",
            "source",
            "fetched_at",
            "data_version",
        ]
    )


def _find_column(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in frame.columns:
            return alias
    return None


def _history_status_frame(raw: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame):
        raise ProviderRequestError("BaoStock response validation failed: history frame")
    date_column = _find_column(raw, ("date", "trade_date"))
    trade_column = _find_column(raw, ("tradestatus", "tradeStatus", "trade_status"))
    st_column = _find_column(raw, ("isST", "is_st"))
    if date_column is None or trade_column is None or st_column is None:
        raise ProviderRequestError("BaoStock response missing status fields")

    dates = pd.to_datetime(raw[date_column], errors="coerce")
    if dates.isna().any():
        raise ProviderRequestError("BaoStock response contains invalid history dates")
    trade_status = raw[trade_column].map(_status_text)
    is_st_text = raw[st_column].map(_status_text)
    return pd.DataFrame(
        {
            "date": dates.dt.date,
            "trade_status": trade_status,
            "is_st": is_st_text.eq("1"),
            "_trade_status_valid": trade_status.isin({"0", "1"}),
            "_is_st_valid": is_st_text.isin({"0", "1"}),
        },
        index=raw.index,
    ).reset_index(drop=True)


def _status_by_code(raw: pd.DataFrame) -> dict[str, str]:
    if raw.empty or "code" not in raw.columns:
        return {}
    column = _find_column(raw, ("tradeStatus", "tradestatus", "trade_status"))
    if column is None:
        return {}
    return {
        str(code).strip(): _status_text(value)
        for code, value in zip(raw["code"], raw[column], strict=False)
    }


def _first_value(row: pd.Series, *columns: str) -> Any:
    for column in columns:
        if column in row.index and not pd.isna(row[column]):
            return row[column]
    return ""


def _status_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(pd.isna(value)):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip().lower()
    if text in {"1", "1.0", "true", "yes", "y"}:
        return "1"
    if text in {"0", "0.0", "false", "no", "n"}:
        return "0"
    return text


def _parse_flag(value: Any) -> bool:
    text = _status_text(value)
    if text == "1":
        return True
    if text == "0":
        return False
    raise ValueError("invalid status flag")


def _optional_date(value: Any, *, field: str) -> date | None:
    if value is None:
        return None
    try:
        if bool(pd.isna(value)) or str(value).strip() == "":
            return None
    except (TypeError, ValueError):
        pass
    return _validate_date(value, field=field)


def _validate_date(value: date | str, *, field: str) -> date:
    if value is None:
        raise ValueError(f"missing {field}")
    parsed = pd.to_datetime(value, errors="coerce")
    if isinstance(parsed, pd.DatetimeIndex) or pd.isna(parsed):
        raise ValueError(f"invalid {field}")
    return parsed.date()


def _same_state(left: pd.Series, right: pd.Series) -> bool:
    return (
        _status_text(left.get("trade_status")) == _status_text(right.get("trade_status"))
        and bool(left.get("is_st", False)) == bool(right.get("is_st", False))
    )


def _valid_state(row: pd.Series) -> bool:
    return bool(row.get("_trade_status_valid", False)) and bool(
        row.get("_is_st_valid", False)
    )


def _exchange_for_symbol(symbol: str) -> str:
    if symbol.startswith("6"):
        return "SH"
    if symbol.startswith(("0", "3")):
        return "SZ"
    if symbol.startswith(("4", "8", "9")):
        return "BJ"
    return "UNKNOWN"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _baostock_index_code(symbol: str) -> str:
    if symbol == "000300":
        return "sh.000300"
    return _baostock_code(symbol)
