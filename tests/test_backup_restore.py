from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.backup import LocalBackupManager, _durable_restore_lock


def _hold_durable_restore_lock(lock_path: str, acquired, release) -> None:
    with _durable_restore_lock(Path(lock_path)):
        acquired.set()
        release.wait(timeout=5)


def _manager(tmp_path) -> tuple[LocalBackupManager, dict[str, object]]:
    account_ledger = tmp_path / "account-ledger.jsonl"
    model_notes = tmp_path / "model-notes.json"
    unmanaged = tmp_path / "unmanaged-local-file.txt"
    account_ledger.write_text('{"kind":"fill"}\n', encoding="utf-8")
    model_notes.write_text('{"model":"local-only"}\n', encoding="utf-8")
    unmanaged.write_text("must not be archived", encoding="utf-8")
    return (
        LocalBackupManager(
            managed_files={
                "account-ledger.jsonl": account_ledger,
                "model-notes.json": model_notes,
            },
            audit_path=tmp_path / "restore-audit.jsonl",
        ),
        {
            "account_ledger": account_ledger,
            "model_notes": model_notes,
            "unmanaged": unmanaged,
            "audit": tmp_path / "restore-audit.jsonl",
        },
    )


def _account_pair_manager(tmp_path) -> tuple[LocalBackupManager, Path, Path, Path]:
    ledger = tmp_path / "account-ledger.jsonl"
    initialization = tmp_path / "account-ledger.jsonl.initialization.json"
    audit_path = tmp_path / "restore-audit.jsonl"
    return (
        LocalBackupManager(
            managed_files={
                "account-ledger.jsonl": ledger,
                "account-ledger.jsonl.initialization.json": initialization,
            },
            consistency_groups={
                "account-state": (
                    "account-ledger.jsonl",
                    "account-ledger.jsonl.initialization.json",
                )
            },
            audit_path=audit_path,
        ),
        ledger,
        initialization,
        audit_path,
    )


def _restore_journal_path(audit_path: Path) -> Path:
    return audit_path.with_name(f".{audit_path.name}.restore-journal.json")


def _audit_restore_lock_path(audit_path: Path) -> Path:
    journal_path = _restore_journal_path(audit_path)
    return journal_path.with_name(f"{journal_path.name}.lock")


def _destination_restore_lock_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.restore-resource.lock")


def _pending_restore_marker_path(destination: Path) -> Path:
    return destination.with_name(f".{destination.name}.restore-pending.json")


def _canonical_destination(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path.resolve())))


