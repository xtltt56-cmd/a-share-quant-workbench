import importlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from a_share_quant.storage.project_storage import (
    ProjectStoragePolicy,
    StorageBoundaryError,
)

MANAGED_ENVIRONMENT = {
    "TEMP": ".runtime/tmp",
    "TMP": ".runtime/tmp",
    "PIP_CACHE_DIR": ".runtime/cache/pip",
    "JOBLIB_TEMP_FOLDER": ".runtime/cache/joblib",
    "XDG_CACHE_HOME": ".runtime/cache/xdg",
    "MPLCONFIGDIR": ".runtime/cache/matplotlib",
}
ROOT = Path(__file__).resolve().parents[1]


def test_project_storage_policy_module_exists() -> None:
    assert importlib.util.find_spec("a_share_quant.storage.project_storage") is not None


def test_project_storage_policy_exports_boundary_types() -> None:
    module = importlib.import_module("a_share_quant.storage.project_storage")

    assert issubclass(module.StorageBoundaryError, ValueError)
    assert callable(module.ProjectStoragePolicy)


def test_repo_root_must_already_exist(tmp_path: Path) -> None:
    with pytest.raises(StorageBoundaryError, match="项目目录"):
        ProjectStoragePolicy(tmp_path / "missing", required_drive=None)


def test_authorize_accepts_repo_paths_without_creating_business_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = ProjectStoragePolicy(repo, required_drive=None)

    relative = policy.authorize("data/daily.parquet")
    absolute = policy.authorize(repo / "data" / "daily.parquet")

    assert relative == repo / "data" / "daily.parquet"
    assert absolute == relative
    assert not relative.exists()


