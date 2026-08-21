from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import namedtuple
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from a_share_quant.contracts.data import CANONICAL_DAILY_COLUMNS, ProviderRequestError
from a_share_quant.runtime.historical_backfill import (
    CheckpointIntegrityError,
    HistoricalBackfillCoordinator,
)
from a_share_quant.storage.project_storage import ProjectStoragePolicy, StorageBoundaryError
from a_share_quant.storage.research_data_store import (
    ManifestIntegrityError,
    ResearchDataStore,
    StorageQuotaError,
)

DiskUsage = namedtuple("DiskUsage", "total used free")


@pytest.fixture
def d_backfill_root() -> Path:
    """Keep this regression's persisted data under the project D: runtime."""

    workspace = Path(__file__).resolve().parents[1]
    assert workspace.drive.casefold() == "d:"
    parent = workspace / ".runtime" / "temp"
    parent.mkdir(parents=True, exist_ok=True)
    root = parent / f"task8-backfill-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        lexical = Path(os.path.normpath(os.path.abspath(root)))
        assert lexical.parent == parent.resolve()
        if lexical.exists():
            shutil.rmtree(lexical)


def _instrument_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "600001",
                "name": "退市样例",
                "listed_date": date(1990, 1, 1),
                "delisted_date": date(2020, 1, 1),
                "status": "0",
                "data_version": "fixture-instruments-v1",
            },
            {
                "symbol": "600002",
                "name": "存续样例",
                "listed_date": date(1991, 1, 1),
                "delisted_date": None,
                "status": "1",
                "data_version": "fixture-instruments-v1",
            },
            {
                "symbol": "600003",
                "name": "恢复样例",
                "listed_date": date(1992, 1, 1),
                "delisted_date": None,
                "status": "1",
                "data_version": "fixture-instruments-v1",
            },
        ]
    )


def _history(symbol: str, start: date, end: date) -> pd.DataFrame:
    days = pd.date_range(start, end, freq="D")
    rows: list[dict[str, object]] = []
    for offset, day in enumerate(days):
        rows.append(
            {
                "symbol": symbol,
                "date": day.date(),
                "open": 10.0 + offset,
                "high": 11.0 + offset,
                "low": 9.0 + offset,
                "close": 10.5 + offset,
                "volume": 1000.0,
                "amount": 10500.0,
                "amplitude_pct": 2.0,
                "change_pct": 1.0,
                "change_amount": 0.1,
                "turnover_pct": 0.5,
                "trade_status": "1",
                "is_st": False,
                "tradable": True,
                "research_return": 0.01 if offset else float("nan"),
                "research_return_version": "fixture-return-v1",
                "research_usable": offset > 0,
                "unusable_reason": "" if offset else "missing previous adjusted close",
                "source": "fixture",
                "fetched_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
                "data_version": "fixture-unadjusted-v1",
            }
        )
    return pd.DataFrame(rows)


class RecordingHistoryProvider:
    name = "fixture"

    def __init__(self, *, fail_once_on: str | None = None) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.fail_once_on = fail_once_on

    def list_research_instruments(self, as_of=None) -> pd.DataFrame:
        self.calls.append(("instruments", as_of))
        return _instrument_frame()

    def get_research_history(self, symbol, start_date, end_date) -> pd.DataFrame:
        self.calls.append(("history", symbol, start_date, end_date))
        if symbol == self.fail_once_on:
            self.fail_once_on = None
            raise ProviderRequestError("permanent fixture failure")
        return _history(symbol, start_date, end_date)


def _store(tmp_path, *, free_bytes: int = 100 * 1024**3) -> ResearchDataStore:
    policy = ProjectStoragePolicy(tmp_path, required_drive=None)
    return ResearchDataStore(
        policy,
        minimum_free_bytes=20 * 1024**3,
        disk_usage=lambda _path: DiskUsage(200 * 1024**3, 100 * 1024**3, free_bytes),
    )


def _coordinator(tmp_path, provider=None, **kwargs) -> HistoricalBackfillCoordinator:
    return HistoricalBackfillCoordinator(
        _store(tmp_path),
        provider or RecordingHistoryProvider(),
        clock=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
        sleeper=lambda _seconds: None,
        delay_seconds=0,
        **kwargs,
    )


def test_backfill_resumes_only_missing_symbols_and_keeps_delisted_symbols(tmp_path) -> None:
    provider = RecordingHistoryProvider()
    provider.list_research_instruments = lambda as_of=None: _instrument_frame().iloc[:2].copy()
    coordinator = _coordinator(tmp_path, provider)

    first = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=2)
    history_calls_after_first = [call for call in provider.calls if call[0] == "history"]
    second = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=2)

    assert first.symbols_updated == 2
    assert first.rows_written == 4
    assert second.rows_written == 0
    assert [call for call in provider.calls if call[0] == "history"] == history_calls_after_first
    assert "600001" in coordinator.coverage().symbols


