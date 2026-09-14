"""Small atomic cache for successful public risk-intelligence snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from a_share_quant.intelligence.contracts import (
    PublicRiskAssessment,
    PublicRiskEvent,
    PublicRiskSnapshot,
)


class PublicRiskStore:
    _FORMAT_VERSION = 1
    _MAX_BYTES = 4 * 1024 * 1024
    _TOP_LEVEL_FIELDS = frozenset({"format_version", "snapshot"})
    _SNAPSHOT_FIELDS = frozenset(
        {"source", "fetched_at", "window_start", "window_end", "status", "notice_zh", "assessments"}
    )
    _ASSESSMENT_FIELDS = frozenset(
        {"symbol", "name", "level", "reason_codes", "events", "checked_at"}
    )
    _EVENT_FIELDS = frozenset(
        {
            "event_id",
            "symbol",
            "name",
            "title",
            "announced_at",
            "source",
            "source_url",
            "level",
            "reason_codes",
        }
    )

    def __init__(self, path: str | Path, *, load_existing: bool = True) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._snapshot: PublicRiskSnapshot | None = None
        if load_existing and self.path.exists():
            self._snapshot = self._load()

    def latest(self) -> PublicRiskSnapshot | None:
        with self._lock:
            return self._snapshot

    def save(self, snapshot: PublicRiskSnapshot) -> None:
        if not isinstance(snapshot, PublicRiskSnapshot):
            raise TypeError("public risk store accepts only PublicRiskSnapshot")
        with self._lock:
            if self._snapshot is not None and snapshot.fetched_at < self._snapshot.fetched_at:
                raise ValueError("older public risk snapshot cannot replace newer evidence")
            self._persist(snapshot)
            self._snapshot = snapshot

    def _load(self) -> PublicRiskSnapshot:
        path = self.path
        if path.is_symlink() or not path.is_file():
            raise ValueError("public risk artifact is invalid")
        try:
            raw = path.read_bytes()
            if not raw or len(raw) > self._MAX_BYTES:
                raise ValueError
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != self._TOP_LEVEL_FIELDS:
                raise ValueError
            if payload["format_version"] != self._FORMAT_VERSION:
                raise ValueError
            snapshot = self._decode_snapshot(payload["snapshot"])
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ValueError("public risk artifact is invalid") from exc
        return snapshot

    def _persist(self, snapshot: PublicRiskSnapshot) -> None:
        path = self.path
        if path.exists() and (path.is_symlink() or not path.is_file()):
            raise ValueError("public risk artifact is invalid")
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
                    {"format_version": self._FORMAT_VERSION, "snapshot": snapshot.to_dict()},
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if temporary.stat().st_size > self._MAX_BYTES:
                raise ValueError("public risk artifact is too large")
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    @classmethod
    def _decode_snapshot(cls, payload: Any) -> PublicRiskSnapshot:
        if not isinstance(payload, dict) or set(payload) != cls._SNAPSHOT_FIELDS:
            raise ValueError("invalid public risk snapshot")
        assessments = payload["assessments"]
        if not isinstance(assessments, list) or len(assessments) > 50:
            raise ValueError("invalid public risk assessments")
        return PublicRiskSnapshot(
            source=payload["source"],
            fetched_at=datetime.fromisoformat(payload["fetched_at"]),
            window_start=date.fromisoformat(payload["window_start"]),
            window_end=date.fromisoformat(payload["window_end"]),
            status=payload["status"],
            notice_zh=payload["notice_zh"],
            assessments=tuple(cls._decode_assessment(item) for item in assessments),
        )

    @classmethod
    def _decode_assessment(cls, payload: Any) -> PublicRiskAssessment:
        if not isinstance(payload, dict) or set(payload) != cls._ASSESSMENT_FIELDS:
            raise ValueError("invalid public risk assessment")
        reasons = payload["reason_codes"]
        events = payload["events"]
        if not isinstance(reasons, list) or not isinstance(events, list) or len(events) > 8:
            raise ValueError("invalid public risk assessment values")
        return PublicRiskAssessment(
            symbol=payload["symbol"],
            name=payload["name"],
            level=payload["level"],
            reason_codes=tuple(reasons),
            events=tuple(cls._decode_event(item) for item in events),
            checked_at=datetime.fromisoformat(payload["checked_at"]),
        )

    @classmethod
    def _decode_event(cls, payload: Any) -> PublicRiskEvent:
        if not isinstance(payload, dict) or set(payload) != cls._EVENT_FIELDS:
            raise ValueError("invalid public risk event")
        reasons = payload["reason_codes"]
        if not isinstance(reasons, list):
            raise ValueError("invalid public risk event reasons")
        return PublicRiskEvent(
            event_id=payload["event_id"],
            symbol=payload["symbol"],
            name=payload["name"],
            title=payload["title"],
            announced_at=datetime.fromisoformat(payload["announced_at"]),
            source=payload["source"],
            source_url=payload["source_url"],
            level=payload["level"],
            reason_codes=tuple(reasons),
        )


__all__ = ["PublicRiskStore"]
