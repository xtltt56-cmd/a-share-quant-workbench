"""Source-to-canonical normalization and point-in-time helpers."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from a_share_quant.contracts.data import (
    CANONICAL_DAILY_COLUMNS,
    CANONICAL_INSTRUMENT_COLUMNS,
    DataValidationError,
)

_DAILY_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "日期", "交易日期", "trade_date"),
    "open": ("open", "开盘", "开盘价"),
    "high": ("high", "最高", "最高价"),
    "low": ("low", "最低", "最低价"),
    "close": ("close", "收盘", "收盘价"),
    "volume": ("volume", "成交量", "vol"),
    "amount": ("amount", "成交额", "成交金额"),
    "amplitude_pct": ("amplitude_pct", "振幅"),
    "change_pct": ("change_pct", "涨跌幅", "pct_chg"),
    "change_amount": ("change_amount", "涨跌额"),
    "turnover_pct": ("turnover_pct", "换手率", "turnover_rate"),
    "source": ("source",),
    "fetched_at": ("fetched_at",),
    "data_version": ("data_version",),
}

_INSTRUMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "code", "代码", "证券代码", "ts_code"),
    "name": ("name", "名称", "股票名称"),
    "listed_date": ("listed_date", "上市日期", "list_date"),
    "is_st": ("is_st",),
    "is_delisting_risk": ("is_delisting_risk",),
    "is_suspended": ("is_suspended",),
    "as_of": ("as_of",),
    "source": ("source",),
    "fetched_at": ("fetched_at",),
    "data_version": ("data_version",),
}

_REQUIRED_DAILY_FIELDS = ("date", "open", "high", "low", "close", "volume", "amount")


def _find_column(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in frame.columns:
            return alias
    return None


def _parse_date(value: Any, *, field: str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise DataValidationError(f"invalid {field}: {value!r}")
    return parsed.date()


def _as_date_series(values: pd.Series, *, field: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    if parsed.isna().any():
        raise DataValidationError(f"invalid {field} value")
    return parsed.dt.date


def _as_optional_date_series(values: pd.Series, *, field: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    present = values.notna() & values.astype(str).str.strip().ne("")
    if parsed[present].isna().any():
        raise DataValidationError(f"invalid {field} value")
    return parsed.map(lambda value: value.date() if not pd.isna(value) else None)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(value: Any) -> str:
    """Normalize common A-share identifiers to six digits.

    Only six numeric characters are accepted after removing an optional exchange
    prefix/suffix. This also makes the value safe for use in a Parquet filename.
    """

    text = str(value).strip().upper().replace(" ", "")
    candidates = [part for part in re.split(r"[.]", text) if part]
    numeric = next((part for part in candidates if re.fullmatch(r"\d{6}", part)), None)
    if numeric is None and re.fullmatch(r"[A-Z]{2}\d{6}", text):
        numeric = text[-6:]
    if numeric is None:
        raise DataValidationError(f"invalid A-share symbol: {value!r}")
    return numeric


def _normalize_numeric(frame: pd.DataFrame, field: str, *, required: bool) -> pd.Series:
    column = _find_column(frame, _DAILY_ALIASES[field])
    if column is None:
        if required:
            raise DataValidationError(f"missing required columns: {field}")
        return pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Float64")
    values = pd.to_numeric(frame[column], errors="coerce")
    if required and values.isna().any():
        raise DataValidationError(f"invalid numeric values: {field}")
    return values


def _empty_daily_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(CANONICAL_DAILY_COLUMNS))


def normalize_daily_bars(
    raw: pd.DataFrame,
    *,
    symbol: str,
    source: str,
    fetched_at: str | None = None,
    data_version: str = "canonical-v1",
) -> pd.DataFrame:
    """Map a provider frame to the canonical daily-bar schema."""

    if not isinstance(raw, pd.DataFrame):
        raise DataValidationError("daily bars must be a pandas DataFrame")
    if raw.empty:
        return _empty_daily_frame()

    normalized_symbol = normalize_symbol(symbol)
    missing = [
        field
        for field in _REQUIRED_DAILY_FIELDS
        if _find_column(raw, _DAILY_ALIASES[field]) is None
    ]
    if missing:
        raise DataValidationError(f"missing required columns: {', '.join(missing)}")

    result = pd.DataFrame(index=raw.index)
    result["symbol"] = normalized_symbol
    date_column = _find_column(raw, _DAILY_ALIASES["date"])
    assert date_column is not None
    result["date"] = _as_date_series(raw[date_column], field="date")

    for field in ("open", "high", "low", "close", "volume", "amount"):
        result[field] = _normalize_numeric(raw, field, required=True)
    for field in ("amplitude_pct", "change_pct", "change_amount", "turnover_pct"):
        result[field] = _normalize_numeric(raw, field, required=False)

    result["source"] = source
    result["fetched_at"] = fetched_at or _utc_now()
    result["data_version"] = data_version

    numeric_fields = ("open", "high", "low", "close", "volume", "amount")
    if result[list(numeric_fields)].isna().any().any():
        raise DataValidationError("required daily numeric values cannot be null")
    if (result[["open", "high", "low", "close"]] <= 0).any().any():
        raise DataValidationError("prices must be positive")
    if (result[["volume", "amount"]] < 0).any().any():
        raise DataValidationError("volume and amount cannot be negative")
    if (result["high"] < result[["open", "close"]].max(axis=1)).any() or (
        result["low"] > result[["open", "close"]].min(axis=1)
    ).any():
        raise DataValidationError("invalid OHLC range")

    result = result.drop_duplicates(subset=["symbol", "date"], keep="last")
    result = result.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)
    if (result["volume"] % 1 == 0).all():
        result["volume"] = result["volume"].astype("int64")
    return result.loc[:, list(CANONICAL_DAILY_COLUMNS)]


def _bool_series(frame: pd.DataFrame, field: str, default: bool) -> pd.Series:
    column = _find_column(frame, _INSTRUMENT_ALIASES[field])
    if column is None:
        return pd.Series([default] * len(frame), index=frame.index, dtype="bool")
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(default).astype(bool)
    return values.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def normalize_instruments(
    raw: pd.DataFrame,
    *,
    source: str,
    as_of: date | str | None = None,
    fetched_at: str | None = None,
    data_version: str = "canonical-v1",
) -> pd.DataFrame:
    """Normalize a historical instrument snapshot without using today's universe."""

    if not isinstance(raw, pd.DataFrame):
        raise DataValidationError("instruments must be a pandas DataFrame")
    if raw.empty:
        return pd.DataFrame(columns=list(CANONICAL_INSTRUMENT_COLUMNS))
    symbol_column = _find_column(raw, _INSTRUMENT_ALIASES["symbol"])
    if symbol_column is None:
        raise DataValidationError("missing required columns: symbol")
    name_column = _find_column(raw, _INSTRUMENT_ALIASES["name"])
    listed_column = _find_column(raw, _INSTRUMENT_ALIASES["listed_date"])

    result = pd.DataFrame(index=raw.index)
    result["symbol"] = raw[symbol_column].map(normalize_symbol)
    result["name"] = raw[name_column].fillna("").astype(str) if name_column else ""
    result["exchange"] = result["symbol"].map(_infer_exchange)
    if listed_column:
        result["listed_date"] = _as_optional_date_series(raw[listed_column], field="listed_date")
    else:
        result["listed_date"] = pd.Series([None] * len(raw), index=raw.index, dtype="object")

    inferred_st = result["name"].str.upper().str.contains("ST", regex=False)
    result["is_st"] = _bool_series(raw, "is_st", False) | inferred_st
    inferred_delisting = result["name"].str.contains("退", regex=False)
    result["is_delisting_risk"] = _bool_series(raw, "is_delisting_risk", False) | inferred_delisting
    result["is_suspended"] = _bool_series(raw, "is_suspended", False)
    result["as_of"] = _parse_date(as_of, field="as_of") if as_of is not None else date.today()
    result["source"] = source
    result["fetched_at"] = fetched_at or _utc_now()
    result["data_version"] = data_version

    result = result.drop_duplicates(subset=["symbol"], keep="last").reset_index(drop=True)
    return result.loc[:, list(CANONICAL_INSTRUMENT_COLUMNS)]


def _infer_exchange(symbol: str) -> str:
    if symbol.startswith("6"):
        return "SH"
    if symbol.startswith(("0", "3")):
        return "SZ"
    if symbol.startswith(("4", "8", "9")):
        return "BJ"
    return "UNKNOWN"


def filter_point_in_time(
    frame: pd.DataFrame, *, as_of: date | str, publication_column: str = "announced_at"
) -> pd.DataFrame:
    """Keep only rows published no later than the requested information date."""

    if publication_column not in frame.columns:
        raise DataValidationError(f"missing publication timestamp: {publication_column}")
    parsed = pd.to_datetime(frame[publication_column], errors="coerce")
    if parsed.isna().any():
        raise DataValidationError("invalid publication timestamp")
    as_of_date = _parse_date(as_of, field="as_of")
    return frame.loc[parsed.dt.date <= as_of_date].copy().reset_index(drop=True)