def test_backfill_can_bound_each_provider_date_request_for_large_history(tmp_path) -> None:
    provider = RecordingHistoryProvider()
    provider.list_research_instruments = lambda as_of=None: _instrument_frame().iloc[:1].copy()
    coordinator = _coordinator(tmp_path, provider, max_request_days=2)

    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 5), limit=1)
    first_calls = [call for call in provider.calls if call[0] == "history"]
    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 5), limit=1)
    second_calls = [call for call in provider.calls if call[0] == "history"]

    assert first_calls == [("history", "600001", date(2019, 1, 1), date(2019, 1, 2))]
    assert second_calls[-1] == ("history", "600001", date(2019, 1, 3), date(2019, 1, 4))


def test_backfill_stops_before_any_provider_call_when_d_drive_space_is_low(tmp_path) -> None:
    provider = RecordingHistoryProvider()
    coordinator = HistoricalBackfillCoordinator(
        _store(tmp_path, free_bytes=1024),
        provider,
        sleeper=lambda _seconds: None,
    )

    with pytest.raises(StorageQuotaError, match="D盘.*剩余空间|D盘.*空闲空间"):
        coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2))

    assert provider.calls == []


def test_backfill_limits_one_batch_to_at_most_one_hundred_symbols(tmp_path) -> None:
    coordinator = _coordinator(tmp_path)

    with pytest.raises(ValueError, match="100"):
        coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=101)


def test_checkpoint_is_hashed_after_each_completed_symbol_and_resumes_after_failure(
    tmp_path,
) -> None:
    provider = RecordingHistoryProvider(fail_once_on="600003")
    coordinator = _coordinator(tmp_path, provider)

    result = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=3)
    checkpoint = json.loads(coordinator.checkpoint_path.read_text(encoding="utf-8"))
    digest = checkpoint.pop("sha256")
    expected = hashlib.sha256(
        json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    assert result.symbols_updated == 2
    assert result.failures == {"600003": "PERMANENT_PROVIDER_FAILURE"}
    assert digest == expected
    assert set(checkpoint["symbols"]) == {"600001", "600002"}

    resumed = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=3)
    assert resumed.symbols_updated == 1
    assert [call[1] for call in provider.calls if call[0] == "history"].count("600001") == 1


def test_corrupt_checkpoint_fails_closed(tmp_path) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)
    payload = json.loads(coordinator.checkpoint_path.read_text(encoding="utf-8"))
    payload["symbols"]["600001"]["rows"] = 999
    coordinator.checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointIntegrityError, match="检查点"):
        coordinator.coverage()


def test_checkpoint_that_references_a_damaged_artifact_propagates_integrity_error(
    tmp_path,
) -> None:
    coordinator = _coordinator(tmp_path)
    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)
    artifact = coordinator.store.active_artifact("research_returns", "600001")
    artifact.path.write_bytes(b"damaged")

    with pytest.raises(ManifestIntegrityError, match="blob"):
        coordinator.coverage()


def test_storage_quota_error_during_publication_stops_the_batch(tmp_path, monkeypatch) -> None:
    coordinator = _coordinator(tmp_path)
    original = coordinator.store.replace_dataset

    def replace(dataset, key, frame, data_version):
        if str(dataset.value if hasattr(dataset, "value") else dataset) == "research_returns":
            raise StorageQuotaError("研究数据总量超过配额")
        return original(dataset, key, frame, data_version)

    monkeypatch.setattr(coordinator.store, "replace_dataset", replace)

    with pytest.raises(StorageQuotaError, match="超过配额"):
        coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)


@pytest.mark.parametrize(
    "failure",
    [
        ManifestIntegrityError("manifest damaged"),
        StorageBoundaryError("path escaped"),
        OSError("checkpoint disk failure"),
    ],
)
def test_storage_and_integrity_failures_are_never_reported_as_provider_failures(
    tmp_path, monkeypatch, failure
) -> None:
    coordinator = _coordinator(tmp_path)
    original = coordinator.store.replace_dataset

    def replace(dataset, key, frame, data_version):
        if str(dataset.value if hasattr(dataset, "value") else dataset) == "research_returns":
            raise failure
        return original(dataset, key, frame, data_version)

    monkeypatch.setattr(coordinator.store, "replace_dataset", replace)

    with pytest.raises(type(failure), match=str(failure)):
        coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)


