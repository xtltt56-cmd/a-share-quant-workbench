"""Historical, point-in-time tradable-universe construction."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from a_share_quant.contracts.data import DataValidationError
from a_share_quant.data.normalization import normalize_symbol


class HistoricalUniverse:
    """Filter instruments using the latest status known on a requested date.

    The input is a historical snapshot table.  Each row is effective from its
    ``as_of`` date until a later snapshot for the same symbol appears.  This is
    deliberately different from filtering today's instrument list and prevents
    survivorship bias in backtests.
    """

    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        exclude_new_days: int = 60,
        min_average_amount: float | None = 10_000_000,
        min_average_turnover_pct: float | None = 0.5,
    ) -> None:
        if not isinstance(frame, pd.DataFrame):
            raise DataValidationError("historical universe must be a pandas DataFrame")
        if exclude_new_days < 0:
            raise DataValidationError("exclude_new_days cannot be negative")
        for name, value in (
            ("min_average_amount", min_average_amount),
            ("min_average_turnover_pct", min_average_turnover_pct),
        ):
            if value is not None and value < 0:
                raise DataValidationError(f"{name} cannot be negative")

        self.exclude_new_days = exclude_new_days
        self.min_average_amount = min_average_amount
        self.min_average_turnover_pct = min_average_turnover_pct
        self._frame = _prepare_universe_frame(frame.copy())

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame.copy()

    def tradable_universe(
        self,
        asof_date: date | str,
        *,
        liquidity: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Return symbols tradable under the historical state at ``asof_date``."""

        requested = _parse_date(asof_date, field="asof_date")
        visible = self._frame.loc[self._frame["as_of"] <= requested].copy()
        if visible.empty:
            return self._empty_result(requested)

        visible = (
            visible.sort_values(["symbol", "as_of", "_row_order"], kind="stable")
            .drop_duplicates("symbol", keep="last")
            .reset_index(drop=True)
        )
        visible["_requested_date"] = requested

        visible["listing_age_days"] = visible["listed_date"].map(
            lambda listed: (requested - listed).days
        )
        eligible = (
            visible["listed_date"].le(requested)
            & visible["listing_age_days"].ge(self.exclude_new_days)
            & (visible["delisted_date"].isna() | visible["delisted_date"].gt(requested))
            & ~visible["is_st"]
            & ~visible["is_delisting_risk"]
            & ~visible["is_suspended"]
            & ~visible.apply(lambda row: _active_interval(row, "st"), axis=1)
            & ~visible.apply(lambda row: _active_interval(row, "suspension"), axis=1)
        )
        visible = visible.loc[eligible].copy()

        if liquidity is not None:
            visible = self._merge_liquidity(visible, liquidity, requested)

        if self.min_average_amount is not None:
            visible = visible.loc[visible["average_amount"].ge(self.min_average_amount)]
        if self.min_average_turnover_pct is not None:
            visible = visible.loc[
                visible["average_turnover_pct"].ge(self.min_average_turnover_pct)
            ]

        visible["universe_date"] = requested
        visible = visible.drop(columns=["_requested_date"])
        return visible.sort_values("symbol", kind="stable").reset_index(drop=True)

    def _merge_liquidity(
        self,
        universe: pd.DataFrame,
        liquidity: pd.DataFrame,
        requested: date,
    ) -> pd.DataFrame:
        if not isinstance(liquidity, pd.DataFrame):
            raise DataValidationError("liquidity must be a pandas DataFrame")
        prepared = _prepare_liquidity_frame(liquidity.copy())
        prepared = prepared.loc[prepared["as_of"] <= requested]
        prepared = (
            prepared.sort_values(["symbol", "as_of", "_row_order"], kind="stable")
            .drop_duplicates("symbol", keep="last")
            .loc[:, ["symbol", "average_amount", "average_turnover_pct"]]
        )
        merged = universe.drop(columns=["average_amount", "average_turnover_pct"])
        return merged.merge(prepared, on="symbol", how="left", validate="one_to_one")

    def _empty_result(self, requested: date) -> pd.DataFrame:
        result = self._frame.iloc[0:0].copy()
        result["listing_age_days"] = pd.Series(dtype="int64")
        result["universe_date"] = pd.Series(dtype="object")
        return result


