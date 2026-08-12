"""Human-controlled champion/challenger evolution with an audit trail."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone


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
    """In-memory control plane; production callers persist its audit records."""

    def __init__(self, *, champion_id: str | None = "champion-v1") -> None:
        self.champion_id = champion_id
        self._reports: dict[str, EvolutionReport] = {}
        self._tokens: dict[str, str] = {}
        self._audit: list[PromotionRecord] = []

    def register_report(self, report: EvolutionReport) -> None:
        self._reports[report.report_id] = report

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


__all__ = [
    "EvolutionEvaluator",
    "EvolutionMetrics",
    "EvolutionRegistry",
    "EvolutionReport",
    "PromotionRecord",
]
