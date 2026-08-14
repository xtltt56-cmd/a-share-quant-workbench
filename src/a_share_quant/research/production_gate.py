"""Pre-registered statistical and integrity gates for production research."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from a_share_quant.contracts.modes import validate_data_mode


@dataclass(frozen=True)
class ResearchEvidence:
    candidate_id: str
    data_mode: str
    walk_forward_windows: int
    oos_excess_returns: tuple[float, ...]
    rank_ic: tuple[float, ...]
    calibration_error: float
    max_drawdown: float
    turnover: float
    pbo: float
    deflated_sharpe: float
    capacity_ok: bool
    no_leakage: bool
    reproducible: bool
    risk_budget_ok: bool

    def __post_init__(self) -> None:
        if not str(self.candidate_id).strip():
            raise ValueError("candidate_id is required")
        object.__setattr__(self, "candidate_id", str(self.candidate_id).strip())
        object.__setattr__(self, "data_mode", validate_data_mode(self.data_mode))
        windows = int(self.walk_forward_windows)
        if windows < 1:
            raise ValueError("walk-forward windows must be positive")
        object.__setattr__(self, "walk_forward_windows", windows)
        for name in ("oos_excess_returns", "rank_ic"):
            values = tuple(float(value) for value in getattr(self, name))
            if len(values) != windows:
                raise ValueError(f"{name} length must equal walk-forward windows")
            if any(not _finite(value) for value in values):
                raise ValueError(f"{name} contains non-finite values")
            object.__setattr__(self, name, values)
        for name in (
            "calibration_error",
            "max_drawdown",
            "turnover",
            "pbo",
            "deflated_sharpe",
        ):
            value = float(getattr(self, name))
            if not _finite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.calibration_error < 0 or self.turnover < 0 or not 0 <= self.pbo <= 1:
            raise ValueError("research evidence metric is outside its valid range")


@dataclass(frozen=True)
class ResearchGateResult:
    candidate_id: str
    passed: bool
    checks: dict[str, bool]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "passed": self.passed,
            "checks": dict(self.checks),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ProductionResearchGate:
    """Conservative defaults; callers may tighten them but not bypass checks."""

    minimum_walk_forward_windows: int = 3
    minimum_positive_window_fraction: float = 0.60
    minimum_median_rank_ic: float = 0.0
    maximum_calibration_error: float = 0.15
    maximum_drawdown: float = -0.35
    maximum_turnover: float = 1.50
    maximum_pbo: float = 0.50
    minimum_deflated_sharpe: float = 0.0

    def __post_init__(self) -> None:
        if self.minimum_walk_forward_windows < 1:
            raise ValueError("minimum_walk_forward_windows must be positive")
        for name in (
            "minimum_positive_window_fraction",
            "maximum_calibration_error",
            "maximum_turnover",
            "maximum_pbo",
        ):
            value = float(getattr(self, name))
            if not 0 <= value <= 1 and name != "maximum_turnover":
                raise ValueError(f"{name} must be between zero and one")
        if self.maximum_turnover < 0 or self.maximum_drawdown > 0:
            raise ValueError("research risk thresholds are invalid")

    def evaluate(self, evidence: ResearchEvidence) -> ResearchGateResult:
        positive_fraction = sum(value > 0 for value in evidence.oos_excess_returns) / len(
            evidence.oos_excess_returns
        )
        checks = {
            # Historical screens are engineering diagnostics only.  They must
            # never satisfy a production promotion gate; only prospective
            # paper evidence may proceed here.
            "historical_data": evidence.data_mode == "paper",
            "walk_forward": evidence.walk_forward_windows >= self.minimum_walk_forward_windows,
            "oos_edge": positive_fraction >= self.minimum_positive_window_fraction
            and median(evidence.oos_excess_returns) > 0,
            "rank_ic": median(evidence.rank_ic) > self.minimum_median_rank_ic,
            "calibration": evidence.calibration_error <= self.maximum_calibration_error,
            "risk": (
                evidence.max_drawdown >= self.maximum_drawdown
                and evidence.turnover <= self.maximum_turnover
                and evidence.capacity_ok
                and evidence.risk_budget_ok
            ),
            "overfit": (
                evidence.pbo <= self.maximum_pbo
                and evidence.deflated_sharpe >= self.minimum_deflated_sharpe
            ),
            "research_integrity": evidence.no_leakage and evidence.reproducible,
        }
        reasons = tuple(key for key, passed in checks.items() if not passed)
        return ResearchGateResult(
            candidate_id=evidence.candidate_id,
            passed=not reasons,
            checks=checks,
            reasons=reasons,
        )


def _finite(value: float) -> bool:
    return value == value and abs(value) != float("inf")