def _managed_configuration_digest(managed_files: dict[str, Path]) -> str:
    configuration = [
        {"path": logical_path, "destination": _canonical_destination(destination)}
        for logical_path, destination in sorted(managed_files.items())
    ]
    encoded = json.dumps(
        configuration,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_bound_pending_journal(
    *,
    audit_path: Path,
    managed_files: dict[str, Path],
    original_contents: dict[str, bytes | None],
    planned_contents: dict[str, bytes],
    invalid_snapshot_hash: bool = False,
) -> tuple[Path, tuple[Path, ...]]:
    transaction_id = f"restore-{'a' * 32}"
    journal_path = _restore_journal_path(audit_path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    snapshots: list[Path] = []
    for index, (logical_path, original_content) in enumerate(original_contents.items()):
        planned_content = planned_contents[logical_path]
        had_original = original_content is not None
        snapshot: Path | None = None
        if had_original:
            snapshot = journal_path.with_name(
                f"{journal_path.name}.{transaction_id}.{index}.rollback"
            )
            snapshot.write_bytes(original_content)
            snapshots.append(snapshot)
        expected_hash = (
            hashlib.sha256(original_content).hexdigest() if original_content is not None else None
        )
        entries.append(
            {
                "path": logical_path,
                "destination": _canonical_destination(managed_files[logical_path]),
                "had_original": had_original,
                "snapshot": snapshot.name if snapshot is not None else None,
                "sha256": "0" * 64 if invalid_snapshot_hash and had_original else expected_hash,
                "size": len(original_content) if original_content is not None else None,
                "planned_sha256": hashlib.sha256(planned_content).hexdigest(),
                "planned_size": len(planned_content),
            }
        )
    journal_path.write_text(
        json.dumps(
            {
                "format_version": 2,
                "transaction_id": transaction_id,
                "configuration_digest": _managed_configuration_digest(managed_files),
                "entries": entries,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return journal_path, tuple(snapshots)


def _write_legacy_pending_account_journal(
    *,
    audit_path: Path,
    original_ledger: bytes,
    original_initialization: bytes,
) -> tuple[Path, tuple[Path, Path]]:
    transaction_id = f"restore-{'c' * 32}"
    journal_path = _restore_journal_path(audit_path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    snapshots: list[Path] = []
    for index, (logical_path, content) in enumerate(
        (
            ("account-ledger.jsonl", original_ledger),
            ("account-ledger.jsonl.initialization.json", original_initialization),
        )
    ):
        snapshot = journal_path.with_name(
            f"{journal_path.name}.{transaction_id}.{index}.rollback"
        )
        snapshot.write_bytes(content)
        snapshots.append(snapshot)
        entries.append(
            {
                "path": logical_path,
                "had_original": True,
                "snapshot": snapshot.name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
        )
    journal_path.write_text(
        json.dumps(
            {"format_version": 1, "transaction_id": transaction_id, "entries": entries},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return journal_path, (snapshots[0], snapshots[1])


def test_backup_writes_versioned_manifest_and_only_explicitly_managed_files(tmp_path) -> None:
    manager, paths = _manager(tmp_path)
    archive = tmp_path / "local-backup.zip"

    manifest = manager.create_backup(archive)

    assert manifest.format_version == 1
    assert {item.path for item in manifest.files} == {
        "account-ledger.jsonl",
        "model-notes.json",
    }
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {
            "backup-manifest.json",
            "managed/account-ledger.jsonl",
            "managed/model-notes.json",
        }
        assert "must not be archived" not in bundle.read("managed/account-ledger.jsonl").decode(
            "utf-8"
        )
    assert paths["unmanaged"].read_text(encoding="utf-8") == "must not be archived"


def test_restore_preflight_rejects_hash_tampering_and_zip_traversal(tmp_path) -> None:
    manager, _ = _manager(tmp_path)
    archive = tmp_path / "local-backup.zip"
    manager.create_backup(archive)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "managed/account-ledger.jsonl":
                content = b"tampered"
            target.writestr(item, content)
    with pytest.raises(ValueError, match="hash"):
        manager.preflight_restore(tampered)

    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as bundle:
        bundle.writestr("../outside.txt", "not allowed")
    with pytest.raises(ValueError, match="traversal"):
        manager.preflight_restore(traversal)


def test_restore_needs_preflight_confirmation_and_audits_before_replacement(tmp_path) -> None:
    manager, paths = _manager(tmp_path)
    archive = tmp_path / "local-backup.zip"
    manager.create_backup(archive)
    preflight = manager.preflight_restore(archive)

    paths["account_ledger"].write_text('{"changed":true}\n', encoding="utf-8")
    paths["model_notes"].write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="confirmation"):
        manager.restore(archive, confirmation_token="not-the-preflight-token")
    assert paths["account_ledger"].read_text(encoding="utf-8") == '{"changed":true}\n'

    receipt = manager.restore(archive, confirmation_token=preflight.confirmation_token)

    assert receipt.restored_files == ("account-ledger.jsonl", "model-notes.json")
    assert paths["account_ledger"].read_text(encoding="utf-8") == '{"kind":"fill"}\n'
    audit_rows = [
        json.loads(line)
        for line in paths["audit"].read_text(encoding="utf-8").splitlines()
    ]
    assert audit_rows[-1]["action"] == "restore"
    assert audit_rows[-1]["phase"] == "before_replacement"


def test_backup_includes_explicit_account_initialization_metadata(tmp_path) -> None:
    ledger_path = tmp_path / "account-ledger.jsonl"
    service = AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=ledger_path,
        today=lambda: date(2026, 8, 10),
    )
    preview = service.preview_manual_buy(name="平安银行", code="000001", quantity=100, price=10)
    service.confirm_manual_buy(preview["confirmation_token"])
    manager = LocalBackupManager(
        managed_files=service.managed_local_files(),
        consistency_groups=service.managed_local_file_consistency_groups(),
        audit_path=tmp_path / "restore-audit.jsonl",
    )

    manifest = manager.create_backup(tmp_path / "local-backup.zip")

    assert {item.path for item in manifest.files} == {
        "account-ledger.jsonl",
        "account-ledger.jsonl.initialization.json",
    }


def test_restore_preflight_rejects_ledger_only_archive_for_account_state_pair(tmp_path) -> None:
    source_ledger = tmp_path / "source" / "account-ledger.jsonl"
    source = AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=source_ledger,
        today=lambda: date(2026, 8, 10),
    )
    preview = source.preview_manual_buy(
        name="平安银行", code="000001", quantity=100, price=10
    )
    source.confirm_manual_buy(preview["confirmation_token"])
    legacy_manager = LocalBackupManager(
        managed_files={"account-ledger.jsonl": source_ledger},
        audit_path=tmp_path / "source" / "restore-audit.jsonl",
    )
    ledger_only_archive = tmp_path / "ledger-only-v1.zip"
    legacy_manager.create_backup(ledger_only_archive)

    target_ledger = tmp_path / "target" / "account-ledger.jsonl"
    target = AdvisoryWorkbenchService(
        initial_cash=Decimal("200000"),
        ledger_path=target_ledger,
        today=lambda: date(2026, 8, 10),
    )
    target_preview = target.preview_manual_buy(
        name="平安银行", code="000001", quantity=100, price=10
    )
    target.confirm_manual_buy(target_preview["confirmation_token"])
    assert target.managed_local_files()[
        "account-ledger.jsonl.initialization.json"
    ].exists()
    manager = LocalBackupManager(
        managed_files=target.managed_local_files(),
        consistency_groups=target.managed_local_file_consistency_groups(),
        audit_path=tmp_path / "target" / "restore-audit.jsonl",
    )

    with pytest.raises(ValueError, match="consistency group"):
        manager.preflight_restore(ledger_only_archive)


def test_unconfirmed_initial_account_state_has_no_partial_backup_pair(tmp_path) -> None:
    service = AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=tmp_path / "account-ledger.jsonl",
        today=lambda: date(2026, 8, 10),
    )
    manager = LocalBackupManager(
        managed_files=service.managed_local_files(),
        consistency_groups=service.managed_local_file_consistency_groups(),
        audit_path=tmp_path / "restore-audit.jsonl",
    )

    manifest = manager.create_backup(tmp_path / "uninitialized.zip")

    assert manifest.files == ()
    assert not service.managed_local_files()[
        "account-ledger.jsonl.initialization.json"
    ].exists()


def test_restore_rolls_back_account_group_after_later_replacement_failure(
    tmp_path, monkeypatch
) -> None:
    ledger = tmp_path / "account-ledger.jsonl"
    initialization = tmp_path / "account-ledger.jsonl.initialization.json"
    supplemental = tmp_path / "supplemental.txt"
    failing = tmp_path / "zzz-failing.txt"
    managed_files = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
        "supplemental.txt": supplemental,
        "zzz-failing.txt": failing,
    }
    manager = LocalBackupManager(
        managed_files=managed_files,
        consistency_groups={
            "account-state": (
                "account-ledger.jsonl",
                "account-ledger.jsonl.initialization.json",
            )
        },
        audit_path=tmp_path / "restore-audit.jsonl",
    )
    ledger.write_bytes(b"archived-ledger")
    initialization.write_bytes(b"archived-initialization")
    supplemental.write_bytes(b"archived-supplemental")
    failing.write_bytes(b"archived-failing")
    archive = tmp_path / "account-backup.zip"
    manager.create_backup(archive)

    ledger.write_bytes(b"original-ledger")
    initialization.write_bytes(b"original-initialization")
    supplemental.unlink()
    failing.write_bytes(b"original-failing")
    preflight = manager.preflight_restore(archive)
    real_replace = os.replace
    injected = False

    def fail_later_replacement(source, destination) -> None:
        nonlocal injected
        if not injected and destination == failing:
            injected = True
            raise OSError("injected replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr("a_share_quant.workbench.backup.os.replace", fail_later_replacement)

    with pytest.raises(OSError, match="injected replacement failure"):
        manager.restore(archive, confirmation_token=preflight.confirmation_token)

    assert ledger.read_bytes() == b"original-ledger"
    assert initialization.read_bytes() == b"original-initialization"
    assert not supplemental.exists()
    assert failing.read_bytes() == b"original-failing"


def test_concurrent_restores_are_serialized_and_leave_one_coherent_archive_state(
    tmp_path, monkeypatch
) -> None:
    ledger = tmp_path / "account-ledger.jsonl"
    initialization = tmp_path / "account-ledger.jsonl.initialization.json"
    manager = LocalBackupManager(
        managed_files={
            "account-ledger.jsonl": ledger,
            "account-ledger.jsonl.initialization.json": initialization,
        },
        consistency_groups={
            "account-state": (
                "account-ledger.jsonl",
                "account-ledger.jsonl.initialization.json",
            )
        },
        audit_path=tmp_path / "restore-audit.jsonl",
    )
    ledger.write_bytes(b"archive-a-ledger")
    initialization.write_bytes(b"archive-a-initialization")
    archive_a = tmp_path / "archive-a.zip"
    manager.create_backup(archive_a)
    ledger.write_bytes(b"archive-b-ledger")
    initialization.write_bytes(b"archive-b-initialization")
    archive_b = tmp_path / "archive-b.zip"
    manager.create_backup(archive_b)
    ledger.write_bytes(b"starting-ledger")
    initialization.write_bytes(b"starting-initialization")
    preflight_a = manager.preflight_restore(archive_a)
    preflight_b = manager.preflight_restore(archive_b)

    real_replace = os.replace
    first_replacement = threading.Event()
    release_first_restore = threading.Event()
    paused = False

    def pause_after_first_archive_ledger(source, destination) -> None:
        nonlocal paused
        if (
            not paused
            and destination == ledger
            and source.read_bytes() == b"archive-a-ledger"
        ):
            paused = True
            real_replace(source, destination)
            first_replacement.set()
            if not release_first_restore.wait(timeout=3):
                raise RuntimeError("test did not release the first restore")
            return
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        pause_after_first_archive_ledger,
    )
    second_finished = threading.Event()

    def restore_second_archive():
        try:
            return manager.restore(archive_b, confirmation_token=preflight_b.confirmation_token)
        finally:
            second_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_restore = executor.submit(
            manager.restore,
            archive_a,
            confirmation_token=preflight_a.confirmation_token,
        )
        assert first_replacement.wait(timeout=3)
        second_restore = executor.submit(restore_second_archive)
        try:
            assert not second_finished.wait(timeout=0.2)
        finally:
            release_first_restore.set()
        first_restore.result(timeout=3)
        second_restore.result(timeout=3)

    assert (ledger.read_bytes(), initialization.read_bytes()) in {
        (b"archive-a-ledger", b"archive-a-initialization"),
        (b"archive-b-ledger", b"archive-b-initialization"),
    }


def test_separate_managers_wait_for_an_active_restore_journal(tmp_path, monkeypatch) -> None:
    ledger = tmp_path / "account-ledger.jsonl"
    initialization = tmp_path / "account-ledger.jsonl.initialization.json"
    audit_path = tmp_path / "restore-audit.jsonl"
    managed_files = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
    }
    consistency_groups = {
        "account-state": (
            "account-ledger.jsonl",
            "account-ledger.jsonl.initialization.json",
        )
    }
    manager_a = LocalBackupManager(
        managed_files=managed_files,
        consistency_groups=consistency_groups,
        audit_path=audit_path,
    )
    archived_state = (b"archived-ledger", b"archived-initialization")
    original_state = (b"original-ledger", b"original-initialization")
    ledger.write_bytes(archived_state[0])
    initialization.write_bytes(archived_state[1])
    restore_archive = tmp_path / "restore-source.zip"
    manager_a.create_backup(restore_archive)
    ledger.write_bytes(original_state[0])
    initialization.write_bytes(original_state[1])
    preflight = manager_a.preflight_restore(restore_archive)

    real_replace = os.replace
    first_replacement = threading.Event()
    release_restore = threading.Event()
    paused = False

    def pause_after_ledger_replacement(source, destination) -> None:
        nonlocal paused
        if not paused and destination == ledger and source.read_bytes() == archived_state[0]:
            paused = True
            real_replace(source, destination)
            first_replacement.set()
            if not release_restore.wait(timeout=3):
                raise RuntimeError("test did not release the first restore")
            return
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        pause_after_ledger_replacement,
    )
    manager_b_started = threading.Event()
    manager_b_finished = threading.Event()
    manager_b_archive = tmp_path / "manager-b-backup.zip"

    def construct_and_use_manager_b() -> None:
        manager_b_started.set()
        try:
            manager_b = LocalBackupManager(
                managed_files=managed_files,
                consistency_groups=consistency_groups,
                audit_path=audit_path,
            )
            manager_b.create_backup(manager_b_archive)
        finally:
            manager_b_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        restore_future = executor.submit(
            manager_a.restore,
            restore_archive,
            confirmation_token=preflight.confirmation_token,
        )
        assert first_replacement.wait(timeout=3)
        manager_b_future = executor.submit(construct_and_use_manager_b)
        assert manager_b_started.wait(timeout=3)
        try:
            assert not manager_b_finished.wait(timeout=0.2)
            assert _restore_journal_path(audit_path).exists()
            assert (ledger.read_bytes(), initialization.read_bytes()) == (
                archived_state[0],
                original_state[1],
            )
        finally:
            release_restore.set()
        restore_future.result(timeout=3)
        manager_b_future.result(timeout=3)

    assert (ledger.read_bytes(), initialization.read_bytes()) == archived_state
    assert not _restore_journal_path(audit_path).exists()
    with zipfile.ZipFile(manager_b_archive) as bundle:
        assert (
            bundle.read("managed/account-ledger.jsonl"),
            bundle.read("managed/account-ledger.jsonl.initialization.json"),
        ) == archived_state


def test_managers_with_different_audits_share_destination_restore_lock(
    tmp_path, monkeypatch
) -> None:
    ledger = tmp_path / "account-ledger.jsonl"
    initialization = tmp_path / "account-ledger.jsonl.initialization.json"
    managed_files = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
    }
    consistency_groups = {
        "account-state": (
            "account-ledger.jsonl",
            "account-ledger.jsonl.initialization.json",
        )
    }
    manager_a = LocalBackupManager(
        managed_files=managed_files,
        consistency_groups=consistency_groups,
        audit_path=tmp_path / "restore-audit-a.jsonl",
    )
    manager_b = LocalBackupManager(
        managed_files=managed_files,
        consistency_groups=consistency_groups,
        audit_path=tmp_path / "restore-audit-b.jsonl",
    )
    archived_state = (b"archived-ledger", b"archived-initialization")
    original_state = (b"original-ledger", b"original-initialization")
    ledger.write_bytes(archived_state[0])
    initialization.write_bytes(archived_state[1])
    restore_archive = tmp_path / "restore-source.zip"
    manager_a.create_backup(restore_archive)
    ledger.write_bytes(original_state[0])
    initialization.write_bytes(original_state[1])
    preflight = manager_a.preflight_restore(restore_archive)

    real_replace = os.replace
    first_replacement = threading.Event()
    release_restore = threading.Event()
    paused = False

    def pause_after_ledger_replacement(source, destination) -> None:
        nonlocal paused
        if not paused and destination == ledger and source.read_bytes() == archived_state[0]:
            paused = True
            real_replace(source, destination)
            first_replacement.set()
            if not release_restore.wait(timeout=3):
                raise RuntimeError("test did not release the first restore")
            return
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        pause_after_ledger_replacement,
    )
    backup_archive = tmp_path / "manager-b-backup.zip"
    backup_started = threading.Event()
    backup_finished = threading.Event()

    def create_manager_b_backup() -> None:
        backup_started.set()
        try:
            manager_b.create_backup(backup_archive)
        finally:
            backup_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        restore_future = executor.submit(
            manager_a.restore,
            restore_archive,
            confirmation_token=preflight.confirmation_token,
        )
        assert first_replacement.wait(timeout=3)
        backup_future = executor.submit(create_manager_b_backup)
        assert backup_started.wait(timeout=3)
        try:
            assert not backup_finished.wait(timeout=0.2)
            assert (ledger.read_bytes(), initialization.read_bytes()) == (
                archived_state[0],
                original_state[1],
            )
        finally:
            release_restore.set()
        restore_future.result(timeout=3)
        backup_future.result(timeout=3)

    with zipfile.ZipFile(backup_archive) as bundle:
        assert (
            bundle.read("managed/account-ledger.jsonl"),
            bundle.read("managed/account-ledger.jsonl.initialization.json"),
        ) == archived_state


