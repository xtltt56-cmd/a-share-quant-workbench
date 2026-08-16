from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from a_share_quant.runtime.research_jobs import ResearchJobSupervisor
from a_share_quant.storage.project_storage import ProjectStoragePolicy


@pytest.fixture
def d_research_root() -> Path:
    """Keep new lifecycle evidence on the governed D-drive test area."""

    workspace = Path(__file__).resolve().parents[1]
    assert workspace.drive.casefold() == "d:"
    parent = workspace / ".runtime" / "temp"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / f"task8-research-supervisor-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        lexical = Path(os.path.normpath(os.path.abspath(root)))
        assert lexical.parent == parent.resolve()
        if lexical.exists():
            shutil.rmtree(lexical)


class FakeChild:
    def __init__(self) -> None:
        self.running = True
        self.terminated = False

    def is_running(self) -> bool:
        return self.running

    def terminate(self) -> None:
        self.terminated = True
        self.running = False

    def wait(self, timeout: float | None = None) -> None:
        self.running = False

    def kill(self) -> None:
        self.running = False

    def poll(self):
        return None if self.running else 0


class FakeLauncher:
    def __init__(self) -> None:
        self.children: list[FakeChild] = []
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: tuple[str, ...]) -> FakeChild:
        child = FakeChild()
        self.children.append(child)
        self.commands.append(command)
        return child


def test_shutdown_checkpoints_and_stops_owned_child(tmp_path) -> None:
    launcher = FakeLauncher()
    supervisor = ResearchJobSupervisor(tmp_path, launcher=launcher)
    supervisor.register_job(
        "history-backfill",
        ("research", "history"),
        due_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    started = supervisor.start_due_jobs(now=datetime(2026, 8, 2, tzinfo=timezone.utc))
    result = supervisor.shutdown(timeout_seconds=5)

    assert len(started) == 1
    assert result.checkpoint_saved is True
    assert result.children_stopped is True
    assert launcher.children[0].terminated is True
    assert not launcher.children[0].is_running()


def test_corrupt_checkpoint_is_not_resumed(tmp_path) -> None:
    (tmp_path / "research-checkpoint.json").write_text("not-json", encoding="utf-8")

    assert ResearchJobSupervisor(tmp_path).resume_eligible_jobs() == ()


def test_checkpoint_rejects_unknown_commands_and_path_like_arguments(tmp_path) -> None:
    supervisor = ResearchJobSupervisor(tmp_path, launcher=FakeLauncher())

    supervisor.register_job(
        "safe",
        ("research", "history"),
        due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    supervisor.start_due_jobs(now=datetime.now(timezone.utc))
    assert supervisor.shutdown().checkpoint_saved is True

    checkpoint = tmp_path / "research-checkpoint.json"
    text = checkpoint.read_text(encoding="utf-8").replace("history", "..\\unsafe")
    checkpoint.write_text(text, encoding="utf-8")
    assert ResearchJobSupervisor(tmp_path).resume_eligible_jobs() == ()


def test_register_rejects_legacy_commands_and_user_supplied_flags(tmp_path) -> None:
    supervisor = ResearchJobSupervisor(tmp_path, launcher=FakeLauncher())

    with pytest.raises(ValueError, match="allowlisted"):
        supervisor.register_job(
            "legacy",
            ("research", "forecast"),
            due_at=datetime.now(timezone.utc),
        )
    with pytest.raises(ValueError, match="allowlisted"):
        supervisor.register_job(
            "injected",
            ("research", "history", "--output", "C:\\bad"),
            due_at=datetime.now(timezone.utc),
        )


def test_default_schedule_respects_session_fingerprint_and_refresh_boundaries(tmp_path) -> None:
    supervisor = ResearchJobSupervisor(tmp_path, launcher=FakeLauncher())
    now = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)

    jobs = supervisor.register_default_jobs(
        now=now,
        session_completed=True,
        data_fingerprint="dataset-a",
        data_refreshed=True,
        outcome_cutoff=now + timedelta(days=5),
    )

    assert {job_id.split("-", maxsplit=1)[0] for job_id in jobs} == {
        "history",
        "screen",
        "predict",
        "settle",
    }
    assert supervisor.register_default_jobs(
        now=now + timedelta(minutes=1),
        session_completed=True,
        data_fingerprint="dataset-a",
        data_refreshed=False,
        outcome_cutoff=now + timedelta(days=5),
    ) == ()

    supervisor2 = ResearchJobSupervisor(tmp_path / "second", launcher=FakeLauncher())
    assert supervisor2.register_default_jobs(
        now=now,
        session_completed=False,
        data_fingerprint="dataset-a",
        data_refreshed=False,
        outcome_cutoff=now + timedelta(days=5),
    )
    assert {
        job_id.split("-", maxsplit=1)[0]
        for job_id in supervisor2.resume_eligible_jobs()
    } == {"screen", "predict"}

    next_day = supervisor.register_default_jobs(
        now=now + timedelta(days=1),
        session_completed=True,
        data_fingerprint="dataset-a",
        data_refreshed=False,
        outcome_cutoff=now + timedelta(days=5),
    )
    assert len(next_day) == 1
    assert next_day[0].startswith("history-")


def test_start_due_jobs_limits_one_cpu_and_one_network_child(tmp_path) -> None:
    launcher = FakeLauncher()
    supervisor = ResearchJobSupervisor(tmp_path, launcher=launcher)
    now = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
    supervisor.register_default_jobs(
        now=now,
        session_completed=True,
        data_fingerprint="dataset-a",
        data_refreshed=True,
        outcome_cutoff=now + timedelta(days=5),
    )

    started = supervisor.start_due_jobs(now=now)

    assert {job_id.split("-", maxsplit=1)[0] for job_id in started} == {
        "history",
        "screen",
    }
    assert len(launcher.children) == 2


def test_checkpoint_contains_lifecycle_evidence(tmp_path) -> None:
    supervisor = ResearchJobSupervisor(tmp_path, launcher=FakeLauncher())
    now = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)
    supervisor.register_job(
        "screen",
        ("research", "screen"),
        due_at=now,
        input_fingerprint="dataset-a",
        stage="engineering-screen",
        artifact_digest="sha256:artifact-a",
        next_eligible_at=now + timedelta(days=1),
    )
    supervisor.start_due_jobs(now=now)
    assert supervisor.shutdown().checkpoint_saved is True

    payload = __import__("json").loads(
        (tmp_path / "research-checkpoint.json").read_text(encoding="utf-8")
    )
    item = payload["jobs"][0]
    assert item["command"] == ["research", "screen"]
    assert item["input_fingerprint"] == "dataset-a"
    assert item["stage"] == "engineering-screen"
    assert item["artifact_digest"] == "sha256:artifact-a"
    assert item["next_eligible_at"].startswith("2026-08-15")


