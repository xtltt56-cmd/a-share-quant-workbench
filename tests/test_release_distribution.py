from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_matches_project_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))

    assert project["project"]["version"] == metadata["version"]
    assert metadata["channel"] == "preview"


def test_release_runtime_excludes_development_dependencies() -> None:
    requirements = (ROOT / "constraints" / "release-py312.txt").read_text(
        encoding="utf-8"
    )

    assert "pytest" not in requirements.casefold()
    assert "ruff" not in requirements.casefold()
    for dependency in ("akshare", "baostock", "pandas", "pyarrow", "lightgbm"):
        assert re.search(rf"(?m)^{dependency}==", requirements, re.IGNORECASE)


def test_launcher_supports_isolated_release_runtime() -> None:
    helper = (ROOT / "scripts" / "workbench_launch_helpers.ps1").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )

    assert "runtime\\python.exe" in helper
    assert ".venv\\Scripts\\python.exe" in helper
    assert "Get-QuantPythonPath" in launcher
    assert "PYTHONPATH = Join-Path $repoRoot 'src'" in launcher


def test_release_builder_does_not_package_private_runtime_data() -> None:
    builder = (ROOT / "scripts" / "build_windows_release.ps1").read_text(
        encoding="utf-8"
    )

    payload_match = re.search(r"\$payloadItems = @\((.*?)\n\)", builder, re.DOTALL)
    assert payload_match is not None
    payload = payload_match.group(1)
    for forbidden in ("'.env'", "'data'", "'.runtime'", "'models'", "'logs'"):
        assert forbidden not in payload
    for required in ("'src'", "'scripts'", "'config'", "'LICENSE'"):
        assert required in payload


def test_release_workflow_publishes_only_versioned_tags_after_tests() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows-release.yml").read_text(
        encoding="utf-8"
    )

    assert 'tags: ["v*"]' in workflow
    assert "python.exe -m pytest -q" in workflow
    assert "build_windows_release.ps1" in workflow
    assert "gh release create" in workflow
    assert "--latest" in workflow


def test_online_release_can_be_built_without_copying_local_runtime(tmp_path: Path) -> None:
    output = tmp_path / "release"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "build_windows_release.ps1"),
            "-Version",
            "0.2.0-test",
            "-OutputDirectory",
            str(output),
            "-Flavor",
            "Online",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    archives = list(output.glob("*-Online.zip"))
    assert len(archives) == 1
    assert (output / "SHA256SUMS.txt").is_file()
    assert archives[0].stat().st_size < 20 * 1024 * 1024
