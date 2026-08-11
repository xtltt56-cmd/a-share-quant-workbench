from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from a_share_quant.contracts.realtime import DataQualityStatus, MarketSnapshot, RealTimeQuote
from a_share_quant.data.realtime.registry import ProviderRegistry
from a_share_quant.runtime.scheduler import MarketHours, SessionResolver, StaticTradingCalendar
from a_share_quant.signals.realtime import OfficialModelSignal
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.workbench.service import WorkbenchService

TZ = ZoneInfo("Asia/Shanghai")


class FakeProvider:
    name = "replay"

    def __init__(self, snapshot: MarketSnapshot) -> None:
        self.snapshot = snapshot

    def get_market_snapshot(self):
        return self.snapshot

    def get_quotes(self, symbols):
        return self.snapshot.quotes

    def get_minute_bars(self, symbols, frequency):
        return ()


def _provider(now: datetime) -> FakeProvider:
    quote = RealTimeQuote(
        symbol="000001",
        market="A",
        timestamp_exchange=now,
        timestamp_received=now,
        last=10.2,
        previous_close=10.0,
        open=10.1,
        high=10.3,
        low=10.0,
        volume=100,
        amount=1020,
        change_pct=2.0,
        source="replay",
    )
    return FakeProvider(
        MarketSnapshot(
            timestamp_exchange=now,
            timestamp_received=now,
            quotes=(quote,),
            source="replay",
        )
    )


def _live_provider(now: datetime) -> FakeProvider:
    quote = RealTimeQuote(
        symbol="000001",
        market="A",
        timestamp_exchange=now,
        timestamp_received=now,
        last=10.2,
        previous_close=10.0,
        open=10.1,
        high=10.3,
        low=10.0,
        volume=100,
        amount=1020,
        change_pct=2.0,
        source="akshare",
    )
    provider = FakeProvider(
        MarketSnapshot(
            timestamp_exchange=now,
            timestamp_received=now,
            quotes=(quote,),
            source="akshare",
        )
    )
    provider.name = "akshare"
    return provider


def test_workbench_service_exposes_paper_only_state_and_sanitized_snapshot() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    official_store = OfficialSignalStore()
    official_store.put_signals(
        [
            OfficialModelSignal(
                signal_date=now.date(),
                symbol="000001",
                normalized_score=82.5,
                strategy_version="stage2-v1",
            )
        ]
    )
    service = WorkbenchService(
        provider=_provider(now),
        official_signal_store=official_store,
        allow_network=True,
        resolver=resolver,
        clock=lambda: now,
    )

    state = service.refresh().to_dict()

    assert state["active_provider"] == "replay"
    assert state["active_source"] == "Replay / Test Data"
    assert state["source_class"] == "REPLAY / NON-MARKET"
    assert state["evidence_mode"] == "REPLAY"
    assert state["data_quality"] == "REPLAY"
    assert state["continuous_updates"] is False
    assert state["paper_only"] is True
    assert state["live_trading_enabled"] is False
    assert state["official_daily_candidates"][0]["normalized_score"] == 82.5
    assert state["intraday_monitor"][0]["state"] == "READY"
    assert state["intraday_monitor"][0]["official_model_signal"] is True
    assert state["intraday_monitor"][0]["quote_timestamp"] == now.isoformat()
    assert "BUY" not in str(state["intraday_monitor"][0])
    assert state["intraday_monitor"][0]["data_age_seconds"] == 0.0


def test_workbench_blocks_ready_when_global_quality_is_not_good() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    official_store = OfficialSignalStore()
    official_store.put_signals(
        [
            OfficialModelSignal(
                signal_date=now.date(),
                symbol="000001",
                normalized_score=82.5,
                strategy_version="stage2-v1",
            )
        ]
    )
    service = WorkbenchService(
        provider=_live_provider(now),
        official_signal_store=official_store,
        allow_network=False,
        clock=lambda: now,
    )

    service._apply_quote_state(
        service.provider.snapshot.quotes,
        now=now,
        data_quality=DataQualityStatus.FAILED,
    )

    monitor = service.snapshot()["intraday_monitor"][0]
    assert monitor["state"] == "STALE_DATA"
    assert monitor["data_quality"] == "FAILED"


def test_workbench_requires_two_live_updates_before_exposing_good_quality() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    current = [now]
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    provider = _live_provider(now)
    service = WorkbenchService(
        provider=provider,
        allow_network=True,
        resolver=resolver,
        clock=lambda: current[0],
        monotonic_clock=lambda: 1.0,
    )

    first = service.refresh().to_dict()
    later = now + timedelta(seconds=15)
    current[0] = later
    provider.snapshot = _live_provider(later).snapshot
    second = service.refresh().to_dict()

    assert first["active_source"] == "AKShare / Eastmoney"
    assert first["source_class"] == "PUBLIC DATA SOURCE"
    assert first["evidence_mode"] == "REAL_MARKET"
    assert first["data_quality"] == "DEGRADED"
    assert first["continuous_updates"] is False
    assert second["data_quality"] == "GOOD"
    assert second["continuous_updates"] is True
    assert second["provider_telemetry"]["quote_count"] == 2
    assert second["intraday_monitor"][0]["official_model_signal"] is False