def test_default_launcher_runs_allowlisted_worker_and_shutdown_owns_it(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, object] = {}
    child = FakeChild()

    def popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return child

    monkeypatch.setattr("a_share_quant.runtime.research_jobs.subprocess.Popen", popen)
    repo_root = tmp_path
    policy = ProjectStoragePolicy(repo_root, required_drive=None)
    supervisor = ResearchJobSupervisor(
        repo_root / ".runtime" / "research", storage_policy=policy
    )
    supervisor.register_job(
        "forecast-on-launch",
        ("research", "history"),
        due_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    supervisor.start_due_jobs(now=datetime.now(timezone.utc))
    result = supervisor.shutdown()

    assert captured["argv"][1:4] == ["-m", "a_share_quant.runtime.research_worker", "history"]
    assert captured["kwargs"]["cwd"] == str(repo_root)
    assert Path(captured["kwargs"]["env"]["TEMP"]).is_relative_to(repo_root)
    assert result.children_stopped is True


def test_task8_offline_supervisor_never_starts_network_jobs(
    d_research_root: Path,
) -> None:
    launcher = FakeLauncher()
    supervisor = ResearchJobSupervisor(
        d_research_root,
        launcher=launcher,
        storage_policy=ProjectStoragePolicy(d_research_root),
        network_enabled=False,
    )
    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    supervisor.register_default_jobs(
        now=now,
        session_completed=True,
        data_fingerprint="verified-dataset-a",
        data_refreshed=True,
        outcome_cutoff=now + timedelta(days=1),
    )

    started = supervisor.start_due_jobs(now=now)
    assert len(started) == 1
    assert started[0].startswith("screen-")
    assert [child for child in launcher.children] and len(launcher.children) == 1


def test_task8_checkpoint_rebuilds_runnable_jobs_without_duplication(
    d_research_root: Path,
) -> None:
    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    first = ResearchJobSupervisor(
        d_research_root,
        launcher=FakeLauncher(),
        storage_policy=ProjectStoragePolicy(d_research_root),
    )
    first.register_job("screen", ("research", "screen"), due_at=now)
    assert first.shutdown().checkpoint_saved is True

    launcher = FakeLauncher()
    restored = ResearchJobSupervisor(
        d_research_root,
        launcher=launcher,
        storage_policy=ProjectStoragePolicy(d_research_root),
    )

    assert restored.resume_eligible_jobs() == ("screen",)
    assert restored.resume_eligible_jobs() == ("screen",)
    assert restored.start_due_jobs(now=now) == ("screen",)
    assert len(launcher.children) == 1


def test_task8_successful_child_is_not_relaunched_and_result_is_checkpointed(
    d_research_root: Path,
) -> None:
    launcher = FakeLauncher()
    policy = ProjectStoragePolicy(d_research_root)
    supervisor = ResearchJobSupervisor(
        d_research_root,
        launcher=launcher,
        storage_policy=policy,
    )
    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    supervisor.register_job("screen", ("research", "screen"), due_at=now)
    assert supervisor.start_due_jobs(now=now) == ("screen",)
    _write_instance_success(supervisor, "screen", {"legacy": "persisted"})
    launcher.children[0].running = False

    assert supervisor.start_due_jobs(now=now + timedelta(minutes=1)) == ()
    assert supervisor.checkpoint_path.exists()
    assert len(launcher.children) == 1
    assert supervisor.shutdown().checkpoint_saved is True
    checkpoint = json.loads((d_research_root / "research-checkpoint.json").read_text())
    assert checkpoint["jobs"][0]["completed"] is True
    assert checkpoint["jobs"][0]["process_exit_code"] == 0
    evidence = {
        "format_version": 1,
        "job": "screen",
        "job_id": "screen",
        "result": {"legacy": "persisted"},
    }
    assert checkpoint["jobs"][0]["artifact_digest"] == hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def test_task8_restore_rejects_tampered_completed_evidence(
    d_research_root: Path,
) -> None:
    """A completed checkpoint cannot silently survive evidence tampering."""

    launcher = FakeLauncher()
    policy = ProjectStoragePolicy(d_research_root)
    supervisor = ResearchJobSupervisor(
        d_research_root, launcher=launcher, storage_policy=policy
    )
    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    supervisor.register_job("screen", ("research", "screen"), due_at=now)
    assert supervisor.start_due_jobs(now=now) == ("screen",)
    _write_instance_success(supervisor, "screen", {"stable": "evidence"})
    launcher.children[0].running = False
    assert supervisor.start_due_jobs(now=now + timedelta(minutes=1)) == ()
    evidence = d_research_root / "evidence" / "screen.json"
    evidence.write_text('{"stable":"tampered"}', encoding="utf-8")

    restored = ResearchJobSupervisor(
        d_research_root, launcher=FakeLauncher(), storage_policy=policy
    )

    assert restored._jobs["screen"].completed is False
    assert restored._jobs["screen"].failure_reason == "EVIDENCE_INTEGRITY_FAILURE"


def _write_instance_success(
    supervisor: ResearchJobSupervisor, job_id: str, body: dict[str, object]
) -> None:
    """Create the durable artifact that a worker success status must bind to."""

    artifact = supervisor.root / "evidence" / f"{job_id}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        {
            "format_version": 1,
            "job": job_id.split("-", maxsplit=1)[0],
            "job_id": job_id,
            "result": body,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    artifact.write_bytes(encoded)
    status = {
        "format_version": 3,
        "job_id": job_id,
        "job": job_id.split("-", maxsplit=1)[0],
        "status": "SUCCESS",
        "reason_code": None,
        "artifact_relpath": artifact.relative_to(supervisor.root).as_posix(),
        "artifact_digest": hashlib.sha256(encoded).hexdigest(),
    }
    path = supervisor.status_path_for(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status), encoding="utf-8")


