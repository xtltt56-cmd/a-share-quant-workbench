import copy
import json
import os
import threading
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from bs4 import BeautifulSoup

from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.workbench.agent_boundary import ReadonlyAgentTools
from a_share_quant.workbench.agent_credentials import AgentCredentials, validate_key
from a_share_quant.workbench.agent_research import build_research
from a_share_quant.workbench.agent_runtime import AgentRuntime, _validate_text
from a_share_quant.workbench.agent_web import WebResearch, public_html, safe_url
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.context_tools import WorkbenchContextTools
from tests.test_agent_runtime import Provider, completed
from tests.test_workbench_context_tools import service


def context():
    return WorkbenchContextTools(service()).get_stock_context("000001")


@pytest.mark.parametrize("count", [0, 20, 21, 25, 60, 61])
def test_history_metrics_bounds_and_short_drawdown(monkeypatch, count):
    start = date(2026, 6, 1)
    bars = [{"date": (start + timedelta(days=i)).isoformat(), "open": 10 + i / 100,
             "close": 10 + i / 100, "high": 12, "low": 9, "volume": 100}
            for i in range(count)]
    # Invalid and future records must not affect indicators.
    extra = [{**bars[0], "date": "2027-01-01"}, {**bars[0], "low": -1}] if bars else []
    monkeypatch.setattr("a_share_quant.workbench.agent_research.load_history",
                        lambda *args: {"bars": bars + extra, "source": "test"})
    result = build_research(context())
    assert result["sample_count"] == count
    assert bool(result["metrics"]) == (count >= 21)
    if count >= 21:
        assert result["metrics"]["drawdown"]["value"] == 0
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("value", ["short", "a" * 513, "sk-" + "a" * 20 + "\n", 42])
def test_invalid_credentials_rejected(value):
    with pytest.raises(ValueError):
        validate_key(value)


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI")
def test_native_credential_save_restart_remove_and_corrupt_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-key-not-real")
    policy = ProjectStoragePolicy(tmp_path)
    store = AgentCredentials(policy)
    secret = "sk-unit-test-not-a-real-key"
    store.save(secret)
    assert secret.encode() not in store.path.read_bytes()
    assert secret not in json.dumps(store.status())
    restored = AgentCredentials(policy)
    assert restored.get() == secret
    restored.remove()
    assert restored.get() == "environment-key-not-real"
    restored.save(secret)
    # Break ciphertext via the normal atomic writer, not a production credential.
    from a_share_quant.storage.atomic_json import atomic_write_bytes

    atomic_write_bytes(restored.path, b"broken")
    invalid = AgentCredentials(policy)
    assert invalid.status()["error"] is True
    assert invalid.get() is None  # No silent switch to a different account.


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost/", "https://127.0.0.1/",
                                  "https://192.168.1.1/", "https://[::1]/",
                                  "https://user:pass@example.com/", "https://example.com:8000/",
                                  "https://example.com/\n"])
def test_public_reader_rejects_nonpublic_links(url):
    with pytest.raises(ValueError):
        safe_url(url)


def test_web_search_dates_relevance_and_reading_state(monkeypatch):
    html = BeautifulSoup('<ul>2026-10-05 10:00 <a href="https://finance.sina.com.cn/test">'
                         '平安银行行业观察</a>2099-01-01 10:00 '
                         '<a href="https://finance.sina.com.cn/future">未来标题</a></ul>',
                         "html.parser")
    monkeypatch.setattr("a_share_quant.workbench.agent_web.public_html", lambda url: html)
    rows = [
        {"title": "平安银行政策研究", "href": "https://example.com/report", "body": "观点摘要"},
        {"title": "完全无关的产品", "href": "https://example.com/other", "body": "无关"}]
    monkeypatch.setattr("ddgs.DDGS", lambda **kwargs: SimpleNamespace(
        text=lambda *args, **kwargs: rows))
    web = WebResearch()
    result = web.search("000001", "平安银行", "行业 政策", initial=True)
    assert len(result["sources"]) == 2
    assert result["sources"]["s1"]["reading_status"] == "TITLE_ONLY"
    assert result["sources"]["s2"]["date_status"] == "UNKNOWN"
    assert "future" not in json.dumps(result)
    monkeypatch.setattr("a_share_quant.workbench.agent_web.public_html",
                        lambda url: BeautifulSoup('<article><p>' + "公开报道正文。" * 20 +
                                                   '</p></article>', "html.parser"))
    page = web.read("000001", "s1")
    assert page["sources"]["s1"]["reading_status"] == "PAGE_EXCERPT"
    assert page["sources"]["s2"]["reading_status"] == "SEARCH_SNIPPET"
    assert page["read_source_id"] == "s1"
    with pytest.raises(ValueError):
        web.read("000001", "invented")


