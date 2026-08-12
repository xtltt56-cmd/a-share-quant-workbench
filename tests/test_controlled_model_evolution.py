from __future__ import annotations

import pytest

from a_share_quant.research.evolution import (
    EvolutionEvaluator,
    EvolutionMetrics,
    EvolutionRegistry,
)


def passing_metrics(**overrides: object) -> EvolutionMetrics:
    values: dict[str, object] = {
        "candidate_id": "challenger-v2",
        "shadow_days": 75,
        "matured_samples": 1400,
        "oos_windows": 4,
        "improved_horizons": 2,
        "brier_not_worse": True,
        "ece_not_worse": True,
        "net_performance_better": True,
        "drawdown_ok": True,
        "capacity_ok": True,
        "deflated_sharpe_pass": True,
        "pbo_pass": True,
        "regimes_pass": True,
        "reproducible": True,
        "leakage_detected": False,
    }
    values.update(overrides)
    return EvolutionMetrics(**values)


def test_passing_challenger_waits_for_human() -> None:
    registry = EvolutionRegistry(champion_id="champion-v1")
    evaluator = EvolutionEvaluator(registry)

    report = evaluator.evaluate(passing_metrics())

    assert report.status == "AWAITING_MANUAL_APPROVAL"
    assert registry.champion_id == "champion-v1"


@pytest.mark.parametrize(
    "gate",
    [
        "shadow_days",
        "matured_samples",
        "oos_windows",
        "improved_horizons",
        "brier_not_worse",
        "ece_not_worse",
        "net_performance_better",
        "drawdown_ok",
        "capacity_ok",
        "deflated_sharpe_pass",
        "pbo_pass",
        "regimes_pass",
        "reproducible",
        "leakage_detected",
    ],
)
def test_each_failed_gate_blocks(gate: str) -> None:
    values: dict[str, object] = {}
    if gate in {"shadow_days", "matured_samples", "oos_windows", "improved_horizons"}:
        values[gate] = 0
    elif gate == "leakage_detected":
        values[gate] = True
    else:
        values[gate] = False

    report = EvolutionEvaluator(EvolutionRegistry()).evaluate(passing_metrics(**values))

    assert report.status == "BLOCKED"
    assert report.checks[gate] is False


def test_approval_is_audited_and_reversible() -> None:
    registry = EvolutionRegistry(champion_id="champion-v1")
    report = EvolutionEvaluator(registry).evaluate(passing_metrics())
    token = registry.issue_confirmation_token(report.report_id)

    record = registry.approve(report.report_id, confirmation_token=token)
    rollback_token = registry.issue_confirmation_token(record.record_id)
    rolled_back = registry.rollback(record.record_id, confirmation_token=rollback_token)

    assert record.champion_id == "challenger-v2"
    assert rolled_back.champion_id == "champion-v1"
    assert len(registry.audit_log()) == 2


def test_confirmation_token_is_one_time_and_auto_approval_is_unavailable() -> None:
    registry = EvolutionRegistry()
    report = EvolutionEvaluator(registry).evaluate(passing_metrics())
    token = registry.issue_confirmation_token(report.report_id)
    registry.approve(report.report_id, confirmation_token=token)

    with pytest.raises(ValueError, match="confirmation"):
        registry.approve(report.report_id, confirmation_token=token)
    with pytest.raises(TypeError):
        registry.approve(report.report_id, auto_approve=True)  # type: ignore[call-arg]


def test_blocked_report_cannot_issue_an_approval_token() -> None:
    registry = EvolutionRegistry()
    report = EvolutionEvaluator(registry).evaluate(
        passing_metrics(shadow_days=1)
    )
    with pytest.raises(ValueError, match="awaiting"):
        registry.issue_confirmation_token(report.report_id)
