"""Framework-neutral portfolio strategy contracts for Stage 3B."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from a_share_quant.contracts.stage3 import ExecutionSpec, PortfolioTarget, SignalFrame


def _as_date(value: date | str) -> date:
    parsed = date.fromisoformat(str(value)) if isinstance(value, str) else value
    if not isinstance(parsed, date):
        raise TypeError("rebalance dates must be date values")
    return parsed


@dataclass(frozen=True)
class RebalancePolicy:
    """One shared schedule definition used by every fast-research adapter."""

    frequency: str = "daily"
    interval_days: int = 1

    def __post_init__(self) -> None:
        frequency = str(self.frequency).strip().lower()
        if frequency not in {"daily", "weekly", "every_n_days"}:
            raise ValueError("frequency must be daily, weekly, or every_n_days")
        if int(self.interval_days) < 1:
            raise ValueError("interval_days must be positive")
        if frequency == "daily" and int(self.interval_days) != 1:
            raise ValueError("daily policy must use interval_days=1")
        if frequency == "weekly" and int(self.interval_days) != 7:
            raise ValueError("weekly policy must use interval_days=7")
        object.__setattr__(self, "frequency", frequency)
        object.__setattr__(self, "interval_days", int(self.interval_days))

    @classmethod
    def daily(cls) -> RebalancePolicy:
        return cls("daily", 1)

    @classmethod
    def weekly(cls) -> RebalancePolicy:
        return cls("weekly", 7)

    @classmethod
    def every_n_days(cls, n_days: int) -> RebalancePolicy:
        return cls("every_n_days", n_days)

    def should_rebalance(
        self,
        current_date: date | str,
        last_rebalance_date: date | str | None,
    ) -> bool:
        if last_rebalance_date is None:
            return True
        current = _as_date(current_date)
        previous = _as_date(last_rebalance_date)
        if current <= previous:
            return False
        return (current - previous).days >= self.interval_days


class EveryNDays(RebalancePolicy):
    """Convenience constructor matching the strategy specification wording."""

    def __init__(self, n_days: int = 1) -> None:
        super().__init__(frequency="every_n_days", interval_days=n_days)


@dataclass(frozen=True)
class PortfolioSpec:
    top_k: int = 10
    target_gross_exposure: float = 0.60
    max_single_position: float = 0.15
    max_positions: int = 10
    cash_buffer: float = 0.40
    rebalance_policy: RebalancePolicy = field(default_factory=RebalancePolicy.daily)
    rank_weight_mode: str = "linear"
    n_drop: int = 1

    def __post_init__(self) -> None:
        if int(self.top_k) < 1 or int(self.max_positions) < 1:
            raise ValueError("top_k and max_positions must be positive")
        if int(self.n_drop) < 0:
            raise ValueError("n_drop must be non-negative")
        for name in ("target_gross_exposure", "max_single_position", "cash_buffer"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if float(self.target_gross_exposure) + float(self.cash_buffer) > 1 + 1e-12:
            raise ValueError("target_gross_exposure and cash_buffer exceed total capital")
        if self.max_single_position <= 0:
            raise ValueError("max_single_position must be positive")
        if self.rank_weight_mode not in {"linear", "inverse_rank"}:
            raise ValueError("rank_weight_mode must be linear or inverse_rank")
        if not isinstance(self.rebalance_policy, RebalancePolicy):
            raise TypeError("rebalance_policy must be a RebalancePolicy")
        object.__setattr__(self, "top_k", int(self.top_k))
        object.__setattr__(self, "max_positions", int(self.max_positions))
        object.__setattr__(self, "n_drop", int(self.n_drop))

    @property
    def gross_exposure(self) -> float:
        """Compatibility alias for callers that use the shorter name."""

        return self.target_gross_exposure


@runtime_checkable
class PortfolioStrategy(Protocol):
    """Strategy boundary: signals in, target weights out."""

    strategy_id: str
    strategy_version: str

    def generate_targets(
        self,
        signal_frame: SignalFrame,
        portfolio_spec: PortfolioSpec,
        execution_spec: ExecutionSpec,
        current_positions: Mapping[str, float] | None = None,
    ) -> list[PortfolioTarget]:
        """Generate target weights without accessing model or feature internals."""


def strategy_contract_types() -> Sequence[type[object]]:
    """Small introspection hook used by contract tests and documentation tooling."""

    return (SignalFrame, PortfolioSpec, ExecutionSpec, PortfolioTarget)
