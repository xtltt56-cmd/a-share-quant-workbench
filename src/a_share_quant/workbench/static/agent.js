"use strict";
(() => {
  let status = null, dirty = false, job = null, timer = null, sequence = 0;
  const names = {READY:"已配置 · 可试用",NOT_ENABLED:"标准工作流",NOT_CONFIGURED:"缺少 API 凭据",SDK_MISSING:"缺少可选 SDK",CONFIG_REJECTED:"配置核验失败",RUNNING:"正在分析",QUEUED:"等待分析",CANCELLING:"正在取消",CANCELLED:"已取消",TIMEOUT:"分析超时",FAILED:"未通过核验 / 服务不可用",SUCCEEDED:"已完成证据检查",MODEL_MISSING:"本机未找到模型",TOOLS_UNSUPPORTED:"工具能力不可用",BUDGET_EXHAUSTED:"费用限额已阻止调用",STALE:"证据已变化，请重新分析",CONFIGURED_NOT_VERIFIED:"已配置，尚未真实验证",NOT_PROBED:"尚未检查"};
  const active = value => ["QUEUED","RUNNING","CANCELLING"].includes(value);
  const label = value => names[value] || value || "暂无";
  names.PRICE_REVIEW_REQUIRED = "云端计价需要复核，已暂停调用";
  names.BUDGET_UNAVAILABLE = "费用账本无法核验，已暂停云端调用";
  Object.assign(names, {MODEL_AVAILABLE_NOT_GENERATED:"连接与模型已核验 · 未生成内容",MODEL_MISSING:"所选模型不可用",API_KEY_REJECTED:"API 凭据未通过认证",ACCESS_DENIED:"API 访问被拒绝",EXPERIMENT_EXHAUSTED:"本次实验累计额度已用尽",AWAITING_APPROVAL:"等待确认 · 收费分析关闭",CONNECTION_NOT_VERIFIED:"尚未通过官方连接核验",AGENT_OUTPUT_SCHEMA_INVALID:"模型回答字段不符合校验要求",AGENT_JSON_INVALID:"模型回答不是有效 JSON"});
  Object.assign(labels, {
    AWAITING_APPROVAL:"等待确认 · 收费分析关闭", CONNECTION_NOT_VERIFIED:"尚未通过官方连接核验",
    EXPERIMENT_EXHAUSTED:"本次实验累计额度已用尽", PRICE_REVIEW_REQUIRED:"云端计价需要复核",
    BUDGET_UNAVAILABLE:"费用账本无法核验", MODEL_AVAILABLE_NOT_GENERATED:"连接与模型已核验 · 未生成内容",
    API_KEY_REJECTED:"API 凭据未通过认证", ACCESS_DENIED:"API 访问被拒绝",
    NO_VALIDATED_REALTIME_QUOTE:"没有通过核验的实时行情", NOT_IN_CURRENT_CANDIDATES:"不在当前日选候选中",
    CALENDAR_UNAVAILABLE:"无法核验交易日历", DAILY_INPUT_STALE:"日线输入过期", DAILY_INPUT_FUTURE:"日线输入日期超前",
    DAILY_UPDATE_FAILED:"日线更新未成功完成", PRICE_PLAN_NOT_APPLICABLE:"价格计划不适用于当前日期",
    PUBLIC_RISK_EXPIRED:"公告风险检查已过期", PUBLIC_RISK_REFRESH_FAILED:"公告风险更新失败",
    PUBLIC_RISK_FUTURE_TIMESTAMP:"公告风险记录时间超前"
  });
  function fields() {
    const cloud = $("agent-mode").value === "CLOUD";
    $("agent-cloud-fields").hidden = !cloud;
    $("agent-local-field").hidden = $("agent-mode").value !== "LOCAL";
  }
  function renderStatus() {
    if (!status) return;
    const cfg = status.config;
    const mode = cfg.enabled ? (cfg.backend === "LOCAL" ? "本地模型" : "DeepSeek V4.1 Flash") : "标准工作流";
    $("agent-mode-label").textContent = mode + " · " + (cfg.enabled ? label(status.status) : "不调用 AI");
    $("agent-status").textContent = label(status.status);
    $("agent-budget-usage").textContent = status.budget_notice_zh || (
      "今日已结算及保守预留（美元）：" + (status.today_reserved_and_spent_usd ?? "暂不可核验")
    );
    $("agent-experiment-usage").textContent = "本次实验累计已结算及保守预留（人民币）：" + (status.experiment?.reserved_and_spent ?? "0") + " / " + (status.experiment?.limit ?? cfg.experiment_budget_cny) + " 元 · 不跨日重置";
    $("agent-connection-status").textContent = "连接核验：" + label(status.probe.status) + (status.probe.checked_at ? " · " + time(status.probe.checked_at) : "");
    $("agent-analysis-mode").textContent = cfg.enabled
      ? mode + " · " + (cfg.backend === "LOCAL" ? cfg.local_model : cfg.cloud_model) + " · 仅分析公开股票信息，试用阶段"
      : "标准工作流模式；可查看核验依据，或在设置中启用可选 Agent。";
    $("agent-start").disabled = status.status !== "READY" || !!job && active(job.status);
    $("agent-cancel").hidden = !job || !active(job.status);
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
  }
  async function refreshStatus() {
    try { status = await api("/api/agent/status"); renderStatus(); }
    catch { $("agent-status").textContent = "助手状态暂不可用"; $("agent-start").disabled = true; }
  }
  function renderJob(value) {
    if (!value || value.symbol !== activeStock || !$("stock-dialog").open) return;
    $("agent-cancel").hidden = !active(value.status);
    const answer = value.result;
    let html = '<p><strong>' + esc(label(value.status)) + '</strong></p><p>' + esc(value.notice_zh) + '</p>';
    if (answer) {
      html += '<p class="agent-claim-note">' + esc(answer.summary_zh) + '</p>';
      for (const [title, rows] of [["支持依据",answer.supporting_facts],["反对因素",answer.opposing_factors],["缺失与限制",answer.missing_conditions]]) {
        html += '<h4>' + title + '</h4>' + (rows.length ? '<ul>' + rows.map(x => '<li>' + esc(title === "缺失与限制" ? zh(x) : x) + '</li>').join("") + '</ul>' : '<p class="muted">暂无补充</p>');
      }
      html += def("后端 / 模型",(value.backend === "LOCAL" ? "本地模型" : "云端 API") + " / " + (value.actual_model || value.model)) + def("数据截止",value.data_cutoff || "未提供") + def("来源",value.source || "未提供") + def("查询时间",time(value.observed_at)) + def("生成时间",time(value.generated_at));
      if (value.cached) html += '<p class="muted">复用仍有效的短期结果，未再次调用模型。</p>';
      html += '<p class="muted">结构和显式证据检查不等于全部文字已证明正确；指导价仍以上方后台结果为准。</p>';
    } else if (!active(value.status)) {
      html += '<p class="muted">原有价格计划和规则事实保持独立，可点击“查看核验依据”；不会自动调用其他模型。</p>';
    }
    if (value.backend === "CLOUD" && value.cost_status) html += def("估算费用（人民币 / 美元）",(value.cost_cny ?? "无法确定") + " 元 / " + (value.cost_usd ?? "无法确定") + " 美元") + def("费用口径",value.cost_status === "CONSERVATIVE_RESERVED" ? "未知消耗，保守预留" : value.cached ? "复用缓存，未调用 API" : "按返回 Token 和峰值单价估算");
    if ((value.trace || []).length) html += '<details><summary>查看查询过程与证据</summary>' + value.trace.map(x => '<p>' + esc(x.tool) + '<br><small>' + esc(x.evidence_id) + '</small></p>').join("") + '</details>';
    if (value.failure_fields?.length) html += '<p class="muted">未通过校验的字段：' + esc(value.failure_fields.join("、")) + '</p>';
    $("agent-result").innerHTML = html;
    $("agent-start").disabled = active(value.status) || status?.status !== "READY";
  }
  async function poll(id, symbol, version) {
    if (version !== sequence) return;
    try {
      const value = await api("/api/agent/task?id=" + encodeURIComponent(id));
      if (version !== sequence || symbol !== activeStock) return;
      job = value; renderJob(value);
      if (active(value.status)) timer = setTimeout(() => poll(id,symbol,version),1000);
      else { await refreshStatus(); renderJob(value); }
    } catch { if (version === sequence) { $("agent-result").textContent = "任务状态暂不可用；可以取消或稍后重试，不会自动重新生成。"; } }
  }
  $("agent-settings-form").addEventListener("input", () => { dirty = true; fields(); });
  $("agent-mode").addEventListener("change", fields);
  $("agent-settings-form").onsubmit = event => {
    event.preventDefault();
    action(event.submitter, async () => {
      if (!status) throw Error("请等待助手配置读取完成");
      const mode = $("agent-mode").value;
      const payload = {...status.config, enabled:mode !== "WORKFLOW", backend:mode === "WORKFLOW" ? status.config.backend : mode, local_model:$("agent-local-model").value.trim(), daily_budget_usd:$("agent-daily-budget").value, task_budget_usd:$("agent-task-budget").value, experiment_budget_cny:$("agent-experiment-budget").value, payment_authorized:$("agent-payment").checked, generation_authorized:$("agent-generation").checked};
      status = await api("/api/agent/config",payload,"agent"); dirty = false; renderStatus();
      $("agent-settings-message").textContent = status.notice_zh;
    },"agent-settings-message");
  };
  $("agent-probe").onclick = () => action($("agent-probe"),async () => {
    if (dirty) throw Error("请先保存模式，再检查已保存配置");
    status = await api("/api/agent/probe",{},"agent"); renderStatus();
    $("agent-settings-message").textContent = (status.probe.notice_zh || label(status.probe.status)) + (status.probe.models?.length ? " 可选模型：" + status.probe.models.join("、") : "");
  },"agent-settings-message");
  $("agent-start").onclick = () => action($("agent-start"),async () => {
    const symbol = activeStock, version = ++sequence;
    const started = await api("/api/agent/start",{symbol,question:$("agent-question").value},"agent");
    if (version !== sequence || symbol !== activeStock || !$("stock-dialog").open) {
      await api("/api/agent/cancel",{task_id:started.task_id},"agent"); return;
    }
    job = started;
    renderJob(job); await refreshStatus(); await poll(job.task_id,symbol,version);
  },"agent-result").finally(renderStatus);
  async function cancel() {
    if (job && active(job.status)) {
      const id = job.task_id;
      const cancelled = await api("/api/agent/cancel",{task_id:id},"agent");
      if (job?.task_id === id) { job = cancelled; renderJob(job); }
    }
  }
  $("agent-cancel").onclick = () => action($("agent-cancel"),cancel,"agent-result");
  $("agent-config-link").onclick = () => $("stock-dialog").close();
  $("stock-dialog").addEventListener("close",() => {
    sequence++; clearTimeout(timer); cancel().catch(() => {});
  });
  document.addEventListener("click",event => {
    if (!event.target.closest("[data-stock]")) return;
    sequence++; clearTimeout(timer);
    if (job && active(job.status)) cancel().catch(() => {});
    job = null; $("agent-result").textContent = "仅解释当前股票的公开证据，不调用持仓工具。";
    renderStatus();
  });
  refreshStatus();
  setInterval(async () => {
    if (document.hidden) return;
    await refreshStatus();
    if (job && !active(job.status) && $("stock-dialog").open) {
      try { job = await api("/api/agent/task?id=" + encodeURIComponent(job.task_id)); renderJob(job); }
      catch { $("agent-result").textContent = "助手证据暂不可核验；保留后台事实。"; }
    }
  },15000);
})();
