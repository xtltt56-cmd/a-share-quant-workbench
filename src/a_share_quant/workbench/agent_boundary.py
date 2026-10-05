"""Provider-neutral evidence boundary; no SDK, network or account writes.

The legacy example config stays disabled. The optional agent_runtime separately
enforces model/cost budgets, one active task and interruptible SDK cancellation.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.storage.atomic_json import canonical_bytes
from a_share_quant.workbench.context_tools import WorkbenchContextTools


class AgentPreparationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: bool = False
    provider: str | None = Field(default=None, min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,95}$")
    max_model_requests: int = Field(default=3, ge=1, le=3)
    max_tool_calls: int = Field(default=6, ge=1, le=6)
    deadline_seconds: int = Field(default=90, ge=1, le=90)
    max_context_bytes: int = Field(default=65536, ge=1024, le=65536)
    max_cost_per_task: float = Field(default=0, ge=0, allow_inf_nan=False)
    cloud_scope: Literal["PUBLIC_STOCK_ONLY"] = "PUBLIC_STOCK_ONLY"


def load_preparation_config(path: Path) -> AgentPreparationConfig:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("AGENT_CONFIG_REJECTED")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = AgentPreparationConfig.model_validate(payload)
    if config.enabled:
        # Setting a flag is not proof of a reviewed, available SDK connection.
        raise RuntimeError("AGENT_ADAPTER_NOT_IMPLEMENTED")
    return config


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    evidence_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    field_path: str = Field(min_length=1, max_length=128)
    value: str | int | float | bool | None


ShortText = Annotated[str, Field(min_length=1, max_length=500)]


class AgentExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    mode: Literal["AGENT_ANALYSIS"] = "AGENT_ANALYSIS"
    symbol: str = Field(pattern=r"^\d{6}$")
    status: Literal["FACTS_ONLY", "LIMITED", "UNAVAILABLE"]
    summary_zh: str = Field(min_length=1, max_length=2000)
    supporting_facts: list[ShortText] = Field(max_length=12)
    opposing_factors: list[ShortText] = Field(max_length=12)
    missing_conditions: list[ShortText] = Field(max_length=64)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)
    claims: list[EvidenceClaim] = Field(max_length=24)
    manual_execution_required: Literal[True] = True


_PUBLIC_FIELDS = {
    "candidate": ("symbol", "name", "normalized_score", "strategy_version", "source",
                  "signal_date", "data_cutoff", "reasons"),
    "quote": ("symbol", "current_price", "change_pct", "quote_timestamp", "source",
              "data_quality"),
    "price_guidance": ("plan_id", "state", "guidance_level", "calculation_date", "valid_for",
                       "entry_lower", "entry_upper", "maximum_acceptable_price",
                       "invalidation_price", "model_version", "evidence_cutoff",
                       "reason_codes", "notice_zh", "manual_execution_required"),
    "event_risk": ("level", "reason_codes", "checked_at"),
}


class ReadonlyAgentTools:
    """Small preflight gateway with a pinned stock scope and no holding tool.

    Only the whitelisted public projection is safe to pass to a future adapter;
    raw service/holding responses and free-form logs must not be sent instead.
    Local snapshot calls are synchronous; the deadline rejects late results but
    is not a way to forcibly cancel arbitrary Python code or remote SDK calls.
    """

    def __init__(
        self, tools: WorkbenchContextTools, symbol: str, *,
        config: AgentPreparationConfig | None = None,
        stop_event: threading.Event | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tools = tools
        self.symbol = normalize_symbol(symbol)
        self.config = config or AgentPreparationConfig()
        self.stop_event = stop_event or threading.Event()
        self.monotonic = monotonic
        self.deadline = monotonic() + self.config.deadline_seconds
        self.calls = 0
        self._evidence: dict[str, dict[str, Any]] = {}
        self._stock_evidence_id: str | None = None

    def _check_active(self) -> None:
        if self.stop_event.is_set():
            raise RuntimeError("AGENT_CANCELLED")
        if self.monotonic() >= self.deadline:
            raise TimeoutError("AGENT_DEADLINE")

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._check_active()
        if name not in {"get_stock_context", "get_workflow_health"}:
            raise PermissionError("AGENT_TOOL_NOT_ALLOWED")
        if name == "get_stock_context":
            if set(arguments) != {"symbol"} or not isinstance(arguments["symbol"], str):
                raise ValueError("AGENT_ARGUMENTS_REJECTED")
            if normalize_symbol(arguments["symbol"]) != self.symbol:
                raise PermissionError("AGENT_STOCK_SCOPE_REJECTED")
        elif arguments:
            raise ValueError("AGENT_ARGUMENTS_REJECTED")
        if self.calls >= self.config.max_tool_calls:
            raise RuntimeError("AGENT_TOOL_BUDGET_EXHAUSTED")
        # Failed/late calls still spend the allowance, avoiding unlimited retries.
        self.calls += 1
        if name == "get_stock_context":
            payload = self._public_stock()
            self._stock_evidence_id = payload["evidence_id"]
        else:
            payload = self._public_health()
        self._check_active()
        if len(canonical_bytes(payload)) > self.config.max_context_bytes:
            raise ValueError("AGENT_CONTEXT_TOO_LARGE")
        self._evidence[payload["evidence_id"]] = copy.deepcopy(payload)
        return copy.deepcopy(payload)

    def _public_stock(self) -> dict[str, Any]:
        raw = self.tools.get_stock_context(self.symbol)
        result = {field: raw.get(field) for field in (
            "schema_version", "symbol", "name", "input_cutoff", "expected_session",
            "daily_status", "blocking_reasons", "source", "supporting_facts",
        )}
        for key, fields in _PUBLIC_FIELDS.items():
            source = raw.get(key)
            result[key] = (
                {field: source[field] for field in fields if field in source}
                if isinstance(source, dict) else None
            )
        result.update({"probability": None, "manual_execution_required": True})
        return _evidence(result, observed_at=raw["observed_at"])

    def _public_health(self) -> dict[str, Any]:
        raw = self.tools.service.workflow_health()
        return _evidence({
            "schema_version": 1, "tool": "get_workflow_health",
            "mode": raw["mode"], "status": raw["status"], "calendar": raw["calendar"],
            "stages": {
                key: {field: raw["stages"][key].get(field) for field in fields}
                for key, fields in {
                    "daily": ("status", "input_cutoff", "expected_session"),
                    "realtime": ("status", "ready", "source", "quote_timestamp"),
                }.items()
            },
        }, observed_at=raw["observed_at"])

    def validate_explanation(self, payload: dict[str, Any]) -> AgentExplanation:
        """Check structure, scope, issued evidence and explicit factual claims.

        This does not prove every free-text sentence, economic correctness or
        investment performance. Live Agent evaluation remains a separate gate.
        """
        self._check_active()
        answer = AgentExplanation.model_validate(payload)
        if answer.symbol != self.symbol or self._stock_evidence_id is None:
            raise ValueError("AGENT_EVIDENCE_MISSING")
        cited = set(answer.evidence_ids)
        if self._stock_evidence_id not in cited or not cited.issubset(self._evidence):
            raise ValueError("AGENT_EVIDENCE_MISSING")
        if self._public_stock()["evidence_id"] != self._stock_evidence_id:
            raise ValueError("AGENT_EVIDENCE_CHANGED")
        health_ids = {eid for eid, item in self._evidence.items()
                      if item.get("tool") == "get_workflow_health"}
        if health_ids and health_ids != {self._public_health()["evidence_id"]}:
            raise ValueError("AGENT_EVIDENCE_CHANGED")
        stock = self._evidence[self._stock_evidence_id]
        missing = set(stock["blocking_reasons"])
        if not missing.issubset(answer.missing_conditions) or (
            missing and answer.status == "FACTS_ONLY"
        ):
            raise ValueError("AGENT_BLOCKING_CONDITIONS_OMITTED")
        for claim in answer.claims:
            if claim.evidence_id not in cited:
                raise ValueError("AGENT_CLAIM_NOT_CITED")
            value: Any = self._evidence[claim.evidence_id]
            for part in claim.field_path.split("."):
                if not isinstance(value, dict) or part not in value:
                    raise ValueError("AGENT_FIELD_NOT_FOUND")
                value = value[part]
            if canonical_bytes(value) != canonical_bytes(claim.value):
                raise ValueError("AGENT_VALUE_MISMATCH")
        self._check_active()
        return answer


def _evidence(payload: dict[str, Any], *, observed_at: str) -> dict[str, Any]:
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    return {**payload, "evidence_id": f"sha256:{digest}", "observed_at": observed_at}