def test_task8_periodic_instances_ignore_static_stale_status_and_retry_next_cycle(
    d_research_root: Path,
) -> None:
    """A new input cycle must never inherit a prior static stage status."""

    launcher = FakeLauncher()
    supervisor = ResearchJobSupervisor(
        d_research_root,
        launcher=launcher,
        storage_policy=ProjectStoragePolicy(d_research_root),
    )
    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)

    first = supervisor.register_default_jobs(
        now=now,
        data_fingerprint="verified-dataset-a",
    )
    assert len(first) == 1
    first_id = first[0]
    assert supervisor.start_due_jobs(now=now) == (first_id,)
    _write_instance_success(supervisor, first_id, {"cycle": "a"})
    launcher.children[0].running = False
    assert supervisor.start_due_jobs(now=now + timedelta(minutes=1)) == ()

    second = supervisor.register_default_jobs(
        now=now + timedelta(minutes=2),
        data_fingerprint="verified-dataset-b",
    )
    assert len(second) == 1
    second_id = second[0]
    assert second_id != first_id
    assert supervisor.start_due_jobs(now=now + timedelta(minutes=2)) == (second_id,)

    # This is the old shared stage filename. It must not complete a different
    # per-cycle job instance.
    (d_research_root / "screen-status.json").write_text(
        json.dumps({"status": "SUCCESS", "artifact_digest": "f" * 64}),
        encoding="utf-8",
    )
    launcher.children[1].running = False
    assert supervisor.start_due_jobs(now=now + timedelta(minutes=3)) == ()
    assert second_id not in supervisor.resume_eligible_jobs()

    third = supervisor.register_default_jobs(
        now=now + timedelta(days=1),
        data_fingerprint="verified-dataset-c",
    )
    assert len(third) == 1
    assert third[0] not in {first_id, second_id}


