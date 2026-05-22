const STORAGE_KEY = "tvk-dashboard-state";
const BYBIT_WS = "wss://stream.bybit.com/v5/public/linear";
const HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws";
const CHART_COUNTS = [1, 2, 4, 6, 8];
const CHART_LIBRARY_URLS = [
  "/static/lightweight-charts.standalone.production.js",
  "https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js",
  "https://cdn.jsdelivr.net/npm/lightweight-charts/dist/lightweight-charts.standalone.production.js",
];

const grid = document.querySelector("#chart-grid");
const countSelect = document.querySelector("#chart-count");
const template = document.querySelector("#pane-template");
const backtestModal = document.querySelector("#backtest-modal");
const backtestClose = document.querySelector("#backtest-close");
const backtestContent = document.querySelector("#backtest-content");

let config = null;
let state = loadState();
let panes = [];

function loadState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch {
    return {};
  }
}

function saveState() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      count: Number(countSelect.value),
      panes: panes.map((pane) => ({
        source: pane.sourceSelect.value,
        symbol: pane.symbolSelect.value,
        timeframe: pane.timeframeSelect.value,
        preset: pane.presetSelect.value,
        indicators: selectedIndicators(pane),
        signalMarkers: pane.signalMarkerToggle.checked,
      })),
    }),
  );
}

async function boot() {
  await loadChartLibrary();

  const response = await fetch("/api/config");
  config = await response.json();

  countSelect.value = CHART_COUNTS.includes(state.count) ? String(state.count) : "1";
  countSelect.addEventListener("change", () => renderPanes(Number(countSelect.value)));
  renderPanes(Number(countSelect.value));

  backtestClose.addEventListener("click", closeBacktestModal);
  backtestModal.addEventListener("click", (event) => {
    if (event.target === backtestModal) closeBacktestModal();
  });

  document.addEventListener("click", (event) => {
    panes.forEach((pane) => {
      if (!pane.indicatorMenu.contains(event.target) && event.target !== pane.indicatorButton) {
        pane.indicatorMenu.hidden = true;
      }
    });
  });
}

async function loadChartLibrary() {
  if (window.LightweightCharts) return;

  for (const url of CHART_LIBRARY_URLS) {
    try {
      await loadScriptWithTimeout(url, 7000);
      if (window.LightweightCharts) return;
    } catch {
      continue;
    }
  }

  throw new Error("Lightweight Charts could not be loaded. Check your internet connection or allow the chart CDN.");
}

function loadScriptWithTimeout(src, timeoutMs) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    const timer = window.setTimeout(() => {
      script.remove();
      reject(new Error(`Timed out loading ${src}`));
    }, timeoutMs);

    script.src = src;
    script.async = true;
    script.onload = () => {
      window.clearTimeout(timer);
      resolve();
    };
    script.onerror = () => {
      window.clearTimeout(timer);
      reject(new Error(`Failed loading ${src}`));
    };
    document.head.appendChild(script);
  });
}

function renderPanes(count) {
  cleanupPanes();
  grid.className = `chart-grid layout-${count}`;
  grid.innerHTML = "";
  panes = [];

  for (let index = 0; index < count; index += 1) {
    const node = template.content.firstElementChild.cloneNode(true);
    grid.appendChild(node);
    panes.push(createPane(node, index));
  }

  saveState();
}

function cleanupPanes() {
  panes.forEach((pane) => {
    if (pane.ws) pane.ws.close();
    if (pane.pollTimer) clearInterval(pane.pollTimer);
    if (pane.resizeObserver) pane.resizeObserver.disconnect();
    clearIndicatorSeries(pane);
    if (pane.chart) pane.chart.remove();
  });
}

