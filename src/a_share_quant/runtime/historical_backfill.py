"""Resumable, quota-guarded historical research backfill orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import pandas as pd

from a_share_quant.contracts.data import ProviderRequestError
from a_share_quant.research.history_contracts import DatasetCoverage
from a_share_quant.storage.research_data_store import (
    ResearchDataset,
    ResearchDataStore,
    StorageQuotaError,
)


class CheckpointIntegrityError(RuntimeError):
    """Raised when a historical-backfill checkpoint cannot be trusted."""


class TransientProviderError(ProviderRequestError):
    """Raised after bounded retries exhaust a confirmed transport failure."""


class HistoricalHistoryProvider(Protocol):
    """Provider surface required by the historical coordinator."""

    name: str

    def list_research_instruments(self, as_of: date | str | None = None) -> pd.DataFrame:
        """Return point-in-time instruments including inactive securities."""

    def get_research_history(
        self, symbol: str, start_date: date | str, end_date: date | str
    ) -> pd.DataFrame:
        """Return governed research history for one symbol and inclusive range."""


@dataclass(frozen=True)
class BackfillResult:
    symbols_updated: int
    rows_written: int
    failures: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols_updated": self.symbols_updated,
            "rows_written": self.rows_written,
            "failures": dict(self.failures),
        }


@dataclass(frozen=True)
class HistoricalCoverage(DatasetCoverage):
    """Offline coverage view backed only by a verified local checkpoint."""

    row_count: int
    symbols: tuple[str, ...]
    earliest_date: date | None
    latest_date: date | None
    unavailable_status_fields: Mapping[str, tuple[str, ...]]
    size_bytes: int
    exclusion_reasons: Mapping[str, tuple[str, ...]]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("start_date", "end_date", "earliest_date", "latest_date"):
            value = payload[key]
            payload[key] = value.isoformat() if value is not None else None
        payload["symbols"] = list(self.symbols)
        payload["unavailable_status_fields"] = {
            symbol: list(fields)
            for symbol, fields in self.unavailable_status_fields.items()
        }
        payload["exclusion_reasons"] = {
            symbol: list(reasons) for symbol, reasons in self.exclusion_reasons.items()
        }
        return payload


class HistoricalBackfillCoordinator:
    """Download at most one bounded batch and checkpoint each completed symbol."""

    MAX_SYMBOLS_PER_BATCH = 100
    CHECKPOINT_FORMAT_VERSION = 1
    CHECKPOINT_MAX_BYTES = 8 * 1024 * 1024

    def __init__(
        self,
        store: ResearchDataStore,
        provider: HistoricalHistoryProvider | None,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        retry_count: int = 3,
        delay_seconds: float = 0.25,
        disk_usage: Callable[[str | os.PathLike[str]], Any] | None = None,
        directory_fsync: Callable[[Path], None] | None = None,
        max_request_days: int | None = None,
    ) -> None:
        if retry_count < 0 or delay_seconds < 0 or (
            max_request_days is not None and int(max_request_days) < 1
        ):
            raise ValueError("retry, delay and request bounds must be non-negative")
        self.store = store
        self.provider = provider
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleeper = sleeper
        self.retry_count = int(retry_count)
        self.delay_seconds = float(delay_seconds)
        self._disk_usage = disk_usage or getattr(store, "_disk_usage", shutil.disk_usage)
        self._directory_fsync = directory_fsync or _fsync_directory
        self.max_request_days = (
            int(max_request_days) if max_request_days is not None else None
        )
        self.data_root = store.root_directory
        self.checkpoint_path = store.policy.authorize(
            ".runtime/research/historical-backfill-checkpoint.json"
        )
        self._last_request_completed = False

    def run(
        self,
        *,
        start: date | str,
        end: date | str,
        limit: int = MAX_SYMBOLS_PER_BATCH,
    ) -> BackfillResult:
        start_day = _as_date(start, "start")
        end_day = _as_date(end, "end")
        if start_day > end_day:
            raise ValueError("start must not be after end")
        if not 1 <= int(limit) <= self.MAX_SYMBOLS_PER_BATCH:
            raise ValueError("one history batch must contain between 1 and 100 symbols")
        if self.provider is None:
            raise RuntimeError("历史回填需要显式启用网络数据源")

        self._ensure_download_capacity()
        checkpoint = self._load_checkpoint()
        instruments = self._request(
            self.provider.list_research_instruments,
            as_of=end_day,
        )
        instruments = self._validate_instruments(instruments)
        self._persist_instrument_history(instruments, end_day)

        previous_target = checkpoint.get("target")
        previous_start = (
            _optional_checkpoint_date(previous_target.get("start_date"))
            if isinstance(previous_target, Mapping)
            else None
        )
        previous_end = (
            _optional_checkpoint_date(previous_target.get("end_date"))
            if isinstance(previous_target, Mapping)
            else None
        )
        checkpoint["target"] = {
            "start_date": min(
                (item for item in (previous_start, start_day) if item is not None),
                default=start_day,
            ).isoformat(),
            "end_date": max(
                (item for item in (previous_end, end_day) if item is not None),
                default=end_day,
            ).isoformat(),
        }
        checkpoint.setdefault("symbols", {})
        checkpoint.setdefault("failures", {})
        symbols_updated = 0
        rows_written = 0
        attempted = 0
        failures: dict[str, str] = {}

        for instrument in instruments.itertuples(index=False):
            symbol = str(instrument.symbol)
            record = checkpoint["symbols"].get(symbol)
            record = self._reconcile_symbol(
                checkpoint, symbol, record, start_day, end_day
            )
            missing = self._missing_ranges(record, start_day, end_day)
            if self.max_request_days is not None:
                # One bounded edge per symbol per lifecycle keeps network and
                # temporary Parquet growth predictable; the next cycle resumes
                # from the checkpointed edge.
                missing = self._bound_missing_ranges(missing)[:1]
            if not missing:
                continue
            if attempted >= int(limit):
                break
            attempted += 1
            try:
                pieces = []
                for missing_start, missing_end in missing:
                    piece = self._request(
                        self.provider.get_research_history,
                        symbol,
                        missing_start,
                        missing_end,
                    )
                    self._validate_history(piece, symbol, missing_start, missing_end)
                    pieces.append(piece)
            except ProviderRequestError as exc:
                reason = (
                    "TRANSIENT_PROVIDER_FAILURE"
                    if isinstance(exc, TransientProviderError)
                    else "PERMANENT_PROVIDER_FAILURE"
                )
                failures[symbol] = reason
                checkpoint["failures"][symbol] = reason
                self._write_checkpoint(checkpoint)
                continue

            new_rows = sum(len(piece) for piece in pieces)
            if new_rows == 0:
                previous_start = (
                    _optional_checkpoint_date(record.get("start_date"))
                    if record is not None
                    else None
                )
                if (
                    record is not None
                    and previous_start is not None
                    and missing[0][0] == start_day
                    and missing[0][1] < previous_start
                ):
                    # A provider may legitimately return no rows for a
                    # calendar-only prefix (holiday, weekend, or pre-listing
                    # period).  Advance the checkpoint to the first verified
                    # session so a bounded forward batch is not starved by
                    # the same empty prefix on every lifecycle tick.
                    updated = dict(record)
                    updated["start_date"] = previous_start.isoformat()
                    updated["leading_gap_skipped"] = True
                    checkpoint["symbols"][symbol] = updated
                    checkpoint["failures"].pop(symbol, None)
                    self._write_checkpoint(checkpoint)
                    continue
                failures[symbol] = "NO_HISTORY"
                checkpoint["failures"][symbol] = "NO_HISTORY"
                self._write_checkpoint(checkpoint)
                continue
            try:
                frame = self._merge_with_previous(symbol, record, pieces)
                frame_start, frame_end = _frame_bounds(frame)
                self._validate_history(frame, symbol, frame_start, frame_end)
            except ProviderRequestError:
                failures[symbol] = "PERMANENT_PROVIDER_FAILURE"
                checkpoint["failures"][symbol] = "PERMANENT_PROVIDER_FAILURE"
                self._write_checkpoint(checkpoint)
                continue

            # Storage, manifest, lock, path and checkpoint failures must escape;
            # reporting them as provider failures would hide a local integrity issue.
            artifact = self.store.replace_dataset(
                ResearchDataset.RESEARCH_RETURNS,
                symbol,
                frame,
                _artifact_version(frame, frame_start, frame_end),
            )
            updated_record = self._symbol_record(frame, artifact, frame_start, frame_end)
            if record is not None and record.get("leading_gap_skipped") is True:
                updated_record["leading_gap_skipped"] = True
            checkpoint["symbols"][symbol] = updated_record
            checkpoint["failures"].pop(symbol, None)
            self._write_checkpoint(checkpoint)
            symbols_updated += 1
            rows_written += new_rows

        return BackfillResult(symbols_updated, rows_written, failures)

    def coverage(self) -> HistoricalCoverage:
        checkpoint = self._load_checkpoint()
        target = checkpoint.get("target", {})
        start_day = _optional_checkpoint_date(target.get("start_date")) or date.min
        end_day = _optional_checkpoint_date(target.get("end_date")) or start_day
        records = checkpoint.get("symbols", {})
        if not isinstance(records, dict):
            raise CheckpointIntegrityError("历史回填检查点 symbols 无效")

        symbols = tuple(sorted(str(symbol) for symbol in records))
        active_artifacts = self.store.active_artifacts(ResearchDataset.RESEARCH_RETURNS)
        for symbol, record in records.items():
            artifact = active_artifacts.get(str(symbol))
            if (
                artifact is None
                or artifact.sha256 != record.get("artifact_sha256")
                or artifact.data_version != record.get("artifact_data_version")
                or artifact.size_bytes != record.get("size_bytes")
            ):
                raise CheckpointIntegrityError(
                    f"历史回填检查点引用的产物校验失败: {symbol}"
                )
        row_count = sum(_nonnegative_int(record, "rows") for record in records.values())
        session_count = max(
            (_nonnegative_int(record, "sessions") for record in records.values()),
            default=0,
        )
        earliest_values = [
            value
            for record in records.values()
            if (value := _optional_checkpoint_date(record.get("earliest_date"))) is not None
        ]
        latest_values = [
            value
            for record in records.values()
            if (value := _optional_checkpoint_date(record.get("latest_date"))) is not None
        ]
        unavailable = {
            str(symbol): tuple(str(item) for item in record.get("unavailable_status_fields", []))
            for symbol, record in records.items()
            if record.get("unavailable_status_fields")
        }
        exclusions = {
            str(symbol): tuple(str(item) for item in record.get("exclusion_reasons", []))
            for symbol, record in records.items()
            if record.get("exclusion_reasons")
        }
        failures = checkpoint.get("failures", {})
        if isinstance(failures, dict):
            for symbol, reason in failures.items():
                exclusions.setdefault(str(symbol), (str(reason),))
        return HistoricalCoverage(
            dataset=ResearchDataset.RESEARCH_RETURNS.value,
            start_date=start_day,
            end_date=end_day,
            symbol_count=len(symbols),
            session_count=session_count,
            row_count=row_count,
            symbols=symbols,
            earliest_date=min(earliest_values) if earliest_values else None,
            latest_date=max(latest_values) if latest_values else None,
            unavailable_status_fields=unavailable,
            size_bytes=sum(
                _nonnegative_int(record, "size_bytes") for record in records.values()
            ),
            exclusion_reasons=exclusions,
        )

    def _ensure_download_capacity(self) -> None:
        self.store.policy.revalidate(self.data_root)
        self.store.policy.revalidate(self.checkpoint_path)
        free_bytes = int(self._disk_usage(self.data_root).free)
        if free_bytes < self.store.minimum_free_bytes:
            raise StorageQuotaError(
                "D盘剩余空间低于安全下限，已在调用数据源前停止历史回填"
            )

    def _request(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if self._last_request_completed and self.delay_seconds:
            self._sleeper(self.delay_seconds)
        for attempt in range(self.retry_count + 1):
            # Revalidate after rate/backoff sleeping and immediately before
            # every external attempt so a falling disk or replaced path closes
            # the gate before the provider can return more bytes.
            self._ensure_download_capacity()
            try:
                result = function(*args, **kwargs)
                self._last_request_completed = True
                return result
            except Exception as exc:
                if not _is_transient_provider_failure(exc):
                    raise
                if attempt >= self.retry_count:
                    raise TransientProviderError(
                        "provider transport retries exhausted"
                    ) from exc
                self._sleeper(self.delay_seconds * (2**attempt))
        raise RuntimeError("unreachable")

    @staticmethod
    def _validate_instruments(frame: Any) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame):
            raise ProviderRequestError("research instrument response must be a DataFrame")
        required = {"symbol", "name", "listed_date", "delisted_date", "status"}
        if not required.issubset(frame.columns):
            raise ProviderRequestError("research instrument response is missing required fields")
        normalized = frame.copy()
        normalized["symbol"] = normalized["symbol"].astype(str)
        if normalized["symbol"].duplicated().any():
            raise ProviderRequestError("research instrument response contains duplicate symbols")
        # Do not filter status=0: inactive and delisted securities are required
        # to avoid survivorship bias in the historical research universe.
        return normalized.sort_values("symbol", kind="stable").reset_index(drop=True)

    def _persist_instrument_history(self, frame: pd.DataFrame, as_of: date) -> None:
        version = f"instrument-history:{as_of.isoformat()}:{_frame_digest(frame)}"
        self.store.replace_dataset(
            ResearchDataset.INSTRUMENT_HISTORY,
            f"asof-{as_of.strftime('%Y%m%d')}",
            frame,
            version,
        )

    def _reconcile_symbol(
        self,
        checkpoint: dict[str, Any],
        symbol: str,
        record: Mapping[str, Any] | None,
        start: date,
        end: date,
    ) -> Mapping[str, Any] | None:
        """Repair only the known publish-before-checkpoint crash window."""

        try:
            active = self.store.active_artifact(
                ResearchDataset.RESEARCH_RETURNS, symbol
            )
        except KeyError:
            if record is None:
                return None
            raise CheckpointIntegrityError(
                f"历史回填检查点引用的产物不存在: {symbol}"
            ) from None

        if record is not None and active.sha256 == record.get("artifact_sha256"):
            self._verified_artifact(symbol, record)
            return record

        if not self.store.verify(active):
            raise CheckpointIntegrityError(f"历史回填活跃产物校验失败: {symbol}")
        frame = pd.read_parquet(active.path)
        try:
            frame_start, frame_end = _frame_bounds(frame)
            self._validate_history(frame, symbol, frame_start, frame_end)
        except ProviderRequestError as exc:
            raise CheckpointIntegrityError(
                f"历史回填活跃产物不符合目标: {symbol}"
            ) from exc
        expected_version = _artifact_version(frame, frame_start, frame_end)
        if active.data_version != expected_version:
            raise CheckpointIntegrityError(
                f"历史回填活跃产物目标或版本不匹配: {symbol}"
            )

        repaired = self._symbol_record(frame, active, start, end)
        checkpoint["symbols"][symbol] = repaired
        checkpoint["failures"].pop(symbol, None)
        self._write_checkpoint(checkpoint)
        return repaired

    def _merge_with_previous(
        self,
        symbol: str,
        record: Mapping[str, Any] | None,
        pieces: list[pd.DataFrame],
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        if record is not None:
            artifact = self._verified_artifact(symbol, record)
            frames.append(pd.read_parquet(artifact.path))
        frames.extend(pieces)
        frame = pd.concat(frames, ignore_index=True)
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        return (
            frame.sort_values("date", kind="stable")
            .drop_duplicates(subset=["symbol", "date"], keep="last")
            .reset_index(drop=True)
        )

    def _verified_artifact(self, symbol: str, record: Mapping[str, Any]):
        try:
            artifact = self.store.active_artifact(
                ResearchDataset.RESEARCH_RETURNS, symbol
            )
        except KeyError as exc:
            raise CheckpointIntegrityError(
                f"历史回填检查点引用的产物不存在: {symbol}"
            ) from exc
        if (
            artifact.sha256 != record.get("artifact_sha256")
            or artifact.data_version != record.get("artifact_data_version")
            or not self.store.verify(artifact)
        ):
            raise CheckpointIntegrityError(
                f"历史回填检查点引用的产物校验失败: {symbol}"
            )
        return artifact

    @staticmethod
    def _validate_history(frame: pd.DataFrame, symbol: str, start: date, end: date) -> None:
        required = {
            "symbol",
            "date",
            "trade_status",
            "research_usable",
            "unusable_reason",
        }
        if not required.issubset(frame.columns):
            raise ProviderRequestError("research history is missing required fields")
        if frame.empty:
            return
        if set(frame["symbol"].astype(str)) != {symbol}:
            raise ProviderRequestError("research history symbol mismatch")
        dates = pd.to_datetime(frame["date"], errors="raise").dt.date
        if dates.min() < start or dates.max() > end or dates.duplicated().any():
            raise ProviderRequestError("research history date range is invalid")
    @staticmethod
    def _symbol_record(frame, artifact, start: date, end: date) -> dict[str, Any]:
        dates = pd.to_datetime(frame["date"], errors="raise").dt.date
        unavailable = [
            field
            for field in ("trade_status", "is_st", "tradable")
            if field not in frame.columns
            or frame[field].isna().any()
            or frame[field].astype(str).str.strip().eq("").any()
        ]
        reasons = sorted(
            {
                str(reason).strip()
                for reason in frame.loc[~frame["research_usable"].astype(bool), "unusable_reason"]
                if str(reason).strip()
            }
        )
        return {
            "start_date": dates.min().isoformat(),
            "end_date": dates.max().isoformat(),
            "rows": len(frame),
            "sessions": int(dates.nunique()),
            "earliest_date": dates.min().isoformat(),
            "latest_date": dates.max().isoformat(),
            "unavailable_status_fields": unavailable,
            "exclusion_reasons": reasons,
            "size_bytes": artifact.size_bytes,
            "artifact_sha256": artifact.sha256,
            "artifact_data_version": artifact.data_version,
        }

    @staticmethod
    def _missing_ranges(
        record: Mapping[str, Any] | None, start: date, end: date
    ) -> list[tuple[date, date]]:
        if record is None:
            return [(start, end)]
        previous_start = _optional_checkpoint_date(record.get("start_date"))
        previous_end = _optional_checkpoint_date(record.get("end_date"))
        if previous_start is None or previous_end is None:
            raise CheckpointIntegrityError("历史回填检查点日期无效")
        missing: list[tuple[date, date]] = []
        if start < previous_start and record.get("leading_gap_skipped") is not True:
            missing.append((start, previous_start.fromordinal(previous_start.toordinal() - 1)))
        if end > previous_end:
            missing.append((previous_end.fromordinal(previous_end.toordinal() + 1), end))
        return missing

    def _bound_missing_ranges(
        self, ranges: list[tuple[date, date]]
    ) -> list[tuple[date, date]]:
        """Split provider requests so one lifecycle never downloads years at once."""

        if self.max_request_days is None:
            return ranges
        bounded: list[tuple[date, date]] = []
        span = timedelta(days=self.max_request_days - 1)
        for start, end in ranges:
            cursor = start
            while cursor <= end:
                chunk_end = min(end, cursor + span)
                bounded.append((cursor, chunk_end))
                cursor = chunk_end + timedelta(days=1)
        return bounded

    def _load_checkpoint(self) -> dict[str, Any]:
        self.store.policy.revalidate(self.checkpoint_path)
        if not self.checkpoint_path.exists():
            return {
                "format_version": self.CHECKPOINT_FORMAT_VERSION,
                "target": {},
                "symbols": {},
                "failures": {},
            }
        try:
            self.store.policy.revalidate(self.checkpoint_path)
            raw = self.checkpoint_path.read_bytes()
            if not raw or len(raw) > self.CHECKPOINT_MAX_BYTES:
                raise CheckpointIntegrityError("历史回填检查点大小无效")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise CheckpointIntegrityError("历史回填检查点格式无效")
            digest = payload.pop("sha256", None)
            if payload.get("format_version") != self.CHECKPOINT_FORMAT_VERSION:
                raise CheckpointIntegrityError("历史回填检查点版本无效")
            if digest != _payload_digest(payload):
                raise CheckpointIntegrityError("历史回填检查点摘要校验失败")
            return payload
        except CheckpointIntegrityError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise CheckpointIntegrityError("历史回填检查点无法读取") from exc

    def _write_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        checkpoint["updated_at"] = _aware_utc(self._clock()).isoformat()
        body = json.loads(json.dumps(checkpoint, ensure_ascii=False))
        body.pop("sha256", None)
        artifact = {**body, "sha256": _payload_digest(body)}
        payload = json.dumps(
            artifact, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8")
        directory = self.store.policy.authorize(self.checkpoint_path.parent)
        self.store.policy.revalidate(directory)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = self.store.policy.authorize(
            directory / f".historical-backfill-{uuid4().hex}.tmp"
        )
        try:
            self.store.policy.revalidate(temporary)
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self.store.policy.revalidate(temporary)
            self.store.policy.revalidate(self.checkpoint_path)
            os.replace(temporary, self.checkpoint_path)
            self.store.policy.revalidate(directory)
            self._directory_fsync(directory)
        finally:
            self.store.policy.revalidate(temporary)
            temporary.unlink(missing_ok=True)


def _frame_bounds(frame: pd.DataFrame) -> tuple[date, date]:
    """Return the actual verified bounds, not the caller's requested window."""

    if not isinstance(frame, pd.DataFrame) or frame.empty or "date" not in frame:
        raise ProviderRequestError("research history has no date bounds")
    dates = pd.to_datetime(frame["date"], errors="raise").dt.date
    return dates.min(), dates.max()


