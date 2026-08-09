from datetime import datetime
from zoneinfo import ZoneInfo

from a_share_quant.contracts.realtime import MarketSnapshot, RealTimeQuote
from a_share_quant.data.realtime.registry import ProviderRegistry
from a_share_quant.runtime.scheduler import MarketHours, SessionResolver, StaticTradingCalendar
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


def test_workbench_service_exposes_paper_only_state_and_sanitized_snapshot() -> None:
    now = datetime(2026, 8, 10, 10, 0, tzinfo=TZ)
    resolver = SessionResolver(
        hours=MarketHours(),
        calendar=StaticTradingCalendar({now.date()}),
    )
    service = WorkbenchService(
        provider=_provider(now),
        allow_network=True,
        resolver=resolver,
        clock=lambda: now,
    )

    state = service.refresh().to_dict()

    assert state["active_provider"] == "replay"
    assert state["data_quality"] == "GOOD"
    assert state["paper_only"] is True
    assert state["live_trading_enabled"] is False
    assert state["official_daily_candidates"] == []
    assert state["intraday_monitor"][0]["state"] == "WAIT"
    assert state["intraday_monitor"][0]["data_age_seconds"] == 0.0


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
