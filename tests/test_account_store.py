from datetime import date
from decimal import Decimal

import pytest

from a_share_quant.account.contracts import FillEvent, TradeSide
from a_share_quant.account.ledger import AccountLedger
from a_share_quant.account.store import JsonlLedgerStore


def test_jsonl_store_round_trips_a_hash_chained_fill(tmp_path) -> None:
    store = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    event = FillEvent(
        event_id="manual-buy-1",
        side=TradeSide.BUY,
        symbol="000001",
        quantity=100,
        price=Decimal("10"),
        trade_date=date(2026, 8, 10),
        name="平安银行",
    )

    store.append_fill(event)

    assert store.load_fills() == (event,)


def test_jsonl_store_rejects_a_tampered_record(tmp_path) -> None:
    store = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    store.append_fill(
        FillEvent(
            event_id="manual-buy-1",
            side=TradeSide.BUY,
            symbol="000001",
            quantity=100,
            price=10,
            trade_date=date(2026, 8, 10),
            name="平安银行",
        )
    )
    tampered = store.path.read_text(encoding="utf-8").replace('"10.0000"', '"1.0000"')
    store.path.write_text(tampered, encoding="utf-8")

    with pytest.raises(ValueError, match="hash"):
        store.load_fills()


def test_replaying_stored_records_restores_the_same_account_state(tmp_path) -> None:
    store = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    ledger = AccountLedger(initial_cash=Decimal("100000"))
    receipt = ledger.record_buy(
        name="平安银行",
        symbol="000001",
        quantity=100,
        price=10,
        trade_date=date(2026, 8, 10),
        event_id="manual-buy-1",
    )
    store.append_fill(receipt.event)

    restored = AccountLedger.from_records(
        initial_cash=Decimal("100000"),
        records=store.load_records(),
    )

    assert restored.snapshot(as_of=date(2026, 8, 10)) == ledger.snapshot(as_of=date(2026, 8, 10))
