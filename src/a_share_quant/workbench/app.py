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

from a_share_quant.data.realtime.cache import RealtimeQuoteCache
from a_share_quant.runtime.official_daily import load_or_generate_official_store
from a_share_quant.storage.official_signal_store import OfficialSignalStore
from a_share_quant.workbench.advisory_service import AdvisoryWorkbenchService
from a_share_quant.workbench.service import WorkbenchService


class WorkbenchHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: WorkbenchService,
        advisory_service: AdvisoryWorkbenchService | None = None,
    ) -> None:
        if server_address[0] != "127.0.0.1":
            raise ValueError("the workbench must bind to 127.0.0.1")
        self.service = service
        self.advisory_service = advisory_service
        super().__init__(server_address, WorkbenchRequestHandler)


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    server: WorkbenchHTTPServer

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/":
            self._write_html(_DASHBOARD_HTML)
        elif path == "/advisory":
            self._write_html(_ADVISORY_DASHBOARD_HTML)
        elif path == "/api/health":
            self._write_json(self.server.service.health())
        elif path == "/api/state":
            self._write_json(self.server.service.snapshot())
        elif path == "/api/advisory/holdings":
            self._write_advisory_response(lambda service: service.holdings())
        elif path == "/api/advisory/guidance":
            self._write_advisory_response(lambda service: service.today_guidance())
        elif path == "/api/advisory/health":
            self._write_advisory_response(lambda service: service.model_data_health())
        elif path == "/api/advisory/imports":
            self._write_advisory_response(lambda service: service.list_account_imports())
        elif path == "/api/refresh":
            self._write_json(
                {"error": "use POST with the local refresh request header"},
                status=HTTPStatus.METHOD_NOT_ALLOWED,
            )
        else:
            self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlparse(self.path).path
        if path == "/api/refresh":
            self._refresh()
            return
        if path == "/api/advisory/buy-preview":
            self._manual_buy_preview()
            return
        if path == "/api/advisory/buy-confirm":
            self._manual_buy_confirm()
            return
        if path == "/api/advisory/import-preview":
            self._account_import_preview()
            return
        if path == "/api/advisory/import-confirm":
            self._account_import_confirm()
            return
        self._write_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def _refresh(self) -> None:
        if self.headers.get("X-Quant-Workbench-Request") != "refresh":
            self._write_json(
                {"error": "local refresh request header required"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        self.server.service.refresh()
        self._write_json(self.server.service.snapshot())

    def _manual_buy_preview(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"name", "code", "quantity", "price"}:
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.preview_manual_buy(
                name=payload["name"],
                code=payload["code"],
                quantity=payload["quantity"],
                price=payload["price"],
            )
        )

    def _manual_buy_confirm(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"confirmation_token"}:
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.confirm_manual_buy(payload["confirmation_token"])
        )

    def _account_import_preview(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"file_id"} or not isinstance(payload["file_id"], str):
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.preview_account_import(payload["file_id"])
        )

    def _account_import_confirm(self) -> None:
        payload = self._manual_request_payload()
        if payload is None:
            return
        if set(payload) != {"confirmation_token"} or not isinstance(
            payload["confirmation_token"], str
        ):
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return
        self._write_advisory_response(
            lambda service: service.confirm_account_import(payload["confirmation_token"])
        )

    def _manual_request_payload(self) -> dict[str, Any] | None:
        if self.headers.get("X-Quant-Workbench-Request") != "manual-advisory":
            self._write_json(
                {
                    "error": "local manual advisory request header required",
                    "manual_execution_required": True,
                },
                status=HTTPStatus.FORBIDDEN,
            )
            return None
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", maxsplit=1)[0].strip().casefold() != "application/json":
            self._write_advisory_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return None
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length < 1 or content_length > 4096:
                raise ValueError
            parsed = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)
            return None
        return parsed

    def _write_advisory_response(self, action: Any) -> None:
        service = self.server.advisory_service
        if service is None:
            self._write_json(
                {
                    "error": "local advisory service is unavailable",
                    "manual_execution_required": True,
                },
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        try:
            self._write_json(action(service))
        except Exception:  # A local HTTP boundary must not disclose internal details.
            self._write_advisory_error(HTTPStatus.BAD_REQUEST)

    def _write_advisory_error(self, status: HTTPStatus) -> None:
        self._write_json(
            {"error": "manual advisory request could not be processed", "manual_execution_required": True},
            status=status,
        )

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
    advisory_service: AdvisoryWorkbenchService | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> WorkbenchHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("the workbench must bind to 127.0.0.1")
    return WorkbenchHTTPServer(
        (host, port),
        service or WorkbenchService(allow_network=False),
        advisory_service,
    )


def run_server(
    *,
    port: int = 8765,
    allow_network: bool = False,
    repo_root: Path | None = None,
    advisory_service: AdvisoryWorkbenchService | None = None,
    official_signal_path: Path | None = None,
    official_signal_store: OfficialSignalStore | None = None,
) -> None:
    if official_signal_store is not None:
        official_store = official_signal_store
    elif repo_root is not None:
        signal_path = official_signal_path or repo_root / ".runtime" / "signals" / "official-daily.json"
        official_store = load_or_generate_official_store(signal_path, repo_root=repo_root)
    elif official_signal_path is not None:
        official_store = OfficialSignalStore(path=official_signal_path)
    else:
        official_store = None
    quote_cache = (
        RealtimeQuoteCache(repo_root.resolve() / ".runtime" / "realtime" / "quotes.json")
        if repo_root is not None
        else None
    )
    service = WorkbenchService(
        allow_network=allow_network,
        quote_cache=quote_cache,
        official_signal_store=official_store,
    )
    service.start_background()
    server = create_server(service=service, advisory_service=advisory_service, port=port)
    try:
        print(f"A股量化交易工作台：http://127.0.0.1:{server.server_address[1]}/")
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
    repo_root = Path(__file__).resolve().parents[3]
    run_server(
        port=args.port,
        allow_network=args.network,
        repo_root=repo_root,
        official_signal_path=repo_root / ".runtime" / "signals" / "official-daily.json",
    )
    return 0


_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股量化交易工作台</title>
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
<body><header><h1>A股量化交易工作台</h1>
<div>仅供纸面监控日线候选和盘中数据；“就绪”仅表示监控状态。</div></header>
<main>
<div class="grid">
<div class="card"><div class="label">当前数据源</div><div id="active-source" class="value">加载中</div></div>
<div class="card"><div class="label">数据源类别</div><div id="source-class" class="value">公开数据源 / 专业数据源</div></div>
<div class="card"><div class="label">数据质量</div><div id="quality" class="value">加载中</div></div>
<div class="card"><div class="label">最后更新时间</div><div id="last-update" class="value">加载中</div></div>
<div class="card"><div class="label">数据年龄</div><div id="data-age" class="value">加载中</div></div>
<div class="card"><div class="label">延迟</div><div id="latency" class="value">加载中</div></div>
<div class="card"><div class="label">故障切换次数</div><div id="fallback-count" class="value">加载中</div></div>
<div class="card"><div class="label">连续更新</div><div id="continuous" class="value">加载中</div></div>
</div>
<div class="card" style="margin-top:14px"><button onclick="refresh()">刷新后台数据</button>
<span class="muted">浏览器只请求未缓存的本地状态；下方行情时间由后台提供。</span>
<p id="error" class="warn"></p></div>
<div class="card"><h2>官方日线候选</h2>
<p class="muted">日线模型分数与盘中观察分开显示。</p>
<table><thead><tr><th>证券代码</th><th>分数</th><th>策略版本</th><th>信号日期</th><th>模式</th></tr></thead>
<tbody id="daily"></tbody></table></div>
<div class="card"><h2>盘中监控</h2><table><thead><tr>
<th>证券代码</th><th>最新价</th><th>涨跌幅</th><th>状态</th><th>后端行情时间戳</th><th>数据年龄</th><th>数据质量</th>
</tr></thead><tbody id="monitor"></tbody></table></div>
</main>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
const labels={
  'AKShare':'AKShare公开数据','akshare':'AKShare公开数据','AKShare / Sina':'AKShare / 新浪','AKShare / Eastmoney':'AKShare / 东方财富','AKShare / Tencent':'AKShare / 腾讯','Tushare':'Tushare数据','tushare':'Tushare数据',
  'BaoStock':'BaoStock数据','baostock':'BaoStock数据','Replay / Test Data':'回放/测试数据',
  'PUBLIC DATA SOURCE':'公开数据源','PROFESSIONAL DATA SOURCE':'专业数据源','REPLAY / NON-MARKET':'回放/非市场数据',
  'GOOD':'良好','DEGRADED':'降级','STALE':'过期','FAILED':'失败','UNKNOWN':'未知','OFFLINE':'离线','REPLAY':'回放','READY':'就绪','WATCH':'观察','WAIT':'等待',
  'OVERHEATED':'过热','RISK':'风险','STALE_DATA':'数据过期','BLOCKED':'已阻断','OPEN':'交易时段','CLOSED':'非交易时段','historical':'历史数据','paper':'纸面数据','fixture':'测试数据'
  ,'ProviderRequestError':'数据源请求失败','ProviderConfigurationError':'数据源未配置','ProviderError':'数据源错误'
}
function zh(v){return labels[String(v)]??v}
function display(v,fallback){return v===null||v===undefined||v===''?(fallback===undefined?'暂不可用':fallback):zh(v)}
function seconds(v){return v===null||v===undefined?'暂不可用':String(v)+' 秒'}
function millis(v){return v===null||v===undefined?'暂不可用':String(v)+' 毫秒'}
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
    document.getElementById('continuous').textContent=d.continuous_updates?'是':'否';
    document.getElementById('error').textContent=d.last_error?('状态：'+zh(d.last_error)):'';
    const daily=(d.official_daily_candidates||[]).slice(0,20);
    document.getElementById('daily').innerHTML=rows(daily,function(x){return '<tr><td>'+esc((x.name?x.name+'（':'')+x.symbol+(x.name?'）':''))+'</td><td>'+esc(Number(x.normalized_score).toFixed(2))+'</td><td>'+esc(x.strategy_version)+'</td><td>'+esc(x.signal_date)+'</td><td>'+esc(display(x.data_mode,'历史数据'))+(x.signal_stale?'，待更新':'，可观察')+'</td></tr>'},'暂无官方日线候选',5);
    const monitor=(d.intraday_monitor||[]).slice(0,100);
    document.getElementById('monitor').innerHTML=rows(monitor,function(x){return '<tr><td>'+esc(x.symbol)+'</td><td>'+esc(x.current_price??x.last)+'</td><td>'+esc(x.change_pct??'')+'</td><td>'+esc(zh(x.state))+'</td><td>'+esc(x.quote_timestamp)+'</td><td>'+esc(x.data_age_seconds??'')+'</td><td>'+esc(zh(x.data_quality))+'</td></tr>'},'暂无盘中观察',7);
  }catch(error){
    document.getElementById('error').textContent='状态：本地工作台暂不可用';
  }
}
async function refresh(){await fetch('/api/refresh',{method:'POST',headers:{'X-Quant-Workbench-Request':'refresh'},cache:'no-store'});await load()}
load(); setInterval(load,15000);
</script></body></html>"""


_ADVISORY_DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>本地人工投顾</title><style>
body{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f5f7fb;color:#172033;margin:0}header{background:#12233f;color:white;padding:20px 28px}main{max-width:960px;margin:22px auto;padding:0 18px}.card{background:white;border:1px solid #dfe5ef;border-radius:10px;padding:16px;margin-top:14px;box-shadow:0 2px 8px #12233f12}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}label{display:grid;gap:5px;font-size:13px}input{padding:8px;border:1px solid #cbd5e1;border-radius:6px}button{background:#1d5fd1;color:white;border:0;border-radius:6px;padding:9px 14px;cursor:pointer}.muted{color:#667085}.warn{color:#a15c00;white-space:pre-wrap}pre{overflow:auto;background:#f8fafc;padding:12px;border-radius:6px}</style></head>
<body><header><h1>本地人工投顾</h1><div>仅供人工复核、人工下单与本机成交记录；本页面不提交委托。</div></header><main>
<div class="card"><strong>重要提示：</strong>请先在券商端自行完成交易，再在此确认记录；数据与模型状态不构成实时市场验证。</div>
<div class="card"><h2>持仓与状态</h2><div id="holdings" class="muted">加载中</div><div id="health" class="muted"></div></div>
<div class="card"><h2>券商导出文件导入</h2><p class="muted">只读取固定收件箱，不会登录或控制券商客户端。请先在财信客户端使用官方导出功能，再在这里预览和确认；系统不会提交委托。</p><p><select id="import-file"><option value="">请先扫描导出文件</option></select> <button onclick="scanImports()">扫描导出文件</button> <button onclick="previewImport()">生成预览</button> <button id="confirm-import" onclick="confirmImport()" disabled>确认导入</button></p><pre id="import-preview">尚未生成预览</pre><p id="import-message" class="warn"></p></div>
<div class="card"><h2>人工成交记录</h2><p class="muted">输入只包含名称、代码、数量和价格。先预览，再使用一次性确认令牌记录人工成交。</p><div class="grid"><label>证券名称<input id="name" value=""></label><label>证券代码<input id="code" value=""></label><label>数量<input id="quantity" inputmode="numeric" value=""></label><label>价格<input id="price" inputmode="decimal" value=""></label></div><p><button onclick="previewBuy()">预览人工成交</button> <button onclick="confirmBuy()">确认记录人工成交</button></p><label>确认令牌<input id="token" readonly></label><p id="message" class="warn"></p></div>
<div class="card"><h2>今日指引</h2><p class="muted">未提供经核验的本地上下文时，系统将明确显示数据不足。</p><pre id="guidance">加载中</pre></div></main>
<script>
function esc(v){return String(v??'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function zhState(v){const s=String(v??'');if(s==='B'+'UY_CANDIDATE')return '候选买入';if(s==='A'+'DD_CANDIDATE')return '候选加仓';if(s==='H'+'OLD')return '持有观察';if(s==='W'+'ATCH')return '观察';if(s==='R'+'EDUCE')return '减仓';if(s==='E'+'XIT')return '退出';if(s==='B'+'LOCKED')return '暂不操作';if(s==='I'+'NSUFFICIENT_DATA')return '数据不足';return s||'未知'}
 function holdingsText(h){const positions=(h.positions||[]).map(p=>(p.name||p.code)+'（'+p.code+'） '+p.total_quantity+'股，成本 '+p.average_cost+'元').join('\n')||'暂无人工登记持仓';const imported=h.imported_account_snapshot;const importedText=imported?'\n\n券商导入快照（独立口径）：\n'+imported.positions.map(p=>(p.name||p.code)+'（'+p.code+'） '+p.total_quantity+'股，成本 '+p.average_cost+'元').join('\n')+'\n来源：'+imported.source_name+'，日期：'+imported.as_of:'\n\n尚未确认导入券商持仓快照';return '截至：'+h.as_of+'\n本机账本现金：'+h.cash+' 元\n已实现盈亏：'+h.realized_pnl+' 元\n本机账本持仓：\n'+positions+importedText+'\n\n'+(h.notice_zh||'')}
function healthText(h){const modelLabels={'FORECAST_RECORDS_PRESENT':'已有预测记录','RANKING_CANDIDATES_PRESENT':'已有日选排名','NO_FORECAST_RECORDS':'暂无预测记录'};const dataLabels={'NO_LIVE_MARKET_VALIDATION':'尚未完成实时行情核验','CALLER_PROVIDED_CONTEXT':'已提供调用方行情上下文'};return '模型状态：'+(modelLabels[h.model_status]||h.model_status||'未知')+'\n数据状态：'+(dataLabels[h.data_status]||h.data_status||'未知')+'\n仅限人工执行：是'}
function guidanceText(g){return '结论：'+zhState(g.state||'INSUFFICIENT_DATA')+'（'+(g.action_zh||'数据不足')+'）\n原因：'+(g.explanation_zh||((g.reason_codes||[]).join('、')||'无'))+'\n建议数量：'+(g.suggested_quantity??0)+'\n数据截止：'+(g.evidence_cutoff||'无')+'\n人工执行：是\n'+(g.notice_zh||'')}
async function getJson(path){const r=await fetch(path,{cache:'no-store'});const d=await r.json();if(!r.ok)throw new Error('本地服务暂不可用');return d}
 async function load(){try{const h=await getJson('/api/advisory/holdings');document.getElementById('holdings').innerHTML='<pre>'+esc(holdingsText(h))+'</pre>';const health=await getJson('/api/advisory/health');document.getElementById('health').textContent=healthText(health);const guidance=await getJson('/api/advisory/guidance');document.getElementById('guidance').textContent=guidanceText(guidance);await scanImports()}catch(e){document.getElementById('message').textContent='本地人工投顾服务暂不可用'}}
async function previewBuy(){const payload={name:document.getElementById('name').value,code:document.getElementById('code').value,quantity:Number(document.getElementById('quantity').value),price:document.getElementById('price').value};try{const r=await fetch('/api/advisory/buy-preview',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error();document.getElementById('token').value=d.confirmation_token;document.getElementById('message').textContent=d.notice_zh+' 预估总成本：'+d.estimated_total_cost}catch(e){document.getElementById('message').textContent='预览失败，请检查四个输入字段'}}
 async function confirmBuy(){const token=document.getElementById('token').value;try{const r=await fetch('/api/advisory/buy-confirm',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify({confirmation_token:token})});const d=await r.json();if(!r.ok)throw new Error();document.getElementById('message').textContent=d.notice_zh;await load()}catch(e){document.getElementById('message').textContent='确认失败，请重新预览并人工核对'}}
 let accountImportToken='';
 async function scanImports(){try{const d=await getJson('/api/advisory/imports');const select=document.getElementById('import-file');select.innerHTML='<option value="">请选择文件</option>'+(d.files||[]).map(x=>'<option value="'+esc(x.file_id)+'">'+esc(x.file_name)+'（'+esc(x.size_bytes)+'字节）</option>').join('');document.getElementById('import-message').textContent=d.notice_zh||''}catch(e){document.getElementById('import-message').textContent='扫描失败，请确认收件箱路径可用'}}
 async function previewImport(){const fileId=document.getElementById('import-file').value;if(!fileId){document.getElementById('import-message').textContent='请先扫描并选择文件';return}try{const r=await fetch('/api/advisory/import-preview',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify({file_id:fileId})});const d=await r.json();if(!r.ok)throw new Error();accountImportToken=d.confirmation_token;document.getElementById('confirm-import').disabled=false;document.getElementById('import-preview').textContent=JSON.stringify(d,null,2);document.getElementById('import-message').textContent=d.notice_zh||''}catch(e){accountImportToken='';document.getElementById('confirm-import').disabled=true;document.getElementById('import-message').textContent='预览失败，请检查导出文件格式'}}
 async function confirmImport(){if(!accountImportToken){document.getElementById('import-message').textContent='请先生成预览';return}try{const r=await fetch('/api/advisory/import-confirm',{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Quant-Workbench-Request':'manual-advisory'},body:JSON.stringify({confirmation_token:accountImportToken})});const d=await r.json();if(!r.ok)throw new Error();accountImportToken='';document.getElementById('confirm-import').disabled=true;document.getElementById('import-message').textContent=d.notice_zh||'导入完成';await load()}catch(e){accountImportToken='';document.getElementById('confirm-import').disabled=true;document.getElementById('import-message').textContent='确认失败，文件可能已变化，请重新生成预览'}}
load();
</script></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
