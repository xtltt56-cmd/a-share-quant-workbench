"""Versioned, local-only backup and explicitly confirmed restore helpers."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import tempfile
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import RLock
from uuid import uuid4

_FORMAT_VERSION = 1
_MANIFEST_NAME = "backup-manifest.json"
_MANAGED_PREFIX = "managed/"
_RESTORE_JOURNAL_FORMAT_VERSION = 2
_LEGACY_RESTORE_JOURNAL_FORMAT_VERSION = 1
_SHARED_RESTORE_LOCKS: dict[str, RLock] = {}
_SHARED_RESTORE_LOCKS_GUARD = RLock()


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


@dataclass(frozen=True)
class _RestoreJournalEntry:
    path: str
    destination: str | None
    had_original: bool
    snapshot: str | None
    sha256: str | None
    size: int | None
    planned_sha256: str | None
    planned_size: int | None

    def to_dict(self) -> dict[str, object]:
        if (
            self.destination is None
            or self.planned_sha256 is None
            or self.planned_size is None
        ):
            raise ValueError("restore journal is invalid")
        return {
            "path": self.path,
            "destination": self.destination,
            "had_original": self.had_original,
            "snapshot": self.snapshot,
            "sha256": self.sha256,
            "size": self.size,
            "planned_sha256": self.planned_sha256,
            "planned_size": self.planned_size,
        }


@dataclass(frozen=True)
class _RestoreJournal:
    format_version: int
    transaction_id: str
    configuration_digest: str | None
    entries: tuple[_RestoreJournalEntry, ...]

    def to_dict(self) -> dict[str, object]:
        if (
            self.format_version != _RESTORE_JOURNAL_FORMAT_VERSION
            or self.configuration_digest is None
        ):
            raise ValueError("restore journal is invalid")
        return {
            "format_version": _RESTORE_JOURNAL_FORMAT_VERSION,
            "transaction_id": self.transaction_id,
            "configuration_digest": self.configuration_digest,
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class _JournalRecoveryTarget:
    destination: Path
    had_original: bool
    content: bytes | None
    sha256: str | None
    size: int | None


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
            _validate_logical_path(logical_path): _resolve_non_symlink_path(
                local_path,
                description="managed backup file",
            )
            for logical_path, local_path in managed_files.items()
        }
        self._consistency_groups = _validate_consistency_groups(
            consistency_groups,
            set(self._managed_files),
        )
        self._audit_path = _resolve_non_symlink_path(
            audit_path,
            description="restore audit file",
        )
        if any(_same_path(path, self._audit_path) for path in self._managed_files.values()):
            raise ValueError("restore audit file cannot be a managed backup file")
        if _has_duplicate_destinations(self._managed_files.values()):
            raise ValueError("managed backup files cannot share a duplicate local destination")
        self._managed_configuration_digest = _managed_configuration_digest(self._managed_files)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pending_restores: dict[str, _PendingRestore] = {}
        self._journal_path = _restore_journal_path(self._audit_path)
        self._restore_lock_path = _restore_lock_path(self._journal_path)
        if self._journal_path.is_symlink():
            raise ValueError("restore journal file cannot be a symbolic link")
        if self._restore_lock_path.is_symlink():
            raise ValueError("restore lock file cannot be a symbolic link")
        if any(_same_path(path, self._journal_path) for path in self._managed_files.values()):
            raise ValueError("restore journal file cannot be a managed backup file")
        if _same_path(self._journal_path, self._audit_path):
            raise ValueError("restore journal file cannot be the audit file")
        if any(_same_path(path, self._restore_lock_path) for path in self._managed_files.values()):
            raise ValueError("restore lock file cannot be a managed backup file")
        if _same_path(self._restore_lock_path, self._audit_path):
            raise ValueError("restore lock file cannot be the audit file")
        if _same_path(self._restore_lock_path, self._journal_path):
            raise ValueError("restore lock file cannot be the restore journal file")
        self._restore_lock = _shared_restore_lock(self._journal_path)
        with self._coordinated_restore_lock():
            self._recover_pending_restore()

    def create_backup(self, archive_path: Path) -> BackupManifest:
        """Write an archive containing the declared files that currently exist."""

        with self._coordinated_restore_lock():
            self._recover_pending_restore()
            return self._create_backup_locked(archive_path)

    def _create_backup_locked(self, archive_path: Path) -> BackupManifest:
        archive = _resolve_local_path(archive_path)
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

        with self._coordinated_restore_lock():
            self._recover_pending_restore()
            archive = _resolve_local_path(archive_path)
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

        with self._coordinated_restore_lock():
            self._recover_pending_restore()
            return self._restore_locked(archive_path, confirmation_token=confirmation_token)

    @contextmanager
    def _coordinated_restore_lock(self) -> Iterator[None]:
        with self._restore_lock:
            with _durable_restore_lock(self._restore_lock_path):
                yield

    def _restore_locked(
        self,
        archive_path: Path,
        *,
        confirmation_token: str,
    ) -> RestoreReceipt:
        pending = self._pending_restores.pop(str(confirmation_token), None)
        archive = _resolve_local_path(archive_path)
        if pending is None or pending.archive_path != archive:
            raise ValueError("explicit restore confirmation is required")
        manifest, contents = self._read_validated_archive(archive)
        manifest_digest = _sha256(_canonical_json(manifest.to_dict()).encode("utf-8"))
        if manifest_digest != pending.manifest_digest:
            raise ValueError("explicit restore confirmation is required")

        staged: list[tuple[str, Path, Path]] = []
        journal: _RestoreJournal | None = None
        try:
            for logical_path, content in contents.items():
                destination = self._managed_files[logical_path]
                staged.append(
                    (
                        logical_path,
                        destination,
                        _stage_restore_bytes(destination, content, suffix=".restore"),
                    )
                )

            journal = self._create_restore_journal(staged, contents)
            self._write_restore_journal(journal)
            audit_id = self._append_restore_audit(manifest)
            for _, destination, temporary in staged:
                os.replace(temporary, destination)
            self._verify_restored_contents(contents)
            self._finalize_restore_journal(journal)
            return RestoreReceipt(
                restored_files=tuple(item.path for item in manifest.files),
                audit_id=audit_id,
            )
        except BaseException:
            if journal is not None and self._journal_path.exists():
                try:
                    self._recover_pending_restore()
                except BaseException as recovery_error:
                    raise ValueError("restore journal recovery failed") from recovery_error
            raise
        finally:
            for _, _, temporary in staged:
                if temporary.exists():
                    temporary.unlink()

    def _create_restore_journal(
        self,
        staged: list[tuple[str, Path, Path]],
        contents: Mapping[str, bytes],
    ) -> _RestoreJournal:
        transaction_id = f"restore-{uuid4().hex}"
        entries: list[_RestoreJournalEntry] = []
        for index, (logical_path, destination, _) in enumerate(staged):
            snapshot_path = _journal_snapshot_path(
                self._journal_path,
                transaction_id,
                index,
            )
            had_original, sha256, size = _snapshot_restore_target(
                destination,
                snapshot_path,
            )
            entries.append(
                _RestoreJournalEntry(
                    path=logical_path,
                    destination=_canonical_path(destination),
                    had_original=had_original,
                    snapshot=snapshot_path.name if had_original else None,
                    sha256=sha256,
                    size=size,
                    planned_sha256=_sha256(contents[logical_path]),
                    planned_size=len(contents[logical_path]),
                )
            )
        return _RestoreJournal(
            format_version=_RESTORE_JOURNAL_FORMAT_VERSION,
            transaction_id=transaction_id,
            configuration_digest=self._managed_configuration_digest,
            entries=tuple(entries),
        )

    def _write_restore_journal(self, journal: _RestoreJournal) -> None:
        _write_durable_bytes(
            self._journal_path,
            _canonical_json(journal.to_dict()).encode("utf-8"),
        )

    def _recover_pending_restore(self) -> None:
        journal = self._read_pending_restore_journal()
        if journal is None:
            return
        try:
            recovery_targets = self._journal_recovery_targets(journal)
            self._recover_restore_journal(journal, recovery_targets)
        except ValueError as exc:
            raise ValueError(f"restore journal cannot be recovered: {exc}") from exc

    def _read_pending_restore_journal(self) -> _RestoreJournal | None:
        if self._journal_path.is_symlink():
            raise ValueError("restore journal is invalid")
        if not self._journal_path.exists():
            return None
        if not self._journal_path.is_file():
            raise ValueError("restore journal is invalid")
        try:
            content = self._journal_path.read_bytes()
        except OSError as exc:
            raise ValueError("restore journal cannot be read") from exc
        if not content or len(content) > 65536:
            raise ValueError("restore journal is invalid")
        journal = _restore_journal_from_bytes(content, self._journal_path)
        paths = {entry.path for entry in journal.entries}
        if not paths.issubset(self._managed_files):
            raise ValueError("restore journal is invalid")
        if (
            journal.format_version != _RESTORE_JOURNAL_FORMAT_VERSION
            or journal.configuration_digest != self._managed_configuration_digest
        ):
            raise ValueError("restore journal configuration mismatch")
        for entry in journal.entries:
            if entry.destination != _canonical_path(self._managed_files[entry.path]):
                raise ValueError("restore journal configuration mismatch")
        self._ensure_consistency_groups_complete(paths)
        return journal

    def _journal_recovery_targets(
        self,
        journal: _RestoreJournal,
    ) -> tuple[_JournalRecoveryTarget, ...]:
        targets: list[_JournalRecoveryTarget] = []
        for index, entry in enumerate(journal.entries):
            destination = self._managed_files[entry.path]
            _validate_restore_destination(destination)
            if not entry.had_original:
                _validate_recovery_target_state(
                    destination,
                    had_original=False,
                    original_sha256=None,
                    original_size=None,
                    planned_sha256=entry.planned_sha256,
                    planned_size=entry.planned_size,
                )
                targets.append(
                    _JournalRecoveryTarget(
                        destination=destination,
                        had_original=False,
                        content=None,
                        sha256=None,
                        size=None,
                    )
                )
                continue
            snapshot_path = _journal_snapshot_path(
                self._journal_path,
                journal.transaction_id,
                index,
            )
            content = _read_valid_snapshot(
                snapshot_path,
                expected_sha256=entry.sha256,
                expected_size=entry.size,
            )
            _validate_recovery_target_state(
                destination,
                had_original=True,
                original_sha256=entry.sha256,
                original_size=entry.size,
                planned_sha256=entry.planned_sha256,
                planned_size=entry.planned_size,
            )
            targets.append(
                _JournalRecoveryTarget(
                    destination=destination,
                    had_original=True,
                    content=content,
                    sha256=entry.sha256,
                    size=entry.size,
                )
            )
        return tuple(targets)

    def _recover_restore_journal(
        self,
        journal: _RestoreJournal,
        recovery_targets: tuple[_JournalRecoveryTarget, ...],
    ) -> None:
        staged: list[tuple[Path, Path]] = []
        try:
            for target in recovery_targets:
                if target.had_original:
                    if target.content is None:
                        raise ValueError("restore journal is invalid")
                    staged.append(
                        (
                            target.destination,
                            _stage_restore_bytes(
                                target.destination,
                                target.content,
                                suffix=".recover",
                            ),
                        )
                    )
            for destination, temporary in staged:
                os.replace(temporary, destination)
            for target in recovery_targets:
                if not target.had_original:
                    target.destination.unlink(missing_ok=True)
            _verify_recovery_targets(recovery_targets)
        finally:
            for _, temporary in staged:
                if temporary.exists():
                    temporary.unlink()
        self._finalize_restore_journal(journal)

    def _verify_restored_contents(self, contents: Mapping[str, bytes]) -> None:
        for logical_path, expected_content in contents.items():
            destination = self._managed_files[logical_path]
            _validate_restore_destination(destination)
            try:
                actual_content = destination.read_bytes()
            except OSError as exc:
                raise ValueError("restored file cannot be verified") from exc
            if actual_content != expected_content:
                raise ValueError("restored file cannot be verified")

    def _finalize_restore_journal(self, journal: _RestoreJournal) -> None:
        if self._journal_path.is_symlink() or not self._journal_path.is_file():
            raise ValueError("restore journal is invalid")
        try:
            self._journal_path.unlink()
        except OSError as exc:
            raise ValueError("restore journal cannot be finalized") from exc
        for index, entry in enumerate(journal.entries):
            if not entry.had_original:
                continue
            snapshot_path = _journal_snapshot_path(
                self._journal_path,
                journal.transaction_id,
                index,
            )
            try:
                snapshot_path.unlink(missing_ok=True)
            except OSError as exc:
                raise ValueError("restore journal cannot be finalized") from exc

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


def _restore_journal_from_bytes(content: bytes, journal_path: Path) -> _RestoreJournal:
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("restore journal is invalid") from exc
    if not isinstance(raw, dict):
        raise ValueError("restore journal is invalid")
    format_version = raw.get("format_version")
    if format_version == _LEGACY_RESTORE_JOURNAL_FORMAT_VERSION:
        return _legacy_restore_journal_from_dict(raw, journal_path)
    if format_version != _RESTORE_JOURNAL_FORMAT_VERSION:
        raise ValueError("restore journal is invalid")
    expected_keys = {
        "format_version",
        "transaction_id",
        "configuration_digest",
        "entries",
    }
    if set(raw) != expected_keys or not _is_sha256(raw["configuration_digest"]):
        raise ValueError("restore journal is invalid")
    transaction_id = raw["transaction_id"]
    if not _is_restore_transaction_id(transaction_id):
        raise ValueError("restore journal is invalid")
    if not isinstance(raw["entries"], list):
        raise ValueError("restore journal is invalid")
    entries: list[_RestoreJournalEntry] = []
    seen_paths: set[str] = set()
    for index, raw_entry in enumerate(raw["entries"]):
        expected_entry_keys = {
            "path",
            "destination",
            "had_original",
            "snapshot",
            "sha256",
            "size",
            "planned_sha256",
            "planned_size",
        }
        if not isinstance(raw_entry, dict) or set(raw_entry) != expected_entry_keys:
            raise ValueError("restore journal is invalid")
        logical_path = _validate_logical_path(raw_entry["path"])
        if logical_path in seen_paths or not isinstance(raw_entry["had_original"], bool):
            raise ValueError("restore journal is invalid")
        if (
            not isinstance(raw_entry["destination"], str)
            or not raw_entry["destination"]
            or raw_entry["destination"].strip() != raw_entry["destination"]
            or "\x00" in raw_entry["destination"]
        ):
            raise ValueError("restore journal is invalid")
        if not _is_sha256(raw_entry["planned_sha256"]):
            raise ValueError("restore journal is invalid")
        if (
            not isinstance(raw_entry["planned_size"], int)
            or isinstance(raw_entry["planned_size"], bool)
            or raw_entry["planned_size"] < 0
        ):
            raise ValueError("restore journal is invalid")
        had_original = raw_entry["had_original"]
        if had_original:
            expected_snapshot = _journal_snapshot_path(
                journal_path,
                transaction_id,
                index,
            ).name
            if raw_entry["snapshot"] != expected_snapshot:
                raise ValueError("restore journal is invalid")
            if not _is_sha256(raw_entry["sha256"]):
                raise ValueError("restore journal is invalid")
            if (
                not isinstance(raw_entry["size"], int)
                or isinstance(raw_entry["size"], bool)
                or raw_entry["size"] < 0
            ):
                raise ValueError("restore journal is invalid")
        elif any(raw_entry[key] is not None for key in ("snapshot", "sha256", "size")):
            raise ValueError("restore journal is invalid")
        seen_paths.add(logical_path)
        entries.append(
            _RestoreJournalEntry(
                path=logical_path,
                destination=raw_entry["destination"],
                had_original=had_original,
                snapshot=raw_entry["snapshot"],
                sha256=raw_entry["sha256"],
                size=raw_entry["size"],
                planned_sha256=raw_entry["planned_sha256"],
                planned_size=raw_entry["planned_size"],
            )
        )
    return _RestoreJournal(
        format_version=_RESTORE_JOURNAL_FORMAT_VERSION,
        transaction_id=transaction_id,
        configuration_digest=raw["configuration_digest"],
        entries=tuple(entries),
    )


def _legacy_restore_journal_from_dict(
    raw: dict[object, object],
    journal_path: Path,
) -> _RestoreJournal:
    expected_keys = {"format_version", "transaction_id", "entries"}
    if set(raw) != expected_keys:
        raise ValueError("restore journal is invalid")
    transaction_id = raw["transaction_id"]
    if not _is_restore_transaction_id(transaction_id):
        raise ValueError("restore journal is invalid")
    if not isinstance(raw["entries"], list):
        raise ValueError("restore journal is invalid")
    entries: list[_RestoreJournalEntry] = []
    seen_paths: set[str] = set()
    for index, raw_entry in enumerate(raw["entries"]):
        expected_entry_keys = {"path", "had_original", "snapshot", "sha256", "size"}
        if not isinstance(raw_entry, dict) or set(raw_entry) != expected_entry_keys:
            raise ValueError("restore journal is invalid")
        logical_path = _validate_logical_path(raw_entry["path"])
        if logical_path in seen_paths or not isinstance(raw_entry["had_original"], bool):
            raise ValueError("restore journal is invalid")
        had_original = raw_entry["had_original"]
        if had_original:
            expected_snapshot = _journal_snapshot_path(
                journal_path,
                transaction_id,
                index,
            ).name
            if raw_entry["snapshot"] != expected_snapshot:
                raise ValueError("restore journal is invalid")
            if not _is_sha256(raw_entry["sha256"]):
                raise ValueError("restore journal is invalid")
            if (
                not isinstance(raw_entry["size"], int)
                or isinstance(raw_entry["size"], bool)
                or raw_entry["size"] < 0
            ):
                raise ValueError("restore journal is invalid")
        elif any(raw_entry[key] is not None for key in ("snapshot", "sha256", "size")):
            raise ValueError("restore journal is invalid")
        seen_paths.add(logical_path)
        entries.append(
            _RestoreJournalEntry(
                path=logical_path,
                destination=None,
                had_original=had_original,
                snapshot=raw_entry["snapshot"],
                sha256=raw_entry["sha256"],
                size=raw_entry["size"],
                planned_sha256=None,
                planned_size=None,
            )
        )
    return _RestoreJournal(
        format_version=_LEGACY_RESTORE_JOURNAL_FORMAT_VERSION,
        transaction_id=transaction_id,
        configuration_digest=None,
        entries=tuple(entries),
    )


def _is_restore_transaction_id(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 40 or not value.startswith("restore-"):
        return False
    return all(character in "0123456789abcdef" for character in value[8:])


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _restore_journal_path(audit_path: Path) -> Path:
    return audit_path.with_name(f".{audit_path.name}.restore-journal.json")


def _restore_lock_path(journal_path: Path) -> Path:
    return journal_path.with_name(f"{journal_path.name}.lock")


def _shared_restore_lock(journal_path: Path) -> RLock:
    journal_key = _canonical_path(journal_path)
    with _SHARED_RESTORE_LOCKS_GUARD:
        lock = _SHARED_RESTORE_LOCKS.get(journal_key)
        if lock is None:
            lock = RLock()
            _SHARED_RESTORE_LOCKS[journal_key] = lock
        return lock


@contextmanager
def _durable_restore_lock(lock_path: Path) -> Iterator[None]:
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise ValueError("restore lock file is invalid")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise ValueError("restore lock file cannot be opened") from exc
    try:
        if lock_path.is_symlink():
            raise ValueError("restore lock file is invalid")
        _acquire_restore_lock_file_descriptor(file_descriptor)
    except BaseException:
        os.close(file_descriptor)
        raise
    try:
        yield
    finally:
        try:
            _release_restore_lock_file_descriptor(file_descriptor)
        finally:
            os.close(file_descriptor)


def _acquire_restore_lock_file_descriptor(file_descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                os.lseek(file_descriptor, 0, os.SEEK_SET)
                msvcrt.locking(file_descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                time.sleep(0.01)
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_EX)


def _release_restore_lock_file_descriptor(file_descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(file_descriptor, 0, os.SEEK_SET)
        msvcrt.locking(file_descriptor, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(file_descriptor, fcntl.LOCK_UN)


def _journal_snapshot_path(journal_path: Path, transaction_id: str, index: int) -> Path:
    return journal_path.with_name(f"{journal_path.name}.{transaction_id}.{index}.rollback")


def _snapshot_restore_target(
    destination: Path,
    snapshot_path: Path,
) -> tuple[bool, str | None, int | None]:
    _validate_restore_destination(destination)
    if not destination.exists():
        return False, None, None
    try:
        content = destination.read_bytes()
    except OSError as exc:
        raise ValueError("managed restore target cannot be snapshotted") from exc
    _write_durable_bytes(snapshot_path, content)
    return True, _sha256(content), len(content)


def _read_valid_snapshot(
    snapshot_path: Path,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
) -> bytes:
    if expected_sha256 is None or expected_size is None:
        raise ValueError("restore journal is invalid")
    if snapshot_path.is_symlink() or not snapshot_path.is_file():
        raise ValueError("restore journal is invalid")
    try:
        content = snapshot_path.read_bytes()
    except OSError as exc:
        raise ValueError("restore journal cannot be read") from exc
    if len(content) != expected_size or _sha256(content) != expected_sha256:
        raise ValueError("restore journal is invalid")
    return content


def _validate_recovery_target_state(
    destination: Path,
    *,
    had_original: bool,
    original_sha256: str | None,
    original_size: int | None,
    planned_sha256: str | None,
    planned_size: int | None,
) -> None:
    if not _is_sha256(planned_sha256) or not _is_nonnegative_size(planned_size):
        raise ValueError("restore journal is invalid")
    if had_original and (
        not _is_sha256(original_sha256) or not _is_nonnegative_size(original_size)
    ):
        raise ValueError("restore journal is invalid")
    _validate_restore_destination(destination)
    if not destination.exists():
        if not had_original:
            return
        raise ValueError("restore journal has unexpected post-interruption state")
    try:
        current_content = destination.read_bytes()
    except OSError as exc:
        raise ValueError("restore journal has unexpected post-interruption state") from exc
    current_sha256 = _sha256(current_content)
    current_size = len(current_content)
    matches_original = (
        had_original
        and current_sha256 == original_sha256
        and current_size == original_size
    )
    matches_planned = current_sha256 == planned_sha256 and current_size == planned_size
    if not matches_original and not matches_planned:
        raise ValueError("restore journal has unexpected post-interruption state")


def _is_nonnegative_size(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _stage_restore_bytes(destination: Path, content: bytes, *, suffix: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=suffix,
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary_path
    except OSError as exc:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        raise ValueError("restore staging file cannot be written") from exc


def _write_durable_bytes(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_path(destination)
    try:
        with temporary_path.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    except OSError as exc:
        raise ValueError("restore journal cannot be written") from exc
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _validate_restore_destination(destination: Path) -> None:
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ValueError("managed restore target must be a regular local file")


def _verify_recovery_targets(recovery_targets: tuple[_JournalRecoveryTarget, ...]) -> None:
    for target in recovery_targets:
        if not target.had_original:
            if target.destination.exists() or target.destination.is_symlink():
                raise ValueError("restore journal cannot be recovered")
            continue
        _validate_restore_destination(target.destination)
        try:
            content = target.destination.read_bytes()
        except OSError as exc:
            raise ValueError("restore journal cannot be recovered") from exc
        if content is None or len(content) != target.size or _sha256(content) != target.sha256:
            raise ValueError("restore journal cannot be recovered")


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


def _managed_configuration_digest(managed_files: Mapping[str, Path]) -> str:
    configuration = [
        {
            "path": logical_path,
            "destination": _canonical_path(destination),
        }
        for logical_path, destination in sorted(managed_files.items())
    ]
    return _sha256(_canonical_json(configuration).encode("utf-8"))


def _resolve_non_symlink_path(path: Path, *, description: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError(f"{description} cannot be a symbolic link")
    return _resolve_local_path(candidate)


def _resolve_local_path(path: Path) -> Path:
    candidate = Path(path)
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def _canonical_path(path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    return os.path.normcase(os.path.normpath(str(resolved)))
