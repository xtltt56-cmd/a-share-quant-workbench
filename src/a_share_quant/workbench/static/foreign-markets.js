"use strict";
(() => {
  if (selectedMarket === "CN") return;

  const view = $("foreign-view");
  const search = $("security-search-dialog");
  const searchInput = $("security-query");
  const detail = $("foreign-stock-dialog");
  const detailNote = $("foreign-stock-note");
  const favoriteButton = $("foreign-stock-favorite");
  const refreshButton = $("refresh");
  const marketName = $("market-switch").selectedOptions[0].textContent.slice(0, -2);
  let data, page, current, foreignChart, loading = false;
  let detailVersion = 0;
  let mutationVersion = 0;
  let quotes, loadingQuotes = false;
  let account, preview;

  function endpoint(action, symbol) {
    const query = new URLSearchParams({market:selectedMarket});
    if (symbol) query.set("symbol", symbol);
    return "/api/foreign/" + action + "?" + query;
  }

  function stock(x) {
    return '<button class="stock-button" data-foreign-symbol="' + esc(x.symbol) + '">'
      + esc(x.name) + '<small>' + esc(x.symbol + " · " + x.exchange) + '</small></button>';
  }

  function panel(title, content, subtitle = "") {
    return '<article class="panel"><div class="panel-heading"><div><h2>' + esc(title)
      + '</h2>' + (subtitle ? '<p>' + esc(subtitle) + '</p>' : '') + '</div></div>'
      + content + '</article>';
  }

  function price(value) {
    return present(value) ? number(value, data.market === "HK" ? 3 : 2) + " " + data.currency : "—";
  }

  function quoteTime(timestamp) {
    return new Date(timestamp).toLocaleString("zh-CN", {
      timeZone: data.market === "US" ? "America/New_York" : "Asia/Hong_Kong",
      month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false,
    });
  }

  function candidatePanel(limit) {
    const historical = !data.candidates.length && data.historical_candidates.length > 0;
    const candidates = historical ? data.historical_candidates : data.candidates;
    const coverage = data.coverage + "/" + data.pool.length + " 只满足 61 个连续交易日日线";
    const title = historical ? "历史日选回看" : "本市场日选";
    if (!candidates.length) return panel(title,
      empty(data.fresh ? "覆盖率不足 80%，等待更多有效日线" : "等待目标交易日日线"), coverage);
    const rows = candidates.slice(0, limit).map((x, i) => [
      String(i + 1), stock(x), number(x.score), number(x.ret20 * 100) + "%",
      number(x.ret60 * 100) + "%", number(x.volatility20 * 100) + "%", esc(price(x.close)),
    ]);
    return panel(title,
      '<div class="table-scroll">' + table(
        ["排名", "股票", "研究分数", "20日涨跌", "60日涨跌", "20日波动", "日线收盘"], rows
      ) + '</div>',
      coverage + " · 复权收益和波动率排序 · " + (historical ? "历史数据截至 " : "数据截至 ")
        + data.as_of + " · 对应下个交易日 " + candidates[0].next_session);
  }

  function poolPanel() {
    const rows = data.pool.map(x => [stock(x), x.eligible ? "已纳入" : "历史窗口不足",
      esc(data.symbol_errors[x.symbol] || "—")]);
    return '<details class="help"><summary>查看研究样本和数据缺口</summary>'
      + table(["股票", "排名资格", "最近刷新问题"], rows) + '</details>';
  }

  function render(nextPage) {
    const enteredMonitor = nextPage === "monitor" && page !== "monitor";
    page = nextPage;
    if (enteredMonitor) loadQuotes();
    $("page-subtitle").textContent = marketName + " · 本市场日线研究";
    if (!data) {
      view.innerHTML = empty("正在读取市场数据");
      return;
    }
    const status = data.running ? "正在刷新"
      : data.error ? "刷新失败"
      : data.fresh ? "日线已更新"
      : data.as_of ? "日线过期" : "待刷新";
    $("connection").className = "connection " + (status === "日线已更新" ? "good" : "warn");
    $("connection").textContent = [data.name, data.session, status].join(" · ");
    $("top-cutoff").textContent = data.as_of ? "日线 " + data.as_of : "日线待补齐";
    refreshButton.disabled = data.running;
    if (page === "overview") {
      view.innerHTML = '<div class="stats">'
        + stat("日线状态", status, "目标 " + data.expected)
        + stat("日选候选", data.candidates.length + " 只", "研究样本 " + data.pool.length + " 只")
        + stat("我的自选", data.favorites.length + " 只", data.currency)
        + stat("数据来源", data.source, "最近完整日线 " + (data.as_of || "暂无"))
        + '</div>' + candidatePanel(10);
    } else if (page === "daily") {
      view.innerHTML = candidatePanel(data.pool.length) + poolPanel();
    } else if (page === "watchlist" || page === "monitor") {
      const monitor = page === "monitor";
      const rows = data.favorites.map(x => {
        const quote = quotes?.quotes[x.symbol];
        return [
        stock(x), esc(price(x.bar?.close)), esc(x.bar?.date || "暂无"),
        ...(monitor ? [quote ? esc(price(quote.price)) : "—", quote
          ? esc(quoteTime(quote.timestamp) + (quote.fresh ? "" : " · 已过期")) : "暂无"] : []),
        '<button type="button" data-foreign-remove="' + esc(x.symbol) + '">移除</button>',
        ];
      });
      const headers = ["股票", "最近收盘", "交易日",
        ...(monitor ? ["最近成交", "行情时间（市场当地）"] : []), "操作"];
      view.innerHTML = panel(monitor ? "自选行情观察" : "自选股票",
        '<div class="toolbar"><button type="button" data-foreign-search>添加股票</button></div>'
          + (rows.length ? '<div class="table-scroll">' + table(headers, rows) + '</div>'
                         : empty("暂无" + data.name + "自选")),
        monitor ? [data.source, quotes?.source, quotes?.notice_zh].filter(Boolean).join(" · ")
          : data.source + " · 收盘数据");
    } else if (page === "holdings") {
      renderHoldings();
    } else if (page === "system") {
      const settings = data.market === "US"
        ? '<p>可直接使用新浪财经。若有 Alpaca Basic 免费账户，可在本机 <code>.env</code> 中设置 '
          + '<code>APCA_API_KEY_ID</code> 和 <code>APCA_API_SECRET_KEY</code>，将下次刷新改为 SIP 历史日线，'
          + '并启用 IEX 单交易所监控。</p>'
        : '<p>新浪财经提供原始港股日线和前复权因子。</p>';
      view.innerHTML = panel("市场数据",
        def("行情来源", data.source) + def("计价币种", data.currency)
        + def("下次刷新来源", data.next_source)
        + def("最近完整交易日", data.as_of) + def("应有交易日", data.expected)
        + def("最近刷新", time(data.updated_at)) + settings) + poolPanel();
    } else {
      const messages = {
        models:"目前使用公开量价规则排序，暂无验证通过的本市场模型",
        agent:"当前市场的股票研究可在日选与详情页查看",
      };
      view.innerHTML = panel(pageInfo[page][0], empty(messages[page]));
    }
    if (search.open) searchResults();
    if (detail.open) updateFavorite();
  }

  async function loadMarket() {
    if (loading) return;
    loading = true;
    const version = mutationVersion;
    try {
      const result = await api(endpoint("state"));
      if (version !== mutationVersion) return;
      if (JSON.stringify(data) !== JSON.stringify(result)) {
        data = result;
        render(page);
      }
      const message = data.error || (!data.network ? "当前使用离线模式，可查看本地已保存的日线" : "");
      $("alert").textContent = message;
    } catch (error) {
      $("connection").textContent = marketName + " · 读取失败";
      $("alert").textContent = error.message;
      if (!data) view.innerHTML = empty("市场服务暂不可用");
    } finally {
      loading = false;
    }
  }

  async function loadQuotes() {
    if (loadingQuotes) return;
    loadingQuotes = true;
    const version = mutationVersion;
    try {
      const result = await api(endpoint("quotes"));
      if (version === mutationVersion) quotes = result;
    } catch {
      quotes = {quotes:{}, notice_zh:"盘中行情读取失败"};
    } finally {
      loadingQuotes = false;
      if (page === "monitor" && data) render(page);
    }
  }

  function renderHoldings() {
    if (!view.querySelector("#foreign-ledger")) {
      view.innerHTML = '<div id="foreign-account-summary"></div>'
        + panel("登记资金或已完成成交", `
          <form id="foreign-ledger">
            <div class="form-grid">
              <label>记录类型<select name="action">
                <option value="deposit">入金</option><option value="buy">买入</option>
                <option value="sell">卖出</option>
              </select></label>
              <label>当地日期<input name="date" type="date" required></label>
              <label data-deposit>入金金额（${esc(data.currency)}）<input name="amount" inputmode="decimal"></label>
              <label data-fill hidden>股票<select name="symbol">
                ${data.pool.map(x => '<option value="' + esc(x.symbol) + '">' + esc(x.symbol + " " + x.name) + '</option>').join("")}
              </select></label>
              <label data-fill hidden>股数<input name="quantity" inputmode="numeric"></label>
              <label data-fill hidden>成交单价（${esc(data.currency)}）<input name="price" inputmode="decimal"></label>
              <label data-fill hidden>实付总费用（${esc(data.currency)}）<input name="fee" inputmode="decimal" value="0"></label>
            </div>
            <div class="toolbar">
              <button type="submit">预览记录</button>
              <button type="button" id="foreign-ledger-confirm" class="primary" hidden>确认记账</button>
            </div>
          </form>
          <div id="foreign-ledger-preview" role="status"></div>`);
      const form = $("foreign-ledger");
      form.elements.date.value = account?.today || "";
      form.elements.action.onchange = () => {
        const deposit = form.elements.action.value === "deposit";
        form.querySelector("[data-deposit]").hidden = !deposit;
        form.querySelectorAll("[data-fill]").forEach(label => { label.hidden = deposit; });
      };
      form.oninput = clearPreview;
      form.onsubmit = event => {
        event.preventDefault();
        action(event.submitter, async () => {
          clearPreview();
          const fields = Object.fromEntries(new FormData(form));
          const body = {id:crypto.randomUUID(), ...fields};
          const result = await api(endpoint("ledger-preview"), body, "foreign-markets");
          if (!form.isConnected
              || JSON.stringify(fields) !== JSON.stringify(Object.fromEntries(new FormData(form)))) return;
          preview = body;
          $("foreign-ledger-confirm").hidden = false;
          const info = [def("记账后可用资金", cash(result.cash)), def("待结算资金", cash(result.pending))];
          if (result.event.settles_on) info.unshift(def("预计交收日", result.event.settles_on));
          if (result.board_lot && data.market === "HK") {
            info.push(def("每手股数 · 名录 " + result.board_lot_as_of, result.board_lot));
            if (result.event.quantity % result.board_lot) info.push(def("成交数量", "包含碎股"));
          }
          if (result.levies && Object.keys(result.levies).length) {
            info.push('<p class="muted">按当前港交所名录估计的交易费用分项。记账使用上方填写的实际总费用。</p>');
            info.push(...Object.entries(result.levies).map(([name, amount]) => def(name, cash(amount))));
          }
          $("foreign-ledger-preview").innerHTML = info.join("");
        }, "foreign-ledger-preview");
      };
      $("foreign-ledger-confirm").onclick = event => action(event.currentTarget, async () => {
        if (!preview) return;
        account = await api(endpoint("ledger-record"), preview, "foreign-markets");
        if (!form.isConnected) return;
        clearPreview();
        renderHoldings();
        $("foreign-ledger-preview").textContent = "已保存到" + marketName + "本地账本";
      }, "foreign-ledger-preview");
      loadAccount();
    }
    if (!account) {
      $("foreign-account-summary").innerHTML = empty("正在读取本机账本");
      return;
    }
    const rows = account.positions.map(x => [
      stock(x), String(x.quantity), String(x.available), esc(price(x.average)),
      esc(x.bar ? price(x.bar.close) + " · " + x.bar.date : "暂无"),
      esc(x.bar ? cash(x.bar.close * x.quantity - Number(x.cost)) : "暂无"),
    ]);
    const history = account.history.map(x => [
      esc(x.date), {buy:"买入", sell:"卖出", deposit:"入金"}[x.action],
      esc(x.symbol || "—"), esc(x.quantity ?? "—"), esc(cash(x.notional || x.amount)),
      esc(x.fee ? cash(x.fee) : "—"), esc(x.settles_on || "—"),
    ]);
    $("foreign-account-summary").innerHTML = '<div class="stats">'
      + stat("已结算可用资金", cash(account.cash))
      + stat("待结算卖出款", cash(account.pending))
      + stat("已实现盈亏", cash(account.realized))
      + stat("持仓记录", account.positions.length + " 只") + '</div>'
      + panel("本机账本持仓", rows.length ? '<div class="table-scroll">'
        + table(["股票", "数量", "可卖数量", "平均成本", "最近收盘", "未实现盈亏"], rows)
        + '</div>' : empty("暂无" + data.name + "持仓记录"))
      + (history.length ? panel("最近记录", '<div class="table-scroll">'
        + table(["日期", "类型", "证券", "股数", "成交金额", "实付费用", "预计交收日"], history)
        + '</div>') : "");
  }

  function cash(value) {
    return number(value, 2) + " " + data.currency;
  }

  function clearPreview() {
    preview = undefined;
    $("foreign-ledger-confirm").hidden = true;
    $("foreign-ledger-preview").textContent = "";
  }

  async function loadAccount() {
    try {
      account = await api(endpoint("account"));
      if (!view.querySelector("#foreign-ledger")) return;
      const date = $("foreign-ledger").elements.date;
      if (!date.value) date.value = account.today;
      renderHoldings();
    } catch (error) {
      if ($("foreign-ledger-preview")) $("foreign-ledger-preview").textContent = error.message;
    }
  }

  function searchResults() {
    const query = searchInput.value.trim().toUpperCase();
    const candidates = (data?.pool || []).filter(x =>
      x.symbol.includes(query) || x.name.toUpperCase().includes(query)
      || x.symbol === query.replace("-", "."));
    $("security-search-status").textContent = "当前市场 " + marketName + " · " + candidates.length + " 只";
    $("security-results").innerHTML = candidates.length
      ? table(["股票", "自选"], candidates.map(x => [stock(x),
          data.favorites.some(item => item.symbol === x.symbol) ? "已添加"
          : '<button data-foreign-add="' + esc(x.symbol) + '">添加</button>']))
      : empty("当前研究样本中没有匹配股票");
  }

  function openSearch() {
    $("security-search-title").textContent = "搜索" + marketName;
    searchInput.value = "";
    searchResults();
    search.showModal();
    searchInput.focus();
  }

  function updateFavorite() {
    const added = data?.favorites.some(x => x.symbol === current);
    favoriteButton.textContent = added ? "★ 移除自选" : "☆ 加入自选";
    favoriteButton.dataset.foreignAction = added ? "remove" : "add";
  }

  function drawChart(rows) {
    if (!rows.length || !window.echarts) return;
    const bars = rows.slice(-120);
    foreignChart = window.echarts.init($("foreign-stock-chart"));
    const days = bars.map(bar => bar.date);
    foreignChart.setOption({
      animation:false,
      tooltip:{trigger:"axis", confine:true},
      grid:[{left:70, right:18, top:16, height:"59%"}, {left:70, right:18, top:"72%", height:"14%"}],
      xAxis:[{type:"category", data:days, axisLabel:{show:false}},
        {type:"category", gridIndex:1, data:days, axisLabel:{color:"#9aaac0"}}],
      yAxis:[{scale:true, axisLabel:{color:"#9aaac0"}}, {gridIndex:1, axisLabel:{show:false}}],
      dataZoom:[{type:"inside", xAxisIndex:[0, 1]}],
      series:[
        {name:"不复权日 K 线", type:"candlestick", data:bars.map(x => [x.open, x.close, x.low, x.high])},
        {name:"成交量", type:"bar", xAxisIndex:1, yAxisIndex:1, data:bars.map(x => x.volume)},
      ],
    });
  }

  async function openStock(symbol) {
    if (search.open) search.close();
    current = symbol;
    const version = ++detailVersion;
    $("foreign-stock-title").textContent = symbol;
    detailNote.textContent = "读取日线中…";
    updateFavorite();
    detail.showModal();
    try {
      const quote = await api(endpoint("security", symbol));
      if (version !== detailVersion) return;
      $("foreign-stock-title").textContent = quote.name + " · " + quote.symbol;
      const last = quote.bars.at(-1);
      detailNote.textContent = quote.source + " · " + (last
        ? "不复权价格 · " + last.date + " · 收盘 " + price(last.close)
        : "暂无日线，请先刷新本市场数据");
      if (quote.board_lot_as_of && quote.board_lot) {
        detailNote.textContent += " · 每手 " + quote.board_lot + " 股（港交所 " + quote.board_lot_as_of + "）";
      }
      drawChart(quote.bars);
    } catch (error) {
      if (version === detailVersion) detailNote.textContent = error.message;
    }
  }

  async function changeFavorite(symbol, operation) {
    mutationVersion++;
    data = await api(endpoint("favorite"), {symbol, action:operation}, "foreign-markets");
    mutationVersion++;
    render(page);
    if (page === "monitor") loadQuotes();
  }

  document.querySelector('.sidebar-bottom a').hidden = true;
  window.renderForeignMarket = render;
  $("open-security-search").onclick = openSearch;
  $("close-security-search").onclick = () => search.close();
  $("close-foreign-stock").onclick = () => detail.close();
  searchInput.oninput = searchResults;
  detail.addEventListener("close", () => {
    detailVersion++;
    foreignChart?.dispose();
    foreignChart = null;
  });
  window.addEventListener("resize", () => foreignChart?.resize());
  favoriteButton.onclick = () => action(favoriteButton,
    () => changeFavorite(current, favoriteButton.dataset.foreignAction), "foreign-stock-note");
  document.addEventListener("click", event => {
    const button = event.target.closest(
      "[data-foreign-search], [data-foreign-symbol], [data-foreign-add], [data-foreign-remove]"
    );
    if (!button) return;
    if (button.hasAttribute("data-foreign-search")) openSearch();
    else if (button.dataset.foreignSymbol) openStock(button.dataset.foreignSymbol);
    else if (button.dataset.foreignAdd) action(button,
      () => changeFavorite(button.dataset.foreignAdd, "add"), "security-search-status");
    else action(button, () => changeFavorite(button.dataset.foreignRemove, "remove"), "alert");
  });
  refreshButton.onclick = async () => {
    await action(refreshButton, async () => {
      await api(endpoint("refresh"), {}, "foreign-markets");
      await loadMarket();
    }, "alert");
    refreshButton.disabled = Boolean(data?.running);
  };
  route();
  loadMarket();
  setInterval(() => { if (!document.hidden) loadMarket(); }, 4000);
  setInterval(() => { if (!document.hidden && page === "monitor") loadQuotes(); }, 15000);
})();
