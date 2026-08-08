"""Point-in-time feature storage with an explicit announcement boundary.

The store keeps observation dates, report periods, announcement dates, effective
dates, and ingestion timestamps as separate fields.  A report period is never
used as a proxy for when information became public.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from a_share_quant.config import PITConfig
from a_share_quant.contracts.data import DataValidationError
from a_share_quant.data.normalization import normalize_symbol

PIT_METADATA_COLUMNS = (
    "symbol",
    "trade_date",
    "report_period",
    "announcement_date",
    "effective_date",
    "ingest_time",
    "source",
    "data_version",
)

_REQUIRED_COLUMNS = ("symbol", "report_period", "announcement_date", "ingest_time")


class PITFeatureStore:
    """Store and query features using only information visible by ``asof_date``.

    ``frame`` is a wide table: every non-metadata column is treated as a feature.
    The class is intentionally independent of Qlib so the same PIT data can feed
    rule-based research and the Qlib adapter.
    """

    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        config: PITConfig | None = None,
        trading_calendar: Iterable[date | str] | None = None,
    ) -> None:
        if not isinstance(frame, pd.DataFrame):
            raise DataValidationError("PIT features must be a pandas DataFrame")
        self.config = config or PITConfig()
        self._trading_calendar = _normalize_calendar(trading_calendar)
        self._frame = self._prepare(frame.copy())

    @classmethod
    def from_parquet(
        cls,
        path: Path,
        *,
        config: PITConfig | None = None,
        trading_calendar: Iterable[date | str] | None = None,
    ) -> PITFeatureStore:
        """Load a PIT table previously written as Parquet."""

        return cls(
            pd.read_parquet(path),
            config=config,
            trading_calendar=trading_calendar,
        )

    @property
    def frame(self) -> pd.DataFrame:
        """Return a defensive copy of the normalized PIT table."""

        return self._frame.copy()

    @property
    def feature_columns(self) -> tuple[str, ...]:
        return tuple(column for column in self._frame.columns if column not in PIT_METADATA_COLUMNS)

    def to_parquet(self, path: Path) -> None:
        """Persist the normalized table without changing its visibility semantics."""

        path.parent.mkdir(parents=True, exist_ok=True)
        self._frame.to_parquet(path, index=False)

    def get_features_asof(self, symbol: str, asof_date: date | str) -> pd.DataFrame:
        """Return the latest disclosed feature row visible on a trading date.

        Visibility is determined by ``effective_date <= asof_date``.  Ingestion
        time is retained for audit and deterministic tie-breaking, but it cannot
        make a row visible before its effective date.
        """

        normalized_symbol = normalize_symbol(symbol)
        asof = _parse_date(asof_date, field="asof_date")
        visible = self._frame.loc[
            (self._frame["symbol"] == normalized_symbol)
            & self._frame["effective_date"].notna()
            & (self._frame["effective_date"] <= asof)
        ].copy()
        if visible.empty:
            return self._empty_result()

        sort_columns = [
            "effective_date",
            "announcement_date",
            "ingest_time",
            "report_period",
            "trade_date",
        ]
        visible = visible.sort_values(sort_columns, kind="stable", na_position="last")
        return visible.tail(1).reset_index(drop=True)

    def get_all_features_asof(self, asof_date: date | str) -> pd.DataFrame:
        """Return one latest visible row per symbol for a full cross-section."""

        asof = _parse_date(asof_date, field="asof_date")
        symbols = self._frame["symbol"].drop_duplicates().tolist()
        rows = [self.get_features_asof(symbol, asof) for symbol in symbols]
        non_empty = [row for row in rows if not row.empty]
        if not non_empty:
            return self._empty_result()
        return pd.concat(non_empty, ignore_index=True)

    def _prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
        if missing:
            raise DataValidationError(f"missing required PIT columns: {', '.join(missing)}")

        result = pd.DataFrame(index=frame.index)
        result["symbol"] = frame["symbol"].map(normalize_symbol)
        result["trade_date"] = _optional_date_series(
            frame.get("trade_date"), "trade_date", index=frame.index
        )
        result["report_period"] = _date_series(frame["report_period"], "report_period")
        result["announcement_date"] = _optional_date_series(
            frame["announcement_date"], "announcement_date"
        )
        result["ingest_time"] = _timestamp_series(frame["ingest_time"], "ingest_time")

        explicit_effective = _optional_date_series(
            frame.get("effective_date"), "effective_date", index=frame.index
        )
        result["effective_date"] = self._resolve_effective_dates(
            result["announcement_date"], explicit_effective
        )

        result["source"] = (
            frame["source"].fillna("unknown").astype(str)
            if "source" in frame.columns
            else "unknown"
        )
        result["data_version"] = (
            frame["data_version"].fillna("pit-v1").astype(str)
            if "data_version" in frame.columns
            else "pit-v1"
        )

        metadata_input = set(PIT_METADATA_COLUMNS)
        feature_columns = [column for column in frame.columns if column not in metadata_input]
        for column in feature_columns:
            result[column] = frame[column].values

        ordered_columns = [
            *PIT_METADATA_COLUMNS,
            *feature_columns,
        ]
        return result.loc[:, ordered_columns].reset_index(drop=True)

    def _resolve_effective_dates(
        self,
        announcement_dates: pd.Series,
        explicit_effective: pd.Series,
    ) -> pd.Series:
        policy = self.config.announcement_day_policy
        if policy not in {"next_trading_day", "same_day"}:
            raise DataValidationError(f"unsupported announcement day policy: {policy}")
        unknown_policy = self.config.unknown_announcement_date
        if unknown_policy not in {"reject", "exclude"}:
            raise DataValidationError(f"unsupported unknown announcement policy: {unknown_policy}")

        values: list[date | None] = []
        for announcement, explicit in zip(announcement_dates, explicit_effective, strict=True):
            if announcement is None or pd.isna(announcement):
                if unknown_policy == "reject":
                    raise DataValidationError("announcement_date is required for PIT visibility")
                values.append(None)
                continue

            minimum_effective = announcement
            if not self.config.allow_same_day_announcement and policy != "same_day":
                minimum_effective = self._next_trading_day(announcement)
            if explicit is not None and not pd.isna(explicit):
                if explicit < announcement:
                    raise DataValidationError("effective_date cannot precede announcement_date")
                values.append(max(explicit, minimum_effective))
            else:
                values.append(minimum_effective)
        return pd.Series(values, index=announcement_dates.index, dtype="object")

    def _next_trading_day(self, current: date) -> date:
        if self._trading_calendar:
            index = bisect_right(self._trading_calendar, current)
            if index >= len(self._trading_calendar):
                raise DataValidationError(f"trading calendar has no date after {current}")
            return self._trading_calendar[index]

        candidate = current + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate

    def _empty_result(self) -> pd.DataFrame:
        return self._frame.iloc[0:0].copy().reset_index(drop=True)


def _normalize_calendar(values: Iterable[date | str] | None) -> tuple[date, ...]:
    if values is None:
        return ()
    parsed = sorted({_parse_date(value, field="trading_calendar") for value in values})
    return tuple(parsed)


def _parse_date(value: date | str | datetime, *, field: str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise DataValidationError(f"invalid {field}: {value!r}")
    if isinstance(parsed, pd.Timestamp):
        return parsed.date()
    return parsed.date()


def _optional_date_series(
    values: pd.Series | None,
    field: str,
    *,
    index: pd.Index | None = None,
) -> pd.Series:
    if values is None:
        if index is None:
            raise DataValidationError(f"missing index for optional {field}")
        return pd.Series([None] * len(index), index=index, dtype="object")
    parsed = pd.to_datetime(values, errors="coerce")
    present = values.notna() & values.astype(str).str.strip().ne("")
    if parsed[present].isna().any():
        raise DataValidationError(f"invalid {field} value")
    return parsed.map(lambda value: value.date() if not pd.isna(value) else None).astype(object)


def _date_series(values: pd.Series, field: str) -> pd.Series:
    result = _optional_date_series(values, field)
    if result.isna().any():
        raise DataValidationError(f"{field} cannot be null")
    return result


def _timestamp_series(values: pd.Series, field: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    if parsed.isna().any():
        raise DataValidationError(f"invalid {field} value")
    return parsed
