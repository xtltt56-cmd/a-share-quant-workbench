import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from a_share_quant.workbench.app import create_server


class FakeService:
    def __init__(self) -> None:
        self.refreshed = False

    def health(self):
        return {
            "status": "OK",
            "active_provider": "replay",
            "active_source": "Replay / Test Data",
            "source_class": "REPLAY / NON-MARKET",
            "paper_only": True,
            "live_trading_enabled": False,
        }

    def snapshot(self):
        return {
            "session": "OPEN",
            "data_quality": "REPLAY",
            "active_provider": "replay",
            "active_source": "Replay / Test Data",
            "source_class": "REPLAY / NON-MARKET",
            "last_update": "2026-08-10T02:00:00+00:00",
            "data_age_seconds": 0.0,
            "latency_ms": 10.0,
            "fallback_count": 0,
            "continuous_updates": False,
            "intraday_monitor": [],
            "official_daily_candidates": [],
        }

    def refresh(self):
        self.refreshed = True


def test_dashboard_binds_only_to_loopback_and_exposes_backend_freshness() -> None:
    service = FakeService()
    server = create_server(service=service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.headers["Cache-Control"] == "no-store"
        assert payload["paper_only"] is True
        with urlopen(f"http://127.0.0.1:{port}/api/state", timeout=3) as response:
            state = json.loads(response.read().decode("utf-8"))
            assert response.headers["Cache-Control"] == "no-store"
        assert state["session"] == "OPEN"
        with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
            html = response.read().decode("utf-8")
            assert response.headers["Cache-Control"] == "no-store"
        assert '<html lang="zh-CN">' in html
        assert "A股量化交易工作台" in html
        assert "仅供纸面监控" in html
        assert "当前数据源" in html
        assert "数据源类别" in html
        assert "后端行情时间戳" in html
        assert "股票名称（代码）" in html
        assert "monitor-symbol" in html
        assert "table-scroll" in html
        assert ".monitor-table{width:100%;min-width:0;table-layout:fixed}" in html
        assert "overflow-wrap:anywhere" in html
        assert "@media (max-width:1100px){.monitor-table{min-width:1120px}}" in html
        assert "参考买入区间" in html
        assert "最高可接受价" in html
        assert "失效价" in html
        assert "价格指导" in html
        assert "AKShare / 东方财富" in html
        assert "数据源请求失败" in html
        assert "'MARKET_CLOSED':'市场已收盘'" in html
        assert "'MARKET_NOT_OPEN':'尚未开盘'" in html
        assert "'MARKET_LUNCH_BREAK':'午间休市'" in html
        assert "'CLOSED':'已收盘'" in html
        assert "'NON_TRADING':'非交易日'" in html
        assert "'LUNCH_BREAK':'午间休市'" in html
        assert "'PRE_MARKET':'盘前时段'" in html
        assert "'CLOSED':'非交易时段'" not in html
        assert '<div class="label">ACTIVE SOURCE</div>' not in html
        assert '<div class="label">PUBLIC DATA SOURCE</div>' not in html
        assert "<th>Backend Quote Timestamp</th>" not in html
        assert "cache:'no-store'" in html
        assert "BUY" not in html
        with pytest.raises(HTTPError) as get_refresh:
            urlopen(f"http://127.0.0.1:{port}/api/refresh", timeout=3)
        assert get_refresh.value.code == 405
        with pytest.raises(HTTPError) as missing_header:
            urlopen(
                Request(
                    f"http://127.0.0.1:{port}/api/refresh",
                    method="POST",
                ),
                timeout=3,
            )
        assert missing_header.value.code == 403
        request = Request(
            f"http://127.0.0.1:{port}/api/refresh",
            method="POST",
            headers={"X-Quant-Workbench-Request": "refresh"},
        )
        with urlopen(request, timeout=3) as response:
            json.loads(response.read().decode("utf-8"))
        assert service.refreshed is True
        with pytest.raises(HTTPError):
            urlopen(f"http://127.0.0.1:{port}/api/not-found", timeout=3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_rejects_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server(host="0.0.0.0", port=0)


def test_safe_exit_requires_guarded_loopback_post() -> None:
    class Supervisor:
        def __init__(self) -> None:
            self.calls = 0

        def shutdown(self, *, timeout_seconds: float = 5.0):
            self.calls += 1
            return type(
                "Result",
                (),
                {"checkpoint_saved": True, "children_stopped": True},
            )()

    supervisor = Supervisor()
    server = create_server(service=FakeService(), supervisor=supervisor, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with pytest.raises(HTTPError) as missing_header:
            urlopen(
                Request(
                    f"http://127.0.0.1:{port}/api/system/safe-exit",
                    method="POST",
                ),
                timeout=3,
            )
        assert missing_header.value.code == 403

        request = Request(
            f"http://127.0.0.1:{port}/api/system/safe-exit",
            method="POST",
            headers={"X-Quant-Workbench-Request": "safe-exit"},
        )
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["checkpoint_saved"] is True
        assert supervisor.calls == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
