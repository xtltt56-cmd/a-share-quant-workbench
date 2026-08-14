from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import textwrap
import time
from collections import namedtuple
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

import a_share_quant.storage.research_data_store as research_store_module
from a_share_quant.research.history_contracts import (
    CorporateAction,
    DatasetCoverage,
    PointInTimeInstrument,
    ResearchArtifact,
)
from a_share_quant.storage.project_storage import ProjectStoragePolicy, StorageBoundaryError
from a_share_quant.storage.research_data_store import (
    ManifestIntegrityError,
    ResearchDataset,
    ResearchDataStore,
    StorageQuotaError,
)

ROOT = Path(__file__).resolve().parents[1]
DiskUsage = namedtuple("DiskUsage", "total used free")


@pytest.fixture
def research_temp() -> Iterator[Path]:
    workspace = ROOT.resolve(strict=True)
    assert workspace.drive.casefold() == "d:"
    controlled = workspace / ".runtime" / "temp"
    controlled.mkdir(parents=True, exist_ok=True)
    controlled = controlled.resolve(strict=True)
    test_root = controlled / f"pytest-research-store-{uuid4().hex}"
    test_root.mkdir()
    try:
        yield test_root
    finally:
        lexical = Path(os.path.normpath(os.path.abspath(test_root)))
        assert lexical.parent == controlled
        if lexical.exists():
            shutil.rmtree(lexical)


def frame(value: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["000001", "000002"],
            "session": ["2026-01-05", "2026-01-05"],
            "x": [value, value + 1],
        }
    )


def store_at(research_temp: Path, **kwargs) -> ResearchDataStore:
    repo = research_temp / f"repo-{uuid4().hex}"
    repo.mkdir()
    return ResearchDataStore(ProjectStoragePolicy(repo, required_drive=None), **kwargs)


def test_history_contracts_are_frozen_and_validate_temporal_fields() -> None:
    artifact = ResearchArtifact(
        dataset="research_returns",
        key="000001",
        data_version="v1",
        sha256="a" * 64,
        path=Path("D:/contracts") / ("a" * 64 + ".parquet"),
        row_count=2,
        size_bytes=10,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        schema_fingerprint="b" * 64,
    )
    assert artifact.row_count == 2
    with pytest.raises((AttributeError, TypeError)):
        artifact.row_count = 3  # type: ignore[misc]
    with pytest.raises(ValueError, match="UTC"):
        ResearchArtifact(**{**artifact.__dict__, "created_at": datetime(2026, 1, 1)})

    coverage = DatasetCoverage("research_returns", date(2020, 1, 1), date(2026, 1, 1), 30, 1750)
    instrument = PointInTimeInstrument("000001", "平安银行", date(1991, 4, 3), None, True)
    action = CorporateAction("000001", date(2026, 1, 5), "cash_dividend", 0.1, "v1")
    assert coverage.start_date <= coverage.end_date
    assert instrument.listed_on < action.effective_date


def test_normal_write_verify_and_deterministic_manifest(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")

    assert artifact.row_count == 2
    assert artifact.path.is_file()
    assert artifact.sha256 == hashlib.sha256(artifact.path.read_bytes()).hexdigest()
    assert store.verify(artifact)
    lines = store.manifest_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert lines[0] == json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert record["manifest_schema"] == "a-share-quant.research-artifact"
    assert record["manifest_version"] == 1


def test_same_logical_content_is_idempotent(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    first = store.replace_dataset(ResearchDataset.RESEARCH_RETURNS, "000001", frame(), "v1")
    second = store.replace_dataset("research_returns", "000001", frame(), "v1")
    assert second == first
    assert store.manifest_count(first.sha256) == 1


def test_cross_key_content_reuses_immutable_blob(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    first = store.replace_dataset("research_returns", "000001", frame(), "v1")
    second = store.replace_dataset("research_returns", "000002", frame(), "v1")
    assert second.path == first.path
    assert store.manifest_count(first.sha256) == 2
    assert len(list(first.path.parent.glob("*.parquet"))) == 1


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"maximum_single_file_bytes": 10}, "单文件"),
        ({"maximum_research_data_bytes": 10}, "配额"),
        ({"minimum_free_bytes": 100, "disk_usage": lambda _path: DiskUsage(100, 99, 1)}, "空闲"),
    ],
)
def test_quota_checks_happen_before_publication(
    research_temp: Path, kwargs: dict, match: str
) -> None:
    store = store_at(research_temp, **kwargs)
    with pytest.raises(StorageQuotaError, match=match):
        store.replace_dataset("research_returns", "000001", frame(), "v1")
    assert not store.manifest_path.exists()
    assert not list(store.blob_directory.glob("*.parquet"))
    assert not list(store.staging_directory.glob(".research-stage-*.parquet"))