def test_search_failure_is_explicit_and_web_disabled_is_enforced(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("private exception with sensitive data")

    monkeypatch.setattr("ddgs.DDGS", lambda **kwargs: SimpleNamespace(text=fail))
    monkeypatch.setattr("a_share_quant.workbench.agent_web.public_html", fail)
    result = WebResearch().search("000001", "平安银行", "公告", initial=True)
    assert result["status"] == "UNAVAILABLE"
    assert "sensitive" not in json.dumps(result)
    gateway = ReadonlyAgentTools(WorkbenchContextTools(service()), "000001")
    with pytest.raises(PermissionError):
        gateway.call("search_public_web", {"symbol": "000001", "query": "公告"})


def test_topic_search_can_read_macro_without_company_name(monkeypatch):
    queries = []

    def search(query, **kwargs):
        queries.append(query)
        return [{"title": "房贷贴息政策公开说明", "href": "https://www.gov.cn/example",
                 "body": "监管部门政策说明，不包含具体公司名称。"}]

    monkeypatch.setattr("ddgs.DDGS", lambda **kwargs: SimpleNamespace(text=search))
    result = WebResearch().search("000001", "平安银行", "房贷贴息 政策", scope="TOPIC")
    assert queries == ["房贷贴息 政策"]
    assert result["sources"]["s1"]["relation"] == "TOPIC_BACKGROUND"
    assert result["sources"]["s1"]["authority"] == "PRIMARY_PUBLISHER"
    assert result["sources"]["s1"]["date_status"] == "UNKNOWN"


def test_reader_pins_public_dns_and_rejects_private_redirect(monkeypatch):
    import httpx

    original = httpx.Client
    requests = []

    def response(request):
        requests.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})

    monkeypatch.setattr("a_share_quant.workbench.agent_web.socket.getaddrinfo",
                        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(response)))
    with pytest.raises(ValueError):
        public_html("https://example.com/report")
    assert len(requests) == 1


def test_unknown_risk_cannot_be_described_as_checked_clear():
    answer = {"summary_zh": "规则记录的公告检查为已查未触发。",
              "supporting_facts": [], "opposing_factors": []}
    with pytest.raises(ValueError, match="RISK_STATUS_CONTRADICTION"):
        _validate_text(answer, stock={"event_risk": {"level": "UNKNOWN"}})


@pytest.mark.parametrize("text", [
    "媒体报道不能视为公告已核验，公告风险检查尚未完成。",
    "这些线索不代表公告风险已核验。",
    "不能据此声称“已查未触发”。",
    "不能说公告检查为已检查。",
])
def test_unknown_risk_allows_explicit_negation_of_checked_claim(text):
    answer = {"summary_zh": text, "supporting_facts": [], "opposing_factors": []}
    _validate_text(answer, stock={"event_risk": {"level": "UNKNOWN"}})


@pytest.mark.parametrize("summary, support", [
    ("公告尚未检查，但公告已核验。", []),
    ("不能视为公告已核验，但公告风险已查无风险。", []),
    ("这些线索不代表", ["公告已核验。"]),
    ("风险仍需核验。", ["已查未触发。"]),
    ("不能据此声称“已查未触发”。公告已核验。", []),
])
def test_risk_negation_cannot_hide_separate_positive_claim(summary, support):
    answer = {"summary_zh": summary, "supporting_facts": support, "opposing_factors": []}
    with pytest.raises(ValueError, match="RISK_STATUS_CONTRADICTION"):
        _validate_text(answer, stock={"event_risk": {"level": "UNKNOWN"}})


def test_fabricated_web_source_id_is_rejected(monkeypatch):
    monkeypatch.setattr(WebResearch, "search", fake_search)
    gateway = ReadonlyAgentTools(WorkbenchContextTools(service()), "000001", allow_web=True)
    stock = gateway.call("get_stock_context", {"symbol": "000001"})
    research = gateway.call("get_stock_research", {"symbol": "000001"})
    web = gateway.call("search_public_web", {"symbol": "000001", "query": "政策"})
    answer = {"symbol": "000001", "status": "LIMITED", "summary_zh": "外部线索仍需核验。",
              "supporting_facts": [], "opposing_factors": [], "claims": [],
              "missing_conditions": stock["blocking_reasons"],
              "evidence_ids": [stock["evidence_id"], research["evidence_id"], web["evidence_id"]],
              "research": {"conclusion": "WAIT_FOR_DATA", "insights": [{
                  "evidence_id": web["evidence_id"], "source_ids": ["s999"],
                  "observation_zh": "存在外部线索。", "interpretation_zh": "待核查。"}],
                  "next_steps": ["核验公告原文。"]}}
    with pytest.raises(ValueError, match="CLAIM_NOT_CITED"):
        gateway.validate_explanation(answer)