def test_partial_destination_overlap_serializes_across_process_temp_directories(
    tmp_path, monkeypatch
) -> None:
    temporary_directory_a = tmp_path / "temporary-a"
    temporary_directory_b = tmp_path / "temporary-b"
    temporary_directory_a.mkdir()
    temporary_directory_b.mkdir()
    ledger = tmp_path / "account-ledger.jsonl"
    initialization = tmp_path / "account-ledger.jsonl.initialization.json"
    managed_pair = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
    }
    original_tempdir = tempfile.tempdir
    try:
        monkeypatch.setenv("TMP", str(temporary_directory_a))
        monkeypatch.setenv("TEMP", str(temporary_directory_a))
        tempfile.tempdir = None
        manager_a = LocalBackupManager(
            managed_files=managed_pair,
            consistency_groups={
                "account-state": (
                    "account-ledger.jsonl",
                    "account-ledger.jsonl.initialization.json",
                )
            },
            audit_path=tmp_path / "restore-audit-a.jsonl",
        )
        monkeypatch.setenv("TMP", str(temporary_directory_b))
        monkeypatch.setenv("TEMP", str(temporary_directory_b))
        tempfile.tempdir = None
        manager_b = LocalBackupManager(
            managed_files={"account-ledger.jsonl": ledger},
            audit_path=tmp_path / "restore-audit-b.jsonl",
        )
        archived_state = (b"archived-ledger", b"archived-initialization")
        original_state = (b"original-ledger", b"original-initialization")
        ledger.write_bytes(archived_state[0])
        initialization.write_bytes(archived_state[1])
        restore_archive = tmp_path / "restore-source.zip"
        manager_a.create_backup(restore_archive)
        ledger.write_bytes(original_state[0])
        initialization.write_bytes(original_state[1])
        preflight = manager_a.preflight_restore(restore_archive)

        real_replace = os.replace
        first_replacement = threading.Event()
        release_restore = threading.Event()
        paused = False

        def pause_after_ledger_replacement(source, destination) -> None:
            nonlocal paused
            if not paused and destination == ledger and source.read_bytes() == archived_state[0]:
                paused = True
                real_replace(source, destination)
                first_replacement.set()
                if not release_restore.wait(timeout=3):
                    raise RuntimeError("test did not release the first restore")
                return
            real_replace(source, destination)

        monkeypatch.setattr(
            "a_share_quant.workbench.backup.os.replace",
            pause_after_ledger_replacement,
        )
        backup_archive = tmp_path / "partial-overlap-backup.zip"
        backup_started = threading.Event()
        backup_finished = threading.Event()

        def create_partial_overlap_backup() -> None:
            backup_started.set()
            try:
                manager_b.create_backup(backup_archive)
            finally:
                backup_finished.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            restore_future = executor.submit(
                manager_a.restore,
                restore_archive,
                confirmation_token=preflight.confirmation_token,
            )
            assert first_replacement.wait(timeout=3)
            backup_future = executor.submit(create_partial_overlap_backup)
            assert backup_started.wait(timeout=3)
            try:
                assert not backup_finished.wait(timeout=0.2)
            finally:
                release_restore.set()
            restore_future.result(timeout=3)
            backup_future.result(timeout=3)
    finally:
        tempfile.tempdir = original_tempdir

    with zipfile.ZipFile(backup_archive) as bundle:
        assert bundle.read("managed/account-ledger.jsonl") == archived_state[0]


