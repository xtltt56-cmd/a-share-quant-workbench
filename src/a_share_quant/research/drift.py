"""Small, dependency-light distribution-drift checks for model operations."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite, log, sqrt
from statistics import mean, median


@dataclass(frozen=True)
class DriftAssessment:
    status: str
    psi: float
    mean_shift: float
    reason: str


class DriftMonitor:
    def __init__(
        self,
        *,
        warn_psi: float = 0.10,
        block_psi: float = 0.25,
        block_mean_shift: float = 5.0,
        bins: int = 10,
    ) -> None:
        if not 0 < warn_psi < block_psi:
            raise ValueError("drift PSI thresholds must satisfy 0 < warn < block")
        if block_mean_shift <= 0 or bins < 2:
            raise ValueError("drift thresholds and bins must be positive")
        self.warn_psi = float(warn_psi)
        self.block_psi = float(block_psi)
        self.block_mean_shift = float(block_mean_shift)
        self.bins = int(bins)

    def assess(self, baseline: Iterable[float], current: Iterable[float]) -> DriftAssessment:
        expected = _finite_values(baseline, "baseline")
        observed = _finite_values(current, "current")
        if not expected or not observed:
            raise ValueError("drift inputs cannot be empty")
        edges = _quantile_edges(expected, self.bins)
        expected_counts = _histogram(expected, edges)
        observed_counts = _histogram(observed, edges)
        psi = _psi(expected_counts, observed_counts)
        baseline_std = _std(expected)
        mean_shift = abs(mean(observed) - mean(expected)) / max(baseline_std, 1e-12)
        if psi >= self.block_psi or mean_shift >= self.block_mean_shift:
            status = "BLOCKED"
            reason = "feature distribution drift exceeds the blocking threshold"
        elif psi >= self.warn_psi:
            status = "WARN"
            reason = "feature distribution drift requires review"
        else:
            status = "PASS"
            reason = "feature distribution remains within the monitoring threshold"
        return DriftAssessment(status, psi, mean_shift, reason)


def _finite_values(values: Iterable[float], field: str) -> list[float]:
    result = [float(value) for value in values]
    if any(not isfinite(value) for value in result):
        raise ValueError(f"{field} values must be finite")
    return result


def _quantile_edges(values: list[float], bins: int) -> list[float]:
    ordered = sorted(values)
    candidates = [ordered[0]]
    for index in range(1, bins):
        position = (len(ordered) - 1) * index / bins
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        candidates.append(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)
    candidates.append(ordered[-1])
    edges = sorted(set(candidates))
    return edges if len(edges) >= 2 else [ordered[0] - 1.0, ordered[0] + 1.0]


def _histogram(values: list[float], edges: list[float]) -> list[int]:
    counts = [0] * (len(edges) - 1)
    for value in values:
        index = min(max(bisect_right(edges, value) - 1, 0), len(counts) - 1)
        counts[index] += 1
    return counts


def _psi(expected: list[int], observed: list[int]) -> float:
    epsilon = 1e-9
    expected_total = max(sum(expected), 1)
    observed_total = max(sum(observed), 1)
    value = 0.0
    for expected_count, observed_count in zip(expected, observed, strict=True):
        expected_ratio = max(expected_count / expected_total, epsilon)
        observed_ratio = max(observed_count / observed_total, epsilon)
        value += (observed_ratio - expected_ratio) * log(observed_ratio / expected_ratio)
    return float(value)


def _std(values: list[float]) -> float:
    center = median(values)
    return sqrt(sum((value - center) ** 2 for value in values) / max(len(values), 1))
