"""Immutable evidence metadata for formal market-data use."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from a_share_quant.data.normalization import normalize_symbol

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CanonicalKey:
    """The one canonical fact a provider is asserting."""

    dataset: str
    symbol: str
    effective_date: date

    def __post_init__(self) -> None:
        if not str(self.dataset).strip():
            raise ValueError("dataset is required")
        if not isinstance(self.effective_date, date):
            raise ValueError("effective_date must be a date")
        candidate = str(self.symbol).strip()
        if not candidate:
            raise ValueError("symbol is required")
        normalized = (
            normalize_symbol(candidate) if candidate.replace(".", "").isalnum() else candidate
        )
        object.__setattr__(self, "dataset", str(self.dataset).strip())
        object.__setattr__(self, "symbol", normalized)


@dataclass(frozen=True)
class DataEvidence:
    """Provider provenance that is sufficient to gate a formal data cut-off."""

    provider: str
    canonical_key: CanonicalKey
    fetched_at: datetime
    effective_at: datetime
    content_sha256: str
    source_version: str = "unknown"
    raw_reference: str = ""

    def __post_init__(self) -> None:
        if not str(self.provider).strip():
            raise ValueError("provider is required")
        if not isinstance(self.canonical_key, CanonicalKey):
            raise TypeError("canonical_key must be a CanonicalKey")
        for field in ("fetched_at", "effective_at"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field} must be timezone-aware")
        if self.effective_at > self.fetched_at:
            raise ValueError("effective_at cannot be later than fetched_at")
        digest = str(self.content_sha256).casefold()
        if not _SHA256.fullmatch(digest):
            raise ValueError("content_sha256 must be a SHA-256 digest")
        if not str(self.source_version).strip():
            raise ValueError("source_version is required")
        object.__setattr__(self, "provider", str(self.provider).strip())
        object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "source_version", str(self.source_version).strip())


class EvidenceLedger:
    """In-memory append-only provenance ledger with a unique canonical key gate."""

    def __init__(self) -> None:
        self._records: list[DataEvidence] = []
        self._keys: set[CanonicalKey] = set()

    def append(self, evidence: DataEvidence) -> None:
        if not isinstance(evidence, DataEvidence):
            raise TypeError("evidence must be a DataEvidence")
        if evidence.canonical_key in self._keys:
            raise ValueError("duplicate canonical key")
        self._records.append(evidence)
        self._keys.add(evidence.canonical_key)

    def records(self) -> tuple[DataEvidence, ...]:
        return tuple(self._records)