def test_different_destination_sets_with_shared_audit_serialize_restores(
    tmp_path, monkeypatch
) -> None:
    ledger = tmp_path / "account-ledger.jsonl"
    model_notes = tmp_path / "model-notes.json"
    audit_path = tmp_path / "restore-audit.jsonl"
    manager_a = LocalBackupManager(
        managed_files={"account-ledger.jsonl": ledger},
        audit_path=audit_path,
    )
    manager_b = LocalBackupManager(
        managed_files={"model-notes.json": model_notes},
        audit_path=audit_path,
    )
    archived_ledger = b"archived-ledger"
    archived_notes = b"archived-notes"
    ledger.write_bytes(archived_ledger)
    model_notes.write_bytes(archived_notes)
    archive_a = tmp_path / "restore-a.zip"
    archive_b = tmp_path / "restore-b.zip"
    manager_a.create_backup(archive_a)
    manager_b.create_backup(archive_b)
    ledger.write_bytes(b"original-ledger")
    model_notes.write_bytes(b"original-notes")
    preflight_a = manager_a.preflight_restore(archive_a)
    preflight_b = manager_b.preflight_restore(archive_b)

    real_replace = os.replace
    first_replacement = threading.Event()
    release_restore = threading.Event()
    paused = False

    def pause_after_ledger_replacement(source, destination) -> None:
        nonlocal paused
        if not paused and destination == ledger and source.read_bytes() == archived_ledger:
            paused = True
            real_replace(source, destination)
            first_replacement.set()
            if not release_restore.wait(timeout=3):
                raise RuntimeError("test did not release the first restore")
            return
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        pause_after_ledger_replacement,
    )
    second_restore_started = threading.Event()
    second_restore_finished = threading.Event()

    def restore_manager_b() -> None:
        second_restore_started.set()
        try:
            manager_b.restore(
                archive_b,
                confirmation_token=preflight_b.confirmation_token,
            )
        finally:
            second_restore_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_restore = executor.submit(
            manager_a.restore,
            archive_a,
            confirmation_token=preflight_a.confirmation_token,
        )
        assert first_replacement.wait(timeout=3)
        second_restore = executor.submit(restore_manager_b)
        assert second_restore_started.wait(timeout=3)
        try:
            assert not second_restore_finished.wait(timeout=0.2)
        finally:
            release_restore.set()
        first_restore.result(timeout=3)
        second_restore.result(timeout=3)

    assert ledger.read_bytes() == archived_ledger
    assert model_notes.read_bytes() == archived_notes
    audit_rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["action"] for row in audit_rows] == ["restore", "restore"]


def test_coordination_locks_use_one_canonical_acquisition_order(tmp_path) -> None:
    ledger = tmp_path / "z-state" / "account-ledger.jsonl"
    initialization = tmp_path / "y-state" / "account-ledger.jsonl.initialization.json"
    audit_path = tmp_path / "a-audit" / "restore-audit.jsonl"
    manager = LocalBackupManager(
        managed_files={
            "account-ledger.jsonl": ledger,
            "account-ledger.jsonl.initialization.json": initialization,
        },
        audit_path=audit_path,
    )

    expected_paths = tuple(
        sorted(
            (*manager._destination_lock_paths, manager._restore_lock_path),
            key=_canonical_destination,
        )
    )
    assert manager._coordination_lock_paths == expected_paths


def test_restore_uses_constructor_resolved_paths_after_working_directory_changes(
    tmp_path, monkeypatch
) -> None:
    cwd_a = tmp_path / "cwd-a"
    cwd_b = tmp_path / "cwd-b"
    ledger_a = cwd_a / "state" / "account-ledger.jsonl"
    initialization_a = cwd_a / "state" / "account-ledger.jsonl.initialization.json"
    audit_a = cwd_a / "audit" / "restore-audit.jsonl"
    ledger_b = cwd_b / "state" / "account-ledger.jsonl"
    initialization_b = cwd_b / "state" / "account-ledger.jsonl.initialization.json"
    audit_b = cwd_b / "audit" / "restore-audit.jsonl"
    ledger_a.parent.mkdir(parents=True)
    audit_a.parent.mkdir(parents=True)
    ledger_b.parent.mkdir(parents=True)
    audit_b.parent.mkdir(parents=True)
    archived_state = (b"archived-ledger", b"archived-initialization")
    original_state = (b"original-ledger", b"original-initialization")
    other_directory_state = (b"other-ledger", b"other-initialization")
    ledger_a.write_bytes(archived_state[0])
    initialization_a.write_bytes(archived_state[1])
    ledger_b.write_bytes(other_directory_state[0])
    initialization_b.write_bytes(other_directory_state[1])
    archive = cwd_a / "archives" / "restore-source.zip"

    monkeypatch.chdir(cwd_a)
    manager = LocalBackupManager(
        managed_files={
            "account-ledger.jsonl": Path("state/account-ledger.jsonl"),
            "account-ledger.jsonl.initialization.json": Path(
                "state/account-ledger.jsonl.initialization.json"
            ),
        },
        consistency_groups={
            "account-state": (
                "account-ledger.jsonl",
                "account-ledger.jsonl.initialization.json",
            )
        },
        audit_path=Path("audit/restore-audit.jsonl"),
    )
    manager.create_backup(archive)
    ledger_a.write_bytes(original_state[0])
    initialization_a.write_bytes(original_state[1])
    preflight = manager.preflight_restore(archive)

    monkeypatch.chdir(cwd_b)
    manager.restore(archive, confirmation_token=preflight.confirmation_token)

    assert (ledger_a.read_bytes(), initialization_a.read_bytes()) == archived_state
    assert (ledger_b.read_bytes(), initialization_b.read_bytes()) == other_directory_state
    assert audit_a.exists()
    assert not audit_b.exists()
    assert not _restore_journal_path(audit_a).exists()
    assert not _restore_journal_path(audit_b).exists()


