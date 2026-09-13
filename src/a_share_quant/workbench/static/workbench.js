"use strict";
const $ = id => document.getElementById(id);
const icons={
  overview:'<path d="M4 13h6V4H4zM14 20h6V11h-6zM4 20h6v-3H4zM14 7h6V4h-6z"/>',
  daily:'<path d="M5 3v4M19 3v4M3 9h18M5 5h14a2 2 0 0 1 2 2v13H3V7a2 2 0 0 1 2-2Z"/><path d="m7 15 3-3 2 2 4-4 2 2"/>',
  monitor:'<path d="M3 12h4l2-5 4 10 2-5h6"/><path d="M4 4h16v16H4z"/>',
  holdings:'<path d="M4 8h16v11H4zM8 8V5h8v3M4 12h16"/><path d="M10 12v2h4v-2"/>',
  models:'<path d="M7 7h10v10H7zM9 2v5M15 2v5M9 17v5M15 17v5M2 9h5M17 9h5M2 15h5M17 15h5"/><path d="m10 13 2-2 2 2"/>',
  system:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
  collapse:'<path d="m14 6-6 6 6 6"/>',density:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  theme:'<path d="M21 12.7A8 8 0 1 1 11.3 3 6.2 6.2 0 0 0 21 12.7Z"/>',
  refresh:'<path d="M20 6v5h-5M4 18v-5h5"/><path d="M6.1 9a7 7 0 0 1 11.5-2.6L20 9M4 15l2.4 2.6A7 7 0 0 0 17.9 15"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  folder:'<path d="M3 6h7l2 2h9v11H3z"/>',close:'<path d="m6 6 12 12M18 6 6 18"/>'
};
document.querySelectorAll('[data-icon]').forEach(el=>{const path=icons[el.dataset.icon];if(path)el.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true">'+path+'</svg>';});
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const present = v => v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v));
const number = (v, digits=2) => present(v) ? Number(v).toLocaleString("zh-CN",{minimumFractionDigits:digits,maximumFractionDigits:digits}) : "暂无";
const labels = {GOOD:"良好",DEGRADED:"降级",FAILED:"失败",STALE:"过期",FRESH:"最新",OFFLINE:"离线",UNKNOWN:"未知",UNAVAILABLE:"暂不可用",REPLAY:"回放",READY:"就绪",WATCH:"观察",WAIT:"等待",OVERHEATED:"过热",RISK:"风险",STALE_DATA:"数据过期",BLOCKED:"暂不操作",OPEN:"交易时段",CLOSED:"已收盘",NON_TRADING:"非交易日",PRE_MARKET:"盘前",LUNCH_BREAK:"午间休市",MARKET_CLOSED:"市场已收盘",MARKET_NOT_OPEN:"尚未开盘",MARKET_LUNCH_BREAK:"午间休市",historical:"历史数据",paper:"纸面数据",fixture:"测试数据",NO_RELIABLE_GUIDANCE:"暂无可靠指导价",INSUFFICIENT_HISTORY:"历史数据不足 252 个交易日",UNSUPPORTED_SECURITY_RULES:"证券交易规则不受支持",PRICE_PLAN_MISSING:"尚未生成冻结价格计划",PLAN_OR_QUOTE_INVALID:"计划已过期或行情时间无效",INVALIDATION_NOT_BELOW_ENTRY:"失效价不低于入场下限",ENTRY_RANGE_INVERTED:"入场区间倒置",ENTRY_ABOVE_MAXIMUM:"入场上限超过最高可接受价",PRICE_BOUNDARIES_INCONSISTENT:"价格边界不一致",RISK_DISTANCE_TOO_HIGH:"风险距离过高",RISK_DISTANCE_TOO_LOW:"风险距离过低",ProviderRequestError:"数据源请求失败",ProviderConfigurationError:"数据源未配置",OFFLINE_MODE:"离线模式",BUY_CANDIDATE:"候选买入",ADD_CANDIDATE:"候选加仓",HOLD:"持有观察",REDUCE:"减仓观察",EXIT:"退出观察",INSUFFICIENT_DATA:"数据不足",NO_CALLER_PROVIDED_CONTEXT:"尚未提供可核验的行情上下文",NO_LIVE_MARKET_VALIDATION:"尚未完成实时行情核验",FORECAST_RECORDS_PRESENT:"已有预测记录",RANKING_CANDIDATES_PRESENT:"已有日选排名",NO_FORECAST_RECORDS:"暂无预测记录",PUBLIC_DATA_SOURCE:"公开数据源",UPDATE_FAILED:"更新失败"};
Object.assign(labels,{RESEARCH_REFERENCE:"研究参考区间",IN_ENTRY_ZONE:"位于参考区间",BELOW_ENTRY:"低于参考区间",ABOVE_ENTRY:"高于参考区间",ABOVE_MAXIMUM:"超过最高可接受价",INVALIDATED:"计划已失效",NO_ACTIVE_SOURCE:"暂无活动数据源",CACHED:"缓存数据",STOPPED:"已停止",ENTRY_ZONE:"位于参考区间",WATCH_ONLY:"仅供观察"});
Object.assign(labels,{CONDITIONS_MET:"条件已满足，待人工复核",WAIT_FOR_PRICE:"等待价格",PRICE_TOO_HIGH:"价格过高",RISK_ALERT:"风险提示",T_PLUS_ONE_BLOCKED:"受 T+1 限制",REDUCE_WATCH:"减仓观察",HOLD_WATCH:"持有观察",DATA_QUALITY_NOT_GOOD:"行情质量未达到良好",PRICE_BELOW_INVALIDATION:"价格低于失效价",PRICE_ABOVE_MAXIMUM:"价格超过最高可接受价",IN_ENTRY_RANGE:"价格位于参考区间",WAIT_FOR_ENTRY_RANGE:"等待价格进入参考区间"});
Object.assign(labels,{RESEARCH_ONLY:"仅供研究参考",NO_QUOTE_AVAILABLE:"暂无可用行情",NO_VALID_PRICE_PLAN:"暂无有效价格计划",MANUAL_EXECUTION_REQUIRED:"仅限人工执行"});
const zh = v => labels[v] || (v ? String(v).replace("AKShare / Sina","AKShare / 新浪").replace("AKShare / Eastmoney","AKShare / 东方财富").replace("AKShare / Tencent","AKShare / 腾讯") : "暂无");
const time = value => {if(!value)return "暂无";const d=new Date(value);return isNaN(d) ? esc(value) : d.toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false});};
const badge = (text,state="") => '<span class="badge '+(["GOOD","FRESH","READY","CONDITIONS_MET","IN_ENTRY_RANGE"].includes(state)?"good":["FAILED","BLOCKED","RISK","RISK_ALERT","INVALIDATED"].includes(state)?"danger":["STALE","STALE_DATA","NO_RELIABLE_GUIDANCE","WAIT","WAIT_FOR_PRICE","PRICE_TOO_HIGH"].includes(state)?"warning":"")+'">'+esc(zh(text))+'</span>';
const empty = text => '<div class="empty"><strong>'+esc(text)+'</strong><span>数据可用后将在这里显示</span></div>';
const stat = (name,value,note="") => '<div class="stat"><div class="stat-label">'+esc(name)+'</div><div class="stat-value">'+esc(value)+'</div><small>'+esc(note)+'</small></div>';
const def = (name,value) => '<div class="definition"><span>'+esc(name)+'</span><span>'+esc(value ?? "暂无")+'</span></div>';
const stockButton = x => '<button class="stock-button" data-stock="'+esc(x.symbol||x.code)+'">'+esc(x.name||x.symbol||x.code)+'<small>'+esc(x.symbol||x.code)+'</small></button>';
const table = (headers,rows) => rows.length ? '<table><thead><tr>'+headers.map(h=>'<th>'+esc(h)+'</th>').join("")+'</tr></thead><tbody>'+rows.map(row=>Array.isArray(row)?'<tr>'+row.map((cell,i)=>'<td data-label="'+esc(headers[i]||'')+'">'+cell+'</td>').join("")+'</tr>':row).join("")+'</tbody></table>' : empty("暂无符合条件的数据");
const range = g => present(g?.entry_lower)&&present(g?.entry_upper) ? number(g.entry_lower)+" – "+number(g.entry_upper) : "暂无可靠区间";
let connected=false,lastRead=null,progress={};
let state={},account={},guidance={},governance={},modelHealth={},activeStock="",chart=null,chartRequest=0,refreshing=false,buyToken="",importToken="",lastFocus=null;
const prefs={get(k, fallback){try{return JSON.parse(localStorage.getItem(k))??fallback;}catch{return fallback;}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch{}}};
let watch = new Set(prefs.get("quant-watch",[]).filter(x=>/^[0-9]{6}$/.test(x)));
document.documentElement.dataset.theme=prefs.get("quant-theme","dark");
document.body.classList.toggle("compact",prefs.get("quant-compact",false));
const pageInfo={overview:["投资总览","先看数据状态，再看候选与价格。"],daily:["日选研究","比较候选分数、价格边界与适用日期。"],monitor:["盘中监控","核验行情时效与价格计划状态。"],holdings:["我的持仓","管理人工成交与券商导入快照。"],models:["模型观察","只用未来结果对证模型表现。"],system:["数据与系统","检查来源、时效和运行状态。"]};
function route(){let page=location.hash.slice(1);if(["entry","import-section"].includes(page))page="holdings";if(!pageInfo[page])page=location.pathname==="/advisory"?"holdings":"overview";document.querySelectorAll("[data-view]").forEach(el=>el.hidden=el.dataset.view!==page);document.querySelectorAll("[data-page]").forEach(el=>{el.classList.toggle("active",el.dataset.page===page);if(el.dataset.page===page)el.setAttribute("aria-current","page");else el.removeAttribute("aria-current");});$("page-title").textContent=$("breadcrumb").textContent=pageInfo[page][0];$("page-subtitle").textContent=pageInfo[page][1];document.title=pageInfo[page][0]+" · A股量化交易工作台";if(["#entry","#import-section"].includes(location.hash))$(location.hash.slice(1)).scrollIntoView({behavior:"smooth"});else window.scrollTo({top:0,behavior:"instant"});}
window.addEventListener("hashchange",route);route();
async function api(path,body,header){const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),20000);try{const response=await fetch(path,{cache:"no-store",signal:controller.signal,...(body!==undefined?{method:"POST",headers:{"Content-Type":"application/json","X-Quant-Workbench-Request":header||"manual-advisory"},body:JSON.stringify(body)}:{})});const result=await response.json();if(!response.ok)throw Error(result.notice_zh||"请求未完成，请检查数据或重新预览");return result;}finally{clearTimeout(timeout);}}
function renderDaily(){const query=$("daily-search").value.trim().toLowerCase();let rows=[...(state.official_daily_candidates||[])].filter(x=>(String(x.name)+x.symbol).toLowerCase().includes(query)&&(!$("daily-plan").checked||(present(x.price_guidance?.entry_lower)&&present(x.price_guidance?.entry_upper))));if($("daily-sort").value==="symbol")rows.sort((a,b)=>a.symbol.localeCompare(b.symbol));else rows.sort((a,b)=>Number(b.normalized_score)-Number(a.normalized_score));$("daily-count").textContent=rows.length+" 只候选";$("daily-description").textContent=(state.daily_data_notice_zh||"本系统策略生成的日线候选")+" · 截止 "+(state.daily_data_cutoff||"暂无");$("daily-table").innerHTML=table(["股票名称 / 代码","候选分数","参考买入区间","价格指导","信号日期","操作"],rows.map(x=>[
  stockButton(x),
  '<div class="score-line"><strong>'+number(x.normalized_score)+'</strong><span class="score-meter"><i style="width:'+Math.max(0,Math.min(100,Number(x.normalized_score)||0))+'%"></i></span></div>',
  esc(range(x.price_guidance)),
  planStatus(x),
  esc(x.signal_date),
  '<button data-stock="'+esc(x.symbol)+'">查看详情</button>'
]));}
function renderMonitor(){const query=$("monitor-search").value.trim().toLowerCase(),filter=$("monitor-filter").value;const daily=new Set((state.official_daily_candidates||[]).map(x=>x.symbol)),held=new Set([...(account.positions||[]),...(account.imported_account_snapshot?.positions||[])].map(x=>x.code||x.symbol));const opened=new Set([...$("monitor-table").querySelectorAll("details[open]")].map(e=>e.dataset.symbol));const rows=(state.intraday_monitor||[]).filter(x=>(String(x.name)+x.symbol).toLowerCase().includes(query)&&(filter==="all"||filter==="daily"&&daily.has(x.symbol)||filter==="holdings"&&held.has(x.symbol)||filter==="watch"&&watch.has(x.symbol)));$("session").textContent=zh(state.session);$("session").className="badge "+(state.session==="OPEN"?"good":"warning");$("monitor-table").innerHTML=table(["股票名称 / 代码","最新可用报价","涨跌幅","监控状态","参考买入区间","行情时间","数据质量"],rows.map(x=>[
  stockButton(x),
  '<span class="numeric">'+number(x.current_price??x.last)+'</span>',
  '<span class="'+(Number(x.change_pct)>0?"up":Number(x.change_pct)<0?"down":"")+'">'+(present(x.change_pct)?(Number(x.change_pct)>0?"+":"")+number(x.change_pct)+"%":"暂无")+'</span>',
  badge(x.state,x.state),esc(range(x.price_guidance)),
  '<span title="'+esc(x.quote_timestamp)+'">'+time(x.quote_timestamp)+'</span>',
  badge(x.data_quality,x.data_quality)
]));
$("monitor-table").insertAdjacentHTML("beforeend",'<div class="monitor-cards">'+rows.map(x=>'<details data-symbol="'+esc(x.symbol)+'" '+(opened.has(x.symbol)?'open':'')+'><summary><strong>'+esc(x.name||x.symbol)+'<small>'+esc(x.symbol)+'</small></strong><span>'+number(x.current_price??x.last)+'</span>'+badge(x.state,x.state)+'</summary>'+def("报价口径",quoteUsable(x)?"有效实时行情":"历史或未核验报价")+def("行情时间",time(x.quote_timestamp))+def("参考区间",range(x.price_guidance))+'<button data-stock="'+esc(x.symbol)+'">查看价格计划</button></details>').join("")+'</div>');
}
function planStatus(x){
  const g=x.price_guidance||{},date=g.valid_for||g.valid_for_date;
  const today=new Date().toLocaleDateString("sv-SE",{timeZone:"Asia/Shanghai"});
  let status=x.signal_stale?"信号待更新":zh(g.state||"PRICE_PLAN_MISSING");
  if(date && /^\d{4}-\d{2}-\d{2}$/.test(date)) status=date>today?"尚未到适用日 · "+status:date<today?"计划已过期 · "+status:"适用日 · "+status;
  const reason=!present(g.entry_lower)?(g.reason_codes||[]).map(zh).join("；"):"";
  return badge(status,x.signal_stale||date&&date!==today?"STALE":g.state)+'<small class="cell-note">'+esc(date?"适用 "+date:reason||"适用日期未提供")+'</small>'+(reason&&date?'<small class="cell-note">'+esc(reason)+'</small>':'');
}
function planApplies(g){
  const date=g?.valid_for||g?.valid_for_date;
  return date===new Date().toLocaleDateString("sv-SE",{timeZone:"Asia/Shanghai"});
}
function quoteUsable(x){
  const age=(Date.now()-Date.parse(x.quote_timestamp))/1000;
  return connected && state.session==="OPEN" && x.data_quality==="GOOD" && present(x.current_price??x.last) && Number.isFinite(age) && age>=-5 && age<=60;
}
function renderState(){
  const daily=state.official_daily_candidates||[],monitor=state.intraday_monitor||[];
  const quality=state.data_quality||"UNKNOWN",session=state.session||"OFFLINE";
  const cutoff=state.daily_data_cutoff||daily[0]?.signal_date||"尚无数据";
  const guided=daily.filter(x=>present(x.price_guidance?.entry_lower)&&present(x.price_guidance?.entry_upper)).length;
  $("connection").textContent=zh(quality)+" · "+zh(session);
  $("connection").className="connection "+(quality==="GOOD"?"good":["FAILED","OFFLINE"].includes(quality)?"bad":"");
  $("top-cutoff").textContent="日线 "+cutoff;
  $("overview-stats").innerHTML=stat("日选候选",daily.length+" 只","已生成价格区间 "+guided+" 只")+stat("盘中监控",monitor.length+" 只",zh(session))+stat("我的持仓",(account.positions||[]).length+" 只","本机账本")+stat("数据质量",zh(quality),"更新 "+time(state.last_update||state.updated_at));
  $("top-candidates").innerHTML=table(["排名","股票","候选分数","参考买入区间","状态",""],daily.slice(0,6).map((x,i)=>[
    '<span class="muted">'+String(i+1).padStart(2,"0")+'</span>',stockButton(x),
    '<div class="score-line"><strong>'+number(x.normalized_score)+'</strong><span class="score-meter"><i style="width:'+Math.max(0,Math.min(100,Number(x.normalized_score)||0))+'%"></i></span></div>',
    esc(range(x.price_guidance)),planStatus(x),
    '<button data-stock="'+esc(x.symbol)+'">详情</button>'
  ]));
  const items=[];
  if(quality!=="GOOD")items.push(["行情需要核验","当前："+zh(quality),"#system","检查"]);
  if(daily.some(x=>x.signal_stale))items.push(["日选等待更新","过期候选仅供回看","#daily","查看"]);
  if(guided<daily.length)items.push(["指导区间不完整",(daily.length-guided)+" 只候选缺少区间","#daily","查看"]);
  if(!(account.positions||[]).length)items.push(["登记实际持仓","当前本机账本为空","#entry","登记"]);
  const shown=items.length?items:[["暂无额外待处理","仍需核对有效日期","#system","状态"]];
  $("attention-count").textContent=items.length?items.length+" 项":"已检查";
  $("attention").innerHTML=shown.map(x=>'<div class="attention-item"><i class="attention-dot"></i><div><b>'+esc(x[0])+'</b><p>'+esc(x[1])+'</p></div><a href="'+x[2]+'">'+x[3]+' →</a></div>').join("");
  const stale=present(state.stale_quote_count)?Number(state.stale_quote_count):monitor.filter(x=>x.data_quality==="STALE").length;
  $("risk-summary").innerHTML='<div class="risk-row"><span class="risk-label"><i class="'+(quality==="GOOD"?"good":"warning")+'"></i>行情质量</span>'+badge(quality,quality)+'</div>'+ '<div class="risk-row"><span class="risk-label"><i class="'+(stale?"danger":"good")+'"></i>过期报价</span><strong>'+number(stale,0)+' 条</strong></div>'+ '<div class="risk-row"><span class="risk-label"><i class="warning"></i>执行方式</span><span>仅人工下单</span></div>';
  $("overview-freshness").innerHTML='<div class="status-row"><span>日线数据截止</span><strong>'+esc(cutoff)+'</strong></div><div class="status-row"><span>最后更新时间</span><strong>'+time(state.last_update||state.updated_at)+'</strong></div><div class="status-row"><span>当前数据源</span><strong>'+esc(zh(state.active_source||state.active_provider))+'</strong></div>';
  $("overview-model").innerHTML='<div class="model-record"><div><strong>'+esc(governance.champion_id||"暂无已登记冠军")+'</strong><small>当前冠军</small></div><div><strong>'+esc(zh(governance.state))+'</strong><small>治理状态</small></div><div><strong>'+number(governance.audit_count,0)+'</strong><small>审计事件</small></div></div><div class="status-row"><span>对证原则</span><strong>只比较未来结果</strong></div>';
  $("system-stats").innerHTML=stat("当前数据源",zh(state.active_source||state.active_provider))+stat("行情总数",number(state.quote_count,0))+stat("过期行情数",number(state.stale_quote_count,0))+stat("故障切换",number(state.fallback_count,0)+" 次");
  $("system-details").innerHTML=def("数据源类别",state.source_class==="PUBLIC DATA SOURCE"?"公开数据源":zh(state.source_class))+def("最后更新时间",time(state.last_update||state.updated_at))+def("日线数据截止",state.daily_data_cutoff)+def("日线数据状态",zh(state.daily_data_status))+def("数据年龄",present(state.data_age_seconds)?number(state.data_age_seconds,0)+" 秒":"暂无")+def("请求延迟",present(state.latency_ms)?number(state.latency_ms,0)+" 毫秒":"暂无")+def("连续更新",state.continuous_updates?"是":"否")+def("最近异常",state.last_error?zh(state.last_error):"无已报告异常");
  $("overview-freshness").innerHTML+=def("日线状态",zh(state.daily_data_status))+def("行情缓存说明",state.data_quality==="GOOD"?"行情已通过质量核验":"缓存报价不可视为实时行情；休市不会使旧缓存恢复有效");
  $("system-details").innerHTML+=def("后台检查时间",time(state.updated_at))+def("本页成功读取",time(lastRead))+def("本次连接成功报价",state.provider_telemetry?.quote_count??"未提供")+def("本次连接请求失败",state.provider_telemetry?.error_count??"未提供");
  renderDaily();renderMonitor();
}
function positionsTable(rows){return table(["股票","总数量（股）","可用数量","成本价"],rows.map(x=>[stockButton(x),number(x.total_quantity,0),number(x.available_quantity,0),number(x.average_cost)]));}
function renderAccount(){renderAllocation();$("account-stats").innerHTML=stat("本机账本现金",number(account.cash),"元 · 非券商实时资金")+stat("已实现盈亏",number(account.realized_pnl),"元 · 仅依据完整登记")+stat("本机持仓",(account.positions||[]).length+" 只")+stat("券商快照",(account.imported_account_snapshot?.positions||[]).length+" 只");$("holding-table").innerHTML=positionsTable(account.positions||[]);const imported=account.imported_account_snapshot;$("imported-date").textContent=imported?("来源："+imported.source_name+" · 截至 "+imported.as_of):"尚未确认导入券商持仓快照";$("imported-table").innerHTML=positionsTable(imported?.positions||[]);$("holding-plans").innerHTML=(account.price_guidance||[]).map(g=>'<div class="quiet-note"><strong>'+esc(g.name||g.symbol)+'</strong> · '+badge(g.state,g.state)+'<div class="status-row"><span>保护价</span><strong>'+number(g.protection_price)+'</strong></div><div class="status-row"><span>减仓区间</span><strong>'+number(g.reduce_lower)+' – '+number(g.reduce_upper)+'</strong></div><div class="status-row"><span>建议卖出</span><strong>'+number(g.suggested_sell_quantity,0)+' 股</strong></div></div>').join("");$("guidance").innerHTML=def("当前结论",guidance.action_zh||zh(guidance.state||"INSUFFICIENT_DATA"))+def("依据",guidance.explanation_zh||(guidance.reason_codes||[]).map(zh).join("；")||"暂无可核验的指导上下文")+def("数据截止",guidance.evidence_cutoff||"暂无")+def("人工执行",guidance.notice_zh||"所有成交由用户在券商客户端完成");}
function progressPanel(){
  if(progress.status!=="OK")return '<div class="notice">'+esc(progress.notice_zh||"前瞻进度尚未读取")+'</div>';
  return '<div class="stats">'+stat("已记录预测",progress.prediction_count)+stat("等待到期",progress.waiting_count)+stat("到期待结算",progress.overdue_count)+stat("已完成对证",progress.settled_count)+'</div>'+def("最近预测",time(progress.latest_prediction_at))+def("模型版本数",progress.model_count)+def("证据口径",progress.notice_zh)+(progress.overdue_count?'<div class="notice">存在到期未结算记录，需核验结算任务和结果数据；当前不能据此判定模型成熟。</div>':'');
}
function renderModels(){$("models").innerHTML=progressPanel()+'<div class="model-record"><div><strong>'+esc(governance.champion_id||"暂无已登记冠军")+'</strong><small>当前冠军</small></div><div><strong>'+esc(zh(governance.state))+'</strong><small>治理状态</small></div><div><strong>'+number(governance.audit_count,0)+'</strong><small>审计事件</small></div></div>'+def("模型记录",zh(modelHealth.model_status))+def("数据验证",zh(modelHealth.data_status))+def("预测对证成绩","接口尚未提供可比较的成绩明细")+def("晋级规则","仅使用预测后的未来数据；由人工审核批准");}
async function load(){
  if(refreshing)return;refreshing=true;
  try{
    const paths=["/api/state","/api/advisory/holdings","/api/advisory/guidance","/api/models/governance","/api/advisory/health","/api/models/progress"];
    const result=await Promise.allSettled(paths.map(p=>api(p)));
    if(result[0].status!=="fulfilled")throw Error("本地服务暂不可用，保留的数据仅供回看。");
    connected=true;lastRead=new Date().toISOString();state=result[0].value;
    $("alert").innerHTML=result.slice(1).some(r=>r.status==="rejected")?'<div class="notice">部分账户或模型接口读取失败，相关信息可能停留在上次读取。</div>':"";
    if(result[1].status==="fulfilled")account=result[1].value;
    if(result[2].status==="fulfilled")guidance=result[2].value;
    if(result[3].status==="fulfilled")governance=result[3].value;
    if(result[4].status==="fulfilled")modelHealth=result[4].value;
    progress=result[5].status==="fulfilled"?result[5].value:{status:"UNAVAILABLE",notice_zh:"前瞻进度接口暂不可用"};
    renderState();renderAccount();renderModels();
  }catch(e){
    connected=false;$("connection").className="connection bad";
    $("connection").textContent="连接中断";
    $("alert").innerHTML='<div class="notice">'+esc(e.message)+'</div>';
  }finally{
    refreshing=false;
    if($("stock-dialog").open)renderStock();
  }
}
async function action(button,fn,target){button.disabled=true;try{await fn();}catch(e){$(target).textContent=e.name==="AbortError"?"请求超时，请稍后重试。":e.message;}finally{button.disabled=false;}}
$("refresh").onclick=()=>action($("refresh"),async()=>{await api("/api/refresh",{},"refresh");await load();},"alert");
for(const id of ["daily-search","daily-sort","daily-plan"])$(id).addEventListener("input",renderDaily);
for(const id of ["monitor-search","monitor-filter"])$(id).addEventListener("input",renderMonitor);
function syncPreferenceButtons(){$("theme").setAttribute("aria-pressed",String(document.documentElement.dataset.theme==="light"));$("density").setAttribute("aria-pressed",String(document.body.classList.contains("compact")));}
$("theme").onclick=()=>{const theme=document.documentElement.dataset.theme==="dark"?"light":"dark";document.documentElement.dataset.theme=theme;prefs.set("quant-theme",theme);syncPreferenceButtons();if($("stock-dialog").open)loadChart();};
$("density").onclick=()=>{document.body.classList.toggle("compact");prefs.set("quant-compact",document.body.classList.contains("compact"));syncPreferenceButtons();};
syncPreferenceButtons();
function mergedStock(symbol){const daily=(state.official_daily_candidates||[]).find(x=>x.symbol===symbol)||{},monitor=(state.intraday_monitor||[]).find(x=>x.symbol===symbol)||{},holding=(account.positions||[]).find(x=>x.code===symbol)||{},imported=(account.imported_account_snapshot?.positions||[]).find(x=>x.code===symbol)||{};return {...imported,...holding,...daily,...monitor,symbol,name:monitor.name||daily.name||holding.name||imported.name||symbol,price_guidance:monitor.price_guidance||daily.price_guidance};}
function renderStock(){const x=mergedStock(activeStock),g=x.price_guidance||{};$("stock-title").textContent=x.name+" · "+activeStock;$("stock-summary").innerHTML='<button class="watch-button" id="toggle-watch">'+(watch.has(activeStock)?"★ 已加入自选":"☆ 加入自选")+'</button>'+def("最新可用行情",number(x.current_price??x.last))+def("行情时间",time(x.quote_timestamp))+def("日选信号日期",x.signal_date||"不在本期日选")+def("数据质量",zh(x.data_quality));$("toggle-watch").onclick=()=>{watch.has(activeStock)?watch.delete(activeStock):watch.add(activeStock);prefs.set("quant-watch",[...watch].slice(0,200));renderStock();renderState();};
const lo=Number(g.entry_lower),hi=Number(g.entry_upper),max=Number(g.maximum_acceptable_price),invalid=Number(g.invalidation_price);let track="";if([lo,hi,max,invalid].every(Number.isFinite)&&invalid>0&&invalid<lo&&lo<=hi&&hi<=max&&max>invalid){const span=max-invalid;const current=x.current_price??x.last;const validQuote=quoteUsable(x)&&planApplies(g);track='<div class="price-track" title="从失效价到最高可接受价；蓝色为参考买入区间"><span class="price-zone" style="left:'+((lo-invalid)/span*100)+'%;width:'+((hi-lo)/span*100)+'%"></span>'+(validQuote?'<span class="price-marker" title="当前有效行情 '+number(current)+'" style="left:'+Math.max(0,Math.min(100,(Number(current)-invalid)/span*100))+'%"></span>':'')+'</div>';}
$("price-plan").innerHTML='<h3>价格指导</h3><p>'+planStatus(x)+'</p>'+track+'<div class="plan-values"><div><small>参考买入区间</small><strong>'+esc(range(g))+'</strong></div><div><small>最高可接受价</small><strong>'+number(g.maximum_acceptable_price)+'</strong></div><div><small>失效价</small><strong>'+number(g.invalidation_price)+'</strong></div></div>'+def("适用日期",g.valid_for||g.valid_for_date||g.calculation_date||"以已有计划记录为准")+def("说明",(g.reason_codes||[]).map(zh).join("；")||(!g.entry_lower?"尚未提供可靠价格计划":"供人工核对；仍需核验当前行情与计划有效性"));
const current=x.current_price??x.last;
$("price-plan").innerHTML+=def("当前相对区间",!quoteUsable(x)?"行情未通过实时核验，暂不判断":!planApplies(g)?"计划不在当前适用日或缺少日期，暂不判断":!present(g.entry_lower)||!present(g.entry_upper)?"缺少参考区间":Number(current)<lo?"低于参考区间":Number(current)>hi?"高于参考区间":"位于参考区间")+def("本页成功读取",time(lastRead));
$("stock-reasons").innerHTML='<div class="quiet-note"><h3>候选依据</h3>'+((x.reasons||[]).length?(x.reasons||[]).map(s=>'<p>'+esc(s)+'</p>').join(""):'<p>暂无已记录的候选理由</p>')+'</div>'+def("策略版本",x.strategy_version||"暂无");}
document.addEventListener("click",e=>{const target=e.target.closest("[data-stock]");if(!target)return;lastFocus=target;activeStock=target.dataset.stock;renderStock();$("stock-dialog").showModal();loadChart();});
$("close-stock").onclick=()=>$("stock-dialog").close();$("stock-dialog").addEventListener("close",()=>{chartRequest++;if(chart){chart.dispose();chart=null;}if(lastFocus?.isConnected)lastFocus.focus();});$("chart-range").onchange=loadChart;
async function loadChart(){const request=++chartRequest;$("chart-note").textContent="正在读取本地日线…";if(chart){chart.dispose();chart=null;}$("stock-chart").innerHTML="";try{const data=await api("/api/stocks/history?symbol="+encodeURIComponent(activeStock)+"&limit="+$("chart-range").value);if(request!==chartRequest||!$("stock-dialog").open)return;const bars=data.bars||[];$("chart-note").textContent=data.notice_zh+" · "+(data.source||"未知来源")+" · "+(data.data_version||"")+" · "+bars.length+" 条";if(!bars.length){$("stock-chart").innerHTML=empty("暂无本地日线");return;}if(!window.echarts){$("stock-chart").innerHTML=empty("图表资源加载失败，请刷新页面");return;}chart=echarts.init($("stock-chart"));const dark=document.documentElement.dataset.theme==="dark",text=dark?"#a3b0c4":"#738096",line=dark?"#2b3b50":"#e6ebf1";chart.setOption({animation:false,legend:{data:["日K线","MA20","MA60"],textStyle:{color:text},top:0},tooltip:{trigger:"axis",confine:true},axisPointer:{link:[{xAxisIndex:"all"}]},grid:[{left:55,right:15,top:32,height:"51%"},{left:55,right:15,top:"70%",height:"14%"}],xAxis:[{type:"category",data:bars.map(x=>x.date),axisLabel:{color:text},axisLine:{lineStyle:{color:line}}},{type:"category",gridIndex:1,data:bars.map(x=>x.date),axisLabel:{show:false},axisLine:{show:false}}],yAxis:[{scale:true,axisLabel:{color:text},splitLine:{lineStyle:{color:line}}},{scale:true,gridIndex:1,axisLabel:{show:false},splitLine:{show:false}}],dataZoom:[{type:"inside",xAxisIndex:[0,1]},{type:"slider",xAxisIndex:[0,1],bottom:0,height:16,borderColor:line}],series:[{name:"日K线",type:"candlestick",data:bars.map(x=>[x.open,x.close,x.low,x.high]),itemStyle:{color:"#d94d5d",color0:"#19876f",borderColor:"#d94d5d",borderColor0:"#19876f"}},...[20,60].map(period=>({name:"MA"+period,type:"line",symbol:"none",lineStyle:{width:1,color:period===20?"#bb913b":"#8a77ca"},data:bars.map((x,i)=>i<period-1?null:bars.slice(i-period+1,i+1).reduce((sum,b)=>sum+b.close,0)/period)})),{name:"成交量（股）",type:"bar",xAxisIndex:1,yAxisIndex:1,data:bars.map(x=>({value:x.volume,itemStyle:{color:x.close>=x.open?"#d94d5d66":"#19876f66"}}))}]});}catch(e){if(request===chartRequest)$("chart-note").textContent="本地日线读取失败，请稍后重试。";}}
window.addEventListener("resize",()=>chart?.resize());
$("code").addEventListener("input",()=>{const x=mergedStock($("code").value);if(x.name!==$("code").value)$("name").value=x.name;});
function invalidateBuy(){buyToken="";$("buy-confirm").hidden=true;$("buy-preview").textContent="";}
$("buy-form").addEventListener("input",invalidateBuy);
$("buy-form").onsubmit=e=>{e.preventDefault();const button=e.submitter;action(button,async()=>{invalidateBuy();const payload={name:$("name").value.trim(),code:$("code").value.trim(),quantity:Number($("quantity").value),price:$("price").value};const fingerprint=JSON.stringify(payload);const result=await api("/api/advisory/buy-preview",payload);const current=JSON.stringify({name:$("name").value.trim(),code:$("code").value.trim(),quantity:Number($("quantity").value),price:$("price").value});if(current!==fingerprint)return;buyToken=result.confirmation_token;$("buy-preview").innerHTML='<div class="quiet-note">'+def("股票",payload.name+" "+payload.code)+def("数量",payload.quantity+" 股")+def("成交价",payload.price+" 元")+def("预估总成本",result.estimated_total_cost)+def("说明",result.notice_zh)+'</div>';$("buy-confirm").hidden=!buyToken;},"buy-preview");};
$("buy-confirm").onclick=()=>action($("buy-confirm"),async()=>{const token=buyToken;if(!token)return;buyToken="";$("buy-confirm").hidden=true;const result=await api("/api/advisory/buy-confirm",{confirmation_token:token});$("buy-preview").textContent=result.notice_zh||"成交记录已保存";await load();},"buy-preview");
async function scan(){const result=await api("/api/advisory/imports");const selected=$("import-file").value;$("import-file").innerHTML='<option value="">请选择文件</option>'+(result.files||[]).map(x=>'<option value="'+esc(x.file_id)+'">'+esc(x.file_name)+' · '+number(x.size_bytes/1024,1)+' KB</option>').join("");if([...$("import-file").options].some(x=>x.value===selected))$("import-file").value=selected;$("import-message").textContent=result.notice_zh||"扫描完成";}
function invalidateImport(){importToken="";$("import-confirm").hidden=true;$("import-preview").textContent="";}
$("import-file").onchange=invalidateImport;$("scan").onclick=()=>action($("scan"),async()=>{invalidateImport();await scan();},"import-message");
$("import-preview-button").onclick=()=>action($("import-preview-button"),async()=>{invalidateImport();const file=$("import-file").value;if(!file)throw Error("请先扫描并选择文件");const result=await api("/api/advisory/import-preview",{file_id:file});if($("import-file").value!==file)return;importToken=result.confirmation_token;$("import-preview").innerHTML=def("来源 / 日期",result.source_name+" / "+result.as_of)+def("可接受行数",result.accepted_rows)+def("拒绝行数",(result.rejected_rows||[]).length)+'<div class="table-scroll">'+table(["股票","数量","价格 / 成本"],(result.rows||[]).map(x=>[esc(x.name)+" "+esc(x.code),number(x.quantity??x.total_quantity,0),number(x.price??x.average_cost)]))+'</div>'+(result.rejected_rows||[]).map(x=>def("第 "+x.row_number+" 行",x.reason)).join("")+(result.warnings||[]).map(x=>'<p>'+esc(x)+'</p>').join("");$("import-message").textContent=result.notice_zh;$("import-confirm").hidden=!importToken;},"import-message");
$("import-confirm").onclick=()=>action($("import-confirm"),async()=>{const token=importToken;if(!token)return;importToken="";$("import-confirm").hidden=true;const result=await api("/api/advisory/import-confirm",{confirmation_token:token});$("import-message").textContent=result.notice_zh||"导入完成";await load();},"import-message");
$("safe-exit").onclick=()=>action($("safe-exit"),async()=>{if(!confirm("确定停止工作台及其管理的研究任务吗？"))return;await api("/api/system/quit",{},"safe-exit");$("exit-message").textContent="已提交安全退出请求，可关闭页面。";},"exit-message");
function renderAllocation(){const rows=(account.positions||[]).map(x=>({...x,cost:Number(x.average_cost)*Number(x.total_quantity)})).filter(x=>Number.isFinite(x.cost)&&x.cost>0);const total=rows.reduce((sum,x)=>sum+x.cost,0);$("allocation-panel").hidden=!total;$("allocation").innerHTML=rows.sort((a,b)=>b.cost-a.cost).map(x=>'<div class="allocation-row"><span>'+esc(x.name||x.code)+'</span><div class="allocation-track"><div class="allocation-fill" style="width:'+(x.cost/total*100)+'%"></div></div><span>'+number(x.cost/total*100,1)+'%</span></div>').join("");}
function updateNav(){const collapsed=document.body.classList.contains("nav-collapsed");$("collapse-nav").setAttribute("aria-expanded",String(!collapsed));document.querySelectorAll("[data-page]").forEach(el=>el.title=pageInfo[el.dataset.page][0]);}
document.body.classList.toggle("nav-collapsed",prefs.get("quant-nav-collapsed",false));updateNav();
$("collapse-nav").onclick=()=>{document.body.classList.toggle("nav-collapsed");prefs.set("quant-nav-collapsed",document.body.classList.contains("nav-collapsed"));updateNav();chart?.resize();};
load();setInterval(()=>{if(!document.hidden)load();},15000);
