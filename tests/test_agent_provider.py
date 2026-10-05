"""Exercise real SDK serialization against in-process transports, not live APIs."""
import asyncio
import json

import pytest

from a_share_quant.workbench.agent_config import AgentRuntimeConfig
from a_share_quant.workbench.agent_provider import SDKProvider, tool_arguments


@pytest.mark.parametrize("remote", [False, True])
def test_official_ollama_probe_and_chat_protocol(monkeypatch, remote):
    ollama = pytest.importorskip("ollama")
    httpx = pytest.importorskip("httpx")
    requests = []
    original = ollama.AsyncClient

    def respond(request):
        requests.append(request)
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"model": "qwen-test:9b"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"general.architecture": "qwen35",
                                                           "general.parameter_count": 9000000000},
                                             "capabilities": ["tools", "completion"],
                                             **({"remote_host": "https://remote.example",
                                                 "remote_model": "qwen-cloud"} if remote else {})})
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["think"] is False
        assert body["format"] == "json"
        assert body["options"]["num_ctx"] == 4096
        assert body["options"]["num_predict"] == 512
        assert len(body["tools"]) == 2
        return httpx.Response(200, json={
            "model": "qwen-test:9b", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": "", "thinking": "do-not-expose",
                        "tool_calls": [{"function": {"name": "get_workflow_health",
                                                      "arguments": {}}}]},
            "prompt_eval_count": 100, "eval_count": 20,
        })

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(ollama, "AsyncClient", client)

    async def exercise():
        provider = SDKProvider(AgentRuntimeConfig(backend="LOCAL", local_model="qwen-test:9b"))
        try:
            if remote:
                with pytest.raises(ValueError, match="REMOTE_MODEL_REJECTED"):
                    await provider.probe_local()
                assert len(requests) == 2
                return
            assert (await provider.probe_local())["status"] == "READY"
            reply = await provider.request([{"role": "user", "content": "公开事实"}])
            assert "thinking" not in reply["message"]
            assert reply["usage"] == {"input": 100, "output": 20}
            assert tool_arguments(reply["message"]["tool_calls"][0]) == (
                "get_workflow_health", {},
            )
        finally:
            await provider.close()

    asyncio.run(exercise())


def test_official_openai_sdk_deepseek_protocol_and_tool_roundtrip(monkeypatch):
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    original = openai.DefaultAsyncHttpxClient
    requests = []

    def respond(request):
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        assert body["model"] == "deepseek-flash"
        assert body["max_tokens"] == 512
        assert body["response_format"] == {"type": "json_object"}
        if len(requests) == 1:
            message = {"role": "assistant", "content": None, "reasoning_content": "do-not-send",
                       "tool_calls": [{"id": "tool-qa", "type": "function", "function": {
                           "name": "get_workflow_health", "arguments": "{}",
                       }}]}
            finish = "tool_calls"
        else:
            assert body["messages"][-1]["tool_call_id"] == "tool-qa"
            assert "reasoning_content" not in body["messages"][-2]
            message = {"role": "assistant", "content": "{}"}
            finish = "stop"
        return httpx2.Response(200, json={
            "id": "chat-qa", "created": 1, "object": "chat.completion",
            "model": "deepseek-flash", "choices": [{"index": 0, "finish_reason": finish,
                                                        "message": message}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        })

    def client(**kwargs):
        assert kwargs == {"trust_env": False, "follow_redirects": False}
        return original(**kwargs, transport=httpx2.MockTransport(respond))

    monkeypatch.setattr(openai, "DefaultAsyncHttpxClient", client)

    async def exercise():
        provider = SDKProvider(AgentRuntimeConfig(backend="CLOUD"))
        provider.api_key = "unit-test-not-real"
        try:
            messages = [{"role": "user", "content": "公开事实"}]
            first = await provider.request(messages)
            assert "reasoning_content" not in first["message"]
            assert tool_arguments(first["message"]["tool_calls"][0]) == (
                "get_workflow_health", {},
            )
            messages += [first["message"], {"role": "tool", "tool_call_id": "tool-qa",
                                           "content": "{}"}]
            second = await provider.request(messages)
            assert second["usage"] == {"input": 100, "output": 20}
            assert second["message"]["content"] == "{}"
        finally:
            await provider.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("http_status,expected", [
    (200, "MODEL_AVAILABLE_NOT_GENERATED"), (401, "API_KEY_REJECTED"),
    (403, "ACCESS_DENIED"), (503, "FAILED"),
])
def test_cloud_probe_uses_only_model_list_never_generation(monkeypatch, http_status, expected):
    openai = pytest.importorskip("openai")
    httpx2 = pytest.importorskip("httpx2")
    original = openai.DefaultAsyncHttpxClient
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://api.deepseek.com/models"
        assert request.content == b""
        body = ({"object": "list", "data": [{"id": "deepseek-flash", "object": "model"}]}
                if http_status == 200 else {"error": {"message": "do-not-disclose-raw-error"}})
        return httpx2.Response(http_status, json=body)

    def client(**kwargs):
        assert kwargs == {"trust_env": False, "follow_redirects": False}
        return original(**kwargs, transport=httpx2.MockTransport(respond))

    monkeypatch.setattr(openai, "DefaultAsyncHttpxClient", client)

    async def exercise():
        provider = SDKProvider(AgentRuntimeConfig())
        provider.api_key = "unit-test-not-real"
        try:
            result = await provider.probe_cloud()
            assert result["status"] == expected
            assert "do-not-disclose" not in json.dumps(result)
            assert len(requests) == 1
        finally:
            await provider.close()

    asyncio.run(exercise())
