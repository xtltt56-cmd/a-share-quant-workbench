import pytest

from a_share_quant.research.production_gate import (
    ProductionResearchGate,
    ResearchEvidence,
)


def _evidence(**overrides) -> ResearchEvidence:
    values = {
        "candidate_id": "model-v2",
        "data_mode": "historical",
        "walk_forward_windows": 6,
        "oos_excess_returns": (0.02, 0.01, -0.01, 0.03, 0.01, 0.02),
        "rank_ic": (0.04, 0.02, 0.01, 0.03, 0.02, 0.01),
        "calibration_error": 0.06,
        "max_drawdown": -0.18,
        "turnover": 0.25,
        "pbo": 0.20,
        "deflated_sharpe": 0.80,
        "capacity_ok": True,
        "no_leakage": True,
        "reproducible": True,
        "risk_budget_ok": True,
    }
    values.update(overrides)
    windows = int(values["walk_forward_windows"])
    if "oos_excess_returns" not in overrides:
        values["oos_excess_returns"] = tuple(values["oos_excess_returns"][:windows])
    if "rank_ic" not in overrides:
        values["rank_ic"] = tuple(values["rank_ic"][:windows])
    return ResearchEvidence(**values)


def test_production_research_gate_accepts_only_robust_historical_evidence() -> None:
    result = ProductionResearchGate().evaluate(_evidence())

    assert result.passed is True
    assert all(result.checks.values())
    assert result.reasons == ()


def test_production_research_gate_fails_closed_for_fixture_and_weak_windows() -> None:
    result = ProductionResearchGate().evaluate(
        _evidence(data_mode="fixture", walk_forward_windows=1)
    )

    assert result.passed is False
    assert result.checks["historical_data"] is False
    assert result.checks["walk_forward"] is False
    assert result.reasons


def test_production_research_gate_rejects_missing_or_unstable_evidence() -> None:
    with pytest.raises(ValueError, match="windows"):
        _evidence(walk_forward_windows=5, oos_excess_returns=(0.01,))

    result = ProductionResearchGate().evaluate(
        _evidence(
            calibration_error=0.30,
            max_drawdown=-0.50,
            pbo=0.80,
            capacity_ok=False,
            no_leakage=False,
        )
    )
    assert result.passed is False
    assert result.checks["calibration"] is False
    assert result.checks["risk"] is False
    assert result.checks["research_integrity"] is False
