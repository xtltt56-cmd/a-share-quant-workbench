"""Durable, integrity-checked storage for confirmed broker position snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .contracts import money
from .import_inbox import ImportedPosition, _is_reparse

_FORMAT_VERSION = 1
_MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ENVELOPE_KEYS = {"format_version", "payload", "payload_sha256"}


@dataclass(frozen=True)
class ImportedAccountSnapshot:
    snapshot_id: str
    source_sha256: str
    source_name: str
    as_of: date
    imported_at: datetime
    cash: Decimal | None
    positions: tuple[ImportedPosition, ...]

    def __post_init__(self) -> None:
        if not str(self.snapshot_id).strip():
            raise ValueError("snapshot_id is required")
        if not _SHA256_RE.fullmatch(str(self.source_sha256)):
            raise ValueError("source_sha256 must be lowercase SHA-256")
        source_name = str(self.source_name).strip()
        if not source_name or "/" in source_name or "\\" in source_name:
            raise ValueError("source_name is invalid")
        object.__setattr__(self, "source_name", source_name)
        if not isinstance(self.as_of, date):
            raise ValueError("snapshot as_of must be a date")
        if self.imported_at.tzinfo is None or self.imported_at.utcoffset() is None:
            raise ValueError("snapshot imported_at must be timezone-aware")
        object.__setattr__(self, "imported_at", self.imported_at.astimezone(timezone.utc))
        if self.cash is not None:
            if not self.cash.is_finite():
                raise ValueError("cash must be finite")
            object.__setattr__(self, "cash", money(self.cash))
        positions = tuple(self.positions)
        symbols = [position.symbol for position in positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate position symbol")
        object.__setattr__(self, "positions", positions)


class AccountSnapshotStore:
    """Atomically persist one independently sourced account snapshot."""

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def save(self, snapshot: ImportedAccountSnapshot) -> None:
        if not isinstance(snapshot, ImportedAccountSnapshot):
            raise TypeError("snapshot must be an ImportedAccountSnapshot")
        current = self._now()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("snapshot clock must be timezone-aware")
        if snapshot.imported_at > current.astimezone(timezone.utc) + timedelta(minutes=5):
            raise ValueError("snapshot imported_at is in the future")
        self._validate_path()
        payload = self._payload(snapshot)
        envelope = {
            "format_version": _FORMAT_VERSION,
            "payload": payload,
            "payload_sha256": _digest(payload),
        }
        encoded = (_canonical_json(envelope) + "\n").encode("utf-8")
        if len(encoded) > _MAX_SNAPSHOT_BYTES:
            raise ValueError("account snapshot exceeds the size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            self._validate_path()
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_path()
            os.replace(temporary_path, self.path)
            temporary_path = None
        except OSError as exc:
            raise ValueError("account snapshot cannot be written") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self) -> ImportedAccountSnapshot | None:
        if not self.path.exists():
            return None
        self._validate_path(require_existing=True)
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise ValueError("account snapshot cannot be read") from exc
        if len(raw) == 0 or len(raw) > _MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot integrity validation failed")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("snapshot integrity validation failed") from exc
        if (
            not isinstance(envelope, dict)
            or set(envelope) != _REQUIRED_ENVELOPE_KEYS
            or envelope["format_version"] != _FORMAT_VERSION
            or not isinstance(envelope["payload"], dict)
            or not isinstance(envelope["payload_sha256"], str)
            or not _SHA256_RE.fullmatch(envelope["payload_sha256"])
            or not hmac.compare_digest(
                envelope["payload_sha256"], _digest(envelope["payload"])
            )
        ):
            raise ValueError("snapshot integrity validation failed")
        try:
            return self._from_payload(envelope["payload"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("snapshot integrity validation failed") from exc

    def _validate_path(self, *, require_existing: bool = False) -> None:
        if self.path.exists() and (_is_reparse(self.path) or not self.path.is_file()):
            raise ValueError("snapshot path is not safe")
        if require_existing and not self.path.is_file():
            raise ValueError("snapshot path is not safe")
        current = self.path.parent
        while True:
            if current.exists() and _is_reparse(current):
                raise ValueError("snapshot path is not safe")
            if current.parent == current:
                break
            current = current.parent

    @staticmethod
    def _payload(snapshot: ImportedAccountSnapshot) -> dict[str, object]:
        return {
            "snapshot_id": snapshot.snapshot_id,
            "source_sha256": snapshot.source_sha256,
            "source_name": snapshot.source_name,
            "as_of": snapshot.as_of.isoformat(),
            "imported_at": snapshot.imported_at.isoformat(),
            "cash": str(snapshot.cash) if snapshot.cash is not None else None,
            "positions": [
                {
                    "symbol": position.symbol,
                    "name": position.name,
                    "total_quantity": position.total_quantity,
                    "available_quantity": position.available_quantity,
                    "frozen_quantity": position.frozen_quantity,
                    "average_cost": str(position.average_cost),
                }
                for position in snapshot.positions
            ],
        }

    @staticmethod
    def _from_payload(payload: dict[str, object]) -> ImportedAccountSnapshot:
        if set(payload) != {
            "snapshot_id",
            "source_sha256",
            "source_name",
            "as_of",
            "imported_at",
            "cash",
            "positions",
        }:
            raise ValueError("payload keys are invalid")
        raw_positions = payload["positions"]
        if not isinstance(raw_positions, list) or not raw_positions:
            raise ValueError("positions are invalid")
        positions = tuple(
            ImportedPosition(
                symbol=str(item["symbol"]),
                name=str(item["name"]),
                total_quantity=int(item["total_quantity"]),
                available_quantity=int(item["available_quantity"]),
                frozen_quantity=int(item["frozen_quantity"]),
                average_cost=Decimal(str(item["average_cost"])),
            )
            for item in raw_positions
            if isinstance(item, dict)
            and set(item)
            == {
                "symbol",
                "name",
                "total_quantity",
                "available_quantity",
                "frozen_quantity",
                "average_cost",
            }
        )
        if len(positions) != len(raw_positions):
            raise ValueError("position payload is invalid")
        raw_cash = payload["cash"]
        cash = None if raw_cash is None else Decimal(str(raw_cash))
        return ImportedAccountSnapshot(
            snapshot_id=str(payload["snapshot_id"]),
            source_sha256=str(payload["source_sha256"]),
            source_name=str(payload["source_name"]),
            as_of=date.fromisoformat(str(payload["as_of"])),
            imported_at=datetime.fromisoformat(str(payload["imported_at"])),
            cash=cash,
            positions=positions,
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
