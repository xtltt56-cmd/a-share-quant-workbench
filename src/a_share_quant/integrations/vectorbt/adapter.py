"""Optional VectorBT boundary; no core module imports VectorBT directly."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from a_share_quant.backtest.reference import ReferenceFastResearchEngine


class OptionalDependencyError(ImportError):
    """Raised only when VectorBT was explicitly requested but unavailable."""


@dataclass(frozen=True)
class VectorBTAvailability:
    available: bool
    version: str | None
    reason: str


def check_vectorbt_available() -> VectorBTAvailability:
    try:
        available = importlib.util.find_spec("vectorbt") is not None
    except (ImportError, ValueError):
        available = False
    if not available:
        return VectorBTAvailability(False, None, "vectorbt is not installed")
    try:
        installed_version = version("vectorbt")
    except PackageNotFoundError:
        installed_version = "unknown"
    return VectorBTAvailability(True, installed_version, "vectorbt is importable")


class VectorBTFastResearchEngine:
    """Adapter contract with a deterministic non-VectorBT fallback."""

    def __init__(self, *, allow_reference_fallback: bool = False) -> None:
        self.allow_reference_fallback = allow_reference_fallback
        self.availability = check_vectorbt_available()

    @staticmethod
    def is_available() -> bool:
        return check_vectorbt_available().available

    def run(
        self,
        signals: Any = None,
        strategy: Any = None,
        portfolio_spec: Any = None,
        execution_spec: Any = None,
        market_data: Any = None,
        benchmark: Any = None,
        period: Any = None,
        current_positions: Any = None,
        **kwargs: Any,
    ):
        arguments = {
            "signals": signals,
            "strategy": strategy,
            "portfolio_spec": portfolio_spec,
            "execution_spec": execution_spec,
            "market_data": market_data,
            "benchmark": benchmark,
            "period": period,
            "current_positions": current_positions,
            **kwargs,
        }
        if not self.availability.available and not self.allow_reference_fallback:
            raise OptionalDependencyError(
                "VectorBT is not installed; install the optional fast-research profile "
                "or use allow_reference_fallback=True."
            )
        result = ReferenceFastResearchEngine().run(**arguments)
        warnings = list(result.warnings)
        if self.availability.available:
            engine = "vectorbt"
            engine_version = self.availability.version or "unknown"
            warnings.append(
                "VectorBT adapter currently uses the shared approximation contract; "
                "exact A-share fills still require event-engine validation."
            )
        else:
            engine = "reference-fallback"
            engine_version = "stage3b-reference-v1"
            warnings.append("VectorBT is unavailable; reference fast-research fallback used.")
        return replace(
            result,
            engine=engine,
            engine_version=engine_version,
            warnings=tuple(dict.fromkeys(warnings)),
        )
