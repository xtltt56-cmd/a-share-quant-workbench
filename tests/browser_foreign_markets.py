"""Browser acceptance of the isolated multi-market research workflow."""

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from a_share_quant.market.foreign_sessions import market_session
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.foreign_markets import (
    _SNAPSHOT_BYTES,
    MARKET_META,
    UNIVERSES,
    ForeignMarkets,
)


def fixture_snapshot(market, now):
    _, cutoff, _, sessions = market_session(market, now)
    days = [day.date().isoformat() for day in sessions.loc[:cutoff.isoformat()].index[-61:]]
    bars = [
        {"date": day, "open": 300 + index, "high": 302 + index, "low": 299 + index,
         "close": 301 + index, "adjusted_close": 301 + index, "volume": 1000}
        for index, day in enumerate(days)
    ]
    return {
        "as_of": cutoff.isoformat(), "updated_at": now.isoformat(),
        "source": MARKET_META[market]["source"],
        "bars": {symbol: bars for symbol in UNIVERSES[market]},
        "exchange_metadata": {"as_of": cutoff.isoformat(), "items": {
            "00700": {"board_lot": 100, "stamp_duty": True},
        }} if market == "HK" else {},
    }


def test_market_switch_keeps_research_and_ledgers_isolated(tmp_path):
    source_root = os.getenv("A_SHARE_QUANT_FOREIGN_SNAPSHOT_ROOT")
    source = ForeignMarkets(Path(source_root), allow_network=False) if source_root else None
    service = ForeignMarkets(tmp_path, allow_network=False)
    now = datetime.now(timezone.utc)
    for market in ("US", "HK"):
        snapshot = (
            source._load(market, "daily", _SNAPSHOT_BYTES)
            if source else fixture_snapshot(market, now)
        )
        assert snapshot, f"Requested snapshot for {market} does not exist"
        service._save(market, "daily", snapshot, _SNAPSHOT_BYTES)
        assert service.state(market)["fresh"], f"{market} snapshot is stale"

    server = create_server(port=0, foreign_markets=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.getenv("A_SHARE_QUANT_BROWSER_CHANNEL"))
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/?market=CN#daily", wait_until="networkidle")
            expect(page.locator('[data-view="daily"]')).to_be_visible()
            page.locator("#market-switch").select_option("HK")
            expect(page.locator("#foreign-view table").first.locator("tbody tr")).to_have_count(
                service.state("HK")["coverage"]
            )
            page.locator("#open-security-search").click()
            page.locator("#security-query").fill("00700")
            page.locator("#security-results [data-foreign-add]").click()
            expect(page.locator("#security-results")).to_contain_text("已添加")
            page.locator("#security-results [data-foreign-symbol]").click()
            expect(page.locator("#foreign-stock-chart canvas").first).to_be_visible()
            board_lot = service.security("HK", "00700")["board_lot"]
            expect(page.locator("#foreign-stock-note")).to_contain_text(f"每手 {board_lot} 股")
            page.locator("#close-foreign-stock").click()

            us = context.new_page()
            us.goto(base + "/?market=US#watchlist")
            expect(us.locator("#foreign-view")).to_contain_text("暂无美股自选")
            us.locator('[data-page="daily"]').click()
            expect(us.locator("#foreign-view table").first.locator("tbody tr")).to_have_count(
                service.state("US")["coverage"]
            )

            page.locator('[data-page="watchlist"]').click()
            expect(page.locator('#foreign-view [data-foreign-remove="00700"]')).to_be_visible()
            page.locator('[data-page="holdings"]').click()
            form = page.locator("#foreign-ledger")
            expect(form.locator('[name="date"]')).not_to_have_value("")
            trade_day = service.state("HK")["as_of"]
            form.locator('[name="date"]').fill(trade_day)
            form.locator('[name="amount"]').fill("100000")
            form.locator('[type="submit"]').click()
            page.locator("#foreign-ledger-confirm").click()
            expect(page.locator("#foreign-account-summary")).to_contain_text("100,000.00 HKD")
            form.locator('[name="action"]').select_option("buy")
            form.locator('[name="symbol"]').select_option("00700")
            form.locator('[name="quantity"]').fill("100")
            form.locator('[name="price"]').fill("340.6")
            form.locator('[name="fee"]').fill("39.8")
            form.locator('[type="submit"]').click()
            expect(page.locator("#foreign-ledger-preview")).to_contain_text("预计交收日")
            page.locator("#foreign-ledger-confirm").click()
            expect(page.locator("#foreign-account-summary")).to_contain_text("65,900.20 HKD")
            us.locator('[data-page="holdings"]').click()
            expect(us.locator("#foreign-account-summary")).to_contain_text("0.00 USD")
            page.set_viewport_size({"width": 390, "height": 844})
            expect(page.locator("#market-switch")).to_be_visible()
            page.locator("#market-switch").select_option("CN")
            expect(page.locator('[data-view="holdings"]')).to_be_visible()
            assert not errors
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
