"""Append-only, D-drive-bound storage for prospective model evidence.

The store intentionally has a very small surface.  Predictions are written as
individual JSONL records before their outcomes exist, and later outcomes are
appended as separate records.  Nothing in this module offers update/delete
semantics: a corrected prediction is a new prediction from a new model
version.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from a_share_quant.storage.project_storage import ProjectStoragePolicy, StorageBoundaryError

if TYPE_CHECKING:  # pragma: no cover - imports only used for type checking
    from a_share_quant.research.prospective_competition import (
        OutcomeObservation,
        ProspectivePrediction,
    )


class ImmutableLedgerError(RuntimeError):
    """Raised when a caller attempts to mutate append-only evidence."""


class LedgerIntegrityError(RuntimeError):
    """Raised when an append-only record is malformed or tampered with."""


_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_DIGEST_LENGTH = 64


class ProspectiveLedgerStore:
    """Durable append-only JSONL store for predictions and settlements.

    ``policy`` is preferred by production callers.  For tests and small local
    tools a concrete path is accepted, but it must still resolve to the D
    drive.  The default path is under the project runtime evidence directory.
    """

    FORMAT_VERSION = 1

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        policy: ProjectStoragePolicy | None = None,
    ) -> None:
        if policy is not None:
            raw_path = path or ".runtime/research/prospective/predictions.jsonl"
            self.policy = policy
            self.path = policy.authorize(raw_path)
            self.lock_path = policy.authorize(f"{self.path}.lock")
            self.root_directory = policy.authorize(self.path.parent)
        else:
            if path is None:
                raise ValueError("path or policy is required")
            self.policy = None
            self.path = Path(path).expanduser().resolve()
            if self.path.drive.casefold() != "d:":
                raise StorageBoundaryError("前瞻预测账本必须位于D盘")
            self.lock_path = self.path.with_name(f".{self.path.name}.lock").resolve()
            self.root_directory = self.path.parent
        self._prepare_paths()
        key = os.path.normcase(os.fspath(self.path))
        with _PROCESS_LOCKS_GUARD:
            self._lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        self._predictions: dict[str, ProspectivePrediction] = {}
        self._settlements: dict[str, OutcomeObservation] = {}
        self._pending: dict[str, tuple[OutcomeObservation, str]] = {}
        if self.path.exists():
            self._load()

    def append_prediction(self, prediction: ProspectivePrediction) -> ProspectivePrediction:
        """Append one prediction, returning the existing equal record idempotently."""

        prediction_id = prediction.id
        with self._locked_append():
            existing = self._predictions.get(prediction_id)
            if existing is not None:
                if existing == prediction:
                    return existing
                raise ValueError("prediction id already exists with different details")
            self._append_record({"kind": "prediction", **prediction.to_dict()})
            self._predictions[prediction_id] = prediction
        return prediction

    def append_settlement(self, outcome: OutcomeObservation) -> OutcomeObservation:
        """Append a validated mature outcome, idempotently by outcome id."""

        with self._locked_append():
            existing = self._settlements.get(outcome.prediction_id)
            if existing is not None:
                if existing == outcome:
                    return existing
                raise ValueError("prediction already has a different settlement")
            self._append_record({"kind": "settlement", **outcome.to_dict()})
            self._settlements[outcome.prediction_id] = outcome
            self._pending.pop(outcome.prediction_id, None)
        return outcome

    def append_pending(self, outcome: OutcomeObservation, reason: str) -> None:
        """Record a delayed quality decision without treating it as a result."""

        reason_value = str(reason).strip().upper()
        if not reason_value:
            raise ValueError("pending reason is required")
        with self._locked_append():
            key = f"{outcome.id}:{reason_value}"
            if key not in self._pending:
                self._append_record(
                    {"kind": "pending", "delay_reason": reason_value, **outcome.to_dict()}
                )
            self._pending[key] = (outcome, reason_value)

    def read(self, prediction_id: str) -> ProspectivePrediction:
        try:
            return self._predictions[str(prediction_id)]
        except KeyError as exc:
            raise KeyError("unknown prospective prediction") from exc

    def predictions(self) -> tuple[ProspectivePrediction, ...]:
        return tuple(self._predictions.values())

    def settlements(self) -> tuple[OutcomeObservation, ...]:
        return tuple(self._settlements.values())

    def pending(self) -> tuple[tuple[OutcomeObservation, str], ...]:
        return tuple(self._pending.values())

    @property
    def matured_predictions(self) -> int:
        return len(self._settlements)

    def delete(self, _prediction_id: str) -> None:
        raise ImmutableLedgerError("前瞻预测账本只允许追加，预测不可删除")

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        raise ImmutableLedgerError("前瞻预测账本只允许追加，记录不可修改")

    def _prepare_paths(self) -> None:
        if self.policy is not None:
            self.policy.revalidate(self.root_directory)
            self.root_directory.mkdir(parents=True, exist_ok=True)
            self.policy.revalidate(self.path)
            self.policy.revalidate(self.lock_path)
        else:
            self.root_directory.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self.path.is_file():
            raise LedgerIntegrityError("前瞻账本路径不是普通文件")
        # The byte-sized sentinel is required by Windows ``msvcrt.locking``.
        # It is created in the same D-drive directory as the ledger and is
        # never used as data.
        try:
            with self.lock_path.open("a+b") as handle:
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise LedgerIntegrityError("前瞻预测账本锁文件不可用") from exc

    def _load(self) -> None:
        if self.policy is not None:
            self.policy.revalidate(self.path)
        if not self.path.exists():
            return
        try:
            raw_lines = self.path.read_bytes().splitlines()
        except OSError as exc:
            raise LedgerIntegrityError("前瞻预测账本不可读") from exc
        for line in raw_lines:
            if not line:
                continue
            try:
                record = json.loads(line.decode("utf-8"))
                if not isinstance(record, dict):
                    raise ValueError
                digest = record.pop("record_sha256", None)
                if not isinstance(digest, str) or len(digest) != _DIGEST_LENGTH:
                    raise ValueError
                if digest != self._record_digest(record):
                    raise ValueError
                if record.get("format_version") != self.FORMAT_VERSION:
                    raise ValueError
                self._restore_record(record)
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                raise LedgerIntegrityError("前瞻预测账本完整性校验失败") from exc

    def _restore_record(self, record: dict[str, Any]) -> None:
        from a_share_quant.research.prospective_competition import (
            OutcomeObservation,
            ProspectivePrediction,
        )

        record.pop("format_version", None)
        kind = record.pop("kind", None)
        if kind == "prediction":
            prediction = ProspectivePrediction.from_dict(record)
            existing = self._predictions.get(prediction.id)
            if existing is not None and existing != prediction:
                raise LedgerIntegrityError("重复预测记录内容不一致")
            self._predictions[prediction.id] = prediction
        elif kind in {"settlement", "pending"}:
            reason = str(record.pop("delay_reason", "")).strip().upper()
            outcome = OutcomeObservation.from_dict(record)
            if kind == "settlement":
                existing = self._settlements.get(outcome.prediction_id)
                if existing is not None and existing != outcome:
                    raise LedgerIntegrityError("重复结算记录内容不一致")
                self._settlements[outcome.prediction_id] = outcome
            else:
                if reason:
                    self._pending[f"{outcome.id}:{reason}"] = (outcome, reason)
        else:
            raise LedgerIntegrityError("前瞻预测账本记录类型无效")

    def _append_record(self, record: dict[str, Any]) -> None:
        record = {"format_version": self.FORMAT_VERSION, **record}
        record["record_sha256"] = self._record_digest(record)
        line = (
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if self.policy is not None:
            self.policy.revalidate(self.path)
        try:
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            fd = os.open(self.path, flags, 0o600)
            try:
                view = memoryview(line)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("ledger append made no progress")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as exc:
            raise LedgerIntegrityError("前瞻预测账本追加失败") from exc

    @staticmethod
    def _record_digest(record: dict[str, Any]) -> str:
        immutable = {key: value for key, value in record.items() if key != "record_sha256"}
        payload = json.dumps(
            immutable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @contextmanager
    def _locked_append(self):  # type: ignore[no-untyped-def]
        with self._lock:
            # The thread lock covers in-process read/append state.  The file
            # lock covers independent worker processes, so each JSONL record
            # is appended only after the latest ledger state is observed.
            with self.lock_path.open("a+b") as handle:
                _lock_file(handle)
                try:
                    self._load()
                    yield
                finally:
                    _unlock_file(handle)


def _lock_file(handle: Any, *, timeout_seconds: float = 30.0) -> None:
    """Acquire one byte of the sidecar lock file across processes."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        deadline = time.monotonic() + timeout_seconds
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("前瞻预测账本锁等待超时") from exc
                time.sleep(0.05)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = [
    "ImmutableLedgerError",
    "LedgerIntegrityError",
    "ProspectiveLedgerStore",
]
