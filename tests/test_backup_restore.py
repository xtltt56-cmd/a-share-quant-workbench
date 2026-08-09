from __future__ import annotations

import json
import os
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

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
