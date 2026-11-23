"""Regression coverage for stocks outside the official and monitor universes."""

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd
import pytest

from a_share_quant.contracts.data import ProviderRequestError
from a_share_quant.contracts.realtime import RealTimeQuote
from a_share_quant.data.realtime import tencent
from a_share_quant.storage.atomic_json import write_checked_json
from a_share_quant.workbench import chart_history, security_catalog, watchlist_quotes
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.security_catalog import SecurityCatalog
from a_share_quant.workbench.service import WorkbenchService
from a_share_quant.workbench.watchlist import Watchlist
from a_share_quant.workbench.watchlist_quotes import WatchlistQuotes

CATALOG = [
    {"exchange": "SSE", "symbol": "688001", "name": "华兴源创"},
    {"exchange": "SZSE", "symbol": "000002", "name": "万  科Ａ"},
    {"exchange": "BSE", "symbol": "920001", "name": "纬达光电"},
]


@pytest.fixture
def local_watchlist(tmp_path):
    write_checked_json(
        tmp_path / ".runtime/watchlist/catalog.json",
        {"updated_at": datetime.now(timezone.utc).isoformat(), "rows": CATALOG},
        maximum_bytes=20000,
    )
    catalog = SecurityCatalog(tmp_path, allow_network=False)
    return Watchlist(tmp_path, catalog)


def test_catalog_normalizes_search_and_preserves_last_valid_refresh(local_watchlist, monkeypatch):
    catalog = local_watchlist.catalog
    assert catalog.search("　０００００２ ")["rows"] == CATALOG[1:2]
    assert catalog.search("万科A")["rows"] == CATALOG[1:2]
    assert catalog.search("920")["rows"] == CATALOG[2:]
    assert catalog.search("999999")["rows"] == []
    catalog.allow_network = True
    monkeypatch.setattr(security_catalog, "fetch_catalog", lambda _stop: CATALOG[:2])
    catalog.request_refresh(operator_requested=True)
    _await(lambda: not catalog.search("")["refreshing"])
    assert catalog.search("920001")["rows"] == CATALOG[2:]
    assert catalog.search("")["refresh_failed"]
    catalog.close()


def test_watchlist_migration_and_restart(local_watchlist, tmp_path):
    local_watchlist.migrate_legacy(["688001", "688001", "999999"])
    local_watchlist.add("BSE", "920001")
    local_watchlist.add("BSE", "920001")
    restarted = Watchlist(tmp_path, SecurityCatalog(tmp_path, allow_network=False))
    assert [(row["symbol"], row["verified"]) for row in restarted.items()] == [
        ("688001", True), ("999999", False), ("920001", True),
    ]
    with pytest.raises(ValueError, match="未在本机证券名录"):
        restarted.add("SSE", "999999")
    restarted.remove("", "999999")
    assert len(restarted.items()) == 2


def test_watchlist_http_download_uses_separate_cache(local_watchlist, tmp_path, monkeypatch):
    monkeypatch.setenv("A_SHARE_QUANT_DATA_DIR", str(tmp_path / "data"))
    local_watchlist.catalog.allow_network = True
    gate = threading.Event()

    def bars(_exchange, symbol, _start, end, _stop):
        assert gate.wait(3)
        return pd.DataFrame([{
            "symbol": symbol, "date": end, "open": 10, "close": 11, "low": 10,
            "high": 12, "volume": 50, "source": "fixture", "data_version": "unadjusted",
        }])

    monkeypatch.setattr(chart_history, "fetch_chart_bars", bars)
    server = create_server(watchlist=local_watchlist, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, payload=None, *, guarded=True):
        headers = {"Content-Type": "application/json"}
        if guarded:
            headers["X-Quant-Workbench-Request"] = "watchlist"
        body = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(base + path, data=body, headers=headers), timeout=3) as response:
            return json.load(response)

    try:
        assert call("/api/securities/search?q=688001")["rows"] == CATALOG[:1]
        favorite = {"action": "add", "exchange": "SSE", "symbol": "688001"}
        with pytest.raises(HTTPError) as error:
            call("/api/watchlist", favorite, guarded=False)
        assert error.value.code == 403
        assert call("/api/watchlist", favorite)["rows"][0]["symbol"] == "688001"
        args = {"exchange": "SSE", "symbol": "688001", "limit": "120"}
        download = "/api/stocks/history/download"
        assert call(download, args)["status"] == "RUNNING"
        assert call(download, args)["status"] == "RUNNING"
        gate.set()
        query = "?exchange=SSE&symbol=688001&limit=120"
        _await(lambda: call("/api/stocks/history/status" + query)["status"] == "DONE")
        result = call("/api/stocks/history" + query)
        assert result["source"] == "fixture"
        assert result["bars"][0]["close"] == 11
        assert not (tmp_path / "data/lake/daily_bars/688001.parquet").exists()
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("primary_unavailable", [False, True])
def test_beijing_chart_uses_sina_and_eastmoney_with_consistent_volume(
    monkeypatch, primary_unavailable,
):
    calls = []
    trading_date = date(2026, 11, 23)

    def sdk(endpoint, kwargs, **_options):
        calls.append((endpoint, kwargs["symbol"]))
        if endpoint == "stock_zh_a_daily":
            if primary_unavailable:
                raise ProviderRequestError("simulated connection failure")
            return pd.DataFrame([{
                "date": trading_date, "open": 10, "high": 12, "low": 9,
                "close": 11, "volume": 200, "amount": 2200,
            }])
        return pd.DataFrame([{
            "日期": trading_date, "开盘": 10, "最高": 12, "最低": 9,
            "收盘": 11, "成交量": "2", "成交额": 2200,
        }])

    monkeypatch.setattr(chart_history, "call_sdk_process", sdk)
    result = chart_history.fetch_chart_bars(
        "BSE", "920001", trading_date, trading_date, threading.Event(),
    )
    assert calls[0] == ("stock_zh_a_daily", "bj920001")
    assert calls[1:] == ([("stock_zh_a_hist", "920001")] if primary_unavailable else [])
    assert result["symbol"].tolist() == ["920001"]
    assert result["volume"].tolist() == [200]
    assert result["source"].tolist() == [
        "akshare-eastmoney" if primary_unavailable else "akshare-sina"
    ]


