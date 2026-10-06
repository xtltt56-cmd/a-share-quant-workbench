"""Small cancellable adapters around official SDKs; no automatic retries."""

from __future__ import annotations

import json
import os
from typing import Any

from a_share_quant.workbench.agent_config import AgentRuntimeConfig

TOOLS = [
    {"type": "function", "function": {
        "name": "search_public_web", "description": "按当前股票检索新闻、行业政策或公司公开信息。",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"},
                       "query": {"type": "string"}, "scope": {
                           "type": "string", "enum": ["COMPANY", "TOPIC"],
                           "description": "COMPANY 查公司；TOPIC 查相关行业、政策或宏观背景。"}},
                       "required": ["symbol", "query"],
                       "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_public_page", "description": "阅读本次已检索来源的正文节选。",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"},
                       "source_id": {"type": "string"}}, "required": ["symbol", "source_id"],
                       "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "get_stock_research",
        "description": "读取当前股票的补充历史风险指标和公告标题证据。",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}},
                       "required": ["symbol"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "get_stock_context", "description": "读取当前指定股票的公开事实和阻塞原因。",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}},
                       "required": ["symbol"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "get_workflow_health", "description": "读取脱敏工作流状态，不修改任务。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
]


class SDKProvider:
    def __init__(self, config: AgentRuntimeConfig) -> None:
        self.config = config
        self.client: Any = None
        self.api_key: str | None = None

    async def probe_local(self) -> dict[str, Any]:
        from ollama import AsyncClient

        client = AsyncClient(host="http://127.0.0.1:11434", timeout=4,
                             trust_env=False, follow_redirects=False)
        try:
            tags = await client.list()
            names = [item.model for item in tags.models if item.model]
            if self.config.local_model not in names:
                return {"status": "MODEL_MISSING", "models": names}
            # Ollama 0.6.3's ShowResponse drops remote_* fields. Inspect the
            # unfiltered response via its existing, loopback-only HTTP client.
            response = await client._client.post(
                "/api/show", json={"model": self.config.local_model},
            )
            response.raise_for_status()
            if len(response.content) > 524288:
                raise ValueError("AGENT_MODEL_METADATA_REJECTED")
            raw = response.json()
            if (not isinstance(raw, dict) or raw.get("remote_host") or raw.get("remote_model")):
                raise ValueError("AGENT_REMOTE_MODEL_REJECTED")
            info = raw.get("model_info")
            if (not isinstance(info, dict) or not info.get("general.architecture")
                or type(info.get("general.parameter_count")) is not int
                or info["general.parameter_count"] <= 0):
                raise ValueError("AGENT_LOCAL_WEIGHTS_NOT_VERIFIED")
            if "tools" not in (raw.get("capabilities") or []):
                return {"status": "TOOLS_UNSUPPORTED", "models": names}
            return {"status": "READY", "models": names,
                    "notice_zh": "模型及工具接口可用；任务质量仍需实际评测。"}
        finally:
            await client._client.aclose()  # SDK exposes no public close method in 0.6.3.

    async def request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        cfg = self.config
        if cfg.backend == "LOCAL":
            from ollama import AsyncClient

            if self.client is None:
                self.client = AsyncClient(host="http://127.0.0.1:11434", timeout=90,
                                          trust_env=False, follow_redirects=False)
            reply = await self.client.chat(
                model=cfg.local_model, messages=messages, tools=TOOLS, think=False, format="json",
                options={"num_ctx": cfg.local_context_tokens,
                         "num_predict": cfg.max_output_tokens, "temperature": 0},
                keep_alive="5m",
            )
            if reply.done is not True or reply.done_reason not in {None, "stop"}:
                raise ValueError("AGENT_INCOMPLETE_REPLY")
            message = reply.message.model_dump(exclude_none=True)
            message.pop("thinking", None)
            return {"message": message, "model": reply.model,
                    "usage": {"input": reply.prompt_eval_count, "output": reply.eval_count}}
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient

        if self.client is None:
            self.client = AsyncOpenAI(
                api_key=self.api_key or os.environ[cfg.api_key_env],
                base_url="https://api.deepseek.com",
                max_retries=0, timeout=90,
                http_client=DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False),
            )
        reply = await self.client.chat.completions.create(
            model=cfg.cloud_model, messages=messages, tools=TOOLS,
            max_tokens=cfg.max_output_tokens,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        if not reply.choices or reply.choices[0].finish_reason not in {"stop", "tool_calls"}:
            raise ValueError("AGENT_INCOMPLETE_REPLY")
        message = reply.choices[0].message.model_dump(exclude_none=True)
        message.pop("reasoning_content", None)
        usage = reply.usage
        return {"message": message, "model": reply.model,
                "usage": {"input": usage.prompt_tokens, "output": usage.completion_tokens}
                if usage else None}

    async def probe_cloud(self) -> dict[str, Any]:
        """Authentication/model discovery only: no generated tokens or stock upload."""
        from openai import APIConnectionError, APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient

        client = AsyncOpenAI(
            api_key=self.api_key or os.environ[self.config.api_key_env],
            base_url="https://api.deepseek.com", max_retries=0, timeout=8,
            http_client=DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False),
        )
        try:
            rows = await client.models.list()
            models = [row.id[:128] for row in rows.data[:32]]
            available = self.config.cloud_model in models
            return {"status": "MODEL_AVAILABLE_NOT_GENERATED" if available else "MODEL_MISSING",
                    "models": models,
                    "notice_zh": ("官方接口及模型列表已核验；尚未进行收费生成或质量验收。"
                                  if available else "官方模型列表没有所选模型；暂停生成。")}
        except APIStatusError as exc:
            code = {401: "API_KEY_REJECTED", 403: "ACCESS_DENIED"}.get(exc.status_code, "FAILED")
            return {"status": code, "models": [],
                    "notice_zh": "官方接口未通过凭据或访问核验；未发送生成请求。"}
        except APIConnectionError:
            return {"status": "FAILED", "models": [],
                    "notice_zh": "暂时无法连接官方接口；未发送生成请求。"}
        finally:
            await client.close()

    async def close(self) -> None:
        if self.client is not None:
            if self.config.backend == "LOCAL":
                await self.client._client.aclose()
            else:
                await self.client.close()


def tool_arguments(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if call.get("type", "function") != "function":
        raise ValueError("AGENT_TOOL_NOT_ALLOWED")
    function = call.get("function", {})
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict) or not isinstance(function.get("name"), str):
        raise ValueError("AGENT_ARGUMENTS_REJECTED")
    return function["name"], arguments