@pytest.mark.parametrize(
    "candidate_factory",
    [
        lambda repo: repo.parent / "outside" / "data.parquet",
        lambda repo: Path("..") / "outside" / "data.parquet",
        lambda repo: Path("C:/outside/data.parquet"),
    ],
)
def test_authorize_rejects_external_drive_and_traversal_paths(
    tmp_path: Path,
    candidate_factory,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = ProjectStoragePolicy(repo, required_drive=None)

    with pytest.raises(StorageBoundaryError, match="项目目录|D盘"):
        policy.authorize(candidate_factory(repo))


@pytest.mark.skipif(os.name != "nt", reason="Windows drive policy")
def test_production_policy_requires_d_drive() -> None:
    artifacts = ROOT / ".runtime" / "test-artifacts"
    repo = artifacts / f"project-storage-{uuid4().hex}"
    repo.mkdir(parents=True)

    try:
        assert ProjectStoragePolicy(repo).repo_root.drive.casefold() == "d:"
        with pytest.raises(StorageBoundaryError, match="D盘"):
            ProjectStoragePolicy(Path("C:/"))
    finally:
        repo.rmdir()


def test_child_environment_is_project_local_and_does_not_mutate_inputs(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    policy = ProjectStoragePolicy(repo, required_drive=None)
    base = {
        "PATH": "kept-path",
        "CUSTOM": "kept-custom",
        "HOME": "kept-home",
        "CODEX_HOME": "kept-codex-home",
        "TEMP": "old-temp",
    }
    base_before = dict(base)
    process_environment_before = dict(os.environ)

    child = policy.child_environment(base)

    assert base == base_before
    assert dict(os.environ) == process_environment_before
    assert child["PATH"] == "kept-path"
    assert child["CUSTOM"] == "kept-custom"
    assert child["HOME"] == "kept-home"
    assert child["CODEX_HOME"] == "kept-codex-home"
    for key, relative in MANAGED_ENVIRONMENT.items():
        expected = repo / Path(relative)
        assert Path(child[key]) == expected
        assert expected.is_dir()
        assert policy.authorize(expected) == expected


def test_child_environment_does_not_add_home_or_codex_home(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    child = ProjectStoragePolicy(repo, required_drive=None).child_environment(
        {"PATH": "kept-path"}
    )

    assert "HOME" not in child
    assert "CODEX_HOME" not in child


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_authorize_rejects_real_junction_ancestor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    junction = repo / "linked"
    _create_junction_or_skip(junction, outside)

    try:
        with pytest.raises(StorageBoundaryError, match="项目目录"):
            ProjectStoragePolicy(repo, required_drive=None).authorize("linked/data.bin")
    finally:
        _remove_junction(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows symbolic-link test")
def test_authorize_rejects_real_symbolic_link_ancestor_when_supported(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside.bin"
    repo.mkdir()
    outside.write_bytes(b"outside")
    link = repo / "linked.bin"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic-link creation unavailable: {exc}")

    try:
        with pytest.raises(StorageBoundaryError, match="项目目录"):
            ProjectStoragePolicy(repo, required_drive=None).authorize("linked.bin")
    finally:
        link.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_lexically_removed_junction_component_does_not_block_safe_path(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    safe = repo / "safe"
    repo.mkdir()
    outside.mkdir()
    safe.mkdir()
    junction = repo / "linked"
    _create_junction_or_skip(junction, outside)

    try:
        authorized = ProjectStoragePolicy(repo, required_drive=None).authorize(
            Path("linked") / ".." / "safe" / "data.bin"
        )
        assert authorized == safe / "data.bin"
    finally:
        _remove_junction(junction)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_authorize_fails_closed_when_ancestor_is_replaced_after_policy_creation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    runtime = repo / "runtime"
    repo.mkdir()
    outside.mkdir()
    runtime.mkdir()
    policy = ProjectStoragePolicy(repo, required_drive=None)
    authorized = policy.authorize("runtime/data.bin")
    runtime.rmdir()
    _create_junction_or_skip(runtime, outside)

    try:
        with pytest.raises(StorageBoundaryError, match="项目目录"):
            policy.revalidate(authorized)
    finally:
        _remove_junction(runtime)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_child_environment_rejects_ancestor_replaced_during_directory_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".runtime"
    outside = tmp_path / "outside"
    repo.mkdir()
    runtime.mkdir()
    outside.mkdir()
    runtime_temp = runtime / "tmp"
    outside_temp = outside / "tmp"
    original_mkdir = Path.mkdir
    replaced = False

    def replace_runtime_then_create(path: Path, *args, **kwargs) -> None:
        nonlocal replaced
        if path == runtime_temp and not replaced:
            runtime.rmdir()
            _create_junction_or_skip(runtime, outside)
            replaced = True
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", replace_runtime_then_create)
    try:
        with pytest.raises(StorageBoundaryError, match="项目目录"):
            ProjectStoragePolicy(repo, required_drive=None).child_environment({})
    finally:
        monkeypatch.undo()
        if outside_temp.exists():
            outside_temp.rmdir()
        _remove_junction(runtime)


def _create_junction_or_skip(junction: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"junction creation unavailable: {result.stderr or result.stdout}")
    assert junction.is_dir()


def _remove_junction(junction: Path) -> None:
    if junction.exists():
        os.rmdir(junction)


@pytest.mark.skipif(os.name != "nt", reason="PowerShell inheritance test")
def test_launcher_environment_assignments_are_inherited_by_child_process() -> None:
    launcher = (ROOT / "scripts" / "start_quant_workbench.ps1").read_text(
        encoding="utf-8"
    )
    managed_names = tuple(MANAGED_ENVIRONMENT)
    setup_prefixes = ("$runtimeTempDir =", "$runtimeCacheDir =") + tuple(
        f"$env:{name} =" for name in managed_names
    )
    setup = "\n".join(
        line for line in launcher.splitlines() if line.startswith(setup_prefixes)
    )
    child_expression = (
        "[ordered]@{"
        + ";".join(
            f"{name}=$env:{name}"
            for name in (*managed_names, "HOME", "CODEX_HOME", "PROJECT_STORAGE_TEST_ROOT")
        )
        + "} | ConvertTo-Json -Compress"
    )
    repo_literal = str(ROOT).replace("'", "''")
    command = (
        f"$repoRoot = '{repo_literal}'\n"
        "$runtimeDir = Join-Path $repoRoot '.runtime'\n"
        "$env:PROJECT_STORAGE_TEST_ROOT = $repoRoot\n"
        f"{setup}\n"
        f"& powershell -NoProfile -Command '{child_expression}'"
    )
    base = dict(os.environ)
    base["HOME"] = "preserved-home"
    base["CODEX_HOME"] = "preserved-codex-home"
    process_environment_before = dict(os.environ)

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=True,
        env=base,
    )

    inherited = json.loads(result.stdout.strip())
    assert inherited["HOME"] == "preserved-home"
    assert inherited["CODEX_HOME"] == "preserved-codex-home"
    assert dict(os.environ) == process_environment_before
    inherited_root = Path(inherited["PROJECT_STORAGE_TEST_ROOT"])
    for name, relative in MANAGED_ENVIRONMENT.items():
        assert Path(inherited[name]) == inherited_root / relative
