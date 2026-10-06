"""Read-only evidence tools shared by the UI and a future optional Agent.

This module performs no model calls, external searches, account writes or orders.
Deterministic explanations are labelled explicitly and never presented as AI.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from a_share_quant.data.normalization import normalize_symbol
from a_share_quant.market.trading_calendar import AShareTradingCalendar, CalendarUnavailableError
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.service import WorkbenchService


class WorkbenchContextTools:
    def __init__(
        self, service: WorkbenchService, advisory: AdvisoryWorkbenchService | None = None,
    ) -> None:
        self.service = service
        self.advisory = advisory

    def get_stock_context(self, symbol: str) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        snapshot = self.service.snapshot()
        now = self.service.clock()
        candidate = next((row for row in snapshot["official_daily_candidates"]
                          if row["symbol"] == normalized), None)
        monitor = next((row for row in snapshot["intraday_monitor"]
                        if row["symbol"] == normalized), None)
        row = monitor or candidate or {}
        quote = self.service.validated_quote(normalized, snapshot=snapshot)
        quote_valid = bool(
            quote is not None and self.service.allow_network
            and snapshot["data_quality"] == "GOOD"
        )
        quote_payload = {
            **quote, "quote_timestamp": quote["quote_timestamp"].isoformat(),
        } if quote_valid else None
        risk = row.get("event_risk") or self.service.public_risk_for_symbol(normalized)
        guidance = row.get("price_guidance") or {
            "state": "NO_RELIABLE_GUIDANCE", "reason_codes": ["PRICE_PLAN_MISSING"],
        }
        blocking = list(guidance.get("reason_codes") or ())
        # The intraday risk overlay must not hide the underlying missing plan.
        blocking.extend(((candidate or {}).get("price_guidance") or {}).get("reason_codes") or ())
        if not quote_valid:
            blocking.append("NO_VALIDATED_REALTIME_QUOTE")
        if candidate is None:
            blocking.append("NOT_IN_CURRENT_CANDIDATES")
        if not risk or risk["level"] != "CLEAR":
            blocking.extend((risk or {}).get("reason_codes") or ["PUBLIC_RISK_NOT_CHECKED"])
        expected = None
        try:
            expected = AShareTradingCalendar().latest_completed_session(now).isoformat()
        except CalendarUnavailableError:
            blocking.append("CALENDAR_UNAVAILABLE")
        cutoff = (candidate or {}).get("data_cutoff") or snapshot["daily_data_cutoff"]
        if candidate is not None and (not cutoff or expected and cutoff < expected):
            blocking.append("DAILY_INPUT_STALE")
        if candidate is not None and expected and cutoff and cutoff > expected:
            blocking.append("DAILY_INPUT_FUTURE")
        if snapshot["daily_data_status"] in {"UPDATE_FAILED", "STALE_DATA", "FAILED"}:
            blocking.append("DAILY_UPDATE_FAILED")
        valid_for = guidance.get("valid_for") or guidance.get("valid_for_date")
        if valid_for and valid_for != now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat():
            blocking.append("PRICE_PLAN_NOT_APPLICABLE")
        facts = list((candidate or {}).get("reasons") or ())
        if candidate is not None:
            facts.insert(0, "已进入当前策略的日线候选；候选分数仅用于排序，并非上涨概率。")
        result = {
            "schema_version": 1, "symbol": normalized,
            "name": row.get("name") or normalized,
            "analysis_mode": "DETERMINISTIC_FACTS", "agent_configured": False,
            "candidate": candidate, "quote": quote_payload, "event_risk": risk,
            "price_guidance": guidance, "probability": None,
            "supporting_facts": facts, "blocking_reasons": list(dict.fromkeys(blocking)),
            "input_cutoff": cutoff,
            "daily_status": snapshot["daily_data_status"],
            "daily_notice_zh": snapshot["daily_data_notice_zh"],
            "expected_session": expected,
            "source": (candidate or {}).get("source") or snapshot["active_source"],
            "summary_zh": (
                "当前摘要是后台可复核的规则事实，不是 AI 研究结论，不能据此保证股价表现。"
            ),
            "manual_execution_required": True,
        }
        return _with_evidence(result, observed_at=now.isoformat())

    def get_holding_context(self, symbol: str) -> dict[str, Any]:
        result = self.get_stock_context(symbol)
        result.pop("evidence_id")
        result.pop("observed_at")
        positions = []
        guidance = []
        if self.advisory is not None:
            account = self.advisory.holdings()
            for origin, rows in (
                ("LOCAL_LEDGER", account.get("positions", [])),
                ("BROKER_SNAPSHOT", (account.get("imported_account_snapshot") or {}).get(
                    "positions", [],
                )),
            ):
                for row in rows:
                    if row.get("code") == result["symbol"]:
                        positions.append({
                            "origin": origin,
                            **{key: row.get(key) for key in (
                                "name", "code", "total_quantity", "available_quantity",
                                "frozen_quantity", "average_cost",
                            )},
                        })
            guidance = [item for item in account.get("price_guidance", [])
                        if item.get("symbol") == result["symbol"]]
        result.update({
            "positions": positions, "holding_guidance": guidance,
            "cloud_transfer_authorized": False,
        })
        return _with_evidence(result, observed_at=self.service.clock().isoformat())


def _with_evidence(payload: dict[str, Any], *, observed_at: str) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return {**payload, "evidence_id": f"sha256:{digest}", "observed_at": observed_at}
