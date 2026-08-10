"""Formal-provider promotion requires persisted comparison evidence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

_EVIDENCE_FORMAT_VERSION = 1


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
        evidence_path: str | Path | None = None,
    ) -> None:
        if not str(formal_provider).strip():
            raise ValueError("formal_provider is required")
        self.formal_provider = str(formal_provider).strip()
        self.policy = policy or ProviderAcceptancePolicy()
        self.evidence_path = Path(evidence_path) if evidence_path is not None else None
        self._comparisons: dict[tuple[str, str], ProviderComparison] = {}
        if self.evidence_path is not None:
            self._load_evidence()
            if not self.evidence_path.exists():
                self._persist_evidence()

    def record_comparison(self, comparison: ProviderComparison) -> None:
        if comparison.incumbent != self.formal_provider:
            raise ValueError("comparison incumbent must be the current formal provider")
        self._comparisons[(comparison.incumbent, comparison.candidate)] = comparison
        self._persist_evidence()

    def promote(self, candidate: str) -> None:
        normalized = str(candidate).strip()
        comparison = self._comparisons.get((self.formal_provider, normalized))
        if comparison is None or not comparison.passes(self.policy):
            raise ValueError("comparison evidence does not authorize provider promotion")
        previous = self.formal_provider
        self.formal_provider = normalized
        try:
            self._persist_evidence()
        except Exception:
            self.formal_provider = previous
            raise

    def comparison_for(self, candidate: str) -> ProviderComparison | None:
        return self._comparisons.get((self.formal_provider, str(candidate).strip()))

    def save(self) -> None:
        """Persist the current formal source and comparison evidence."""

        self._persist_evidence()

    def _load_evidence(self) -> None:
        assert self.evidence_path is not None
        path = self.evidence_path
        if not path.exists():
            return
        if path.is_symlink() or not path.is_file():
            raise ValueError("provider evidence is invalid")
        try:
            raw = path.read_bytes()
            if not raw or len(raw) > 1_048_576:
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "format_version",
                "formal_provider",
                "comparisons",
            }:
                raise ValueError
            if payload["format_version"] != _EVIDENCE_FORMAT_VERSION:
                raise ValueError
            if str(payload["formal_provider"]).strip() != self.formal_provider:
                raise ValueError("provider evidence formal provider conflicts")
            entries = payload["comparisons"]
            if not isinstance(entries, list):
                raise ValueError
            for item in entries:
                if not isinstance(item, dict) or set(item) != {
                    "incumbent",
                    "candidate",
                    "as_of",
                    "coverage_ratio",
                    "max_timestamp_drift_seconds",
                    "adjustment_match",
                    "identifier_match",
                    "suspension_match",
                }:
                    raise ValueError
                comparison = ProviderComparison(
                    incumbent=item["incumbent"],
                    candidate=item["candidate"],
                    as_of=date.fromisoformat(item["as_of"]),
                    coverage_ratio=item["coverage_ratio"],
                    max_timestamp_drift_seconds=item["max_timestamp_drift_seconds"],
                    adjustment_match=item["adjustment_match"],
                    identifier_match=item["identifier_match"],
                    suspension_match=item["suspension_match"],
                )
                self._comparisons[(comparison.incumbent, comparison.candidate)] = comparison
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("provider evidence is invalid") from exc

    def _persist_evidence(self) -> None:
        if self.evidence_path is None:
            return
        path = self.evidence_path
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError("provider evidence is invalid")
        payload: dict[str, Any] = {
            "format_version": _EVIDENCE_FORMAT_VERSION,
            "formal_provider": self.formal_provider,
            "comparisons": [
                {
                    "incumbent": item.incumbent,
                    "candidate": item.candidate,
                    "as_of": item.as_of.isoformat(),
                    "coverage_ratio": item.coverage_ratio,
                    "max_timestamp_drift_seconds": item.max_timestamp_drift_seconds,
                    "adjustment_match": item.adjustment_match,
                    "identifier_match": item.identifier_match,
                    "suspension_match": item.suspension_match,
                }
                for item in sorted(
                    self._comparisons.values(), key=lambda value: (value.incumbent, value.candidate)
                )
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise ValueError("provider evidence cannot be written") from exc
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
