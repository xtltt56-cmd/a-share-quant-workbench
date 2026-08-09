"""Safety-contract tests for the optional, manual-only QMT boundary."""

from __future__ import annotations

import builtins
import csv
import json
import os
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_share_quant.account.ledger import AccountLedger
from a_share_quant.integrations.qmt import (
    ManualBasketExporter,
    QmtAccountSnapshot,
    QmtPositionSnapshot,
    QmtReadOnlyAdapter,
    detect,
)
from a_share_quant.integrations.qmt import read_only as qmt_read_only


class FakeReadOnlyClient:
    """A local stand-in for an already-authorized official read-only client."""

    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def fetch_account_snapshot(self) -> object:
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


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        (("000001", 100, "10.00"), {}),
        ((), {"symbol": "000001", "quantity": 100, "price": "10.00"}),
    ],
)
def test_submission_boundary_rejects_positional_and_keyword_calls(
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    adapter = QmtReadOnlyAdapter(sdk_finder=_missing_sdk)

    with pytest.raises(PermissionError, match="manual execution"):
        adapter.submit_order(*args, **kwargs)


def test_detect_rejects_dotted_sdk_module_before_finder_runs() -> None:
    finder_calls: list[str] = []

    def finder(module_name: str) -> None:
        finder_calls.append(module_name)
        return None

    with pytest.raises(ValueError, match="exact top-level"):
        detect(sdk_module="xtquant.xttrader", sdk_finder=finder)

    assert finder_calls == []


def test_default_detection_uses_metadata_without_importing_or_opening_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finder_calls: list[str] = []

    def finder(module_name: str) -> None:
        finder_calls.append(module_name)
        return None

    def forbid_import(*_: object, **__: object) -> object:
        raise AssertionError("detect must not import the QMT SDK")

    monkeypatch.setattr(qmt_read_only.importlib.util, "find_spec", finder)
    monkeypatch.setattr(builtins, "__import__", forbid_import)

    capability = detect()

    assert finder_calls == ["xtquant"]
    assert capability.status == "SDK_NOT_FOUND"
    assert capability.live_connection_validated is False


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


def test_injected_direct_snapshot_is_strictly_normalized() -> None:
    raw_snapshot = QmtAccountSnapshot(
        as_of="2026-08-11",  # type: ignore[arg-type]
        cash=Decimal("12345.678"),
        positions=(
            QmtPositionSnapshot(
                symbol="SZ000001",
                name="平安银行",
                total_quantity=300,
                available_quantity=200,
                frozen_quantity=100,
            ),
        ),
    )
    adapter = QmtReadOnlyAdapter(
        client=FakeReadOnlyClient(raw_snapshot),
        sdk_finder=_missing_sdk,
    )

    snapshot = adapter.fetch_account_snapshot()

    assert snapshot is not raw_snapshot
    assert snapshot.as_of == date(2026, 8, 11)
    assert snapshot.cash == Decimal("12345.68")
    assert snapshot.positions[0].symbol == "000001"


@pytest.mark.parametrize(
    ("raw_snapshot", "message"),
    [
        (
            QmtAccountSnapshot(as_of=None, cash=Decimal("NaN"), positions=()),
            "cash",
        ),
        (
            QmtAccountSnapshot(
                as_of=None,
                cash=Decimal("1"),
                positions=(),
                manual_execution_required=1,  # type: ignore[arg-type]
            ),
            "manual execution",
        ),
        (
            QmtAccountSnapshot(
                as_of=None,
                cash=Decimal("1"),
                positions=(
                    QmtPositionSnapshot(
                        symbol="not-a-symbol",
                        name="",
                        total_quantity=0,
                        available_quantity=0,
                        frozen_quantity=0,
                    ),
                ),
            ),
            "symbol",
        ),
        (
            QmtAccountSnapshot(
                as_of=None,
                cash=Decimal("1"),
                positions=(
                    QmtPositionSnapshot("000001", "", 100, 100, 0),
                    QmtPositionSnapshot("SZ000001", "", 100, 100, 0),
                ),
            ),
            "duplicate",
        ),
        (
            QmtAccountSnapshot(
                as_of=None,
                cash=Decimal("1"),
                positions=(
                    QmtPositionSnapshot("000001", "", 100, 100, 1),
                ),
            ),
            "inconsistent",
        ),
    ],
)
def test_injected_direct_snapshot_is_not_trusted_without_validation(
    raw_snapshot: QmtAccountSnapshot,
    message: str,
) -> None:
    adapter = QmtReadOnlyAdapter(
        client=FakeReadOnlyClient(raw_snapshot),
        sdk_finder=_missing_sdk,
    )

    with pytest.raises(ValueError, match=message):
        adapter.fetch_account_snapshot()


def test_snapshot_requires_explicit_frozen_quantity() -> None:
    raw_snapshot = _qmt_snapshot()
    position = raw_snapshot["positions"][0]
    assert isinstance(position, dict)
    del position["frozen_volume"]
    adapter = QmtReadOnlyAdapter(
        client=FakeReadOnlyClient(raw_snapshot),
        sdk_finder=_missing_sdk,
    )

    with pytest.raises(ValueError, match="frozen_quantity"):
        adapter.fetch_account_snapshot()


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


@pytest.mark.parametrize(
    ("method_name", "suffix"),
    [("export_csv", ".csv"), ("export_json", ".json")],
)
@pytest.mark.parametrize(
    "path_stem",
    [
        r"\\server\share\manual-basket",
        "//server/share/manual-basket",
        r"\\?\C:\manual-basket",
        r"\\.\C:\manual-basket",
    ],
)
def test_manual_basket_refuses_non_local_paths_before_filesystem_access(
    method_name: str,
    suffix: str,
    path_stem: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = ManualBasketExporter()
    filesystem_calls: list[Path] = []

    def forbid_filesystem_access(path: Path, *_: object, **__: object) -> object:
        filesystem_calls.append(path)
        raise AssertionError("non-local basket output must be rejected before filesystem access")

    monkeypatch.setattr(Path, "resolve", forbid_filesystem_access)
    monkeypatch.setattr(Path, "mkdir", forbid_filesystem_access)
    monkeypatch.setattr(Path, "open", forbid_filesystem_access)
    monkeypatch.setattr(Path, "write_text", forbid_filesystem_access)

    with pytest.raises(ValueError, match="local-only"):
        getattr(exporter, method_name)(
            [{"symbol": "000001", "quantity": 100, "price": "10"}],
            f"{path_stem}{suffix}",
        )

    assert filesystem_calls == []


@pytest.mark.parametrize(
    ("method_name", "suffix"),
    [("export_csv", ".csv"), ("export_json", ".json")],
)
def test_manual_basket_refuses_directory_linked_parent_before_write(
    tmp_path: Path,
    method_name: str,
    suffix: str,
) -> None:
    target_directory = tmp_path / "real-local-output"
    target_directory.mkdir()
    linked_parent = tmp_path / "linked-output"
    _create_directory_link_or_skip(linked_parent, target_directory)
    output_path = linked_parent / f"manual-basket{suffix}"

    with pytest.raises(ValueError, match="symlink|junction|local-only"):
        getattr(ManualBasketExporter(), method_name)(
            [{"symbol": "000001", "quantity": 100, "price": "10"}],
            output_path,
        )

    assert not (target_directory / output_path.name).exists()


def _create_directory_link_or_skip(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode == 0 and link.exists():
            return
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory link capability unavailable: {exc}")
