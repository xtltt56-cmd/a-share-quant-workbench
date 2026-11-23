(() => {
  const dialog = $("security-search-dialog");
  const input = $("security-query");
  const results = $("security-results");
  const status = $("security-search-status");
  const refresh = $("refresh-security-catalog");
  const exchanges = {SSE: "沪市", SZSE: "深市", BSE: "北交所"};
  const quoteLabels = {
    LIVE: "有效报价", CLOSED_CACHE: "收盘缓存", STALE: "报价过期",
    UNVERIFIED: "行情时间未核验", NO_QUOTE: "暂无报价"
  };
  let saved = [];
  let matches = [];
  let searchVersion = 0;
  let searchTimer;
  let changing = false;

  function findSaved(symbol, exchange) {
    return saved.find(row => row.symbol === symbol && (!exchange || row.exchange === exchange));
  }

  function stockLink(row) {
    const market = exchanges[row.exchange] || "交易所待核验";
    return '<button class="stock-button" data-stock="'+esc(row.symbol)+'" data-exchange="'+
      esc(row.exchange)+'" data-stock-name="'+esc(row.name)+'">'+esc(row.name)+
      '<small>'+esc(row.symbol)+' · '+esc(market)+'</small></button>';
  }

  function renderMatches() {
    results.innerHTML = matches.map(row => {
      const exists = !!findSaved(row.symbol, row.exchange);
      return '<div class="security-row">'+stockLink(row)+
        '<button type="button" class="security-action" data-add-symbol="'+esc(row.symbol)+
        '" data-exchange="'+esc(row.exchange)+'" '+(exists ? "disabled" : "")+'>'+
        (exists ? "已加入" : "加入自选")+'</button></div>';
    }).join("");
  }

  function quoteDetails(row) {
    if (!row.verified) return '<span class="muted">证券待核验</span>';
    const label = quoteLabels[row.quote_status] || "暂无报价";
    const moment = row.quote_time
      ? " · " + (row.quote_status === "UNVERIFIED" ? "采集 " : "") + time(row.quote_time)
      : "";
    return '<span class="muted watchlist-quote">'+number(row.price)+
      '<small>'+esc(label+moment)+'</small></span>';
  }

  function renderSaved() {
    $("watchlist-items").innerHTML = saved.map(row =>
      '<div class="security-row">'+stockLink(row)+
      quoteDetails(row)+
      '<button type="button" class="security-action" data-remove-symbol="'+esc(row.symbol)+
      '" data-exchange="'+esc(row.saved_exchange)+'" aria-label="移除 '+esc(row.name)+
      '">移除</button></div>'
    ).join("") || empty("还没有自选股票，可以按代码或简称搜索添加。");
  }

  function applySaved(rows) {
    const identity = items => items.map(row => row.exchange + ":" + row.symbol).join(",");
    const selectionChanged = identity(saved) !== identity(rows);
    saved = rows;
    watch = new Set(saved.map(item => item.symbol));
    renderSaved();
    if (selectionChanged) {
      renderMatches();
      renderMonitor();
      if ($("stock-dialog").open) renderStock();
    }
  }

  async function search() {
    const version = ++searchVersion;
    const query = input.value.trim();
    try {
      const data = await api("/api/securities/search?q="+encodeURIComponent(query));
      if (version !== searchVersion || !dialog.open) return;
      matches = data.rows;
      refresh.hidden = !data.allow_network;
      refresh.disabled = data.refreshing;
      const messages = [];
      if (data.updated_at) {
        messages.push("名录更新于 "+time(data.updated_at));
        if (data.stale || data.refresh_failed) messages.push("使用上次有效名录");
      } else if (!data.refreshing) {
        messages.push(data.allow_network ? "尚无可用证券名录，可点击更新" : "离线模式且暂无证券名录");
      }
      if (data.refreshing) messages.push("正在更新名录…");
      if (query && data.updated_at) {
        messages.push(data.matches ? "找到 "+data.matches+" 只" : "没有匹配的股票");
        if (data.matches > matches.length) messages.push("显示前 "+matches.length+" 只");
      }
      status.textContent = messages.join(" · ");
      renderMatches();
      if (data.refreshing) searchTimer = setTimeout(search, 1400);
    } catch (error) {
      if (version === searchVersion && dialog.open) status.textContent = error.message;
    }
  }

  function openSearch() {
    if (!dialog.open) dialog.showModal();
    input.focus();
    clearTimeout(searchTimer);
    search();
  }

  async function write(payload, message) {
    if (changing) return;
    changing = true;
    if (message) message.textContent = "";
    try {
      const data = await api("/api/watchlist", payload, "watchlist");
      applySaved(data.rows);
      if (message) message.textContent = payload.action === "remove" ? "已移除自选" : "已加入自选";
    } catch (error) {
      if (message) message.textContent = error.message;
    } finally {
      changing = false;
    }
  }

  window.quantWatchlist = {
    has(symbol, exchange) { return !!findSaved(symbol, exchange); },
    async toggle(symbol, security) {
      const message = $("stock-watch-message");
      const existing = findSaved(symbol, security?.exchange);
      if (existing) {
        await write({action:"remove", symbol, exchange:existing.saved_exchange}, message);
        return;
      }
      try {
        const exchange = security?.exchange || (await resolveSecurity(symbol)).exchange;
        await write({action:"add", symbol, exchange}, message);
      } catch (error) {
        message.textContent = error.message;
      }
    }
  };

  async function resolveSecurity(symbol) {
    const {rows} = await api("/api/securities/search?q="+encodeURIComponent(symbol));
    const exact = rows.filter(row => row.symbol === symbol);
    if (exact.length !== 1) throw Error("请先用股票搜索核验这只证券。");
    return exact[0];
  }

  let downloadVersion = 0;
  const download = $("download-stock-history");
  const downloadStatus = $("stock-history-status");
  $("stock-dialog").addEventListener("close", () => {
    ++downloadVersion;
    download.disabled = false;
  });
  $("chart-range").addEventListener("change", () => {
    ++downloadVersion;
    download.disabled = false;
  });
  download.onclick = async () => {
    const version = ++downloadVersion;
    const symbol = activeStock;
    const limit = $("chart-range").value;
    const current = () => version === downloadVersion && $("stock-dialog").open;
    download.disabled = true;
    downloadStatus.textContent = "正在提交日线下载…";
    try {
      const security = activeSecurity || await resolveSecurity(symbol);
      if (!current()) return;
      activeSecurity = security;
      const args = {exchange:security.exchange, symbol, limit};
      const endpoint = "/api/stocks/history/status?"+new URLSearchParams(args);
      let status = await api("/api/stocks/history/download", args, "watchlist");
      for (let attempt = 0; current() && status.status === "RUNNING" && attempt < 45; ++attempt) {
        downloadStatus.textContent = status.notice_zh;
        await new Promise(resolve => setTimeout(resolve, 1400));
        if (!current()) return;
        status = await api(endpoint);
      }
      if (!current()) return;
      downloadStatus.textContent = status.notice_zh || "请稍后重新打开详情查看。";
      if (status.status === "DONE") await loadChart();
    } catch (error) {
      if (current()) downloadStatus.textContent = error.message;
    } finally {
      if (current()) download.disabled = false;
    }
  };

  $("open-security-search").onclick = openSearch;
  $("watchlist-search").onclick = openSearch;
  $("close-security-search").onclick = () => dialog.close();
  dialog.addEventListener("close", () => {
    ++searchVersion;
    clearTimeout(searchTimer);
  });
  input.addEventListener("input", () => {
    ++searchVersion;
    clearTimeout(searchTimer);
    matches = [];
    renderMatches();
    searchTimer = setTimeout(search, 160);
  });
  refresh.onclick = async () => {
    refresh.disabled = true;
    try {
      await api("/api/securities/refresh", {}, "watchlist");
      clearTimeout(searchTimer);
      await search();
    } catch (error) {
      status.textContent = error.message;
      refresh.disabled = false;
    }
  };
  results.addEventListener("click", event => {
    const target = event.target.closest("[data-add-symbol], [data-stock]");
    if (!target) return;
    if (target.hasAttribute("data-stock")) {
      dialog.close();
      return;
    }
    write({action:"add", symbol:target.dataset.addSymbol, exchange:target.dataset.exchange}, status);
  });
  $("watchlist-items").addEventListener("click", event => {
    const target = event.target.closest("[data-remove-symbol]");
    if (target) {
      write({action:"remove", symbol:target.dataset.removeSymbol, exchange:target.dataset.exchange},
        $("watchlist-message"));
    }
  });

  async function loadSaved() {
    if (changing) return;
    try {
      const previous = prefs.get("quant-watch", []);
      const symbols = Array.isArray(previous) ? previous.filter(code => /^[0-9]{6}$/.test(code)) : [];
      if (symbols.length) {
        const {rows} = await api("/api/watchlist", {action:"migrate", symbols}, "watchlist");
        prefs.set("quant-watch", []);
        applySaved(rows);
      } else {
        const {rows} = await api("/api/watchlist");
        if (!changing) applySaved(rows);
      }
    } catch (error) {
      $("watchlist-message").textContent = error.message;
    }
  }
  loadSaved();
  setInterval(() => {
    if (!document.hidden) loadSaved();
  }, 15000);
})();
