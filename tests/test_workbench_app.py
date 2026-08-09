import json
import threading
from urllib.request import urlopen

import pytest

from a_share_quant.workbench.app import create_server


class FakeService:
    def __init__(self) -> None:
        self.refreshed = False

    def health(self):
        return {
            "status": "OK",
            "active_provider": "replay",
            "paper_only": True,
            "live_trading_enabled": False,
        }

    def snapshot(self):
        return {
            "session": "OPEN",
            "data_quality": "GOOD",
            "active_provider": "replay",
            "intraday_monitor": [],
            "official_daily_candidates": [],
        }

    def refresh(self):
        self.refreshed = True


def test_dashboard_binds_only_to_loopback() -> None:
    service = FakeService()
    server = create_server(service=service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["paper_only"] is True
        with urlopen(f"http://127.0.0.1:{port}/api/state", timeout=3) as response:
            state = json.loads(response.read().decode("utf-8"))
        assert state["session"] == "OPEN"
        with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
            html = response.read().decode("utf-8")
        assert "不提供实盘下单" in html
        with urlopen(f"http://127.0.0.1:{port}/api/refresh", timeout=3) as response:
            json.loads(response.read().decode("utf-8"))
        assert service.refreshed is True
        with pytest.raises(Exception):
            urlopen(f"http://127.0.0.1:{port}/api/not-found", timeout=3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_dashboard_rejects_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        create_server(host="0.0.0.0", port=0)
