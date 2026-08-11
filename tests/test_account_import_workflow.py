from decimal import Decimal

import pytest

from a_share_quant.account.import_inbox import AccountImportInbox
from a_share_quant.account.snapshot_store import AccountSnapshotStore
from a_share_quant.account.store import JsonlLedgerStore
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService


def _service_with_fill_export(tmp_path):
    inbox_path = tmp_path / "inbox"
    inbox_path.mkdir()
    source = inbox_path / "成交.csv"
    source.write_text(
        "证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,10\n",
        encoding="utf-8",
    )
    ledger_path = tmp_path / "ledger.jsonl"
    snapshot_path = tmp_path / "snapshot.json"
    service = AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=ledger_path,
        account_import_inbox=AccountImportInbox(inbox_path),
        account_snapshot_store=AccountSnapshotStore(snapshot_path),
    )
    return service, source, ledger_path, snapshot_path


def _service_with_position_export(tmp_path):
    inbox_path = tmp_path / "inbox"
    inbox_path.mkdir()
    source = inbox_path / "持仓.csv"
    source.write_text(
        "证券代码,证券名称,证券数量,可用数量,冻结数量,成本价,可用资金,日期\n"
        "000001,平安银行,300,200,100,10.1234,88000.50,2026-08-11\n",
        encoding="utf-8",
    )
    ledger_path = tmp_path / "ledger.jsonl"
    snapshot_path = tmp_path / "snapshot.json"
    service = AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=ledger_path,
        account_import_inbox=AccountImportInbox(inbox_path),
        account_snapshot_store=AccountSnapshotStore(snapshot_path),
    )
    return service, source, ledger_path, snapshot_path


def test_fill_file_preview_does_not_write_before_confirmation(tmp_path) -> None:
    service, source, ledger_path, snapshot_path = _service_with_fill_export(tmp_path)

    listed = service.list_account_imports()
    preview = service.preview_account_import(listed["files"][0]["file_id"])

    assert preview["kind"] == "FILLS"
    assert not ledger_path.exists()
    assert not snapshot_path.exists()
    assert preview["source_name"] == source.name


def test_fill_confirmation_is_one_time_and_file_import_is_idempotent(tmp_path) -> None:
    service, source, ledger_path, unused = _service_with_fill_export(tmp_path)
    file_id = service.list_account_imports()["files"][0]["file_id"]
    token = service.preview_account_import(file_id)["confirmation_token"]

    result = service.confirm_account_import(token)

    assert result["kind"] == "FILLS"
    assert result["recorded_rows"] == 1
    with pytest.raises(ValueError, match="unknown or expired"):
        service.confirm_account_import(token)

    second_token = service.preview_account_import(file_id)["confirmation_token"]
    assert service.confirm_account_import(second_token)["recorded_rows"] == 0
    assert len(JsonlLedgerStore(ledger_path).load_fills()) == 1


def test_position_confirmation_writes_snapshot_but_no_fill(tmp_path) -> None:
    service, source, ledger_path, snapshot_path = _service_with_position_export(tmp_path)
    file_id = service.list_account_imports()["files"][0]["file_id"]
    token = service.preview_account_import(file_id)["confirmation_token"]

    result = service.confirm_account_import(token)

    assert result["kind"] == "POSITIONS"
    assert result["position_rows"] == 1
    assert not ledger_path.exists()
    assert AccountSnapshotStore(snapshot_path).load() is not None


def test_changed_source_invalidates_confirmation_token(tmp_path) -> None:
    service, source, ledger_path, snapshot_path = _service_with_fill_export(tmp_path)
    file_id = service.list_account_imports()["files"][0]["file_id"]
    token = service.preview_account_import(file_id)["confirmation_token"]
    source.write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="file changed after preview"):
        service.confirm_account_import(token)
    assert not ledger_path.exists()
    assert not snapshot_path.exists()


def test_holdings_exposes_local_and_imported_sources_separately(tmp_path) -> None:
    service, source, ledger_path, snapshot_path = _service_with_position_export(tmp_path)
    file_id = service.list_account_imports()["files"][0]["file_id"]
    token = service.preview_account_import(file_id)["confirmation_token"]
    service.confirm_account_import(token)

    holdings = service.holdings()

    assert holdings["imported_account_snapshot"]["source_name"] == source.name
    assert holdings["imported_account_snapshot"]["positions"][0]["code"] == "000001"
    assert holdings["local_ledger"]["positions"] == []
