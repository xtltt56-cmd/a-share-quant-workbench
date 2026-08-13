"""Human-controlled champion/challenger evolution with an audit trail."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvolutionMetrics:
    candidate_id: str
    shadow_days: int
    matured_samples: int
    oos_windows: int
    improved_horizons: int
    brier_not_worse: bool
    ece_not_worse: bool
    net_performance_better: bool
    drawdown_ok: bool
    capacity_ok: bool
    deflated_sharpe_pass: bool
    pbo_pass: bool
    regimes_pass: bool
    reproducible: bool
    leakage_detected: bool


@dataclass(frozen=True)
class EvolutionReport:
    report_id: str
    candidate_id: str
    status: str
    checks: dict[str, bool]
    created_at: datetime


@dataclass(frozen=True)
class PromotionRecord:
    record_id: str
    report_id: str
    candidate_id: str
    previous_champion_id: str | None
    champion_id: str | None
    action: str
    approved_at: datetime


class EvolutionEvaluator:
    """Evaluate evidence without mutating the champion alias."""

    def __init__(self, registry: EvolutionRegistry) -> None:
        self.registry = registry

    def evaluate(self, metrics: EvolutionMetrics) -> EvolutionReport:
        checks = {
            "shadow_days": metrics.shadow_days >= 60,
            "matured_samples": metrics.matured_samples >= 1000,
            "oos_windows": metrics.oos_windows >= 3,
            "improved_horizons": metrics.improved_horizons >= 2,
            "brier_not_worse": metrics.brier_not_worse,
            "ece_not_worse": metrics.ece_not_worse,
            "net_performance_better": metrics.net_performance_better,
            "drawdown_ok": metrics.drawdown_ok,
            "capacity_ok": metrics.capacity_ok,
            "deflated_sharpe_pass": metrics.deflated_sharpe_pass,
            "pbo_pass": metrics.pbo_pass,
            "regimes_pass": metrics.regimes_pass,
            "reproducible": metrics.reproducible,
            "leakage_detected": not metrics.leakage_detected,
        }
        report = EvolutionReport(
            report_id=f"evolution-{secrets.token_hex(12)}",
            candidate_id=str(metrics.candidate_id).strip(),
            status=("AWAITING_MANUAL_APPROVAL" if all(checks.values()) else "BLOCKED"),
            checks=checks,
            created_at=datetime.now(timezone.utc),
        )
        self.registry.register_report(report)
        return report


class EvolutionRegistry:
    """Human-controlled model registry with optional integrity-checked storage."""

    def __init__(
        self,
        *,
        champion_id: str | None = "champion-v1",
        state_path: str | Path | None = None,
    ) -> None:
        self.state_path = Path(state_path).resolve() if state_path is not None else None
        self.champion_id = champion_id
        self._reports: dict[str, EvolutionReport] = {}
        self._tokens: dict[str, str] = {}
        self._audit: list[PromotionRecord] = []
        if self.state_path is not None and self.state_path.exists():
            self._load()
        elif self.state_path is not None:
            self._persist()

    def register_report(self, report: EvolutionReport) -> None:
        self._reports[report.report_id] = report
        self._persist()

    def report(self, report_id: str) -> EvolutionReport:
        report = self._reports.get(report_id)
        if report is None:
            raise ValueError("unknown evolution report")
        return report

    def issue_confirmation_token(self, subject_id: str) -> str:
        report = self._reports.get(subject_id)
        if report is not None and report.status != "AWAITING_MANUAL_APPROVAL":
            raise ValueError("report is not awaiting manual approval")
        if report is None and not any(
            record.record_id == subject_id for record in self._audit
        ):
            raise ValueError("unknown approval subject")
        token = secrets.token_urlsafe(24)
        self._tokens[subject_id] = hashlib.sha256(token.encode()).hexdigest()
        return token

    def approve(self, report_id: str, *, confirmation_token: str) -> PromotionRecord:
        report = self._reports.get(report_id)
        if report is None or report.status != "AWAITING_MANUAL_APPROVAL":
            raise ValueError("report is not awaiting manual approval")
        self._consume_token(report_id, confirmation_token)
        previous = self.champion_id
        self.champion_id = report.candidate_id
        record = PromotionRecord(
            record_id=f"promotion-{secrets.token_hex(12)}",
            report_id=report_id,
            candidate_id=report.candidate_id,
            previous_champion_id=previous,
            champion_id=self.champion_id,
            action="APPROVE",
            approved_at=datetime.now(timezone.utc),
        )
        self._audit.append(record)
        self._persist()
        return record

    def rollback(self, record_id: str, *, confirmation_token: str) -> PromotionRecord:
        original = next((record for record in self._audit if record.record_id == record_id), None)
        if original is None:
            raise ValueError("unknown promotion record")
        self._consume_token(record_id, confirmation_token)
        previous = self.champion_id
        self.champion_id = original.previous_champion_id
        record = PromotionRecord(
            record_id=f"rollback-{secrets.token_hex(12)}",
            report_id=original.report_id,
            candidate_id=original.candidate_id,
            previous_champion_id=previous,
            champion_id=self.champion_id,
            action="ROLLBACK",
            approved_at=datetime.now(timezone.utc),
        )
        self._audit.append(record)
        self._persist()
        return record

    def audit_log(self) -> tuple[PromotionRecord, ...]:
        return tuple(self._audit)

    def _consume_token(self, subject_id: str, token: str) -> None:
        expected = self._tokens.pop(subject_id, None)
        if expected is None or not secrets.compare_digest(
            expected,
            hashlib.sha256(str(token).encode()).hexdigest(),
        ):
            raise ValueError("confirmation token is invalid or already used")

    def invalidate_confirmation_token(self, subject_id: str) -> None:
        self._tokens.pop(subject_id, None)

    def _persist(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        body: dict[str, Any] = {
            "format_version": 1,
            "champion_id": self.champion_id,
            "reports": [_json_record(item) for item in self._reports.values()],
            "audit": [_json_record(item) for item in self._audit],
        }
        encoded = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        payload = json.dumps(
            {**body, "sha256": hashlib.sha256(encoded).hexdigest()},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode()
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.state_path.name}.", dir=self.state_path.parent
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _load(self) -> None:
        assert self.state_path is not None
        try:
            raw = self.state_path.read_bytes()
            if not raw or len(raw) > 4_194_304:
                raise ValueError("evolution registry integrity check failed")
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("format_version") != 1:
                raise ValueError("evolution registry integrity check failed")
            digest = payload.pop("sha256", None)
            encoded = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            if not isinstance(digest, str) or not secrets.compare_digest(
                digest, hashlib.sha256(encoded).hexdigest()
            ):
                raise ValueError("evolution registry integrity check failed")
            reports = payload.get("reports")
            audit = payload.get("audit")
            if not isinstance(reports, list) or not isinstance(audit, list):
                raise ValueError("evolution registry integrity check failed")
            restored_reports = {
                item["report_id"]: EvolutionReport(
                    report_id=item["report_id"],
                    candidate_id=item["candidate_id"],
                    status=item["status"],
                    checks=dict(item["checks"]),
                    created_at=datetime.fromisoformat(item["created_at"]),
                )
                for item in reports
            }
            restored_audit = [
                PromotionRecord(
                    record_id=item["record_id"],
                    report_id=item["report_id"],
                    candidate_id=item["candidate_id"],
                    previous_champion_id=item.get("previous_champion_id"),
                    champion_id=item.get("champion_id"),
                    action=item["action"],
                    approved_at=datetime.fromisoformat(item["approved_at"]),
                )
                for item in audit
            ]
            self.champion_id = payload.get("champion_id")
            self._reports = restored_reports
            self._audit = restored_audit
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("evolution registry integrity check failed") from exc


def _json_record(value: EvolutionReport | PromotionRecord) -> dict[str, Any]:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if isinstance(item, datetime):
            payload[key] = item.isoformat()
    return payload


__all__ = [
    "EvolutionEvaluator",
    "EvolutionMetrics",
    "EvolutionRegistry",
    "EvolutionReport",
    "PromotionRecord",
]
