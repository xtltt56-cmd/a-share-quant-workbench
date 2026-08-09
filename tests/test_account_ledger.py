from datetime import date
from decimal import Decimal

import pytest

from a_share_quant.account.contracts import FillEvent, TradeSide
from a_share_quant.account.ledger import AccountLedger


def test_four_field_buy_creates_t_plus_one_position_and_deducts_cash() -> None:
    ledger = AccountLedger(
        initial_cash=Decimal("100000"),
        known_instruments={"000001": "平安银行"},
    )

    receipt = ledger.record_buy(
        name="平安银行",
        symbol="000001",
        quantity=100,
        price=Decimal("10"),
        trade_date=date(2026, 8, 10),
        event_id="manual-buy-1",
    )

    assert receipt.idempotent is False
    assert receipt.cash_after == Decimal("98995.00")
    assert receipt.position.total_quantity == 100
    assert receipt.position.available_quantity == 0
    assert receipt.position.frozen_quantity == 100
    assert receipt.position.average_cost == Decimal("10.0500")


def test_same_manual_event_is_idempotent_and_name_code_mismatch_is_rejected() -> None:
    ledger = AccountLedger(
        initial_cash=Decimal("100000"),
        known_instruments={"000001": "平安银行"},
    )

    first = ledger.record_buy(
        name="平安银行",
        symbol="000001",
        quantity=100,
        price=10,
        trade_date=date(2026, 8, 10),
        event_id="manual-buy-1",
    )
    duplicate = ledger.record_buy(
        name="平安银行",
        symbol="000001",
        quantity=100,
        price=10,
        trade_date=date(2026, 8, 10),
        event_id="manual-buy-1",
    )

    assert duplicate.idempotent is True
    assert duplicate.cash_after == first.cash_after
    assert len(ledger.events()) == 1

    with pytest.raises(ValueError, match="name does not match"):
        ledger.record_buy(
            name="错误名称",
            symbol="000001",
            quantity=100,
            price=10,
            trade_date=date(2026, 8, 10),
            event_id="manual-buy-2",
        )


def test_sell_rejects_same_day_t_plus_one_lot_and_uses_fifo_cost_on_next_session() -> None:
    ledger = AccountLedger(initial_cash=Decimal("100000"))
    ledger.record_buy(
        name="平安银行",
        symbol="000001",
        quantity=100,
        price=10,
        trade_date=date(2026, 8, 10),
        event_id="manual-buy-1",
    )

    with pytest.raises(ValueError, match="available quantity"):
        ledger.record_sell(
            symbol="000001",
            quantity=100,
            price=11,
            trade_date=date(2026, 8, 10),
            event_id="manual-sell-same-day",
        )

    receipt = ledger.record_sell(
        symbol="000001",
        quantity=100,
        price=11,
        trade_date=date(2026, 8, 11),
        event_id="manual-sell-next-day",
    )

    assert receipt.position.total_quantity == 0
    assert receipt.realized_pnl == Decimal("89.45")
    assert receipt.cash_after == Decimal("100089.45")


def test_correction_appends_audit_record_and_replaces_the_original_effect() -> None:
    ledger = AccountLedger(initial_cash=Decimal("100000"))
    ledger.record_buy(
        name="平安银行",
        symbol="000001",
        quantity=100,
        price=10,
        trade_date=date(2026, 8, 10),
        event_id="manual-buy-1",
    )

    receipt = ledger.append_correction(
        original_event_id="manual-buy-1",
        replacement=FillEvent(
            event_id="manual-buy-1-corrected",
            side=TradeSide.BUY,
            symbol="000001",
            quantity=100,
            price=9,
            trade_date=date(2026, 8, 10),
            name="平安银行",
        ),
    )

    assert len(ledger.events()) == 2
    assert ledger.corrections()[0].original_event_id == "manual-buy-1"
    assert receipt.cash_after == Decimal("99095.00")
    assert receipt.position.average_cost == Decimal("9.0500")
