"""Project-wide test safety defaults for the Windows D-drive workspace."""

from __future__ import annotations

from pathlib import Path


def pytest_configure(config) -> None:
    """Keep pytest's implicit temporary tree off the system drive."""

    if config.option.basetemp:
        return
    workspace = Path(__file__).resolve().parents[1]
    if workspace.drive.casefold() != "d:":
        raise RuntimeError("tests must run from the governed D-drive worktree")
    base = workspace / ".runtime" / "temp" / "pytest-default"
    base.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(base)
