from __future__ import annotations

from datetime import datetime, timedelta, timezone

from a_share_quant.runtime.research_jobs import ResearchJobSupervisor


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
        "monthly-forecast",
        ("research", "forecast"),
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
        ("research", "forecast"),
        due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    supervisor.start_due_jobs(now=datetime.now(timezone.utc))
    assert supervisor.shutdown().checkpoint_saved is True

    checkpoint = tmp_path / "research-checkpoint.json"
    text = checkpoint.read_text(encoding="utf-8").replace("research", "..\\unsafe")
    checkpoint.write_text(text, encoding="utf-8")
    assert ResearchJobSupervisor(tmp_path).resume_eligible_jobs() == ()
