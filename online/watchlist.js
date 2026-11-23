(() => {
  const input = document.getElementById("security-query");
  const results = document.getElementById("security-results");
  const items = document.getElementById("watchlist-items");
  const catalogStatus = document.getElementById("catalog-status");
  const watchStatus = document.getElementById("watchlist-status");
  const count = document.getElementById("watchlist-count");
  const markets = {SSE: "沪市", SZSE: "深市", BSE: "北交所"};
  const storeKey = "a-share-online-watchlist";
  const stockKey = row => row.exchange + ":" + row.symbol;
  const searchKey = value => value.normalize("NFKC").toLowerCase().replace(/\s/g, "");
  let catalog = null;
  let selected = [];

  try {
    const stored = JSON.parse(localStorage.getItem(storeKey) || "[]");
    if (Array.isArray(stored)) selected = stored.filter(row => row && markets[row.exchange] &&
      /^[0-9]{6}$/.test(row.symbol) && typeof row.name === "string");
  } catch {
    watchStatus.textContent = "暂时无法读取此浏览器的自选。";
  }

  function select(next, message) {
    try {
      localStorage.setItem(storeKey, JSON.stringify(next));
    } catch {
      watchStatus.textContent = "浏览器无法保存自选，请检查存储权限。";
      return;
    }
    selected = next;
    watchStatus.textContent = message;
    render();
  }

  function line(row, isSelected, action, disabled = false) {
    const wrapper = document.createElement("div");
    wrapper.className = "security-row";
    const identity = document.createElement("span");
    identity.className = "stock-button";
    identity.textContent = row.name;
    const detail = document.createElement("small");
    detail.textContent = row.symbol + " · " + markets[row.exchange];
    identity.append(detail);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "security-action";
    button.textContent = isSelected ? "移除" : (disabled ? "已加入" : "加入自选");
    button.disabled = disabled;
    button.setAttribute("aria-label", button.textContent + " " + row.name);
    button.addEventListener("click", action);
    wrapper.append(identity, button);
    return wrapper;
  }

  function rank(stock, query) {
    const name = searchKey(stock.name);
    if (stock.symbol === query) return 0;
    if (stock.symbol.startsWith(query)) return 1;
    if (name === query) return 2;
    return name.includes(query) ? 3 : 4;
  }

  function render() {
    const saved = new Set(selected.map(stockKey));
    count.textContent = selected.length + " 只";
    items.replaceChildren(...selected.map(row => line(row, true, () => {
      select(selected.filter(item => stockKey(item) !== stockKey(row)), "已移除 " + row.name);
    })));
    if (!selected.length) items.textContent = "还没有自选股票。";
    results.replaceChildren();
    if (!catalog) return;
    const query = searchKey(input.value);
    const matches = query ? catalog.rows.map(row => [rank(row, query), row])
      .filter(([score]) => score < 4)
      .sort((a, b) => a[0] - b[0] || a[1].symbol.localeCompare(b[1].symbol)) : [];
    const stamp = new Date(catalog.updated_at).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai"});
    catalogStatus.textContent = "名录更新于 " + stamp + (query ?
      (matches.length ? " · 找到 " + matches.length + " 只" : " · 没有匹配的股票") : "");
    if (matches.length > 30) catalogStatus.textContent += " · 显示前 30 只";
    results.append(...matches.slice(0, 30).map(([, row]) => line(row, false, () => {
      if (selected.length >= 200) {
        watchStatus.textContent = "自选已达 200 只，请先移除部分股票。";
        return;
      }
      select([...selected, row], "已加入 " + row.name);
    }, saved.has(stockKey(row)))));
  }

  input.addEventListener("input", render);
  render();
  fetch("./catalog.json").then(response => {
    if (!response.ok) throw new Error("catalog");
    return response.json();
  }).then(data => {
    if (!Array.isArray(data.rows) || !data.updated_at) throw new Error("schema");
    catalog = data;
    render();
  }).catch(() => {
    catalogStatus.textContent = "证券名录暂不可用，可以继续查看已保存的自选。";
  });
})();
