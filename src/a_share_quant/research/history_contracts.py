"""Immutable contracts for governed historical research data."""

from __future__ import annotations

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
        _require_digest(self.sha256, "sha256")
        _require_digest(self.schema_fingerprint, "schema_fingerprint")
        _require_utc(self.created_at, "created_at")
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
        if self.adjustment_factor < 0:
            raise ValueError("adjustment_factor must be non-negative")