def fake_search(self, symbol, name, query, **kwargs):
    self.searches += 1
    source = {"source_id": "s1", "title": name + "行业公开报道", "url": "https://example.com/report",
              "excerpt": "外部行业报道观点待核查", "published_at": None,
              "reading_status": "SEARCH_SNIPPET", "host": "example.com", "date_status": "UNKNOWN"}
    self.sources["s1"] = source
    return {"tool": "search_public_web", "symbol": symbol, "sources": {"s1": source},
            "retrieved_at": "2026-10-06T10:00:00+08:00", "limitations": ["日期未知"]}


def research_answer(answer, provider):
    pack = next(json.loads(m["content"]) for m in provider.messages if m["role"] == "user")
    answer.update(copy.deepcopy(pack["required_output"]))
    evidence = pack["web_evidence"][-1] if pack["web_evidence"] else pack["research_evidence"]
    answer["research"] = {"conclusion": "WAIT_FOR_DATA", "insights": [{
        "evidence_id": evidence["evidence_id"], "source_ids": list(evidence.get("sources", {})),
        "observation_zh": "外部报道提供行业线索，但事实尚需核验。",
        "interpretation_zh": "该线索可能改变研究条件，也可能仅为媒体观点。"}],
        "next_steps": ["核验原始公告与报道日期。"]}


@pytest.mark.parametrize("web_enabled", [False, True])
def test_research_uses_actual_issued_web_evidence_and_no_private_data(
    tmp_path, monkeypatch, web_enabled,
):
    monkeypatch.setattr(WebResearch, "search", fake_search)
    monkeypatch.setattr(WebResearch, "read", lambda self, symbol, source_id: {
        "tool": "read_public_page", "symbol": symbol, "sources": self.sources,
        "retrieved_at": "2026-10-06T10:01:00+08:00"})
    instances = []

    def provider(cfg):
        instance = Provider(cfg, mutation=research_answer)
        instances.append(instance)
        return instance

    runtime = AgentRuntime(WorkbenchContextTools(service()), tmp_path, provider_factory=provider)
    runtime.configure({"enabled": True, "backend": "LOCAL"})
    try:
        result = completed(runtime, runtime.start("000001", "research", web_research=web_enabled))
        assert result["status"] == "SUCCEEDED", result
        assert result["result"]["research"]["conclusion"] == "WAIT_FOR_DATA"
        assert bool(result["web_evidence"]) == web_enabled
        assert len(result["trace"]) == (4 if web_enabled else 2)
        assert instances[-1].cfg.max_output_tokens >= 1536
    finally:
        runtime.stop()


def test_web_cancel_before_generation_makes_no_model_request(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_search(*args, **kwargs):
        entered.set()
        release.wait(timeout=3)
        return {"tool": "search_public_web", "sources": {}, "retrieved_at": "now"}

    monkeypatch.setattr(WebResearch, "search", slow_search)
    instances = []
    runtime = AgentRuntime(WorkbenchContextTools(service()), tmp_path,
                           provider_factory=lambda cfg: instances.append(Provider(cfg)))
    runtime.configure({"enabled": True, "backend": "LOCAL"})
    try:
        job = runtime.start("000001", "research")
        assert entered.wait(timeout=3)
        runtime.cancel(job["task_id"])
        release.set()
        result = completed(runtime, job)
        assert result["status"] == "CANCELLED"
        assert not instances
        assert result["model_requests"] == 0
    finally:
        release.set()
        runtime.stop()


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI")
def test_credential_http_same_origin_secrecy_and_probe_invalidation(tmp_path):
    runtime = AgentRuntime(WorkbenchContextTools(service()), tmp_path)
    server = create_server(port=0, agent_runtime=runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    secret = "sk-unit-test-not-a-real-key"
    try:
        body = json.dumps({"api_key": secret}).encode()
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(base + "/api/agent/credential", data=body,
                            headers={"Content-Type": "application/json"}), timeout=3)
        assert denied.value.code == 403
        runtime.probe_result = {"status": "MODEL_AVAILABLE_NOT_GENERATED", "models": []}
        with urlopen(Request(base + "/api/agent/credential", data=body, headers={
            "Content-Type": "application/json", "X-Quant-Workbench-Request": "agent"}),
            timeout=3) as response:
            result = response.read().decode()
        assert secret not in result
        assert json.loads(result)["credential"]["saved"] is True
        assert runtime.probe_result["status"] == "NOT_PROBED"
        assert not runtime.cache
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        runtime.stop()
