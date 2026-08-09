"""Safety-contract tests for the optional, manual-only QMT boundary."""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_share_quant.account.ledger import AccountLedger
from a_share_quant.integrations.qmt import ManualBasketExporter, QmtReadOnlyAdapter


class FakeReadOnlyClient:
    """A local stand-in for an already-authorized official read-only client."""

    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def fetch_account_snapshot(self) -> dict[str, object]:
        self.calls += 1
        return self.snapshot


def _missing_sdk(_: str) -> None:
    return None


def _qmt_snapshot(
    *,
    cash: str = "12345.67",
    quantity: int = 300,
    available_quantity: int = 200,
) -> dict[str, object]:
    return {
        "as_of": "2026-08-11",
        "cash": cash,
        "positions": [
            {
                "stock_code": "SZ000001",
                "stock_name": "平安银行",
                "volume": quantity,
                "can_use_volume": available_quantity,
                "frozen_volume": quantity - available_quantity,
            }
        ],
    }


def test_absent_sdk_is_truthful_and_submission_is_permanently_rejected() -> None:
    adapter = QmtReadOnlyAdapter(sdk_finder=_missing_sdk)

    capability = adapter.detect()

    assert capability.status == "SDK_NOT_FOUND"
    assert capability.sdk_available is False
    assert capability.manual_execution_required is True
    assert capability.live_connection_validated is False
    with pytest.raises(ValueError, match="unavailable"):
        adapter.fetch_account_snapshot()
    with pytest.raises(PermissionError, match="manual execution"):
        adapter.submit_order(symbol="000001", quantity=100, price="10.00")

    public_names = set(dir(adapter))
    assert "submit_order" in public_names
    assert not public_names.intersection(
        {"place_order", "send_order", "execute_order", "order_stock"}
    )


def test_injected_read_only_client_normalizes_cash_and_positions_without_sdk() -> None:
    fake_client = FakeReadOnlyClient(_qmt_snapshot())
    adapter = QmtReadOnlyAdapter(client=fake_client, sdk_finder=_missing_sdk)

    snapshot = adapter.fetch_account_snapshot()

    assert fake_client.calls == 1
    assert snapshot.as_of == date(2026, 8, 11)
    assert snapshot.cash == Decimal("12345.67")
    assert snapshot.positions[0].symbol == "000001"
    assert snapshot.positions[0].name == "平安银行"
    assert snapshot.positions[0].total_quantity == 300
    assert snapshot.positions[0].available_quantity == 200
    assert snapshot.positions[0].frozen_quantity == 100
    assert snapshot.manual_execution_required is True


def test_reconciliation_preview_reports_deltas_without_mutating_local_ledger() -> None:
    ledger = AccountLedger(initial_cash=Decimal("100000"))
    ledger.record_buy(
        name="平安银行",
        symbol="000001",
        quantity=100,
        price=Decimal("10"),
        trade_date=date(2026, 8, 10),
        event_id="manual-buy-1",
    )
    before_records = ledger.records()
    before_snapshot = ledger.snapshot(as_of=date(2026, 8, 11))
    adapter = QmtReadOnlyAdapter(
        client=FakeReadOnlyClient(
            _qmt_snapshot(cash="98000.00", quantity=200, available_quantity=200)
        ),
        sdk_finder=_missing_sdk,
    )

    preview = adapter.reconciliation_preview(ledger)

    assert preview.status == "DISCREPANCIES_FOUND"
    assert preview.cash_delta == Decimal("-995.00")
    assert len(preview.position_deltas) == 1
    delta = preview.position_deltas[0]
    assert delta.symbol == "000001"
    assert delta.total_quantity_delta == 100
    assert delta.available_quantity_delta == 100
    assert preview.manual_execution_required is True
    assert ledger.records() == before_records
    assert ledger.snapshot(as_of=date(2026, 8, 11)) == before_snapshot


def test_manual_basket_exports_only_review_fields_for_mapping_and_object_recommendations(
    tmp_path: Path,
) -> None:
    recommendations = [
        {
            "symbol": "SH600000",
            "name": "浦发银行",
            "action": "BUY_CANDIDATE",
            "suggested_quantity": 100,
            "maximum_acceptable_price": "12.34",
            "reason_codes": ["FORMAL_GATE_PASSED"],
            "order_id": "must-never-export",
            "manual_execution_required": True,
        },
        SimpleNamespace(
            code="000001",
            name="平安银行",
            action_zh="候选买入",
            quantity=200,
            price=Decimal("10.50"),
            reason_codes=("RISK_OK",),
            manual_execution_required=True,
        ),
    ]
    exporter = ManualBasketExporter()

    csv_path = exporter.export_csv(recommendations, tmp_path / "manual-basket.csv")
    json_path = exporter.export_json(recommendations, tmp_path / "manual-basket.json")

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    json_payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert rows[0]["review_status"] == "MANUAL_REVIEW_ONLY"
    assert rows[0]["manual_execution_required"] == "true"
    assert rows[0]["symbol"] == "600000"
    assert rows[0]["maximum_acceptable_price"] == "12.3400"
    assert "order_id" not in rows[0]
    assert json_payload["review_status"] == "MANUAL_REVIEW_ONLY"
    assert json_payload["manual_execution_required"] is True
    assert json_payload["items"][1]["symbol"] == "000001"
    assert "order_id" not in json_payload["items"][0]


@pytest.mark.parametrize(
    ("recommendation", "message"),
    [
        ({"symbol": "not-a-symbol", "quantity": 100, "price": "10"}, "symbol"),
        ({"symbol": "000001", "quantity": 0, "price": "10"}, "quantity"),
        ({"symbol": "000001", "quantity": 100, "price": "0"}, "price"),
    ],
)
def test_manual_basket_validates_recommendations_and_output_path(
    tmp_path: Path,
    recommendation: dict[str, object],
    message: str,
) -> None:
    exporter = ManualBasketExporter()

    with pytest.raises(ValueError, match=message):
        exporter.export_csv([recommendation], tmp_path / "manual-basket.csv")
    with pytest.raises(ValueError, match=".csv"):
        exporter.export_csv(
            [{"symbol": "000001", "quantity": 100, "price": "10"}],
            tmp_path / "manual-basket.json",
        )
