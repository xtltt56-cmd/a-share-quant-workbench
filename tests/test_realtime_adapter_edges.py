import importlib
import sys
import types
from datetime import datetime, timezone

import pandas as pd
import pytest

from a_share_quant.contracts import (
    DataQualityStatus,
    MarketSnapshot,
    ProviderCapability,
    ProviderHealth,
    ProviderMetadata,
    ProviderSwitchEvent,
    RealTimeQuote,
)
from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.contracts.realtime import MinuteBar
from a_share_quant.data.realtime.akshare import AKShareRealTimeProvider
from a_share_quant.data.realtime.normalization import (
    normalize_minute_bars,
    normalize_realtime_quotes,
)
from a_share_quant.data.realtime.replay import ReplayRealTimeProvider
from a_share_quant.data.realtime.rqdata import RQDataRealTimeProvider
from a_share_quant.data.realtime.tushare import TushareRealTimeProvider

NOW = datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc)


def _minute_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_time": "2026-08-08 11:29:00",
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "vol": 100,
                "amount": 1010,
            }
        ]
    )


def _bar() -> MinuteBar:
    return MinuteBar(
        symbol="000001",
        timestamp=NOW,
        frequency="1m",
        open=10,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=100,
        amount=1010,
        source="replay",
        is_final=True,
    )


def test_normalization_rejects_non_frames_and_skips_malformed_rows() -> None:
    with pytest.raises(ValueError, match="DataFrame"):
        normalize_realtime_quotes([], source="test", received_at=NOW)  # type: ignore[arg-type]

    raw = pd.DataFrame(
        [
            {"代码": "not-a-symbol", "最新价": 10},
            {"代码": "000001", "最新价": 10, "成交量": -1},
            {"代码": "000002", "最新价": 0},
        ]
    )

    assert normalize_realtime_quotes(raw, source="test", received_at=NOW) == ()

    with pytest.raises(ValueError, match="DataFrame"):
        normalize_minute_bars([], symbol="000001", frequency="1m", source="test", received_at=NOW)  # type: ignore[arg-type]


def test_normalization_marks_invalid_timestamp_and_minute_schema_rows() -> None:
    quote = normalize_realtime_quotes(
        pd.DataFrame([{"代码": "000001", "最新价": 10, "时间": "not-a-date"}]),
        source="test",
        received_at=NOW,
    )[0]
    bars = normalize_minute_bars(
        pd.DataFrame(
            [
                {
                    "时间": "2026-08-08 11:29:00",
                    "开盘": 10,
                    "最高": 10.2,
                    "最低": 9.9,
                    "收盘": 10.1,
                    "成交量": 100,
                },
                {
                    "时间": "2026-08-08 11:29:00",
                    "开盘": 10,
                    "最高": 10.2,
                    "最低": 9.9,
                    "收盘": 10.1,
                    "成交量": 100,
                    "成交额": 1010,
                },
            ]
        ),
        symbol="000001",
        frequency="1m",
        source="test",
        received_at=NOW,
    )

    assert quote.quality_flag.value == "DEGRADED"
    assert len(bars) == 1


def test_akshare_provider_reports_missing_endpoints_and_bounded_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_spot() -> pd.DataFrame:
        raise TimeoutError("provider payload must not be exposed")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_zh_a_spot_em=failing_spot),
    )
    provider = AKShareRealTimeProvider(timeout_seconds=0.5, retry_count=0, delay_seconds=0)

    assert provider.health_check().connected is True
    with pytest.raises(ProviderRequestError, match="real-time request failed"):
        provider.get_market_snapshot()
    with pytest.raises(ProviderRequestError, match="index snapshot"):
        provider.get_index_snapshot(["000300"])
    with pytest.raises(ProviderRequestError, match="minute endpoint"):
        provider.get_minute_bars(["000001"], "1m")


def test_akshare_minute_provider_retries_without_adjust_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stock_zh_a_minute(symbol: str, period: str) -> pd.DataFrame:
        assert symbol == "000001"
        assert period == "1m"
        return _minute_frame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_zh_a_minute=stock_zh_a_minute),
    )

    bars = AKShareRealTimeProvider(retry_count=0, delay_seconds=0).get_minute_bars(
        ["000001"], "1m"
    )

    assert len(bars) == 1


def test_tushare_success_path_fetches_quotes_and_minute_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def rt_min(self, **kwargs: str) -> pd.DataFrame:
            return _minute_frame().assign(ts_code=kwargs["ts_code"], pre_close=10.0)

    monkeypatch.setitem(
        sys.modules,
        "tushare",
        types.SimpleNamespace(pro_api=lambda token: FakeClient()),
    )
    provider = TushareRealTimeProvider(token="token-value")

    health = provider.health_check()
    quotes = provider.get_quotes(["000001"])
    bars = provider.get_minute_bars(["000001"], "5m")
    indexes = provider.get_index_snapshot(["000300"])

    assert health.connected is True
    assert quotes[0].symbol == "000001"
    assert bars[0].frequency == "5m"
    assert indexes[0].symbol == "000300"
    assert provider.get_market_status() == "UNKNOWN"
    with pytest.raises(ProviderRequestError, match="full-market"):
        provider.get_market_snapshot()


