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
        assert "Paper-only monitoring" in html
        assert "ACTIVE SOURCE" in html
        assert "PUBLIC DATA SOURCE" in html
        assert "Backend Quote Timestamp" in html
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
