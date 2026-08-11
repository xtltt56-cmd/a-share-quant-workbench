from datetime import datetime, timezone
from pathlib import Path

import pytest

from a_share_quant.contracts.realtime import DataQualityStatus, RealTimeQuote
from a_share_quant.data.realtime.cache import RealtimeQuoteCache
from a_share_quant.workbench.service import WorkbenchService


def _quote(at: datetime, *, symbol: str = "000001") -> RealTimeQuote:
    return RealTimeQuote(
        symbol=symbol,
        market="A",
        timestamp_exchange=at,
        timestamp_received=at,
        last=10.0,
        open=9.8,
        high=10.2,
        low=9.7,
        previous_close=9.9,
        volume=100,
        amount=1000,
        source="AKShare / Sina",
    )


def test_realtime_cache_round_trip_marks_quotes_stale(tmp_path: Path) -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    cache = RealtimeQuoteCache(tmp_path / "quotes.json")

    cache.save((_quote(now),), saved_at=now)
    snapshot = cache.load(now=now)

    assert snapshot is not None
    assert snapshot.saved_at == now
    assert snapshot.quotes[0].is_stale is True
    assert snapshot.quotes[0].quality_flag is DataQualityStatus.STALE


def test_realtime_cache_rejects_tampering_and_future_quotes(tmp_path: Path) -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    path = tmp_path / "quotes.json"
    cache = RealtimeQuoteCache(path)
    cache.save((_quote(now),), saved_at=now)

    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace("000001", "000002"), encoding="utf-8")
    with pytest.raises(ValueError, match="cache"):
        cache.load(now=now)

    cache.save((_quote(now.replace(hour=11)),), saved_at=now.replace(hour=11))
    with pytest.raises(ValueError, match="future"):
        cache.load(now=now)


def test_realtime_cache_rejects_oversized_or_symlinked_files(tmp_path: Path) -> None:
    path = tmp_path / "quotes.json"
    path.write_text("{}", encoding="utf-8")
    cache = RealtimeQuoteCache(path, max_bytes=1)
    with pytest.raises(ValueError, match="cache"):
        cache.load()


def test_workbench_exposes_cache_as_stale_monitoring_state(tmp_path: Path) -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
    cache = RealtimeQuoteCache(tmp_path / "quotes.json")
    cache.save((_quote(now),), saved_at=now)

    class InjectedProvider:
        name = "injected"

    service = WorkbenchService(
        provider=InjectedProvider(),
        allow_network=False,
        clock=lambda: now,
        quote_cache=cache,
    )

    snapshot = service.snapshot()

    assert snapshot["evidence_mode"] == "CACHED"
    assert snapshot["data_quality"] == "STALE"
    assert snapshot["quotes"][0]["is_stale"] is True
    assert snapshot["intraday_monitor"]