function createPane(node, index) {
  const pane = {
    node,
    index,
    sourceSelect: node.querySelector(".source-select"),
    symbolSelect: node.querySelector(".symbol-select"),
    timeframeSelect: node.querySelector(".timeframe-select"),
    presetSelect: node.querySelector(".preset-select"),
    backtestButton: node.querySelector(".backtest-button"),
    indicatorButton: node.querySelector(".indicator-button"),
    indicatorMenu: node.querySelector(".indicator-menu"),
    indicatorList: node.querySelector(".indicator-list"),
    signalMarkerToggle: node.querySelector(".signal-marker-toggle"),
    signalBadge: node.querySelector(".signal-badge"),
    ticker: node.querySelector(".ticker"),
    tickerSymbol: node.querySelector(".ticker-symbol"),
    tickerPrice: node.querySelector(".ticker-price"),
    chartEl: node.querySelector(".chart"),
    indicatorPanesEl: node.querySelector(".indicator-panes"),
    status: node.querySelector(".pane-status"),
    chart: null,
    series: null,
    overlaySeries: [],
    indicatorCharts: [],
    signalMarkers: [],
    backtestMarkers: [],
    candles: [],
    signalMarkerPrimitive: null,
    ws: null,
    pollTimer: null,
    resizeObserver: null,
    lastPrice: null,
    requestId: 0,
  };

  setupChart(pane);
  populateSourceSelect(pane);
  populatePresetSelect(pane);
  populateIndicatorMenu(pane);

  const saved = state.panes?.[index] || {};
  pane.sourceSelect.value = config.sources[saved.source] ? saved.source : "bybit";
  pane.presetSelect.value = config.strategy_presets?.some((preset) => preset.id === saved.preset)
    ? saved.preset
    : config.default_strategy_preset;
  populateSymbolAndTimeframe(pane, saved);
  restoreIndicators(pane, saved.indicators || []);
  pane.signalMarkerToggle.checked = saved.signalMarkers !== false;

  [pane.sourceSelect, pane.symbolSelect, pane.timeframeSelect].forEach((select) => {
    select.addEventListener("change", () => {
      if (select === pane.sourceSelect) populateSymbolAndTimeframe(pane, {});
      startPane(pane);
      saveState();
    });
  });

  pane.presetSelect.addEventListener("change", saveState);

  pane.indicatorButton.addEventListener("click", (event) => {
    event.stopPropagation();
    pane.indicatorMenu.hidden = !pane.indicatorMenu.hidden;
  });

  pane.indicatorList.addEventListener("change", () => {
    startPane(pane);
    saveState();
  });

  pane.signalMarkerToggle.addEventListener("change", () => {
    updateChartMarkers(pane);
    saveState();
  });

  pane.backtestButton.addEventListener("click", () => openBacktestControls(pane));

  startPane(pane);
  return pane;
}

function setupChart(pane) {
  pane.chart = createBaseChart(pane.chartEl, { height: undefined });

  const candleOptions = {
    upColor: "#12b886",
    downColor: "#ff5c7a",
    borderUpColor: "#12b886",
    borderDownColor: "#ff5c7a",
    wickUpColor: "#12b886",
    wickDownColor: "#ff5c7a",
  };

  pane.series = addSeries(pane.chart, "candlestick", candleOptions);

  pane.resizeObserver = new ResizeObserver(() => {
    pane.chart.timeScale().fitContent();
    pane.indicatorCharts.forEach((item) => item.chart.timeScale().fitContent());
  });
  pane.resizeObserver.observe(pane.chartEl);
}

