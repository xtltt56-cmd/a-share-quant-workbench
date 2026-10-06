import json
import threading
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from a_share_quant.contracts.realtime import RealTimeQuote
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.context_tools import WorkbenchContextTools
from a_share_quant.workbench.service import WorkbenchService

NOW = datetime(2026, 10, 4, 2, tzinfo=timezone.utc)


def service():
    store = OfficialSignalStore()
    store.put_signals((OfficialModelSignal(
        signal_date=date(2026, 9, 30), symbol="000001", name="平安银行",
        normalized_score=78.5, strategy_version="rule-v1", source="baostock",
        reasons=("趋势与流动性满足研究条件",),
    ),))
    return WorkbenchService(
        provider=type("Provider", (), {"name": "offline"})(),
        official_signal_store=store, public_risk_required=True, clock=lambda: NOW,
    )


def test_stock_tool_reports_real_missing_conditions_without_fabricated_prices():
    context = WorkbenchContextTools(service()).get_stock_context("000001.SZ")
    assert context["symbol"] == "000001"
    assert context["name"] == "平安银行"
    assert context["candidate"]["normalized_score"] == 78.5
    assert context["quote"] is None
    assert context["probability"] is None
    assert context["analysis_mode"] == "DETERMINISTIC_FACTS"
    assert "PUBLIC_RISK_NOT_CHECKED" in context["blocking_reasons"]
    assert "PRICE_PLAN_MISSING" in context["blocking_reasons"]
    assert context["evidence_id"].startswith("sha256:")
    assert context["manual_execution_required"] is True


def test_context_digest_is_stable_and_changes_with_underlying_evidence():
    current = service()
    tools = WorkbenchContextTools(current)
    first = tools.get_stock_context("000001")
    assert first["evidence_id"] == tools.get_stock_context("000001")["evidence_id"]
    current.publish_official_daily((), status="UPDATE_FAILED", notice_zh="更新失败")
    assert first["evidence_id"] != tools.get_stock_context("000001")["evidence_id"]
    assert "DAILY_UPDATE_FAILED" in tools.get_stock_context("000001")["blocking_reasons"]


def test_context_reports_stale_daily_inputs_using_exchange_calendar():
    current = service()
    current.clock = lambda: datetime(2026, 10, 12, 2, tzinfo=timezone.utc)
    result = WorkbenchContextTools(current).get_stock_context("000001")
    assert result["expected_session"] == "2026-10-09"
    assert "DAILY_INPUT_STALE" in result["blocking_reasons"]


def test_latest_completed_signal_is_not_stale_during_exchange_holiday():
    current = service()
    assert current.snapshot()["official_daily_candidates"][0]["signal_stale"] is False


def test_context_reports_specific_stock_cutoff_not_cross_section_maximum():
    current = service()
    first = current.official_signal_store.latest()[0]
    lagged = OfficialModelSignal(
        signal_date=first.signal_date, data_cutoff=date(2026, 9, 29),
        symbol="600519", name="贵州茅台", normalized_score=80,
        strategy_version=first.strategy_version, source="baostock",
    )
    current.publish_official_daily((first, lagged), status="FRESH", notice_zh="测试覆盖差异")
    assert current.snapshot()["daily_data_cutoff"] == "2026-09-30"
    result = WorkbenchContextTools(current).get_stock_context("600519")
    assert result["input_cutoff"] == "2026-09-29"
    assert "DAILY_INPUT_STALE" in result["blocking_reasons"]


def test_unknown_stock_is_not_fabricated_into_a_candidate():
    context = WorkbenchContextTools(service()).get_stock_context("600519")
    assert context["candidate"] is None
    assert "NOT_IN_CURRENT_CANDIDATES" in context["blocking_reasons"]
    assert context["quote"] is None


