"""Local-only HTTP dashboard for the real-time paper monitor."""

# ruff: noqa: E501

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
            self._write_json(
                {"error": "use POST with the local refresh request header"},
                status=HTTPStatus.METHOD_NOT_ALLOWED,
            )
        else:
            self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path != "/api/refresh":
            self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("X-Quant-Workbench-Request") != "refresh":
            self._write_json(
                {"error": "local refresh request header required"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        self.server.service.refresh()
        self._write_json(self.server.service.snapshot())

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
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A-Share Quant Workbench</title>
<style>
body{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f5f7fb;color:#172033;margin:0}
header{background:#12233f;color:white;padding:20px 28px}
main{max-width:1280px;margin:22px auto;padding:0 18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}
.card{background:white;border:1px solid #dfe5ef;border-radius:10px;padding:16px;
box-shadow:0 2px 8px #12233f12}
.label{font-size:12px;color:#667085;letter-spacing:.03em}.value{font-size:20px;font-weight:650;margin-top:7px;overflow-wrap:anywhere}
button{background:#1d5fd1;color:white;border:0;border-radius:6px;padding:9px 14px;cursor:pointer}
table{width:100%;border-collapse:collapse;background:white;margin-top:14px}
.card table{margin-top:8px}
th,td{padding:9px;border-bottom:1px solid #edf0f5;text-align:left;font-size:13px;vertical-align:top}th{color:#667085}
.muted{color:#667085}.safe{color:#147a46}.warn{color:#a15c00}.danger{color:#b42318}
</style></head>
<body><header><h1>A-Share Quant Workbench</h1>
<div>Paper-only monitoring of daily candidates and intraday data. READY is a monitoring status.</div></header>
<main>
<div class="grid">
<div class="card"><div class="label">ACTIVE SOURCE</div><div id="active-source" class="value">Loading</div></div>
<div class="card"><div class="label">SOURCE CLASS</div><div id="source-class" class="value">PUBLIC DATA SOURCE / PROFESSIONAL DATA SOURCE</div></div>
<div class="card"><div class="label">DATA QUALITY</div><div id="quality" class="value">Loading</div></div>
<div class="card"><div class="label">LAST UPDATE</div><div id="last-update" class="value">Loading</div></div>
<div class="card"><div class="label">DATA AGE</div><div id="data-age" class="value">Loading</div></div>
<div class="card"><div class="label">LATENCY</div><div id="latency" class="value">Loading</div></div>
<div class="card"><div class="label">FALLBACK COUNT</div><div id="fallback-count" class="value">Loading</div></div>
<div class="card"><div class="label">CONTINUOUS UPDATES</div><div id="continuous" class="value">Loading</div></div>
</div>
<div class="card" style="margin-top:14px"><button onclick="refresh()">Refresh backend data</button>
<span class="muted">The browser requests uncached local state. Quote times shown below are supplied by the backend.</span>
<p id="error" class="warn"></p></div>
<div class="card"><h2>Official Daily Candidates</h2>
<p class="muted">Daily model scores remain separate from intraday overlays.</p>
<table><thead><tr><th>Symbol</th><th>Score</th><th>Strategy Version</th><th>Signal Date</th><th>Mode</th></tr></thead>
<tbody id="daily"></tbody></table></div>
<div class="card"><h2>Intraday Monitor</h2><table><thead><tr>
<th>Symbol</th><th>Last</th><th>Change %</th><th>State</th><th>Backend Quote Timestamp</th><th>Data Age</th><th>Quality</th>
</tr></thead><tbody id="monitor"></tbody></table></div>
</main>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function display(v,fallback){return v===null||v===undefined||v===''?(fallback===undefined?'Unavailable':fallback):v}
function seconds(v){return v===null||v===undefined?'Unavailable':String(v)+' s'}
function millis(v){return v===null||v===undefined?'Unavailable':String(v)+' ms'}
function rows(items,render,empty,colspan){
  return items.length?items.map(render).join(''):'<tr><td colspan="'+esc(colspan)+'" class="muted">'+esc(empty)+'</td></tr>'}
async function load(){
  try{
    const r=await fetch('/api/state',{cache:'no-store'});
    const d=await r.json();
    document.getElementById('active-source').textContent=display(d.active_source||d.active_provider);
    document.getElementById('source-class').textContent=display(d.source_class);
    document.getElementById('quality').textContent=display(d.data_quality);
    document.getElementById('last-update').textContent=display(d.last_update||d.updated_at);
    document.getElementById('data-age').textContent=seconds(d.data_age_seconds);
    document.getElementById('latency').textContent=millis(d.latency_ms);
    document.getElementById('fallback-count').textContent=display(d.fallback_count,0);
    document.getElementById('continuous').textContent=d.continuous_updates?'Yes':'No';
    document.getElementById('error').textContent=d.last_error?('Status: '+d.last_error):'';
    const daily=(d.official_daily_candidates||[]).slice(0,20);
    document.getElementById('daily').innerHTML=rows(daily,function(x){return '<tr><td>'+esc(x.symbol)+'</td><td>'+esc(x.normalized_score)+'</td><td>'+esc(x.strategy_version)+'</td><td>'+esc(x.signal_date)+'</td><td>Monitoring only</td></tr>'},'No official daily candidates',5);
    const monitor=(d.intraday_monitor||[]).slice(0,100);
    document.getElementById('monitor').innerHTML=rows(monitor,function(x){return '<tr><td>'+esc(x.symbol)+'</td><td>'+esc(x.current_price??x.last)+'</td><td>'+esc(x.change_pct??'')+'</td><td>'+esc(x.state)+'</td><td>'+esc(x.quote_timestamp)+'</td><td>'+esc(x.data_age_seconds??'')+'</td><td>'+esc(x.data_quality)+'</td></tr>'},'No intraday observations',7);
  }catch(error){
    document.getElementById('error').textContent='Status: local dashboard response unavailable';
  }
}
async function refresh(){await fetch('/api/refresh',{method:'POST',headers:{'X-Quant-Workbench-Request':'refresh'},cache:'no-store'});await load()}
load(); setInterval(load,15000);
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