def test_expanding_range_requests_only_missing_edges_and_merges_last_valid_artifact(
    tmp_path,
) -> None:
    provider = RecordingHistoryProvider()
    instruments = _instrument_frame().iloc[:1].copy()
    provider.list_research_instruments = lambda as_of=None: instruments
    coordinator = _coordinator(tmp_path, provider)

    coordinator.run(start=date(2019, 1, 2), end=date(2019, 1, 3), limit=1)
    provider.calls.clear()
    result = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 4), limit=1)

    assert [call[2:] for call in provider.calls if call[0] == "history"] == [
        (date(2019, 1, 1), date(2019, 1, 1)),
        (date(2019, 1, 4), date(2019, 1, 4)),
    ]
    assert result.rows_written == 2
    artifact = coordinator.store.active_artifact("research_returns", "600001")
    assert coordinator.store.verify(artifact)
    assert list(pd.read_parquet(artifact.path)["date"]) == [
        date(2019, 1, 1),
        date(2019, 1, 2),
        date(2019, 1, 3),
        date(2019, 1, 4),
    ]


def test_task8_forward_history_chunks_preserve_verified_coverage(
    d_backfill_root: Path,
) -> None:
    """A later bounded chunk must merge instead of rejecting earlier rows."""

    provider = RecordingHistoryProvider()
    instruments = _instrument_frame().iloc[:1].copy()
    provider.list_research_instruments = lambda as_of=None: instruments
    coordinator = _coordinator(d_backfill_root, provider)

    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)
    first_coverage = coordinator.coverage()
    provider.calls.clear()

    second = coordinator.run(start=date(2019, 1, 3), end=date(2019, 1, 4), limit=1)
    second_coverage = coordinator.coverage()

    assert second.failures == {}
    assert second.rows_written == 2
    assert second_coverage.session_count > first_coverage.session_count
    assert [call[2:] for call in provider.calls if call[0] == "history"] == [
        (date(2019, 1, 3), date(2019, 1, 4)),
    ]
    artifact = coordinator.store.active_artifact("research_returns", "600001")
    assert coordinator.store.verify(artifact)
    assert list(pd.read_parquet(artifact.path)["date"]) == [
        date(2019, 1, 1),
        date(2019, 1, 2),
        date(2019, 1, 3),
        date(2019, 1, 4),
    ]

    provider.calls.clear()
    complete = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 4), limit=1)

    assert complete.rows_written == 0
    assert [call for call in provider.calls if call[0] == "history"] == []


def test_coverage_validates_manifest_once_for_many_symbols(
    d_backfill_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingHistoryProvider()
    coordinator = _coordinator(d_backfill_root, provider)
    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=3)

    calls = 0
    original = coordinator.store._load_records

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(coordinator.store, "_load_records", counted)

    coverage = coordinator.coverage()

    assert coverage.symbol_count == 3
    assert calls == 1


def test_empty_leading_calendar_gap_does_not_block_forward_resume(
    d_backfill_root: Path,
) -> None:
    class HolidayProvider(RecordingHistoryProvider):
        def get_research_history(self, symbol, start_date, end_date):
            frame = super().get_research_history(symbol, start_date, end_date)
            return frame.loc[frame["date"] != date(2019, 1, 1)].reset_index(drop=True)

    provider = HolidayProvider()
    provider.list_research_instruments = lambda as_of=None: _instrument_frame().iloc[:1].copy()
    coordinator = _coordinator(d_backfill_root, provider, max_request_days=2)

    first = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)
    provider.calls.clear()
    second = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 4), limit=1)
    provider.calls.clear()
    third = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 4), limit=1)

    assert first.rows_written == 1
    assert second.failures == {}
    assert third.rows_written == 2
    assert [call[2:] for call in provider.calls if call[0] == "history"] == [
        (date(2019, 1, 3), date(2019, 1, 4)),
    ]


def test_retries_only_bounded_transient_failures_and_applies_configured_delays(
    tmp_path,
) -> None:
    class TransientProvider(RecordingHistoryProvider):
        def __init__(self) -> None:
            super().__init__()
            self.remaining_failures = 2

        def get_research_history(self, symbol, start_date, end_date):
            self.calls.append(("history", symbol, start_date, end_date))
            if self.remaining_failures:
                self.remaining_failures -= 1
                try:
                    raise TimeoutError("temporary")
                except TimeoutError as exc:
                    raise ProviderRequestError("transport failed") from exc
            return _history(symbol, start_date, end_date)

    provider = TransientProvider()
    sleeps: list[float] = []
    coordinator = HistoricalBackfillCoordinator(
        _store(tmp_path),
        provider,
        retry_count=2,
        delay_seconds=0.25,
        sleeper=sleeps.append,
    )

    result = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)

    assert result.symbols_updated == 1
    assert len([call for call in provider.calls if call[0] == "history"]) == 3
    assert sleeps == [0.25, 0.25, 0.5]


