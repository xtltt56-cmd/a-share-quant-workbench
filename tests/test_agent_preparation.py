import copy
import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from a_share_quant.storage.atomic_json import canonical_bytes, read_checked_json, write_checked_json
from a_share_quant.workbench.agent_boundary import (
    AgentPreparationConfig,
    ReadonlyAgentTools,
    load_preparation_config,
)
from a_share_quant.workbench.context_tools import WorkbenchContextTools
from tests.test_workbench_context_tools import service


def gateway(**kwargs):
    return ReadonlyAgentTools(WorkbenchContextTools(service()), "000001", **kwargs)


def answer(context):
    return {
        "symbol": "000001", "status": "LIMITED", "summary_zh": "仅解释事实；当前缺少有效指导。",
        "supporting_facts": ["排序分数不是上涨概率。"], "opposing_factors": [],
        "missing_conditions": context["blocking_reasons"],
        "evidence_ids": [context["evidence_id"]],
        "claims": [{"evidence_id": context["evidence_id"],
                    "field_path": "candidate.normalized_score", "value": 78.5}],
    }


def test_example_stays_disabled_and_does_not_contain_a_key():
    root = Path(__file__).resolve().parents[1]
    config = load_preparation_config(root / "config/agent.example.yaml")
    assert config.enabled is False
    assert config.api_key_env is None
    assert config.provider is None
    assert config.model is None
    assert config.max_cost_per_task == 0


@pytest.mark.parametrize("fields", [
    {"max_tool_calls": 7}, {"deadline_seconds": 91}, {"max_model_requests": 4},
    {"cloud_scope": "HOLDINGS"}, {"api_key_env": "actual-secret-value"},
    {"api_key": "do-not-accept"}, {"max_cost_per_task": float("nan")},
])
def test_config_rejects_unknown_or_unsafe_values(fields):
    with pytest.raises(ValidationError):
        AgentPreparationConfig(**fields)