def test_backup_manager_rejects_audit_log_destination_path_alias(tmp_path) -> None:
    audit_path = tmp_path / "restore-audit.jsonl"
    audit_path.write_text("existing audit entry\n", encoding="utf-8")
    audit_alias = tmp_path / "nested" / ".." / "restore-audit.jsonl"

    with pytest.raises(ValueError, match="audit"):
        LocalBackupManager(
            managed_files={"model-notes.json": audit_alias},
            audit_path=audit_path,
        )

    assert audit_path.read_text(encoding="utf-8") == "existing audit entry\n"


def test_backup_manager_rejects_duplicate_managed_destination_path_aliases(tmp_path) -> None:
    destination = tmp_path / "model-notes.json"
    destination_alias = tmp_path / "nested" / ".." / "model-notes.json"

    with pytest.raises(ValueError, match="duplicate"):
        LocalBackupManager(
            managed_files={
                "model-notes.json": destination,
                "model-notes-copy.json": destination_alias,
            },
            audit_path=tmp_path / "restore-audit.jsonl",
        )


def test_backup_manager_rejects_managed_file_symlink_before_backup_or_restore(tmp_path) -> None:
    external_file = tmp_path / "external-model-notes.json"
    managed_symlink = tmp_path / "model-notes.json"
    external_file.write_text('{"model":"external"}\n', encoding="utf-8")
    try:
        managed_symlink.symlink_to(external_file)
    except OSError as exc:
        pytest.skip(f"creating a file symlink is unavailable: {exc}")

    assert managed_symlink.is_symlink()
    with pytest.raises(ValueError, match="symbolic link"):
        LocalBackupManager(
            managed_files={"model-notes.json": managed_symlink},
            audit_path=tmp_path / "restore-audit.jsonl",
        )

    assert external_file.read_text(encoding="utf-8") == '{"model":"external"}\n'


