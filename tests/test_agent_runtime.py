import asyncio
import copy
import json
import threading
import time
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from pydantic import ValidationError

from a_share_quant.storage.atomic_json import write_checked_json
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.workbench.agent_config import AgentRuntimeConfig
from a_share_quant.workbench.agent_runtime import AgentRuntime, BudgetLedger, _prompt
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.context_tools import WorkbenchContextTools
from tests.test_workbench_context_tools import service


def test_prompt_distinguishes_checked_clear_risk_and_workflow_health():
    prompt = _prompt()

    assert "CLEAR 不得描述为“未知”或“未检查”" in prompt
    assert "overall status" in prompt
    assert "daily 与 realtime 阶段状态" in prompt


class Provider:
    def __init__(self, cfg, *, mutation=None, tool=False, delay=0, usage=True):
        self.cfg = cfg
        self.mutation = mutation
        self.tool = tool
        self.delay = delay
        self.usage = usage
        self.calls = 0
        self.closed = False
        self.messages = []

    async def probe_local(self):
        return {"status": "READY", "models": [self.cfg.local_model]}

    async def probe_cloud(self):
        return {"status": "MODEL_AVAILABLE_NOT_GENERATED", "models": [self.cfg.cloud_model]}

    async def request(self, messages):
        self.calls += 1
        self.messages = copy.deepcopy(messages)
        await asyncio.sleep(self.delay)
        context = next(json.loads(message["content"])["evidence"] for message in messages
                       if message["role"] == "user")
        if self.tool and self.calls == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [{
                "id": "test-call", "type": "function", "function": {
                    "name": "get_workflow_health", "arguments": {},
                },
            }]}, "model": (self.cfg.local_model if self.cfg.backend == "LOCAL"
                           else self.cfg.cloud_model), "usage": {"input": 100, "output": 10}}
        payload = {
            "symbol": context["symbol"],
            "status": "LIMITED" if context["blocking_reasons"] else "FACTS_ONLY",
            "summary_zh": "仅依据后台事实解释，缺失条件须核验，不能保证股价表现。",
            "supporting_facts": ["候选分数用于排序，并非上涨概率。"],
            "opposing_factors": [], "missing_conditions": context["blocking_reasons"],
            "evidence_ids": [context["evidence_id"]], "claims": [],
        }
        if self.mutation:
            self.mutation(payload, self)
        return {"message": {"role": "assistant", "content": json.dumps(payload)},
                "model": (self.cfg.local_model if self.cfg.backend == "LOCAL"
                          else self.cfg.cloud_model), "usage": {"input": 100, "output": 20}
                if self.usage else None}

    async def close(self):
        self.closed = True


@pytest.fixture
def runtime_factory(tmp_path, monkeypatch):
    # Test doubles are not real SDK/model validation.
    monkeypatch.setattr("a_share_quant.workbench.agent_runtime.importlib.util.find_spec",
                        lambda name: object())
    created = []

    def factory(*, root=True, **kwargs):
        instances = []

        def provider(cfg):
            instance = Provider(cfg, **kwargs)
            instances.append(instance)
            return instance

        runtime = AgentRuntime(WorkbenchContextTools(service()), tmp_path if root else None,
                               provider_factory=provider)
        created.append(runtime)
        runtime.configure({"enabled": True, "backend": "LOCAL"})
        return runtime, instances

    yield factory
    for runtime in created:
        runtime.stop()


def completed(runtime, job):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if runtime.thread and not runtime.thread.is_alive():
            return runtime.task(job["task_id"])
        time.sleep(0.01)
    pytest.fail("test task did not terminate")


