"""Local-only HTTP dashboard for the real-time paper monitor."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from a_share_quant.workbench.service import WorkbenchService


class WorkbenchHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], service: WorkbenchService) -> None:
        self.service = service
        super().__init__(server_address, WorkbenchRequestHandler)


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    server: WorkbenchHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/":
            self._write_html(_DASHBOARD_HTML)
        elif path == "/api/health":
            self._write_json(self.server.service.health())
        elif path == "/api/state":
            self._write_json(self.server.service.snapshot())
        elif path == "/api/refresh":
            self.server.service.refresh()
            self._write_json(self.server.service.snapshot())
        else:
            self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the default access log local and payload-free.
        return None

    def _write_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)


def create_server(
    *,
    service: WorkbenchService | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> WorkbenchHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("the workbench must bind to 127.0.0.1")
    return WorkbenchHTTPServer((host, port), service or WorkbenchService(allow_network=False))


def run_server(
    *,
    port: int = 8765,
    allow_network: bool = False,
    repo_root: Path | None = None,
) -> None:
    del repo_root  # reserved for future config loading; no path is trusted from HTTP
    service = WorkbenchService(allow_network=allow_network)
    service.start_background()
    server = create_server(service=service, port=port)
    try:
        print(f"A-share Quant Workbench: http://127.0.0.1:{server.server_address[1]}/")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        service.stop_background()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--network",
        action="store_true",
        help="allow real provider requests; without it the dashboard stays offline",
    )
    args = parser.parse_args()
    run_server(port=args.port, allow_network=args.network)
    return 0


_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股量化交易系统 · 纸面监控</title>
<style>
body{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f5f7fb;color:#172033;margin:0}
header{background:#12233f;color:white;padding:20px 28px}
main{max-width:1180px;margin:22px auto;padding:0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.card{background:white;border:1px solid #dfe5ef;border-radius:10px;padding:16px;
box-shadow:0 2px 8px #12233f12}
.label{font-size:12px;color:#667085}.value{font-size:22px;font-weight:650;margin-top:7px}
button{background:#1d5fd1;color:white;border:0;border-radius:6px;padding:9px 14px;cursor:pointer}
table{width:100%;border-collapse:collapse;background:white;margin-top:14px}
.card table{margin-top:8px}
th,td{padding:9px;border-bottom:1px solid #edf0f5;text-align:left;font-size:13px}th{color:#667085}
.muted{color:#667085}.safe{color:#147a46}.warn{color:#a15c00}.danger{color:#b42318}
</style></head>
<body><header><h1>A股量化交易系统</h1>
<div>Real-Time Quant Workbench · 纸面/信号监控 · 不提供实盘下单</div></header>
<main><div class="grid">
<div class="card"><div class="label">数据源</div><div id="provider" class="value">加载中</div></div>
<div class="card"><div class="label">会话</div><div id="session" class="value">加载中</div></div>
<div class="card"><div class="label">数据质量</div>
<div id="quality" class="value">加载中</div></div>
<div class="card"><div class="label">市场温度</div>
<div id="temperature" class="value">加载中</div></div>
</div>
<div class="card" style="margin-top:14px"><button onclick="refresh()">刷新数据</button>
<span class="muted">自动轮询由本地服务控制；本页不会发送订单。</span>
<p id="error" class="warn"></p></div>
<div class="card"><h2>官方日频候选</h2>
<p class="muted">日频模型候选与盘中监控分离；当前页面不会把盘中触发状态升级为官方信号。</p>
<div id="daily">暂无官方候选</div></div>
<div class="card"><h2>盘中监控</h2><table><thead><tr>
<th>代码</th><th>价格</th><th>涨跌幅</th><th>状态</th><th>数据年龄</th>
</tr></thead><tbody id="monitor"></tbody></table></div>
</main>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({
'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function load(){const r=await fetch('/api/state',{cache:'no-store'});const d=await r.json();
document.getElementById('provider').textContent=d.active_provider||'不可用';
document.getElementById('session').textContent=d.session||'未知';
document.getElementById('quality').textContent=d.data_quality||'未知';
document.getElementById('temperature').textContent=(d.breadth||{}).temperature||'未知';
document.getElementById('error').textContent=d.last_error?('状态：'+d.last_error):'';
document.getElementById('monitor').innerHTML=(d.intraday_monitor||[]).slice(0,50)
.map(x=>`<tr><td>${esc(x.symbol)}</td><td>${esc(x.current_price)}</td>
<td>${esc(x.change_pct??'')}</td><td>${esc(x.state)}</td>
<td>${esc(x.data_age_seconds??'')}</td></tr>`).join('');}
async function refresh(){await fetch('/api/refresh',{cache:'no-store'});await load()}
load(); setInterval(load,15000);
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
