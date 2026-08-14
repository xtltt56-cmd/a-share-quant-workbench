from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from a_share_quant.runtime.research_jobs import ResearchJobSupervisor
from a_share_quant.storage.project_storage import ProjectStoragePolicy


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

    def __call__(self, command: tuple[str, ...]) -> FakeChild:
        child = FakeChild()
        self.children.append(child)
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

    assert jobs == ("history-backfill", "screen", "predict", "settle")
    assert supervisor.register_default_jobs(
        now=now + timedelta(minutes=1),
        session_completed=True,
        data_fingerprint="dataset-a",
        data_refreshed=False,
        outcome_cutoff=now + timedelta(days=5),
    ) == ("predict",)

    supervisor2 = ResearchJobSupervisor(tmp_path / "second", launcher=FakeLauncher())
    assert supervisor2.register_default_jobs(
        now=now,
        session_completed=False,
        data_fingerprint="dataset-a",
        data_refreshed=False,
        outcome_cutoff=now + timedelta(days=5),
    ) == ("screen", "predict")


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

    assert set(started) == {"history-backfill", "screen"}
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
