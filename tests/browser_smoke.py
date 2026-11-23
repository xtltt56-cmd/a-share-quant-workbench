"""Opt-in desktop and mobile browser smoke with deterministic local API evidence."""

import os
import threading

from playwright.sync_api import expect, sync_playwright

from a_share_quant.workbench.app import create_server


def test_workbench_renders_data_and_failure_states():
    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    workflow = {
        "observed_at": "2026-11-23T10:00:00+08:00",
        "calendar": {"coverage_warning": True, "lookahead_sessions": 18},
        "stages": {
            "realtime": {"status": "DEGRADED", "data_age_seconds": 99},
            "daily": {
                "status": "FRESH",
                "refresh": {"input_summary": {"symbols": [
                    {"symbol": "600001", "status": "FRESH"},
                    {"symbol": "600002", "status": "INSUFFICIENT_HISTORY"},
                ]}},
            },
            "public_risk": {"status": "PARTIAL", "expired_count": 1},
            "agent": {"status": "PRICE_REVIEW_REQUIRED"},
        },
    }
    try:
        with sync_playwright() as browser_api:
            browser = browser_api.chromium.launch(
                channel=os.environ.get("A_SHARE_QUANT_BROWSER_CHANNEL")
            )
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/workflow/health", lambda route: route.fulfill(json=workflow))
            page.route("**/api/models/progress", lambda route: route.fulfill(json={
                "status": "OK", "prediction_count": 40, "settled_count": 37,
                "overdue_count": 3, "waiting_count": 0, "model_count": 1,
                "settlement_reasons": {"OUTCOME_ADJUSTMENT_CONFLICT": 3},
            }))
            agent = server.agent_runtime.status()
            agent.update(
                status="PRICE_REVIEW_REQUIRED", notice_zh="请安装含有效计价快照的工作台版本。"
            )
            page.route("**/api/agent/status", lambda route: route.fulfill(json=agent))

            page.goto(base)
            page.get_by_role("link", name="日选研究", exact=True).click()
            expect(page.locator("#daily-description")).to_contain_text("本机日线池 2 只")
            page.get_by_role("link", name="模型观察", exact=True).click()
            expect(page.get_by_text("收盘价与交易所涨跌幅口径冲突")).to_be_visible()
            page.get_by_role("link", name="数据与系统", exact=True).click()
            expect(page.get_by_text("正式日历仅覆盖未来")).to_be_visible()
            expect(page.get_by_text("降级", exact=True).first).to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("link", name="AI 分析", exact=True).click()
            expect(page.locator("#agent-start")).to_be_disabled()
            expect(page.locator("#agent-start-notice")).to_contain_text("有效计价快照")
            assert page.evaluate("document.documentElement.scrollWidth") == 390
            assert errors == []
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    test_workbench_renders_data_and_failure_states()