def test_workbench_keeps_monitor_non_ready_when_scheduler_quarantines_partial_snapshot() -> None:
    first_time = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    second_time = first_time + timedelta(seconds=15)
    first_one = _live_provider(first_time).snapshot.quotes[0]
    first_two = replace(first_one, symbol="000002", last=20.0)
    second_one = replace(
        first_one,
        timestamp_exchange=first_time - timedelta(seconds=1),
        timestamp_received=second_time,
        last=10.3,
    )
    second_two = replace(
        first_two,
        timestamp_exchange=second_time,
        timestamp_received=second_time,
        last=20.3,
    )
    first_snapshot = MarketSnapshot(
        timestamp_exchange=first_time,
        timestamp_received=first_time,
        quotes=(first_one, first_two),
        source="akshare",
    )
    second_snapshot = MarketSnapshot(
        timestamp_exchange=second_time,
        timestamp_received=second_time,
        quotes=(second_one, second_two),
        source="akshare",
    )
    snapshots = iter((first_snapshot, second_snapshot))
    provider = FakeProvider(first_snapshot)
    provider.name = "akshare"
    provider.get_market_snapshot = lambda: next(snapshots)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({first_time.date()}),
    )
    current = [first_time]
    service = WorkbenchService(
        provider=provider,
        allow_network=True,
        resolver=resolver,
        clock=lambda: current[0],
    )

    service.refresh()
    current[0] = second_time
    second = service.refresh().to_dict()

    assert second["data_quality"] == "DEGRADED"
    assert second["continuous_updates"] is False
    assert second["intraday_monitor"][0]["state"] == "STALE_DATA"


def test_workbench_reports_the_actual_akshare_snapshot_endpoint() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    provider = _live_provider(now)
    provider.active_source_name = "AKShare / Tencent"
    service = WorkbenchService(
        provider=provider,
        allow_network=True,
        resolver=resolver,
        clock=lambda: now,
    )

    state = service.refresh().to_dict()

    assert state["active_provider"] == "akshare"
    assert state["active_source"] == "AKShare / Tencent"


def test_workbench_never_labels_a_non_requested_session_as_real_market() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar(()),
    )
    service = WorkbenchService(
        provider=_live_provider(now),
        allow_network=True,
        resolver=resolver,
        clock=lambda: now,
    )

    state = service.refresh().to_dict()

    assert state["evidence_mode"] == "OFFLINE"
    assert state["data_quality"] == "FAILED"


def test_workbench_offline_mode_does_not_call_provider() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    provider = _provider(now)
    called = False
    original = provider.get_market_snapshot

    def fail_if_called():
        nonlocal called
        called = True
        return original()

    provider.get_market_snapshot = fail_if_called
    service = WorkbenchService(provider=provider, allow_network=False, clock=lambda: now)

    state = service.refresh().to_dict()

    assert called is False
    assert state["last_error"] == "OFFLINE_MODE"
    assert state["paper_only"] is True


def test_workbench_without_capable_provider_stays_unavailable() -> None:
    def unavailable():
        raise RuntimeError("provider secret payload")

    registry = ProviderRegistry([("unavailable", unavailable)])
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    service = WorkbenchService(
        registry=registry,
        allow_network=True,
        clock=lambda: now,
    )

    state = service.refresh().to_dict()

    assert service.provider is None
    assert service.health()["status"] == "DATA_UNAVAILABLE"
    assert state["last_error"] == "NO_PROVIDER"
    assert "secret payload" not in str(state)


def test_workbench_exposes_persisted_daily_candidates_without_realtime_quotes() -> None:
    now = datetime(2026, 8, 12, 9, 0, tzinfo=TZ)
    official_store = OfficialSignalStore()
    official_store.put_signals(
        [
            OfficialModelSignal(
                signal_date=datetime(2026, 8, 7).date(),
                symbol="600000",
                name="浦发银行",
                normalized_score=88,
                strategy_version="initial-free-data-v1",
                model_version="rule-none-v1",
                feature_version="rule-features-v1",
                source="baostock",
                rank=1,
                reasons=("收盘价位于60日均线上方",),
                reference_price=9.21,
                average_amount=520_000_000,
                invalidation_price=8.75,
            )
        ]
    )

    service = WorkbenchService(
        provider=_provider(now),
        official_signal_store=official_store,
        allow_network=False,
        clock=lambda: now,
    )
    state = service.snapshot()

    assert state["official_daily_candidates"][0]["name"] == "浦发银行"
    assert state["official_daily_candidates"][0]["data_mode"] == "historical"
    assert state["official_daily_candidates"][0]["signal_stale"] is True
    assert state["intraday_monitor"][0]["symbol"] == "600000"
    assert state["intraday_monitor"][0]["state"] == "STALE_DATA"
    assert state["intraday_monitor"][0]["data_quality"] == "FAILED"
    assert state["intraday_monitor"][0]["current_price"] is None


def test_workbench_prioritizes_official_candidates_in_realtime_monitor() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    ordinary = _provider(now).snapshot.quotes[0]
    candidate_quote = RealTimeQuote(
        symbol="600000",
        market="A",
        timestamp_exchange=now,
        timestamp_received=now,
        last=9.25,
        previous_close=9.21,
        open=9.22,
        high=9.30,
        low=9.18,
        volume=100,
        amount=925,
        change_pct=0.43,
        source="akshare",
    )
    provider = FakeProvider(
        MarketSnapshot(
            timestamp_exchange=now,
            timestamp_received=now,
            quotes=(ordinary, candidate_quote),
            source="akshare",
        )
    )
    provider.name = "akshare"
    official_store = OfficialSignalStore()
    official_store.put_signals(
        [
            OfficialModelSignal(
                signal_date=datetime(2026, 8, 7).date(),
                symbol="600000",
                normalized_score=88,
                strategy_version="initial-free-data-v1",
                source="baostock",
            )
        ]
    )
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    service = WorkbenchService(
        provider=provider,
        official_signal_store=official_store,
        allow_network=True,
        resolver=resolver,
        clock=lambda: now,
    )

    state = service.refresh().to_dict()

    assert state["intraday_monitor"][0]["symbol"] == "600000"
    assert state["intraday_monitor"][0]["official_model_signal"] is True