def _prepare_universe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if "symbol" not in frame.columns:
        raise DataValidationError("missing required universe column: symbol")
    if "listed_date" not in frame.columns:
        raise DataValidationError("missing required universe column: listed_date")

    result = pd.DataFrame(index=frame.index)
    result["symbol"] = frame["symbol"].map(normalize_symbol)
    result["listed_date"] = _date_series(frame["listed_date"], "listed_date", required=True)
    result["delisted_date"] = _date_series(
        frame.get("delisted_date"), "delisted_date", required=False, index=frame.index
    )
    result["as_of"] = _date_series(
        frame.get("as_of", frame.get("snapshot_date")),
        "as_of",
        required=False,
        index=frame.index,
    )
    result["as_of"] = result["as_of"].fillna(result["listed_date"])

    result["is_st"] = _bool_series(frame, "is_st")
    if "name" in frame.columns:
        result["is_st"] = result["is_st"] | frame["name"].astype(str).str.upper().str.contains("ST")
    result["is_delisting_risk"] = _bool_series(frame, "is_delisting_risk")
    result["is_suspended"] = _bool_series(frame, "is_suspended")

    for prefix, aliases in {
        "st_effective_date": ("st_effective_date", "st_start_date"),
        "st_end_date": ("st_end_date",),
        "suspension_effective_date": (
            "suspension_effective_date",
            "suspended_effective_date",
            "suspension_start_date",
        ),
        "suspension_end_date": (
            "suspension_end_date",
            "suspended_end_date",
            "suspension_stop_date",
        ),
    }.items():
        source = _first_column(frame, aliases)
        result[prefix] = _date_series(
            frame[source] if source else None,
            prefix,
            required=False,
            index=frame.index,
        )

    result["average_amount"] = _numeric_series(
        frame, ("average_amount", "avg_amount", "min_average_amount")
    )
    result["average_turnover_pct"] = _numeric_series(
        frame, ("average_turnover_pct", "avg_turnover_pct", "min_average_turnover_pct")
    )
    result["_row_order"] = range(len(result))
    return result.reset_index(drop=True)


def _prepare_liquidity_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if "symbol" not in frame.columns:
        raise DataValidationError("missing required liquidity column: symbol")
    source_as_of = frame.get("as_of", frame.get("trade_date"))
    if source_as_of is None:
        raise DataValidationError("liquidity requires as_of or trade_date")
    result = pd.DataFrame(index=frame.index)
    result["symbol"] = frame["symbol"].map(normalize_symbol)
    result["as_of"] = _date_series(source_as_of, "liquidity.as_of", required=True)
    result["average_amount"] = _numeric_series(
        frame, ("average_amount", "avg_amount", "amount")
    )
    result["average_turnover_pct"] = _numeric_series(
        frame, ("average_turnover_pct", "avg_turnover_pct", "turnover_pct")
    )
    result["_row_order"] = range(len(result))
    return result.reset_index(drop=True)


def _first_column(frame: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def _bool_series(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(False, index=frame.index, dtype="bool")
    values = frame[name]
    if values.dtype == bool:
        return values.fillna(False).astype(bool)
    return values.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})


def _numeric_series(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    source = _first_column(frame, names)
    if source is None:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[source], errors="coerce")


def _date_series(
    values: pd.Series | None,
    field: str,
    *,
    required: bool,
    index: pd.Index | None = None,
) -> pd.Series:
    if values is None:
        if index is None:
            if required:
                raise DataValidationError(f"missing {field}")
            return pd.Series(dtype="object")
        return pd.Series([None] * len(index), index=index, dtype="object")
    parsed = pd.to_datetime(values, errors="coerce")
    present = values.notna() & values.astype(str).str.strip().ne("")
    if parsed[present].isna().any():
        raise DataValidationError(f"invalid {field} value")
    result = parsed.map(lambda value: value.date() if not pd.isna(value) else None).astype(object)
    if required and result.isna().any():
        raise DataValidationError(f"{field} cannot be null")
    return result


def _parse_date(value: date | str | datetime, *, field: str) -> date:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise DataValidationError(f"invalid {field}: {value!r}")
    return parsed.date()


def _active_interval(row: pd.Series, prefix: str) -> bool:
    start = row[f"{prefix}_effective_date"]
    end = row[f"{prefix}_end_date"]
    requested = row["_requested_date"] if "_requested_date" in row else None
    if requested is None or start is None or pd.isna(start):
        return False
    return start <= requested and (end is None or pd.isna(end) or requested < end)
