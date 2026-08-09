"""Formal-provider promotion requires persisted comparison evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ProviderAcceptancePolicy:
    minimum_coverage_ratio: float = 0.98
    maximum_timestamp_drift_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_coverage_ratio <= 1:
            raise ValueError("minimum_coverage_ratio must be between zero and one")
        if self.maximum_timestamp_drift_seconds < 0:
            raise ValueError("maximum_timestamp_drift_seconds must be non-negative")


@dataclass(frozen=True)
class ProviderComparison:
    incumbent: str
    candidate: str
    as_of: date
    coverage_ratio: float
    max_timestamp_drift_seconds: float
    adjustment_match: bool
    identifier_match: bool
    suspension_match: bool

    def __post_init__(self) -> None:
        if not str(self.incumbent).strip() or not str(self.candidate).strip():
            raise ValueError("provider names are required")
        if self.incumbent == self.candidate:
            raise ValueError("incumbent and candidate must differ")
        if not isinstance(self.as_of, date):
            raise ValueError("as_of must be a date")
        if not 0 <= float(self.coverage_ratio) <= 1:
            raise ValueError("coverage_ratio must be between zero and one")
        if float(self.max_timestamp_drift_seconds) < 0:
            raise ValueError("max_timestamp_drift_seconds must be non-negative")

    def passes(self, policy: ProviderAcceptancePolicy) -> bool:
        return (
            self.coverage_ratio >= policy.minimum_coverage_ratio
            and self.max_timestamp_drift_seconds <= policy.maximum_timestamp_drift_seconds
            and self.adjustment_match
            and self.identifier_match
            and self.suspension_match
        )


class ProviderRegistry:
    """Tracks the current formal source without importing provider SDKs."""

    def __init__(
        self,
        *,
        formal_provider: str,
        policy: ProviderAcceptancePolicy | None = None,
    ) -> None:
        if not str(formal_provider).strip():
            raise ValueError("formal_provider is required")
        self.formal_provider = str(formal_provider).strip()
        self.policy = policy or ProviderAcceptancePolicy()
        self._comparisons: dict[tuple[str, str], ProviderComparison] = {}

    def record_comparison(self, comparison: ProviderComparison) -> None:
        if comparison.incumbent != self.formal_provider:
            raise ValueError("comparison incumbent must be the current formal provider")
        self._comparisons[(comparison.incumbent, comparison.candidate)] = comparison

    def promote(self, candidate: str) -> None:
        normalized = str(candidate).strip()
        comparison = self._comparisons.get((self.formal_provider, normalized))
        if comparison is None or not comparison.passes(self.policy):
            raise ValueError("comparison evidence does not authorize provider promotion")
        self.formal_provider = normalized

    def comparison_for(self, candidate: str) -> ProviderComparison | None:
        return self._comparisons.get((self.formal_provider, str(candidate).strip()))
