"""Whole-market browsing and persisted listing identity."""

from datetime import datetime, timezone
from threading import Event

import pytest

from a_share_quant.data.providers.foreign_daily import ForeignDataError
from a_share_quant.market.foreign_sessions import market_session
from a_share_quant.workbench import foreign_markets
from a_share_quant.workbench.foreign_catalog import update_listings
from a_share_quant.workbench.foreign_markets import UNIVERSES, ForeignMarkets


def _row(symbol, name, currency="HKD", identity="existing", keys=None):
    return {
        "symbol": symbol, "name": name, "currency": currency,
        "exchange": "XNYS" if currency == "USD" else "XHKG",
        "identity": identity, "keys": keys or [name.casefold()],
    }


def _catalog(service, market, raw, as_of="2026-11-23"):
    previous = service._catalog(market)
    rows, archived = update_listings(market, previous, raw, UNIVERSES[market], as_of)
    snapshot = {
        "rows": rows, "archived": archived, "as_of": as_of,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    service._save(market, "catalog", snapshot, foreign_markets._CATALOG_BYTES)
    service.catalogs[market] = snapshot
    return rows


def test_search_favorites_and_ledger_survive_a_reused_code(tmp_path):
    service = ForeignMarkets(tmp_path, allow_network=False)
    raw = {
        "01093": _row("01093", "石藥集團", keys=["石藥集團", "石药集团", "cspc pharma"]),
        "80700": _row("80700", "騰訊控股－Ｒ", currency="CNY"),
    }
    rows = _catalog(service, "HK", raw)
    listing_id = rows["01093"]["listing_id"]
    for query in ("1093", "01093", "石药", "石藥", "CSPC"):
        assert service.search("HK", query)["rows"][0]["listing_id"] == listing_id
    assert service.search("HK", "010")["matches"] == 1
    assert not service.search("US", "01093")["rows"]

    service._save("HK", "favorites", {"symbols": ["00700"]}, 8192)
    service.favorite("HK", "1093", "add", listing_id)
    day = str(market_session("HK", datetime.now(timezone.utc))[1])
    deposit = _event("deposit", day, amount="100000")
    service.record("HK", deposit, commit=True)
    trade = _event("buy", day, symbol="01093", listing_id=listing_id)
    service.record("HK", trade, commit=True)
    incompatible = _event("buy", day, symbol="80700", reference=9)
    with pytest.raises(ValueError, match="账本币种"):
        service.record("HK", incompatible, commit=False)

    rows = _catalog(
        service, "HK", {**raw, "01093": _row("01093", "另一家公司", identity="reused")},
        "2026-11-24",
    )
    assert rows["01093"]["listing_id"] != listing_id
    assert rows["01093"]["history_from"] == "2026-11-24"
    service.close()

    reopened = ForeignMarkets(tmp_path, allow_network=False)
    favorite = next(row for row in reopened.state("HK")["favorites"] if row["symbol"] == "01093")
    assert favorite["listing_id"] == listing_id and not favorite["active"]
    assert reopened.security("HK", "01093", listing_id)["status"] == "inactive"
    assert reopened.account("HK")["positions"][0]["name"] == "石藥集團"
    stale = _event("sell", day, symbol="01093", listing_id=listing_id, reference=8)
    with pytest.raises(ValueError, match="当前市场名录"):
        reopened.record("HK", stale, commit=True)
    reopened.favorite("HK", "01093", "remove", listing_id)
    assert [item["symbol"] for item in reopened.state("HK")["favorites"]] == ["00700"]
    assert len(reopened.state("HK")["pool"]) == len(UNIVERSES["HK"])
    reopened.close()


def _event(action, day, reference=1, **fields):
    if action == "deposit":
        reference = 0
    return {
        "id": f"11111111-1111-4111-a111-{reference:012}", "action": action,
        "date": day, "symbol": "", "quantity": "100", "price": "12", "fee": "0", "amount": "",
        **fields,
    }


def test_failed_catalog_refresh_keeps_last_good_directory(tmp_path, monkeypatch):
    service = ForeignMarkets(tmp_path, allow_network=True)
    rows = _catalog(service, "US", {"BRK.B": _row("BRK.B", "Berkshire Hathaway", "USD")})

    def offline(market):
        raise ForeignDataError("名录暂时不可用")

    monkeypatch.setattr(foreign_markets, "fetch_catalog", offline)
    service._refresh_catalog("US")
    result = service.search("US", "brk-b")
    assert result["complete_catalog"]
    assert result["error"] == "名录暂时不可用"
    assert result["rows"][0]["listing_id"] == rows["BRK.B"]["listing_id"]
    service.close()


def test_concurrent_details_fetch_once_then_use_cache_and_keep_it_on_error(tmp_path, monkeypatch):
    service = ForeignMarkets(tmp_path, allow_network=True)
    raw = {"PLTR": _row("PLTR", "Palantir", currency="USD")}
    _catalog(service, "US", raw)
    monkeypatch.setattr(foreign_markets, "alpaca_credentials", lambda root: {})
    _, day, _, _ = market_session("US", datetime.now(timezone.utc))
    entered, release = Event(), Event()
    calls = []

    def fake_daily(symbol, start, end, *, market):
        calls.append((market, symbol))
        entered.set()
        assert release.wait(3)
        return [{"date": str(day), "close": 20, "open": 20, "high": 22, "low": 19,
                 "volume": 500, "adjusted_close": 20}]

    monkeypatch.setattr(foreign_markets, "sina_daily", fake_daily)
    try:
        assert service.security("US", "pltr")["status"] == "loading"
        assert entered.wait(3)
        assert service.security("US", "PLTR")["status"] == "loading"
        release.set()
        service.history_executor.submit(lambda: None).result()
        service.history_executor.shutdown(wait=True)
        result = service.security("US", "PLTR")
        assert not result["research_sample"]
        assert result["status"] == "ready" and len(result["bars"]) == 1
        assert calls == [("US", "PLTR")]
    finally:
        release.set()
        service.close()

    def offline(*args, **kwargs):
        raise ForeignDataError("行情源暂不可用")

    monkeypatch.setattr(foreign_markets, "sina_daily", offline)
    reopened = ForeignMarkets(tmp_path, allow_network=True)
    try:
        assert reopened.security("US", "PLTR", force=True)["status"] == "loading"
        reopened.history_executor.shutdown(wait=True)
        result = reopened.security("US", "PLTR")
        assert result["status"] == "error"
        assert len(result["bars"]) == 1
        assert "行情源暂不可用" in result["notice"]
    finally:
        reopened.close()
