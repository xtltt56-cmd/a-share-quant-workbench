from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from a_share_quant.research.research_snapshot import (
    ResearchSnapshotBuilder,
    SnapshotIntegrityError,
)
from a_share_quant.storage.project_storage import ProjectStoragePolicy
from a_share_quant.storage.research_data_store import ResearchDataset, ResearchDataStore


@pytest.fixture
def snapshot_store() -> ResearchDataStore:
    workspace = Path(__file__).resolve().parents[1]
    root = workspace / ".runtime" / "temp" / f"pytest-research-snapshot-{uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield ResearchDataStore(
            ProjectStoragePolicy(root, required_drive=None),
            minimum_free_bytes=0,
        )
    finally:
        if root.exists():
            shutil.rmtree(root)


def _source_artifacts(store: ResearchDataStore):
    universe = pd.DataFrame(
        [
            {
                "symbol": "delisted-later",
                "name": "后来退市",
                "listed_date": "2018-01-01",
                "delisted_date": "2023-01-01",
                "as_of": "2020-01-01",
                "trade_status": "1",
                "is_st": False,
            },
            {
                "symbol": "listed-later",
                "name": "后来上市",
                "listed_date": "2022-01-01",
                "delisted_date": None,
                "as_of": "2022-01-01",
                "trade_status": "1",
                "is_st": False,
            },
            {
                "symbol": "unknown-status",
                "name": "状态未知",
                "listed_date": "2018-01-01",
                "delisted_date": None,
                "as_of": "2020-01-01",
                "trade_status": None,
                "is_st": False,
            },
            {
                "symbol": "known-st",
                "name": "风险股票",
                "listed_date": "2018-01-01",
                "delisted_date": None,
                "as_of": "2020-01-01",
                "trade_status": "1",
                "is_st": True,
            },
        ]
    )
    features = pd.DataFrame(
        [
            {
                "symbol": "delisted-later",
                "available_at": "2020-01-01",
                "momentum": 1.0,
            },
            {
                "symbol": "delisted-later",
                "available_at": "2021-01-01",
                "momentum": 2.0,
            },
            {
                "symbol": "unknown-status",
                "available_at": "2020-01-01",
                "momentum": 3.0,
            },
            {
                "symbol": "listed-later",
                "available_at": "2022-01-01",
                "momentum": 4.0,
            },
        ]
    )
    labels = pd.DataFrame(
        [
            {
                "symbol": "delisted-later",
                "label_date": "2020-01-08",
                "forward_return": 0.05,
            }
        ]
    )
    return (
        store.replace_dataset(
            ResearchDataset.INSTRUMENT_HISTORY,
            "fixture-universe",
            universe,
            "universe-v1",
        ),
        store.replace_dataset(
            ResearchDataset.RESEARCH_RETURNS,
            "fixture-features",
            features,
            "features-v1",
        ),
        store.replace_dataset(
            ResearchDataset.TRIAL_EVIDENCE,
            "fixture-labels",
            labels,
            "labels-v1",
        ),
    )


def _builder(store: ResearchDataStore, *, persist: bool = False):
    artifacts = _source_artifacts(store)
    return ResearchSnapshotBuilder(
        store,
        universe_artifact=artifacts[0],
        feature_artifact=artifacts[1],
        label_artifact=artifacts[2],
        universe_version="universe-v1",
        feature_version="features-v1",
        label_version="labels-v1",
        persist=persist,
    )


def test_snapshot_uses_only_information_visible_at_signal_date(
    snapshot_store: ResearchDataStore,
) -> None:
    snapshot = _builder(snapshot_store).build(signal_date=date(2021, 6, 30))

    assert snapshot.features["available_at"].le(date(2021, 6, 30)).all()
    assert "delisted-later" in snapshot.universe["symbol"].tolist()
    assert "listed-later" not in snapshot.universe["symbol"].tolist()
    assert snapshot.features.loc[
        snapshot.features["symbol"] == "delisted-later", "momentum"
    ].tolist() == [1.0, 2.0]


def test_unknown_st_or_trade_status_is_not_tradable(
    snapshot_store: ResearchDataStore,
) -> None:
    snapshot = _builder(snapshot_store).build(signal_date=date(2021, 6, 30))

    assert "unknown-status" not in snapshot.tradable_symbols
    assert snapshot.exclusions["unknown-status"] == "UNKNOWN_TRADE_STATUS"
    assert "known-st" not in snapshot.tradable_symbols
    assert snapshot.exclusions["known-st"] == "ST"


def test_snapshot_requires_verified_manifest_artifacts(
    snapshot_store: ResearchDataStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = _builder(snapshot_store)
    monkeypatch.setattr(snapshot_store, "verify", lambda _artifact: False)

    with pytest.raises(SnapshotIntegrityError, match="manifest"):
        builder.build(signal_date=date(2021, 6, 30))


def test_snapshot_fingerprint_changes_when_source_artifact_changes(
    snapshot_store: ResearchDataStore,
) -> None:
    first_artifacts = _source_artifacts(snapshot_store)
    first = ResearchSnapshotBuilder(
        snapshot_store,
        universe_artifact=first_artifacts[0],
        feature_artifact=first_artifacts[1],
        label_artifact=first_artifacts[2],
        universe_version="universe-v1",
        feature_version="features-v1",
        label_version="labels-v1",
    ).build(signal_date=date(2021, 6, 30))

    changed_features = pd.DataFrame(
        [
            {
                "symbol": "delisted-later",
                "available_at": "2020-01-01",
                "momentum": 9.0,
            }
        ]
    )
    changed = snapshot_store.replace_dataset(
        ResearchDataset.RESEARCH_RETURNS,
        "fixture-features",
        changed_features,
        "features-v2",
    )
    second = ResearchSnapshotBuilder(
        snapshot_store,
        universe_artifact=first_artifacts[0],
        feature_artifact=changed,
        label_artifact=first_artifacts[2],
        universe_version="universe-v1",
        feature_version="features-v2",
        label_version="labels-v1",
    ).build(signal_date=date(2021, 6, 30))

    assert first.canonical_sha256 != second.canonical_sha256
    assert first.features.loc[0, "momentum"] == 1.0
    assert second.features.loc[0, "momentum"] == 9.0


def test_persisted_snapshot_uses_governed_frozen_snapshot_dataset(
    snapshot_store: ResearchDataStore,
) -> None:
    snapshot = _builder(snapshot_store, persist=True).build(signal_date=date(2021, 6, 30))

    artifact = snapshot_store.active_artifact(
        ResearchDataset.FROZEN_SNAPSHOTS,
        f"snapshot-{snapshot.snapshot_id}",
    )
    assert snapshot_store.verify(artifact)
    assert artifact.path.drive.casefold() == "d:"
