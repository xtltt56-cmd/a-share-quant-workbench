from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import namedtuple
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

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
        path=Path("blobs/a.parquet"),
        row_count=2,
        size_bytes=10,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
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
    orphan = store.blob_directory / ("f" * 64 + ".parquet")
    orphan.write_bytes(b"orphan")
    stage = store.staging_directory / f".research-stage-{uuid4().hex}.parquet"
    stage.write_bytes(b"partial")

    dry = store.cleanup_rebuildable_temporary_files(grace_seconds=0, dry_run=True)
    assert orphan in dry.candidates and stage in dry.candidates
    assert orphan.exists() and stage.exists()
    report = store.cleanup_rebuildable_temporary_files(grace_seconds=0)
    assert orphan in report.removed and stage in report.removed
    assert artifact.path.exists() and store.manifest_path.exists() and unknown.exists()


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