def test_backup_manager_rejects_managed_path_below_directory_junction(tmp_path) -> None:
    external_directory = tmp_path / "external-state"
    junction_directory = tmp_path / "linked-state"
    external_ledger = external_directory / "account-ledger.jsonl"
    external_directory.mkdir()
    external_ledger.write_text('{"kind":"external"}\n', encoding="utf-8")
    try:
        result = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                str(junction_directory),
                str(external_directory),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        pytest.skip(f"creating a directory junction is unavailable: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        pytest.skip(f"creating a directory junction is unavailable: {detail}")

    is_junction = getattr(junction_directory, "is_junction", None)
    if is_junction is not None:
        assert is_junction()
    managed_ledger = junction_directory / "account-ledger.jsonl"
    assert not managed_ledger.is_symlink()
    try:
        with pytest.raises(ValueError, match="symbolic link or junction"):
            LocalBackupManager(
                managed_files={"account-ledger.jsonl": managed_ledger},
                audit_path=tmp_path / "restore-audit.jsonl",
            )
    finally:
        junction_directory.rmdir()

    assert external_ledger.read_text(encoding="utf-8") == '{"kind":"external"}\n'


def test_backup_manager_accepts_managed_path_with_elided_junction_segment(tmp_path) -> None:
    safe_directory = tmp_path / "safe-state"
    external_directory = tmp_path / "outside" / "external-state"
    junction_directory = tmp_path / "linked-state"
    safe_ledger = safe_directory / "account-ledger.jsonl"
    safe_directory.mkdir()
    external_directory.mkdir(parents=True)
    safe_ledger.write_bytes(b'{"kind":"safe"}\n')
    try:
        result = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                str(junction_directory),
                str(external_directory),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        pytest.skip(f"creating a directory junction is unavailable: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        pytest.skip(f"creating a directory junction is unavailable: {detail}")

    archive = tmp_path / "safe-backup.zip"
    managed_ledger = junction_directory / ".." / "safe-state" / "account-ledger.jsonl"
    try:
        manager = LocalBackupManager(
            managed_files={"account-ledger.jsonl": managed_ledger},
            audit_path=tmp_path / "restore-audit.jsonl",
        )
        manager.create_backup(archive)
    finally:
        junction_directory.rmdir()

    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("managed/account-ledger.jsonl") == b'{"kind":"safe"}\n'


def test_backup_rejects_directory_junction_swap_before_external_access(tmp_path) -> None:
    safe_root = tmp_path / "safe-root"
    safe_state_directory = safe_root / "state"
    safe_audit_directory = safe_root / "audit"
    safe_ledger = safe_state_directory / "account-ledger.jsonl"
    safe_audit_path = safe_audit_directory / "restore-audit.jsonl"
    safe_state_directory.mkdir(parents=True)
    safe_audit_directory.mkdir(parents=True)
    safe_ledger.write_text('{"kind":"safe"}\n', encoding="utf-8")
    manager = LocalBackupManager(
        managed_files={"account-ledger.jsonl": safe_ledger},
        audit_path=safe_audit_path,
    )

    external_root = tmp_path / "external-root"
    external_ledger = external_root / "state" / "account-ledger.jsonl"
    external_audit_path = external_root / "audit" / "restore-audit.jsonl"
    external_ledger.parent.mkdir(parents=True)
    external_audit_path.parent.mkdir(parents=True)
    external_ledger.write_text('{"kind":"external"}\n', encoding="utf-8")
    safe_ledger.unlink()
    _destination_restore_lock_path(safe_ledger).unlink(missing_ok=True)
    _pending_restore_marker_path(safe_ledger).unlink(missing_ok=True)
    for path in safe_audit_directory.iterdir():
        path.unlink()
    safe_state_directory.rmdir()
    safe_audit_directory.rmdir()
    safe_root.rmdir()
    try:
        result = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                str(safe_root),
                str(external_root),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        pytest.skip(f"creating a directory junction is unavailable: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        pytest.skip(f"creating a directory junction is unavailable: {detail}")

    archive = tmp_path / "backup.zip"
    external_journal_path = _restore_journal_path(external_audit_path)
    external_lock_path = external_journal_path.with_name(f"{external_journal_path.name}.lock")
    try:
        with pytest.raises(ValueError, match="symbolic link or junction"):
            manager.create_backup(archive)
    finally:
        safe_root.rmdir()

    assert external_ledger.read_text(encoding="utf-8") == '{"kind":"external"}\n'
    assert not archive.exists()
    assert not external_audit_path.exists()
    assert not external_journal_path.exists()
    assert not external_lock_path.exists()


def test_backup_revalidates_paths_after_waiting_for_durable_lock(tmp_path) -> None:
    anchor_directory = tmp_path / "anchor-state"
    anchor_path = anchor_directory / "anchor.json"
    safe_state_directory = tmp_path / "safe-state"
    safe_ledger = safe_state_directory / "account-ledger.jsonl"
    audit_path = tmp_path / "audit" / "restore-audit.jsonl"
    anchor_directory.mkdir()
    safe_state_directory.mkdir()
    audit_path.parent.mkdir()
    anchor_path.write_text('{"kind":"anchor"}\n', encoding="utf-8")
    safe_ledger.write_text('{"kind":"safe"}\n', encoding="utf-8")
    manager = LocalBackupManager(
        managed_files={
            "account-ledger.jsonl": safe_ledger,
            "anchor.json": anchor_path,
        },
        audit_path=audit_path,
    )

    external_state_directory = tmp_path / "external-state"
    external_ledger = external_state_directory / "account-ledger.jsonl"
    external_state_directory.mkdir()
    external_ledger.write_text('{"kind":"external"}\n', encoding="utf-8")
    archive = safe_state_directory / "backup.zip"
    external_archive = external_state_directory / "backup.zip"

    context = multiprocessing.get_context("spawn")
    lock_acquired = context.Event()
    release_lock = context.Event()
    lock_holder = context.Process(
        target=_hold_durable_restore_lock,
        args=(str(_destination_restore_lock_path(anchor_path)), lock_acquired, release_lock),
    )
    backup_started = threading.Event()
    backup_finished = threading.Event()

    def create_backup() -> None:
        backup_started.set()
        try:
            manager.create_backup(archive)
        finally:
            backup_finished.set()

    lock_holder.start()
    try:
        assert lock_acquired.wait(timeout=3)
        with ThreadPoolExecutor(max_workers=1) as executor:
            backup_future = executor.submit(create_backup)
            assert backup_started.wait(timeout=3)
            assert not backup_finished.wait(timeout=0.2)
            safe_ledger.unlink()
            _destination_restore_lock_path(safe_ledger).unlink(missing_ok=True)
            _pending_restore_marker_path(safe_ledger).unlink(missing_ok=True)
            safe_state_directory.rmdir()
            try:
                result = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(safe_state_directory),
                        str(external_state_directory),
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                )
            except OSError as exc:
                pytest.skip(f"creating a directory junction is unavailable: {exc}")
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                pytest.skip(f"creating a directory junction is unavailable: {detail}")
            release_lock.set()
            with pytest.raises(ValueError, match="symbolic link or junction"):
                backup_future.result(timeout=3)
    finally:
        release_lock.set()
        lock_holder.join(timeout=3)
        if lock_holder.is_alive():
            lock_holder.terminate()
            lock_holder.join(timeout=3)
        if safe_state_directory.exists() and safe_state_directory.is_dir():
            is_junction = getattr(safe_state_directory, "is_junction", None)
            if is_junction is not None and is_junction():
                safe_state_directory.rmdir()
            else:
                for path in safe_state_directory.iterdir():
                    path.unlink()
                safe_state_directory.rmdir()

    assert external_ledger.read_text(encoding="utf-8") == '{"kind":"external"}\n'
    assert not external_archive.exists()
    assert not audit_path.exists()


@pytest.mark.parametrize(
    "artifact",
    ("audit", "journal", "audit lock", "destination lock", "pending marker"),
)
def test_backup_rejects_archive_alias_to_protected_restore_artifact(tmp_path, artifact) -> None:
    ledger = tmp_path / "account-ledger.jsonl"
    audit_path = tmp_path / "restore-audit.jsonl"
    ledger.write_text('{"kind":"safe"}\n', encoding="utf-8")
    if artifact == "audit":
        audit_path.write_bytes(b"original audit")
    manager = LocalBackupManager(
        managed_files={"account-ledger.jsonl": ledger},
        audit_path=audit_path,
    )
    protected_path = {
        "audit": audit_path,
        "journal": _restore_journal_path(audit_path),
        "audit lock": _audit_restore_lock_path(audit_path),
        "destination lock": _destination_restore_lock_path(ledger),
        "pending marker": _pending_restore_marker_path(ledger),
    }[artifact]
    original_content = protected_path.read_bytes() if protected_path.exists() else None
    archive_alias = protected_path.parent / "archive-alias" / ".." / protected_path.name

    with pytest.raises(ValueError, match="backup archive cannot replace a protected file"):
        manager.create_backup(archive_alias)

    assert protected_path.exists() is (original_content is not None)
    if original_content is not None:
        assert protected_path.read_bytes() == original_content


@pytest.mark.parametrize(
    "artifact",
    (
        "pending marker",
        "destination lock",
        "journal",
        "audit lock",
        "journal rollback",
    ),
)
def test_backup_rejects_other_manager_coordination_sidecar_archive_target(
    tmp_path, artifact
) -> None:
    owner_target = tmp_path / "owner" / "account-ledger.jsonl"
    owner = LocalBackupManager(
        managed_files={"account-ledger.jsonl": owner_target},
        audit_path=tmp_path / "owner" / "restore-audit.jsonl",
    )
    owner_artifact = {
        "pending marker": next(iter(owner._destination_marker_paths.values())),
        "destination lock": owner._destination_lock_paths[0],
        "journal": owner._journal_path,
        "audit lock": owner._restore_lock_path,
        "journal rollback": owner._journal_path.with_name(
            f"{owner._journal_path.name}.restore-{'a' * 32}.0.rollback"
        ),
    }[artifact]
    original_content = f"owner-{artifact}".encode()
    owner_artifact.write_bytes(original_content)
    independent_target = tmp_path / "independent" / "model-notes.json"
    independent_target.parent.mkdir()
    independent_target.write_text('{"model":"independent"}\n', encoding="utf-8")
    independent = LocalBackupManager(
        managed_files={"model-notes.json": independent_target},
        audit_path=tmp_path / "independent" / "restore-audit.jsonl",
    )
    archive_alias = owner_artifact.parent / "alias" / ".." / owner_artifact.name

    with pytest.raises(ValueError, match="reserved backup coordination sidecar"):
        independent.create_backup(archive_alias)

    assert owner_artifact.read_bytes() == original_content


@pytest.mark.parametrize(
    "artifact",
    (
        "pending marker",
        "destination lock",
        "journal",
        "audit lock",
        "journal rollback",
    ),
)
@pytest.mark.parametrize("declared_as", ("managed target", "audit path"))
def test_manager_rejects_other_configuration_reserved_sidecar_path(
    tmp_path, artifact, declared_as
) -> None:
    owner_target = tmp_path / "owner" / "account-ledger.jsonl"
    owner_audit = tmp_path / "owner" / "restore-audit.jsonl"
    owner = LocalBackupManager(
        managed_files={"account-ledger.jsonl": owner_target},
        audit_path=owner_audit,
    )
    reserved_path = {
        "pending marker": next(iter(owner._destination_marker_paths.values())),
        "destination lock": owner._destination_lock_paths[0],
        "journal": owner._journal_path,
        "audit lock": owner._restore_lock_path,
        "journal rollback": owner._journal_path.with_name(
            f"{owner._journal_path.name}.restore-{'a' * 32}.0.rollback"
        ),
    }[artifact]

    kwargs = {
        "managed_files": {"model-notes.json": tmp_path / "other" / "model-notes.json"},
        "audit_path": tmp_path / "other" / "restore-audit.jsonl",
    }
    if declared_as == "managed target":
        kwargs["managed_files"] = {"model-notes.json": reserved_path}
    else:
        kwargs["audit_path"] = reserved_path

    with pytest.raises(ValueError, match="reserved backup coordination sidecar"):
        LocalBackupManager(**kwargs)


def test_manager_allows_non_coordination_json_and_lock_names(tmp_path) -> None:
    manager = LocalBackupManager(
        managed_files={"model-notes.json": tmp_path / "model-notes.json"},
        audit_path=tmp_path / "restore-audit.lock",
    )

    assert manager._audit_path.name == "restore-audit.lock"


def test_backup_manager_checks_declared_path_for_symlink_before_resolution(
    tmp_path, monkeypatch
) -> None:
    declared_managed_path = tmp_path / "declared-model-notes.json"
    real_is_symlink = Path.is_symlink

    def report_declared_path_as_symlink(path: Path) -> bool:
        return path == declared_managed_path or real_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", report_declared_path_as_symlink)

    with pytest.raises(ValueError, match="symbolic link"):
        LocalBackupManager(
            managed_files={"model-notes.json": declared_managed_path},
            audit_path=tmp_path / "restore-audit.jsonl",
        )


def test_restore_recovers_account_pair_after_keyboard_interrupt(tmp_path, monkeypatch) -> None:
    manager, ledger, initialization, audit_path = _account_pair_manager(tmp_path)
    original_ledger = b"original-ledger"
    original_initialization = b"original-initialization"
    ledger.write_bytes(b"archived-ledger")
    initialization.write_bytes(b"archived-initialization")
    archive = tmp_path / "account-backup.zip"
    manager.create_backup(archive)
    ledger.write_bytes(original_ledger)
    initialization.write_bytes(original_initialization)
    preflight = manager.preflight_restore(archive)

    real_replace = os.replace
    interrupted = False

    def interrupt_after_ledger_replacement(source, destination) -> None:
        nonlocal interrupted
        if not interrupted and destination == ledger and source.read_bytes() == b"archived-ledger":
            interrupted = True
            real_replace(source, destination)
            raise KeyboardInterrupt("injected interruption")
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        interrupt_after_ledger_replacement,
    )

    with pytest.raises(KeyboardInterrupt, match="injected interruption"):
        manager.restore(archive, confirmation_token=preflight.confirmation_token)

    assert ledger.read_bytes() == original_ledger
    assert initialization.read_bytes() == original_initialization
    assert not _restore_journal_path(audit_path).exists()


def test_new_manager_recovers_durable_journal_after_partial_account_restore(tmp_path) -> None:
    _, ledger, initialization, audit_path = _account_pair_manager(tmp_path)
    original_ledger = b"original-ledger"
    original_initialization = b"original-initialization"
    managed_files = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
    }
    ledger.write_bytes(b"partially-restored-ledger")
    initialization.write_bytes(original_initialization)
    journal_path, snapshots = _write_bound_pending_journal(
        audit_path=audit_path,
        managed_files=managed_files,
        original_contents={
            "account-ledger.jsonl": original_ledger,
            "account-ledger.jsonl.initialization.json": original_initialization,
        },
        planned_contents={
            "account-ledger.jsonl": b"partially-restored-ledger",
            "account-ledger.jsonl.initialization.json": b"planned-initialization",
        },
    )

    LocalBackupManager(
        managed_files=managed_files,
        consistency_groups={
            "account-state": (
                "account-ledger.jsonl",
                "account-ledger.jsonl.initialization.json",
            )
        },
        audit_path=audit_path,
    )

    assert ledger.read_bytes() == original_ledger
    assert initialization.read_bytes() == original_initialization
    assert not journal_path.exists()
    assert all(not snapshot.exists() for snapshot in snapshots)


