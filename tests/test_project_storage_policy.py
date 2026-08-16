import importlib
import importlib.util
import os
import shutil
import subprocess
from collections.abc import Iterator
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


@pytest.fixture
def project_temp() -> Iterator[Path]:
    workspace = ROOT.resolve(strict=True)
    assert workspace.drive.casefold() == "d:"
    controlled_root = workspace / ".runtime" / "temp"
    controlled_root.mkdir(parents=True, exist_ok=True)
    controlled_root = controlled_root.resolve(strict=True)
    assert controlled_root.is_relative_to(workspace)
    test_root = controlled_root / f"pytest-project-storage-{uuid4().hex}"
    test_root.mkdir()
    assert test_root.resolve(strict=True).parent == controlled_root

    try:
        yield test_root
    finally:
        lexical_root = Path(os.path.normpath(os.path.abspath(test_root)))
        assert lexical_root.parent == controlled_root
        if lexical_root.exists():
            shutil.rmtree(lexical_root)


def test_project_storage_policy_module_exists() -> None:
    assert importlib.util.find_spec("a_share_quant.storage.project_storage") is not None


def test_project_storage_policy_exports_boundary_types() -> None:
    module = importlib.import_module("a_share_quant.storage.project_storage")

    assert issubclass(module.StorageBoundaryError, ValueError)
    assert callable(module.ProjectStoragePolicy)


def test_repo_root_must_already_exist(project_temp: Path) -> None:
    with pytest.raises(StorageBoundaryError, match="项目目录"):
        ProjectStoragePolicy(project_temp / "missing", required_drive=None)


def test_authorize_accepts_repo_paths_without_creating_business_file(
    project_temp: Path,
) -> None:
    repo = project_temp / "repo"
    repo.mkdir()
    policy = ProjectStoragePolicy(repo, required_drive=None)

    relative = policy.authorize("data/daily.parquet")
    absolute = policy.authorize(repo / "data" / "daily.parquet")

    assert relative == repo / "data" / "daily.parquet"
    assert absolute == relative
    assert not relative.exists()


@pytest.mark.parametrize("method_name", ("authorize", "revalidate"))
@pytest.mark.parametrize(
    "unsafe_path",
    [
        "data/file.json:stream",
        "folder:stream/file.json",
        "CON",
        "con.txt",
        "data/CON/file.json",
        "PrN.json",
        "AUX",
        "nul.data",
        "CONIN$",
        "conin$.json",
        "CONOUT$",
        "conout$.json",
        "COM1",
        "com9.log",
        "LPT1",
        "lpt9.log",
        "COM¹.csv",
        "COM².csv",
        "COM³.csv",
        "LPT¹.csv",
        "LPT².csv",
        "LPT³.csv",
        "data/LPT²/file.json",
        "COM1 .txt",
        "data/file.json.",
        "data/file.json ",
        "data/folder./file.json",
        "data/folder /file.json",
    ],
)
def test_authorize_rejects_windows_unsafe_path_components(
    project_temp: Path,
    method_name: str,
    unsafe_path: str,
) -> None:
    repo = project_temp / "repo"
    repo.mkdir()
    policy = ProjectStoragePolicy(repo, required_drive=None)

    with pytest.raises(StorageBoundaryError, match="项目目录"):
        getattr(policy, method_name)(unsafe_path)


@pytest.mark.parametrize(
    "safe_path",
    ("data/file.json", "COM10", "COM10.json", "CLOCK$", "clock$.json"),
)
def test_authorize_allows_windows_safe_path_components(
    project_temp: Path,
    safe_path: str,
) -> None:
    repo = project_temp / "repo"
    repo.mkdir()

    authorized = ProjectStoragePolicy(repo, required_drive=None).authorize(safe_path)

    assert authorized == repo / safe_path
    assert not authorized.exists()


@pytest.mark.parametrize(
    "candidate_factory",
    [
        lambda repo: repo.parent / "outside" / "data.parquet",
        lambda repo: Path("..") / "outside" / "data.parquet",
        lambda repo: Path("C:/outside/data.parquet"),
    ],
)
def test_authorize_rejects_external_drive_and_traversal_paths(
    project_temp: Path,
    candidate_factory,
) -> None:
    repo = project_temp / "repo"
    repo.mkdir()
    policy = ProjectStoragePolicy(repo, required_drive=None)

    with pytest.raises(StorageBoundaryError, match="项目目录|D盘"):
        policy.authorize(candidate_factory(repo))


@pytest.mark.skipif(os.name != "nt", reason="Windows drive policy")
def test_production_policy_requires_d_drive(project_temp: Path) -> None:
    repo = project_temp / "repo"
    repo.mkdir()

    assert ProjectStoragePolicy(repo).repo_root.drive.casefold() == "d:"
    with pytest.raises(StorageBoundaryError, match="D盘"):
        ProjectStoragePolicy(Path("C:/"))


def test_child_environment_is_project_local_and_does_not_mutate_inputs(
    project_temp: Path,
) -> None:
    repo = project_temp / "repo"
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


def test_child_environment_does_not_add_home_or_codex_home(project_temp: Path) -> None:
    repo = project_temp / "repo"
    repo.mkdir()

    child = ProjectStoragePolicy(repo, required_drive=None).child_environment(
        {"PATH": "kept-path"}
    )

    assert "HOME" not in child
    assert "CODEX_HOME" not in child


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_authorize_rejects_real_junction_ancestor(project_temp: Path) -> None:
    repo = project_temp / "repo"
    outside = project_temp / "outside"
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
    project_temp: Path,
) -> None:
    repo = project_temp / "repo"
    outside = project_temp / "outside.bin"
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
    project_temp: Path,
) -> None:
    repo = project_temp / "repo"
    outside = project_temp / "outside"
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
    project_temp: Path,
) -> None:
    repo = project_temp / "repo"
    outside = project_temp / "outside"
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
    project_temp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = project_temp / "repo"
    runtime = repo / ".runtime"
    outside = project_temp / "outside"
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
