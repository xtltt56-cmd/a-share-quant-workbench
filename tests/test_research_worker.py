from __future__ import annotations

import json
from pathlib import Path

import pytest

from a_share_quant.runtime import research_worker


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