def test_overlapping_manager_rejects_restore_owned_by_other_audit_journal(
    tmp_path, monkeypatch
) -> None:
    ledger = tmp_path / "account-ledger.jsonl"
    initialization = tmp_path / "account-ledger.jsonl.initialization.json"
    audit_a = tmp_path / "restore-audit-a.jsonl"
    audit_b = tmp_path / "restore-audit-b.jsonl"
    managed_pair = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
    }
    manager_a = LocalBackupManager(
        managed_files=managed_pair,
        consistency_groups={
            "account-state": (
                "account-ledger.jsonl",
                "account-ledger.jsonl.initialization.json",
            )
        },
        audit_path=audit_a,
    )
    manager_b = LocalBackupManager(
        managed_files={"account-ledger.jsonl": ledger},
        audit_path=audit_b,
    )
    original_ledger = b"original-ledger"
    original_initialization = b"original-initialization"
    planned_ledger = b"planned-ledger"
    planned_initialization = b"planned-initialization"
    ledger.write_bytes(planned_ledger)
    initialization.write_bytes(planned_initialization)
    archive_a = tmp_path / "restore-a.zip"
    archive_b = tmp_path / "restore-b.zip"
    manager_a.create_backup(archive_a)
    manager_b.create_backup(archive_b)
    ledger.write_bytes(original_ledger)
    initialization.write_bytes(original_initialization)
    preflight_a = manager_a.preflight_restore(archive_a)
    preflight_b = manager_b.preflight_restore(archive_b)

    real_replace = os.replace
    interrupted = False

    def interrupt_after_ledger_replacement(source, destination) -> None:
        nonlocal interrupted
        if not interrupted and destination == ledger and source.read_bytes() == planned_ledger:
            interrupted = True
            real_replace(source, destination)
            raise KeyboardInterrupt("injected interruption")
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        interrupt_after_ledger_replacement,
    )
    monkeypatch.setattr(manager_a, "_recover_pending_restore", lambda: None)
    try:
        with pytest.raises(KeyboardInterrupt, match="injected interruption"):
            manager_a.restore(
                archive_a,
                confirmation_token=preflight_a.confirmation_token,
            )
    finally:
        monkeypatch.undo()

    journal_path = _restore_journal_path(audit_a)
    assert journal_path.exists()
    assert ledger.read_bytes() == planned_ledger
    assert initialization.read_bytes() == original_initialization
    with pytest.raises(ValueError, match="pending restore"):
        manager_b.restore(
            archive_b,
            confirmation_token=preflight_b.confirmation_token,
        )

    LocalBackupManager(
        managed_files=managed_pair,
        consistency_groups={
            "account-state": (
                "account-ledger.jsonl",
                "account-ledger.jsonl.initialization.json",
            )
        },
        audit_path=audit_a,
    )

    assert ledger.read_bytes() == original_ledger
    assert initialization.read_bytes() == original_initialization
    assert not journal_path.exists()
    assert not audit_b.exists()


