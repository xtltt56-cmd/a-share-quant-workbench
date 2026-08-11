"""Provider-to-provider comparison over canonical daily frames."""

from __future__ import annotations

from datetime import date
from math import isfinite
from typing import Any

import pandas as pd

from a_share_quant.data.normalization import normalize_symbol

from .registry import ProviderComparison

_REQUIRED_COLUMNS = frozenset({"symbol", "date", "close", "volume", "amount"})


def compare_daily_frames(
    incumbent_frame: pd.DataFrame,
    candidate_frame: pd.DataFrame,
    *,
    incumbent: str,
    candidate: str,
    as_of: date | str,
    price_tolerance: float = 1e-6,
) -> ProviderComparison:
    """Create promotion evidence from two already-normalized daily frames."""

    if price_tolerance < 0 or not isfinite(float(price_tolerance)):
        raise ValueError("price_tolerance must be finite and non-negative")
    incumbent_prepared = _prepare(incumbent_frame, "incumbent")
    candidate_prepared = _prepare(candidate_frame, "candidate")
    incumbent_keys = set(incumbent_prepared.index)
    candidate_keys = set(candidate_prepared.index)
    common = sorted(incumbent_keys & candidate_keys)
    coverage = len(common) / len(incumbent_keys) if incumbent_keys else 0.0
    identifier_match = (
        {key[0] for key in incumbent_keys} == {key[0] for key in candidate_keys}
        and incumbent_keys == candidate_keys
    )
    adjustment_match = _numeric_match(
        incumbent_prepared.loc[common, "close"],
        candidate_prepared.loc[common, "close"],
        tolerance=float(price_tolerance),
    ) if common else False
    suspension_match = _suspension_match(incumbent_prepared, candidate_prepared, common)
    timestamp_drift = _timestamp_drift(incumbent_prepared, candidate_prepared, common)
    return ProviderComparison(
        incumbent=incumbent,
        candidate=candidate,
        as_of=pd.to_datetime(as_of, errors="raise").date(),
        coverage_ratio=coverage,
        max_timestamp_drift_seconds=timestamp_drift,
        adjustment_match=adjustment_match,
        identifier_match=identifier_match,
        suspension_match=suspension_match,
    )


def _prepare(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{label} frame must be a pandas DataFrame")
    missing = sorted(_REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} frame missing required columns: {', '.join(missing)}")
    result = frame.copy()
    result["symbol"] = result["symbol"].map(normalize_symbol)
    result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date
    for field in ("close", "volume", "amount"):
        result[field] = pd.to_numeric(result[field], errors="coerce")
        if result[field].isna().any():
            raise ValueError(f"{label} frame contains invalid {field}")
    result["_key"] = list(zip(result["symbol"], result["date"], strict=True))
    if result["_key"].duplicated().any():
        raise ValueError(f"{label} frame contains duplicate symbol/date keys")
    if "is_suspended" in result:
        result["is_suspended"] = _bool_series(result["is_suspended"])
    if "fetched_at" in result:
        result["_fetched_at"] = pd.to_datetime(result["fetched_at"], errors="coerce", utc=True)
        if result["_fetched_at"].isna().any():
            raise ValueError(f"{label} frame contains invalid fetched_at")
    return result.set_index("_key", drop=False)


def _numeric_match(first: pd.Series, second: pd.Series, *, tolerance: float) -> bool:
    return bool(
        ((first - second).abs() <= tolerance * first.abs().clip(lower=1.0) + tolerance).all()
    )


def _suspension_match(first: pd.DataFrame, second: pd.DataFrame, common: list[Any]) -> bool:
    first_has = "is_suspended" in first.columns
    second_has = "is_suspended" in second.columns
    if not first_has and not second_has:
        return True
    if first_has != second_has or not common:
        return False
    return bool((first.loc[common, "is_suspended"] == second.loc[common, "is_suspended"]).all())


def _timestamp_drift(first: pd.DataFrame, second: pd.DataFrame, common: list[Any]) -> float:
    if "_fetched_at" not in first.columns or "_fetched_at" not in second.columns or not common:
        return 0.0
    differences = (
        first.loc[common, "_fetched_at"] - second.loc[common, "_fetched_at"]
    ).abs().dt.total_seconds()
    return float(differences.max()) if not differences.empty else 0.0


def _bool_series(values: pd.Series) -> pd.Series:
    if values.dtype == bool:
        return values.fillna(False).astype(bool)
    return values.astype(str).str.strip().str.lower().isin({"1", "true", "yes", "y"})
