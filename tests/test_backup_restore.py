from __future__ import annotations

import json
import zipfile

import pytest

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
    audit_rows = [json.loads(line) for line in paths["audit"].read_text(encoding="utf-8").splitlines()]
    assert audit_rows[-1]["action"] == "restore"
    assert audit_rows[-1]["phase"] == "before_replacement"
