import sys
import threading
import types
from datetime import datetime, timezone

import pandas as pd
import pytest

from a_share_quant.contracts.data import ProviderConfigurationError, ProviderRequestError
from a_share_quant.data.realtime.akshare import AKShareRealTimeProvider
from a_share_quant.data.realtime.rqdata import RQDataRealTimeProvider
from a_share_quant.data.realtime.tushare import TushareRealTimeProvider


def _spot_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "最新价": 10.5,
                "涨跌幅": 2.94,
                "涨跌额": 0.3,
                "成交量": 1000,
                "成交额": 10500,
                "最高": 10.8,
                "最低": 9.9,
                "今开": 10.0,
                "昨收": 10.2,
                "换手率": 1.2,
            },
            {
                "代码": "000002",
                "名称": "万科A",
                "最新价": 0,
                "涨跌幅": 0,
                "涨跌额": 0,
                "成交量": 0,
                "成交额": 0,
                "最高": 0,
                "最低": 0,
                "今开": 0,
                "昨收": 0,
                "换手率": 0,
            },
        ]
    )


def test_akshare_realtime_provider_normalizes_snapshot_and_quarantines_zero_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def stock_zh_a_spot_em() -> pd.DataFrame:
        calls.append("spot")
        return _spot_frame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_zh_a_spot_em=stock_zh_a_spot_em),
    )

    provider = AKShareRealTimeProvider(retry_count=0, delay_seconds=0)
    snapshot = provider.get_market_snapshot()

    assert calls == ["spot"]
    assert [quote.symbol for quote in snapshot.quotes] == ["000001"]
    assert snapshot.quotes[0].last == pytest.approx(10.5)
    assert snapshot.quotes[0].timestamp_received.tzinfo is not None
    assert snapshot.quotes[0].quality_flag.value == "DEGRADED"


def test_akshare_realtime_provider_detects_optional_index_and_minute_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stock_zh_a_spot_em() -> pd.DataFrame:
        return _spot_frame().iloc[[0]]

    def stock_zh_index_spot_em() -> pd.DataFrame:
        return pd.DataFrame(
            [{"代码": "000300", "名称": "沪深300", "最新价": 4000, "涨跌幅": 1.0}]
        )

    def stock_zh_a_hist_min_em(**kwargs: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "时间": "2026-08-08 11:29:00",
                    "开盘": 10,
                    "最高": 10.2,
                    "最低": 9.9,
                    "收盘": 10.1,
                    "成交量": 100,
                    "成交额": 1010,
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(
            stock_zh_a_spot_em=stock_zh_a_spot_em,
            stock_zh_index_spot_em=stock_zh_index_spot_em,
            stock_zh_a_hist_min_em=stock_zh_a_hist_min_em,
        ),
    )

    provider = AKShareRealTimeProvider(retry_count=0, delay_seconds=0)
    metadata = provider.metadata()
    index_quotes = provider.get_index_snapshot(["000300"])
    bars = provider.get_minute_bars(["000001"], "1m")

    assert "snapshot" in metadata.frequencies
    assert "1m" in metadata.frequencies
    assert index_quotes[0].symbol == "000300"
    assert bars[0].is_final is True


def test_akshare_full_market_endpoint_is_not_retried_inside_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def stock_zh_a_spot_em() -> pd.DataFrame:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("secret=must-not-appear")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_zh_a_spot_em=stock_zh_a_spot_em),
    )

    provider = AKShareRealTimeProvider(
        retry_count=2,
        delay_seconds=0,
    )

    with pytest.raises(ProviderRequestError, match="all snapshot endpoints"):
        provider.get_market_snapshot()

    assert attempts == 1