# Versioned 40-case contract matrix: four supported questions x ten input conditions.
# This is deliberately not described as forty real-model evaluations.
@pytest.mark.parametrize("question", ["selection", "guidance", "freshness", "workflow"])
@pytest.mark.parametrize("condition", [
    "baseline", "stale", "no_guidance", "quote_missing", "risk_review",
    "future", "candidate_missing", "provider_failed", "history_missing", "injection",
])
def test_v1_forty_public_snapshot_contracts(runtime_factory, monkeypatch, question, condition):
    runtime, providers = runtime_factory(tool=question == "workflow")
    original = runtime.tools.get_stock_context

    def snapshot(symbol):
        raw = original(symbol)
        if condition == "stale":
            raw.update(input_cutoff="2026-09-29", daily_status="STALE_DATA")
            raw["blocking_reasons"].append("DAILY_INPUT_STALE")
        elif condition == "no_guidance":
            raw["price_guidance"] = None
            raw["blocking_reasons"].append("PRICE_PLAN_MISSING")
        elif condition == "quote_missing":
            raw["quote"] = None
            raw["blocking_reasons"].append("NO_VALIDATED_REALTIME_QUOTE")
        elif condition == "risk_review":
            raw["event_risk"] = {"level": "REVIEW", "reason_codes": ["EARNINGS_RISK"],
                                  "checked_at": raw["observed_at"]}
            raw["blocking_reasons"].append("EARNINGS_RISK")
        elif condition == "future":
            raw["input_cutoff"] = "2026-10-09"
            raw["blocking_reasons"].append("DAILY_INPUT_FUTURE")
        elif condition == "candidate_missing":
            raw["candidate"] = None
            raw["blocking_reasons"].append("NOT_IN_CURRENT_CANDIDATES")
        elif condition == "provider_failed":
            raw["daily_status"] = "UPDATE_FAILED"
            raw["blocking_reasons"].append("DAILY_UPDATE_FAILED")
        elif condition == "history_missing":
            raw["price_guidance"] = {"state": "NO_RELIABLE_GUIDANCE",
                                      "reason_codes": ["INSUFFICIENT_HISTORY"]}
            raw["blocking_reasons"].append("INSUFFICIENT_HISTORY")
        if condition == "injection":
            raw["positions"] = [{"private_account": "do-not-send"}]
            raw["candidate"]["private_credential"] = "do-not-send"
        return raw

    monkeypatch.setattr(runtime.tools, "get_stock_context", snapshot)
    job = completed(runtime, runtime.start("000001", question))
    assert job["status"] == "SUCCEEDED"
    assert job["result"]["status"] == "LIMITED"
    assert job["result"]["manual_execution_required"] is True
    assert "do-not-send" not in json.dumps(providers[-1].messages)
    assert providers[-1].closed
    assert job["model_requests"] == (2 if question == "workflow" else 1)
    assert job["cost_usd"] is None


@pytest.mark.parametrize("text", ["参考价格 10 元", "预计上涨２０％", "可以买一百股", "百分之十",
                                      "建议买入", "稳赚", "保证收益"])
def test_free_text_price_quantity_and_guarantee_rejected(runtime_factory, text):
    runtime, _ = runtime_factory(mutation=lambda answer, p: answer.update(summary_zh=text))
    result = completed(runtime, runtime.start("000001", "guidance"))
    assert result["status"] == "FAILED"
    assert result["result"] is None


def test_output_schema_errors_are_sanitized_and_diagnosable(runtime_factory):
    runtime, _ = runtime_factory(
        mutation=lambda answer, _: answer.update({"untrusted-secret-shaped-key": "not disclosed"})
    )
    result = completed(runtime, runtime.start("000001", "guidance"))
    assert result["status"] == "FAILED"
    assert result["failure_code"] == "AGENT_OUTPUT_SCHEMA_INVALID"
    assert result["failure_fields"] == ["结构不符"]
    assert "untrusted-secret-shaped-key" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    lambda a, p: a.update(missing_conditions=[]),
    lambda a, p: a.update(symbol="600519"),
    lambda a, p: a.update(status="FACTS_ONLY"),
    lambda a, p: a.update(evidence_ids=["sha256:" + "f" * 64]),
    lambda a, p: a.update(probability=0.99),
])
def test_fabricated_or_omitted_evidence_rejected(runtime_factory, change):
    runtime, _ = runtime_factory(mutation=change)
    assert completed(runtime, runtime.start("000001", "guidance"))["status"] == "FAILED"


