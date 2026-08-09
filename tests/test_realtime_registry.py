from datetime import datetime, timezone

from a_share_quant.contracts.realtime import (
    MarketSnapshot,
    ProviderHealth,
    ProviderMetadata,
    RealTimeQuote,
)
from a_share_quant.data.realtime.base import ProviderRequestError
from a_share_quant.data.realtime.registry import (
    FailoverRealTimeProvider,
    ProviderRegistry,
)


def _quote(source: str, *, stale: bool = False) -> RealTimeQuote:
    now = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)
    return RealTimeQuote(
        symbol="000001",
        name="平安银行",
        market="A",
        timestamp_exchange=now,
        timestamp_received=now,
        last=10.0,
        open=10.0,
        high=10.1,
        low=9.9,
        previous_close=10.0,
        volume=1,
        amount=10,
        bid1=None,
        ask1=None,
        change=0,
        change_pct=0,
        turnover_rate=None,
        source=source,
        quality_flag="STALE" if stale else "GOOD",
        is_stale=stale,
    )


class FakeProvider:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider=self.name,
            market="A",
            frequencies=("snapshot", "1m"),
            authenticated=self.name != "akshare",
            permissions=("snapshot",),
        )

    def health_check(self) -> ProviderHealth:
        if self.fail:
            raise ProviderRequestError(f"{self.name} unavailable")
        return ProviderHealth(
            provider=self.name,
            connected=True,
            authenticated=self.name != "akshare",
            permissions=("snapshot",),
            status="READY",
        )

    def get_market_snapshot(self) -> MarketSnapshot:
        if self.fail:
            raise ProviderRequestError(f"{self.name} snapshot failure")
        return MarketSnapshot(
            timestamp_exchange=_quote(self.name).timestamp_exchange,
            timestamp_received=_quote(self.name).timestamp_received,
            quotes=(_quote(self.name),),
            source=self.name,
            quality_flag="GOOD",
            is_stale=False,
        )

    def get_quotes(self, symbols: list[str]) -> tuple[RealTimeQuote, ...]:
        return tuple(_quote(self.name) for _ in symbols)

    def get_minute_bars(self, symbols: list[str], frequency: str):
        return ()

    def get_index_snapshot(self, symbols: list[str]):
        return self.get_quotes(symbols)

    def get_market_status(self):
        return "OPEN"


def test_realtime_provider_protocol_is_runtime_checkable_by_shape() -> None:
    provider = FakeProvider("fixture")

    registry = ProviderRegistry([("fixture", lambda: provider)])

    assert registry.discover()[0].provider == "fixture"
    assert registry.active_provider_name == "fixture"


def test_provider_registry_skips_unavailable_provider_without_exposing_error_payload() -> None:
    registry = ProviderRegistry(
        [
            ("bad", lambda: FakeProvider("bad", fail=True)),
            ("good", lambda: FakeProvider("good")),
        ]
    )

    capabilities = registry.discover()

    assert [item.provider for item in capabilities] == ["bad", "good"]
    assert registry.active_provider_name == "good"
    assert capabilities[0].status == "UNAVAILABLE"
    assert "unavailable" not in capabilities[0].message.lower()


def test_provider_failover_records_explicit_switch_event() -> None:
    primary = FakeProvider("primary", fail=True)
    secondary = FakeProvider("secondary")
    provider = FailoverRealTimeProvider([primary, secondary])

    snapshot = provider.get_market_snapshot()

    assert snapshot.source == "secondary"
    assert provider.active_provider_name == "secondary"
    assert len(provider.switch_events) == 1
    assert provider.switch_events[0].source_from == "primary"
    assert provider.switch_events[0].source_to == "secondary"
    assert provider.switch_events[0].reason == "ProviderRequestError"


def test_provider_health_is_available_without_logging_credentials() -> None:
    provider = FakeProvider("tushare")
    registry = ProviderRegistry([("tushare", lambda: provider)])

    capabilities = registry.discover()
    health = registry.health_report()

    assert capabilities[0].authenticated is True
    assert health[0].connected is True
    assert "token" not in str(health).lower()

