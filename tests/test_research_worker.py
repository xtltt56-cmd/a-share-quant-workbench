from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from a_share_quant.runtime import research_worker
from a_share_quant.storage.project_storage import ProjectStoragePolicy


@pytest.fixture
def d_worker_root() -> Path:
    workspace = Path(__file__).resolve().parents[1]
    assert workspace.drive.casefold() == "d:"
    parent = workspace / ".runtime" / "temp"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / f"task8-research-worker-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        lexical = Path(os.path.normpath(os.path.abspath(root)))
        assert lexical.parent == parent.resolve()
        if lexical.exists():
            shutil.rmtree(lexical)


def test_worker_parser_allows_only_fixed_internal_jobs() -> None:
    assert research_worker.parse_args(["history"]).job == "history"
    assert research_worker.parse_args(["screen"]).job == "screen"
    assert research_worker.parse_args(["predict"]).job == "predict"
    assert research_worker.parse_args(["settle"]).job == "settle"

    with pytest.raises(SystemExit):
        research_worker.parse_args(["forecast"])
    with pytest.raises(SystemExit):
        research_worker.parse_args(["history", "--output", "C:\\outside.json"])


def test_worker_status_is_project_local_and_screen_is_non_promotional(tmp_path) -> None:
    exit_code = research_worker.run_job("screen", tmp_path)

    assert exit_code != 0  # no verified research dataset is a blocked screen
    path = tmp_path / ".runtime" / "research" / "screen-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["promotion"] == "NEVER"
    assert Path(payload["status_path"]).resolve() == path.resolve()
    assert Path(payload["status_path"]).drive == path.drive


@pytest.mark.parametrize(
    ("job", "expected_status"),
    [
        ("history", "BLOCKED"),
        ("predict", "BLOCKED"),
        ("settle", "BLOCKED"),
    ],
)
def test_worker_commands_fail_closed_without_verified_inputs(
    tmp_path, job: str, expected_status: str
) -> None:
    exit_code = research_worker.run_job(job, tmp_path)

    assert exit_code != 0
    path = tmp_path / ".runtime" / "research" / f"{job}-status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == expected_status
    assert payload["promotion"] == "NEVER"


def test_worker_main_does_not_accept_paths_or_flags(tmp_path) -> None:
    with pytest.raises(SystemExit):
        research_worker.main(["history", "--repo-root", str(tmp_path)])


@pytest.mark.parametrize("job", ["history", "screen", "predict", "settle"])
def test_task8_worker_dispatches_to_the_fixed_coordinator_when_verified(
    d_worker_root: Path, monkeypatch, job: str
) -> None:
    calls: list[tuple[str, Path]] = []
    policy = ProjectStoragePolicy(d_worker_root)

    monkeypatch.setattr(
        research_worker,
        "_verified_job_context",
        lambda _job, _policy: {"verified": True},
        raising=False,
    )
    monkeypatch.setattr(
        research_worker,
        f"_run_{job}",
        lambda _root, _policy, _context: calls.append((job, _root))
        or {"artifact_digest": "b" * 64},
        raising=False,
    )

    assert research_worker.run_job(job, d_worker_root, storage_policy=policy) == 0
    assert calls == [(job, d_worker_root.resolve())]
    payload = json.loads(
        (d_worker_root / ".runtime" / "research" / f"{job}-status.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["status"] == "SUCCESS"
    assert payload["promotion"] == "NEVER"
    assert payload["artifact_digest"] == "b" * 64