def test_manager_retains_journal_and_snapshots_when_recovery_cannot_validate(tmp_path) -> None:
    _, ledger, initialization, audit_path = _account_pair_manager(tmp_path)
    original_ledger = b"original-ledger"
    original_initialization = b"original-initialization"
    managed_files = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
    }
    ledger.write_bytes(b"partially-restored-ledger")
    initialization.write_bytes(original_initialization)
    journal_path, snapshots = _write_bound_pending_journal(
        audit_path=audit_path,
        managed_files=managed_files,
        original_contents={
            "account-ledger.jsonl": original_ledger,
            "account-ledger.jsonl.initialization.json": original_initialization,
        },
        planned_contents={
            "account-ledger.jsonl": b"partially-restored-ledger",
            "account-ledger.jsonl.initialization.json": b"planned-initialization",
        },
        invalid_snapshot_hash=True,
    )

    with pytest.raises(ValueError, match="restore journal"):
        LocalBackupManager(
            managed_files=managed_files,
            consistency_groups={
                "account-state": (
                    "account-ledger.jsonl",
                    "account-ledger.jsonl.initialization.json",
                )
            },
            audit_path=audit_path,
        )

    assert journal_path.exists()
    assert all(snapshot.exists() for snapshot in snapshots)
    assert ledger.read_bytes() == b"partially-restored-ledger"
    assert initialization.read_bytes() == original_initialization


def test_new_manager_finalizes_an_empty_pending_restore_journal(tmp_path) -> None:
    audit_path = tmp_path / "restore-audit.jsonl"
    managed_files = {"model-notes.json": tmp_path / "model-notes.json"}
    journal_path, snapshots = _write_bound_pending_journal(
        audit_path=audit_path,
        managed_files=managed_files,
        original_contents={},
        planned_contents={},
    )

    LocalBackupManager(
        managed_files=managed_files,
        audit_path=audit_path,
    )

    assert not journal_path.exists()
    assert snapshots == ()


def test_backup_waits_for_restore_and_archives_one_coherent_account_state(
    tmp_path, monkeypatch
) -> None:
    manager, ledger, initialization, _ = _account_pair_manager(tmp_path)
    original_state = (b"original-ledger", b"original-initialization")
    archived_state = (b"archived-ledger", b"archived-initialization")
    ledger.write_bytes(archived_state[0])
    initialization.write_bytes(archived_state[1])
    restore_archive = tmp_path / "restore-source.zip"
    manager.create_backup(restore_archive)
    ledger.write_bytes(original_state[0])
    initialization.write_bytes(original_state[1])
    preflight = manager.preflight_restore(restore_archive)

    real_replace = os.replace
    first_replacement = threading.Event()
    release_restore = threading.Event()
    paused = False

    def pause_after_ledger_replacement(source, destination) -> None:
        nonlocal paused
        if not paused and destination == ledger and source.read_bytes() == archived_state[0]:
            paused = True
            real_replace(source, destination)
            first_replacement.set()
            if not release_restore.wait(timeout=3):
                raise RuntimeError("test did not release the restore")
            return
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        pause_after_ledger_replacement,
    )
    backup_archive = tmp_path / "concurrent-backup.zip"
    backup_started = threading.Event()
    backup_finished = threading.Event()

    def create_concurrent_backup() -> None:
        backup_started.set()
        try:
            manager.create_backup(backup_archive)
        finally:
            backup_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        restore_future = executor.submit(
            manager.restore,
            restore_archive,
            confirmation_token=preflight.confirmation_token,
        )
        assert first_replacement.wait(timeout=3)
        backup_future = executor.submit(create_concurrent_backup)
        assert backup_started.wait(timeout=3)
        try:
            assert not backup_finished.wait(timeout=0.2)
        finally:
            release_restore.set()
        restore_future.result(timeout=3)
        backup_future.result(timeout=3)

    with zipfile.ZipFile(backup_archive) as bundle:
        backup_state = (
            bundle.read("managed/account-ledger.jsonl"),
            bundle.read("managed/account-ledger.jsonl.initialization.json"),
        )
    assert backup_state in {original_state, archived_state}


def test_manager_rejects_pending_journal_bound_to_different_managed_destinations(tmp_path) -> None:
    audit_path = tmp_path / "shared" / "restore-audit.jsonl"
    source_ledger = tmp_path / "source" / "account-ledger.jsonl"
    source_initialization = tmp_path / "source" / "account-ledger.jsonl.initialization.json"
    target_ledger = tmp_path / "target" / "account-ledger.jsonl"
    target_initialization = tmp_path / "target" / "account-ledger.jsonl.initialization.json"
    source_ledger.parent.mkdir()
    target_ledger.parent.mkdir()
    source_ledger.write_bytes(b"partially-restored-source-ledger")
    source_initialization.write_bytes(b"source-initialization")
    target_ledger.write_bytes(b"target-ledger")
    target_initialization.write_bytes(b"target-initialization")
    # A v1 journal only identifies logical paths, so it can be replayed against B.
    journal_path, snapshots = _write_legacy_pending_account_journal(
        audit_path=audit_path,
        original_ledger=b"original-source-ledger",
        original_initialization=b"original-source-initialization",
    )

    with pytest.raises(ValueError, match="configuration mismatch"):
        LocalBackupManager(
            managed_files={
                "account-ledger.jsonl": target_ledger,
                "account-ledger.jsonl.initialization.json": target_initialization,
            },
            consistency_groups={
                "account-state": (
                    "account-ledger.jsonl",
                    "account-ledger.jsonl.initialization.json",
                )
            },
            audit_path=audit_path,
        )

    assert target_ledger.read_bytes() == b"target-ledger"
    assert target_initialization.read_bytes() == b"target-initialization"
    assert journal_path.exists()
    assert all(snapshot.exists() for snapshot in snapshots)


def test_manager_retains_manual_writes_that_do_not_match_pending_restore_state(
    tmp_path, monkeypatch
) -> None:
    manager, ledger, initialization, audit_path = _account_pair_manager(tmp_path)
    managed_files = {
        "account-ledger.jsonl": ledger,
        "account-ledger.jsonl.initialization.json": initialization,
    }
    planned_ledger = b"planned-ledger"
    planned_initialization = b"planned-initialization"
    ledger.write_bytes(planned_ledger)
    initialization.write_bytes(planned_initialization)
    archive = tmp_path / "account-backup.zip"
    manager.create_backup(archive)
    ledger.unlink()
    initialization.unlink()
    preflight = manager.preflight_restore(archive)

    real_replace = os.replace
    interrupted = False

    def interrupt_after_first_replacement(source, destination) -> None:
        nonlocal interrupted
        if not interrupted and destination == ledger and source.read_bytes() == planned_ledger:
            interrupted = True
            real_replace(source, destination)
            raise KeyboardInterrupt("injected interruption")
        real_replace(source, destination)

    monkeypatch.setattr(
        "a_share_quant.workbench.backup.os.replace",
        interrupt_after_first_replacement,
    )
    # Model a process ending before its in-process exception handler can recover.
    monkeypatch.setattr(manager, "_recover_pending_restore", lambda: None)

    with pytest.raises(KeyboardInterrupt, match="injected interruption"):
        manager.restore(archive, confirmation_token=preflight.confirmation_token)

    manual_ledger = b"manual-ledger-after-interruption"
    manual_initialization = b"manual-initialization-after-interruption"
    ledger.write_bytes(manual_ledger)
    initialization.write_bytes(manual_initialization)
    journal_path = _restore_journal_path(audit_path)

    with pytest.raises(ValueError, match="unexpected post-interruption state"):
        LocalBackupManager(
            managed_files=managed_files,
            consistency_groups={
                "account-state": (
                    "account-ledger.jsonl",
                    "account-ledger.jsonl.initialization.json",
                )
            },
            audit_path=audit_path,
        )

    assert ledger.read_bytes() == manual_ledger
    assert initialization.read_bytes() == manual_initialization
    assert journal_path.exists()