def test_quote_state_keeps_unavailable_favorites_visible():
    now = datetime(2026, 11, 23, 2, tzinfo=timezone.utc)
    service = WorkbenchService(clock=lambda: now, allow_network=False)
    service.store.put_quotes([
        RealTimeQuote("688001", "A_SHARE", now, now, 31, "fixture"),
        RealTimeQuote("000002", "A_SHARE", now - timedelta(hours=1), now, 5, "fixture"),
    ])
    quotes = WatchlistQuotes(service)
    try:
        rows = quotes.enrich([{**row, "verified": True} for row in CATALOG], allow_network=False)
        assert [row["quote_status"] for row in rows] == ["LIVE", "STALE", "NO_QUOTE"]
        assert rows[-1]["price"] is None
    finally:
        quotes.close()


def test_tencent_quote_uses_verified_ticker_and_rejects_future_or_missing_time():
    received = datetime(2026, 11, 23, 2, tzinfo=timezone.utc)

    def line(ticker, code, stamp):
        fields = [""] * 31
        fields[1:4] = ["股票", code, "12.34"]
        fields[30] = stamp
        return f'v_{ticker}="{"~".join(fields)}";'

    payload = "".join([
        line("sh688001", "688001", "20261123095959"),
        line("sz000002", "000002", "20261123100002"),
        line("bj920001", "920002", "20261123100000"),
        line("bj920001", "920001", ""),
    ])
    ids = {"sh688001": ("SSE", "688001"), "sz000002": ("SZSE", "000002"),
           "bj920001": ("BSE", "920001")}
    quote, = tencent._parse_quotes(payload, ids, received)
    assert quote.symbol == "688001"
    assert quote.last == 12.34
    assert quote.timestamp_exchange == received - timedelta(seconds=1)


def test_fresh_supplement_replaces_old_primary_cache_without_changing_main_store(monkeypatch):
    now = datetime(2026, 11, 23, 2, tzinfo=timezone.utc)
    service = WorkbenchService(clock=lambda: now, allow_network=False)
    previous = RealTimeQuote("688001", "A_SHARE", now - timedelta(minutes=4), now, 31, "fixture")
    current = RealTimeQuote("688001", "SSE", now, now, 31.6, "Tencent")
    service.store.put_quotes([previous])
    monkeypatch.setattr(watchlist_quotes, "get_tencent_quotes", lambda _batch: (current,))
    quotes = WatchlistQuotes(service)
    try:
        items = [{**CATALOG[0], "verified": True}]
        before, = quotes.enrich(items, allow_network=True)
        assert before["quote_status"] == "STALE"
        _await(lambda: quotes.enrich(items, allow_network=False)[0]["quote_status"] == "LIVE")
        refreshed, = quotes.enrich(items, allow_network=False)
        assert refreshed["price"] == 31.6
        assert refreshed["quote_source"] == "Tencent"
        assert service.store.quotes() == (previous,)
    finally:
        quotes.close()


def _await(check):
    deadline = time.monotonic() + 5
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.02)
