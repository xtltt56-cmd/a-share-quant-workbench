"""Shared result schema returned by fast and future event backtest engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from a_share_quant.contracts.modes import validate_data_mode


@dataclass(frozen=True)
class BacktestResult:
    nav: Any
    returns: Any
    benchmark_returns: Any
    positions: Any
    orders: Any
    trades: Any
    turnover: Any
    transaction_cost: Any
    metrics: dict[str, Any]
    data_mode: str = "historical"
    engine: str = "reference-fast"
    engine_version: str = "unknown"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = field(default="stage3b-backtest-result-v1", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_mode", validate_data_mode(self.data_mode))
        if not str(self.engine).strip():
            raise ValueError("engine is required")
        if not isinstance(self.metrics, dict):
            raise TypeError("metrics must be a dictionary")
        for name in ("warnings", "limitations", "assumptions"):
            values = tuple(str(value) for value in getattr(self, name))
            object.__setattr__(self, name, values)

    def schema(self) -> tuple[str, ...]:
        return (
            "nav",
            "returns",
            "benchmark_returns",
            "positions",
            "orders",
            "trades",
            "turnover",
            "transaction_cost",
            "metrics",
            "warnings",
            "data_mode",
            "engine",
            "engine_version",
            "limitations",
            "assumptions",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "data_mode": self.data_mode,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "metrics": dict(self.metrics),
            "warnings": list(self.warnings),
            "limitations": list(self.limitations),
            "assumptions": list(self.assumptions),
        }
