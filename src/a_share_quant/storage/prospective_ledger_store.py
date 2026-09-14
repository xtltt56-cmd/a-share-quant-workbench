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
import stat
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
        recover_incomplete_tail: bool = True,
    ) -> None:
        self._recover_incomplete_tail = recover_incomplete_tail
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
            self.path = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
            if self.path.drive.casefold() != "d:":
                raise StorageBoundaryError("前瞻预测账本必须位于D盘")
            self.lock_path = self.path.with_name(f".{self.path.name}.lock")
            self.root_directory = self.path.parent
            _reject_reparse_components(self.path)
            _reject_reparse_components(self.lock_path)
        self._prepare_paths()
        key = os.path.normcase(os.fspath(self.path))
        with _PROCESS_LOCKS_GUARD:
            self._lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        self._predictions: dict[str, ProspectivePrediction] = {}
        self._settlements: dict[str, OutcomeObservation] = {}
        self._pending: dict[str, tuple[OutcomeObservation, str]] = {}
        if self.path.exists():
            with self.lock_path.open("a+b") as handle:
                _lock_file(handle)
                try:
                    self._load()
                finally:
                    _unlock_file(handle)

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
            self._validate_outcome(outcome)
            if outcome.outcome_at.date() < outcome.maturity_date:
                raise ValueError("settlement outcome is before maturity")
            self._validate_settlement_quality(outcome)
            existing = self._settlements.get(outcome.prediction_id)
            if existing is not None:
                if existing == outcome:
                    return existing
                raise ValueError("prediction already has a different settlement")
            self._append_record({"kind": "settlement", **outcome.to_dict()})
            self._settlements[outcome.prediction_id] = outcome
            for key in tuple(self._pending):
                pending_outcome = self._pending[key][0]
                if (
                    key.startswith(f"{outcome.id}:")
                    or pending_outcome.prediction_id == outcome.prediction_id
                ):
                    self._pending.pop(key, None)
        return outcome

    def append_pending(self, outcome: OutcomeObservation, reason: str) -> None:
        """Record a delayed quality decision without treating it as a result."""

        reason_value = str(reason).strip().upper()
        if not reason_value:
            raise ValueError("pending reason is required")
        with self._locked_append():
            self._validate_outcome(outcome)
            if outcome.prediction_id in self._settlements:
                raise ValueError("prediction already has a settlement")
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

    def _validate_outcome(self, outcome: OutcomeObservation) -> None:
        prediction = self._predictions.get(outcome.prediction_id)
        if prediction is None:
            raise KeyError("outcome prediction does not exist")
        if prediction.symbol != outcome.symbol:
            raise ValueError("outcome symbol does not match prediction")
        if prediction.maturity_date != outcome.maturity_date:
            raise ValueError("outcome maturity date does not match prediction")

    @staticmethod
    def _validate_settlement_quality(outcome: OutcomeObservation) -> None:
        if (
            outcome.status != "OK"
            or not outcome.fresh
            or not outcome.complete
            or not outcome.session_aligned
            or not outcome.corporate_action_ok
            or outcome.realized_price is None
            or outcome.realized_return is None
        ):
            raise ValueError("only complete OK outcomes can be settlements")

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
            _reject_reparse_components(self.root_directory)
            self.root_directory.mkdir(parents=True, exist_ok=True)
            _reject_reparse_components(self.root_directory)
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
        self._predictions.clear()
        self._settlements.clear()
        self._pending.clear()
        if self.policy is not None:
            self.policy.revalidate(self.path)
        if not self.path.exists():
            return
        try:
            raw_bytes = self.path.read_bytes()
        except OSError as exc:
            raise LedgerIntegrityError("前瞻预测账本不可读") from exc
        raw_lines = raw_bytes.splitlines(keepends=True)
        for index, raw_line in enumerate(raw_lines):
            line = raw_line.rstrip(b"\r\n")
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
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
                KeyError,
                IndexError,
                OverflowError,
                AttributeError,
            ) as exc:
                if (
                    self._recover_incomplete_tail
                    and index == len(raw_lines) - 1
                    and not raw_line.endswith((b"\n", b"\r"))
                ):
                    try:
                        with self.path.open("r+b") as handle:
                            handle.truncate(sum(len(item) for item in raw_lines[:index]))
                            handle.flush()
                            os.fsync(handle.fileno())
                        break
                    except OSError as truncate_error:
                        raise LedgerIntegrityError("前瞻预测账本尾部恢复失败") from truncate_error
                raise LedgerIntegrityError("前瞻预测账本完整性校验失败") from exc
        for outcome in tuple(self._settlements.values()):
            for key in tuple(self._pending):
                pending_outcome = self._pending[key][0]
                if (
                    key.startswith(f"{outcome.id}:")
                    or pending_outcome.prediction_id == outcome.prediction_id
                ):
                    self._pending.pop(key, None)

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
            if outcome.prediction_id not in self._predictions:
                raise LedgerIntegrityError("结算记录引用未知预测")
            prediction = self._predictions[outcome.prediction_id]
            if (
                prediction.symbol != outcome.symbol
                or prediction.maturity_date != outcome.maturity_date
            ):
                raise LedgerIntegrityError("结算记录与预测不匹配")
            if kind == "settlement":
                if outcome.outcome_at.date() < outcome.maturity_date:
                    raise LedgerIntegrityError("结算记录早于预测到期日")
                self._validate_settlement_quality(outcome)
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
            if self.policy is None:
                _reject_reparse_components(self.path)
                _reject_reparse_components(self.lock_path)
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


def _reject_reparse_components(path: Path) -> None:
    current = Path(path.anchor) if path.anchor else Path.cwd()
    for part in path.parts[1:] if path.anchor else path.parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(metadata, "st_file_attributes", 0)
        if stat.S_ISLNK(metadata.st_mode) or attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise StorageBoundaryError("前瞻预测账本路径不能包含符号链接或reparse point")


__all__ = [
    "ImmutableLedgerError",
    "LedgerIntegrityError",
    "ProspectiveLedgerStore",
]
