"""Immutable contracts for governed historical research data."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field_name} must be an aware UTC datetime")


def _require_digest(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class ResearchArtifact:
    """A manifest-backed immutable Parquet research artifact."""

    dataset: str
    key: str
    data_version: str
    sha256: str
    path: Path
    row_count: int
    size_bytes: int
    created_at: datetime
    schema_fingerprint: str

    def __post_init__(self) -> None:
        for field_name in ("dataset", "key", "data_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        _require_digest(self.sha256, "sha256")
        _require_digest(self.schema_fingerprint, "schema_fingerprint")
        _require_utc(self.created_at, "created_at")
        if not isinstance(self.path, Path):
            raise TypeError("path must be a Path")
        if not self.path.is_absolute():
            raise ValueError("path must be absolute")
        if Path(os.path.normpath(self.path)) != self.path:
            raise ValueError("path must be normalized")
        if self.path.name != f"{self.sha256}.parquet":
            raise ValueError("path filename must match sha256")
        if self.row_count < 0 or self.size_bytes < 0:
            raise ValueError("row_count and size_bytes must be non-negative")


@dataclass(frozen=True)
class DatasetCoverage:
    """Observed date and population coverage for one research dataset."""

    dataset: str
    start_date: date
    end_date: date
    symbol_count: int
    session_count: int

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if self.symbol_count < 0 or self.session_count < 0:
            raise ValueError("coverage counts must be non-negative")


@dataclass(frozen=True)
class PointInTimeInstrument:
    """Instrument membership known as of a historical date."""

    symbol: str
    name: str
    listed_on: date
    delisted_on: date | None
    is_tradable: bool

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.name.strip():
            raise ValueError("symbol and name must be non-empty")
        if self.delisted_on is not None and self.delisted_on < self.listed_on:
            raise ValueError("delisted_on must not precede listed_on")


@dataclass(frozen=True)
class CorporateAction:
    """A versioned corporate action effective on a market date."""

    symbol: str
    effective_date: date
    action_type: str
    adjustment_factor: float
    data_version: str

    def __post_init__(self) -> None:
        if not self.action_type or not self.data_version:
            raise ValueError("action_type and data_version are required")
        if not math.isfinite(self.adjustment_factor) or self.adjustment_factor <= 0:
            raise ValueError("adjustment_factor must be finite and positive")
