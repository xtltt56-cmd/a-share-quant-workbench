"""Qlib calendar initialization kept behind the integration boundary."""

from __future__ import annotations

from datetime import date
from pathlib import Path


class QlibDependencyError(RuntimeError):
    """Raised when an optional Qlib research dependency is unavailable."""


class QlibCalendarAdapter:
    def __init__(self, provider_uri: Path, *, kernels: int = 1) -> None:
        self.provider_uri = Path(provider_uri)
        self.kernels = kernels

    def initialize(self) -> None:
        try:
            import qlib
        except ImportError as exc:  # pragma: no cover - exercised in non-research environments
            raise QlibDependencyError(
                "Qlib is not installed; install the project research extra"
            ) from exc
        qlib.init(
            provider_uri=str(self.provider_uri),
            region="cn",
            expression_cache=None,
            dataset_cache=None,
            kernels=self.kernels,
        )

    def calendar(self, start_date: date | str | None = None, end_date: date | str | None = None):
        self.initialize()
        from qlib.data import D

        return D.calendar(start_time=start_date, end_time=end_date, freq="day")
