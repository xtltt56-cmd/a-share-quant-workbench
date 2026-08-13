"""Short-lived, allowlisted research jobs owned by the workbench process."""

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

_ALLOWED_COMMANDS = frozenset({
    ("research", "forecast"),
    ("research", "calibrate"),
    ("research", "evaluate"),
})


@dataclass(frozen=True)
class ResearchJob:
    job_id: str
    command: tuple[str, ...]
    due_at: datetime


@dataclass(frozen=True)
class ShutdownResult:
    checkpoint_saved: bool
    children_stopped: bool


class ResearchJobSupervisor:
    """Manage only registered children and fail closed on checkpoint errors."""

    def __init__(
        self,
        root: str | Path,
        *,
        launcher: Callable[[tuple[str, ...]], Any] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.root / "research-checkpoint.json"
        self._launcher = launcher or (
            lambda command: _default_launcher(command, repo_root=self.root.parents[1])
        )
        self._jobs: dict[str, ResearchJob] = {}
        self._children: dict[str, Any] = {}
        self._accepting = True

    def register_job(self, job_id: str, command: tuple[str, ...], *, due_at: datetime) -> None:
        normalized = tuple(str(item).strip() for item in command)
        if not str(job_id).strip() or normalized not in _ALLOWED_COMMANDS:
            raise ValueError("job is not allowlisted")
        due = _utc(due_at)
        self._jobs[str(job_id).strip()] = ResearchJob(str(job_id).strip(), normalized, due)

    def start_due_jobs(self, *, now: datetime) -> tuple[str, ...]:
        if not self._accepting:
            return ()
        current = _utc(now)
        started: list[str] = []
        for job in self._jobs.values():
            if job.job_id in self._children or job.due_at > current:
                continue
            self._children[job.job_id] = self._launcher(job.command)
            started.append(job.job_id)
        return tuple(started)

    def shutdown(self, *, timeout_seconds: float = 5.0) -> ShutdownResult:
        self._accepting = False
        checkpoint_saved = self._write_checkpoint()
        stopped = True
        for child in tuple(self._children.values()):
            try:
                if child.is_running():
                    child.terminate()
                    child.wait(timeout=timeout_seconds)
                if child.is_running():
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
            if not isinstance(item, dict):
                return ()
            job_id = item.get("job_id")
            command = item.get("command")
            if (
                not isinstance(job_id, str)
                or not isinstance(command, list)
                or tuple(command) not in _ALLOWED_COMMANDS
                or any(_unsafe_argument(str(part)) for part in command)
            ):
                return ()
            eligible.append(job_id)
        return tuple(eligible)

    def _write_checkpoint(self) -> bool:
        body = {
            "format_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "jobs": [
                {
                    "job_id": job.job_id,
                    "command": list(job.command),
                    "due_at": job.due_at.isoformat(),
                }
                for job in self._jobs.values()
            ],
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        artifact = {**body, "sha256": hashlib.sha256(encoded).hexdigest()}
        payload = json.dumps(artifact, sort_keys=True, indent=2).encode()
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
            if not raw or len(raw) > 1_048_576:
                return None
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("format_version") != 1:
                return None
            digest = payload.pop("sha256", None)
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            if not isinstance(digest, str) or digest != hashlib.sha256(encoded).hexdigest():
                return None
            return payload
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _unsafe_argument(value: str) -> bool:
    return any(token in value for token in ("..", "/", "\\", "--"))


def _default_launcher(command: tuple[str, ...], *, repo_root: Path) -> Any:
    if command not in _ALLOWED_COMMANDS:
        raise ValueError("job is not allowlisted")
    process = subprocess.Popen(
        [sys.executable, "-m", "a_share_quant.runtime.research_worker", command[1]],
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    return _OwnedProcess(process)


class _OwnedProcess:
    def __init__(self, process: Any) -> None:
        self.process = process

    def is_running(self) -> bool:
        return self.process.poll() is None

    def terminate(self) -> None:
        self.process.terminate()

    def wait(self, timeout: float | None = None) -> None:
        self.process.wait(timeout=timeout)


__all__ = ["ResearchJob", "ResearchJobSupervisor", "ShutdownResult"]