def test_tushare_client_initialization_and_missing_endpoint_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "tushare",
        types.SimpleNamespace(pro_api=lambda token: (_ for _ in ()).throw(RuntimeError("secret"))),
    )
    with pytest.raises(ProviderRequestError, match="initialization"):
        TushareRealTimeProvider(token="token-value").health_check()

    monkeypatch.setitem(
        sys.modules,
        "tushare",
        types.SimpleNamespace(pro_api=lambda token: object()),
    )
    health = TushareRealTimeProvider(token="token-value").health_check()
    assert health.status == "MISSING_ENDPOINT"


def test_rqdata_success_path_is_lazy_and_maps_indexed_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRQData:
        def init(self, username: str, password: str) -> None:
            assert username == "user"
            assert password == "pass"

        def current_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "last": [10.1],
                    "open": [10.0],
                    "high": [10.2],
                    "low": [9.9],
                    "volume": [100],
                    "amount": [1010],
                },
                index=symbols,
            )

        def get_price(self, symbol: str, *, frequency: str, fields: list[str]) -> pd.DataFrame:
            return _minute_frame().set_index("trade_time").rename(columns={"vol": "volume"})

    monkeypatch.setitem(sys.modules, "rqdatac", FakeRQData())
    provider = RQDataRealTimeProvider(username="user", password="pass")

    health = provider.health_check()
    quotes = provider.get_quotes(["000001"])
    indexes = provider.get_index_snapshot(["000300"])
    bars = provider.get_minute_bars(["000001"], "1m")

    assert health.connected is True
    assert quotes[0].symbol == "000001"
    assert indexes[0].symbol == "000300"
    assert bars[0].amount == pytest.approx(1010)
    assert provider.get_market_status() == "UNKNOWN"
    with pytest.raises(ProviderRequestError, match="full-market"):
        provider.get_market_snapshot()


def test_rqdata_missing_endpoint_and_import_failure_are_reported_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingRQData:
        def init(self, **kwargs: str) -> None:
            return None

    monkeypatch.setitem(sys.modules, "rqdatac", MissingRQData())
    health = RQDataRealTimeProvider(config_path="C:/secure/rqdata.toml").health_check()
    assert health.status == "MISSING_ENDPOINT"

    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ImportError(name)),
    )
    with pytest.raises(ProviderConfigurationError, match="RQData"):
        RQDataRealTimeProvider(username="user", password="pass").health_check()


def test_rqdata_permission_denial_and_minute_failures_are_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeniedRQData:
        def init(self, username: str, password: str) -> None:
            return None

        def current_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            raise RuntimeError("permission denied")

    monkeypatch.setitem(sys.modules, "rqdatac", DeniedRQData())
    provider = RQDataRealTimeProvider(username="user", password="pass")
    first = provider.health_check()
    second = provider.health_check()

    assert first.status == "PERMISSION_DENIED"
    assert second.status == first.status
    with pytest.raises(ProviderRequestError, match="permission"):
        provider.get_quotes(["000001"])


def test_rqdata_minute_endpoint_and_request_failure_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoMinuteRQData:
        def init(self, username: str, password: str) -> None:
            return None

        def current_snapshot(self, symbols: list[str]) -> pd.DataFrame:
            return pd.DataFrame({"last": [10]}, index=symbols)

    monkeypatch.setitem(sys.modules, "rqdatac", NoMinuteRQData())
    provider = RQDataRealTimeProvider(username="user", password="pass")
    with pytest.raises(ProviderRequestError, match="minute endpoint"):
        provider.get_minute_bars(["000001"], "1m")

    class FailingMinuteRQData(NoMinuteRQData):
        def get_price(self, *args: object, **kwargs: object) -> pd.DataFrame:
            raise RuntimeError("payload")

    monkeypatch.setitem(sys.modules, "rqdatac", FailingMinuteRQData())
    provider = RQDataRealTimeProvider(username="user", password="pass")
    with pytest.raises(ProviderRequestError, match="minute request"):
        provider.get_minute_bars(["000001"], "1m")


def test_replay_provider_controls_cursor_reset_and_empty_state() -> None:
    provider = ReplayRealTimeProvider(bars=[_bar()])
    assert provider.metadata().provider == "replay"
    assert provider.health_check().connected is True
    assert provider.get_minute_bars(["000001"], "1m")
    assert provider.get_quotes(["000001"])[0].last == pytest.approx(10.1)
    assert provider.get_index_snapshot(["000001"])[0].symbol == "000001"
    assert provider.replay_next() is not None
    assert provider.replay_next() is None
    provider.reset()
    assert provider.replay_next() is not None
    assert provider.get_market_status() == "OPEN"

    empty = ReplayRealTimeProvider(bars=[])
    assert empty.health_check().status == "EMPTY"
    assert empty.get_market_status() == "CLOSED"
    with pytest.raises(ValueError, match="no bars"):
        empty.get_market_snapshot()


def test_realtime_contract_lazy_exports_are_importable() -> None:
    assert DataQualityStatus.GOOD.value == "GOOD"
    assert MarketSnapshot is not None
    assert ProviderCapability is not None
    assert ProviderHealth is not None
    assert ProviderMetadata is not None
    assert ProviderSwitchEvent is not None
    assert RealTimeQuote is not None