def test_task8_next_tick_starts_queued_cpu_job_after_prior_cpu_exits(
    d_research_root: Path,
) -> None:
    """The next lifecycle tick drains a queued CPU job without a busy loop."""

    launcher = FakeLauncher()
    supervisor = ResearchJobSupervisor(
        d_research_root,
        launcher=launcher,
        storage_policy=ProjectStoragePolicy(d_research_root),
    )
    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    jobs = supervisor.register_default_jobs(
        now=now,
        session_completed=True,
        data_fingerprint="verified-dataset-a",
        data_refreshed=True,
        outcome_cutoff=now + timedelta(days=1),
    )
    started = supervisor.start_due_jobs(now=now)
    screen_id = next(job_id for job_id in started if job_id.startswith("screen-"))
    screen_child = launcher.children[launcher.commands.index(("research", "screen"))]
    _write_instance_success(supervisor, screen_id, {"screen": "persisted"})
    screen_child.running = False

    next_started = supervisor.start_due_jobs(now=now + timedelta(minutes=1))

    assert next(job_id for job_id in jobs if job_id.startswith("predict-")) in next_started
    assert any(command == ("research", "history") for command in launcher.commands)


def test_task8_restored_terminal_failure_is_not_resume_eligible_but_next_cycle_retries(
    d_research_root: Path,
) -> None:
    """A failed instance is terminal; fresh verified input creates its retry."""

    now = datetime(2026, 8, 14, 8, tzinfo=timezone.utc)
    first_launcher = FakeLauncher()
    first = ResearchJobSupervisor(
        d_research_root,
        launcher=first_launcher,
        storage_policy=ProjectStoragePolicy(d_research_root),
    )
    original = first.register_default_jobs(
        now=now, data_fingerprint="verified-dataset-a"
    )
    original_id = original[0]
    assert first.start_due_jobs(now=now) == (original_id,)
    # No instance status is emitted: the owned child has a terminal failure.
    first_launcher.children[0].running = False
    assert first.start_due_jobs(now=now + timedelta(minutes=1)) == ()
    assert first.shutdown().checkpoint_saved is True

    retry_launcher = FakeLauncher()
    restored = ResearchJobSupervisor(
        d_research_root,
        launcher=retry_launcher,
        storage_policy=ProjectStoragePolicy(d_research_root),
    )

    assert original_id not in restored.resume_eligible_jobs()
    assert restored.start_due_jobs(now=now + timedelta(minutes=2)) == ()
    assert restored.register_default_jobs(
        now=now + timedelta(minutes=3), data_fingerprint="verified-dataset-a"
    ) == ()
    retry = restored.register_default_jobs(
        now=now + timedelta(minutes=4), data_fingerprint="verified-dataset-b"
    )
    assert len(retry) == 1
    assert retry[0] != original_id
    assert restored.start_due_jobs(now=now + timedelta(minutes=4)) == retry