@pytest.mark.parametrize(
    "failure", ["parquet", "file_fsync", "blob_replace", "manifest_fsync", "manifest_replace"]
)
def test_injected_failure_preserves_previous_active(research_temp: Path, failure: str) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    old = store.replace_dataset("research_returns", "000001", frame(1), "v1")
    original_writer = store._parquet_writer
    original_file_fsync = store._file_fsync
    original_blob_replace = store._blob_replace
    original_manifest_fsync = store._manifest_fsync
    original_manifest_replace = store._manifest_replace

    def fail(*_args, **_kwargs):
        raise OSError(f"injected {failure}")

    setattr(store, f"_{failure if failure != 'parquet' else 'parquet_writer'}", fail)
    try:
        with pytest.raises(OSError, match="injected"):
            store.replace_dataset("research_returns", "000001", frame(10), "v2")
    finally:
        store._parquet_writer = original_writer
        store._file_fsync = original_file_fsync
        store._blob_replace = original_blob_replace
        store._manifest_fsync = original_manifest_fsync
        store._manifest_replace = original_manifest_replace

    assert store.active_artifact("research_returns", "000001") == old
    assert store.verify(old)
    assert not list(store.staging_directory.glob(".research-stage-*.parquet"))
    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)
    assert all(path.name != old.path.name for path in report.removed)


