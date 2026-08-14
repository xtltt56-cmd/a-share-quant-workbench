"""Workbench-owned, allowlisted research job lifecycle supervision.

Only a running local workbench creates research children. A child receives one
fixed verb; its working directory and temporary/cache directories stay under
the D-drive project root. The supervisor treats a worker status artifact as
the completion contract, so an exited process cannot masquerade as success.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
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
# Only history collection contacts a provider.  Settlement consumes the local,
# already refreshed research store, so it remains available in an offline
# workbench without weakening the no-network boundary.
_NETWORK_COMMANDS = frozenset({("research", "history")})
_DEFAULT_STAGE = {
    ("research", "history"): "history-backfill",
    ("research", "screen"): "engineering-screen",
    ("research", "predict"): "future-predict",
    ("research", "settle"): "outcome-settlement",
}
_VALID_STAGES = frozenset(_DEFAULT_STAGE.values())
_MAX_CHECKPOINT_BYTES = 1_048_576
_MAX_STATUS_BYTES = 65_536
_MAX_RETAINED_TERMINAL_JOBS = 64


@dataclass(frozen=True)
class ResearchJob:
    """One fixed internal job and the evidence needed to resume it safely."""

    job_id: str
    command: tuple[str, ...]
    due_at: datetime
    input_fingerprint: str | None = None
    stage: str = ""
    artifact_digest: str | None = None
    next_eligible_at: datetime | None = None
    completed: bool = False
    process_exit_code: int | None = None
    failure_reason: str | None = None
    cycle_key: str = ""

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
        network_enabled: bool = True,
        create_root: bool = True,
    ) -> None:
        self.storage_policy = storage_policy
        self.root = (
            storage_policy.authorize(root)
            if storage_policy is not None
            else Path(root).resolve()
        )
        self.network_enabled = bool(network_enabled)
        if create_root:
            self._prepare_root()
        elif self.root.exists():
            self._revalidate(self.root)
        self.checkpoint_path = self.root / "research-checkpoint.json"
        self._launcher = launcher
        self._jobs: dict[str, ResearchJob] = {}
        self._children: dict[str, Any] = {}
        self._terminal_job_ids: set[str] = set()
        self._accepting = True
        self._last_history_session: str | None = None
        self._last_screen_fingerprint: str | None = None
        self._restore_schedule_state()

    @property
    def _repo_root(self) -> Path:
        # Production checkpoints live at <repo>/.runtime/research.
        if self.storage_policy is not None:
            return self.storage_policy.repo_root
        return self.root.parents[1]

    def status_path_for(self, job_id: str) -> Path:
        """Return the fixed per-instance worker status target for ``job_id``."""

        normalized = str(job_id).strip()
        if not normalized or _unsafe_argument(normalized):
            raise ValueError("job id is not safe")
        return self._revalidate(self.root / "status" / f"{normalized}.json")

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
        self._terminal_job_ids.discard(normalized_id)

    def register_default_jobs(
        self,
        *,
        now: datetime,
        session_completed: bool = False,
        data_fingerprint: str | None = None,
        data_refreshed: bool = False,
        outcome_cutoff: datetime | None = None,
    ) -> tuple[str, ...]:
        """Register only lifecycle-eligible jobs at one workbench tick.

        The caller determines current local evidence. Network work is not even
        registered when the workbench was opened without ``--network``;
        ``start_due_jobs`` performs the same hard check as defence in depth.
        """

        current = _utc(now)
        fingerprint = _safe_metadata(data_fingerprint, "input_fingerprint")
        registered: list[str] = []
        session_key = current.date().isoformat()
        if (
            self.network_enabled
            and session_completed
            and (
                job_id := self._register_cycle(
                ("research", "history"),
                current,
                fingerprint,
                cycle_key=f"history:{session_key}",
                )
            )
        ):
            self._last_history_session = session_key
            registered.append(job_id)

        if (
            fingerprint
            and (
                job_id := self._register_cycle(
                    ("research", "screen"),
                    current,
                    fingerprint,
                    cycle_key=f"screen:{fingerprint}",
                )
            )
        ):
            self._last_screen_fingerprint = fingerprint
            registered.append(job_id)

        if (
            outcome_cutoff is not None
            and current < _utc(outcome_cutoff)
            and (
                job_id := self._register_cycle(
                    ("research", "predict"),
                    current,
                    fingerprint,
                    cycle_key=(
                        f"predict:{fingerprint or 'no-fingerprint'}:"
                        f"{_utc(outcome_cutoff).date().isoformat()}"
                    ),
                )
            )
        ):
            registered.append(job_id)

        if (
            data_refreshed
            and (
                job_id := self._register_cycle(
                    ("research", "settle"),
                    current,
                    fingerprint,
                    cycle_key=(
                        f"settle:{fingerprint or 'no-fingerprint'}:"
                        f"{current.date().isoformat()}"
                    ),
                )
            )
        ):
            registered.append(job_id)
        self._prune_terminal_history()
        return tuple(registered)

    def start_due_jobs(self, *, now: datetime) -> tuple[str, ...]:
        """Launch at most one CPU and one network child owned by this process."""

        if not self._accepting:
            return ()
        current = _utc(now)
        self._collect_finished_children()
        active_classes = {
            job.resource_class
            for job_id, child in tuple(self._children.items())
            if self._child_running(child)
            for job in (self._jobs.get(job_id),)
            if job is not None
        }
        started: list[str] = []
        for job in tuple(self._jobs.values()):
            if job.completed or job.job_id in self._terminal_job_ids:
                continue
            if job.job_id in self._children:
                # A terminal worker remains owned evidence; it never spins in
                # a tight restart loop during this workbench session.
                continue
            if job.due_at > current or (job.next_eligible_at or job.due_at) > current:
                continue
            if job.resource_class == "network" and not self.network_enabled:
                continue
            if job.resource_class in active_classes:
                continue
            try:
                child = self._launch_job(job)
            except Exception:
                self._jobs[job.job_id] = replace(
                    job, failure_reason="LAUNCH_FAILED", process_exit_code=-1
                )
                self._terminal_job_ids.add(job.job_id)
                continue
            self._children[job.job_id] = child
            active_classes.add(job.resource_class)
            started.append(job.job_id)
        return tuple(started)

    def owned_children(self) -> tuple[Any, ...]:
        """Return only children started by this supervisor."""

        return tuple(self._children.values())

    def shutdown(self, *, timeout_seconds: float = 5.0) -> ShutdownResult:
        """Persist evidence first, then stop every owned child without orphans."""

        self._accepting = False
        self._collect_finished_children()
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
        """Return checkpoint-restored non-complete jobs without launching them."""

        # A restored terminal failure cannot be launched again in the same
        # cycle.  Reporting it as resumable used to leave an operator-facing
        # phantom job: it appeared eligible yet ``start_due_jobs`` would
        # correctly refuse to relaunch it.  A later, distinct cycle remains
        # eligible through ``_register_cycle``.
        return tuple(
            job.job_id
            for job in self._jobs.values()
            if not job.completed and job.job_id not in self._terminal_job_ids
        )

    def _register_cycle(
        self,
        command: tuple[str, ...],
        due_at: datetime,
        fingerprint: str | None,
        *,
        cycle_key: str,
    ) -> str | None:
        """Register one deterministic, auditable lifecycle cycle.

        A stage name alone is intentionally never the persistent identity.  A
        new verified input fingerprint, completed-session date, or prediction
        window produces a distinct instance while repeated ticks in that same
        cycle return ``None``.  This keeps an old worker status from being
        interpreted as success for fresh input.
        """

        normalized_cycle = _safe_metadata(cycle_key, "cycle_key")
        if normalized_cycle is None:
            raise ValueError("cycle key is required")
        job_id = _cycle_job_id(command, normalized_cycle)
        existing = self._jobs.get(job_id)
        if existing is not None:
            # A deterministic ID can only refer to exactly one internal
            # contract.  Treat a mismatch as corruption rather than replacing
            # state while an owned child may still be running.
            if (
                existing.command != command
                or existing.cycle_key != normalized_cycle
                or existing.input_fingerprint != fingerprint
            ):
                raise ValueError("cycle identity does not match existing job")
            return None
        resolved_stage = _DEFAULT_STAGE[command]
        self._jobs[job_id] = ResearchJob(
            job_id=job_id,
            command=command,
            due_at=_utc(due_at),
            input_fingerprint=fingerprint,
            stage=resolved_stage,
            next_eligible_at=_utc(due_at),
            cycle_key=normalized_cycle,
        )
        self._terminal_job_ids.discard(job_id)
        return job_id

    def _launch_job(self, job: ResearchJob) -> Any:
        if self._launcher is not None:
            return self._launcher(job.command)
        return _default_launcher(
            job.command,
            job_id=job.job_id,
            repo_root=self._repo_root,
            storage_policy=self.storage_policy,
            network_enabled=self.network_enabled,
        )

    def _collect_finished_children(self) -> None:
        for job_id, child in tuple(self._children.items()):
            if self._child_running(child):
                continue
            job = self._jobs.get(job_id)
            if job is None:
                continue
            exit_code = self._child_exit_code(child)
            status = self._read_child_status(job)
            digest = _safe_status_digest(status.get("artifact_digest"))
            status_value = str(status.get("status", "")).upper()
            success = (
                exit_code == 0
                and status_value == "SUCCESS"
                and digest is not None
                and self._status_has_verified_artifact(job, status, digest)
            )
            reason = _safe_reason(status.get("reason_code"))
            if not success and reason is None:
                reason = "STATUS_MISSING" if not status else "WORKER_FAILED"
            self._jobs[job_id] = replace(
                job,
                completed=success,
                artifact_digest=digest or job.artifact_digest,
                process_exit_code=exit_code,
                failure_reason=None if success else reason,
            )
            self._terminal_job_ids.add(job_id)
            # Terminal children have already exited.  Releasing the process
            # wrapper makes bounded lifecycle retention effective while the
            # immutable job record remains in the checkpoint.
            self._children.pop(job_id, None)
        self._prune_terminal_history()

    def _read_child_status(self, job: ResearchJob) -> dict[str, Any]:
        path = self.status_path_for(job.job_id)
        try:
            self._revalidate(path)
            raw = path.read_bytes()
            if not raw or len(raw) > _MAX_STATUS_BYTES:
                return {}
            payload = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("job_id") != job.job_id
                or payload.get("job") != job.command[1]
            ):
                return {}
            return payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            return {}

    def _status_has_verified_artifact(
        self, job: ResearchJob, status: dict[str, Any], digest: str
    ) -> bool:
        """Verify the child bound success to its own immutable evidence file."""

        expected_relpath = f"evidence/{job.job_id}.json"
        if status.get("artifact_relpath") != expected_relpath:
            return False
        try:
            artifact = self._revalidate(self.root / expected_relpath)
            raw = artifact.read_bytes()
        except (OSError, ValueError):
            return False
        actual = hashlib.sha256(raw).hexdigest()
        return actual == digest.removeprefix("sha256:")

    def _prune_terminal_history(self) -> None:
        """Bound completed/failed instance retention without touching children."""

        terminal = [
            job_id
            for job_id in self._jobs
            if job_id in self._terminal_job_ids and job_id not in self._children
        ]
        removable = terminal[:-_MAX_RETAINED_TERMINAL_JOBS]
        for job_id in removable:
            self._jobs.pop(job_id, None)
            self._terminal_job_ids.discard(job_id)

    def _write_checkpoint(self) -> bool:
        body = {
            "format_version": 4,
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
                    "completed": job.completed,
                    "process_exit_code": job.process_exit_code,
                    "failure_reason": job.failure_reason,
                    "cycle_key": job.cycle_key or None,
                }
                for job in self._jobs.values()
            ],
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        artifact = {**body, "sha256": hashlib.sha256(encoded).hexdigest()}
        payload = json.dumps(artifact, sort_keys=True, indent=2).encode()
        temporary: str | None = None
        try:
            self._prepare_root()
            self._revalidate(self.checkpoint_path)
            descriptor, temporary = tempfile.mkstemp(
                prefix=".research-checkpoint.", dir=self.root
            )
            temporary_path = Path(temporary)
            self._revalidate(temporary_path)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._revalidate(temporary_path)
            self._revalidate(self.checkpoint_path)
            os.replace(temporary_path, self.checkpoint_path)
            self._revalidate(self.checkpoint_path)
            return True
        except (OSError, ValueError):
            if temporary is not None:
                try:
                    temporary_path = Path(temporary)
                    self._revalidate(temporary_path)
                    temporary_path.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            return False

    def _read_checkpoint(self) -> dict[str, Any] | None:
        try:
            self._revalidate(self.checkpoint_path)
            raw = self.checkpoint_path.read_bytes()
            if not raw or len(raw) > _MAX_CHECKPOINT_BYTES:
                return None
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict) or parsed.get("format_version") not in {2, 3, 4}:
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
        """Rebuild runnable state, but never start a child during construction."""

        payload = self._read_checkpoint()
        if payload is None or not isinstance(payload.get("jobs"), list):
            return
        restored: dict[str, ResearchJob] = {}
        history_session: str | None = None
        screen_fingerprint: str | None = None
        for item in payload["jobs"]:
            if not isinstance(item, dict) or not self._valid_checkpoint_job(item):
                return
            job_id = str(item["job_id"])
            if job_id in restored:
                return
            command = tuple(item["command"])
            try:
                job = ResearchJob(
                    job_id=job_id,
                    command=command,
                    due_at=_utc(datetime.fromisoformat(item["due_at"])),
                    input_fingerprint=_safe_metadata(
                        item.get("input_fingerprint"), "input_fingerprint"
                    ),
                    stage=str(item["stage"]),
                    artifact_digest=_safe_metadata(
                        item.get("artifact_digest"), "artifact_digest"
                    ),
                    next_eligible_at=_utc(
                        datetime.fromisoformat(item["next_eligible_at"])
                    ),
                    completed=bool(item.get("completed", False)),
                    process_exit_code=item.get("process_exit_code"),
                    failure_reason=_safe_reason(item.get("failure_reason")),
                    cycle_key=_safe_metadata(item.get("cycle_key"), "cycle_key")
                    or _legacy_cycle_key(command, item),
                )
            except (TypeError, ValueError):
                return
            restored[job_id] = job
            if job.completed or job.process_exit_code is not None:
                self._terminal_job_ids.add(job_id)
            if command == ("research", "screen"):
                screen_fingerprint = job.input_fingerprint
            elif command == ("research", "history"):
                history_session = job.due_at.date().isoformat()
        self._jobs = restored
        self._last_history_session = history_session
        self._last_screen_fingerprint = screen_fingerprint

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
            try:
                _safe_metadata(item.get(key), key)
            except ValueError:
                return False
        try:
            cycle_key = _safe_metadata(item.get("cycle_key"), "cycle_key")
        except ValueError:
            return False
        if item.get("cycle_key") is not None and cycle_key is None:
            return False
        completed_digest = item.get("completed_artifact_digest")
        try:
            _safe_metadata(completed_digest, "completed_artifact_digest")
        except ValueError:
            return False
        if (
            completed_digest is not None
            and completed_digest != item.get("artifact_digest")
        ):
            return False
        if "completed" in item and not isinstance(item["completed"], bool):
            return False
        if item.get("completed", False) and _safe_status_digest(
            item.get("artifact_digest")
        ) is None:
            return False
        code = item.get("process_exit_code")
        if code is not None and (not isinstance(code, int) or isinstance(code, bool)):
            return False
        if _safe_reason(item.get("failure_reason")) != item.get("failure_reason"):
            return False
        return True

    def _prepare_root(self) -> None:
        self._revalidate(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._revalidate(self.root)

    def _revalidate(self, value: str | Path) -> Path:
        if self.storage_policy is not None:
            return self.storage_policy.revalidate(value)
        return Path(value).resolve()

    @staticmethod
    def _child_running(child: Any) -> bool:
        try:
            return bool(child.is_running())
        except AttributeError:
            return _child_return_code(child) is None

    @staticmethod
    def _child_exit_code(child: Any) -> int | None:
        return _child_return_code(child)


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


def _safe_status_digest(value: Any) -> str | None:
    try:
        normalized = _safe_metadata(value, "artifact_digest")
    except ValueError:
        return None
    if normalized is None:
        return None
    raw = normalized.removeprefix("sha256:")
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        return None
    return normalized


def _safe_reason(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper()
    if (
        not normalized
        or len(normalized) > 96
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in normalized)
    ):
        return None
    return normalized


def _unsafe_argument(value: str) -> bool:
    return any(token in value for token in ("..", "/", "\\", "--"))


def _cycle_job_id(command: tuple[str, ...], cycle_key: str) -> str:
    """Return a stable, non-user-path identity for one lifecycle cycle."""

    if command not in _ALLOWED_COMMANDS:
        raise ValueError("job is not allowlisted")
    encoded = json.dumps(
        {"command": list(command), "cycle_key": cycle_key},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{command[1]}-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _legacy_cycle_key(command: tuple[str, ...], item: dict[str, Any]) -> str:
    """Give pre-v4 checkpoint records a fixed audit identity on restore."""

    fingerprint = str(item.get("input_fingerprint") or "no-fingerprint")
    due = str(item.get("due_at") or "unknown")
    return f"legacy:{command[1]}:{fingerprint}:{due}"


def _child_return_code(child: Any) -> int | None:
    try:
        poll = getattr(child, "poll", None)
        if callable(poll):
            value = poll()
        else:
            process = getattr(child, "process", None)
            value = process.poll() if process is not None else None
        if value is None:
            return None
        return int(value)
    except (AttributeError, TypeError, ValueError):
        return None


def _default_launcher(
    command: tuple[str, ...],
    *,
    job_id: str,
    repo_root: Path,
    storage_policy: ProjectStoragePolicy | None,
    network_enabled: bool = True,
) -> Any:
    if command not in _ALLOWED_COMMANDS:
        raise ValueError("job is not allowlisted")
    policy = storage_policy or ProjectStoragePolicy(repo_root)
    safe_root = policy.revalidate(policy.repo_root)
    environment = policy.child_environment(os.environ)
    environment["A_SHARE_QUANT_RESEARCH_WORKBENCH"] = "1"
    environment["A_SHARE_QUANT_RESEARCH_NETWORK"] = "1" if network_enabled else "0"
    environment["A_SHARE_QUANT_RESEARCH_JOB_ID"] = job_id
    if command == ("research", "history"):
        environment["A_SHARE_QUANT_RESEARCH_HISTORY_READY"] = "1"
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

    def poll(self) -> int | None:
        return self.process.poll()

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()

    def wait(self, timeout: float | None = None) -> None:
        self.process.wait(timeout=timeout)


__all__ = ["ResearchJob", "ResearchJobSupervisor", "ShutdownResult"]
