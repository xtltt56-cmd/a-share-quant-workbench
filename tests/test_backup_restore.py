from __future__ import annotations

import json
import zipfile
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
