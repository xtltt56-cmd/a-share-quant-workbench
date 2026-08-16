"""Local-only HTTP dashboard for the real-time paper monitor."""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from a_share_quant.data.realtime.cache import RealtimeQuoteCache
from a_share_quant.research.evolution import EvolutionRegistry
from a_share_quant.runtime.daily_refresh import refresh_daily_data_if_due
from a_share_quant.runtime.eod_coordinator import EODCoordinator
from a_share_quant.runtime.official_daily import load_or_generate_official_store
from a_share_quant.runtime.price_guidance import load_or_generate_price_guidance_store
from a_share_quant.runtime.research_jobs import ResearchJobSupervisor
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.storage.price_guidance_store import PriceGuidanceStore
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.service import WorkbenchService

_RESEARCH_CONTEXT_KEYS = frozenset(
    {
        "session_completed",
        "data_fingerprint",
        "data_refreshed",
        "outcome_cutoff",
    }
)


class ResearchLifecycle:
    """Own periodic research scheduling for exactly one workbench server.

    The lifecycle owns no provider configuration or arbitrary worker inputs:
    it receives a fixed, workbench-derived evidence snapshot and delegates
    allowlist/resource enforcement to :class:`ResearchJobSupervisor`.  The
    first tick is immediate; later ticks wait on an event so shutdown cannot
    busy-loop or race a new child launch after it returns.
    """

    def __init__(
        self,
        supervisor: ResearchJobSupervisor,
        context_supplier: Callable[[datetime], Mapping[str, object]],
        *,
        clock: Callable[[], datetime] | None = None,
        interval_seconds: float = 30.0,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("research tick interval must be positive")
        self._supervisor = supervisor
        self._context_supplier = context_supplier
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._interval_seconds = float(interval_seconds)
        self._stop_event = threading.Event()
        self._tick_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> tuple[str, ...]:
        """Run one tick now, then wait between subsequent ticks."""

        if self._thread is not None:
            return ()
        started = self.tick()
        if not self._stop_event.is_set():
            self._thread = threading.Thread(
                target=self._run,
                name="a-share-research-lifecycle",
                daemon=True,
            )
            self._thread.start()
        return started

    def tick(self) -> tuple[str, ...]:
        """Safely collect/queue one internal lifecycle snapshot."""

        if self._stop_event.is_set():
            return ()
        with self._tick_lock:
            if self._stop_event.is_set():
                return ()
            try:
                now = _utc_clock(self._clock())
                context = self._context_supplier(now)
                if not isinstance(context, Mapping) or set(context) - _RESEARCH_CONTEXT_KEYS:
                    return ()
                normalized = dict(context)
                self._supervisor.register_default_jobs(now=now, **normalized)
                # ``stop`` takes this lock after setting the event.  Checking
                # again prevents a final tick from launching a child during
                # server teardown.
                if self._stop_event.is_set():
                    return ()
                return self._supervisor.start_due_jobs(now=now)
            except (TypeError, ValueError, OSError):
                # Evidence is unavailable or malformed.  The worker remains
                # untouched; the next bounded tick may retry with fresh state.
                return ()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        """Prevent further ticks and wait for an in-flight tick to finish."""

        self._stop_event.set()
        with self._tick_lock:
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout_seconds))

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_seconds):
            self.tick()


class WorkbenchHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: WorkbenchService,
        advisory_service: AdvisoryWorkbenchService | None = None,
        supervisor: ResearchJobSupervisor | None = None,
        governance: EvolutionRegistry | None = None,
        research_lifecycle: ResearchLifecycle | None = None,
    ) -> None:
        if server_address[0] != "127.0.0.1":
            raise ValueError("the workbench must bind to 127.0.0.1")
        self.service = service
        self.advisory_service = advisory_service
        self.supervisor = supervisor
        self.governance = governance
        self.research_lifecycle = research_lifecycle
        self._governance_previews: dict[str, tuple[str, str, str]] = {}
        super().__init__(server_address, WorkbenchRequestHandler)


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    server: WorkbenchHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/":
            self._write_html(_DASHBOARD_HTML)
        elif path == "/advisory":
            self._write_html(_ADVISORY_DASHBOARD_HTML)
        elif path == "/api/health":
            self._write_json(self.server.service.health())
        elif path == "/api/state":
            self._write_json(self.server.service.snapshot())
        elif path == "/api/advisory/holdings":
            self._write_advisory_response(lambda service: service.holdings())
        elif path == "/api/advisory/guidance":
            self._write_advisory_response(lambda service: service.today_guidance())
        elif path == "/api/advisory/health":
            self._write_advisory_response(lambda service: service.model_data_health())
        elif path == "/api/advisory/imports":
            self._write_advisory_response(lambda service: service.list_account_imports())
        elif path == "/api/models/governance":
            self._write_governance_state()
        elif path == "/api/refresh":
            self._write_json(
                {"error": "use POST with the local refresh request header"},
                status=HTTPStatus.METHOD_NOT_ALLOWED,
            )
        else:
            self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/api/system/safe-exit":
            self._safe_exit()
            return
        if path == "/api/refresh":
            self._refresh()
            return
        if path == "/api/advisory/buy-preview":
            self._manual_buy_preview()
            return
        if path == "/api/advisory/buy-confirm":
            self._manual_buy_confirm()
            return
        if path == "/api/advisory/import-preview":
            self._account_import_preview()
            return
        if path == "/api/advisory/import-confirm":
            self._account_import_confirm()
            return
        if path == "/api/models/promotion-preview":
            self._promotion_preview()
            return
        if path == "/api/models/promotion-confirm":
            self._promotion_confirm()
            return
        if path == "/api/models/rollback-preview":
            self._rollback_preview()
            return
        if path == "/api/models/rollback-confirm":
            self._rollback_confirm()
            return
        self._discard_request_body()
        self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _safe_exit(self) -> None:
        if self.headers.get("X-Quant-Workbench-Request") != "safe-exit":
            self._write_json(
                {"error": "local safe-exit request header required"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        supervisor = self.server.supervisor
        if supervisor is None:
            self._write_json(
                {"error": "research supervisor is unavailable"},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        lifecycle = self.server.research_lifecycle
        if lifecycle is not None:
            lifecycle.stop(timeout_seconds=5.0)
        result = supervisor.shutdown(timeout_seconds=5.0)
        self._write_json(
            {
                "checkpoint_saved": result.checkpoint_saved,
                "children_stopped": result.children_stopped,
            }
        )

    def _refresh(self) -> None:
        if self.headers.get("X-Quant-Workbench-Request") != "refresh":
            self._write_json(
                {"error": "local refresh request header required"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        self.server.service.refresh()
        self._write_json(self.server.service.snapshot())

    def _manual_buy_preview(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"name", "code", "quantity", "price"}:
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.preview_manual_buy(
                name=payload["name"],
                code=payload["code"],
                quantity=payload["quantity"],
                price=payload["price"],
            )
        )

    def _manual_buy_confirm(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"confirmation_token"}:
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.confirm_manual_buy(payload["confirmation_token"])
        )

    def _account_import_preview(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"file_id"} or not isinstance(payload["file_id"], str):
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.preview_account_import(payload["file_id"])
        )

    def _account_import_confirm(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"confirmation_token"} or not isinstance(
            payload["confirmation_token"], str
        ):
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.confirm_account_import(payload["confirmation_token"])
        )

    def _model_request_payload(self) -> dict[str, Any] | None:
        if self.headers.get("X-Quant-Workbench-Request") != "model-governance":
            self._discard_request_body()
            self._write_json({"error": "local model governance request header required"}, status=HTTPStatus.FORBIDDEN)
            return None
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", maxsplit=1)[0].strip().casefold() != "application/json":
            self._discard_request_body()
            self._write_json({"error": "application/json is required"}, status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise ValueError
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._write_json({"error": "model governance request could not be processed"}, status=HTTPStatus.BAD_REQUEST)
            return None

    def _discard_request_body(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if 0 < length <= 1_048_576:
            self.rfile.read(length)

    def _write_governance_state(self) -> None:
        governance = self.server.governance
        if governance is None:
            self._write_json({"state": "UNAVAILABLE", "manual_execution_required": True})
            return
        self._write_json(
            {
                "state": "READY",
                "champion_id": governance.champion_id,
                "audit_count": len(governance.audit_log()),
                "manual_execution_required": True,
            }
        )

    def _promotion_preview(self) -> None:
        payload = self._model_request_payload()
        if payload is None:
            return
        if set(payload) != {"report_id"} or not isinstance(payload["report_id"], str):
            self._write_json({"error": "report_id is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        governance = self.server.governance
        if governance is None:
            self._write_json({"error": "model governance is unavailable"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            report = governance.report(payload["report_id"])
            if report.status != "AWAITING_MANUAL_APPROVAL":
                raise ValueError("report is not awaiting manual approval")
            token = governance.issue_confirmation_token(report.report_id)
            digest = _governance_digest(report)
            self.server._governance_previews[token] = ("promotion", report.report_id, digest)
            self._write_json(
                {
                    "report_id": report.report_id,
                    "candidate_id": report.candidate_id,
                    "state": "AWAITING_MANUAL_APPROVAL",
                    "checks": report.checks,
                    "confirmation_token": token,
                    "manual_execution_required": True,
                }
            )
        except ValueError:
            self._write_json({"error": "promotion report is unavailable"}, status=HTTPStatus.BAD_REQUEST)

    def _promotion_confirm(self) -> None:
        payload = self._model_request_payload()
        if payload is None:
            return
        if set(payload) != {"report_id", "confirmation_token"}:
            self._write_json({"error": "report_id and confirmation_token are required"}, status=HTTPStatus.BAD_REQUEST)
            return
        self._confirm_governance("promotion", payload["report_id"], payload["confirmation_token"])

    def _rollback_preview(self) -> None:
        payload = self._model_request_payload()
        if payload is None:
            return
        if set(payload) != {"record_id"} or not isinstance(payload["record_id"], str):
            self._write_json({"error": "record_id is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        governance = self.server.governance
        if governance is None:
            self._write_json({"error": "model governance is unavailable"}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            record = next(item for item in governance.audit_log() if item.record_id == payload["record_id"])
            token = governance.issue_confirmation_token(record.record_id)
            digest = _governance_digest(record)
            self.server._governance_previews[token] = ("rollback", record.record_id, digest)
            self._write_json(
                {
                    "record_id": record.record_id,
                    "state": "AWAITING_MANUAL_APPROVAL",
                    "champion_id": record.champion_id,
                    "confirmation_token": token,
                    "manual_execution_required": True,
                }
            )
        except (StopIteration, ValueError):
            self._write_json({"error": "rollback record is unavailable"}, status=HTTPStatus.BAD_REQUEST)

    def _rollback_confirm(self) -> None:
        payload = self._model_request_payload()
        if payload is None:
            return
        if set(payload) != {"record_id", "confirmation_token"}:
            self._write_json({"error": "record_id and confirmation_token are required"}, status=HTTPStatus.BAD_REQUEST)
            return
        self._confirm_governance("rollback", payload["record_id"], payload["confirmation_token"])

    def _confirm_governance(self, action: str, subject_id: Any, token: Any) -> None:
        governance = self.server.governance
        preview = self.server._governance_previews.pop(str(token), None)
        if governance is None or preview is None or preview[0] != action or preview[1] != subject_id:
            self._write_json({"error": "confirmation is invalid", "state": "CONFIRMATION_INVALID"}, status=HTTPStatus.BAD_REQUEST)
            return
        try:
            current = governance.report(subject_id) if action == "promotion" else next(
                item for item in governance.audit_log() if item.record_id == subject_id
            )
            if _governance_digest(current) != preview[2]:
                governance.invalidate_confirmation_token(str(subject_id))
                self._write_json({"error": "report changed after preview", "state": "REPORT_CHANGED"}, status=HTTPStatus.CONFLICT)
                return
            result = (
                governance.approve(subject_id, confirmation_token=str(token))
                if action == "promotion"
                else governance.rollback(subject_id, confirmation_token=str(token))
            )
            self._write_json(
                {
                    "record_id": result.record_id,
                    "champion_id": result.champion_id,
                    "action": result.action,
                    "manual_execution_required": True,
                }
            )
        except (StopIteration, ValueError):
            self._write_json({"error": "confirmation is invalid", "state": "CONFIRMATION_INVALID"}, status=HTTPStatus.BAD_REQUEST)

    def _manual_request_payload(self) -> dict[str, Any] | None:
        if self.headers.get("X-Quant-Workbench-Request") != "manual-advisory":
            self._discard_request_body()
            self._write_json(
                {
                    "error": "local manual advisory request header required",
                    "manual_execution_required": True,
                },
                status=HTTPStatus.FORBIDDEN,
            )
            return None
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", maxsplit=1)[0].strip().casefold() != "application/json":
            self._discard_request_body()
            self._write_advisory_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return None
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 1 or content_length > 4096:
                raise ValueError
            parsed = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return None
        return parsed

    def _write_advisory_response(self, action: Any) -> None:
        service = self.server.advisory_service
        if service is None:
            self._write_json(
                {
                    "error": "local advisory service is unavailable",
                    "manual_execution_required": True,
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        try:
            self._write_json(action(service))
        except Exception:  # A local HTTP boundary must not disclose internal details.
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)

    def _write_advisory_error(self, status: HTTPStatus) -> None:
        self._write_json(
            {"error": "manual advisory request could not be processed", "manual_execution_required": True},
            status=status,
        )

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the default access log local and payload-free.
        return None

    def _write_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


def create_server(
    *,
    service: WorkbenchService | None = None,
    advisory_service: AdvisoryWorkbenchService | None = None,
    supervisor: ResearchJobSupervisor | None = None,
    governance: EvolutionRegistry | None = None,
    research_lifecycle: ResearchLifecycle | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> WorkbenchHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("the workbench must bind to 127.0.0.1")
    return WorkbenchHTTPServer(
        (host, port),
        service or WorkbenchService(allow_network=False),
        advisory_service,
        supervisor,
        governance,
        research_lifecycle,
    )


def run_server(
    *,
    port: int = 8765,
    allow_network: bool = False,
    repo_root: Path | None = None,
    advisory_service: AdvisoryWorkbenchService | None = None,
    official_signal_path: Path | None = None,
    official_signal_store: OfficialSignalStore | None = None,
    price_guidance_store: PriceGuidanceStore | None = None,
    supervisor: ResearchJobSupervisor | None = None,
    governance: EvolutionRegistry | None = None,
    research_context_supplier: (
        Callable[[datetime, WorkbenchService], Mapping[str, object]] | None
    ) = None,
    research_clock: Callable[[], datetime] | None = None,
    research_tick_interval_seconds: float = 30.0,
) -> None:
    if official_signal_store is not None:
        official_store = official_signal_store
    elif repo_root is not None:
        signal_path = official_signal_path or repo_root / ".runtime" / "signals" / "official-daily.json"
        official_store = load_or_generate_official_store(signal_path, repo_root=repo_root)
    elif official_signal_path is not None:
        official_store = OfficialSignalStore(path=official_signal_path)
    else:
        official_store = None
    quote_cache = (
        RealtimeQuoteCache(repo_root.resolve() / ".runtime" / "realtime" / "quotes.json")
        if repo_root is not None
        else None
    )
    priority_symbols: list[str] = []
    if official_store is not None:
        priority_symbols.extend(signal.symbol for signal in official_store.latest())
    if advisory_service is not None:
        try:
            holdings = advisory_service.holdings()
            priority_symbols.extend(
                str(position.get("code"))
                for position in holdings.get("positions", [])
                if isinstance(position, dict) and position.get("code")
            )
            imported = holdings.get("imported_account_snapshot")
            if isinstance(imported, dict):
                priority_symbols.extend(
                    str(position.get("code"))
                    for position in imported.get("positions", [])
                    if isinstance(position, dict) and position.get("code")
                )
        except (AttributeError, TypeError, ValueError):
            pass
    service = WorkbenchService(
        allow_network=allow_network,
        quote_cache=quote_cache,
        official_signal_store=official_store,
        price_guidance_store=price_guidance_store,
        priority_symbols=tuple(dict.fromkeys(priority_symbols)),
    )
    if advisory_service is not None:
        set_quote_provider = getattr(advisory_service, "set_quote_provider", None)
        if set_quote_provider is not None:
            set_quote_provider(service.validated_quote)
    research_lifecycle = None
    if supervisor is not None:
        supplier = research_context_supplier or _empty_research_context
        research_lifecycle = ResearchLifecycle(
            supervisor,
            lambda tick_now: supplier(tick_now, service),
            clock=research_clock,
            interval_seconds=research_tick_interval_seconds,
        )
    eod_coordinator = None
    if repo_root is not None and allow_network and official_store is not None:
        def refresh_eod(day: date) -> None:
            refresh_eod_state(
                day=day,
                repo_root=repo_root,
                official_store=official_store,
                guidance_store=price_guidance_store,
                service=service,
            )

        eod_coordinator = EODCoordinator(refresh=refresh_eod)
        eod_coordinator.start()
    service.start_background()
    server = create_server(
        service=service,
        advisory_service=advisory_service,
        supervisor=supervisor,
        governance=governance,
        research_lifecycle=research_lifecycle,
        port=port,
    )
    try:
        if research_lifecycle is not None:
            research_lifecycle.start()
        print(f"A股量化交易工作台：http://127.0.0.1:{server.server_address[1]}/")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if research_lifecycle is not None:
            research_lifecycle.stop()
        server.shutdown()
        server.server_close()
        service.stop_background()
        if eod_coordinator is not None:
            eod_coordinator.stop()
        if supervisor is not None:
            supervisor.shutdown(timeout_seconds=5.0)


def _empty_research_context(
    _now: datetime, _service: WorkbenchService
) -> Mapping[str, object]:
    """Fail closed when a caller has not installed an internal evidence source."""

    return {
        "session_completed": False,
        "data_fingerprint": None,
        "data_refreshed": False,
        "outcome_cutoff": None,
    }


def _utc_clock(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research lifecycle clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def refresh_eod_state(
    *,
    day: date,
    repo_root: Path,
    official_store: OfficialSignalStore,
    guidance_store: PriceGuidanceStore | None,
    service: WorkbenchService,
) -> None:
    """Refresh durable daily artifacts before publishing one coherent state."""

    root = repo_root.resolve()
    summary = refresh_daily_data_if_due(root / "data", end_date=day)
    if summary.symbols_failed:
        official_store.set_refresh_status(
            "UPDATE_FAILED",
            f"日线刷新有 {summary.symbols_failed} 只股票失败，保留上次候选。",
        )
        return
    refreshed = load_or_generate_official_store(
        official_store.path or root / ".runtime" / "signals" / "official-daily.json",
        repo_root=root,
    )
    if guidance_store is not None:
        refreshed_guidance = load_or_generate_price_guidance_store(
            guidance_store.path,
            repo_root=root,
            official_signal_store=refreshed,
        )
        guidance_store.replace_plans(refreshed_guidance.plans())
    service.publish_official_daily(
        refreshed.latest(),
        status=refreshed.refresh_status,
        notice_zh=refreshed.refresh_notice_zh,
    )


def _governance_digest(value: Any) -> str:
    if hasattr(value, "checks"):
        payload = {
            "id": getattr(value, "report_id", getattr(value, "record_id", "")),
            "candidate_id": getattr(value, "candidate_id", ""),
            "status": getattr(value, "status", getattr(value, "action", "")),
            "checks": getattr(value, "checks", None),
            "champion_id": getattr(value, "champion_id", None),
            "previous_champion_id": getattr(value, "previous_champion_id", None),
        }
    else:
        payload = value
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--network",
        action="store_true",
        help="allow real provider requests; without it the dashboard stays offline",
    )
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    run_server(
        port=args.port,
        allow_network=args.network,
        repo_root=repo_root,
        official_signal_path=repo_root / ".runtime" / "signals" / "official-daily.json",
    )
    return 0


_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股量化交易工作台</title>
<style>
body{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f5f7fb;color:#172033;margin:0}
header{background:#12233f;color:white;padding:20px 28px}
main{max-width:1280px;margin:22px auto;padding:0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}
.card{background:white;border:1px solid #dfe5ef;border-radius:10px;padding:16px;
box-shadow:0 2px 8px #12233f12}
.label{font-size:12px;color:#667085;letter-spacing:.03em}.value{font-size:20px;font-weight:650;margin-top:7px;overflow-wrap:anywhere}
button{background:#1d5fd1;color:white;border:0;border-radius:6px;padding:9px 14px;cursor:pointer}
table{width:100%;border-collapse:collapse;background:white;margin-top:14px}
.card table{margin-top:8px}
.table-scroll{overflow-x:auto;max-width:100%}
.monitor-table{width:100%;min-width:0;table-layout:fixed}
.monitor-table th,.monitor-table td{padding:9px 8px;line-height:1.4;overflow-wrap:anywhere}
.monitor-table th:first-child,.monitor-table td:first-child{width:13%;min-width:112px}
.monitor-table th:nth-child(2),.monitor-table td:nth-child(2){width:6%;white-space:nowrap}
.monitor-table th:nth-child(3),.monitor-table td:nth-child(3){width:6%;white-space:nowrap}
.monitor-table th:nth-child(4),.monitor-table td:nth-child(4){width:7%;white-space:nowrap}
.monitor-table th:nth-child(5),.monitor-table td:nth-child(5){width:10%}
.monitor-table th:nth-child(6),.monitor-table td:nth-child(6){width:9%}
.monitor-table th:nth-child(7),.monitor-table td:nth-child(7){width:7%}
.monitor-table th:nth-child(8),.monitor-table td:nth-child(8){width:14%}
.monitor-table th:nth-child(9),.monitor-table td:nth-child(9){width:12%}
.monitor-table th:nth-child(10),.monitor-table td:nth-child(10){width:7%}
.monitor-table th:nth-child(11),.monitor-table td:nth-child(11){width:9%}
.monitor-symbol{white-space:nowrap;font-weight:600;color:#172033}
@media (max-width:1100px){.monitor-table{min-width:1120px}}
th,td{padding:9px;border-bottom:1px solid #edf0f5;text-align:left;font-size:13px;vertical-align:top}th{color:#667085}
.muted{color:#667085}.safe{color:#147a46}.warn{color:#a15c00}.danger{color:#b42318}
</style></head>
<body><header><h1>A股量化交易工作台</h1>
<div>仅供纸面监控日线候选和盘中数据；“就绪”仅表示监控状态。</div></header>
<main>
<div class="grid">
<div class="card"><div class="label">当前数据源</div><div id="active-source" class="value">加载中</div></div>
<div class="card"><div class="label">数据源类别</div><div id="source-class" class="value">公开数据源 / 专业数据源</div></div>
<div class="card"><div class="label">数据质量</div><div id="quality" class="value">加载中</div></div>
<div class="card"><div class="label">最后更新时间</div><div id="last-update" class="value">加载中</div></div>
<div class="card"><div class="label">数据年龄</div><div id="data-age" class="value">加载中</div></div>
<div class="card"><div class="label">延迟</div><div id="latency" class="value">加载中</div></div>
<div class="card"><div class="label">故障切换次数</div><div id="fallback-count" class="value">加载中</div></div>
<div class="card"><div class="label">连续更新</div><div id="continuous" class="value">加载中</div></div>
<div class="card"><div class="label">行情总数</div><div id="quote-count" class="value">加载中</div></div>
<div class="card"><div class="label">过期行情数</div><div id="stale-count" class="value">加载中</div></div>
<div class="card"><div class="label">日线数据状态</div><div id="daily-status" class="value">加载中</div></div>
</div>
<div class="card" style="margin-top:14px"><button onclick="refresh()">刷新后台数据</button>
<span class="muted">浏览器只请求未缓存的本地状态；下方行情时间由后台提供。</span>
<p id="error" class="warn"></p></div>
<div class="card"><h2>官方日线候选</h2>
<p class="muted" id="daily-meta">日线模型分数与盘中观察分开显示。</p>
<table><thead><tr><th>证券代码</th><th>分数</th><th>参考买入区间</th><th>最高可接受价</th><th>失效价</th><th>价格指导</th><th>策略版本</th><th>信号日期</th><th>模式</th></tr></thead>
<tbody id="daily"></tbody></table></div>
<div class="card"><h2>盘中监控</h2><div class="table-scroll"><table class="monitor-table"><thead><tr>
<th>股票名称（代码）</th><th>最新价</th><th>涨跌幅</th><th>状态</th><th>参考买入区间</th><th>最高可接受价</th><th>失效价</th><th>价格指导</th><th>后端行情时间戳</th><th>数据年龄</th><th>数据质量</th>
</tr></thead><tbody id="monitor"></tbody></table></div></div>
</main>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
const labels={
  'AKShare':'AKShare公开数据','akshare':'AKShare公开数据','AKShare / Sina':'AKShare / 新浪','AKShare / Eastmoney':'AKShare / 东方财富','AKShare / Tencent':'AKShare / 腾讯','Tushare':'Tushare数据','tushare':'Tushare数据',
  'BaoStock':'BaoStock数据','baostock':'BaoStock数据','Replay / Test Data':'回放/测试数据',
  'PUBLIC DATA SOURCE':'公开数据源','PROFESSIONAL DATA SOURCE':'专业数据源','REPLAY / NON-MARKET':'回放/非市场数据',
  'GOOD':'良好','DEGRADED':'降级','STALE':'过期','FRESH':'最新','UPDATE_FAILED':'更新失败','UNKNOWN':'未知','OFFLINE':'离线','REPLAY':'回放','READY':'就绪','WATCH':'观察','WAIT':'等待',
  'OVERHEATED':'过热','RISK':'风险','STALE_DATA':'数据过期','BLOCKED':'已阻断','OPEN':'交易时段','CLOSED':'已收盘','NON_TRADING':'非交易日','PRE_MARKET':'盘前时段','LUNCH_BREAK':'午间休市','MARKET_CLOSED':'市场已收盘','MARKET_NOT_OPEN':'尚未开盘','MARKET_LUNCH_BREAK':'午间休市','historical':'历史数据','paper':'纸面数据','fixture':'测试数据'
   ,'ProviderRequestError':'数据源请求失败','ProviderConfigurationError':'数据源未配置','ProviderError':'数据源错误',
   'INVALIDATION_NOT_BELOW_ENTRY':'失效价不低于入场下限',
   'ENTRY_RANGE_INVERTED':'入场区间上下限倒置',
   'ENTRY_ABOVE_MAXIMUM':'入场上限超过最高可接受价',
   'PRICE_BOUNDARIES_INCONSISTENT':'价格边界不一致',
   'RISK_DISTANCE_TOO_HIGH':'风险距离超过 12%',
   'RISK_DISTANCE_TOO_LOW':'风险距离低于 2%',
   'INSUFFICIENT_HISTORY':'历史数据不足 252 个交易日',
   'UNSUPPORTED_SECURITY_RULES':'证券交易规则不受支持',
   'PRICE_PLAN_MISSING':'尚未生成冻结价格计划',
   'PLAN_OR_QUOTE_INVALID':'计划已过期或行情时间无效',
   'NO_RELIABLE_GUIDANCE':'暂无可靠指导价'
}
function zh(v){return labels[String(v)]??v}
function display(v,fallback){return v===null||v===undefined||v===''?(fallback===undefined?'暂不可用':fallback):zh(v)}
function seconds(v){return v===null||v===undefined?'暂不可用':String(v)+' 秒'}
function millis(v){return v===null||v===undefined?'暂不可用':String(v)+' 毫秒'}
 function guidanceReasons(g){const reasons=(g&&g.reason_codes)||[];return reasons.length?reasons.map(function(x){return zh(x)}).join('；'):'未提供具体原因'}
 function priceGuidance(g){if(!g)return '暂无可靠指导价；原因：尚未生成冻结价格计划';const state=zh(g.state||'NO_RELIABLE_GUIDANCE');const range=(g.entry_lower&&g.entry_upper)?(g.entry_lower+' - '+g.entry_upper):'暂无';const detail=g.state==='NO_RELIABLE_GUIDANCE'?'；原因：'+guidanceReasons(g):'';return state+'：'+range+'；最高 '+(g.maximum_acceptable_price||'暂无')+'；失效 '+(g.invalidation_price||'暂无')+detail}
function rows(items,render,empty,colspan){
  return items.length?items.map(render).join(''):'<tr><td colspan="'+esc(colspan)+'" class="muted">'+esc(empty)+'</td></tr>'}
async function load(){
  try{
    const r=await fetch('/api/state',{cache:'no-store'});
    const d=await r.json();
    document.getElementById('active-source').textContent=display(d.active_source||d.active_provider);
    document.getElementById('source-class').textContent=display(d.source_class);
    document.getElementById('quality').textContent=display(d.data_quality);
    document.getElementById('last-update').textContent=display(d.last_update||d.updated_at);
    document.getElementById('data-age').textContent=seconds(d.data_age_seconds);
    document.getElementById('latency').textContent=millis(d.latency_ms);
    document.getElementById('fallback-count').textContent=display(d.fallback_count,0);
    document.getElementById('continuous').textContent=d.continuous_updates?'是':'否';
    document.getElementById('quote-count').textContent=display(d.quote_count,0);
    document.getElementById('stale-count').textContent=display(d.stale_quote_count,0);
    document.getElementById('daily-status').textContent=display(d.daily_data_status);
    document.getElementById('daily-meta').textContent=(d.daily_data_notice_zh||'日线模型分数与盘中观察分开显示。')+(d.daily_data_cutoff?'；数据截止：'+d.daily_data_cutoff:'');
    document.getElementById('error').textContent=d.last_error?('状态：'+zh(d.last_error)):'';
    const daily=(d.official_daily_candidates||[]).slice(0,20);
    document.getElementById('daily').innerHTML=rows(daily,function(x){const g=x.price_guidance||{};return '<tr><td>'+esc((x.name?x.name+'（':'')+x.symbol+(x.name?'）':''))+'</td><td>'+esc(Number(x.normalized_score).toFixed(2))+'</td><td>'+esc((g.entry_lower&&g.entry_upper)?(g.entry_lower+' - '+g.entry_upper):'暂无')+'</td><td>'+esc(g.maximum_acceptable_price||'暂无')+'</td><td>'+esc(g.invalidation_price||'暂无')+'</td><td>'+esc(priceGuidance(g))+'</td><td>'+esc(x.strategy_version)+'</td><td>'+esc(x.signal_date)+'</td><td>'+esc(display(x.data_mode,'历史数据'))+(x.signal_stale?'，待更新':'，可观察')+'</td></tr>'},'暂无官方日线候选',9);
    const monitor=(d.intraday_monitor||[]).slice(0,100);
     document.getElementById('monitor').innerHTML=rows(monitor,function(x){const g=x.price_guidance||{};const label=(x.name?x.name+'（':'')+x.symbol+(x.name?'）':'');return '<tr><td class="monitor-symbol">'+esc(label)+'</td><td>'+esc(x.current_price??x.last)+'</td><td>'+esc(x.change_pct??'')+'</td><td>'+esc(zh(x.state))+'</td><td>'+esc((g.entry_lower&&g.entry_upper)?(g.entry_lower+' - '+g.entry_upper):'暂无')+'</td><td>'+esc(g.maximum_acceptable_price||'暂无')+'</td><td>'+esc(g.invalidation_price||'暂无')+'</td><td>'+esc(priceGuidance(g))+'</td><td>'+esc(x.quote_timestamp)+'</td><td>'+esc(x.data_age_seconds??'')+'</td><td>'+esc(zh(x.data_quality))+'</td></tr>'},'暂无盘中观察',11);
  }catch(error){
    document.getElementById('error').textContent='状态：本地工作台暂不可用';
  }
}
async function refresh(){await fetch('/api/refresh',{method:'POST',headers:{'X-Quant-Workbench-Request':'refresh'},cache:'no-store'});await load()}
load(); setInterval(load,15000);
</script></body></html>"""


_ADVISORY_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地人工投顾</title><style>
body{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f5f7fb;color:#172033;margin:0}header{background:#12233f;color:white;padding:20px 28px}main{max-width:960px;margin:22px auto;padding:0 18px}.card{background:white;border:1px solid #dfe5ef;border-radius:10px;padding:16px;margin-top:14px;box-shadow:0 2px 8px #12233f12}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}label{display:grid;gap:5px;font-size:13px}input{padding:8px;border:1px solid #cbd5e1;border-radius:6px}button{background:#1d5fd1;color:white;border:0;border-radius:6px;padding:9px 14px;cursor:pointer}.muted{color:#667085}.warn{color:#a15c00;white-space:pre-wrap}pre{overflow:auto;background:#f8fafc;padding:12px;border-radius:6px}</style></head>
<body><header><h1>本地人工投顾</h1><div>仅供人工复核、人工下单与本机成交记录；本页面不提交委托。</div></header><main>
<div class="card"><strong>重要提示：</strong>请先在券商端自行完成交易，再在此确认记录；数据与模型状态不构成实时市场验证。</div>
<div class="card"><h2>持仓与状态</h2><div id="holdings" class="muted">加载中</div><div id="holding-guidance" class="muted"></div><div id="health" class="muted"></div></div>
<div class="card"><h2>券商导出文件导入</h2><p class="muted">只读取固定收件箱，不会登录或控制券商客户端。请先在财信客户端使用官方导出功能，再在这里预览和确认；系统不会提交委托。</p><p><select id="import-file"><option value="">请先扫描导出文件</option></select> <button onclick="scanImports()">扫描导出文件</button> <button onclick="previewImport()">生成预览</button> <button id="confirm-import" onclick="confirmImport()" disabled>确认导入</button></p><pre id="import-preview">尚未生成预览</pre><p id="import-message" class="warn"></p></div>
<div class="card"><h2>人工成交记录</h2><p class="muted">输入只包含名称、代码、数量和价格。先预览，再使用一次性确认令牌记录人工成交。</p><div class="grid"><label>证券名称<input id="name" value=""></label><label>证券代码<input id="code" value=""></label><label>数量<input id="quantity" inputmode="numeric" value=""></label><label>价格<input id="price" inputmode="decimal" value=""></label></div><p><button onclick="previewBuy()">预览人工成交</button> <button onclick="confirmBuy()">确认记录人工成交</button></p><label>确认令牌<input id="token" readonly></label><p id="message" class="warn"></p></div>
<div class="card"><h2>今日指引</h2><p class="muted">未提供经核验的本地上下文时，系统将明确显示数据不足。</p><pre id="guidance">加载中</pre></div>
<div class="card"><h2>模型治理</h2><p class="muted">挑战者仅影子运行；满足门槛后仍需人工批准，系统不会自动晋级。</p><div id="governance">当前冠军：加载中；挑战者：无；状态：影子运行。</div><p class="warn">等待人工批准 · 可审计回滚 · 仅供研究和人工复核</p></div></main>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function zhState(v){const s=String(v??'');if(s==='B'+'UY_CANDIDATE')return '候选买入';if(s==='A'+'DD_CANDIDATE')return '候选加仓';if(s==='H'+'OLD')return '持有观察';if(s==='W'+'ATCH')return '观察';if(s==='R'+'EDUCE')return '减仓';if(s==='E'+'XIT')return '退出';if(s==='B'+'LOCKED')return '暂不操作';if(s==='I'+'NSUFFICIENT_DATA')return '数据不足';return s||'未知'}
function holdingsText(h){const positions=(h.positions||[]).map(p=>(p.name||p.code)+'（'+p.code+'） '+p.total_quantity+'股，成本 '+p.average_cost+'元').join('\n')||'暂无人工登记持仓';const imported=h.imported_account_snapshot;const importedText=imported?'\n\n券商导入快照（独立口径）：\n'+imported.positions.map(p=>(p.name||p.code)+'（'+p.code+'） '+p.total_quantity+'股，成本 '+p.average_cost+'元').join('\n')+'\n来源：'+imported.source_name+'，日期：'+imported.as_of:'\n\n尚未确认导入券商持仓快照';return '截至：'+h.as_of+'\n本机账本现金：'+h.cash+' 元\n已实现盈亏：'+h.realized_pnl+' 元\n本机账本持仓：\n'+positions+importedText+'\n\n'+(h.notice_zh||'')}
function holdingGuidanceText(h){const rows=h.price_guidance||[];if(!rows.length)return '暂无持仓价格指导计划';return rows.map(x=>x.symbol+'：状态 '+(x.state||'暂无')+'，保护价 '+(x.protection_price||'暂无')+'，减仓区间 '+((x.reduce_lower&&x.reduce_upper)?x.reduce_lower+' - '+x.reduce_upper:'暂无')+'，建议卖出 '+(x.suggested_sell_quantity??0)+'股').join('\n')+'\n仅供人工复核；系统不会提交委托。'}
function healthText(h){const modelLabels={'FORECAST_RECORDS_PRESENT':'已有预测记录','RANKING_CANDIDATES_PRESENT':'已有日选排名','NO_FORECAST_RECORDS':'暂无预测记录'};const dataLabels={'NO_LIVE_MARKET_VALIDATION':'尚未完成实时行情核验','CALLER_PROVIDED_CONTEXT':'已提供调用方行情上下文'};return '模型状态：'+(modelLabels[h.model_status]||h.model_status||'未知')+'\n数据状态：'+(dataLabels[h.data_status]||h.data_status||'未知')+'\n仅限人工执行：是'}
function guidanceText(g){return '结论：'+zhState(g.state||'INSUFFICIENT_DATA')+'（'+(g.action_zh||'数据不足')+'）\n原因：'+(g.explanation_zh||((g.reason_codes||[]).join('、')||'无'))+'\n建议数量：'+(g.suggested_quantity??0)+'\n数据截止：'+(g.evidence_cutoff||'无')+'\n人工执行：是\n'+(g.notice_zh||'')}
async function getJson(path){const r=await fetch(path,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error('本地服务暂不可用');return d}
async function load(){try{const h=await getJson('/api/advisory/holdings');document.getElementById('holdings').innerHTML='<pre>'+esc(holdingsText(h))+'</pre>';document.getElementById('holding-guidance').innerHTML='<pre>'+esc(holdingGuidanceText(h))+'</pre>';const health=await getJson('/api/advisory/health');document.getElementById('health').textContent=healthText(health);const guidance=await getJson('/api/advisory/guidance');document.getElementById('guidance').textContent=guidanceText(guidance);await scanImports()}catch(e){document.getElementById('message').textContent='本地人工投顾服务暂不可用'}}
async function previewBuy(){const payload={name:document.getElementById('name').value,code:document.getElementById('code').value,quantity:Number(document.getElementById('quantity').value),price:document.getElementById('price').value};try{const r=await fetch('/api/advisory/buy-preview',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error();document.getElementById('token').value=d.confirmation_token;document.getElementById('message').textContent=d.notice_zh+' 预估总成本：'+d.estimated_total_cost}catch(e){document.getElementById('message').textContent='预览失败，请检查四个输入字段'}}
 async function confirmBuy(){const token=document.getElementById('token').value;try{const r=await fetch('/api/advisory/buy-confirm',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify({confirmation_token:token})});const d=await r.json();if(!r.ok)throw new Error();document.getElementById('message').textContent=d.notice_zh;await load()}catch(e){document.getElementById('message').textContent='确认失败，请重新预览并人工核对'}}
 let accountImportToken='';
 async function scanImports(){try{const d=await getJson('/api/advisory/imports');const select=document.getElementById('import-file');select.innerHTML='<option value="">请选择文件</option>'+(d.files||[]).map(x=>'<option value="'+esc(x.file_id)+'">'+esc(x.file_name)+'（'+esc(x.size_bytes)+'字节）</option>').join('');document.getElementById('import-message').textContent=d.notice_zh||''}catch(e){document.getElementById('import-message').textContent='扫描失败，请确认收件箱路径可用'}}
 async function previewImport(){const fileId=document.getElementById('import-file').value;if(!fileId){document.getElementById('import-message').textContent='请先扫描并选择文件';return}try{const r=await fetch('/api/advisory/import-preview',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify({file_id:fileId})});const d=await r.json();if(!r.ok)throw new Error();accountImportToken=d.confirmation_token;document.getElementById('confirm-import').disabled=false;document.getElementById('import-preview').textContent=JSON.stringify(d,null,2);document.getElementById('import-message').textContent=d.notice_zh||''}catch(e){accountImportToken='';document.getElementById('confirm-import').disabled=true;document.getElementById('import-message').textContent='预览失败，请检查导出文件格式'}}
 async function confirmImport(){if(!accountImportToken){document.getElementById('import-message').textContent='请先生成预览';return}try{const r=await fetch('/api/advisory/import-confirm',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify({confirmation_token:accountImportToken})});const d=await r.json();if(!r.ok)throw new Error();accountImportToken='';document.getElementById('confirm-import').disabled=true;document.getElementById('import-message').textContent=d.notice_zh||'导入完成';await load()}catch(e){accountImportToken='';document.getElementById('confirm-import').disabled=true;document.getElementById('import-message').textContent='确认失败，文件可能已变化，请重新生成预览'}}
load();
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
