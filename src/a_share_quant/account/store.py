"""Hash-chained JSONL persistence for local append-only account records."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TypeAlias

from .contracts import FillEvent, LedgerCorrection

LedgerRecord: TypeAlias = FillEvent | LedgerCorrection


class JsonlLedgerStore:
    """Persist records atomically enough for a local single-writer application.

    Each row carries the previous row's digest and its own canonical digest. This
    detects accidental edits or truncation during load; it is not a substitute
    for an encrypted remote audit service.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append_fill(self, event: FillEvent) -> str:
        return self.append(event)

    def append_correction(self, correction: LedgerCorrection) -> str:
        return self.append(correction)

    def append(self, record: LedgerRecord) -> str:
        rows = self._validated_rows()
        previous_hash = rows[-1]["record_hash"] if rows else ""
        row = {
            "kind": "fill" if isinstance(record, FillEvent) else "correction",
            "previous_hash": previous_hash,
            "payload": record.to_dict(),
        }
        record_hash = _digest(row)
        complete = {**row, "record_hash": record_hash}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(complete))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record_hash

    def load_records(self) -> tuple[LedgerRecord, ...]:
        rows = self._validated_rows()
        records: list[LedgerRecord] = []
        for row in rows:
            payload = row["payload"]
            if row["kind"] == "fill":
                records.append(FillEvent.from_dict(payload))
            elif row["kind"] == "correction":
                records.append(LedgerCorrection.from_dict(payload))
            else:  # _validated_rows makes this unreachable, retain a defensive boundary
                raise ValueError("unknown ledger record kind")
        return tuple(records)

    def load_fills(self) -> tuple[FillEvent, ...]:
        return tuple(item for item in self.load_records() if isinstance(item, FillEvent))

    def _validated_rows(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        previous_hash = ""
        rows: list[dict[str, object]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ValueError("ledger cannot be read") from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise ValueError(f"ledger hash validation failed at line {line_number}")
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"ledger hash validation failed at line {line_number}") from exc
            if not isinstance(parsed, dict):
                raise ValueError(f"ledger hash validation failed at line {line_number}")
            required = {"kind", "previous_hash", "payload", "record_hash"}
            if set(parsed) != required or parsed["kind"] not in {"fill", "correction"}:
                raise ValueError(f"ledger hash validation failed at line {line_number}")
            if parsed["previous_hash"] != previous_hash:
                raise ValueError(f"ledger hash validation failed at line {line_number}")
            unsigned = {key: parsed[key] for key in required if key != "record_hash"}
            if parsed["record_hash"] != _digest(unsigned):
                raise ValueError(f"ledger hash validation failed at line {line_number}")
            previous_hash = str(parsed["record_hash"])
            rows.append(parsed)
        return rows


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