def test_akshare_prefers_sina_and_throttles_full_market_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monotonic = [100.0]

    def stock_zh_a_spot() -> pd.DataFrame:
        calls.append("sina")
        return pd.DataFrame(
            [
                {
                    "代码": "sz000001",
                    "名称": "平安银行",
                    "最新价": 10.5 + len(calls) / 10,
                    "涨跌额": 0.3,
                    "涨跌幅": 2.94,
                    "买入": 10.49,
                    "卖出": 10.51,
                    "昨收": 10.2,
                    "今开": 10.0,
                    "最高": 10.8,
                    "最低": 9.9,
                    "成交量": 100_000,
                    "成交额": 1_050_000,
                    "时间戳": "10:00:00",
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_zh_a_spot=stock_zh_a_spot),
    )
    provider = AKShareRealTimeProvider(
        retry_count=0,
        delay_seconds=0,
        monotonic_clock=lambda: monotonic[0],
        wall_clock=lambda: datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc),
        full_market_min_interval_seconds=60,
    )

    first = provider.get_market_snapshot()
    monotonic[0] = 159.9
    throttled = provider.get_market_snapshot()
    monotonic[0] = 160.0
    refreshed = provider.get_market_snapshot()

    assert calls == ["sina", "sina"]
    assert throttled is first
    assert refreshed is not first
    assert first.quotes[0].quality_flag.value == "GOOD"
    assert first.quotes[0].timestamp_exchange == datetime(
        2026, 8, 10, 2, 0, tzinfo=timezone.utc
    )
    assert first.quotes[0].bid1 == pytest.approx(10.49)
    assert first.quotes[0].ask1 == pytest.approx(10.51)
    assert provider.active_endpoint == "sina"
    assert provider.active_source_name == "AKShare / Sina"


