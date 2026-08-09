from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from inspect import signature
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from a_share_quant.advisory.contracts import ForecastRecord
from a_share_quant.advisory.engine import AdvisoryContext
from a_share_quant.advisory.risk import PortfolioRiskSnapshot
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.service import WorkbenchService
from scripts.quant_cli import main as quant_cli_main


def _service(tmp_path) -> AdvisoryWorkbenchService:
    return AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=tmp_path / "account-ledger.jsonl",
        known_instruments={"000001": "平安银行"},
        today=lambda: date(2026, 8, 10),
    )


def _context() -> AdvisoryContext:
    generated_at = datetime(2026, 8, 10, 8, tzinfo=timezone.utc)
    return AdvisoryContext(
        forecast=ForecastRecord(
            forecast_id="forecast-1",
            symbol="000001",
            generated_at=generated_at,
            data_cutoff=generated_at,
            reference_price=Decimal("10"),
            model_version="champion-v1",
            data_version="data-v1",
            feature_version="features-v1",
            horizon_days=5,
            maturity_date=date(2026, 8, 15),
            predicted_return=Decimal("0.05"),
            predicted_probability=Decimal("0.65"),
            predicted_rank=1,
            uncertainty=Decimal("0.10"),
        ),
        portfolio=PortfolioRiskSnapshot(
            equity=Decimal("100000"),
            cash=Decimal("100000"),
            drawdown=Decimal("0"),
            positions=(),
        ),
        data_quality="GOOD",
        tradeable=True,
        market_regime="NORMAL",
        market_price=Decimal("10"),
        invalidation_price=Decimal("9.50"),
        industry="银行",
        liquidity_amount=Decimal("100000000"),
        event_risk=False,
    )


def test_manual_buy_preview_accepts_only_four_fields_and_does_not_persist_until_confirmed(
    tmp_path,
) -> None:
    service = _service(tmp_path)

    assert list(signature(service.preview_manual_buy).parameters) == [
        "name",
        "code",
        "quantity",
        "price",
    ]
    preview = service.preview_manual_buy(
        name="平安银行",
        code="000001",
        quantity=100,
        price="10.00",
    )

    assert preview["manual_execution_required"] is True
    assert preview["name"] == "平安银行"
    assert preview["code"] == "000001"
    assert preview["quantity"] == 100
    assert preview["price"] == "10.0000"
    assert preview["estimated_total_cost"] == "1005.00"
    assert service.holdings()["positions"] == []
    assert not (tmp_path / "account-ledger.jsonl").exists()
    assert not any("order" in name.lower() for name in dir(service) if not name.startswith("_"))

    confirmed = service.confirm_manual_buy(preview["confirmation_token"])

    assert confirmed["manual_execution_required"] is True
    assert confirmed["recorded"] is True
    assert service.holdings()["positions"] == [
        {
            "name": "平安银行",
            "code": "000001",
            "total_quantity": 100,
            "available_quantity": 0,
            "frozen_quantity": 100,
            "average_cost": "10.0500",
        }
    ]
    assert (tmp_path / "account-ledger.jsonl").exists()


def test_holdings_replays_local_jsonl_and_keeps_manual_execution_requirement(tmp_path) -> None:
    first = _service(tmp_path)
    preview = first.preview_manual_buy(name="平安银行", code="000001", quantity=100, price=10)
    first.confirm_manual_buy(preview["confirmation_token"])

    replayed = _service(tmp_path)
    holdings = replayed.holdings()

    assert holdings["manual_execution_required"] is True
    assert holdings["cash"] == "98995.00"
    assert holdings["positions"][0]["total_quantity"] == 100


def test_today_guidance_uses_only_caller_provided_context_and_is_manual_only(tmp_path) -> None:
    service = _service(tmp_path)

    guidance = service.today_guidance(_context())

    assert guidance["state"] == "BUY_CANDIDATE"
    assert guidance["manual_execution_required"] is True
    assert guidance["market_validation"] == "CALLER_PROVIDED_CONTEXT"
    assert guidance["suggested_quantity"] == 400


def test_model_and_data_health_does_not_claim_unavailable_live_validation(tmp_path) -> None:
    health = _service(tmp_path).model_data_health()

    assert health == {
        "model_status": "NO_FORECAST_RECORDS",
        "data_status": "NO_LIVE_MARKET_VALIDATION",
        "manual_execution_required": True,
    }


def test_advisory_routes_are_local_header_guarded_and_leave_restore_manual(tmp_path) -> None:
    service = _service(tmp_path)
    server = create_server(
        service=WorkbenchService(allow_network=False),
        advisory_service=service,
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/advisory", timeout=3) as response:
            html = response.read().decode("utf-8")
            assert response.headers["Cache-Control"] == "no-store"
        assert "人工投顾" in html

        with urlopen(f"http://127.0.0.1:{port}/api/advisory/holdings", timeout=3) as response:
            holdings = json.loads(response.read().decode("utf-8"))
            assert response.headers["Cache-Control"] == "no-store"
        assert holdings["manual_execution_required"] is True

        body = json.dumps(
            {"name": "平安银行", "code": "000001", "quantity": 100, "price": "10"}
        ).encode("utf-8")
        with pytest.raises(HTTPError) as missing_header:
            urlopen(
                Request(
                    f"http://127.0.0.1:{port}/api/advisory/buy-preview",
                    data=body,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                ),
                timeout=3,
            )
        assert missing_header.value.code == 403

        request = Request(
            f"http://127.0.0.1:{port}/api/advisory/buy-preview",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Quant-Workbench-Request": "manual-advisory",
            },
        )
        with urlopen(request, timeout=3) as response:
            preview = json.loads(response.read().decode("utf-8"))
        assert preview["manual_execution_required"] is True

        with pytest.raises(HTTPError) as absent_restore_route:
            urlopen(f"http://127.0.0.1:{port}/api/advisory/restore", timeout=3)
        assert absent_restore_route.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_advisory_routes_sanitize_internal_errors(tmp_path) -> None:
    class FailingAdvisoryService:
        def holdings(self) -> dict[str, object]:
            raise RuntimeError("local_token=secret-value")

    server = create_server(
        service=WorkbenchService(allow_network=False),
        advisory_service=FailingAdvisoryService(),
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with pytest.raises(HTTPError) as response_error:
            urlopen(f"http://127.0.0.1:{port}/api/advisory/holdings", timeout=3)
        assert response_error.value.code == 400
        assert response_error.value.headers["Cache-Control"] == "no-store"
        assert "secret-value" not in response_error.value.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_advisory_status_cli_is_local_only(tmp_path, capsys) -> None:
    exit_code = quant_cli_main(
        [
            "advisory-status",
            "--ledger",
            str(tmp_path / "account-ledger.jsonl"),
            "--initial-cash",
            "100000",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["holdings"]["manual_execution_required"] is True
    assert payload["model_data_health"]["data_status"] == "NO_LIVE_MARKET_VALIDATION"