def test_holding_context_excludes_account_identity_and_full_cash_state(tmp_path):
    advisory = AdvisoryWorkbenchService(
        initial_cash=100000, ledger_path=tmp_path / "ledger.jsonl",
        today=lambda: NOW.date(),
    )
    preview = advisory.preview_manual_buy(name="平安银行", code="000001", quantity=100, price=10)
    advisory.confirm_manual_buy(preview["confirmation_token"])
    context = WorkbenchContextTools(service(), advisory).get_holding_context("000001")
    assert context["positions"][0]["total_quantity"] == 100
    serialized = json.dumps(context)
    assert "initial_cash" not in serialized
    assert "cash_after" not in serialized
    assert "ledger.jsonl" not in serialized
    assert context["cloud_transfer_authorized"] is False


def test_health_distinguishes_optional_broker_and_agent_from_core():
    health = service().workflow_health()
    assert health["mode"] == "MANUAL_ASSISTANCE"
    assert health["stages"]["agent"]["status"] == "NOT_CONFIGURED"
    assert health["stages"]["broker_read_only"]["required"] is False
    assert health["stages"]["daily"]["expected_session"] == "2026-09-30"


def test_runtime_eod_failure_metadata_is_visible():
    current = service()
    current.set_eod_status_provider(lambda: {
        "status": "FAILED", "error_code": "UPDATE_FAILED",
        "last_attempt_at": NOW.isoformat(), "next_retry_at": NOW.isoformat(),
    })
    assert current.workflow_health()["stages"]["daily"]["refresh"]["status"] == "FAILED"


def test_health_does_not_reuse_old_good_quote_as_current_pass():
    current = service()
    current.allow_network = True
    current.state.data_quality = "GOOD"
    current.state.schema_pass = current.state.continuous_updates = True
    current.state.latest_quote_at = (NOW - timedelta(hours=1)).isoformat()
    assert current.health()["data_ready"] is False
    stage = current.workflow_health()["stages"]["realtime"]
    assert stage["ready"] is False
    assert stage["status"] == "STALE"


def test_public_risk_health_counts_new_unchecked_holdings():
    current = service()
    current.update_account_symbols(("600519",))
    risk = current.workflow_health()["stages"]["public_risk"]
    assert risk["required_count"] == 2
    assert risk["missing_count"] == 2
    assert risk["unknown_count"] == 2
    assert risk["coverage_ratio"] == 0


def test_evidence_uses_captured_quote_snapshot_not_newer_store_value():
    current = service()
    captured = current.snapshot()
    quote = RealTimeQuote(
        symbol="000001", market="A", timestamp_exchange=NOW, timestamp_received=NOW,
        last=10.2, source="akshare",
    )
    current.store.put_quotes((quote,))
    assert current.validated_quote("000001")["current_price"] == 10.2
    assert current.validated_quote("000001", snapshot=captured) is None


def test_risk_read_does_not_mix_unpublished_quote_with_old_monitor_row(monkeypatch):
    current = service()
    current.state.data_quality = "GOOD"
    current.state.intraday_monitor = [{
        "symbol": "000001", "current_price": 10.2,
        "quote_timestamp": (NOW - timedelta(seconds=1)).isoformat(),
    }]
    current.store.put_quotes((RealTimeQuote(
        symbol="000001", market="A", timestamp_exchange=NOW,
        timestamp_received=NOW, last=20, source="akshare",
    ),))
    seen = []

    def calculate(plan, *, quote, data_quality, now):
        seen.append(quote)
        return {"state": "NO_RELIABLE_GUIDANCE"}

    monkeypatch.setattr(current, "_quote_guidance_payload", calculate)
    current.snapshot()
    assert seen == [None]


def test_health_and_context_http_endpoints_are_local_read_only():
    server = create_server(service=service(), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/api/workflow/health", timeout=3) as response:
            health = json.load(response)
        assert health["paper_only"] is True
        with urlopen(base + "/api/analysis/context?symbol=000001", timeout=3) as response:
            context = json.load(response)
        assert context["analysis_mode"] == "DETERMINISTIC_FACTS"
        with pytest.raises(HTTPError) as invalid:
            urlopen(base + "/api/analysis/context?symbol=../secret", timeout=3)
        assert invalid.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
