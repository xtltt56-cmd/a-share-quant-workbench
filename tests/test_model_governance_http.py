from __future__ import annotations

import json
import threading
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from a_share_quant.research.evolution import EvolutionEvaluator, EvolutionMetrics, EvolutionRegistry
from a_share_quant.workbench.app import create_server


class FakeService:
    def health(self):
        return {"status": "OK", "paper_only": True, "live_trading_enabled": False}

    def snapshot(self):
        return {"intraday_monitor": [], "official_daily_candidates": []}


def _registry() -> tuple[EvolutionRegistry, str]:
    registry = EvolutionRegistry(champion_id="champion-v1")
    report = EvolutionEvaluator(registry).evaluate(
        EvolutionMetrics(
            candidate_id="challenger-v2",
            shadow_days=75,
            matured_samples=1400,
            oos_windows=4,
            improved_horizons=2,
            brier_not_worse=True,
            ece_not_worse=True,
            net_performance_better=True,
            drawdown_ok=True,
            capacity_ok=True,
            deflated_sharpe_pass=True,
            pbo_pass=True,
            regimes_pass=True,
            reproducible=True,
            leakage_detected=False,
        )
    )
    return registry, report.report_id


def _post(port: int, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Quant-Workbench-Request": "model-governance",
        },
        data=json.dumps(payload).encode("utf-8"),
    )
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_governance_page_is_chinese() -> None:
    registry, _ = _registry()
    server = create_server(service=FakeService(), governance=registry, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/advisory"
        with urlopen(url, timeout=3) as response:
            html = response.read().decode("utf-8")
        for text in ("模型治理", "当前冠军", "挑战者", "影子运行", "等待人工批准", "回滚"):
            assert text in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_changed_report_invalidates_approval_token() -> None:
    registry, report_id = _registry()
    server = create_server(service=FakeService(), governance=registry, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, preview = _post(port, "/api/models/promotion-preview", {"report_id": report_id})
        assert status == 200
        registry.register_report(replace(registry.report(report_id), candidate_id="changed-v3"))
        confirm_status, result = _post(
            port,
            "/api/models/promotion-confirm",
            {"report_id": report_id, "confirmation_token": preview["confirmation_token"]},
        )
        assert confirm_status == 409
        assert result["state"] == "REPORT_CHANGED"
        assert registry.champion_id == "champion-v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_approval_and_rollback_are_manual_and_audited() -> None:
    registry, report_id = _registry()
    server = create_server(service=FakeService(), governance=registry, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        _, preview = _post(port, "/api/models/promotion-preview", {"report_id": report_id})
        status, approval = _post(
            port,
            "/api/models/promotion-confirm",
            {"report_id": report_id, "confirmation_token": preview["confirmation_token"]},
        )
        assert status == 200
        assert approval["champion_id"] == "challenger-v2"
        _, rollback_preview = _post(
            port,
            "/api/models/rollback-preview",
            {"record_id": approval["record_id"]},
        )
        rollback_status, rollback = _post(
            port,
            "/api/models/rollback-confirm",
            {
                "record_id": approval["record_id"],
                "confirmation_token": rollback_preview["confirmation_token"],
            },
        )
        assert rollback_status == 200
        assert rollback["champion_id"] == "champion-v1"
        assert len(registry.audit_log()) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
