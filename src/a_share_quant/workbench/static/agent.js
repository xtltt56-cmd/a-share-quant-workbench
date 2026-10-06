"use strict";
(() => {
  let status = null, dirty = false, running = false, stopping = false, job = null;
  let results = [], total = 0, finished = 0;
  const selected = new Set(), extras = new Set(), limit = 10;
  const names = {READY:"可开始分析",NOT_ENABLED:"标准工作流",NOT_CONFIGURED:"缺少 API 密钥",SDK_MISSING:"缺少可选 SDK",CONFIG_REJECTED:"配置核验失败",RUNNING:"正在分析",QUEUED:"等待分析",CANCELLING:"正在停止",CANCELLED:"已取消",TIMEOUT:"分析超时",FAILED:"未通过核验 / 服务不可用",SUCCEEDED:"已完成证据检查",MODEL_MISSING:"所选模型不可用",TOOLS_UNSUPPORTED:"工具能力不可用",BUDGET_EXHAUSTED:"费用限额已阻止调用",STALE:"证据已变化，请重新分析",NOT_PROBED:"尚未检查",PRICE_REVIEW_REQUIRED:"云端计价需要复核",BUDGET_UNAVAILABLE:"费用账本无法核验",MODEL_AVAILABLE_NOT_GENERATED:"连接与模型已核验",API_KEY_REJECTED:"API 密钥未通过认证",ACCESS_DENIED:"API 访问被拒绝",EXPERIMENT_EXHAUSTED:"累计实验额度已用尽",AWAITING_APPROVAL:"收费分析尚未启用",CONNECTION_NOT_VERIFIED:"尚未核验连接",AGENT_OUTPUT_SCHEMA_INVALID:"模型回答字段校验失败",AGENT_JSON_INVALID:"模型回答不是有效 JSON",WAIT_FOR_DATA:"等待补全数据",RISK_REVIEW:"优先核查风险",OBSERVE:"保持观察",RESEARCH_CANDIDATE:"值得进一步研究"};
  Object.assign(labels, {NO_VALIDATED_REALTIME_QUOTE:"没有通过核验的实时行情",NOT_IN_CURRENT_CANDIDATES:"不在当前日选候选中",CALENDAR_UNAVAILABLE:"无法核验交易日历",DAILY_INPUT_STALE:"日线输入过期",DAILY_INPUT_FUTURE:"日线输入日期超前",DAILY_UPDATE_FAILED:"日线更新未完成",PRICE_PLAN_NOT_APPLICABLE:"价格计划不适用于当前日期",PUBLIC_RISK_EXPIRED:"公告检查已过期",PUBLIC_RISK_REFRESH_FAILED:"公告更新失败",PUBLIC_RISK_FUTURE_TIMESTAMP:"公告记录时间超前"});
  const active = value => ["QUEUED","RUNNING","CANCELLING"].includes(value);
  const label = value => names[value] || zh(value) || "暂无";
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  function universe() {
    const rows = new Map();
    for (const row of [...(state.official_daily_candidates || []), ...(state.intraday_monitor || [])]) {
      if (/^\d{6}$/.test(row.symbol)) rows.set(row.symbol, {symbol:row.symbol, name:row.name || rows.get(row.symbol)?.name || row.symbol});
    }
    for (const symbol of [...extras, ...selected]) if (!rows.has(symbol)) rows.set(symbol, {symbol,name:symbol});
    return [...rows.values()];
  }
  const stockName = symbol => universe().find(row => row.symbol === symbol)?.name || symbol;
  function visibleStocks() {
    const query = $("agent-stock-search").value.trim().toLowerCase();
    return universe().filter(row => (row.name + row.symbol).toLowerCase().includes(query));
  }
  function renderSelection() {
    $("agent-stock-list").innerHTML = visibleStocks().map(row => '<label class="agent-stock-choice"><input type="checkbox" data-agent-select="'+esc(row.symbol)+'" '+(selected.has(row.symbol)?'checked ':'')+(running?'disabled':'')+'><span><strong>'+esc(row.name)+'</strong><small>'+esc(row.symbol)+'</small></span></label>').join("") || '<p class="muted">没有匹配的股票，可以直接添加证券代码。</p>';
    $("agent-selected-count").textContent = "已选 " + selected.size + " 只";
    $("agent-step-selection").textContent = selected.size + " / " + limit;
    for (const id of ["agent-select-all","agent-clear","agent-add-code"]) $(id).disabled = running;
    $("agent-add-form").querySelector("button").disabled = running;
    renderControls();
  }
  function fields() {
    $("agent-cloud-fields").hidden = $("agent-mode").value !== "CLOUD";
    $("agent-local-field").hidden = $("agent-mode").value !== "LOCAL";
  }
  function renderControls() {
    $("agent-start").disabled = running || status?.status !== "READY" || !selected.size;
    $("agent-cancel").hidden = !running;
    $("agent-cancel").disabled = stopping;
    $("agent-question").disabled = running;
    $("agent-web-research").disabled = running || $("agent-question").value !== "research";
    $("agent-start-notice").textContent = running ? (stopping ? "正在停止；不会继续队列中的股票。" : "切换页面不会中断任务；可随时停止本批。") : status?.status !== "READY" ? ((status?.notice_zh || "正在读取助手配置…") + " 请在“API 与模式设置”中处理。") : !selected.size ? "先勾选股票，再开始分析；本页不会自动产生 API 费用。" : "已准备好，点击按钮才开始联网检索与模型分析。";
    $("agent-step-run").textContent = running ? (stopping ? "正在停止" : "执行中") : results.length ? "已结束" : "未启动";
  }
  function renderStatus() {
    if (!status) return;
    const cfg = status.config, credential = status.credential || {};
    const mode = cfg.enabled ? (cfg.backend === "LOCAL" ? "本地模型" : "DeepSeek V4.1 Flash") : "标准工作流";
    $("agent-mode-label").textContent = mode + " · " + label(status.status);
    $("agent-status").textContent = label(status.status);
    $("agent-step-config").textContent = label(status.status);
    $("agent-analysis-mode").textContent = mode + " · " + (cfg.enabled ? (cfg.backend === "LOCAL" ? cfg.local_model : cfg.cloud_model) + " · 仅公开股票研究" : "原有量化工作流保持可用，启用 Agent 后可扩展研究");
    $("agent-key-status").textContent = credential.error ? "本机密钥文件无法解密或核验，请重新保存。" : credential.configured ? "已配置密钥 · " + ({SAVED:"网页保存（Windows 加密）",ENVIRONMENT:"已有环境变量",LEGACY_FILE:"已有本机配置"}[credential.source] || "本机配置") + " · 不显示密钥内容" : "尚未配置，请在下方粘贴 API 密钥。";
    $("agent-key-save").disabled = !credential.can_save || running || status.status === "RUNNING";
    $("agent-key-remove").disabled = !(credential.saved || credential.error) || running || status.status === "RUNNING";
    $("agent-budget-usage").textContent = status.budget_notice_zh || "今日已结算及保守预留（美元）：" + (status.today_reserved_and_spent_usd ?? "暂不可核验");
    $("agent-experiment-usage").textContent = "实验累计（人民币）：" + (status.experiment?.reserved_and_spent ?? "0") + " / " + (status.experiment?.limit ?? cfg.experiment_budget_cny) + " 元 · 不跨日重置";
    $("agent-connection-status").textContent = "连接核验：" + label(status.probe.status) + (status.probe.checked_at ? " · " + time(status.probe.checked_at) : "");
    if (!dirty) {
      $("agent-mode").value = cfg.enabled ? cfg.backend : "WORKFLOW";
      $("agent-local-model").value = cfg.local_model;
      $("agent-daily-budget").value = cfg.daily_budget_usd;
      $("agent-task-budget").value = cfg.task_budget_usd;
      $("agent-experiment-budget").value = cfg.experiment_budget_cny;
      $("agent-payment").checked = cfg.payment_authorized;
      $("agent-generation").checked = cfg.generation_authorized;
      fields();
    }
    $("agent-generation").disabled = !cfg.generation_authorized && status.probe.status !== "MODEL_AVAILABLE_NOT_GENERATED";
    renderControls();
  }
  async function refreshStatus() {
    try { status = await api("/api/agent/status"); renderStatus(); return status; }
    catch { status = null; $("agent-status").textContent = "助手状态暂不可用"; renderControls(); return null; }
  }
  function sourceLink(source) {
    try { const url = new URL(source.url); if (url.protocol !== "https:" || url.username || url.password) return esc(source.title); }
    catch { return esc(source.title); }
    return '<a target="_blank" rel="noopener noreferrer" href="'+esc(source.url)+'">'+esc(source.title)+' ↗</a>';
  }
  function resultCard(value) {
    const answer = value.result, workflow = value.workflow_evidence;
    let html = '<article class="panel agent-result-card"><div class="panel-heading"><div><h2>'+esc(value.name || stockName(value.symbol))+' <small class="muted">'+esc(value.symbol)+'</small></h2><p>'+esc(label(value.status))+'</p></div><button data-stock="'+esc(value.symbol)+'">股票详情</button></div>';
    if (workflow) html += '<div class="agent-rule-summary"><h3>工作流 · 原始规则结论</h3>'+def("候选分数",number(workflow.candidate?.normalized_score))+def("参考价格区间",range(workflow.price_guidance))+def("公告检查",riskZh(workflow.event_risk?.level || "UNKNOWN"))+def("阻塞条件",workflow.blocking_reasons.length ? workflow.blocking_reasons.map(zh).join("；") : "无已报告阻塞项")+'</div>';
    if (answer) {
      html += '<div class="agent-verdict"><span class="muted">Agent · 补充判断</span><h3>'+esc(label(answer.research?.conclusion || answer.status))+'</h3><p>'+esc(answer.summary_zh)+'</p></div>';
      if (answer.research) {
        html += '<div class="agent-insights">'+answer.research.insights.map(item => '<div><h4>证据观察</h4><p>'+esc(item.observation_zh)+'</p><h4>分析推断（待验证）</h4><p>'+esc(item.interpretation_zh)+'</p><small class="muted">'+(item.source_ids?.length ? '网络来源 '+esc(item.source_ids.join("、")) : '本轮规则 / 历史指标')+'</small></div>').join("")+'</div>';
        html += '<h4>后续核验条件</h4><ul>'+answer.research.next_steps.map(item => '<li>'+esc(item)+'</li>').join("")+'</ul>';
      }
      for (const [title, rows] of [["支持依据",answer.supporting_facts],["反对因素",answer.opposing_factors],["缺失与限制",answer.missing_conditions]]) html += '<details '+(title === "缺失与限制" && rows.length ? 'open' : '')+'><summary>'+title+' · '+rows.length+'</summary><ul>'+rows.map(item => '<li>'+esc(title === "缺失与限制" ? zh(item) : item)+'</li>').join("")+'</ul></details>';
      html += '<p class="agent-snapshot-note">本次研究快照 · '+esc(time(value.generated_at))+'；不是持续更新的交易信号。新事实出现后请重新分析。</p>';
    } else html += '<p>'+esc(value.notice_zh || label(value.status))+'</p>';
    const metrics = value.research_evidence?.metrics || {};
    if (Object.keys(metrics).length) html += '<details><summary>可核验的历史指标</summary>'+Object.values(metrics).map(metric => def(metric.label,number(metric.value) + " " + metric.unit)).join("")+ '<p class="muted">'+esc(value.research_evidence.method_notice_zh)+'</p></details>';
    if (value.web_research) {
      const sources = new Map(), limitations = new Set();
      for (const evidence of value.web_evidence || []) {
        for (const [id, source] of Object.entries(evidence.sources || {})) sources.set(id, source);
        for (const text of evidence.limitations || []) limitations.add(text);
      }
      html += '<details class="agent-sources" open><summary>联网证据 · '+sources.size+' 个来源</summary>';
      if (!sources.size) html += '<p class="muted">本轮未取得可用网络来源；不能声称已核实最新信息。</p>';
      for (const [id, source] of sources) html += '<div class="agent-source"><small>'+esc(id)+' · '+esc(source.host)+' · '+esc({PAGE_EXCERPT:"已读取正文节选",TITLE_ONLY:"仅新闻标题",SEARCH_SNIPPET:"仅搜索摘要",READ_FAILED:"正文读取失败"}[source.reading_status] || "未读取")+'</small><p>'+sourceLink(source)+'</p><small class="muted">发布：'+esc(source.published_at || "日期不明，不能视为最新")+' · '+esc({RECENT:"近一周",OLDER:"旧资料",UNKNOWN:"时间不明"}[source.date_status] || "时间不明")+'</small></div>';
      html += '<p class="muted">'+esc([...limitations].join(" "))+'</p></details>';
    }
    if (value.trace?.length) html += '<details><summary>查询过程与技术信息</summary>'+value.trace.map(item => '<p>'+esc({get_stock_context:"规则事实",get_stock_research:"历史与公告线索",search_public_web:"公开网络检索",read_public_page:"读取来源正文",get_workflow_health:"工作流健康"}[item.tool] || item.tool)+'<small class="agent-evidence-id">'+esc(item.evidence_id)+'</small></p>').join("")+def("模型",value.actual_model || value.model)+def("数据截止",value.data_cutoff)+def("提示版本",value.prompt_version)+'</details>';
    if (value.backend === "CLOUD" && value.cost_usd !== null && value.cost_usd !== undefined) html += '<p class="muted">费用估算：'+esc(value.cost_cny ?? "未知")+' 元 / '+esc(value.cost_usd)+' 美元 · '+(value.cost_status === "CONSERVATIVE_RESERVED" ? "未确认用量，保守预留" : value.cached ? "复用缓存" : "按 API 返回用量估算")+'</p>';
    if (value.failure_code) html += '<p class="muted">诊断：'+esc(label(value.failure_code))+'</p>';
    if (value.failure_fields?.length) html += '<p class="muted">字段检查：'+esc(value.failure_fields.join("；"))+'</p>';
    return html + '<p class="muted">研究辅助，不是收益保证；Agent 不修改指导价或执行交易。</p></article>';
  }
  function renderResults() {
    const list = [...results];
    if (job && active(job.status)) list.push(job);
    $("agent-result").innerHTML = list.map(resultCard).join("") || '<div class="panel agent-empty"><h3>还没有分析结果</h3><p>勾选股票后点击开始分析，可比较原始规则与 Agent 补充判断。</p></div>';
    $("agent-progress").textContent = running ? "本批进度 " + finished + " / " + total + (job ? " · 当前 " + stockName(job.symbol) + "（" + job.symbol + "）" : "") : total ? "本批已结束：" + finished + " / " + total + " 只已处理" + (finished < total ? "，其余未调用" : "") : "";
    renderControls();
  }
  async function runBatch() {
    if (running || !selected.size || status?.status !== "READY") return;
    const queue = [...selected], question = $("agent-question").value;
    const web = question === "research" && $("agent-web-research").checked;
    running = true; stopping = false; results = []; job = null; total = queue.length; finished = 0;
    renderSelection(); renderResults();
    try {
      for (const symbol of queue) {
        if (stopping) break;
        const latest = await refreshStatus();
        if (!latest || latest.status !== "READY") break;
        try {
          job = await api("/api/agent/start", {symbol,question,web_research:web}, "agent");
          renderResults();
          if (stopping && active(job.status)) await api("/api/agent/cancel", {task_id:job.task_id}, "agent");
          while (active(job.status)) {
            await pause(750);
            job = await api("/api/agent/task?id=" + encodeURIComponent(job.task_id));
            renderResults();
          }
          results.push(job); finished++; renderResults();
          if (["BUDGET_EXHAUSTED","CANCELLED"].includes(job.status)) break;
          job = null;
          await pause(150);
        } catch (error) {
          if (job && active(job.status)) {
            try { await api("/api/agent/cancel", {task_id:job.task_id}, "agent"); } catch {}
            stopping = true;
          }
          results.push({symbol,status:"FAILED",notice_zh:error.message}); finished++; job = null; renderResults();
          break; // Unknown transport state must not trigger duplicate paid requests.
        }
      }
    } finally {
      running = false; job = null; await refreshStatus(); renderSelection(); renderResults();
    }
  }
  $("agent-stock-list").addEventListener("change", event => {
    const input = event.target.closest("[data-agent-select]"); if (!input || running) return;
    if (input.checked && selected.size >= limit) { input.checked = false; $("agent-selection-message").textContent = "每批最多选择 " + limit + " 只，请分批分析。"; return; }
    input.checked ? selected.add(input.dataset.agentSelect) : selected.delete(input.dataset.agentSelect); renderSelection();
  });
  $("agent-stock-search").oninput = renderSelection;
  $("agent-select-all").onclick = () => { if (running) return; const rows = visibleStocks(); for (const row of rows) if (selected.size < limit) selected.add(row.symbol); $("agent-selection-message").textContent = rows.some(row => !selected.has(row.symbol)) ? "已达到每批上限，剩余股票请分批处理。" : "已选择当前筛选列表。"; renderSelection(); };
  $("agent-clear").onclick = () => { if (!running) { selected.clear(); $("agent-selection-message").textContent = ""; renderSelection(); } };
  function addStock(symbol) {
    if (running) { $("agent-selection-message").textContent = "本批正在运行，结束后可修改选择。"; return; }
    if (!/^\d{6}$/.test(symbol)) return;
    if (selected.size >= limit && !selected.has(symbol)) { $("agent-selection-message").textContent = "已达到每批上限，请先取消部分股票。"; return; }
    extras.add(symbol); selected.add(symbol); renderSelection();
  }
  $("agent-add-form").onsubmit = event => { event.preventDefault(); addStock($("agent-add-code").value.trim()); $("agent-add-code").value = ""; };
  document.addEventListener("quant:state-updated", renderSelection);
  document.addEventListener("click", event => { const button = event.target.closest("[data-agent-stock]"); if (button) { addStock(button.dataset.agentStock); location.hash = "agent"; } });
  $("stock-agent-entry").onclick = () => { addStock(activeStock); $("stock-dialog").close(); location.hash = "agent"; };
  $("agent-question").onchange = renderControls;
  $("agent-start").onclick = runBatch;
  $("agent-cancel").onclick = async () => {
    stopping = true; renderControls();
    if (job && active(job.status)) {
      try { await api("/api/agent/cancel", {task_id:job.task_id}, "agent"); }
      catch { $("agent-start-notice").textContent = "停止请求未获确认；队列已停止，请稍后核验当前任务。"; }
    }
  };
  $("agent-settings-form").addEventListener("input", () => { dirty = true; fields(); });
  $("agent-mode").onchange = fields;
  $("agent-settings-form").onsubmit = event => {
    event.preventDefault();
    action(event.submitter, async () => {
      if (!status) throw Error("请等待助手配置读取完成");
      const mode = $("agent-mode").value;
      status = await api("/api/agent/config", {...status.config,enabled:mode !== "WORKFLOW",backend:mode === "WORKFLOW" ? status.config.backend : mode,local_model:$("agent-local-model").value.trim(),daily_budget_usd:$("agent-daily-budget").value,task_budget_usd:$("agent-task-budget").value,experiment_budget_cny:$("agent-experiment-budget").value,payment_authorized:$("agent-payment").checked,generation_authorized:$("agent-generation").checked}, "agent");
      dirty = false; renderStatus(); $("agent-settings-message").textContent = status.notice_zh;
    }, "agent-settings-message");
  };
  $("agent-probe").onclick = () => action($("agent-probe"), async () => {
    if (dirty) throw Error("请先保存模式，再核验连接");
    status = await api("/api/agent/probe", {}, "agent"); renderStatus();
    $("agent-settings-message").textContent = (status.probe.notice_zh || label(status.probe.status)) + (status.probe.models?.length ? " · " + status.probe.models.join("、") : "");
  }, "agent-settings-message");
  $("agent-key-form").onsubmit = event => {
    event.preventDefault();
    action($("agent-key-save"), async () => {
      const value = $("agent-api-key").value; $("agent-api-key").value = "";
      status = await api("/api/agent/credential", {api_key:value}, "agent"); renderStatus();
      $("agent-key-message").textContent = status.notice_zh;
    }, "agent-key-message").finally(renderStatus);
  };
  $("agent-key-remove").onclick = () => action($("agent-key-remove"), async () => {
    status = await api("/api/agent/credential/remove", {}, "agent"); renderStatus(); $("agent-key-message").textContent = status.notice_zh;
  }, "agent-key-message").finally(renderStatus);
  renderSelection(); refreshStatus();
  setInterval(() => { if (!document.hidden) refreshStatus(); }, 15000);
})();
