"""Conservative uncertainty gates for formal model and advisory decisions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UncertaintyDecision:
    expected_excess_return: float
    confidence_interval_low: float
    confidence_interval_high: float
    abstain: bool
    reason: str


def assess_uncertainty(
    *,
    expected_excess_return: float,
    confidence_interval_low: float,
    confidence_interval_high: float,
    minimum_material_edge: float,
    maximum_interval_width: float = 0.08,
) -> UncertaintyDecision:
    """Require a material lower confidence bound before a model can assert edge."""

    if confidence_interval_low > confidence_interval_high:
        raise ValueError("confidence interval lower bound cannot exceed upper bound")
    if minimum_material_edge < 0 or maximum_interval_width <= 0:
        raise ValueError("uncertainty thresholds are invalid")
    interval_width = confidence_interval_high - confidence_interval_low
    abstain = (
        expected_excess_return <= minimum_material_edge
        or confidence_interval_low <= minimum_material_edge
        or interval_width > maximum_interval_width
    )
    reason = (
        "insufficient_or_uncertain_edge"
        if abstain
        else "material_edge_within_uncertainty_gate"
    )
    return UncertaintyDecision(
        expected_excess_return=expected_excess_return,
        confidence_interval_low=confidence_interval_low,
        confidence_interval_high=confidence_interval_high,
        abstain=abstain,
        reason=reason,
    )
