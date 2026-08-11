from datetime import date

import pytest

from a_share_quant.account.contracts import TradeSide
from a_share_quant.account.import_inbox import AccountImportInbox, AccountImportKind


def _write_fill_csv(tmp_path, *, price: str = "10"):
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    source = inbox / "成交.csv"
    source.write_text(
        f"证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,{price}\n",
        encoding="utf-8",
    )
    return source


def test_scan_returns_only_supported_regular_direct_children(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "成交.csv").write_text(
        "证券代码,成交数量,成交价格\n000001,100,10\n", encoding="utf-8"
    )
    (inbox / "说明.txt").write_text("ignore", encoding="utf-8")
    (inbox / "nested").mkdir()
    (inbox / "nested" / "隐藏.csv").write_text("x", encoding="utf-8")

    files = AccountImportInbox(inbox).scan()

    assert [item.file_name for item in files] == ["成交.csv"]


def test_scan_rejects_link_or_reparse_entry_when_supported_by_platform(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    outside = tmp_path / "outside.csv"
    inbox.mkdir()
    outside.write_text(
        "证券代码,成交数量,成交价格\n000001,100,10\n", encoding="utf-8"
    )
    link = inbox / "linked.csv"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"link creation unavailable: {exc}")

    assert AccountImportInbox(inbox).scan() == ()


def test_file_id_cannot_be_used_after_file_content_changes(tmp_path) -> None:
    source = _write_fill_csv(tmp_path, price="10")
    inbox = AccountImportInbox(source.parent)
    discovered = inbox.scan()[0]
    preview = inbox.preview(discovered.file_id, default_trade_date=date(2026, 8, 11))
    source.write_text(
        "证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,11\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="file changed after preview"):
        inbox.verify_unchanged(preview)


def test_unknown_file_identifier_never_reads_an_arbitrary_path(tmp_path) -> None:
    inbox = AccountImportInbox(tmp_path)

    with pytest.raises(ValueError, match="unknown account import file"):
        inbox.preview("../../secrets.txt", default_trade_date=date(2026, 8, 11))


def test_detects_common_chinese_fill_columns_and_buy_side(tmp_path) -> None:
    source = tmp_path / "成交明细.csv"
    source.write_text(
        "证券名称,证券代码,买卖标志,成交数量,成交价格,成交日期\n"
        "平安银行,000001,买入,100,10.25,2026-08-11\n",
        encoding="utf-8",
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]

    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))

    assert preview.kind is AccountImportKind.FILLS
    assert preview.detected_mapping == {
        "name": "证券名称",
        "symbol": "证券代码",
        "side": "买卖标志",
        "quantity": "成交数量",
        "price": "成交价格",
        "trade_date": "成交日期",
    }
    assert preview.fill_preview is not None
    assert preview.fill_preview.candidate_events[0].side is TradeSide.BUY


@pytest.mark.parametrize("side", ["卖出", "S", "SELL"])
def test_normalizes_common_sell_values(tmp_path, side: str) -> None:
    source = tmp_path / "成交.csv"
    source.write_text(
        "证券代码,买卖标志,成交数量,成交价格\n000001,"
        f"{side},100,10\n",
        encoding="utf-8",
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]

    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))

    assert preview.fill_preview is not None
    assert preview.fill_preview.candidate_events[0].side is TradeSide.SELL


def test_unknown_explicit_side_is_rejected_instead_of_assumed_buy(tmp_path) -> None:
    source = tmp_path / "成交.csv"
    source.write_text(
        "证券代码,买卖标志,成交数量,成交价格\n000001,未知,100,10\n",
        encoding="utf-8",
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]

    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))

    assert preview.fill_preview is not None
    assert preview.fill_preview.accepted_rows == 0
    assert preview.fill_preview.rejected_rows[0].row_number == 1


def test_missing_side_is_explicitly_warned_as_buy_default(tmp_path) -> None:
    source = _write_fill_csv(tmp_path)
    inbox = AccountImportInbox(source.parent)
    file = inbox.scan()[0]

    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))

    assert preview.fill_preview is not None
    assert preview.fill_preview.candidate_events[0].side is TradeSide.BUY
    assert "MISSING_SIDE_DEFAULTED_TO_BUY" in preview.warnings


def test_detects_position_snapshot_without_creating_fill_events(tmp_path) -> None:
    source = tmp_path / "持仓.csv"
    source.write_text(
        "证券代码,证券名称,证券数量,可用数量,冻结数量,成本价,可用资金,日期\n"
        "000001,平安银行,300,200,100,10.1234,88000.50,2026-08-11\n",
        encoding="utf-8",
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]

    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))

    assert preview.kind is AccountImportKind.POSITIONS
    assert preview.fill_preview is None
    assert preview.cash == 88000.50
    assert preview.positions[0].total_quantity == 300
    assert preview.positions[0].available_quantity == 200
    assert preview.positions[0].frozen_quantity == 100


def test_position_snapshot_rejects_inconsistent_quantities(tmp_path) -> None:
    source = tmp_path / "持仓.csv"
    source.write_text(
        "证券代码,证券名称,证券数量,可用数量,冻结数量,成本价\n"
        "000001,平安银行,300,250,100,10.1234\n",
        encoding="utf-8",
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]

    with pytest.raises(ValueError, match="position quantities are inconsistent"):
        inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))


def test_realistic_ths_tab_export_with_xls_extension_is_positions(tmp_path) -> None:
    source = tmp_path / "table.xls"
    source.write_bytes(
        "证券代码\t证券名称\t股票余额\t可用余额\t冻结数量\t成本价\n"
        "=\"002007\"\t华兰生物\t4200\t4200\t0\t31.961\n"
        "=\"603883\"\t老百姓\t5000\t5000\t0\t16.036\n".encode("gb18030")
    )
    inbox = AccountImportInbox(tmp_path)
    file = inbox.scan()[0]

    preview = inbox.preview(file.file_id, default_trade_date=date(2026, 8, 11))

    assert preview.kind is AccountImportKind.POSITIONS
    assert [position.symbol for position in preview.positions] == ["002007", "603883"]
    assert preview.positions[0].name == "华兰生物"
