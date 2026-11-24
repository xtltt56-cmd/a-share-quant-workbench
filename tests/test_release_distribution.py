from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_matches_project_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    metadata = json.loads((ROOT / "VERSION.json").read_text(encoding="utf-8"))

    assert project["project"]["version"] == metadata["version"]
    assert metadata["channel"] == "preview"
    assert metadata["product_name"] == "股票量化研究工作台"


def test_release_ships_agent_dependencies_without_secrets() -> None:
    release = (ROOT / "constraints" / "release-py312.txt").read_text(encoding="utf-8")
    agent = (ROOT / "constraints" / "agent-py312.txt").read_text(encoding="utf-8")
    assert "-r agent-py312.txt" in release
    for dependency in ("openai", "ollama", "ddgs", "beautifulsoup4"):
        assert re.search(rf"(?m)^{dependency}==", agent, re.IGNORECASE)
    assert "DEEPSEEK_API_KEY=" not in agent


def test_ci_installs_agent_dependencies_for_agent_regression_tests() -> None:
    for filename in ("windows-tests.yml", "windows-release.yml"):
        workflow = (ROOT / ".github" / "workflows" / filename).read_text(encoding="utf-8")
        assert '[dev,data-free,agent]' in workflow
        assert "-c constraints/agent-py312.txt" in workflow


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

    assert "$installParameters = @{" in builder
    assert "@installParameters" in builder
    assert "$installArguments" not in builder


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
    with ZipFile(archives[0]) as archive:
        assert json.loads(archive.read("VERSION.json"))["product_name"] == "股票量化研究工作台"
        names = set(archive.namelist())
        assert {".env.example", "LICENSE", "README_RELEASE_ZH.md", "pyproject.toml"} <= names
        for action in ("安装", "启动", "停止"):
            assert f"{action}股票量化研究工作台.cmd" in names


@pytest.mark.parametrize(("runtime", "expected"), [
    (None, "install"),
    (".venv/Scripts/python.exe", "launch"),
    ("runtime/python.exe", "launch"),
])
def test_windows_entrypoint_selects_install_or_launch(tmp_path, runtime, expected):
    launcher = tmp_path / "启动股票量化研究工作台.cmd"
    shutil.copyfile(ROOT / launcher.name, launcher)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "install_release.ps1").write_text("Write-Output install", encoding="ascii")
    (scripts / "start_quant_workbench.ps1").write_text("Write-Output launch", encoding="ascii")
    if runtime:
        marker = tmp_path / runtime
        marker.parent.mkdir(parents=True)
        marker.touch()
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", launcher.name], cwd=tmp_path,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=True,
    )
    assert result.stdout.strip() == expected.encode("ascii")
