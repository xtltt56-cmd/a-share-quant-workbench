"""Workbench-owned, allowlisted research job lifecycle supervision.

The workbench is the only process allowed to start the research pipeline. A
worker receives one fixed command and inherits a process-local environment
whose temporary/cache locations are below the project root. No CLI path or
flag is forwarded to a worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from a_share_quant.storage.project_storage import ProjectStoragePolicy

_ALLOWED_COMMANDS = frozenset(
    {
        ("research", "history"),
        ("research", "screen"),
        ("research", "predict"),
        ("research", "settle"),
    }
)
_NETWORK_COMMANDS = frozenset({("research", "history"), ("research", "settle")})
_DEFAULT_STAGE = {
    ("research", "history"): "history-backfill",
    ("research", "screen"): "engineering-screen",
    ("research", "predict"): "future-predict",
    ("research", "settle"): "outcome-settlement",
}
_VALID_STAGES = frozenset(_DEFAULT_STAGE.values())
_MAX_CHECKPOINT_BYTES = 1_048_576


@dataclass(frozen=True)
class ResearchJob:
    """A fixed internal job and its resumable lifecycle evidence."""

    job_id: str
    command: tuple[str, ...]
    due_at: datetime
    input_fingerprint: str | None = None
    stage: str = ""
    artifact_digest: str | None = None
    next_eligible_at: datetime | None = None

    @property
    def resource_class(self) -> str:
        return "network" if self.command in _NETWORK_COMMANDS else "cpu"

    @property
    def completed_artifact_digest(self) -> str | None:
        return self.artifact_digest


@dataclass(frozen=True)
class ShutdownResult:
    checkpoint_saved: bool
    children_stopped: bool


class ResearchJobSupervisor:
    """Manage only repository-registered children and fail closed on state."""

    def __init__(
        self,
        root: str | Path,
        *,
        launcher: Callable[[tuple[str, ...]], Any] | None = None,
        storage_policy: ProjectStoragePolicy | None = None,
    ) -> None:
        self.storage_policy = storage_policy
        self.root = (
            storage_policy.authorize(root)
            if storage_policy is not None
            else Path(root).resolve()
        )
        self.root.mkdir(parents=True, exist_ok=True)
        if storage_policy is not None:
            storage_policy.revalidate(self.root)
        self.checkpoint_path = self.root / "research-checkpoint.json"
        self._launcher = launcher or (
            lambda command: _default_launcher(
                command,
                repo_root=self._repo_root,
                storage_policy=self.storage_policy,
            )
        )
        self._jobs: dict[str, ResearchJob] = {}
        self._children: dict[str, Any] = {}
        self._accepting = True
        self._last_history_session: str | None = None
        self._last_screen_fingerprint: str | None = None
        self._restore_schedule_state()

    @property
    def _repo_root(self) -> Path:
        # A production checkpoint lives at <repo>/.runtime/research.
        return self.root.parents[1]

    def register_job(
        self,
        job_id: str,
        command: tuple[str, ...],
        *,
        due_at: datetime,
        input_fingerprint: str | None = None,
        stage: str | None = None,
        artifact_digest: str | None = None,
        completed_artifact_digest: str | None = None,
        next_eligible_at: datetime | None = None,
        next_eligible: datetime | None = None,
    ) -> None:
        """Register a fixed command; arbitrary flags and paths are rejected."""

        normalized = tuple(str(item).strip() for item in command)
        normalized_id = str(job_id).strip()
        if (
            not normalized_id
            or _unsafe_argument(normalized_id)
            or normalized not in _ALLOWED_COMMANDS
        ):
            raise ValueError("job is not allowlisted")
        due = _utc(due_at)
        resolved_stage = stage or _DEFAULT_STAGE[normalized]
        if resolved_stage not in _VALID_STAGES:
            raise ValueError("job stage is not allowlisted")
        fingerprint = _safe_metadata(input_fingerprint, "input_fingerprint")
        if (
            artifact_digest is not None
            and completed_artifact_digest is not None
            and artifact_digest != completed_artifact_digest
        ):
            raise ValueError("artifact digests do not match")
        digest = _safe_metadata(
            artifact_digest or completed_artifact_digest, "artifact_digest"
        )
        if next_eligible_at is not None and next_eligible is not None:
            raise ValueError("next eligible times do not match")
        eligible_value = next_eligible_at if next_eligible_at is not None else next_eligible
        eligible = _utc(eligible_value) if eligible_value is not None else due
        self._jobs[normalized_id] = ResearchJob(
            normalized_id,
            normalized,
            due,
            fingerprint,
            resolved_stage,
            digest,
            eligible,
        )

    def register_default_jobs(
        self,
        *,
        now: datetime,
        session_completed: bool = False,
        data_fingerprint: str | None = None,
        data_refreshed: bool = False,
        outcome_cutoff: datetime | None = None,
    ) -> tuple[str, ...]:
        """Register jobs whose prerequisites are true at this lifecycle tick.

        ``history`` is eligible only after a completed session; ``screen`` is
        eligible only for a new dataset fingerprint; ``predict`` is scheduled
        before its outcome cutoff; and ``settle`` follows a data refresh.
        """

        current = _utc(now)
        registered: list[str] = []
        session_key = current.date().isoformat()
        if session_completed and self._last_history_session != session_key:
            self.register_job(
                "history-backfill",
                ("research", "history"),
                due_at=current,
                input_fingerprint=data_fingerprint,
            )
            self._last_history_session = session_key
            registered.append("history-backfill")

        if data_fingerprint and data_fingerprint != self._last_screen_fingerprint:
            self.register_job(
                "screen",
                ("research", "screen"),
                due_at=current,
                input_fingerprint=data_fingerprint,
            )
            self._last_screen_fingerprint = data_fingerprint
            registered.append("screen")

        if outcome_cutoff is not None and current < _utc(outcome_cutoff):
            self.register_job(
                "predict",
                ("research", "predict"),
                due_at=current,
                input_fingerprint=data_fingerprint,
                next_eligible_at=current,
            )
            registered.append("predict")

        if data_refreshed:
            self.register_job(
                "settle",
                ("research", "settle"),
                due_at=current,
                input_fingerprint=data_fingerprint,
            )
            registered.append("settle")
        return tuple(registered)

    def start_due_jobs(self, *, now: datetime) -> tuple[str, ...]:
        if not self._accepting:
            return ()
        current = _utc(now)
        active_classes = {
            job.resource_class
            for job_id, child in tuple(self._children.items())
            if self._child_running(child)
            for job in (self._jobs.get(job_id),)
            if job is not None
        }
        started: list[str] = []
        for job in self._jobs.values():
            if job.job_id in self._children and self._child_running(self._children[job.job_id]):
                continue
            if job.due_at > current or (job.next_eligible_at or job.due_at) > current:
                continue
            if job.resource_class in active_classes:
                continue
            self._children[job.job_id] = self._launcher(job.command)
            active_classes.add(job.resource_class)
            started.append(job.job_id)
        return tuple(started)

    def owned_children(self) -> tuple[Any, ...]:
        """Return only children started by this supervisor."""

        return tuple(self._children.values())

    def shutdown(self, *, timeout_seconds: float = 5.0) -> ShutdownResult:
        """Persist evidence first, then stop every owned child."""

        self._accepting = False
        checkpoint_saved = self._write_checkpoint()
        stopped = True
        for child in tuple(self._children.values()):
            try:
                if self._child_running(child):
                    child.terminate()
                    try:
                        child.wait(timeout=timeout_seconds)
                    except Exception:
                        pass
                if self._child_running(child):
                    kill = getattr(child, "kill", None)
                    if callable(kill):
                        kill()
                    try:
                        child.wait(timeout=timeout_seconds)
                    except Exception:
                        pass
                if self._child_running(child):
                    stopped = False
            except Exception:
                stopped = False
        return ShutdownResult(checkpoint_saved=checkpoint_saved, children_stopped=stopped)

    def resume_eligible_jobs(self) -> tuple[str, ...]:
        payload = self._read_checkpoint()
        if payload is None:
            return ()
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            return ()
        eligible: list[str] = []
        for item in jobs:
            if not isinstance(item, dict) or not self._valid_checkpoint_job(item):
                return ()
            eligible.append(str(item["job_id"]))
        return tuple(eligible)

    def _write_checkpoint(self) -> bool:
        body = {
            "format_version": 2,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "jobs": [
                {
                    "job_id": job.job_id,
                    "command": list(job.command),
                    "input_fingerprint": job.input_fingerprint,
                    "stage": job.stage,
                    "artifact_digest": job.artifact_digest,
                    "completed_artifact_digest": job.artifact_digest,
                    "next_eligible_at": (job.next_eligible_at or job.due_at).isoformat(),
                    "due_at": job.due_at.isoformat(),
                }
                for job in self._jobs.values()
            ],
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        artifact = {**body, "sha256": hashlib.sha256(encoded).hexdigest()}
        payload = json.dumps(artifact, sort_keys=True, indent=2).encode()
        self.root.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".research-checkpoint.", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.checkpoint_path)
            return True
        except OSError:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            return False

    def _read_checkpoint(self) -> dict[str, Any] | None:
        try:
            raw = self.checkpoint_path.read_bytes()
            if not raw or len(raw) > _MAX_CHECKPOINT_BYTES:
                return None
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict) or parsed.get("format_version") != 2:
                return None
            digest = parsed.get("sha256")
            body = {key: value for key, value in parsed.items() if key != "sha256"}
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            if not isinstance(digest, str) or digest != hashlib.sha256(encoded).hexdigest():
                return None
            return body
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            return None

    def _restore_schedule_state(self) -> None:
        """Recover de-duplication state without resuming any child."""

        payload = self._read_checkpoint()
        if payload is None or not isinstance(payload.get("jobs"), list):
            return
        for item in payload["jobs"]:
            if not isinstance(item, dict) or not self._valid_checkpoint_job(item):
                return
            command = tuple(item["command"])
            if command == ("research", "screen"):
                self._last_screen_fingerprint = item.get("input_fingerprint")
            elif command == ("research", "history"):
                try:
                    self._last_history_session = datetime.fromisoformat(
                        item["due_at"]
                    ).date().isoformat()
                except (TypeError, ValueError):
                    return

    @staticmethod
    def _valid_checkpoint_job(item: dict[str, Any]) -> bool:
        job_id = item.get("job_id")
        command = item.get("command")
        stage = item.get("stage")
        if (
            not isinstance(job_id, str)
            or not job_id.strip()
            or not isinstance(command, list)
            or tuple(command) not in _ALLOWED_COMMANDS
            or stage not in _VALID_STAGES
        ):
            return False
        if any(_unsafe_argument(str(part)) for part in command):
            return False
        for key in ("due_at", "next_eligible_at"):
            if not isinstance(item.get(key), str):
                return False
            try:
                _utc(datetime.fromisoformat(item[key]))
            except (TypeError, ValueError):
                return False
        for key in ("input_fingerprint", "artifact_digest"):
            value = item.get(key)
            if value is not None:
                try:
                    _safe_metadata(value, key)
                except ValueError:
                    return False
        return True

    @staticmethod
    def _child_running(child: Any) -> bool:
        try:
            return bool(child.is_running())
        except AttributeError:
            return child.poll() is None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _safe_metadata(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized or len(normalized) > 256 or _unsafe_argument(normalized):
        raise ValueError(f"{field} is not safe")
    return normalized


def _unsafe_argument(value: str) -> bool:
    return any(token in value for token in ("..", "/", "\\", "--"))


def _default_launcher(
    command: tuple[str, ...],
    *,
    repo_root: Path,
    storage_policy: ProjectStoragePolicy | None,
) -> Any:
    if command not in _ALLOWED_COMMANDS:
        raise ValueError("job is not allowlisted")
    policy = storage_policy or ProjectStoragePolicy(repo_root)
    safe_root = policy.revalidate(policy.repo_root)
    environment = policy.child_environment(os.environ)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [sys.executable, "-m", "a_share_quant.runtime.research_worker", command[1]],
        cwd=str(safe_root),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    return _OwnedProcess(process)


class _OwnedProcess:
    def __init__(self, process: Any) -> None:
        self.process = process

    def is_running(self) -> bool:
        return self.process.poll() is None

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()

    def wait(self, timeout: float | None = None) -> None:
        self.process.wait(timeout=timeout)


__all__ = ["ResearchJob", "ResearchJobSupervisor", "ShutdownResult"]