def test_provider_validation_error_is_permanent_and_is_not_retried(tmp_path) -> None:
    class InvalidProvider(RecordingHistoryProvider):
        def get_research_history(self, symbol, start_date, end_date):
            self.calls.append(("history", symbol, start_date, end_date))
            raise ProviderRequestError("response validation failed")

    provider = InvalidProvider()
    coordinator = _coordinator(tmp_path, provider, retry_count=3)

    result = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)

    assert result.failures == {"600001": "PERMANENT_PROVIDER_FAILURE"}
    assert len([call for call in provider.calls if call[0] == "history"]) == 1


def test_reconciles_manifest_after_interrupt_between_publish_and_checkpoint(
    tmp_path, monkeypatch
) -> None:
    provider = RecordingHistoryProvider()
    provider.list_research_instruments = lambda as_of=None: _instrument_frame().iloc[:1].copy()
    coordinator = _coordinator(tmp_path, provider)
    coordinator.run(start=date(2019, 1, 2), end=date(2019, 1, 3), limit=1)
    original_write = coordinator._write_checkpoint
    monkeypatch.setattr(
        coordinator,
        "_write_checkpoint",
        lambda _payload: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(KeyboardInterrupt):
        coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 4), limit=1)

    monkeypatch.setattr(coordinator, "_write_checkpoint", original_write)
    history_calls = [call for call in provider.calls if call[0] == "history"]
    resumed = coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 4), limit=1)

    assert resumed.rows_written == 0
    assert [call for call in provider.calls if call[0] == "history"] == history_calls
    assert coordinator.coverage().row_count == 4


def test_checkpoint_replace_fsyncs_parent_directory(tmp_path) -> None:
    fsynced = []
    coordinator = HistoricalBackfillCoordinator(
        _store(tmp_path),
        RecordingHistoryProvider(),
        sleeper=lambda _seconds: None,
        delay_seconds=0,
        directory_fsync=fsynced.append,
    )

    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)

    assert fsynced
    assert set(fsynced) == {coordinator.checkpoint_path.parent}


def test_space_is_rechecked_immediately_before_each_provider_attempt(tmp_path) -> None:
    provider = RecordingHistoryProvider()
    usage_calls = 0

    def disk_usage(_path):
        nonlocal usage_calls
        usage_calls += 1
        free = 100 * 1024**3 if usage_calls <= 2 else 1024
        return DiskUsage(200 * 1024**3, 100 * 1024**3, free)

    coordinator = HistoricalBackfillCoordinator(
        _store(tmp_path),
        provider,
        sleeper=lambda _seconds: None,
        delay_seconds=0,
        disk_usage=disk_usage,
    )

    with pytest.raises(StorageQuotaError, match="D盘.*空间"):
        coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)

    assert [call[0] for call in provider.calls] == ["instruments"]


def test_space_is_rechecked_before_a_provider_retry(tmp_path) -> None:
    class TimeoutProvider(RecordingHistoryProvider):
        def get_research_history(self, symbol, start_date, end_date):
            self.calls.append(("history", symbol, start_date, end_date))
            raise TimeoutError("temporary")

    provider = TimeoutProvider()
    usage_calls = 0

    def disk_usage(_path):
        nonlocal usage_calls
        usage_calls += 1
        free = 100 * 1024**3 if usage_calls <= 3 else 1024
        return DiskUsage(200 * 1024**3, 100 * 1024**3, free)

    coordinator = HistoricalBackfillCoordinator(
        _store(tmp_path),
        provider,
        sleeper=lambda _seconds: None,
        delay_seconds=0,
        disk_usage=disk_usage,
        retry_count=3,
    )

    with pytest.raises(StorageQuotaError, match="D盘.*空间"):
        coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)

    assert len([call for call in provider.calls if call[0] == "history"]) == 1


def test_coverage_reports_rows_sessions_dates_status_bytes_and_exclusions(tmp_path) -> None:
    provider = RecordingHistoryProvider()

    def incomplete_history(symbol, start_date, end_date):
        frame = _history(symbol, start_date, end_date)
        frame.loc[0, "trade_status"] = ""
        frame.loc[0, "research_usable"] = False
        frame.loc[0, "unusable_reason"] = "invalid history status"
        return frame

    provider.get_research_history = incomplete_history
    coordinator = _coordinator(tmp_path, provider)
    coordinator.run(start=date(2019, 1, 1), end=date(2019, 1, 2), limit=1)

    coverage = coordinator.coverage()
    assert coverage.row_count == 2
    assert coverage.session_count == 2
    assert coverage.earliest_date == date(2019, 1, 1)
    assert coverage.latest_date == date(2019, 1, 2)
    assert coverage.size_bytes > 0
    assert coverage.unavailable_status_fields == {"600001": ("trade_status",)}
    assert coverage.exclusion_reasons["600001"] == ("invalid history status",)
    assert set(CANONICAL_DAILY_COLUMNS).issubset(set(pd.read_parquet(
        coordinator.store.active_artifact("research_returns", "600001").path
    ).columns))
