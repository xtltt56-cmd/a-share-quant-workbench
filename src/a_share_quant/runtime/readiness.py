"""Auditable release-readiness checks for the local paper-only workbench."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import yaml


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    name: str
    category: str
    status: str
    evidence: str
    blocking: bool


@dataclass(frozen=True)
class ReadinessReport:
    checks: tuple[ReadinessCheck, ...]

    REQUIRED_CATEGORIES: ClassVar[frozenset[str]] = frozenset(
        {"data", "model", "advisory", "account", "backup", "broker", "ui", "execution_safety"}
    )
    STATUSES: ClassVar[frozenset[str]] = frozenset(
        {"PASS", "FAIL", "BLOCKED", "NOT_OBSERVED"}
    )

    @classmethod
    def load(cls, path: Path) -> ReadinessReport:
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("r", encoding="utf-8") as handle:
            raw: Any = yaml.safe_load(handle) or {}
        if not isinstance(raw, dict) or not isinstance(raw.get("checks"), list):
            raise ValueError("readiness configuration must contain a checks list")

        checks: list[ReadinessCheck] = []
        seen_keys: set[str] = set()
        seen_categories: set[str] = set()
        for item in raw["checks"]:
            if not isinstance(item, dict):
                raise ValueError("each readiness check must be a mapping")
            key = _required_text(item, "key")
            if key in seen_keys:
                raise ValueError(f"duplicate readiness check key: {key}")
            seen_keys.add(key)
            category = _required_text(item, "category")
            if category not in cls.REQUIRED_CATEGORIES:
                raise ValueError(f"unknown readiness category: {category}")
            seen_categories.add(category)
            status = _required_text(item, "status").upper()
            if status not in cls.STATUSES:
                raise ValueError(f"unknown readiness status: {status}")
            checks.append(
                ReadinessCheck(
                    key=key,
                    name=_required_text(item, "name"),
                    category=category,
                    status=status,
                    evidence=_required_text(item, "evidence"),
                    blocking=bool(item.get("blocking", True)),
                )
            )

        missing = cls.REQUIRED_CATEGORIES - seen_categories
        if missing:
            raise ValueError(f"readiness categories missing: {', '.join(sorted(missing))}")
        return cls(checks=tuple(checks))

    @property
    def overall_status(self) -> str:
        statuses = {check.status for check in self.checks}
        if "FAIL" in statuses:
            return "FAIL"
        if "BLOCKED" in statuses:
            return "BLOCKED"
        if "NOT_OBSERVED" in statuses:
            return "NOT_OBSERVED"
        return "PASS"

    @property
    def is_release_ready(self) -> bool:
        return bool(self.checks) and all(check.status == "PASS" for check in self.checks)

    @property
    def blocking_failures(self) -> tuple[str, ...]:
        return tuple(
            check.key for check in self.checks if check.blocking and check.status == "FAIL"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "is_release_ready": self.is_release_ready,
            "blocking_failures": list(self.blocking_failures),
            "checks": [
                {
                    "key": check.key,
                    "name": check.name,
                    "category": check.category,
                    "status": check.status,
                    "evidence": check.evidence,
                    "blocking": check.blocking,
                }
                for check in self.checks
            ],
        }


def _required_text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"readiness check requires non-empty {key}")
    return value.strip()
