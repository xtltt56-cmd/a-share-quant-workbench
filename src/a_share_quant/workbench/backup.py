"""Versioned, local-only backup and explicitly confirmed restore helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import uuid4

_FORMAT_VERSION = 1
_MANIFEST_NAME = "backup-manifest.json"
_MANAGED_PREFIX = "managed/"


@dataclass(frozen=True)
class BackupFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class BackupManifest:
    format_version: int
    backup_id: str
    created_at: str
    files: tuple[BackupFile, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": item.size}
                for item in self.files
            ],
        }


@dataclass(frozen=True)
class RestorePreflight:
    manifest: BackupManifest
    confirmation_token: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class RestoreReceipt:
    restored_files: tuple[str, ...]
    audit_id: str


@dataclass(frozen=True)
class _PendingRestore:
    archive_path: Path
    manifest_digest: str


class LocalBackupManager:
    """Back up only explicit files and never restore without a fresh local token."""

    def __init__(
        self,
        *,
        managed_files: Mapping[str, Path],
        audit_path: Path,
        consistency_groups: Mapping[str, Iterable[str]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._managed_files = {
            _validate_logical_path(logical_path): Path(local_path)
            for logical_path, local_path in managed_files.items()
        }
        self._consistency_groups = _validate_consistency_groups(
            consistency_groups,
            set(self._managed_files),
        )
        self._audit_path = Path(audit_path)
        if any(_same_path(path, self._audit_path) for path in self._managed_files.values()):
            raise ValueError("restore audit file cannot be a managed backup file")
        if _has_duplicate_destinations(self._managed_files.values()):
            raise ValueError("managed backup files cannot share a duplicate local destination")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pending_restores: dict[str, _PendingRestore] = {}
        self._restore_lock = RLock()

    def create_backup(self, archive_path: Path) -> BackupManifest:
        """Write an archive containing the declared files that currently exist."""

        archive = Path(archive_path)
        if any(_same_path(archive, path) for path in self._managed_files.values()):
            raise ValueError("backup archive cannot replace a managed local file")
        contents: dict[str, bytes] = {}
        files: list[BackupFile] = []
        for logical_path, local_path in sorted(self._managed_files.items()):
            if not local_path.exists():
                continue
            if local_path.is_symlink() or not local_path.is_file():
                raise ValueError("managed backup file must be a regular local file")
            try:
                content = local_path.read_bytes()
            except OSError as exc:
                raise ValueError("managed backup file cannot be read") from exc
            contents[logical_path] = content
            files.append(
                BackupFile(
                    path=logical_path,
                    sha256=_sha256(content),
                    size=len(content),
                )
            )
        self._ensure_consistency_groups_complete(set(contents))
        manifest = BackupManifest(
            format_version=_FORMAT_VERSION,
            backup_id=f"backup-{uuid4().hex}",
            created_at=_utc_now(self._clock).isoformat(),
            files=tuple(files),
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = _temporary_path(archive)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr(_MANIFEST_NAME, _canonical_json(manifest.to_dict()).encode("utf-8"))
                for logical_path, content in contents.items():
                    bundle.writestr(f"{_MANAGED_PREFIX}{logical_path}", content)
            os.replace(temporary, archive)
        finally:
            if temporary.exists():
                temporary.unlink()
        return manifest

    def preflight_restore(self, archive_path: Path) -> RestorePreflight:
        """Validate archive members and hashes before issuing a one-use token."""

        with self._restore_lock:
            archive = Path(archive_path).resolve()
            manifest, _ = self._read_validated_archive(archive)
            confirmation_token = f"restore-{uuid4().hex}"
            self._pending_restores[confirmation_token] = _PendingRestore(
                archive_path=archive,
                manifest_digest=_sha256(_canonical_json(manifest.to_dict()).encode("utf-8")),
            )
            return RestorePreflight(
                manifest=manifest,
                confirmation_token=confirmation_token,
                files=tuple(item.path for item in manifest.files),
            )

    def restore(self, archive_path: Path, *, confirmation_token: str) -> RestoreReceipt:
        """Stage verified bytes, audit intent, then replace only declared files."""

        with self._restore_lock:
            return self._restore_locked(archive_path, confirmation_token=confirmation_token)

    def _restore_locked(
        self,
        archive_path: Path,
        *,
        confirmation_token: str,
    ) -> RestoreReceipt:
        pending = self._pending_restores.pop(str(confirmation_token), None)
        archive = Path(archive_path).resolve()
        if pending is None or pending.archive_path != archive:
            raise ValueError("explicit restore confirmation is required")
        manifest, contents = self._read_validated_archive(archive)
        manifest_digest = _sha256(_canonical_json(manifest.to_dict()).encode("utf-8"))
        if manifest_digest != pending.manifest_digest:
            raise ValueError("explicit restore confirmation is required")

        staged: list[tuple[Path, Path]] = []
        rollback_snapshots: dict[Path, Path | None] = {}
        replaced_destinations: list[Path] = []
        rollback_failed = False
        try:
            for logical_path, content in contents.items():
                destination = self._managed_files[logical_path]
                destination.parent.mkdir(parents=True, exist_ok=True)
                handle = tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f".{destination.name}.",
                    suffix=".restore",
                    dir=destination.parent,
                    delete=False,
                )
                with handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged.append((destination, Path(handle.name)))

            for destination, _ in staged:
                rollback_snapshots[destination] = _snapshot_restore_target(destination)
            audit_id = self._append_restore_audit(manifest)
            try:
                for destination, temporary in staged:
                    os.replace(temporary, destination)
                    replaced_destinations.append(destination)
            except OSError:
                try:
                    _rollback_replaced_targets(replaced_destinations, rollback_snapshots)
                except OSError:
                    rollback_failed = True
                    raise
                raise
            return RestoreReceipt(
                restored_files=tuple(item.path for item in manifest.files),
                audit_id=audit_id,
            )
        finally:
            for _, temporary in staged:
                if temporary.exists():
                    temporary.unlink()
            if not rollback_failed:
                for snapshot in rollback_snapshots.values():
                    if snapshot is not None and snapshot.exists():
                        snapshot.unlink()

    def _read_validated_archive(
        self,
        archive_path: Path,
    ) -> tuple[BackupManifest, dict[str, bytes]]:
        try:
            with zipfile.ZipFile(archive_path) as bundle:
                infos = bundle.infolist()
                names = [item.filename for item in infos]
                for name in names:
                    _validate_zip_member(name)
                if len(names) != len(set(names)):
                    raise ValueError("backup archive contains duplicate members")
                if _MANIFEST_NAME not in names:
                    raise ValueError("backup archive manifest is missing")
                manifest = _manifest_from_bytes(bundle.read(_MANIFEST_NAME))
                expected_names = {_MANIFEST_NAME}
                expected_names.update(f"{_MANAGED_PREFIX}{item.path}" for item in manifest.files)
                if set(names) != expected_names:
                    raise ValueError("backup archive contains undeclared members")
                contents: dict[str, bytes] = {}
                for item in manifest.files:
                    if item.path not in self._managed_files:
                        raise ValueError("backup archive references an unmanaged file")
                    content = bundle.read(f"{_MANAGED_PREFIX}{item.path}")
                    if len(content) != item.size or _sha256(content) != item.sha256:
                        raise ValueError("backup hash validation failed")
                    contents[item.path] = content
                self._ensure_consistency_groups_complete(set(contents))
                return manifest, contents
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError("backup archive cannot be read") from exc

    def _append_restore_audit(self, manifest: BackupManifest) -> str:
        audit_id = f"restore-audit-{uuid4().hex}"
        payload = {
            "audit_id": audit_id,
            "action": "restore",
            "phase": "before_replacement",
            "recorded_at": _utc_now(self._clock).isoformat(),
            "backup_id": manifest.backup_id,
            "files": [item.path for item in manifest.files],
        }
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self._audit_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(payload))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return audit_id

    def _ensure_consistency_groups_complete(self, present_paths: set[str]) -> None:
        for group_name, group_paths in self._consistency_groups.items():
            included_paths = group_paths.intersection(present_paths)
            if included_paths and included_paths != group_paths:
                raise ValueError(f"backup consistency group '{group_name}' is incomplete")


def _manifest_from_bytes(content: bytes) -> BackupManifest:
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("backup archive manifest is invalid") from exc
    expected_keys = {"format_version", "backup_id", "created_at", "files"}
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise ValueError("backup archive manifest is invalid")
    if raw["format_version"] != _FORMAT_VERSION:
        raise ValueError("backup archive format version is unsupported")
    if not isinstance(raw["backup_id"], str) or not raw["backup_id"].strip():
        raise ValueError("backup archive manifest is invalid")
    if not isinstance(raw["created_at"], str) or not raw["created_at"].strip():
        raise ValueError("backup archive manifest is invalid")
    if not isinstance(raw["files"], list):
        raise ValueError("backup archive manifest is invalid")
    files: list[BackupFile] = []
    seen_paths: set[str] = set()
    for item in raw["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise ValueError("backup archive manifest is invalid")
        logical_path = _validate_logical_path(item["path"])
        if logical_path in seen_paths:
            raise ValueError("backup archive manifest is invalid")
        if (
            not isinstance(item["sha256"], str)
            or len(item["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in item["sha256"])
        ):
            raise ValueError("backup archive manifest is invalid")
        if not isinstance(item["size"], int) or isinstance(item["size"], bool) or item["size"] < 0:
            raise ValueError("backup archive manifest is invalid")
        seen_paths.add(logical_path)
        files.append(BackupFile(path=logical_path, sha256=item["sha256"], size=item["size"]))
    return BackupManifest(
        format_version=raw["format_version"],
        backup_id=raw["backup_id"],
        created_at=raw["created_at"],
        files=tuple(files),
    )


def _validate_logical_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("backup logical path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.startswith("./") or path.name == ".":
        raise ValueError("backup logical path is invalid")
    normalized = path.as_posix()
    if normalized != value or normalized == ".":
        raise ValueError("backup logical path is invalid")
    return normalized


def _validate_consistency_groups(
    consistency_groups: Mapping[str, Iterable[str]] | None,
    managed_paths: set[str],
) -> dict[str, frozenset[str]]:
    if consistency_groups is None:
        return {}
    if not isinstance(consistency_groups, Mapping):
        raise ValueError("backup consistency groups are invalid")
    validated: dict[str, frozenset[str]] = {}
    for group_name, paths in consistency_groups.items():
        if not isinstance(group_name, str) or not group_name.strip():
            raise ValueError("backup consistency groups are invalid")
        if isinstance(paths, (str, bytes)):
            raise ValueError("backup consistency groups are invalid")
        try:
            normalized_paths = tuple(_validate_logical_path(path) for path in paths)
        except TypeError as exc:
            raise ValueError("backup consistency groups are invalid") from exc
        if len(normalized_paths) < 2 or len(normalized_paths) != len(set(normalized_paths)):
            raise ValueError("backup consistency groups are invalid")
        if not set(normalized_paths).issubset(managed_paths):
            raise ValueError("backup consistency groups reference unmanaged files")
        validated[group_name] = frozenset(normalized_paths)
    return validated


def _validate_zip_member(name: str) -> None:
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or ".." in path.parts:
        raise ValueError("zip traversal rejected")


def _snapshot_restore_target(destination: Path) -> Path | None:
    if destination.is_symlink():
        raise ValueError("managed restore target must be a regular local file")
    if not destination.exists():
        return None
    if not destination.is_file():
        raise ValueError("managed restore target must be a regular local file")
    try:
        content = destination.read_bytes()
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".rollback",
            dir=destination.parent,
            delete=False,
        )
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(handle.name)
    except OSError as exc:
        raise ValueError("managed restore target cannot be snapshotted") from exc


def _rollback_replaced_targets(
    replaced_destinations: list[Path],
    rollback_snapshots: Mapping[Path, Path | None],
) -> None:
    rollback_error: OSError | None = None
    for destination in reversed(replaced_destinations):
        snapshot = rollback_snapshots[destination]
        try:
            if snapshot is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(snapshot, destination)
        except OSError as exc:
            if rollback_error is None:
                rollback_error = exc
    if rollback_error is not None:
        raise rollback_error


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("backup clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _temporary_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return _canonical_path(left) == _canonical_path(right)


def _has_duplicate_destinations(paths: Iterable[Path]) -> bool:
    seen_paths: list[Path] = []
    for path in paths:
        if any(_same_path(path, seen) for seen in seen_paths):
            return True
        seen_paths.append(path)
    return False


def _canonical_path(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(os.path.normpath(str(resolved)))