def test_finally_does_not_delete_stage_replaced_after_seal(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    replaced_stage: Path | None = None

    def replace_stage_then_fail(source: Path, _target: Path) -> None:
        nonlocal replaced_stage
        source.write_bytes(b"foreign-stage-after-seal")
        replaced_stage = source
        raise OSError("blob replace interrupted")

    store._blob_replace = replace_stage_then_fail
    with pytest.raises(OSError, match="blob replace interrupted"):
        store.replace_dataset("research_returns", "000001", frame(), "v1")

    assert replaced_stage is not None and replaced_stage.exists()
    markers = list(store.ownership_directory.glob("*.owned.json"))
    assert markers
    assert any(
        store._read_owned_marker(marker)["relative_path"]
        == replaced_stage.relative_to(store.root_directory).as_posix()
        for marker in markers
    )


def test_stage_fsync_failure_cleans_current_owned_unsealed_file(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    old = store.replace_dataset("research_returns", "000001", frame(1), "v1")
    original_fsync = store._file_fsync

    def fail_only_stage(path: Path) -> None:
        if path.name.startswith(".research-stage-"):
            raise OSError("stage fsync failed")
        original_fsync(path)

    store._file_fsync = fail_only_stage
    with pytest.raises(OSError, match="stage fsync failed"):
        store.replace_dataset("research_returns", "000001", frame(20), "v2")

    store._file_fsync = original_fsync
    assert store.active_artifact("research_returns", "000001") == old
    assert not list(store.blob_directory.glob(".research-stage-*.parquet"))
    assert not any(
        store._read_owned_marker(marker)["kind"] == "stage"
        for marker in store.ownership_directory.glob("*.owned.json")
    )


def test_stage_fsync_failure_retains_file_replaced_after_expected_capture(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    original_fsync = store._file_fsync

    def replace_stage_then_fail(path: Path) -> None:
        if path.name.startswith(".research-stage-"):
            path.write_bytes(b"foreign-after-expected-capture")
            raise OSError("stage fsync failed")
        original_fsync(path)

    store._file_fsync = replace_stage_then_fail
    with pytest.raises(OSError, match="stage fsync failed"):
        store.replace_dataset("research_returns", "000001", frame(), "v1")

    stages = list(store.blob_directory.glob(".research-stage-*.parquet"))
    assert len(stages) == 1
    assert stages[0].read_bytes() == b"foreign-after-expected-capture"
    assert any(
        store._read_owned_marker(marker)["kind"] == "stage"
        for marker in store.ownership_directory.glob("*.owned.json")
    )


def test_finally_does_not_delete_manifest_temp_replaced_after_seal(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    replaced_temp: Path | None = None

    def replace_manifest_temp_then_fail(source: Path, _target: Path) -> None:
        nonlocal replaced_temp
        source.write_bytes(b"foreign-manifest-temp-after-seal")
        replaced_temp = source
        raise OSError("manifest replace interrupted")

    store._manifest_replace = replace_manifest_temp_then_fail
    with pytest.raises(OSError, match="manifest replace interrupted"):
        store.replace_dataset("research_returns", "000001", frame(), "v1")

    assert replaced_temp is not None and replaced_temp.exists()
    markers = list(store.ownership_directory.glob("*.owned.json"))
    assert any(
        store._read_owned_marker(marker)["relative_path"]
        == replaced_temp.relative_to(store.root_directory).as_posix()
        for marker in markers
    )


def test_manifest_fsync_failure_cleans_current_owned_unsealed_temp(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    old = store.replace_dataset("research_returns", "000001", frame(1), "v1")
    original_fsync = store._manifest_fsync
    fsync_calls = 0

    def fail_new_manifest_temp(path: Path) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError("manifest fsync failed")
        original_fsync(path)

    store._manifest_fsync = fail_new_manifest_temp
    with pytest.raises(OSError, match="manifest fsync failed"):
        store.replace_dataset("research_returns", "000001", frame(20), "v2")

    store._manifest_fsync = original_fsync
    assert fsync_calls == 2
    assert store.active_artifact("research_returns", "000001") == old
    assert not list(store.root_directory.glob(".manifest-*.tmp"))
    assert not any(
        store._read_owned_marker(marker)["kind"] == "manifest_temp"
        for marker in store.ownership_directory.glob("*.owned.json")
    )


def test_manifest_fsync_failure_retains_temp_replaced_after_expected_capture(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)

    def replace_manifest_then_fail(path: Path) -> None:
        path.write_bytes(b"foreign-after-expected-capture")
        raise OSError("manifest fsync failed")

    store._manifest_fsync = replace_manifest_then_fail
    with pytest.raises(OSError, match="manifest fsync failed"):
        store.replace_dataset("research_returns", "000001", frame(), "v1")

    temps = list(store.root_directory.glob(".manifest-*.tmp"))
    assert len(temps) == 1
    assert temps[0].read_bytes() == b"foreign-after-expected-capture"
    assert any(
        store._read_owned_marker(marker)["kind"] == "manifest_temp"
        for marker in store.ownership_directory.glob("*.owned.json")
    )


def test_success_finally_keeps_blob_marker_when_referenced_blob_was_replaced(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    replaced_blob: Path | None = None

    def publish_manifest_then_replace_blob(source: Path, target: Path) -> None:
        nonlocal replaced_blob
        os.replace(source, target)
        record = json.loads(target.read_text(encoding="utf-8"))
        replaced_blob = store.root_directory / record["blob_path"]
        replaced_blob.write_bytes(b"foreign-referenced-blob")

    store._manifest_replace = publish_manifest_then_replace_blob
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")

    assert replaced_blob == artifact.path
    assert not store.verify(artifact)
    markers = list(store.ownership_directory.glob("*.owned.json"))
    assert any(
        store._read_owned_marker(marker)["kind"] == "orphan_blob"
        for marker in markers
    )


def test_manifest_truncation_and_conflicting_duplicate_fail_closed(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    store.replace_dataset("research_returns", "000001", frame(), "v1")
    original = store.manifest_path.read_bytes()
    store.manifest_path.write_bytes(original[:-1])
    with pytest.raises(ManifestIntegrityError, match="截断"):
        store.active_artifact("research_returns", "000001")

    store.manifest_path.write_bytes(original)
    line = json.loads(original.decode("utf-8"))
    line["sha256"] = "f" * 64
    store.manifest_path.write_text(
        original.decode("utf-8") + json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestIntegrityError):
        store.active_artifact("research_returns", "000001")


def test_manifest_path_escape_and_blob_tamper_fail_closed(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")
    record = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    record["blob_path"] = "../escape.parquet"
    store.manifest_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    with pytest.raises(ManifestIntegrityError, match="路径"):
        store.active_artifact("research_returns", "000001")

    record["blob_path"] = f"blobs/{artifact.sha256}.parquet"
    store.manifest_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    artifact.path.write_bytes(b"tampered")
    assert not store.verify(artifact)


def test_concurrent_same_key_is_serialized_and_idempotent(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    with ThreadPoolExecutor(max_workers=8) as pool:
        artifacts = list(
            pool.map(
                lambda _index: store.replace_dataset("research_returns", "000001", frame(), "v1"),
                range(16),
            )
        )
    assert len({artifact.sha256 for artifact in artifacts}) == 1
    assert store.manifest_count(artifacts[0].sha256) == 1


@pytest.mark.parametrize(
    "dataset,key",
    [
        ("unknown", "000001"),
        ("research_returns", "../escape"),
        ("research_returns", "folder/key"),
        ("research_returns", "file:ads"),
        ("research_returns", "CON"),
        ("research_returns", "LPT1.txt"),
    ],
)
def test_dataset_and_logical_key_are_allowlisted_and_path_safe(
    research_temp: Path, dataset: str, key: str
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    with pytest.raises((ValueError, StorageBoundaryError)):
        store.replace_dataset(dataset, key, frame(), "v1")


def test_reparse_storage_ancestor_is_rejected(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    if os.name != "nt":
        pytest.skip("Windows junction test")
    outside = research_temp / "outside"
    outside.mkdir()
    store.root_directory.mkdir(parents=True, exist_ok=True)
    if store.blob_directory.exists():
        store.blob_directory.rmdir()
    result = os.system(f'cmd /d /c mklink /J "{store.blob_directory}" "{outside}" >nul')
    if result != 0:
        pytest.skip("junction creation unavailable")
    try:
        with pytest.raises(StorageBoundaryError):
            store.replace_dataset("research_returns", "000001", frame(), "v1")
    finally:
        os.rmdir(store.blob_directory)


def test_safe_cleanup_never_deletes_referenced_or_unknown_files(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")
    unknown = store.root_directory / "model.bin"
    unknown.write_bytes(b"must survive")
    unknown_digest = store.blob_directory / ("f" * 64 + ".parquet")
    unknown_digest.write_bytes(b"foreign")
    unknown_stage = store.blob_directory / f".research-stage-{uuid4().hex}.parquet"
    unknown_stage.write_bytes(b"foreign-stage")

    dry = store.cleanup_rebuildable_temporary_files(grace_seconds=0, dry_run=True)
    assert unknown_digest not in dry.candidates
    assert unknown_stage not in dry.candidates
    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)
    assert unknown_digest not in report.removed
    assert artifact.path.exists() and store.manifest_path.exists() and unknown.exists()
    assert unknown_digest.exists()
    assert unknown_stage.exists()


def test_outer_c_temp_does_not_redirect_store_or_test_artifacts(
    research_temp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMP", "C:\\Windows\\Temp")
    monkeypatch.setenv("TMP", "C:\\Windows\\Temp")
    store = store_at(research_temp, minimum_free_bytes=0)
    artifact = store.replace_dataset("trial_evidence", "trial-1", frame(), "v1")
    assert artifact.path.drive.casefold() == "d:"
    assert artifact.path.is_relative_to(ROOT)
    assert research_temp.drive.casefold() == "d:"


def test_default_space_limits_are_conservative() -> None:
    assert ResearchDataStore.DEFAULT_MAXIMUM_SINGLE_FILE_BYTES == 536_870_912
    assert ResearchDataStore.DEFAULT_MAXIMUM_RESEARCH_DATA_BYTES == 21_474_836_480
    assert ResearchDataStore.DEFAULT_MINIMUM_FREE_BYTES == 21_474_836_480


def test_windows_cross_process_lock_waits_instead_of_reading_locked_byte(
    research_temp: Path,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows byte-range lock test")
    repo = research_temp / "multiprocess-repo"
    repo.mkdir()
    ready = research_temp / "holder-ready"
    child_temp = research_temp / "child-temp"
    child_temp.mkdir()
    environment = dict(os.environ)
    environment["TEMP"] = str(child_temp)
    environment["TMP"] = str(child_temp)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(ROOT / "src"), environment.get("PYTHONPATH")))
    )
    holder_code = textwrap.dedent(
        f"""
        import time
        from pathlib import Path
        from a_share_quant.storage.project_storage import ProjectStoragePolicy
        from a_share_quant.storage.research_data_store import ResearchDataStore

        store = ResearchDataStore(
            ProjectStoragePolicy(Path({str(repo)!r}), required_drive=None),
            minimum_free_bytes=0,
        )
        with store._locked():
            Path({str(ready)!r}).write_text("ready", encoding="utf-8")
            time.sleep(12)
        """
    )
    writer_code = textwrap.dedent(
        f"""
        from pathlib import Path
        import pandas as pd
        from a_share_quant.storage.project_storage import ProjectStoragePolicy
        from a_share_quant.storage.research_data_store import ResearchDataStore

        store = ResearchDataStore(
            ProjectStoragePolicy(Path({str(repo)!r}), required_drive=None),
            minimum_free_bytes=0,
        )
        store.replace_dataset(
            "research_returns",
            "000001",
            pd.DataFrame({{"symbol": ["000001"], "x": [1]}}),
            "v1",
        )
        """
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ready.exists(), holder.communicate(timeout=2)

    started = time.monotonic()
    writer = subprocess.run(
        [sys.executable, "-c", writer_code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    elapsed = time.monotonic() - started
    holder_stdout, holder_stderr = holder.communicate(timeout=8)

    assert holder.returncode == 0, (holder_stdout, holder_stderr)
    assert writer.returncode == 0, writer.stderr
    assert elapsed >= 10.5


def test_windows_lock_non_contention_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows byte-range lock test")
    import msvcrt

    calls = 0

    def fail_bad_descriptor(_fd: int, _mode: int, _count: int) -> None:
        nonlocal calls
        calls += 1
        raise OSError(errno.EBADF, "bad descriptor")

    class FakeHandle:
        def seek(self, _offset: int) -> None:
            return None

        def fileno(self) -> int:
            return 123

    monkeypatch.setattr(msvcrt, "locking", fail_bad_descriptor)
    with pytest.raises(OSError) as error:
        research_store_module._lock_file(
            FakeHandle(), timeout_seconds=1, poll_interval_seconds=0.01
        )
    assert error.value.errno == errno.EBADF
    assert calls == 1


def test_windows_lock_contention_timeout_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows byte-range lock test")
    import msvcrt

    def always_contended(_fd: int, _mode: int, _count: int) -> None:
        raise OSError(errno.EACCES, "lock occupied")

    class FakeHandle:
        def seek(self, _offset: int) -> None:
            return None

        def fileno(self) -> int:
            return 123

    monkeypatch.setattr(msvcrt, "locking", always_contended)
    with pytest.raises(
        research_store_module.StorageLockTimeoutError, match="timed out"
    ):
        research_store_module._lock_file(
            FakeHandle(), timeout_seconds=0, poll_interval_seconds=0.01
        )


@pytest.mark.parametrize("field,value", [("row_count", 999), ("schema_fingerprint", "c" * 64)])
def test_manifest_canonical_digest_rejects_metadata_tampering(
    research_temp: Path, field: str, value: object
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")
    record = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    record[field] = value
    store.manifest_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    with pytest.raises(ManifestIntegrityError, match="摘要|metadata|record"):
        store.active_artifact("research_returns", "000001")
    assert not store.verify(artifact)


def test_manifest_created_at_wrong_type_is_integrity_error(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    store.replace_dataset("research_returns", "000001", frame(), "v1")
    record = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    record["created_at"] = 123
    store.manifest_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    with pytest.raises(ManifestIntegrityError, match="created_at"):
        store.active_artifact("research_returns", "000001")


def test_verify_rejects_manifest_metadata_rewrite_even_when_record_is_resigned(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")
    record = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    record["created_at"] = "2020-01-01T00:00:00Z"
    record["record_sha256"] = store._canonical_record_sha256(record)
    store.manifest_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    assert not store.verify(artifact)


def test_new_research_code_avoids_python_311_datetime_utc_import() -> None:
    forbidden_import = "from datetime import " + "UTC"
    for relative in (
        "src/a_share_quant/research/history_contracts.py",
        "src/a_share_quant/storage/research_data_store.py",
        "tests/test_research_data_store.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert forbidden_import not in source


def test_blob_parquet_metadata_must_match_manifest(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    store.replace_dataset("research_returns", "000001", frame(), "v1")
    expanded = pd.concat([frame(), frame().iloc[[0]]], ignore_index=True)
    temporary = store.blob_directory / f"metadata-test-{uuid4().hex}.parquet"
    expanded.to_parquet(temporary, index=False)
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    replacement = store.blob_directory / f"{digest}.parquet"
    os.replace(temporary, replacement)
    record = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    record["sha256"] = digest
    record["blob_path"] = f"blobs/{digest}.parquet"
    record["size_bytes"] = replacement.stat().st_size
    record["record_id"] = hashlib.sha256(
        f"{record['dataset']}\0{record['key']}\0{record['data_version']}\0{digest}".encode()
    ).hexdigest()
    record["record_sha256"] = store._canonical_record_sha256(record)
    store.manifest_path.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )

    with pytest.raises(ManifestIntegrityError, match="摘要|大小"):
        store.active_artifact("research_returns", "000001")


def test_same_logical_version_with_changed_content_is_a_conflict(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    old = store.replace_dataset("research_returns", "000001", frame(1), "v1")

    with pytest.raises(research_store_module.ManifestConflictError, match="data_version|逻辑"):
        store.replace_dataset("research_returns", "000001", frame(50), "v1")

    assert store.active_artifact("research_returns", "000001") == old
    assert store.manifest_count() == 1


@pytest.mark.parametrize("limit", ["single", "total", "free"])
def test_idempotent_replacement_still_enforces_current_storage_limits(
    research_temp: Path, limit: str
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    old = store.replace_dataset("research_returns", "000001", frame(), "v1")
    if limit == "single":
        store.maximum_single_file_bytes = 1
    elif limit == "total":
        store.maximum_research_data_bytes = 1
    else:
        store.minimum_free_bytes = 1
        store._disk_usage = lambda _path: DiskUsage(100, 100, 0)

    with pytest.raises(StorageQuotaError):
        store.replace_dataset("research_returns", "000001", frame(), "v1")

    assert store.active_artifact("research_returns", "000001") == old


def test_manifest_rejects_conflicting_duplicate_logical_version(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    store.replace_dataset("research_returns", "000001", frame(), "v1")
    record = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    duplicate = dict(record)
    duplicate["sha256"] = "d" * 64
    duplicate["blob_path"] = f"blobs/{duplicate['sha256']}.parquet"
    duplicate["record_id"] = hashlib.sha256(
        f"{duplicate['dataset']}\0{duplicate['key']}\0{duplicate['data_version']}\0{duplicate['sha256']}".encode()
    ).hexdigest()
    duplicate["record_sha256"] = store._canonical_record_sha256(duplicate)
    store.manifest_path.write_text(
        "".join(
            json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
            for item in (record, duplicate)
        ),
        encoding="utf-8",
    )

    with pytest.raises(research_store_module.ManifestConflictError, match="data_version|逻辑"):
        store.active_artifact("research_returns", "000001")


def test_manifest_failure_leaves_owned_orphan_that_cleanup_can_remove(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("manifest publish failed")

    store._manifest_replace = fail_replace
    with pytest.raises(OSError, match="manifest publish"):
        store.replace_dataset("research_returns", "000001", frame(), "v1")

    orphans = [path for path in store.blob_directory.glob("*.parquet") if len(path.stem) == 64]
    markers = list(store.ownership_directory.glob("*.owned.json"))
    assert len(orphans) == 1
    assert markers

    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)
    assert orphans[0] in report.removed
    assert not orphans[0].exists()
    assert not list(store.ownership_directory.glob("*.owned.json"))


def test_fake_ownership_marker_never_authorizes_deletion(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    foreign = store.blob_directory / ("e" * 64 + ".parquet")
    foreign.write_bytes(b"foreign")
    fake = store.ownership_directory / f"{uuid4().hex}.owned.json"
    fake.write_text(
        json.dumps(
            {
                "schema": "a-share-quant.research-owned-object",
                "kind": "orphan_blob",
                "relative_path": f"blobs/{foreign.name}",
                "digest": foreign.stem,
            }
        ),
        encoding="utf-8",
    )

    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)
    assert foreign.exists()
    assert fake.exists()
    assert fake in report.rejected


def test_allocated_unsealed_marker_never_authorizes_cleanup(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    target = store.blob_directory / f".research-stage-{uuid4().hex}.parquet"
    marker = store._create_owned_marker("stage", target)
    target.write_bytes(b"unsealed")

    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)

    assert target.exists()
    assert marker.exists()
    assert marker in report.rejected


def test_sealed_stage_cleanup_requires_matching_hash_and_size(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    target = store.blob_directory / f".research-stage-{uuid4().hex}.parquet"
    marker = store._create_owned_marker("stage", target)
    target.write_bytes(b"owned-stage")
    store._seal_owned_marker(marker, target)

    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)

    assert target in report.removed
    assert not target.exists()
    assert not marker.exists()


def test_sealed_stage_replaced_with_foreign_content_is_rejected(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    target = store.blob_directory / f".research-stage-{uuid4().hex}.parquet"
    marker = store._create_owned_marker("stage", target)
    target.write_bytes(b"owned-stage")
    store._seal_owned_marker(marker, target)
    target.write_bytes(b"foreign-replacement")

    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)

    assert target.exists()
    assert marker.exists()
    assert marker in report.rejected


def test_cleanup_revalidates_content_again_immediately_before_unlink(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    target = store.blob_directory / f".research-stage-{uuid4().hex}.parquet"
    marker = store._create_owned_marker("stage", target)
    target.write_bytes(b"owned-stage")
    store._seal_owned_marker(marker, target)
    original_validate = store._validate_owned_target
    validations = 0

    def replace_after_first_validation(*args, **kwargs) -> None:
        nonlocal validations
        validations += 1
        original_validate(*args, **kwargs)
        if validations == 1:
            target.write_bytes(b"replacement-between-checks")

    store._validate_owned_target = replace_after_first_validation
    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)

    assert validations == 2
    assert target.exists()
    assert marker.exists()
    assert marker in report.rejected


def test_owned_orphan_replaced_with_foreign_content_is_rejected(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("manifest publish failed")

    store._manifest_replace = fail_replace
    with pytest.raises(OSError, match="manifest publish"):
        store.replace_dataset("research_returns", "000001", frame(), "v1")
    orphan = next(store.blob_directory.glob("[0-9a-f]*.parquet"))
    marker = next(store.ownership_directory.glob("*.owned.json"))
    orphan.write_bytes(b"foreign-replacement")

    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)

    assert orphan.exists()
    assert marker.exists()
    assert marker in report.rejected


def test_stale_owned_marker_for_referenced_blob_removes_only_marker(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")
    marker = store._create_owned_marker(
        "orphan_blob", artifact.path, digest=artifact.sha256
    )
    store._seal_owned_marker(marker, artifact.path)

    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)

    assert artifact.path.exists()
    assert artifact.path not in report.removed
    assert not marker.exists()


def test_corrupt_referenced_blob_does_not_hide_damage_by_removing_marker(
    research_temp: Path,
) -> None:
    store = store_at(research_temp, minimum_free_bytes=0, orphan_grace_seconds=0)
    artifact = store.replace_dataset("research_returns", "000001", frame(), "v1")
    marker = store._create_owned_marker(
        "orphan_blob", artifact.path, digest=artifact.sha256
    )
    store._seal_owned_marker(marker, artifact.path)
    artifact.path.write_bytes(b"corrupt-referenced-content")

    with pytest.raises(ManifestIntegrityError):
        store.cleanup_rebuildable_temporary_files(grace_seconds=0)

    assert marker.exists()
    assert artifact.path.exists()


def test_recursive_quota_counts_old_stage_and_unknown_files(research_temp: Path) -> None:
    store = store_at(
        research_temp,
        minimum_free_bytes=0,
        maximum_research_data_bytes=8_000,
    )
    stranded = store.root_directory / "foreign-cache.bin"
    stranded.write_bytes(b"x" * 6_000)

    with pytest.raises(StorageQuotaError, match="配额"):
        store.replace_dataset("research_returns", "000001", frame(), "v1")

    assert stranded.exists()
    assert not store.manifest_path.exists()
    assert not list(store.blob_directory.glob("[0-9a-f]*.parquet"))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"dataset": ""}, "dataset"),
        ({"key": ""}, "key"),
        ({"data_version": ""}, "data_version"),
        ({"path": "not-a-path"}, "path"),
        ({"path": Path("relative/a.parquet")}, "absolute"),
        ({"path": Path("D:/safe/wrong.parquet")}, "sha256|filename"),
    ],
)
def test_research_artifact_rejects_invalid_identity_and_path(
    overrides: dict[str, object], match: str
) -> None:
    values: dict[str, object] = {
        "dataset": "research_returns",
        "key": "000001",
        "data_version": "v1",
        "sha256": "a" * 64,
        "path": Path("D:/safe") / ("a" * 64 + ".parquet"),
        "row_count": 1,
        "size_bytes": 1,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "schema_fingerprint": "b" * 64,
    }
    values.update(overrides)
    with pytest.raises((TypeError, ValueError), match=match):
        ResearchArtifact(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("factor", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_corporate_action_requires_positive_finite_factor(factor: float) -> None:
    with pytest.raises(ValueError, match="adjustment_factor"):
        CorporateAction("000001", date(2026, 1, 5), "split", factor, "v1")


@pytest.mark.parametrize("symbol,name", [("", "name"), ("000001", ""), (" ", "name")])
def test_point_in_time_instrument_requires_identity(symbol: str, name: str) -> None:
    with pytest.raises(ValueError, match="symbol|name"):
        PointInTimeInstrument(symbol, name, date(2020, 1, 1), None, True)


def test_directory_fsync_failure_preserves_previous_active(research_temp: Path) -> None:
    store = store_at(research_temp, minimum_free_bytes=0)
    old = store.replace_dataset("research_returns", "000001", frame(1), "v1")

    def fail_directory_fsync(path: Path) -> None:
        if path == store.root_directory:
            raise OSError("directory fsync failed")

    store._directory_fsync = fail_directory_fsync
    with pytest.raises(OSError, match="directory fsync"):
        store.replace_dataset("research_returns", "000001", frame(20), "v2")

    store._directory_fsync = lambda _path: None
    assert store.active_artifact("research_returns", "000001") == old


def test_successful_publication_fsyncs_blob_and_manifest_directories(
    research_temp: Path,
) -> None:
    calls: list[Path] = []
    store = store_at(research_temp, minimum_free_bytes=0, directory_fsync=calls.append)
    store.replace_dataset("research_returns", "000001", frame(), "v1")
    assert store.blob_directory in calls
    assert store.root_directory in calls