def test_cancel_duplicate_mode_lock_and_cleanup(runtime_factory):
    runtime, providers = runtime_factory(delay=30)
    job = runtime.start("000001", "guidance")
    assert runtime.start("000001", "guidance")["task_id"] == job["task_id"]
    with pytest.raises(RuntimeError):
        runtime.start("600519", "guidance")
    with pytest.raises(RuntimeError):
        runtime.configure({"enabled": False})
    time.sleep(0.1)
    runtime.cancel(job["task_id"])
    result = completed(runtime, job)
    assert result["status"] == "CANCELLED"
    assert providers[-1].closed
    runtime.configure({"enabled": False})
    with pytest.raises(RuntimeError):
        runtime.start("000001", "guidance")


def test_deadline_terminates_request(runtime_factory):
    runtime, providers = runtime_factory(delay=30)
    runtime.configure({"enabled": True, "backend": "LOCAL", "deadline_seconds": 5})
    result = completed(runtime, runtime.start("000001", "guidance"))
    assert result["status"] == "TIMEOUT"
    assert providers[-1].closed


def test_changed_data_invalidates_visible_result_and_cache(runtime_factory):
    runtime, providers = runtime_factory()
    first = completed(runtime, runtime.start("000001", "guidance"))
    again = completed(runtime, runtime.start("000001", "guidance"))
    assert again["cached"] is True
    for field in ("actual_model", "source", "data_cutoff", "generated_at", "prompt_version"):
        assert again[field] == first[field]
    assert again["usage"] == []
    assert sum(p.calls for p in providers) == 1
    runtime.tools.service.publish_official_daily((), status="UPDATE_FAILED", notice_zh="更新失败")
    assert runtime.task(first["task_id"])["status"] == "STALE"
    changed = completed(runtime, runtime.start("000001", "guidance"))
    assert changed["evidence_id"] != first["evidence_id"]
    assert sum(p.calls for p in providers) == 2


@pytest.mark.parametrize("payload", [
    {"api_key": "must-not-store"}, {"backend": "OTHER"}, {"local_model": "qwen-cloud"},
    {"local_url": "http://remote.test"}, {"enabled": True, "backend": "CLOUD"},
    {"daily_budget_usd": "NaN"}, {"daily_budget_usd": "-1"}, {"cloud_model": "unknown"},
])
def test_unsafe_config_rejected(payload):
    with pytest.raises(ValidationError):
        AgentRuntimeConfig.model_validate(payload)


def cloud_settings(**extra):
    return {"enabled": True, "backend": "CLOUD", "payment_authorized": True,
            "daily_budget_usd": "0.10", "task_budget_usd": "0.02",
            "experiment_budget_cny": "3", **extra}


def authorize_cloud(runtime, **extra):
    runtime.configure(cloud_settings(**extra))
    runtime.probe()
    runtime.configure(cloud_settings(generation_authorized=True, **extra))


