(function (root) {
  const categories = ["All", "Stocks", "Funds", "Futures", "Forex", "Crypto", "Indices", "Bonds", "Economy", "Options"];
  function filterInstruments(records, query, filters = {}) {
    const words = query.trim().toUpperCase().split(/\s+/).filter(Boolean);
    return records.filter((row) => {
      const categoryMatches = !filters.category || filters.category === "All" || row.category === filters.category || (filters.category === "Futures" && row.exchangeType === "Futures");
      const display = row.displaySymbol || (row.symbol + (row.type === "Perpetual" ? ".P" : ""));
      const text = `${display} ${row.symbol} ${row.description} ${row.exchange} ${row.type} ${row.exchangeType}`.toUpperCase();
      const datedMatches = !["Futures", "Option"].includes(row.type) || ["Futures", "Options"].includes(filters.category) || filters.type === row.type || filters.exchangeType === row.exchangeType;
      return categoryMatches && datedMatches && (!filters.exchange || row.exchange === filters.exchange) && (!filters.type || row.type === filters.type)
        && (!filters.exchangeType || row.exchangeType === filters.exchangeType) && words.every((word) => text.includes(word));
    }).sort((a, b) => {
      const exact = (row) => {
        const names = [row.symbol, row.displaySymbol || row.symbol + (row.type === "Perpetual" ? ".P" : "")].map((name) => name.toUpperCase());
        const text = query.trim().toUpperCase();
        return names.includes(text) ? 0 : names.some((name) => name.startsWith(text)) ? 1 : 2;
      };
      return exact(a) - exact(b) || a.symbol.localeCompare(b.symbol) || a.exchange.localeCompare(b.exchange) || a.type.localeCompare(b.type);
    });
  }
  const api = { categories, filterInstruments };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (!root.document) return;
  let catalogs = new Map(), pending = new Map(), active = null;
  async function getJson(url) {
    const response = await fetch(url);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Market catalog unavailable");
    return payload;
  }
  function open({ sources, source, symbol, onSelect }) {
    if (active) active.close();
    const previousFocus = document.activeElement;
    const backdrop = document.createElement("div"); backdrop.className = "symbol-picker-backdrop";
    backdrop.innerHTML = `<section class="symbol-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="symbol-picker-title">
      <header><h2 id="symbol-picker-title">Symbol search</h2><button type="button" class="symbol-picker-close" aria-label="Close symbol search" title="Close"><i data-lucide="x"></i></button></header>
      <div class="symbol-picker-input"><i data-lucide="search"></i><input aria-label="Search all symbols" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="symbol-picker-results" autocomplete="off"><button type="button" aria-label="Clear symbol search" title="Clear"><i data-lucide="circle-x"></i></button></div>
      <div class="symbol-picker-categories" role="tablist" aria-label="Asset class"></div>
      <div class="symbol-picker-filters"><select aria-label="Symbol sources"></select><select aria-label="Instrument types"></select><select aria-label="Exchange types"></select></div>
      <div class="symbol-picker-status" role="status"></div><div id="symbol-picker-results" role="listbox" aria-label="Symbol results"></div>
      <footer class="symbol-picker-warnings"></footer></section>`;
    document.body.append(backdrop);
    const input = backdrop.querySelector("input"), results = backdrop.querySelector('[role="listbox"]');
    const [exchangeFilter, typeFilter, exchangeTypeFilter] = backdrop.querySelectorAll("select");
    const status = backdrop.querySelector('[role="status"]'), warningsEl = backdrop.querySelector("footer");
    let category = "All", remote = [], selected = 0, visible = [], timer, queryId = 0, loading = 0, warnings = new Map();
    const closed = () => active !== instance;
    const close = () => { clearTimeout(timer); queryId++; backdrop.remove(); if (active === instance) active = null; previousFocus?.focus(); };
    const instance = { close }; active = instance;
    const fallback = Object.entries(sources).flatMap(([id, config]) => config.symbols.map((name) => ({
      id: `${id}:${name}`, source: id, symbol: name, description: name,
      exchange: config.exchange || config.label,
      category: id === "yfinance" ? "Stocks" : "Crypto", type: config.marketType || "Spot",
      exchangeType: id === "yfinance" ? "Cash" : config.marketType === "Spot" ? "Spot" : config.marketType === "Option" ? "Options" : "Futures",
    })));
    function options(select, label, values) {
      const value = select.value;
      select.replaceChildren(new Option(label, ""), ...[...new Set(values)].sort().map((item) => new Option(item, item)));
      if ([...select.options].some((option) => option.value === value)) select.value = value;
    }
    function render() {
      if (closed()) return;
      const records = new Map();
      for (const row of fallback) if (!catalogs.has(row.source)) records.set(row.id, row);
      for (const rows of catalogs.values()) for (const row of rows) records.set(row.id, row);
      for (const row of remote) records.set(row.id, row);
      const all = [...records.values()];
      options(exchangeFilter, "All sources", all.map((row) => row.exchange));
      options(typeFilter, "All types", all.map((row) => row.type));
      options(exchangeTypeFilter, "All exchange types", all.map((row) => row.exchangeType));
      const matches = filterInstruments(all, input.value, { category, exchange: exchangeFilter.value, type: typeFilter.value, exchangeType: exchangeTypeFilter.value });
      visible = matches.slice(0, 250); selected = Math.min(selected, Math.max(visible.length - 1, 0));
      status.textContent = `${matches.length} results${matches.length > 250 ? " · Showing first 250" : ""}${loading ? " · Loading sources..." : ""}`;
      results.replaceChildren(...visible.map((row, index) => {
        const button = document.createElement("button"); button.type = "button"; button.setAttribute("role", "option"); button.id = "symbol-result-" + index;
        button.setAttribute("aria-selected", String(index === selected)); button.className = "symbol-picker-row";
        for (const [className, text] of [["symbol-result-name", row.displaySymbol || row.symbol + (row.type === "Perpetual" ? ".P" : "")], ["symbol-result-description", row.description], ["symbol-result-kind", row.type], ["symbol-result-exchange", row.exchange]]) {
          const span = document.createElement("span"); span.className = className; span.textContent = text; button.append(span);
        }
        button.title = `${row.exchange} · ${row.exchangeType} · ${row.type} · ${row.symbol}`;
        button.addEventListener("click", () => { close(); onSelect(row); }); return button;
      }));
      input.setAttribute("aria-activedescendant", visible.length ? "symbol-result-" + selected : "");
      if (!visible.length) { const empty = document.createElement("p"); empty.className = "symbol-picker-empty"; empty.textContent = loading ? "Loading instruments..." : "No matching instruments"; results.append(empty); }
      warningsEl.textContent = [...warnings.values()].join(" · ");
    }
    for (const name of categories) {
      const button = document.createElement("button"); button.type = "button"; button.textContent = name; button.setAttribute("role", "tab"); button.setAttribute("aria-selected", String(name === category));
      button.addEventListener("click", () => { category = name; selected = 0; backdrop.querySelectorAll('[role="tab"]').forEach((tab) => tab.setAttribute("aria-selected", String(tab === button))); render(); });
      backdrop.querySelector('[role="tablist"]').append(button);
    }
    for (const select of [exchangeFilter, typeFilter, exchangeTypeFilter]) select.addEventListener("change", () => { selected = 0; render(); });
    input.value = symbol.toUpperCase();
    input.addEventListener("input", () => {
      const start = input.selectionStart, end = input.selectionEnd;
      input.value = input.value.toUpperCase();
      input.setSelectionRange(start, end);
      selected = 0; remote = []; render(); clearTimeout(timer); const id = ++queryId, query = input.value.trim();
      warnings.delete("finance");
      if (query.length < 2) return;
      timer = setTimeout(async () => {
        try { const payload = await getJson("/api/market/search?" + new URLSearchParams({ q: query }));
          if (closed() || id !== queryId) return; remote = payload.instruments || []; if (payload.warning) warnings.set("finance", payload.warning); render();
        } catch (error) { if (!closed() && id === queryId) { warnings.set("finance", error.message); render(); } }
      }, 250);
    });
    backdrop.querySelector('.symbol-picker-input button').addEventListener("click", () => { input.value = ""; input.dispatchEvent(new Event("input")); input.focus(); });
    backdrop.querySelector('.symbol-picker-close').addEventListener("click", close);
    backdrop.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
    backdrop.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.target === input && ["ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault(); selected = Math.max(0, Math.min(visible.length - 1, selected + (event.key === "ArrowDown" ? 1 : -1))); render();
        results.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
      }
      if (event.target === input && event.key === "Enter" && visible[selected]) { event.preventDefault(); const row = visible[selected]; close(); onSelect(row); }
      if (event.key === "Tab") {
        const items = [...backdrop.querySelectorAll('button,input,select')].filter((element) => element.offsetWidth > 0);
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1).focus(); }
        else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
      }
    });
    render(); root.lucide?.createIcons(); input.focus(); input.select();
    for (const id of Object.keys(sources)) {
      if (catalogs.has(id)) continue;
      loading++;
      if (!pending.has(id)) pending.set(id, getJson("/api/market/instruments?" + new URLSearchParams({ source: id })).finally(() => pending.delete(id)));
      pending.get(id).then((payload) => { catalogs.set(id, payload.instruments || []); if (payload.warning) warnings.set(id, sources[id].label + ": " + payload.warning); })
        .catch(() => { warnings.set(id, sources[id].label + " catalog unavailable"); })
        .finally(() => { loading--; render(); });
    }
    render();
  }
  api.open = open; root.SymbolPicker = api;
})(typeof window !== "undefined" ? window : globalThis);
