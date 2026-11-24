import json
import threading
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from a_share_quant.account.foreign_ledger import account_snapshot, prepare_event
from a_share_quant.data.providers.foreign_daily import SINA_US_SOURCE
from a_share_quant.market.foreign_sessions import market_session
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.foreign_markets import UNIVERSES, ForeignMarkets, _ranking


@pytest.mark.parametrize(("market", "when", "status", "cutoff", "following"), [
    ("US", "2026-11-27T18:01:00+00:00", "已收盘", "2026-11-25", "2026-11-27"),
    ("US", "2026-11-28T05:16:00+00:00", "非交易日", "2026-11-27", "2026-11-30"),
    ("US", "2026-07-06T13:35:00+00:00", "常规交易时段", "2026-07-02", "2026-07-06"),
    ("HK", "2026-11-24T04:30:00+00:00", "午间休市", "2026-11-23", "2026-11-24"),
    ("HK", "2026-12-24T04:30:00+00:00", "已收盘", "2026-12-24", "2026-12-28"),
])
def test_exchange_sessions(market, when, status, cutoff, following):
    session, date, next_date, _ = market_session(
        market, datetime.fromisoformat(when), vendor_day_bar=market == "US"
    )
    assert (session, str(date), str(next_date)) == (status, cutoff, following)


def test_ranking_requires_consecutive_sessions_and_uses_adjusted_prices():
    _, _, _, schedule = market_session("HK", datetime.fromisoformat("2026-11-24T08:30:00+00:00"))
    days = [str(x.date()) for x in schedule.loc[:"2026-11-24"].index[-61:]]
    rows = [{"date": day, "close": 300, "adjusted_close": 10 + i / 10}
            for i, day in enumerate(days)]
    incomplete = [rows[0], *rows[:-1]]
    ranked = _ranking({"00700": rows, "09988": incomplete}, UNIVERSES["HK"], days, "2026-11-25")
    assert [row["symbol"] for row in ranked] == ["00700"]
    assert ranked[0]["ret60"] == pytest.approx(0.6)
    assert ranked[0]["close"] == 300


def test_market_endpoints_and_favorites_are_scoped(tmp_path, monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    service = ForeignMarkets(tmp_path, allow_network=False)
    server = create_server(port=0, foreign_markets=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/api/foreign"

    def call(endpoint, body=None, *, headers=None):
        request = Request(
            base + endpoint, data=json.dumps(body).encode() if body is not None else None,
            headers=headers or {},
        )
        with urlopen(request, timeout=6) as response:
            return json.load(response)

    write = {"Content-Type": "application/json", "X-Quant-Workbench-Request": "foreign-markets"}
    try:
        state = call("/favorite?market=HK", {"symbol": "00700", "action": "add"}, headers=write)
        assert state["favorites"][0]["symbol"] == "00700"
        assert state["currency"] == "HKD"
        assert not call("/state?market=US")["favorites"]
        assert call("/security?market=US&symbol=BRK.B")["currency"] == "USD"
        with pytest.raises(HTTPError) as missing:
            call("/security?market=US&symbol=00700")
        assert missing.value.code == 400
        assert call("/state?market=US")["next_source"] == SINA_US_SOURCE
        with pytest.raises(HTTPError) as denied:
            call("/favorite?market=HK", {"symbol": "00700", "action": "remove"})
        assert denied.value.code == 403
        assert ForeignMarkets(tmp_path, allow_network=False).state("HK")["favorites"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_foreign_ledger_settlement_and_market_isolation(tmp_path):
    service = ForeignMarkets(tmp_path, allow_network=False)
    now = datetime.fromisoformat("2026-11-23T10:00:00+00:00")

    def entry(market, action, entry_id, **kwargs):
        payload = {
            "id": f"11111111-1111-4111-a111-{entry_id:012}", "action": action,
            "date": "2026-11-20", "symbol": "", "quantity": "", "price": "",
            "fee": "0", "amount": "", **kwargs,
        }
        return payload, prepare_event(market, payload, UNIVERSES[market], now)

    for market, symbol, shares, settlement in [
        ("US", "AAPL", "1", "2026-11-23"), ("HK", "00700", "100", "2026-11-24"),
    ]:
        deposit, initial = entry(market, "deposit", 0, amount="50000")
        buy, fill = entry(market, "buy", 1, symbol=symbol, quantity=shares, price="100", fee="5")
        sell, exit_fill = entry(market, "sell", 2, symbol=symbol, quantity=shares, price="110",
                                fee="6")
        assert exit_fill["settles_on"] == settlement
        before = account_snapshot([initial, fill, exit_fill], "2026-11-20")
        after = account_snapshot([initial, fill, exit_fill], settlement)
        assert not before["positions"] and before["pending"] != "0.00"
        assert after["pending"] == "0.00"
        assert after["realized"] == ("-1.00" if market == "US" else "989.00")
        assert service.record(market, deposit, commit=True)["cash"] == "50000.00"
        assert service.record(market, buy, commit=False)["positions"][0]["available"] == int(shares)
        assert not service.account(market)["positions"]
        service.record(market, buy, commit=True)
        assert service.record(market, buy, commit=True)["positions"][0]["quantity"] == int(shares)
        service.record(market, sell, commit=True)
        assert len(service.account(market)["history"]) == 3
    assert service.account("US")["history"][0]["symbol"] == "AAPL"
    assert service.account("HK")["history"][0]["symbol"] == "00700"
