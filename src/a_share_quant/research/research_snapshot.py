"""Leakage-safe, immutable point-in-time research snapshots.

The snapshot builder is deliberately downstream of :class:`ResearchDataStore`.
It accepts manifest-backed ``ResearchArtifact`` objects rather than arbitrary
paths, verifies every artifact before reading it, and records the source
digests in the resulting fingerprint.  A snapshot is a research input
boundary; it is not a trading or model-promotion decision.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from a_share_quant.research.history_contracts import ResearchArtifact
from a_share_quant.storage.research_data_store import ResearchDataset, ResearchDataStore


class SnapshotIntegrityError(RuntimeError):
    """Raised when a snapshot input is not a verified, well-formed artifact."""


class SnapshotSchemaError(SnapshotIntegrityError):
    """Raised when a verified artifact lacks the snapshot contract fields."""


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    """A defensive, content-addressed view of research inputs at one cutoff.

    The data frames are copied when constructed and whenever exposed through a
    property.  Metadata mappings and symbol lists are represented internally
    as tuples, so changing a caller-owned mapping cannot change an evaluated
    snapshot.
    """

    _signal_cutoff: date
    _universe_version: str
    _feature_version: str
    _label_version: str
    _universe: pd.DataFrame
    _features: pd.DataFrame
    _labels: pd.DataFrame
    _source_artifact_hashes: tuple[tuple[str, str], ...]
    _eligible_symbols: tuple[str, ...]
    _exclusions: tuple[tuple[str, str], ...]
    _canonical_sha256: str
    _snapshot_artifact: ResearchArtifact | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self._signal_cutoff, date):
            raise TypeError("signal_cutoff must be a date")
        for value, field_name in (
            (self._universe_version, "universe_version"),
            (self._feature_version, "feature_version"),
            (self._label_version, "label_version"),
            (self._canonical_sha256, "canonical_sha256"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        if len(self._canonical_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self._canonical_sha256
        ):
            raise ValueError("canonical_sha256 must be a lowercase SHA-256 digest")
        for frame, field_name in (
            (self._universe, "universe"),
            (self._features, "features"),
            (self._labels, "labels"),
        ):
            if not isinstance(frame, pd.DataFrame):
                raise TypeError(f"{field_name} must be a pandas DataFrame")
            frame_copy = frame.copy(deep=True)
            frame_copy.flags.writeable = False
            object.__setattr__(self, f"_{field_name}", frame_copy)

    @property
    def signal_cutoff(self) -> date:
        return self._signal_cutoff

    @property
    def signal_date(self) -> date:
        """Alias used by the research and operator-facing APIs."""

        return self._signal_cutoff

    @property
    def universe_version(self) -> str:
        return self._universe_version

    @property
    def feature_version(self) -> str:
        return self._feature_version

    @property
    def label_version(self) -> str:
        return self._label_version

    @property
    def universe(self) -> pd.DataFrame:
        return self._universe.copy(deep=True)

    @property
    def features(self) -> pd.DataFrame:
        return self._features.copy(deep=True)

    @property
    def labels(self) -> pd.DataFrame:
        return self._labels.copy(deep=True)

    @property
    def source_artifact_hashes(self) -> dict[str, str]:
        return dict(self._source_artifact_hashes)

    @property
    def eligible_symbols(self) -> tuple[str, ...]:
        return self._eligible_symbols

    @property
    def tradable_symbols(self) -> tuple[str, ...]:
        return self._eligible_symbols

    @property
    def exclusions(self) -> dict[str, str]:
        return dict(self._exclusions)

    @property
    def canonical_sha256(self) -> str:
        return self._canonical_sha256

    @property
    def snapshot_id(self) -> str:
        """The content address; source changes necessarily create a new ID."""

        return self._canonical_sha256

    @property
    def snapshot_artifact(self) -> ResearchArtifact | None:
        return self._snapshot_artifact

    def metadata(self) -> dict[str, Any]:
        """Return JSON-safe metadata without exposing mutable frame objects."""

        return {
            "canonical_sha256": self._canonical_sha256,
            "eligible_symbols": list(self._eligible_symbols),
            "exclusions": dict(self._exclusions),
            "feature_version": self._feature_version,
            "label_version": self._label_version,
            "signal_cutoff": self._signal_cutoff.isoformat(),
            "snapshot_id": self.snapshot_id,
            "source_artifact_hashes": dict(self._source_artifact_hashes),
            "universe_version": self._universe_version,
        }


class ResearchSnapshotBuilder:
    """Build a frozen research snapshot from verified local artifacts only."""

    def __init__(
        self,
        store: ResearchDataStore,
        universe_artifact: ResearchArtifact | None = None,
        feature_artifact: ResearchArtifact | None = None,
        label_artifact: ResearchArtifact | None = None,
        *,
        sources: Mapping[str, ResearchArtifact] | None = None,
        universe_key: str | None = None,
        feature_key: str | None = None,
        label_key: str | None = None,
        universe_dataset: str | ResearchDataset = ResearchDataset.INSTRUMENT_HISTORY,
        feature_dataset: str | ResearchDataset = ResearchDataset.RESEARCH_RETURNS,
        label_dataset: str | ResearchDataset = ResearchDataset.TRIAL_EVIDENCE,
        universe_version: str = "universe-v1",
        feature_version: str = "features-v1",
        label_version: str = "labels-v1",
        persist: bool = False,
    ) -> None:
        if not isinstance(store, ResearchDataStore):
            raise TypeError("store must be a ResearchDataStore")
        if sources is not None:
            if universe_artifact is None:
                universe_artifact = sources.get("universe")
            if feature_artifact is None:
                feature_artifact = sources.get("features", sources.get("feature"))
            if label_artifact is None:
                label_artifact = sources.get("labels", sources.get("label"))
        if universe_artifact is None and universe_key is not None:
            universe_artifact = store.active_artifact(universe_dataset, universe_key)
        if feature_artifact is None and feature_key is not None:
            feature_artifact = store.active_artifact(feature_dataset, feature_key)
        if label_artifact is None and label_key is not None:
            label_artifact = store.active_artifact(label_dataset, label_key)
        if universe_artifact is None or feature_artifact is None:
            raise ValueError("universe_artifact and feature_artifact are required")
        for value, field_name in (
            (universe_version, "universe_version"),
            (feature_version, "feature_version"),
            (label_version, "label_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")

        self.store = store
        self.universe_artifact = universe_artifact
        self.feature_artifact = feature_artifact
        self.label_artifact = label_artifact
        self.universe_version = universe_version
        self.feature_version = feature_version
        self.label_version = label_version
        self.persist = bool(persist)

    def build(
        self,
        signal_date: date | str | datetime | None = None,
        *,
        signal_cutoff: date | str | datetime | None = None,
        persist: bool | None = None,
    ) -> ResearchSnapshot:
        cutoff = _resolve_cutoff(signal_date, signal_cutoff)
        universe = self._read_verified(self.universe_artifact, "universe")
        features = self._read_verified(self.feature_artifact, "features")
        labels = (
            self._read_verified(self.label_artifact, "labels")
            if self.label_artifact is not None
            else pd.DataFrame()
        )

        prepared_universe, eligible, exclusions = _build_point_in_time_universe(
            universe, cutoff
        )
        prepared_features = _build_visible_features(features, cutoff, eligible)
        prepared_labels = _build_labels(labels, eligible)
        source_hashes = {
            "universe": self.universe_artifact.sha256,
            "features": self.feature_artifact.sha256,
        }
        if self.label_artifact is not None:
            source_hashes["labels"] = self.label_artifact.sha256

        payload = {
            "feature_version": self.feature_version,
            "features_sha256": _frame_sha256(prepared_features),
            "label_version": self.label_version,
            "labels_sha256": _frame_sha256(prepared_labels),
            "signal_cutoff": cutoff.isoformat(),
            "source_artifact_hashes": source_hashes,
            "eligible_symbols": list(eligible),
            "exclusions": dict(exclusions),
            "universe_sha256": _frame_sha256(prepared_universe),
            "universe_version": self.universe_version,
        }
        canonical_sha256 = _payload_sha256(payload)
        snapshot = ResearchSnapshot(
            _signal_cutoff=cutoff,
            _universe_version=self.universe_version,
            _feature_version=self.feature_version,
            _label_version=self.label_version,
            _universe=prepared_universe,
            _features=prepared_features,
            _labels=prepared_labels,
            _source_artifact_hashes=tuple(sorted(source_hashes.items())),
            _eligible_symbols=tuple(eligible),
            _exclusions=tuple(sorted(exclusions.items())),
            _canonical_sha256=canonical_sha256,
        )
        if self.persist if persist is None else bool(persist):
            return self._persist(snapshot)
        return snapshot

    def _read_verified(self, artifact: ResearchArtifact, role: str) -> pd.DataFrame:
        if not isinstance(artifact, ResearchArtifact):
            raise SnapshotIntegrityError(f"{role} artifact must be a ResearchArtifact")
        try:
            self.store.policy.revalidate(artifact.path)
            verified_before = self.store.verify(artifact)
            if not verified_before:
                raise SnapshotIntegrityError(f"{role} artifact manifest 校验失败")
            frame = pd.read_parquet(artifact.path)
            self.store.policy.revalidate(artifact.path)
            verified_after = self.store.verify(artifact)
            if not verified_after:
                raise SnapshotIntegrityError(f"{role} artifact manifest 校验失败")
        except SnapshotIntegrityError:
            raise
        except Exception as exc:
            raise SnapshotIntegrityError(f"{role} artifact 无法读取或 manifest 校验失败") from exc
        if not isinstance(frame, pd.DataFrame):
            raise SnapshotIntegrityError(f"{role} artifact 不是表格")
        return frame.copy(deep=True)

    def _persist(self, snapshot: ResearchSnapshot) -> ResearchSnapshot:
        metadata = snapshot.metadata()
        frame = pd.DataFrame(
            [
                {
                    **metadata,
                    "eligible_symbols": json.dumps(
                        metadata["eligible_symbols"], ensure_ascii=False, separators=(",", ":")
                    ),
                    "exclusions": json.dumps(
                        metadata["exclusions"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "source_artifact_hashes": json.dumps(
                        metadata["source_artifact_hashes"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ]
        )
        artifact = self.store.replace_dataset(
            ResearchDataset.FROZEN_SNAPSHOTS,
            f"snapshot-{snapshot.snapshot_id}",
            frame,
            f"research-snapshot:{snapshot.snapshot_id}",
        )
        return ResearchSnapshot(
            _signal_cutoff=snapshot.signal_cutoff,
            _universe_version=snapshot.universe_version,
            _feature_version=snapshot.feature_version,
            _label_version=snapshot.label_version,
            _universe=snapshot.universe,
            _features=snapshot.features,
            _labels=snapshot.labels,
            _source_artifact_hashes=tuple(snapshot.source_artifact_hashes.items()),
            _eligible_symbols=snapshot.eligible_symbols,
            _exclusions=tuple(snapshot.exclusions.items()),
            _canonical_sha256=snapshot.canonical_sha256,
            _snapshot_artifact=artifact,
        )


def _resolve_cutoff(
    signal_date: date | str | datetime | None,
    signal_cutoff: date | str | datetime | None,
) -> date:
    if signal_date is not None and signal_cutoff is not None:
        first = _as_date(signal_date, "signal_date")
        second = _as_date(signal_cutoff, "signal_cutoff")
        if first != second:
            raise ValueError("signal_date and signal_cutoff must match")
        return first
    value = signal_cutoff if signal_cutoff is not None else signal_date
    if value is None:
        raise ValueError("signal_date is required")
    return _as_date(value, "signal_date")


def _as_date(value: date | str | datetime, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid {field_name}: {value!r}")
    return parsed.date()


def _build_point_in_time_universe(
    frame: pd.DataFrame, cutoff: date
) -> tuple[pd.DataFrame, tuple[str, ...], dict[str, str]]:
    required = {"symbol", "listed_date"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise SnapshotSchemaError(
            f"universe artifact missing required fields: {', '.join(missing)}"
        )
    result = frame.copy(deep=True)
    result["symbol"] = result["symbol"].map(_symbol)
    if result["symbol"].eq("").any():
        raise SnapshotSchemaError("universe artifact contains an empty symbol")
    result["listed_date"] = _date_column(result["listed_date"], "listed_date", required=True)
    if "delisted_date" in result:
        result["delisted_date"] = _date_column(
            result["delisted_date"], "delisted_date", required=False
        )
    else:
        result["delisted_date"] = pd.Series([None] * len(result), dtype="object")
    if "as_of" in result:
        result["as_of"] = _date_column(result["as_of"], "as_of", required=False)
    else:
        result["as_of"] = result["listed_date"]
    result["as_of"] = result["as_of"].where(result["as_of"].notna(), result["listed_date"])
    result["_row_order"] = range(len(result))

    exclusions: dict[str, str] = {}
    for symbol, rows in result.groupby("symbol", sort=True):
        if rows["listed_date"].min() > cutoff:
            exclusions[symbol] = "NOT_YET_LISTED"

    visible = result.loc[result["as_of"].le(cutoff)].copy()
    visible = (
        visible.sort_values(["symbol", "as_of", "_row_order"], kind="stable")
        .drop_duplicates("symbol", keep="last")
        .reset_index(drop=True)
    )
    if visible.empty:
        return visible.drop(columns=["_row_order"]), (), exclusions

    kept_rows: list[int] = []
    for row_index, row in visible.iterrows():
        symbol = str(row["symbol"])
        if row["listed_date"] > cutoff:
            exclusions[symbol] = "NOT_YET_LISTED"
            continue
        delisted = row["delisted_date"]
        if delisted is not None and not pd.isna(delisted) and delisted <= cutoff:
            exclusions[symbol] = "DELISTED"
            continue
        trade_status = _row_trade_status(row, visible.columns)
        is_st = _known_bool(row.get("is_st")) if "is_st" in visible else None
        if trade_status is None or is_st is None:
            exclusions[symbol] = "UNKNOWN_TRADE_STATUS"
        elif not trade_status:
            exclusions[symbol] = "NOT_TRADABLE"
        elif is_st:
            exclusions[symbol] = "ST"
        elif _known_bool(row.get("is_suspended")) is True:
            exclusions[symbol] = "SUSPENDED"
        elif _known_bool(row.get("is_delisting_risk")) is True:
            exclusions[symbol] = "DELISTING_RISK"
        else:
            kept_rows.append(row_index)

    universe = visible.loc[visible.index.isin(kept_rows) | visible["symbol"].isin(exclusions)]
    # The table is the point-in-time visible universe, including rows that are
    # explicitly excluded so the reason can be audited.  Already delisted and
    # not-yet-listed rows are absent by construction.
    universe = universe.loc[universe["listed_date"].le(cutoff)]
    universe = universe.loc[
        universe["delisted_date"].isna() | universe["delisted_date"].gt(cutoff)
    ]
    universe = universe.drop(columns=["_row_order"], errors="ignore")
    universe = universe.sort_values("symbol", kind="stable").reset_index(drop=True)
    eligible = tuple(sorted(visible.loc[kept_rows, "symbol"].astype(str).tolist()))
    return universe, eligible, dict(sorted(exclusions.items()))


def _build_visible_features(
    frame: pd.DataFrame, cutoff: date, eligible_symbols: tuple[str, ...]
) -> pd.DataFrame:
    if "symbol" not in frame.columns or "available_at" not in frame.columns:
        raise SnapshotSchemaError("features artifact requires symbol and available_at")
    result = frame.copy(deep=True)
    result["symbol"] = result["symbol"].map(_symbol)
    result["available_at"] = _date_column(result["available_at"], "available_at", required=True)
    result = result.loc[
        result["available_at"].le(cutoff)
        & result["symbol"].isin(set(eligible_symbols))
    ].copy()
    return result.sort_values(["symbol", "available_at"], kind="stable").reset_index(drop=True)


def _build_labels(frame: pd.DataFrame, eligible_symbols: tuple[str, ...]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy(deep=True)
    if "symbol" not in frame.columns:
        raise SnapshotSchemaError("labels artifact requires symbol")
    result = frame.copy(deep=True)
    result["symbol"] = result["symbol"].map(_symbol)
    for column in ("label_date", "available_at", "as_of"):
        if column in result:
            result[column] = _date_column(result[column], column, required=False)
    return result.loc[result["symbol"].isin(set(eligible_symbols))].sort_values(
        ["symbol", *(["label_date"] if "label_date" in result else [])], kind="stable"
    ).reset_index(drop=True)


def _symbol(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _date_column(values: pd.Series, field_name: str, *, required: bool) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    present = values.notna() & values.astype(str).str.strip().ne("")
    if parsed[present].isna().any() or (required and parsed.isna().any()):
        raise SnapshotSchemaError(f"invalid {field_name} in research snapshot artifact")
    return parsed.map(lambda value: value.date() if not pd.isna(value) else None).astype(object)


def _known_bool(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "st", "是", "正常"}:
        return True
    if text in {"0", "false", "no", "n", "非st", "否"}:
        return False
    return None


def _known_trade_status(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and math.isnan(value)) or pd.isna(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "trade", "trading", "tradable", "正常", "交易"}:
        return True
    if text in {"0", "false", "no", "n", "suspend", "suspended", "停牌", "不可交易"}:
        return False
    return None


def _row_trade_status(row: pd.Series, columns: Any) -> bool | None:
    if "trade_status" in columns:
        return _known_trade_status(row.get("trade_status"))
    # BaoStock's instrument-history contract may expose the derived suspension
    # flag instead of the raw daily trade status.  It is safe to use only when
    # that flag is explicitly present and boolean-like; absent/unknown remains
    # UNKNOWN_TRADE_STATUS.
    if "tradable" in columns:
        return _known_bool(row.get("tradable"))
    if "is_suspended" in columns:
        suspended = _known_bool(row.get("is_suspended"))
        return None if suspended is None else not suspended
    return None


def _frame_sha256(frame: pd.DataFrame) -> str:
    column_pairs = sorted((str(column), column) for column in frame.columns)
    columns = [name for name, _ in column_pairs]
    ordered = frame.loc[:, [column for _, column in column_pairs]].copy()
    ordered.columns = columns
    if columns and len(ordered):
        ordered = ordered.assign(
            __sort_key=ordered.astype(str).agg("\x1f".join, axis=1)
        ).sort_values("__sort_key", kind="stable").drop(columns=["__sort_key"])
    records = [
        {column: _json_value(value) for column, value in zip(ordered.columns, row, strict=True)}
        for row in ordered.itertuples(index=False, name=None)
    ]
    return _payload_sha256({"columns": columns, "rows": records})


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if hasattr(value, "item"):
        return _json_value(value.item())
    if pd.isna(value):
        return None
    return str(value)


__all__ = [
    "ResearchSnapshot",
    "ResearchSnapshotBuilder",
    "SnapshotIntegrityError",
    "SnapshotSchemaError",
]
