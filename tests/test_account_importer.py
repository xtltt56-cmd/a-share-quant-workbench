from datetime import date
from decimal import Decimal

import pytest

from a_share_quant.account.importer import read_broker_file
from a_share_quant.account.ledger import AccountLedger
from a_share_quant.account.service import AccountEntryService
from a_share_quant.account.store import JsonlLedgerStore


def test_preview_then_confirm_import_keeps_ledger_unchanged_until_confirmation(tmp_path) -> None:
    ledger = AccountLedger(
        initial_cash=Decimal("100000"),
        known_instruments={"000001": "平安银行"},
    )
    service = AccountEntryService(ledger=ledger, store=JsonlLedgerStore(tmp_path / "ledger.jsonl"))

    preview = service.preview_rows(
        rows=[
            {
                "证券名称": "平安银行",
                "证券代码": "000001",
                "成交数量": "100",
                "成交价格": "10.00",
            }
        ],
        mapping={
            "name": "证券名称",
            "symbol": "证券代码",
            "quantity": "成交数量",
            "price": "成交价格",
        },
        default_trade_date=date(2026, 8, 10),
        source_bytes=b"broker-import-one",
    )

    assert preview.accepted_rows == 1
    assert preview.rejected_rows == ()
    assert ledger.events() == ()

    receipts = service.confirm_preview(preview.preview_id)

    assert len(receipts) == 1
    assert receipts[0].idempotent is False
    assert ledger.snapshot(as_of=date(2026, 8, 10)).position("000001").total_quantity == 100
    assert len(service.store.load_fills()) == 1


def test_import_preview_rejects_bad_rows_and_confirming_same_source_is_idempotent(tmp_path) -> None:
    ledger = AccountLedger(initial_cash=Decimal("100000"))
    service = AccountEntryService(ledger=ledger, store=JsonlLedgerStore(tmp_path / "ledger.jsonl"))
    mapping = {"name": "name", "symbol": "code", "quantity": "quantity", "price": "price"}

    preview = service.preview_rows(
        rows=[
            {"name": "平安银行", "code": "000001", "quantity": "100", "price": "10"},
            {"name": "异常行", "code": "000002", "quantity": "10.5", "price": "-1"},
        ],
        mapping=mapping,
        default_trade_date=date(2026, 8, 10),
        source_bytes=b"broker-import-two",
    )

    assert preview.accepted_rows == 1
    assert preview.rejected_rows[0].row_number == 2
    first = service.confirm_preview(preview.preview_id)
    duplicate = service.confirm_preview(preview.preview_id)

    assert first[0].idempotent is False
    assert duplicate[0].idempotent is True
    assert len(service.store.load_fills()) == 1


def test_csv_file_preview_uses_only_explicitly_mapped_columns(tmp_path) -> None:
    source = tmp_path / "broker.csv"
    source.write_text(
        "name,code,quantity,price,ignored_secret\n平安银行,000001,100,10,do-not-store\n",
        encoding="utf-8",
    )
    service = AccountEntryService(
        ledger=AccountLedger(initial_cash=Decimal("100000")),
        store=JsonlLedgerStore(tmp_path / "ledger.jsonl"),
    )

    preview = service.preview_file(
        source=source,
        mapping={"name": "name", "symbol": "code", "quantity": "quantity", "price": "price"},
        default_trade_date=date(2026, 8, 10),
    )

    assert preview.accepted_rows == 1
    assert "do-not-store" not in str(preview)


def test_csv_file_accepts_gb18030_from_chinese_broker(tmp_path) -> None:
    source = tmp_path / "成交明细.csv"
    source.write_bytes(
        "证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,10.25\n".encode(
            "gb18030"
        )
    )

    rows, raw = read_broker_file(source)

    assert raw == source.read_bytes()
    assert rows[0]["证券代码"] == "000001"


def test_excel_read_failure_is_sanitized(tmp_path) -> None:
    source = tmp_path / "成交明细.xlsx"
    source.write_bytes(b"not-an-excel-workbook")

    with pytest.raises(ValueError, match="Excel import could not be read"):
        read_broker_file(source)


def test_xls_extension_with_gb18030_tabular_export_is_read_as_text(tmp_path) -> None:
    source = tmp_path / "table.xls"
    source.write_bytes(
        "证券代码\t股票名称\t股票余额\t可用数量\t参考成本\n"
        "=\"002007\"\t华兰生物\t4200\t4200\t31.961\n".encode("gb18030")
    )

    rows, raw = read_broker_file(source)

    assert raw == source.read_bytes()
    assert rows[0]["证券代码"] == '=\"002007\"'
    assert rows[0]["股票余额"] == "4200"