function createBaseChart(element, overrides = {}) {
  return LightweightCharts.createChart(element, {
    autoSize: true,
    layout: {
      background: { color: "#11161d" },
      textColor: "#9ca8b7",
      fontFamily: getComputedStyle(document.body).fontFamily,
    },
    grid: {
      vertLines: { color: "rgba(255, 255, 255, 0.05)" },
      horzLines: { color: "rgba(255, 255, 255, 0.05)" },
    },
    rightPriceScale: { borderColor: "#26313d" },
    timeScale: { borderColor: "#26313d", timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    ...overrides,
  });
}

function addSeries(chart, type, options) {
  if (type === "candlestick") {
    return chart.addCandlestickSeries
      ? chart.addCandlestickSeries(options)
      : chart.addSeries(LightweightCharts.CandlestickSeries, options);
  }
  if (type === "histogram") {
    return chart.addHistogramSeries
      ? chart.addHistogramSeries(options)
      : chart.addSeries(LightweightCharts.HistogramSeries, options);
  }
  return chart.addLineSeries
    ? chart.addLineSeries(options)
    : chart.addSeries(LightweightCharts.LineSeries, options);
}

function populateSourceSelect(pane) {
  pane.sourceSelect.innerHTML = Object.entries(config.sources)
    .map(([value, item]) => `<option value="${value}">${item.label}</option>`)
    .join("");
}

function populatePresetSelect(pane) {
  pane.presetSelect.innerHTML = (config.strategy_presets || [])
    .map((preset) => `<option value="${preset.id}">${preset.label}</option>`)
    .join("");
}

function populateSymbolAndTimeframe(pane, saved) {
  const sourceConfig = config.sources[pane.sourceSelect.value];
  pane.symbolSelect.innerHTML = sourceConfig.symbols
    .map((symbol) => `<option value="${symbol}">${symbol}</option>`)
    .join("");
  pane.timeframeSelect.innerHTML = sourceConfig.timeframes
    .map((timeframe) => `<option value="${timeframe}">${timeframe}</option>`)
    .join("");

  pane.symbolSelect.value = sourceConfig.symbols.includes(saved.symbol) ? saved.symbol : sourceConfig.symbols[0];
  pane.timeframeSelect.value = sourceConfig.timeframes.includes(saved.timeframe) ? saved.timeframe : sourceConfig.timeframes[0];
}

function populateIndicatorMenu(pane) {
  pane.indicatorList.innerHTML = config.indicators
    .map((indicator) => `
      <label class="indicator-option">
        <input type="checkbox" value="${indicator.id}">
        <span>${indicator.label}</span>
      </label>
    `)
    .join("");
}

function restoreIndicators(pane, ids) {
  const selected = new Set(ids);
  pane.indicatorList.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function selectedIndicators(pane) {
  return Array.from(pane.indicatorList.querySelectorAll("input[type='checkbox']:checked")).map((input) => input.value);
}

async function startPane(pane) {
  const requestId = pane.requestId + 1;
  pane.requestId = requestId;
  if (pane.ws) pane.ws.close();
  if (pane.pollTimer) clearInterval(pane.pollTimer);
  pane.ws = null;
  pane.pollTimer = null;
  pane.lastPrice = null;
  clearIndicatorSeries(pane);
  clearSignalMarkers(pane);
  clearBacktestMarkers(pane);
  pane.status.textContent = "Loading candles...";
  pane.tickerSymbol.textContent = pane.symbolSelect.value;
  pane.tickerPrice.textContent = "Loading";

  if (pane.sourceSelect.value === "bybit") {
    connectBybit(pane, requestId);
  } else if (pane.sourceSelect.value === "hyperliquid") {
    connectHyperliquid(pane, requestId);
  }

  try {
    const candles = await loadCandles(pane, { limit: 1000 });
    if (requestId !== pane.requestId) return;
    pane.candles = candles;
    pane.series.setData(candles);
    pane.chart.timeScale().fitContent();
    if (candles.length) updateTicker(pane, candles[candles.length - 1].close);
    await renderIndicators(pane, requestId);
    await renderSignals(pane, requestId);
    pane.status.textContent = `${candles.length} candles loaded`;
    loadOlderHistory(pane, requestId);
  } catch (error) {
    if (requestId !== pane.requestId) return;
    pane.series.setData([]);
    pane.status.textContent = error.message;
  }

  if (pane.sourceSelect.value === "yfinance") {
    startYfinancePolling(pane);
  }
}

async function loadOlderHistory(pane, requestId) {
  if (pane.sourceSelect.value !== "bybit") return;
  try {
    const candles = await loadCandles(pane, { limit: 20000 });
    if (requestId !== pane.requestId) return;
    if (candles.length <= pane.candles.length) return;
    pane.candles = candles;
    pane.series.setData(candles);
    pane.chart.timeScale().fitContent();
    pane.status.textContent = `${candles.length} candles loaded`;
    await renderIndicators(pane, requestId);
    await renderSignals(pane, requestId);
  } catch (error) {
    if (requestId === pane.requestId) pane.status.textContent = `Older history unavailable: ${error.message}`;
  }
}

async function renderSignals(pane, requestId) {
  resetSignalBadge(pane);
  clearSignalMarkers(pane);

  const params = new URLSearchParams({
    source: pane.sourceSelect.value,
    symbol: pane.symbolSelect.value,
    timeframe: pane.timeframeSelect.value,
    limit: "300",
  });
  const response = await fetch(`/api/signals?${params}`);
  const payload = await response.json();
  if (requestId !== pane.requestId) return;
  if (!response.ok) throw new Error(payload.error || "Signal request failed");

  pane.signalMarkers = payload.markers || [];
  updateSignalBadge(pane, payload);
  updateChartMarkers(pane);
}

async function loadCandles(pane, options = {}) {
  const params = new URLSearchParams({
    source: pane.sourceSelect.value,
    symbol: pane.symbolSelect.value,
    timeframe: pane.timeframeSelect.value,
    limit: String(options.limit || 240),
    visible_charts: String(visibleChartCount()),
  });
  const response = await fetch(`/api/candles?${params}`);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Candle request failed");
  return payload.candles;
}

function visibleChartCount() {
  return panes.length || Number(countSelect.value) || 1;
}

async function renderIndicators(pane, requestId) {
  const indicators = selectedIndicators(pane);
  clearIndicatorSeries(pane);
  updateIndicatorButton(pane);
  if (!indicators.length) return;

  const params = new URLSearchParams({
    source: pane.sourceSelect.value,
    symbol: pane.symbolSelect.value,
    timeframe: pane.timeframeSelect.value,
    indicators: indicators.join(","),
    limit: "300",
  });
  const response = await fetch(`/api/indicators?${params}`);
  const payload = await response.json();
  if (requestId !== pane.requestId) return;
  if (!response.ok) throw new Error(payload.error || "Indicator request failed");

  payload.overlays.forEach((overlay) => {
    const series = addSeries(pane.chart, overlay.type, seriesOptions(overlay));
    series.setData(overlay.data);
    pane.overlaySeries.push(series);
  });

  payload.panes.forEach((indicatorPane) => {
    const paneEl = document.createElement("div");
    paneEl.className = "indicator-pane";
    paneEl.innerHTML = `<div class="indicator-title">${indicatorPane.title}</div><div class="indicator-chart"></div>`;
    pane.indicatorPanesEl.appendChild(paneEl);

    const chart = createBaseChart(paneEl.querySelector(".indicator-chart"));
    indicatorPane.series.forEach((item) => {
      const series = addSeries(chart, item.type, seriesOptions(item));
      series.setData(item.data);
    });
    chart.timeScale().fitContent();
    pane.indicatorCharts.push({ chart, element: paneEl });
  });

  pane.chart.timeScale().fitContent();
}

function seriesOptions(series) {
  if (series.type === "histogram") {
    return {
      color: series.color || "#748ffc",
      priceFormat: { type: "volume" },
    };
  }
  return {
    color: series.color || "#ced4da",
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: true,
  };
}

function clearIndicatorSeries(pane) {
  pane.overlaySeries.forEach((series) => {
    try {
      pane.chart.removeSeries(series);
    } catch {
      // A pane can be destroyed while an async indicator request is still returning.
    }
  });
  pane.overlaySeries = [];
  pane.indicatorCharts.forEach((item) => item.chart.remove());
  pane.indicatorCharts = [];
  pane.indicatorPanesEl.innerHTML = "";
}

function updateSignalBadge(pane, payload) {
  pane.signalBadge.textContent = `${payload.label} ${payload.score}`;
  pane.signalBadge.title = [
    ...(payload.components || []).map((item) => `${item.name}: ${item.score}`),
    ...(payload.warnings || []),
  ].join("\n");
  pane.signalBadge.classList.remove("buy", "sell", "neutral");
  pane.signalBadge.classList.add(payload.tone || "neutral");
}

function resetSignalBadge(pane) {
  pane.signalBadge.textContent = "NEUTRAL";
  pane.signalBadge.title = "";
  pane.signalBadge.classList.remove("buy", "sell");
  pane.signalBadge.classList.add("neutral");
}

function updateChartMarkers(pane) {
  const visibleMarkers = [
    ...(pane.signalMarkerToggle.checked ? pane.signalMarkers : []),
    ...pane.backtestMarkers,
  ].sort((a, b) => a.time - b.time);
  if (pane.series.setMarkers) {
    pane.series.setMarkers(visibleMarkers);
    return;
  }
  if (!LightweightCharts.createSeriesMarkers) return;
  if (pane.signalMarkerPrimitive) {
    pane.signalMarkerPrimitive.setMarkers(visibleMarkers);
  } else {
    pane.signalMarkerPrimitive = LightweightCharts.createSeriesMarkers(pane.series, visibleMarkers);
  }
}

function clearSignalMarkers(pane) {
  pane.signalMarkers = [];
  updateChartMarkers(pane);
}

function clearBacktestMarkers(pane) {
  pane.backtestMarkers = [];
  if (pane.series && pane.series.setMarkers) {
    updateChartMarkers(pane);
  } else if (pane.signalMarkerPrimitive) {
    updateChartMarkers(pane);
  }
}

function openBacktestControls(pane) {
  const options = (config.strategy_presets || [])
    .map((preset) => `<option value="${preset.id}" ${preset.id === pane.presetSelect.value ? "selected" : ""}>${preset.label}</option>`)
    .join("");
  openBacktestModal(`
    <div class="backtest-controls">
      <label>
        <span>Preset</span>
        <select id="modal-preset-select">${options}</select>
      </label>
      <label>
        <span>Limit</span>
        <input id="modal-limit-input" type="number" min="100" max="5000" value="5000">
      </label>
      <label>
        <span>Fee % / side</span>
        <input id="modal-fee-input" type="number" min="0" step="0.01" value="0">
      </label>
      <label>
        <span>Slippage % / side</span>
        <input id="modal-slippage-input" type="number" min="0" step="0.01" value="0">
      </label>
      <label class="allow-short-toggle">
        <input id="modal-allow-shorts" type="checkbox">
        <span>Allow shorts</span>
      </label>
      <div class="backtest-actions">
        <button id="modal-run-backtest" type="button">Run Backtest</button>
        <button id="modal-test-presets" type="button">Test presets</button>
      </div>
      <p id="modal-preset-note" class="modal-note"></p>
    </div>
    <div id="modal-results"></div>
  `);

  const presetSelect = document.querySelector("#modal-preset-select");
  const note = document.querySelector("#modal-preset-note");
  const syncNote = () => {
    const preset = (config.strategy_presets || []).find((item) => item.id === presetSelect.value);
    note.textContent = preset?.intended_timeframes ? `Recommended timeframe: ${preset.intended_timeframes}` : "";
  };
  presetSelect.addEventListener("change", syncNote);
  syncNote();

  document.querySelector("#modal-run-backtest").addEventListener("click", () => runBacktest(pane, presetSelect.value));
  document.querySelector("#modal-test-presets").addEventListener("click", () => testPresets(pane));
}

function backtestSettings(presetId) {
  return {
    preset: presetId,
    limit: document.querySelector("#modal-limit-input")?.value || "5000",
    fee_pct: document.querySelector("#modal-fee-input")?.value || "0",
    slippage_pct: document.querySelector("#modal-slippage-input")?.value || "0",
    allowShorts: document.querySelector("#modal-allow-shorts")?.checked ? "true" : "false",
  };
}

async function runBacktest(pane, presetId) {
  pane.backtestButton.disabled = true;
  pane.backtestButton.textContent = "Testing...";
  const resultsEl = document.querySelector("#modal-results") || backtestContent;
  resultsEl.innerHTML = `<p class="pane-status">Running backtest for ${pane.symbolSelect.value}...</p>`;

  try {
    const settings = backtestSettings(presetId);
    const params = new URLSearchParams({
      source: pane.sourceSelect.value,
      symbol: pane.symbolSelect.value,
      timeframe: pane.timeframeSelect.value,
      period: "60d",
      ...settings,
    });
    const response = await fetch(`/api/backtest?${params}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Backtest failed");

    pane.backtestMarkers = markersFromBacktestPayload(payload);
    updateChartMarkers(pane);
    resultsEl.innerHTML = renderBacktestResults(payload);
  } catch (error) {
    resultsEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  } finally {
    pane.backtestButton.disabled = false;
    pane.backtestButton.textContent = "Backtest";
  }
}

function markersFromBacktestPayload(payload) {
  const trades = normalizedTrades(payload);
  const markers = [];
  trades.forEach((trade) => {
    const entryTime = normalizeMarkerTime(trade.entry_time ?? trade.entryTime);
    const exitTime = normalizeMarkerTime(trade.exit_time ?? trade.exitTime);
    if (entryTime !== null) {
      markers.push({
        time: entryTime,
        position: "belowBar",
        color: "#12b886",
        shape: "arrowUp",
        text: "BT BUY",
      });
    }
    if (exitTime !== null) {
      markers.push({
        time: exitTime,
        position: "aboveBar",
        color: "#ff5c7a",
        shape: "arrowDown",
        text: "BT SELL",
      });
    }
  });
  return markers;
}

function normalizedTrades(payload) {
  if (Array.isArray(payload.trade_list)) return payload.trade_list;
  if (Array.isArray(payload.tradeList)) {
    return payload.tradeList.map((trade) => ({
      entry_time: trade.entryTime,
      exit_time: trade.exitTime,
      entry_price: trade.entryPrice,
      exit_price: trade.exitPrice,
      return_pct: trade.returnPct,
      bars_held: trade.barsHeld,
      exit_reason: trade.exitReason,
    }));
  }
  return [];
}

function normalizeMarkerTime(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return number > 1_000_000_000_000 ? Math.floor(number / 1000) : Math.floor(number);
}

async function testPresets(pane) {
  const resultsEl = document.querySelector("#modal-results") || backtestContent;
  resultsEl.innerHTML = `<p class="pane-status">Testing all presets for ${pane.symbolSelect.value}...</p>`;
  const rows = [];

  for (const preset of config.strategy_presets || []) {
    const settings = backtestSettings(preset.id);
    const params = new URLSearchParams({
      source: pane.sourceSelect.value,
      symbol: pane.symbolSelect.value,
      timeframe: pane.timeframeSelect.value,
      period: "60d",
      ...settings,
    });
    const response = await fetch(`/api/backtest?${params}`);
    const payload = await response.json();
    if (response.ok) rows.push(payload);
  }

  resultsEl.innerHTML = renderPresetComparison(rows);
}

function openBacktestModal(html) {
  backtestContent.innerHTML = html;
  backtestModal.hidden = false;
}

function closeBacktestModal() {
  backtestModal.hidden = true;
}

function renderBacktestResults(payload) {
  const metrics = [
    ["Total return", `${formatSigned(payload.total_return_pct)}%`],
    ["Trades", payload.number_of_trades],
    ["Win rate", `${payload.win_rate}%`],
    ["Average win", `${payload.average_win}%`],
    ["Average loss", `${payload.average_loss}%`],
    ["Max drawdown", `${payload.max_drawdown}%`],
    ["Profit factor", payload.profit_factor],
    ["Avg bars held", payload.average_bars_held],
    ["Period", payload.period],
  ];
  const diagnostics = payload.diagnostics || {};
  const diagnosticMetrics = [
    ["Preset", payload.preset],
    ["First candle", formatIsoDate(diagnostics.first_candle_date)],
    ["Last candle", formatIsoDate(diagnostics.last_candle_date)],
    ["Candles loaded", diagnostics.number_of_candles_loaded],
    ["Timeframe", diagnostics.timeframe],
    ["Actual days", diagnostics.actual_days_returned],
    ["Reliability", diagnostics.backtest_reliability],
    ["Warmup skipped", diagnostics.warmup_candles_skipped],
    ["Warmup %", `${diagnostics.warmup_pct ?? 0}%`],
    ["Average ATR %", diagnostics.average_atr_pct],
    ["Average volume", formatCompact(diagnostics.average_volume)],
    ["Trades/day", diagnostics.trades_per_day],
    ["Fee/side", `${diagnostics.fee_pct_per_side ?? payload.fee_pct}%`],
    ["Slippage/side", `${diagnostics.slippage_pct_per_side ?? payload.slippage_pct}%`],
    ["Raw score", diagnostics.raw_latest_score],
    ["Smoothed score", diagnostics.smoothed_latest_score],
  ];
  const skipped = diagnostics.skipped_trade_reasons || {};
  const skippedRows = Object.entries(skipped).sort((a, b) => b[1] - a[1]).map(([reason, count]) => `
    <tr><td>${formatReason(reason)}</td><td>${count}</td></tr>
  `).join("");
  const warnings = (diagnostics.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const trades = normalizedTrades(payload);
  const rows = trades.map((trade) => `
    <tr>
      <td>${formatDateTime(trade.entry_time)}</td>
      <td>${formatDateTime(trade.exit_time)}</td>
      <td>${formatPrice(trade.entry_price)}</td>
      <td>${formatPrice(trade.exit_price)}</td>
      <td class="${trade.return_pct >= 0 ? "positive" : "negative"}">${formatSigned(trade.return_pct)}%</td>
      <td>${trade.bars_held}</td>
      <td>${escapeHtml(trade.exit_reason)}</td>
    </tr>
  `).join("");

  return `
    <div class="metric-grid">
      ${metrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join("")}
    </div>
    <h3 class="modal-section-title">Diagnostics</h3>
    <div class="metric-grid diagnostics-grid">
      ${diagnosticMetrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value ?? "-"}</strong></div>`).join("")}
    </div>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
    <h3 class="modal-section-title">Skipped Entry Reasons</h3>
    <table class="trade-table skipped-table">
      <tbody>${skippedRows || `<tr><td>No skipped score>=70 candles.</td><td>0</td></tr>`}</tbody>
    </table>
    <h3 class="modal-section-title">Trades</h3>
    <table class="trade-table">
      <thead>
        <tr>
          <th>Entry</th>
          <th>Exit</th>
          <th>Entry Price</th>
          <th>Exit Price</th>
          <th>Return</th>
          <th>Bars</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>${rows || `<tr><td colspan="7">No trades for this period.</td></tr>`}</tbody>
    </table>
  `;
}

function renderPresetComparison(results) {
  if (!results.length) return "<p>No preset results returned.</p>";
  const bestReturn = Math.max(...results.map((item) => Number(item.total_return_pct || 0)));
  const bestProfitFactor = Math.max(...results.map((item) => Number(item.profit_factor || 0)));
  const rows = results.map((item) => `
    <tr>
      <td>${escapeHtml(item.preset)}</td>
      <td class="${item.total_return_pct >= 0 ? "positive" : "negative"}">${formatSigned(item.total_return_pct)}%</td>
      <td>${item.number_of_trades}</td>
      <td>${item.win_rate}%</td>
      <td>${item.max_drawdown}%</td>
      <td>${item.profit_factor}</td>
      <td>${item.average_bars_held}</td>
      <td>${item.total_return_pct === bestReturn ? "Best return" : ""} ${item.profit_factor === bestProfitFactor ? "Best PF" : ""}</td>
    </tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Preset Comparison</h3>
    <table class="trade-table">
      <thead>
        <tr>
          <th>Preset</th>
          <th>Return</th>
          <th>Trades</th>
          <th>Win Rate</th>
          <th>Max DD</th>
          <th>Profit Factor</th>
          <th>Avg Bars</th>
          <th>Highlight</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function formatIsoDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function formatCompact(value) {
  const number = Number(value || 0);
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(number);
}

function formatReason(reason) {
  return reason.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatSigned(value) {
  const number = Number(value || 0);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}

function formatDateTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function updateIndicatorButton(pane) {
  const count = selectedIndicators(pane).length;
  pane.indicatorButton.textContent = count ? `Indicators ${count}` : "Indicators";
}

function connectBybit(pane, requestId) {
  const symbol = pane.symbolSelect.value;
  const timeframe = bybitInterval(pane.timeframeSelect.value);
  const ws = new WebSocket(BYBIT_WS);
  pane.ws = ws;

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({
      op: "subscribe",
      args: [`kline.${timeframe}.${symbol}`],
    }));
    if (requestId === pane.requestId) pane.status.textContent = "Bybit websocket connected";
  });

  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    const row = message.data?.[0];
    if (!row || !message.topic?.startsWith("kline.")) return;
    const candle = {
      time: Math.floor(Number(row.start) / 1000),
      open: Number(row.open),
      high: Number(row.high),
      low: Number(row.low),
      close: Number(row.close),
    };
    pane.series.update(candle);
    updateTicker(pane, candle.close);
  });

  ws.addEventListener("close", () => {
    if (pane.ws === ws && requestId === pane.requestId) pane.status.textContent = "Bybit websocket closed";
  });

  ws.addEventListener("error", () => {
    if (requestId === pane.requestId) pane.status.textContent = "Bybit websocket error";
  });
}

function connectHyperliquid(pane, requestId) {
  const symbol = pane.symbolSelect.value;
  const timeframe = pane.timeframeSelect.value;
  const ws = new WebSocket(HYPERLIQUID_WS);
  pane.ws = ws;

  ws.addEventListener("open", () => {
    ws.send(JSON.stringify({
      method: "subscribe",
      subscription: { type: "candle", coin: symbol, interval: timeframe },
    }));
    if (requestId === pane.requestId) pane.status.textContent = "Hyperliquid websocket connected";
  });

  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.channel !== "candle" || !message.data) return;
    const row = message.data;
    const candle = {
      time: Math.floor(row.t / 1000),
      open: Number(row.o),
      high: Number(row.h),
      low: Number(row.l),
      close: Number(row.c),
    };
    pane.series.update(candle);
    updateTicker(pane, candle.close);
  });

  ws.addEventListener("close", () => {
    if (pane.ws === ws && requestId === pane.requestId) pane.status.textContent = "Hyperliquid websocket closed";
  });

  ws.addEventListener("error", () => {
    if (requestId === pane.requestId) pane.status.textContent = "Hyperliquid websocket error";
  });
}

function bybitInterval(timeframe) {
  if (timeframe.endsWith("m")) return timeframe.slice(0, -1);
  if (timeframe.endsWith("h")) return String(Number(timeframe.slice(0, -1)) * 60);
  if (timeframe === "1d") return "D";
  return timeframe;
}

function startYfinancePolling(pane) {
  const refresh = async () => {
    try {
      const candles = await loadCandles(pane);
      if (!candles.length) {
        pane.status.textContent = "No yfinance candles returned";
        return;
      }
      pane.series.setData(candles);
      updateTicker(pane, candles[candles.length - 1].close);
      await renderIndicators(pane, pane.requestId);
      await renderSignals(pane, pane.requestId);
      pane.status.textContent = `yfinance refreshed ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      pane.status.textContent = error.message;
    }
  };

  pane.pollTimer = setInterval(refresh, 15000);
}

function updateTicker(pane, price) {
  const direction = pane.lastPrice === null ? "neutral" : price >= pane.lastPrice ? "up" : "down";
  pane.lastPrice = price;
  pane.tickerSymbol.textContent = pane.symbolSelect.value;
  pane.tickerPrice.textContent = formatPrice(price);
  pane.ticker.classList.remove("up", "down", "neutral");
  pane.ticker.classList.add(direction);

  window.clearTimeout(pane.flashTimer);
  pane.flashTimer = window.setTimeout(() => {
    pane.ticker.classList.remove("up", "down");
    pane.ticker.classList.add("neutral");
  }, 450);
}

function formatPrice(price) {
  if (price >= 1000) return price.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (price >= 1) return price.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return price.toLocaleString(undefined, { maximumFractionDigits: 8 });
}

boot().catch((error) => {
  grid.innerHTML = `<div class="fatal">${error.message}</div>`;
});