def test_cloud_credentials_and_positive_budget_required(runtime_factory, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    runtime, providers = runtime_factory()
    runtime.configure(cloud_settings())
    assert runtime.status()["status"] == "NOT_CONFIGURED"
    with pytest.raises(RuntimeError):
        runtime.start("000001", "guidance")
    assert not providers


def test_cloud_budget_and_unknown_usage_survive_restart(runtime_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key-not-real")
    runtime, providers = runtime_factory(usage=False)
    authorize_cloud(runtime, daily_budget_usd="0.001")
    result = completed(runtime, runtime.start("000001", "guidance"))
    assert result["status"] == "BUDGET_EXHAUSTED"
    assert providers[-1].calls == 0
    authorize_cloud(runtime)
    result = completed(runtime, runtime.start("000001", "guidance"))
    assert result["status"] == "SUCCEEDED"
    assert result["cost_status"] == "CONSERVATIVE_RESERVED"
    reserved = result["cost_usd"]
    restarted = AgentRuntime(runtime.tools, tmp_path, provider_factory=lambda c: Provider(c))
    try:
        assert restarted.config.backend == "CLOUD"
        assert list(restarted.budget.read()["days"].values()) == [reserved]
        assert "unit-test-key" not in json.dumps(restarted.status())
        assert "unit-test-key" not in restarted.settings_path.read_text()
    finally:
        restarted.stop()


def test_real_usage_settles_reservation(runtime_factory, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key-not-real")
    runtime, _ = runtime_factory(tool=True)
    authorize_cloud(runtime)
    result = completed(runtime, runtime.start("000001", "workflow"))
    assert result["status"] == "SUCCEEDED"
    assert Decimal(result["cost_usd"]) == Decimal("0.000096")
    assert Decimal(result["cost_cny"]) == Decimal("0.00064")
    assert result["cost_status"] == "ESTIMATED_USAGE"


def test_budget_interprocess_lock_prevents_double_reservation(tmp_path):
    first = BudgetLedger(ProjectStoragePolicy(tmp_path))
    second = BudgetLedger(ProjectStoragePolicy(tmp_path))
    cfg = AgentRuntimeConfig.model_validate(cloud_settings(daily_budget_usd="0.02"))
    outcomes = []

    def reserve(ledger):
        try:
            ledger.reserve(Decimal("0.012"), cfg, Decimal(0), amount_cny=Decimal("0.08"))
            outcomes.append("reserved")
        except RuntimeError:
            outcomes.append("blocked")

    threads = [threading.Thread(target=reserve, args=(ledger,)) for ledger in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["blocked", "reserved"]


def test_bad_saved_config_does_not_enable_or_break_workflow(tmp_path):
    path = tmp_path / ".runtime/agent/settings.json"
    write_checked_json(path, {"enabled": True, "api_key": "reject"}, maximum_bytes=16384)
    runtime = AgentRuntime(WorkbenchContextTools(service()), tmp_path)
    assert runtime.status()["status"] == "CONFIG_REJECTED"
    assert runtime.config.enabled is False
    assert runtime.tools.get_stock_context("000001")["symbol"] == "000001"


@pytest.mark.parametrize("days", [{"2026-10-05": "NaN"}, {"not-date": "1"},
                                  {"2026-10-05": "-1"}, {"2026-10-05": 0}])
def test_corrupted_budget_fails_closed_without_breaking_standard_workflow(
    runtime_factory, monkeypatch, days,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key-not-real")
    runtime, providers = runtime_factory()
    runtime.configure(cloud_settings())
    write_checked_json(runtime.budget.path, {"schema_version": 1, "days": days},
                       maximum_bytes=524288)
    assert runtime.status()["status"] == "BUDGET_UNAVAILABLE"
    with pytest.raises(RuntimeError):
        runtime.start("000001", "guidance")
    assert providers == []
    runtime.configure({"enabled": False})
    assert runtime.status()["status"] == "NOT_ENABLED"
    assert runtime.tools.get_stock_context("000001")["symbol"] == "000001"


def test_fee_authorization_does_not_allow_generation(runtime_factory, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key-not-real")
    runtime, providers = runtime_factory()
    runtime.configure(cloud_settings())
    assert runtime.status()["status"] == "AWAITING_APPROVAL"
    with pytest.raises(RuntimeError, match="AGENT_NOT_READY"):
        runtime.start("000001", "guidance")
    with pytest.raises(RuntimeError, match="CONNECTION_NOT_VERIFIED"):
        runtime.configure(cloud_settings(generation_authorized=True))
    assert not providers
    runtime.probe()
    assert runtime.status()["probe"]["status"] == "MODEL_AVAILABLE_NOT_GENERATED"
    assert runtime.status()["status"] == "AWAITING_APPROVAL"
    assert all(provider.calls == 0 for provider in providers)
    assert runtime.budget.read()["experiment"]["reserved_and_spent"] == "0"
    runtime.configure(cloud_settings(generation_authorized=True))
    assert runtime.status()["status"] == "READY"


@pytest.mark.parametrize("payload", [
    {"daily_budget_usd": "0.11"}, {"task_budget_usd": "0.03"},
    {"experiment_budget_cny": "3.01"}, {"experiment_budget_cny": "NaN"},
    {"generation_authorized": True, "experiment_budget_cny": "0"},
])
def test_experiment_authorization_limits_cannot_be_raised(payload):
    with pytest.raises(ValidationError):
        AgentRuntimeConfig.model_validate(cloud_settings(**payload))


def test_experiment_total_survives_restart_daily_rollover_and_disable(tmp_path):
    ledger = BudgetLedger(ProjectStoragePolicy(tmp_path))
    cfg = AgentRuntimeConfig.model_validate(cloud_settings())
    ledger.approve_experiment(Decimal("3"))
    day = ledger.reserve(Decimal("0.01"), cfg, Decimal(0), amount_cny=Decimal("2.90"))
    ledger.settle(day, Decimal("0.01"), None,
                  reserved_cny=Decimal("2.90"), actual_cny=None)
    restarted = BudgetLedger(ProjectStoragePolicy(tmp_path))
    body = restarted.read()
    body["days"] = {"2026-01-01": body["days"][day]}
    write_checked_json(restarted.path, body, maximum_bytes=524288)
    restarted.approve_experiment(Decimal("3"))
    assert restarted.read()["experiment"]["reserved_and_spent"] == "2.90"
    disabled_trial = AgentRuntimeConfig.model_validate(cloud_settings(experiment_budget_cny="0"))
    for new_cfg in (cfg, disabled_trial):
        with pytest.raises(RuntimeError, match="BUDGET_EXHAUSTED"):
            restarted.reserve(Decimal("0.001"), new_cfg, Decimal(0), amount_cny=Decimal("0.11"))
    assert restarted.read()["experiment"]["reserved_and_spent"] == "2.90"


def test_cny_and_usd_reservations_and_settlement_are_atomic(tmp_path):
    ledger = BudgetLedger(ProjectStoragePolicy(tmp_path))
    cfg = AgentRuntimeConfig.model_validate(cloud_settings())
    ledger.approve_experiment(Decimal("3"))
    before = ledger.read()
    with pytest.raises(RuntimeError, match="BUDGET_EXHAUSTED"):
        ledger.reserve(Decimal("0.021"), cfg, Decimal(0), amount_cny=Decimal("0.01"))
    assert ledger.read() == before
    day = ledger.reserve(Decimal("0.01"), cfg, Decimal(0), amount_cny=Decimal("0.08"))
    ledger.settle(day, Decimal("0.01"), Decimal("0.001"),
                  reserved_cny=Decimal("0.08"), actual_cny=Decimal("0.006"))
    assert Decimal(ledger.read()["days"][day]) == Decimal("0.001")
    assert Decimal(ledger.read()["experiment"]["reserved_and_spent"]) == Decimal("0.006")


def test_agent_api_requires_local_origin_header_and_rejects_free_text(runtime_factory):
    runtime, _ = runtime_factory()
    server = create_server(service=runtime.tools.service, agent_runtime=runtime, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, body, **headers):
        return urlopen(Request(url + path, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json", "X-Quant-Workbench-Request": "agent",
            **headers,
        }), timeout=3)

    try:
        with post("/api/agent/config", {"enabled": False}) as response:
            assert json.load(response)["status"] == "NOT_ENABLED"
        for headers in ({"Origin": "http://evil.example"},
                        {"X-Quant-Workbench-Request": "wrong"}, {"Host": "evil.example"}):
            with pytest.raises(HTTPError) as error:
                post("/api/agent/config", {"enabled": True}, **headers)
            assert error.value.code == 403
        with pytest.raises(HTTPError) as error:
            post("/api/agent/start", {"symbol": "000001", "question": "guidance",
                                      "free_text": "private account"})
        assert error.value.code == 400
        with urlopen(url + "/api/agent/status") as response:
            assert json.load(response)["enabled"] is False
        with urlopen(url + "/api/workflow/health") as response:
            assert json.load(response)["stages"]["agent"]["status"] == "NOT_ENABLED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