def test_akshare_endpoint_circuit_skips_failed_sources_until_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monotonic = [0.0]

    def stock_zh_a_spot() -> pd.DataFrame:
        calls.append("sina")
        raise ConnectionError("sina unavailable")

    def stock_zh_a_spot_em() -> pd.DataFrame:
        calls.append("eastmoney")
        raise ConnectionError("eastmoney unavailable")

    def stock_zh_a_spot_tx() -> pd.DataFrame:
        calls.append("tencent")
        return pd.DataFrame(
            [
                {
                    "code": "sz000001",
                    "name": "平安银行",
                    "zxj": "10.50",
                    "zd": "0.30",
                    "zdf": "2.94",
                    "volume": "1000",
                    "turnover": "105",
                    "hsl": "1.2",
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(
            stock_zh_a_spot=stock_zh_a_spot,
            stock_zh_a_spot_em=stock_zh_a_spot_em,
            stock_zh_a_spot_tx=stock_zh_a_spot_tx,
        ),
    )
    provider = AKShareRealTimeProvider(
        retry_count=3,
        delay_seconds=0,
        monotonic_clock=lambda: monotonic[0],
        full_market_min_interval_seconds=60,
        endpoint_cooldown_seconds=300,
    )

    first = provider.get_market_snapshot()
    monotonic[0] = 60.0
    second = provider.get_market_snapshot()

    assert calls == ["sina", "eastmoney", "tencent", "tencent"]
    assert first.quality_flag.value == "DEGRADED"
    assert second.quality_flag.value == "DEGRADED"
    assert provider.active_source_name == "AKShare / Tencent"


def test_akshare_realtime_provider_falls_back_to_tencent_full_market_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def stock_zh_a_spot_em() -> pd.DataFrame:
        calls.append("eastmoney")
        raise ConnectionError("proxy rejected endpoint")

    def stock_zh_a_spot_tx() -> pd.DataFrame:
        calls.append("tencent")
        return pd.DataFrame(
            [
                {
                    "code": "sz000001",
                    "name": "平安银行",
                    "zxj": "10.50",
                    "zd": "0.30",
                    "zdf": "2.94",
                    "volume": "1000",
                    "turnover": "105",
                    "hsl": "1.2",
                },
                {
                    "code": "sh600000",
                    "name": "浦发银行",
                    "zxj": "9.20",
                    "zd": "-0.10",
                    "zdf": "-1.08",
                    "volume": "900",
                    "turnover": "82.8",
                    "hsl": "0.8",
                },
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(
            stock_zh_a_spot_em=stock_zh_a_spot_em,
            stock_zh_a_spot_tx=stock_zh_a_spot_tx,
        ),
    )

    provider = AKShareRealTimeProvider(retry_count=0, delay_seconds=0)
    snapshot = provider.get_market_snapshot()

    assert calls == ["eastmoney", "tencent"]
    assert snapshot.quotes[0].symbol == "000001"
    assert snapshot.quotes[0].last == pytest.approx(10.5)
    assert snapshot.quotes[0].change_pct == pytest.approx(2.94)
    assert snapshot.quotes[0].volume == pytest.approx(100_000)
    assert snapshot.quotes[0].amount == pytest.approx(1_050_000)
    assert snapshot.quotes[0].amount / snapshot.quotes[0].volume == pytest.approx(10.5)
    assert snapshot.quotes[0].quality_flag.value == "DEGRADED"
    assert snapshot.quotes[1].change == pytest.approx(-0.1)
    assert snapshot.quotes[1].change_pct == pytest.approx(-1.08)
    assert snapshot.quality_flag.value == "DEGRADED"
    assert provider.active_endpoint == "tencent"


def test_akshare_realtime_provider_uses_official_single_stock_quote_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def stock_bid_ask_em(symbol: str) -> pd.DataFrame:
        calls.append(symbol)
        return pd.DataFrame(
            [
                {"item": "最新", "value": 10.5},
                {"item": "今开", "value": 10.0},
                {"item": "最高", "value": 10.8},
                {"item": "最低", "value": 9.9},
                {"item": "昨收", "value": 10.2},
                {"item": "总手", "value": 1000},
                {"item": "金额", "value": 10500},
                {"item": "涨跌", "value": 0.3},
                {"item": "涨幅", "value": 2.94},
                {"item": "换手", "value": 1.2},
                {"item": "buy_1", "value": 10.49},
                {"item": "sell_1", "value": 10.51},
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_bid_ask_em=stock_bid_ask_em),
    )

    quotes = AKShareRealTimeProvider(retry_count=0, delay_seconds=0).get_quotes(
        ["000001", "000002"]
    )

    assert calls == ["000001", "000002"]
    assert [quote.symbol for quote in quotes] == ["000001", "000002"]
    assert quotes[0].last == pytest.approx(10.5)
    assert quotes[0].quality_flag.value == "DEGRADED"


def test_akshare_priority_quotes_never_fall_back_to_slow_full_market_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def stock_zh_a_spot() -> pd.DataFrame:
        calls.append("full")
        raise AssertionError("priority path must not call full-market endpoint")

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_zh_a_spot=stock_zh_a_spot),
    )
    provider = AKShareRealTimeProvider(retry_count=0, delay_seconds=0)

    with pytest.raises(ProviderRequestError, match="single-stock"):
        provider.get_priority_quotes(["000001"])

    assert calls == []


def test_akshare_priority_quotes_skip_one_bad_symbol_and_keep_other_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def stock_bid_ask_em(symbol: str) -> pd.DataFrame:
        calls.append(symbol)
        if symbol == "000001":
            return pd.DataFrame([{"bad": "schema"}])
        return pd.DataFrame(
            [
                {"item": "最新", "value": 10.5},
                {"item": "今开", "value": 10.0},
                {"item": "最高", "value": 10.8},
                {"item": "最低", "value": 9.9},
                {"item": "昨收", "value": 10.2},
                {"item": "总手", "value": 1000},
                {"item": "金额", "value": 10500},
                {"item": "涨跌", "value": 0.3},
                {"item": "涨幅", "value": 2.94},
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(stock_bid_ask_em=stock_bid_ask_em),
    )
    provider = AKShareRealTimeProvider(retry_count=0, delay_seconds=0)

    quotes = provider.get_priority_quotes(["000001", "000002"])

    assert calls == ["000001", "000002"]
    assert [quote.symbol for quote in quotes] == ["000002"]


def test_tushare_realtime_provider_requires_token_before_importing_client() -> None:
    with pytest.raises(ProviderConfigurationError, match="TUSHARE_TOKEN"):
        TushareRealTimeProvider(token="")


def test_tushare_permission_denial_is_cached_and_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_calls = 0

    class FakeClient:
        def rt_min(self, **kwargs: str) -> pd.DataFrame:
            nonlocal probe_calls
            probe_calls += 1
            raise RuntimeError("permission denied token=secret")

    monkeypatch.setitem(
        sys.modules,
        "tushare",
        types.SimpleNamespace(pro_api=lambda token: FakeClient()),
    )

    provider = TushareRealTimeProvider(token="real-token-shaped-value")
    first = provider.health_check()
    second = provider.health_check()

    assert probe_calls == 1
    assert first.connected is False
    assert first.status == "PERMISSION_DENIED"
    assert "secret" not in first.message
    assert second.status == first.status


def test_rqdata_provider_rejects_missing_credentials_without_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(name: str):
        raise AssertionError(f"unexpected import: {name}")

    monkeypatch.setattr("importlib.import_module", fail_import)

    with pytest.raises(ProviderConfigurationError, match="RQData"):
        RQDataRealTimeProvider()


def test_replay_provider_returns_bars_in_chronological_order() -> None:
    from a_share_quant.contracts.realtime import MinuteBar
    from a_share_quant.data.realtime.replay import ReplayRealTimeProvider

    def bar(minute: int) -> MinuteBar:
        return MinuteBar(
            symbol="000001",
            timestamp=datetime(2026, 8, 9, 3, minute, tzinfo=timezone.utc),
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

    provider = ReplayRealTimeProvider(bars=[bar(2), bar(1)])

    first = provider.replay_next()
    second = provider.replay_next()

    assert first is not None and second is not None
    assert first.timestamp < second.timestamp
    assert provider.replay_next() is None


def test_akshare_priority_quotes_prefer_timestamped_cached_market_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def stock_zh_a_spot() -> pd.DataFrame:
        calls.append("full")
        return pd.DataFrame(
            [
                {"代码": "sz000001", "名称": "平安银行", "最新价": 10.5,
                 "昨收": 10.2, "成交量": 100_000, "成交额": 1_050_000,
                 "时间戳": "10:00:00"},
                {"代码": "sz000002", "名称": "万科A", "最新价": 8.2,
                 "昨收": 8.0, "成交量": 80_000, "成交额": 656_000,
                 "时间戳": "10:00:00"},
            ]
        )

    def stock_bid_ask_em(symbol: str) -> pd.DataFrame:
        calls.append(f"single:{symbol}")
        raise AssertionError("cached timestamped snapshot must be authoritative")

    monkeypatch.setitem(sys.modules, "akshare", types.SimpleNamespace(
        stock_zh_a_spot=stock_zh_a_spot, stock_bid_ask_em=stock_bid_ask_em
    ))
    provider = AKShareRealTimeProvider(
        retry_count=0, delay_seconds=0,
        wall_clock=lambda: datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc),
    )

    provider.get_market_snapshot()
    quotes = provider.get_priority_quotes(["000002"])

    assert calls == ["full"]
    assert [quote.symbol for quote in quotes] == ["000002"]
    assert quotes[0].quality_flag.value == "GOOD"


def test_akshare_timeouts_do_not_create_unbounded_worker_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def blocked_endpoint() -> pd.DataFrame:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
        return pd.DataFrame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        types.SimpleNamespace(blocked_endpoint=blocked_endpoint),
    )
    provider = AKShareRealTimeProvider(
        timeout_seconds=0.1,
        retry_count=0,
        delay_seconds=0,
    )

    with pytest.raises(ProviderRequestError):
        provider._call("blocked_endpoint", retry_on_timeout=False)
    assert started.wait(timeout=1)
    with pytest.raises(ProviderRequestError):
        provider._call("blocked_endpoint", retry_on_timeout=False)

    assert calls == 1
    release.set()
    provider.close()