def test_enabled_flag_does_not_pretend_sdk_is_available(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text("enabled: true\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ADAPTER_NOT_IMPLEMENTED"):
        load_preparation_config(path)


@pytest.mark.parametrize("tool", [
    "get_holding_context", "submit_order", "run_shell", "retry_daily_workflow",
])
def test_unsupported_and_account_tools_are_blocked(tool):
    with pytest.raises(PermissionError, match="NOT_ALLOWED"):
        gateway().call(tool, {})


def test_stock_scope_and_arguments_cannot_expand():
    current = gateway()
    with pytest.raises(PermissionError, match="SCOPE_REJECTED"):
        current.call("get_stock_context", {"symbol": "600519"})
    with pytest.raises(ValueError, match="ARGUMENTS_REJECTED"):
        current.call("get_workflow_health", {"include_account": True})
    assert current.calls == 0


def test_allowlisted_projection_drops_private_and_unknown_fields(monkeypatch):
    tools = WorkbenchContextTools(service())
    raw = tools.get_stock_context("000001")
    raw["positions"] = [{"average_cost": 10, "total_quantity": 100}]
    raw["candidate"]["private_account"] = "not-for-cloud"
    raw["price_guidance"].update({"suggested_quantity": 100, "average_cost": 10,
                                  "entry_lower": "9.80"})
    monkeypatch.setattr(tools, "get_stock_context", lambda symbol: copy.deepcopy(raw))
    result = ReadonlyAgentTools(tools, "000001").call("get_stock_context", {"symbol": "000001"})
    serialized = json.dumps(result)
    assert "average_cost" not in serialized
    assert "total_quantity" not in serialized
    assert "private_account" not in serialized
    assert "suggested_quantity" not in serialized
    assert result["price_guidance"]["entry_lower"] == "9.80"
    assert result["probability"] is None


def test_tool_limit_cancellation_deadline_and_late_results(monkeypatch):
    clock = [0.0]
    current = gateway(monotonic=lambda: clock[0], config=AgentPreparationConfig(max_tool_calls=1))
    current.call("get_workflow_health", {})
    with pytest.raises(RuntimeError, match="BUDGET_EXHAUSTED"):
        current.call("get_workflow_health", {})
    clock[0] = 90.0
    with pytest.raises(TimeoutError, match="DEADLINE"):
        current.call("get_workflow_health", {})
    stop = threading.Event()
    stop.set()
    with pytest.raises(RuntimeError, match="CANCELLED"):
        gateway(stop_event=stop).call("get_workflow_health", {})
    clock[0] = 0.0
    current = gateway(monotonic=lambda: clock[0])
    original = current.tools.get_stock_context

    def late(symbol):
        clock[0] = 91.0
        return original(symbol)

    monkeypatch.setattr(current.tools, "get_stock_context", late)
    with pytest.raises(TimeoutError, match="DEADLINE"):
        current.call("get_stock_context", {"symbol": "000001"})
    assert current.calls == 1


def test_context_size_limit_is_enforced(monkeypatch):
    current = gateway(config=AgentPreparationConfig(max_context_bytes=1024))
    original = current.tools.get_stock_context

    def large(symbol):
        raw = original(symbol)
        raw["supporting_facts"] = ["测" * 10000]
        return raw

    monkeypatch.setattr(current.tools, "get_stock_context", large)
    with pytest.raises(ValueError, match="TOO_LARGE"):
        current.call("get_stock_context", {"symbol": "000001"})


def test_valid_output_uses_real_issued_evidence_and_exact_values():
    current = gateway()
    context = current.call("get_stock_context", {"symbol": "000001"})
    context["candidate"]["normalized_score"] = 999  # Returned copies cannot mutate evidence.
    context["candidate"]["normalized_score"] = 78.5
    result = current.validate_explanation(answer(context))
    assert result.claims[0].value == 78.5
    assert result.manual_execution_required is True


@pytest.mark.parametrize("mutation,error", [
    ("price", "VALUE_MISMATCH"), ("evidence", "EVIDENCE_MISSING"),
    ("missing", "CONDITIONS_OMITTED"), ("status", "CONDITIONS_OMITTED"),
    ("field", "FIELD_NOT_FOUND"), ("uncited", "CLAIM_NOT_CITED"),
])
def test_invalid_agent_output_is_rejected(mutation, error):
    current = gateway()
    context = current.call("get_stock_context", {"symbol": "000001"})
    payload = answer(context)
    if mutation == "price":
        payload["claims"][0]["value"] = 79.5
    elif mutation == "evidence":
        payload["evidence_ids"] = ["sha256:" + "f" * 64]
    elif mutation == "missing":
        payload["missing_conditions"] = []
    elif mutation == "status":
        payload["status"] = "FACTS_ONLY"
    elif mutation == "field":
        payload["claims"][0]["field_path"] = "probability.fake"
    else:
        payload["claims"][0]["evidence_id"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match=error):
        current.validate_explanation(payload)


def test_changed_inputs_and_extra_probability_are_rejected():
    current = gateway()
    context = current.call("get_stock_context", {"symbol": "000001"})
    payload = answer(context)
    with pytest.raises(ValidationError):
        current.validate_explanation({**payload, "probability": 0.785})
    current.tools.service.publish_official_daily((), status="UPDATE_FAILED", notice_zh="更新失败")
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED"):
        current.validate_explanation(payload)


def test_unchecked_risk_placeholder_does_not_change_evidence_as_clock_advances():
    current = gateway()
    context = current.call("get_stock_context", {"symbol": "000001"})
    assert context["event_risk"]["checked_at"] is None
    original = current.tools.service.clock()
    current.tools.service.clock = lambda: original + timedelta(seconds=15)
    later = current._public_stock()
    assert later["observed_at"] != context["observed_at"]
    assert later["evidence_id"] == context["evidence_id"]
    current.validate_explanation(answer(context))


def test_real_risk_timestamp_change_still_invalidates_issued_evidence(monkeypatch):
    current = gateway()
    original = current.tools.get_stock_context
    checked = ["2026-10-04T02:00:00+00:00"]

    def stock(symbol):
        raw = original(symbol)
        raw["event_risk"] = {"level": "CLEAR", "reason_codes": [], "checked_at": checked[0]}
        return raw

    monkeypatch.setattr(current.tools, "get_stock_context", stock)
    context = current.call("get_stock_context", {"symbol": "000001"})
    checked[0] = "2026-10-04T02:00:01+00:00"
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED"):
        current.validate_explanation(answer(context))


def test_changed_workflow_evidence_cannot_be_released_with_unchanged_stock(monkeypatch):
    current = gateway()
    context = current.call("get_stock_context", {"symbol": "000001"})
    health = current.tools.service.workflow_health()
    monkeypatch.setattr(current.tools.service, "workflow_health", lambda: copy.deepcopy(health))
    current.call("get_workflow_health", {})
    health["status"] = "STOPPED"
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED"):
        current.validate_explanation(answer(context))


def test_checked_json_accounts_for_final_newline_in_size_budget(tmp_path):
    body = {"test": "value"}
    path = tmp_path / "bounded.json"
    write_checked_json(path, body, maximum_bytes=1024)
    exact_size = path.stat().st_size
    with pytest.raises(ValueError, match="size limit"):
        write_checked_json(path, body, maximum_bytes=exact_size - 1)
    write_checked_json(path, body, maximum_bytes=exact_size)
    assert len(canonical_bytes(read_checked_json(path, maximum_bytes=exact_size))) > 0
