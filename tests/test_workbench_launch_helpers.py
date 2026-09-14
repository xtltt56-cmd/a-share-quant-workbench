from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "scripts" / "workbench_launch_helpers.ps1"


def _legacy_powershell() -> str:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


def _shell(executable: str, expression: str) -> str:
    helper_path = str(HELPERS).replace("'", "''")
    completed = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f". '{helper_path}'; {expression}",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _powershell(expression: str) -> str:
    return _shell(_legacy_powershell(), expression)


def _decision(**overrides: object) -> str:
    values = {
        "metadata_pid": 42,
        "actual_pid": 42,
        "metadata_mode": "network",
        "requested_mode": "network",
        "metadata_revision": "abc",
        "requested_revision": "abc",
        "metadata_fingerprint": "sha256:one",
        "requested_fingerprint": "sha256:one",
    }
    values.update(overrides)
    metadata = {
        "format_version": 1,
        "pid": values["metadata_pid"],
        "mode": values["metadata_mode"],
        "git_revision": values["metadata_revision"],
        "code_fingerprint": values["metadata_fingerprint"],
        "started_at": "2026-08-11T16:00:00+08:00",
    }
    payload = json.dumps(metadata, separators=(",", ":")).replace("'", "''")
    return _powershell(
        f"$metadata = '{payload}' | ConvertFrom-Json; "
        "Get-QuantLaunchDecision "
        f"-Metadata $metadata -ActualPid {values['actual_pid']} "
        f"-RequestedMode '{values['requested_mode']}' "
        f"-RequestedGitRevision '{values['requested_revision']}' "
        f"-RequestedCodeFingerprint '{values['requested_fingerprint']}'"
    )


def test_launch_decision_reuses_only_identical_runtime() -> None:
    assert _decision() == "REUSE"
    assert _decision(requested_mode="offline") == "RESTART_MODE_MISMATCH"
    assert _decision(requested_revision="def") == "RESTART_REVISION_MISMATCH"
    assert _decision(requested_fingerprint="sha256:two") == "RESTART_CODE_MISMATCH"
    assert _decision(actual_pid=43) == "RESTART_PID_MISMATCH"


def test_launch_decision_restarts_without_valid_metadata() -> None:
    assert (
        _powershell(
            "Get-QuantLaunchDecision -Metadata $null -ActualPid 42 "
            "-RequestedMode 'network' -RequestedGitRevision 'abc' "
            "-RequestedCodeFingerprint 'sha256:one'"
        )
        == "RESTART_MISSING_METADATA"
    )


def test_identity_requires_this_repository_workbench() -> None:
    expected = (
        f'"{ROOT / ".venv" / "Scripts" / "python.exe"}" -X utf8 '
        f'"{ROOT / "scripts" / "quant_cli.py"}" workbench --network'
    ).replace("'", "''")
    unrelated = '"C:\\Python312\\python.exe" other.py workbench --network'
    root = str(ROOT).replace("'", "''")
    assert (
        _powershell(
            f"Test-QuantWorkbenchCommandLine -CommandLine '{expected}' "
            f"-RepoRoot '{root}'"
        )
        == "True"
    )
    assert (
        _powershell(
            f"Test-QuantWorkbenchCommandLine -CommandLine '{unrelated}' "
            f"-RepoRoot '{root}'"
        )
        == "False"
    )


def test_python_resolution_prefers_development_and_supports_release_runtime(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    development = root / ".venv" / "Scripts" / "python.exe"
    release = root / "runtime" / "python.exe"
    development.parent.mkdir(parents=True)
    release.parent.mkdir(parents=True)
    development.write_bytes(b"development")
    release.write_bytes(b"release")
    escaped = str(root).replace("'", "''")

    assert _powershell(f"Get-QuantPythonPath -RepoRoot '{escaped}'") == str(development)
    development.unlink()
    assert _powershell(f"Get-QuantPythonPath -RepoRoot '{escaped}'") == str(release)


def test_fingerprint_is_stable_and_metadata_round_trips(tmp_path: Path) -> None:
    root = str(ROOT).replace("'", "''")
    first = _powershell(f"Get-QuantCodeFingerprint -RepoRoot '{root}'")
    second = _powershell(f"Get-QuantCodeFingerprint -RepoRoot '{root}'")
    assert first == second
    assert first.startswith("sha256:")
    assert len(first) == 71

    metadata_path = str(tmp_path / "launch.json").replace("'", "''")
    expression = (
        f"Write-QuantLaunchMetadata -Path '{metadata_path}' -ProcessId 42 "
        "-Mode 'network' -GitRevision 'abc' -CodeFingerprint 'sha256:one'; "
        f"Read-QuantLaunchMetadata -Path '{metadata_path}' | ConvertTo-Json -Compress"
    )
    payload = json.loads(_powershell(expression))
    assert payload["format_version"] == 1
    assert payload["pid"] == 42
    assert payload["mode"] == "network"
    assert payload["git_revision"] == "abc"
    assert payload["code_fingerprint"] == "sha256:one"


def test_fingerprint_is_identical_across_supported_powershell_hosts() -> None:
    expression = "Get-QuantCodeFingerprint -RepoRoot (Get-Location).Path"

    legacy = _shell("powershell.exe", expression)
    modern = _shell("pwsh.exe", expression)

    assert legacy == modern


def test_fingerprint_changes_when_frontend_asset_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    static_dir = repo / "src" / "package" / "static"
    static_dir.mkdir(parents=True)
    (repo / "scripts").mkdir()
    stylesheet = static_dir / "workbench.css"
    stylesheet.write_text(":root { color: #111; }\n", encoding="utf-8")

    command = [
        sys.executable,
        str(ROOT / "scripts" / "workbench_code_fingerprint.py"),
        str(repo),
    ]
    first = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    stylesheet.write_text(":root { color: #222; }\n", encoding="utf-8")
    second = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()

    assert first.startswith("sha256:")
    assert second.startswith("sha256:")
    assert first != second
