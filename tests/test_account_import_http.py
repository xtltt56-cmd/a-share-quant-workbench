import json
import threading
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from a_share_quant.account.import_inbox import AccountImportInbox
from a_share_quant.account.snapshot_store import AccountSnapshotStore
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.app import create_server
from a_share_quant.workbench.service import WorkbenchService


def _running_service(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "成交.csv").write_text(
        "证券名称,证券代码,成交数量,成交价格\n平安银行,000001,100,10\n",
        encoding="utf-8",
    )
    advisory = AdvisoryWorkbenchService(
        initial_cash=Decimal("100000"),
        ledger_path=tmp_path / "ledger.jsonl",
        account_import_inbox=AccountImportInbox(inbox),
        account_snapshot_store=AccountSnapshotStore(tmp_path / "snapshot.json"),
    )
    server = create_server(
        service=WorkbenchService(allow_network=False),
        advisory_service=advisory,
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _post(port: int, path: str, payload: object, *, headers: dict[str, str] | None = None):
    request_headers = {
        "Content-Type": "application/json",
        "X-Quant-Workbench-Request": "manual-advisory",
    }
    request_headers.update(headers or {})
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=request_headers,
    )
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_account_import_routes_require_guard_and_never_accept_paths(tmp_path) -> None:
    server, thread = _running_service(tmp_path)
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/advisory/imports", timeout=3) as response:
            listed = json.loads(response.read().decode("utf-8"))
        assert listed["files"][0]["file_name"] == "成交.csv"
        file_id = listed["files"][0]["file_id"]

        status, payload = _post(
            port,
            "/api/advisory/import-preview",
            {"path": "C:/secret.txt"},
        )
        assert status == 400
        assert payload["manual_execution_required"] is True
        assert "secret" not in json.dumps(payload)

        status, payload = _post(
            port,
            "/api/advisory/import-preview",
            {"file_id": file_id},
            headers={"Content-Type": "text/plain"},
        )
        assert status == 415
        assert payload["manual_execution_required"] is True

        request = Request(
            f"http://127.0.0.1:{port}/api/advisory/import-preview",
            data=json.dumps({"file_id": file_id}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_account_import_preview_and_confirm_use_identifier_only(tmp_path) -> None:
    server, thread = _running_service(tmp_path)
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/api/advisory/imports", timeout=3) as response:
            file_id = json.loads(response.read().decode("utf-8"))["files"][0]["file_id"]
        status, preview = _post(
            port,
            "/api/advisory/import-preview",
            {"file_id": file_id},
        )
        assert status == 200
        assert preview["kind"] == "FILLS"
        assert preview["rows"][0]["code"] == "000001"
        status, confirmed = _post(
            port,
            "/api/advisory/import-confirm",
            {"confirmation_token": preview["confirmation_token"]},
        )
        assert status == 200
        assert confirmed["recorded_rows"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_advisory_page_contains_chinese_account_import_workflow(tmp_path) -> None:
    server, thread = _running_service(tmp_path)
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/advisory", timeout=3
        ) as response:
            html = response.read().decode("utf-8")
        assert "券商导出文件导入" in html
        assert "扫描导出文件" in html
        assert "生成预览" in html
        assert "确认导入" in html
        assert "只读取固定收件箱" in html
        assert "不会登录或控制券商客户端" in html
        assert "系统不会提交委托" in html
        assert "Broker Import" not in html
        assert "Scan Files" not in html
        assert "Confirm Import" not in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
