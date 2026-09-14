"""Project-wide test safety defaults for the Windows D-drive workspace."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def bypass_system_proxy_for_loopback_http_tests():
    """Keep local HTTP integration tests independent of Windows proxy state."""

    names = ("NO_PROXY", "no_proxy")
    previous = {name: os.environ.get(name) for name in names}
    for name in names:
        entries = [item.strip() for item in (previous[name] or "").split(",") if item.strip()]
        known = {item.casefold() for item in entries}
        for loopback in ("127.0.0.1", "localhost"):
            if loopback not in known:
                entries.append(loopback)
        os.environ[name] = ",".join(entries)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


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
