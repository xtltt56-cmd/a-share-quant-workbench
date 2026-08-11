"""Run an offline end-to-end acceptance for the confirmed account import bridge."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path

from a_share_quant.account.import_inbox import AccountImportInbox
from a_share_quant.account.snapshot_store import AccountSnapshotStore
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    inbox_path = workspace / "inbox"
    ledger_path = workspace / "account-ledger.jsonl"
    snapshot_path = workspace / "imported-account-snapshot.json"
    inbox_path.mkdir(parents=True, exist_ok=True)
    _write_fixture_exports(inbox_path)

    service = _service(inbox_path, ledger_path, snapshot_path)
    files = {item["file_name"]: item["file_id"] for item in service.list_account_imports()["files"]}
    fill_token = service.preview_account_import(files["成交.csv"])["confirmation_token"]
    first_fill = service.confirm_account_import(fill_token)
    duplicate_token = service.preview_account_import(files["成交.csv"])["confirmation_token"]
    duplicate_fill = service.confirm_account_import(duplicate_token)
    position_token = service.preview_account_import(files["持仓.xls"])["confirmation_token"]
    service.confirm_account_import(position_token)

    restarted = _service(inbox_path, ledger_path, snapshot_path)
    holdings = restarted.holdings()
    payload = {
        "status": "PASS",
        "fills_recorded": first_fill["recorded_rows"],
        "duplicate_fills_recorded": duplicate_fill["recorded_rows"],
        "positions_loaded": len(holdings["imported_account_snapshot"]["positions"]),
        "manual_execution_required": holdings["manual_execution_required"],
        "order_capability_present": any(
            name in dir(restarted)
            for name in ("order_send", "order_stock", "place_order", "submit_order")
        ),
    }
    if payload != {
        "status": "PASS",
        "fills_recorded": 1,
        "duplicate_fills_recorded": 0,
        "positions_loaded": 1,
        "manual_execution_required": True,
        "order_capability_present": False,
    }:
        raise RuntimeError(f"account import acceptance mismatch: {payload}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _service(inbox_path: Path, ledger_path: Path, snapshot_path: Path) -> AdvisoryWorkbenchService:
    return AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=ledger_path,
        account_import_inbox=AccountImportInbox(inbox_path),
        account_snapshot_store=AccountSnapshotStore(snapshot_path),
    )


def _write_fixture_exports(inbox_path: Path) -> None:
    (inbox_path / "成交.csv").write_text(
        "证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,10\n",
        encoding="utf-8",
    )
    (inbox_path / "持仓.xls").write_bytes(
        "证券代码\t证券名称\t股票余额\t可用余额\t冻结数量\t成本价\n"
        "=\"002007\"\t华兰生物\t4200\t4200\t0\t31.961\n".encode("gb18030")
    )


if __name__ == "__main__":
    raise SystemExit(main())
