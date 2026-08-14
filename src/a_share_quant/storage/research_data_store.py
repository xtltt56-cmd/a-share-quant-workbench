"""Atomic, content-addressed storage for governed research datasets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq

from a_share_quant.research.history_contracts import ResearchArtifact
from a_share_quant.storage.project_storage import ProjectStoragePolicy, StorageBoundaryError


class StorageQuotaError(RuntimeError):
    """Raised before publication when a governed storage limit is exceeded."""


class ManifestIntegrityError(RuntimeError):
    """Raised when manifest or referenced immutable content is not trustworthy."""


class ResearchDataset(str, Enum):
    INSTRUMENT_HISTORY = "instrument_history"
    POINT_IN_TIME_UNIVERSE = "point_in_time_universe"
    RESEARCH_RETURNS = "research_returns"
    CORPORATE_ACTIONS = "corporate_actions"
    FROZEN_SNAPSHOTS = "frozen_snapshots"
    TRIAL_EVIDENCE = "trial_evidence"


@dataclass(frozen=True)
class CleanupReport:
    candidates: tuple[Path, ...]
    removed: tuple[Path, ...]
    dry_run: bool


_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_STAGE_PATTERN = re.compile(r"\.research-stage-[0-9a-f]{32}\.parquet\Z")
_MANIFEST_TEMP_PATTERN = re.compile(r"\.manifest-[0-9a-f]{32}\.tmp\Z")
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}


class ResearchDataStore:
    """Publish immutable Parquet blobs and atomically replace a JSONL manifest."""

    DEFAULT_MAXIMUM_SINGLE_FILE_BYTES = 536_870_912
    DEFAULT_MAXIMUM_RESEARCH_DATA_BYTES = 21_474_836_480
    DEFAULT_MINIMUM_FREE_BYTES = 21_474_836_480
    MANIFEST_SCHEMA = "a-share-quant.research-artifact"
    MANIFEST_VERSION = 1

    def __init__(
        self,
        policy: ProjectStoragePolicy,
        *,
        maximum_single_file_bytes: int = DEFAULT_MAXIMUM_SINGLE_FILE_BYTES,
        maximum_research_data_bytes: int = DEFAULT_MAXIMUM_RESEARCH_DATA_BYTES,
        minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES,
        disk_usage: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
        parquet_writer: Callable[[pd.DataFrame, Path], None] | None = None,
        file_fsync: Callable[[Path], None] | None = None,
        blob_replace: Callable[[Path, Path], None] = os.replace,
        manifest_fsync: Callable[[Path], None] | None = None,
        manifest_replace: Callable[[Path, Path], None] = os.replace,
        orphan_grace_seconds: float = 3600,
    ) -> None:
        if min(
            maximum_single_file_bytes,
            maximum_research_data_bytes,
            minimum_free_bytes,
            orphan_grace_seconds,
        ) < 0:
            raise ValueError("storage limits must be non-negative")
        self.policy = policy
        self.maximum_single_file_bytes = maximum_single_file_bytes
        self.maximum_research_data_bytes = maximum_research_data_bytes
        self.minimum_free_bytes = minimum_free_bytes
        self._disk_usage = disk_usage
        self._parquet_writer = parquet_writer or self._write_parquet
        self._file_fsync = file_fsync or self._fsync_path
        self._blob_replace = blob_replace
        self._manifest_fsync = manifest_fsync or self._fsync_path
        self._manifest_replace = manifest_replace
        self.orphan_grace_seconds = orphan_grace_seconds

        self.root_directory = policy.authorize("data/research")
        self.blob_directory = policy.authorize("data/research/blobs")
        self.staging_directory = self.blob_directory
        self.manifest_path = policy.authorize("data/research/manifest.jsonl")
        self.lock_path = policy.authorize("data/research/research.lock")
        self._prepare_owned_directories()
        with _PROCESS_LOCKS_GUARD:
            key = os.path.normcase(os.fspath(self.lock_path))
            self._thread_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())

    def replace_dataset(
        self,
        dataset: str | ResearchDataset,
        key: str,
        frame: pd.DataFrame,
        data_version: str,
    ) -> ResearchArtifact:
        dataset_value = self._validate_dataset(dataset)
        self._validate_key(key)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame must be a pandas DataFrame")
        if not data_version or len(data_version) > 256 or any(c in data_version for c in "\r\n\0"):
            raise ValueError("data_version is invalid")

        with self._locked():
            records = self._load_records()
            stage = self.policy.authorize(
                self.blob_directory / f".research-stage-{uuid4().hex}.parquet"
            )
            try:
                self.policy.revalidate(stage)
                self._parquet_writer(frame, stage)
                self.policy.revalidate(stage)
                self._file_fsync(stage)
                size_bytes = stage.stat().st_size
                digest = self._sha256(stage)
                self.policy.revalidate(stage)
                parquet_file = pq.ParquetFile(stage)
                try:
                    row_count = parquet_file.metadata.num_rows
                    schema_bytes = parquet_file.schema_arrow.serialize().to_pybytes()
                finally:
                    parquet_file.close()
                schema_fingerprint = hashlib.sha256(schema_bytes).hexdigest()
                self._enforce_limits(size_bytes, digest)

                existing = self._find_idempotent(records, dataset_value, key, data_version, digest)
                if existing is not None:
                    return self._artifact_from_record(existing)

                blob = self.policy.authorize(self.blob_directory / f"{digest}.parquet")
                if not blob.exists():
                    self.policy.revalidate(stage)
                    self.policy.revalidate(blob)
                    self._blob_replace(stage, blob)
                    self.policy.revalidate(blob)
                    self._file_fsync(blob)
                else:
                    self._validate_blob(blob, digest, size_bytes)

                created_at = datetime.now(UTC)
                record_id = hashlib.sha256(
                    f"{dataset_value}\0{key}\0{data_version}\0{digest}".encode()
                ).hexdigest()
                record = {
                    "blob_path": f"blobs/{digest}.parquet",
                    "created_at": created_at.isoformat().replace("+00:00", "Z"),
                    "data_version": data_version,
                    "dataset": dataset_value,
                    "key": key,
                    "manifest_schema": self.MANIFEST_SCHEMA,
                    "manifest_version": self.MANIFEST_VERSION,
                    "record_id": record_id,
                    "row_count": row_count,
                    "schema_fingerprint": schema_fingerprint,
                    "sha256": digest,
                    "size_bytes": size_bytes,
                }
                self._publish_manifest([*records, record])
                return self._artifact_from_record(record)
            finally:
                self._safe_unlink_stage(stage)

    def active_artifact(self, dataset: str | ResearchDataset, key: str) -> ResearchArtifact:
        dataset_value = self._validate_dataset(dataset)
        self._validate_key(key)
        with self._locked():
            matches = [
                record
                for record in self._load_records()
                if record["dataset"] == dataset_value and record["key"] == key
            ]
            if not matches:
                raise KeyError(f"no active research artifact for {dataset_value}/{key}")
            return self._artifact_from_record(matches[-1])

    def verify(self, artifact: ResearchArtifact) -> bool:
        try:
            dataset_value = self._validate_dataset(artifact.dataset)
            self._validate_key(artifact.key)
            with self._locked():
                records = self._load_records(verify_blobs=False)
                expected = self.policy.authorize(self.blob_directory / f"{artifact.sha256}.parquet")
                if artifact.path != expected:
                    return False
                matching = any(
                    record["dataset"] == dataset_value
                    and record["key"] == artifact.key
                    and record["data_version"] == artifact.data_version
                    and record["sha256"] == artifact.sha256
                    for record in records
                )
                if not matching:
                    return False
                self._validate_blob(artifact.path, artifact.sha256, artifact.size_bytes)
                return True
        except (OSError, StorageBoundaryError, ManifestIntegrityError, ValueError):
            return False

    def manifest_count(self, sha256: str | None = None) -> int:
        with self._locked():
            records = self._load_records()
            if sha256 is None:
                return len(records)
            return sum(record["sha256"] == sha256 for record in records)

    def cleanup_rebuildable_temporary_files(
        self, *, grace_seconds: float | None = None, dry_run: bool = False
    ) -> CleanupReport:
        grace = self.orphan_grace_seconds if grace_seconds is None else grace_seconds
        if grace < 0:
            raise ValueError("grace_seconds must be non-negative")
        with self._locked():
            records = self._load_records()
            referenced = {record["sha256"] for record in records}
            cutoff = time.time() - grace
            candidates: list[Path] = []
            for directory in (self.blob_directory, self.root_directory):
                self.policy.revalidate(directory)
                for path in directory.iterdir():
                    self.policy.revalidate(path)
                    if not path.is_file() or path.stat().st_mtime > cutoff:
                        continue
                    is_stage = (
                        directory == self.blob_directory
                        and _STAGE_PATTERN.fullmatch(path.name)
                    )
                    is_manifest_temp = (
                        directory == self.root_directory
                        and _MANIFEST_TEMP_PATTERN.fullmatch(path.name)
                    )
                    match = (
                        _DIGEST_PATTERN.fullmatch(path.stem)
                        if path.suffix == ".parquet"
                        else None
                    )
                    is_orphan = (
                        directory == self.blob_directory
                        and match is not None
                        and path.stem not in referenced
                    )
                    if is_stage or is_manifest_temp or is_orphan:
                        candidates.append(self.policy.revalidate(path))
            candidates.sort(key=lambda path: os.fspath(path).casefold())
            removed: list[Path] = []
            if not dry_run:
                for path in candidates:
                    self.policy.revalidate(path)
                    path.unlink(missing_ok=True)
                    removed.append(path)
            return CleanupReport(tuple(candidates), tuple(removed), dry_run)

    def _prepare_owned_directories(self) -> None:
        for directory in (self.root_directory, self.blob_directory):
            self.policy.revalidate(directory)
            directory.mkdir(parents=True, exist_ok=True)
            self.policy.revalidate(directory)
            if not directory.is_dir():
                raise StorageBoundaryError("研究数据仓受控路径必须是目录")

    @staticmethod
    def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
        frame.to_parquet(path, index=False)

    @staticmethod
    def _fsync_path(path: Path) -> None:
        # Windows rejects FlushFileBuffers for a read-only CRT descriptor.
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())

    def _enforce_limits(self, new_size: int, digest: str) -> None:
        if new_size > self.maximum_single_file_bytes:
            raise StorageQuotaError("研究数据单文件超过配额上限")
        self.policy.revalidate(self.blob_directory)
        current = 0
        for path in self.blob_directory.glob("*.parquet"):
            if _DIGEST_PATTERN.fullmatch(path.stem):
                self.policy.revalidate(path)
                current += path.stat().st_size
        blob = self.blob_directory / f"{digest}.parquet"
        projected = current + (0 if blob.exists() else new_size)
        if projected > self.maximum_research_data_bytes:
            raise StorageQuotaError("研究数据总量超过配额")
        self.policy.revalidate(self.root_directory)
        if self._disk_usage(self.root_directory).free < self.minimum_free_bytes:
            raise StorageQuotaError("D盘空闲空间低于安全下限")

    def _load_records(self, *, verify_blobs: bool = True) -> list[dict[str, Any]]:
        self.policy.revalidate(self.manifest_path)
        if not self.manifest_path.exists():
            return []
        raw = self.manifest_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ManifestIntegrityError("manifest 截断或缺少终止换行")
        records: list[dict[str, Any]] = []
        record_ids: set[str] = set()
        for line_number, raw_line in enumerate(raw.splitlines(), start=1):
            try:
                record = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ManifestIntegrityError(f"manifest 第 {line_number} 行无效") from exc
            self._validate_record(record)
            record_id = record["record_id"]
            if record_id in record_ids:
                raise ManifestIntegrityError("manifest 含重复或冲突记录")
            record_ids.add(record_id)
            blob = self._blob_from_record(record)
            if verify_blobs:
                self._validate_blob(blob, record["sha256"], record["size_bytes"])
            records.append(record)
        return records

    def _validate_record(self, record: Any) -> None:
        required = {
            "blob_path",
            "created_at",
            "data_version",
            "dataset",
            "key",
            "manifest_schema",
            "manifest_version",
            "record_id",
            "row_count",
            "schema_fingerprint",
            "sha256",
            "size_bytes",
        }
        if not isinstance(record, dict) or set(record) != required:
            raise ManifestIntegrityError("manifest schema 字段无效")
        if record["manifest_schema"] != self.MANIFEST_SCHEMA or record["manifest_version"] != 1:
            raise ManifestIntegrityError("manifest schema/version 不受支持")
        if (
            not isinstance(record["data_version"], str)
            or not record["data_version"]
            or len(record["data_version"]) > 256
            or any(character in record["data_version"] for character in "\r\n\0")
        ):
            raise ManifestIntegrityError("manifest data_version 无效")
        try:
            self._validate_dataset(record["dataset"])
            self._validate_key(record["key"])
        except (ValueError, StorageBoundaryError) as exc:
            raise ManifestIntegrityError("manifest 逻辑标识无效") from exc
        for field in ("sha256", "schema_fingerprint", "record_id"):
            if not isinstance(record[field], str) or not _DIGEST_PATTERN.fullmatch(record[field]):
                raise ManifestIntegrityError(f"manifest {field} 无效")
        if not isinstance(record["row_count"], int) or record["row_count"] < 0:
            raise ManifestIntegrityError("manifest row_count 无效")
        if not isinstance(record["size_bytes"], int) or record["size_bytes"] < 0:
            raise ManifestIntegrityError("manifest size_bytes 无效")
        try:
            created_at = datetime.fromisoformat(record["created_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ManifestIntegrityError("manifest created_at 无效") from exc
        if created_at.utcoffset() != UTC.utcoffset(created_at):
            raise ManifestIntegrityError("manifest created_at 必须是 UTC")
        expected_id = hashlib.sha256(
            f"{record['dataset']}\0{record['key']}\0{record['data_version']}\0{record['sha256']}".encode()
        ).hexdigest()
        if record["record_id"] != expected_id:
            raise ManifestIntegrityError("manifest 记录摘要不匹配")
        expected_path = f"blobs/{record['sha256']}.parquet"
        if record["blob_path"] != expected_path:
            raise ManifestIntegrityError("manifest blob 路径越界或不规范")

    def _blob_from_record(self, record: dict[str, Any]) -> Path:
        candidate = self.policy.authorize(self.root_directory / record["blob_path"])
        expected = self.policy.authorize(self.blob_directory / f"{record['sha256']}.parquet")
        if candidate != expected:
            raise ManifestIntegrityError("manifest blob 路径越界")
        return candidate

    def _validate_blob(self, blob: Path, digest: str, size_bytes: int) -> None:
        try:
            self.policy.revalidate(blob)
            invalid = (
                not blob.is_file()
                or blob.stat().st_size != size_bytes
                or self._sha256(blob) != digest
            )
            if invalid:
                raise ManifestIntegrityError("研究数据 blob 摘要或大小不匹配")
        except OSError as exc:
            raise ManifestIntegrityError("研究数据 blob 不可读") from exc

    def _publish_manifest(self, records: Sequence[dict[str, Any]]) -> None:
        temp = self.policy.authorize(self.root_directory / f".manifest-{uuid4().hex}.tmp")
        payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for record in records
        ).encode("utf-8")
        try:
            self.policy.revalidate(temp)
            with temp.open("xb") as handle:
                handle.write(payload)
                handle.flush()
            self.policy.revalidate(temp)
            self._manifest_fsync(temp)
            self.policy.revalidate(temp)
            self.policy.revalidate(self.manifest_path)
            self._manifest_replace(temp, self.manifest_path)
            self.policy.revalidate(self.manifest_path)
        finally:
            self._safe_unlink_manifest_temp(temp)

    def _artifact_from_record(self, record: dict[str, Any]) -> ResearchArtifact:
        return ResearchArtifact(
            dataset=record["dataset"],
            key=record["key"],
            data_version=record["data_version"],
            sha256=record["sha256"],
            path=self._blob_from_record(record),
            row_count=record["row_count"],
            size_bytes=record["size_bytes"],
            created_at=datetime.fromisoformat(record["created_at"].replace("Z", "+00:00")),
            schema_fingerprint=record["schema_fingerprint"],
        )

    @staticmethod
    def _find_idempotent(
        records: Sequence[dict[str, Any]], dataset: str, key: str, data_version: str, digest: str
    ) -> dict[str, Any] | None:
        return next(
            (
                record
                for record in records
                if record["dataset"] == dataset
                and record["key"] == key
                and record["data_version"] == data_version
                and record["sha256"] == digest
            ),
            None,
        )

    def _validate_dataset(self, dataset: str | ResearchDataset) -> str:
        try:
            return ResearchDataset(dataset).value
        except (TypeError, ValueError) as exc:
            raise ValueError("dataset is not in the governed allowlist") from exc

    def _validate_key(self, key: str) -> None:
        if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
            raise ValueError("logical key is not path safe")
        self.policy.authorize(self.root_directory / "logical-key-validation" / key)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _safe_unlink_stage(self, path: Path) -> None:
        if _STAGE_PATTERN.fullmatch(path.name) and path.parent == self.blob_directory:
            self.policy.revalidate(path)
            path.unlink(missing_ok=True)

    def _safe_unlink_manifest_temp(self, path: Path) -> None:
        if _MANIFEST_TEMP_PATTERN.fullmatch(path.name) and path.parent == self.root_directory:
            self.policy.revalidate(path)
            path.unlink(missing_ok=True)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock:
            self.policy.revalidate(self.lock_path)
            with self.lock_path.open("a+b") as handle:
                self.policy.revalidate(self.lock_path)
                _lock_file(handle)
                try:
                    yield
                finally:
                    _unlock_file(handle)


def _lock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