_TRANSPORT_FAILURES = (TimeoutError, ConnectionError, OSError)


def _is_transient_provider_failure(exc: Exception) -> bool:
    if isinstance(exc, _TRANSPORT_FAILURES):
        return True
    if not isinstance(exc, ProviderRequestError):
        return False
    cause = exc.__cause__
    while cause is not None:
        if isinstance(cause, _TRANSPORT_FAILURES):
            return True
        cause = cause.__cause__
    return False


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability on platforms that expose it."""

    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        if os.name != "nt":
            raise


def _as_date(value: date | str, field: str) -> date:
    if isinstance(value, datetime):
        raise ValueError(f"{field} must be a date without time")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc


def _optional_checkpoint_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CheckpointIntegrityError("历史回填检查点日期无效") from exc


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _payload_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frame_digest(frame: pd.DataFrame) -> str:
    canonical = frame.copy()
    canonical = canonical.reindex(sorted(canonical.columns), axis=1)
    values = pd.util.hash_pandas_object(canonical.astype(str), index=True).values.tobytes()
    return hashlib.sha256(values).hexdigest()


def _artifact_version(frame: pd.DataFrame, start: date, end: date) -> str:
    return f"research-history:{start.isoformat()}:{end.isoformat()}:{_frame_digest(frame)}"


def _nonnegative_int(record: Any, field: str) -> int:
    if not isinstance(record, dict):
        raise CheckpointIntegrityError("历史回填检查点 symbol 记录无效")
    value = record.get(field, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CheckpointIntegrityError(f"历史回填检查点 {field} 无效")
    return value


__all__ = [
    "BackfillResult",
    "CheckpointIntegrityError",
    "HistoricalBackfillCoordinator",
    "HistoricalCoverage",
    "TransientProviderError",
]
