import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd
import pytest

from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.stock_history import load_history


def test_history_rejects_paths_and_unbounded_range(tmp_path):
    for symbol, limit in [("../600000", "60"), ("600000", "999999"), ("600000.SH", "60")]:
        with pytest.raises(ValueError):
            load_history(symbol, limit, data_root=tmp_path)
    assert load_history("600000", data_root=tmp_path)["bars"] == []


def test_history_returns_bounded_sorted_real_rows(tmp_path):
    root = tmp_path / "lake" / "daily_bars"
    root.mkdir(parents=True)
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=80),
            "open": 10,
            "close": 11,
            "low": 9,
            "high": 12,
            "volume": 100,
            "source": "baostock",
            "data_version": "baostock-unadjusted-v1",
        }
    )
    frame.iloc[::-1].to_parquet(root / "600000.parquet")
    result = load_history("600000", "60", data_root=tmp_path)
    assert len(result["bars"]) == 60
    assert result["bars"][0]["date"] == "2026-01-21"
    assert result["bars"][-1]["close"] == 11
    assert result["data_version"] == "baostock-unadjusted-v1"
    json.dumps(result, allow_nan=False)


def test_new_frontend_assets_and_classic_compatibility():
    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        for route in ("/", "/advisory"):
            with urlopen(base + route, timeout=3) as response:
                html = response.read().decode()
                assert 'data-view="holdings"' in html
                assert 'data-view="monitor"' in html
                assert "/assets/workbench.js" in html
                assert 'id="stock-dialog"' in html
        for asset, mime in (("workbench.js", "text/javascript"), ("workbench.css", "text/css")):
            with urlopen(base + "/assets/" + asset, timeout=3) as response:
                assert response.headers["Content-Type"].startswith(mime)
                assert response.headers["X-Content-Type-Options"] == "nosniff"
        with urlopen(base + "/classic/advisory", timeout=3) as response:
            assert "本地人工投顾" in response.read().decode()
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/api/stocks/history?symbol=../600000", timeout=3)
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            urlopen(base + "/assets/../../.env", timeout=3)
        assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/api/system/quit", data=b"{}"), timeout=3)
        assert error.value.code == 403
        with urlopen(
            Request(
                base + "/api/system/quit",
                data=b"{}",
                headers={"X-Quant-Workbench-Request": "safe-exit"},
            ),
            timeout=3,
        ) as response:
            assert "正在安全退出" in json.loads(response.read())["notice_zh"]
        thread.join(timeout=3)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
