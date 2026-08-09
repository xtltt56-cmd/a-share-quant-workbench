from __future__ import annotations

import hashlib
import json
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.backup import LocalBackupManager


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
