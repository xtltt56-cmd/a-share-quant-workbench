from datetime import date

import pytest

from a_share_quant.account.import_inbox import AccountImportInbox


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
