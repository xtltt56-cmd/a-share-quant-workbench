from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from a_share_quant.account.import_inbox import ImportedPosition
from a_share_quant.account.snapshot_store import AccountSnapshotStore, ImportedAccountSnapshot


def _snapshot(*, source_sha256: str = "a" * 64) -> ImportedAccountSnapshot:
    return ImportedAccountSnapshot(
        snapshot_id="snapshot-1",
        source_sha256=source_sha256,
        source_name="持仓.csv",
        as_of=date(2026, 8, 11),
        imported_at=datetime(2026, 8, 11, 3, tzinfo=timezone.utc),
        cash=Decimal("88000.50"),
        positions=(
            ImportedPosition(
                symbol="000001",
                name="平安银行",
                total_quantity=300,
                available_quantity=200,
                frozen_quantity=100,
                average_cost=Decimal("10.1234"),
            ),
        ),
    )


def test_snapshot_store_round_trips_canonical_snapshot(tmp_path) -> None:
    path = tmp_path / "imported-account-snapshot.json"
    snapshot = _snapshot()
    store = AccountSnapshotStore(
        path,
        now=lambda: datetime(2026, 8, 11, 4, tzinfo=timezone.utc),
    )

    store.save(snapshot)

    assert store.load() == snapshot


def test_snapshot_store_rejects_tampering_and_non_finite_values(tmp_path) -> None:
    path = tmp_path / "imported-account-snapshot.json"
    store = AccountSnapshotStore(path, now=lambda: datetime(2026, 8, 11, 4, tzinfo=timezone.utc))
    store.save(_snapshot())
    payload = path.read_text(encoding="utf-8").replace("88000.50", "1.00")
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot integrity validation failed"):
        store.load()

    with pytest.raises(ValueError, match="cash must be finite"):
        store.save(
            _snapshot().__class__(
                snapshot_id="snapshot-2",
                source_sha256="b" * 64,
                source_name="持仓.csv",
                as_of=date(2026, 8, 11),
                imported_at=datetime(2026, 8, 11, 3, tzinfo=timezone.utc),
                cash=Decimal("NaN"),
                positions=_snapshot().positions,
            )
        )


def test_snapshot_store_rejects_future_import_and_duplicate_symbols(tmp_path) -> None:
    path = tmp_path / "snapshot.json"
    now = datetime(2026, 8, 11, 4, tzinfo=timezone.utc)
    store = AccountSnapshotStore(path, now=lambda: now)
    future = _snapshot().__class__(
        snapshot_id="future",
        source_sha256="a" * 64,
        source_name="持仓.csv",
        as_of=date(2026, 8, 11),
        imported_at=now + timedelta(minutes=6),
        cash=Decimal("1"),
        positions=_snapshot().positions,
    )
    with pytest.raises(ValueError, match="future"):
        store.save(future)

    with pytest.raises(ValueError, match="duplicate position"):
        _snapshot().__class__(
            snapshot_id="duplicate",
            source_sha256="a" * 64,
            source_name="持仓.csv",
            as_of=date(2026, 8, 11),
            imported_at=now,
            cash=Decimal("1"),
            positions=_snapshot().positions * 2,
        )


def test_snapshot_store_does_not_follow_linked_destination(tmp_path) -> None:
    destination = tmp_path / "snapshot.json"
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    try:
        destination.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"link creation unavailable: {exc}")
    store = AccountSnapshotStore(destination)

    with pytest.raises(ValueError, match="snapshot path is not safe"):
        store.save(_snapshot())
    assert outside.read_text(encoding="utf-8") == "keep"


def test_snapshot_store_cleans_temporary_file_when_fsync_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "snapshot.json"
    store = AccountSnapshotStore(path)
    original_fsync = __import__("os").fsync

    def fail_fsync(fd: int) -> None:
        original_fsync(fd)
        raise OSError("injected fsync failure")

    monkeypatch.setattr("a_share_quant.account.snapshot_store.os.fsync", fail_fsync)

    with pytest.raises(ValueError, match="account snapshot cannot be written"):
        store.save(_snapshot())
    assert not path.exists()
    assert list(tmp_path.glob(".snapshot.json.*.tmp")) == []
