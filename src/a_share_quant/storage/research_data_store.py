"""Atomic, content-addressed storage for governed research datasets."""

from __future__ import annotations

import errno
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


class ManifestConflictError(ManifestIntegrityError):
    """Raised when one logical dataset version maps to different content."""


class StorageLockTimeoutError(TimeoutError):
    """Raised when the governed storage lock cannot be acquired in time."""


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
    rejected: tuple[Path, ...] = ()


_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_STAGE_PATTERN = re.compile(r"\.research-stage-[0-9a-f]{32}\.parquet\Z")
_MANIFEST_TEMP_PATTERN = re.compile(r"\.manifest-[0-9a-f]{32}\.tmp\Z")
_MARKER_PATTERN = re.compile(r"([0-9a-f]{32})\.owned\.json\Z")
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
        directory_fsync: Callable[[Path], None] | None = None,
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
        self._directory_fsync = directory_fsync or self._fsync_directory
        self.orphan_grace_seconds = orphan_grace_seconds

        self.root_directory = policy.authorize("data/research")
        self.blob_directory = policy.authorize("data/research/blobs")
        self.staging_directory = self.blob_directory
        self.manifest_path = policy.authorize("data/research/manifest.jsonl")
        self.lock_path = policy.authorize("data/research/research.lock")
        self.ownership_directory = policy.authorize("data/research/ownership")
        self._prepare_owned_directories()
        self._prepare_lock_sentinel()
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
            stage_marker = self._create_owned_marker("stage", stage)
            blob_marker: Path | None = None
            manifest_published = False
            try:
                self.policy.revalidate(stage)
                self._parquet_writer(frame, stage)
                self.policy.revalidate(stage)
                self._file_fsync(stage)
                self.policy.revalidate(stage)
                size_bytes = stage.stat().st_size
                self.policy.revalidate(stage)
                digest = self._sha256(stage)
                self._seal_owned_marker(stage_marker, stage)
                self.policy.revalidate(stage)
                parquet_file = pq.ParquetFile(stage)
                try:
                    row_count = parquet_file.metadata.num_rows
                    schema_bytes = parquet_file.schema_arrow.serialize().to_pybytes()
                finally:
                    parquet_file.close()
                schema_fingerprint = hashlib.sha256(schema_bytes).hexdigest()

                existing = self._find_idempotent(records, dataset_value, key, data_version, digest)
                if existing is not None:
                    self._enforce_limits(size_bytes, digest, 0)
                    return self._artifact_from_record(existing)
                if any(
                    record["dataset"] == dataset_value
                    and record["key"] == key
                    and record["data_version"] == data_version
                    for record in records
                ):
                    raise ManifestConflictError(
                        "同一逻辑 dataset/key/data_version 不能指向不同内容"
                    )

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
                record["record_sha256"] = self._canonical_record_sha256(record)
                peak_reserve = self._manifest_peak_reserve(records, record)
                self._enforce_limits(size_bytes, digest, peak_reserve)

                blob = self.policy.authorize(self.blob_directory / f"{digest}.parquet")
                self.policy.revalidate(blob)
                if not blob.exists():
                    blob_marker = self._create_owned_marker("orphan_blob", blob, digest=digest)
                    self.policy.revalidate(stage)
                    self.policy.revalidate(blob)
                    self._blob_replace(stage, blob)
                    self.policy.revalidate(self.blob_directory)
                    self._directory_fsync(self.blob_directory)
                    self.policy.revalidate(blob)
                    self._file_fsync(blob)
                    if blob_marker is None:
                        raise ManifestIntegrityError("blob ownership marker is missing")
                    self._seal_owned_marker(blob_marker, blob)
                else:
                    self._validate_blob(
                        blob, digest, size_bytes, row_count, schema_fingerprint
                    )

                self._publish_manifest([*records, record])
                manifest_published = True
                return self._artifact_from_record(record)
            finally:
                self._safe_unlink_stage(stage)
                self._remove_owned_marker(stage_marker)
                if manifest_published and blob_marker is not None:
                    self._remove_owned_marker(blob_marker)

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
                self._validate_blob(
                    artifact.path,
                    artifact.sha256,
                    artifact.size_bytes,
                    artifact.row_count,
                    artifact.schema_fingerprint,
                )
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
            marker_targets: list[tuple[Path, Path, bool]] = []
            rejected: list[Path] = []
            self.policy.revalidate(self.ownership_directory)
            for marker in self.ownership_directory.iterdir():
                try:
                    self.policy.revalidate(marker)
                    if not marker.is_file():
                        continue
                    self.policy.revalidate(marker)
                    if marker.stat().st_mtime > cutoff:
                        continue
                    ownership = self._read_owned_marker(marker)
                    if ownership["state"] != "sealed":
                        raise ManifestIntegrityError(
                            "allocated ownership marker is not cleanup-authorized"
                        )
                    target = self._owned_marker_target(ownership)
                    is_referenced = (
                        ownership["kind"] == "orphan_blob"
                        and ownership["digest"] in referenced
                    )
                    self.policy.revalidate(target)
                    if target.exists():
                        self._validate_owned_target(ownership, target)
                        if not is_referenced:
                            candidates.append(target)
                    marker_targets.append((marker, target, is_referenced))
                except (OSError, StorageBoundaryError, ManifestIntegrityError, ValueError):
                    rejected.append(marker)
            candidates.sort(key=lambda path: os.fspath(path).casefold())
            removed: list[Path] = []
            if not dry_run:
                candidate_set = set(candidates)
                for marker, target, _is_referenced in marker_targets:
                    if target in candidate_set:
                        self.policy.revalidate(target)
                        target.unlink(missing_ok=True)
                        removed.append(target)
                    self._remove_owned_marker(marker)
            return CleanupReport(
                tuple(candidates), tuple(removed), dry_run, tuple(rejected)
            )

    def _validate_owned_target(self, ownership: dict[str, Any], target: Path) -> None:
        self.policy.revalidate(target)
        if not target.is_file():
            raise ManifestIntegrityError("owned cleanup target is not a regular file")
        self.policy.revalidate(target)
        actual_size = target.stat().st_size
        self.policy.revalidate(target)
        actual_sha256 = self._sha256(target)
        if (
            actual_size != ownership["target_size_bytes"]
            or actual_sha256 != ownership["target_sha256"]
        ):
            raise ManifestIntegrityError(
                "owned cleanup target hash or size no longer matches sealed marker"
            )

    def _prepare_owned_directories(self) -> None:
        for directory in (
            self.root_directory,
            self.blob_directory,
            self.ownership_directory,
        ):
            self.policy.revalidate(directory)
            directory.mkdir(parents=True, exist_ok=True)
            self.policy.revalidate(directory)
            if not directory.is_dir():
                raise StorageBoundaryError("研究数据仓受控路径必须是目录")

    def _prepare_lock_sentinel(self) -> None:
        """Create byte zero without reading or touching an already locked byte."""

        self.policy.revalidate(self.lock_path)
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                try:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                except PermissionError:
                    # A first-time peer initialized and locked byte zero between
                    # fstat and write. Its successful write is the sentinel.
                    pass
            deadline = time.monotonic() + 5
            while os.fstat(descriptor).st_size < 1:
                if time.monotonic() >= deadline:
                    raise TimeoutError("research lock sentinel initialization timed out")
                time.sleep(0.01)
        finally:
            os.close(descriptor)

    @staticmethod
    def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
        frame.to_parquet(path, index=False)

    @staticmethod
    def _fsync_path(path: Path) -> None:
        # Windows rejects FlushFileBuffers for a read-only CRT descriptor.
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        """Best-effort directory durability; Windows does not expose POSIX dir fsync."""

        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            if os.name != "nt":
                raise

    def _enforce_limits(
        self, new_size: int, digest: str, additional_peak_bytes: int = 0
    ) -> None:
        if new_size > self.maximum_single_file_bytes:
            raise StorageQuotaError("研究数据单文件超过配额上限")
        # The freshly written stage already occupies new_size. Counting the
        # complete tree avoids double-counting it and includes stranded/foreign
        # files that still consume the governed volume.
        if (
            self._tree_size(self.root_directory) + additional_peak_bytes
            > self.maximum_research_data_bytes
        ):
            raise StorageQuotaError("研究数据总量超过配额")
        self.policy.revalidate(self.root_directory)
        if self._disk_usage(self.root_directory).free < self.minimum_free_bytes:
            raise StorageQuotaError("D盘空闲空间低于安全下限")

    @staticmethod
    def _manifest_peak_reserve(
        records: Sequence[dict[str, Any]], new_record: dict[str, Any]
    ) -> int:
        def encoded_size(items: Sequence[dict[str, Any]]) -> int:
            return sum(
                len(
                    (
                        json.dumps(
                            item,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                for item in items
            )

        # New/rollback manifest files plus bounded marker records and their
        # atomic marker temps. This is deliberately conservative and is checked
        # before an immutable blob can be published.
        return encoded_size([*records, new_record]) + encoded_size(records) + 16_384

    def _tree_size(self, directory: Path) -> int:
        self.policy.revalidate(directory)
        total = 0
        self.policy.revalidate(directory)
        for path in directory.iterdir():
            self.policy.revalidate(path)
            if path.is_dir():
                total += self._tree_size(path)
            else:
                self.policy.revalidate(path)
                if path.is_file():
                    self.policy.revalidate(path)
                    total += path.stat().st_size
        return total

    def _load_records(self, *, verify_blobs: bool = True) -> list[dict[str, Any]]:
        self.policy.revalidate(self.manifest_path)
        if not self.manifest_path.exists():
            return []
        self.policy.revalidate(self.manifest_path)
        raw = self.manifest_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ManifestIntegrityError("manifest 截断或缺少终止换行")
        records: list[dict[str, Any]] = []
        record_ids: set[str] = set()
        logical_versions: dict[tuple[str, str, str], str] = {}
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
            logical_version = (record["dataset"], record["key"], record["data_version"])
            prior_digest = logical_versions.get(logical_version)
            if prior_digest is not None:
                raise ManifestConflictError(
                    "manifest 同一逻辑 dataset/key/data_version 存在重复或冲突"
                )
            logical_versions[logical_version] = record["sha256"]
            blob = self._blob_from_record(record)
            if verify_blobs:
                self._validate_blob(
                    blob,
                    record["sha256"],
                    record["size_bytes"],
                    record["row_count"],
                    record["schema_fingerprint"],
                )
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
            "record_sha256",
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
        for field in ("sha256", "schema_fingerprint", "record_id", "record_sha256"):
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
        if record["record_sha256"] != self._canonical_record_sha256(record):
            raise ManifestIntegrityError("manifest record 摘要不匹配")

    def _blob_from_record(self, record: dict[str, Any]) -> Path:
        candidate = self.policy.authorize(self.root_directory / record["blob_path"])
        expected = self.policy.authorize(self.blob_directory / f"{record['sha256']}.parquet")
        if candidate != expected:
            raise ManifestIntegrityError("manifest blob 路径越界")
        return candidate

    def _validate_blob(
        self,
        blob: Path,
        digest: str,
        size_bytes: int,
        row_count: int,
        schema_fingerprint: str,
    ) -> None:
        try:
            self.policy.revalidate(blob)
            if not blob.is_file():
                raise ManifestIntegrityError("研究数据 blob 不是普通文件")
            self.policy.revalidate(blob)
            actual_size = blob.stat().st_size
            self.policy.revalidate(blob)
            actual_digest = self._sha256(blob)
            self.policy.revalidate(blob)
            parquet_file = pq.ParquetFile(blob)
            try:
                actual_rows = parquet_file.metadata.num_rows
                schema_bytes = parquet_file.schema_arrow.serialize().to_pybytes()
            finally:
                parquet_file.close()
            actual_schema = hashlib.sha256(schema_bytes).hexdigest()
            invalid = (
                actual_size != size_bytes
                or actual_digest != digest
                or actual_rows != row_count
                or actual_schema != schema_fingerprint
            )
            if invalid:
                raise ManifestIntegrityError("研究数据 blob 摘要或大小不匹配")
        except (OSError, ValueError) as exc:
            raise ManifestIntegrityError("研究数据 blob 不可读") from exc

    @staticmethod
    def _canonical_record_sha256(record: dict[str, Any]) -> str:
        immutable = {key: value for key, value in record.items() if key != "record_sha256"}
        payload = json.dumps(
            immutable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _create_owned_marker(
        self, kind: str, target: Path, *, digest: str | None = None
    ) -> Path:
        marker_id = uuid4().hex
        relative_path = target.relative_to(self.root_directory).as_posix()
        marker = self.policy.authorize(
            self.ownership_directory / f"{marker_id}.owned.json"
        )
        temp = self.policy.authorize(
            self.ownership_directory / f".marker-{marker_id}.tmp"
        )
        ownership: dict[str, Any] = {
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "digest": digest,
            "kind": kind,
            "marker_id": marker_id,
            "relative_path": relative_path,
            "schema": "a-share-quant.research-owned-object",
            "state": "allocated",
            "target_sha256": None,
            "target_size_bytes": None,
            "version": 1,
        }
        ownership["marker_sha256"] = self._canonical_marker_sha256(ownership)
        payload = (
            json.dumps(
                ownership, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        try:
            self.policy.revalidate(temp)
            with temp.open("xb") as handle:
                handle.write(payload)
                handle.flush()
            self.policy.revalidate(temp)
            self._file_fsync(temp)
            self.policy.revalidate(temp)
            self.policy.revalidate(marker)
            os.replace(temp, marker)
            self.policy.revalidate(self.ownership_directory)
            self._directory_fsync(self.ownership_directory)
            return marker
        finally:
            self.policy.revalidate(temp)
            temp.unlink(missing_ok=True)

    @staticmethod
    def _canonical_marker_sha256(ownership: dict[str, Any]) -> str:
        immutable = {
            key: value for key, value in ownership.items() if key != "marker_sha256"
        }
        payload = json.dumps(
            immutable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _read_owned_marker(self, marker: Path) -> dict[str, Any]:
        match = _MARKER_PATTERN.fullmatch(marker.name)
        if match is None:
            raise ManifestIntegrityError("ownership marker filename is invalid")
        self.policy.revalidate(marker)
        try:
            raw = marker.read_bytes()
            ownership = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestIntegrityError("ownership marker is invalid JSON") from exc
        required = {
            "created_at",
            "digest",
            "kind",
            "marker_id",
            "marker_sha256",
            "relative_path",
            "schema",
            "state",
            "target_sha256",
            "target_size_bytes",
            "version",
        }
        if not isinstance(ownership, dict) or set(ownership) != required:
            raise ManifestIntegrityError("ownership marker schema fields are invalid")
        if (
            ownership["schema"] != "a-share-quant.research-owned-object"
            or ownership["version"] != 1
            or ownership["marker_id"] != match.group(1)
            or ownership["kind"] not in {"stage", "manifest_temp", "orphan_blob"}
            or ownership["marker_sha256"]
            != self._canonical_marker_sha256(ownership)
        ):
            raise ManifestIntegrityError("ownership marker integrity check failed")
        state = ownership["state"]
        if state == "allocated":
            if ownership["target_sha256"] is not None or ownership["target_size_bytes"] is not None:
                raise ManifestIntegrityError("allocated marker cannot bind target content")
        elif state == "sealed":
            if (
                not isinstance(ownership["target_sha256"], str)
                or not _DIGEST_PATTERN.fullmatch(ownership["target_sha256"])
                or not isinstance(ownership["target_size_bytes"], int)
                or ownership["target_size_bytes"] < 0
            ):
                raise ManifestIntegrityError("sealed marker target metadata is invalid")
            if (
                ownership["kind"] == "orphan_blob"
                and ownership["digest"] != ownership["target_sha256"]
            ):
                raise ManifestIntegrityError("orphan marker digest does not match target")
        else:
            raise ManifestIntegrityError("ownership marker state is invalid")
        try:
            created_at = datetime.fromisoformat(
                ownership["created_at"].replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as exc:
            raise ManifestIntegrityError("ownership marker created_at is invalid") from exc
        if created_at.utcoffset() != UTC.utcoffset(created_at):
            raise ManifestIntegrityError("ownership marker created_at must be UTC")
        return ownership

    def _seal_owned_marker(self, marker: Path, target: Path) -> None:
        ownership = self._read_owned_marker(marker)
        if ownership["state"] != "allocated":
            raise ManifestIntegrityError("ownership marker is already sealed")
        expected_target = self._owned_marker_target(ownership)
        if target != expected_target:
            raise ManifestIntegrityError("ownership marker target changed before seal")
        self.policy.revalidate(target)
        if not target.is_file():
            raise ManifestIntegrityError("ownership target is not a regular file")
        self.policy.revalidate(target)
        target_size = target.stat().st_size
        self.policy.revalidate(target)
        target_sha256 = self._sha256(target)
        if ownership["kind"] == "orphan_blob" and ownership["digest"] != target_sha256:
            raise ManifestIntegrityError("orphan ownership digest mismatch")
        ownership["state"] = "sealed"
        ownership["target_sha256"] = target_sha256
        ownership["target_size_bytes"] = target_size
        ownership["marker_sha256"] = self._canonical_marker_sha256(ownership)
        self._replace_owned_marker(marker, ownership)

    def _replace_owned_marker(self, marker: Path, ownership: dict[str, Any]) -> None:
        marker_id = ownership["marker_id"]
        temp = self.policy.authorize(
            self.ownership_directory / f".marker-{marker_id}.tmp"
        )
        payload = (
            json.dumps(
                ownership, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        try:
            self.policy.revalidate(temp)
            with temp.open("xb") as handle:
                handle.write(payload)
                handle.flush()
            self.policy.revalidate(temp)
            self._file_fsync(temp)
            self.policy.revalidate(temp)
            self.policy.revalidate(marker)
            os.replace(temp, marker)
            self.policy.revalidate(self.ownership_directory)
            self._directory_fsync(self.ownership_directory)
        finally:
            self.policy.revalidate(temp)
            temp.unlink(missing_ok=True)

    def _owned_marker_target(self, ownership: dict[str, Any]) -> Path:
        relative_path = ownership["relative_path"]
        if not isinstance(relative_path, str):
            raise ManifestIntegrityError("ownership marker path is invalid")
        target = self.policy.authorize(self.root_directory / relative_path)
        kind = ownership["kind"]
        if kind == "stage":
            valid = target.parent == self.blob_directory and bool(
                _STAGE_PATTERN.fullmatch(target.name)
            )
        elif kind == "manifest_temp":
            valid = target.parent == self.root_directory and bool(
                _MANIFEST_TEMP_PATTERN.fullmatch(target.name)
            )
        else:
            digest = ownership["digest"]
            valid = (
                isinstance(digest, str)
                and bool(_DIGEST_PATTERN.fullmatch(digest))
                and target == self.blob_directory / f"{digest}.parquet"
            )
        if not valid:
            raise ManifestIntegrityError("ownership marker path/kind is invalid")
        return target

    def _remove_owned_marker(self, marker: Path) -> None:
        if _MARKER_PATTERN.fullmatch(marker.name) and marker.parent == self.ownership_directory:
            self.policy.revalidate(marker)
            marker.unlink(missing_ok=True)

    def _publish_manifest(self, records: Sequence[dict[str, Any]]) -> None:
        temp = self.policy.authorize(self.root_directory / f".manifest-{uuid4().hex}.tmp")
        marker = self._create_owned_marker("manifest_temp", temp)
        payload = "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for record in records
        ).encode("utf-8")
        self.policy.revalidate(self.manifest_path)
        previous_exists = self.manifest_path.exists()
        previous = b""
        if previous_exists:
            self.policy.revalidate(self.manifest_path)
            previous = self.manifest_path.read_bytes()
        rollback: Path | None = None
        rollback_marker: Path | None = None
        try:
            if previous_exists:
                rollback = self.policy.authorize(
                    self.root_directory / f".manifest-{uuid4().hex}.tmp"
                )
                rollback_marker = self._create_owned_marker("manifest_temp", rollback)
                self._write_fsynced_file(rollback, previous, self._manifest_fsync)
                self._seal_owned_marker(rollback_marker, rollback)
            self.policy.revalidate(temp)
            self._write_fsynced_file(temp, payload, self._manifest_fsync)
            self._seal_owned_marker(marker, temp)
            if self._tree_size(self.root_directory) > self.maximum_research_data_bytes:
                raise StorageQuotaError("研究数据总量超过配额")
            self.policy.revalidate(temp)
            self.policy.revalidate(self.manifest_path)
            self._manifest_replace(temp, self.manifest_path)
            self.policy.revalidate(self.manifest_path)
            try:
                self.policy.revalidate(self.root_directory)
                self._directory_fsync(self.root_directory)
            except OSError:
                self._restore_manifest(previous_exists, rollback)
                raise
        finally:
            self._safe_unlink_manifest_temp(temp)
            self._remove_owned_marker(marker)
            if rollback is not None:
                self._safe_unlink_manifest_temp(rollback)
            if rollback_marker is not None:
                self._remove_owned_marker(rollback_marker)

    def _write_fsynced_file(
        self, path: Path, payload: bytes, fsync: Callable[[Path], None]
    ) -> None:
        self.policy.revalidate(path)
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
        self.policy.revalidate(path)
        fsync(path)

    def _restore_manifest(self, previous_exists: bool, rollback: Path | None) -> None:
        if not previous_exists:
            self.policy.revalidate(self.manifest_path)
            self.manifest_path.unlink(missing_ok=True)
            return
        if rollback is None:
            raise ManifestIntegrityError("manifest rollback file is missing")
        self.policy.revalidate(rollback)
        self.policy.revalidate(self.manifest_path)
        os.replace(rollback, self.manifest_path)
        self.policy.revalidate(self.root_directory)
        self._fsync_directory(self.root_directory)

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


def _lock_file(
    handle: Any,
    *,
    timeout_seconds: float = 300,
    poll_interval_seconds: float = 0.05,
) -> None:
    if timeout_seconds < 0 or poll_interval_seconds <= 0:
        raise ValueError("lock timeout and poll interval must be positive")
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + timeout_seconds
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                is_contention = exc.errno in {errno.EACCES, errno.EDEADLK} or getattr(
                    exc, "winerror", None
                ) in {33, 36}
                if not is_contention:
                    raise
                if time.monotonic() >= deadline:
                    raise StorageLockTimeoutError(
                        f"research storage lock timed out after {timeout_seconds:g} seconds"
                    ) from exc
                time.sleep(poll_interval_seconds)
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
