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
const paperTabButton = document.querySelector("#paper-tab-button");
const paperPanel = document.querySelector("#paper-panel");
const paperPanelClose = document.querySelector("#paper-panel-close");
const paperPanelContent = document.querySelector("#paper-panel-content");

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

function hasElement(...elements) {
  return elements.every(Boolean);
}

async function boot() {
  await loadChartLibrary();

  const response = await fetch("/api/config");
  config = await response.json();

  if (!hasElement(grid, countSelect, template, backtestModal, backtestClose, backtestContent)) {
    throw new Error("Dashboard HTML is incomplete. Please restart Flask and hard-refresh the page.");
  }

  countSelect.value = CHART_COUNTS.includes(state.count) ? String(state.count) : "1";
  countSelect.addEventListener("change", () => renderPanes(Number(countSelect.value)));
  renderPanes(Number(countSelect.value));

  backtestClose.addEventListener("click", closeBacktestModal);
  backtestModal.addEventListener("click", (event) => {
    if (event.target === backtestModal) closeBacktestModal();
  });
  if (hasElement(paperTabButton, paperPanel, paperPanelClose, paperPanelContent)) {
    paperTabButton.addEventListener("click", openPaperPanel);
    paperPanelClose.addEventListener("click", () => {
      paperPanel.hidden = true;
    });
  }

  document.addEventListener("click", (event) => {
    panes.forEach((pane) => {
      if (!pane.indicatorMenu.contains(event.target) && event.target !== pane.indicatorButton) {
        pane.indicatorMenu.hidden = true;
      }
    });
  });
}

async function openPaperPanel() {
  if (!hasElement(paperPanel, paperPanelContent)) return;
  paperPanel.hidden = false;
  paperPanelContent.innerHTML = `<p class="pane-status">Loading paper simulation status...</p>`;
  try {
    const response = await fetch("/api/paper/status");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Paper status failed");
    paperPanelContent.innerHTML = renderPaperStatus(payload);
  } catch (error) {
    paperPanelContent.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperStatus(payload) {
  const positions = payload.openPositions || [];
  const trades = payload.closedTrades || [];
  const events = payload.lastSignals || [];
  const warnings = payload.warnings || [];
  return `
    <div class="paper-warning">Simulated only. No real order execution, no exchange account connection, no API keys.</div>
    <div class="metric-grid">
      <div class="metric"><span>Enabled</span><strong>${payload.candidate?.enabled ? "Yes" : "No"}</strong></div>
      <div class="metric"><span>Equity</span><strong>${formatPrice(Number(payload.equity || 0))}</strong></div>
      <div class="metric"><span>Realized PnL</span><strong class="${payload.realizedPnL >= 0 ? "positive" : "negative"}">${formatSigned(payload.realizedPnL)}</strong></div>
      <div class="metric"><span>Unrealized PnL</span><strong class="${payload.unrealizedPnL >= 0 ? "positive" : "negative"}">${formatSigned(payload.unrealizedPnL)}</strong></div>
      <div class="metric"><span>Fees</span><strong>${formatPrice(Number(payload.totalFees || 0))}</strong></div>
      <div class="metric"><span>Slippage</span><strong>${formatPrice(Number(payload.totalSlippage || 0))}</strong></div>
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(payload.candidate?.strategy || "-")}</strong></div>
      <div class="metric"><span>Fill</span><strong>${escapeHtml(payload.candidate?.fillModel || "-")}</strong></div>
    </div>
    ${warnings.length ? `<ul class="backtest-warnings">${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
    <h3 class="modal-section-title">Open Positions</h3>
    <table class="trade-table">
      <thead><tr><th>Symbol</th><th>Interval</th><th>Side</th><th>Entry</th><th>Last</th><th>Unrealized</th></tr></thead>
      <tbody>${positions.map((position) => `
        <tr>
          <td>${escapeHtml(position.symbol)}</td>
          <td>${escapeHtml(position.interval)}</td>
          <td>${escapeHtml(position.side)}</td>
          <td>${formatPrice(Number(position.entryFillPrice || 0))}</td>
          <td>${formatPrice(Number(position.lastPrice || 0))}</td>
          <td class="${position.unrealizedPnl >= 0 ? "positive" : "negative"}">${formatSigned(position.unrealizedPnl)}</td>
        </tr>
      `).join("") || `<tr><td colspan="6">No open simulated positions.</td></tr>`}</tbody>
    </table>
    <h3 class="modal-section-title">Recent Journal</h3>
    <table class="trade-table">
      <thead><tr><th>Time</th><th>Symbol</th><th>Type</th><th>Reason</th><th>Fill</th><th>PnL</th></tr></thead>
      <tbody>${events.slice().reverse().map((event) => `
        <tr>
          <td>${escapeHtml(new Date(event.timestamp).toLocaleString())}</td>
          <td>${escapeHtml(event.symbol)} ${escapeHtml(event.interval)}</td>
          <td>${escapeHtml(event.eventType)}</td>
          <td>${escapeHtml(event.reason)}</td>
          <td>${event.fillPrice === "" ? "-" : formatPrice(Number(event.fillPrice))}</td>
          <td class="${Number(event.netPnl || 0) >= 0 ? "positive" : "negative"}">${event.netPnl === "" ? "-" : formatSigned(event.netPnl)}</td>
        </tr>
      `).join("") || `<tr><td colspan="6">No journal events yet.</td></tr>`}</tbody>
    </table>
  `;
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
    dataDiagnosticsEl: null,
    chart: null,
    series: null,
    overlaySeries: [],
    backtestOverlaySeries: [],
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
    indicatorRequestId: 0,
    lastSignalPayload: null,
    rangeSyncing: false,
  };
  pane.dataDiagnosticsEl = document.createElement("div");
  pane.dataDiagnosticsEl.className = "data-diagnostics";
  pane.status.after(pane.dataDiagnosticsEl);

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
  pane.signalBadge.addEventListener("click", () => openSignalDetails(pane));

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
    syncIndicatorTimeRanges(pane);
  });
  pane.resizeObserver.observe(pane.chartEl);
  if (pane.chart.timeScale().subscribeVisibleTimeRangeChange) {
    pane.chart.timeScale().subscribeVisibleTimeRangeChange(() => syncIndicatorTimeRanges(pane));
  }
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
    const candlePayload = await loadCandles(pane, { limit: 1000 });
    if (requestId !== pane.requestId) return;
    const candles = candlePayload.candles;
    pane.candleDiagnostics = candlePayload.diagnostics || {};
    pane.candles = candles;
    pane.series.setData(candles);
    pane.chart.timeScale().fitContent();
    if (candles.length) updateTicker(pane, candles[candles.length - 1].close);
    await renderIndicators(pane, requestId);
    await renderSignals(pane, requestId);
    pane.status.textContent = `${candles.length} candles loaded`;
    renderDataDiagnostics(pane);
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
    const candlePayload = await loadCandles(pane, { limit: historyLimitForPane() });
    if (requestId !== pane.requestId) return;
    const candles = candlePayload.candles;
    if (candles.length <= pane.candles.length) return;
    const visibleRange = currentVisibleRange(pane.chart);
    pane.candleDiagnostics = candlePayload.diagnostics || {};
    pane.candles = candles;
    pane.series.setData(candles);
    restoreVisibleRange(pane.chart, visibleRange);
    pane.status.textContent = `${candles.length} candles loaded`;
    renderDataDiagnostics(pane);
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
  pane.lastSignalPayload = payload;
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
  return payload;
}

function visibleChartCount() {
  return panes.length || Number(countSelect.value) || 1;
}

function historyLimitForPane() {
  const count = visibleChartCount();
  if (count <= 1) return 20000;
  if (count <= 2) return 12000;
  if (count <= 4) return 8000;
  if (count <= 6) return 5000;
  return 3000;
}

async function renderIndicators(pane, requestId) {
  const indicators = selectedIndicators(pane);
  const indicatorRequestId = ++pane.indicatorRequestId;
  updateIndicatorButton(pane);
  if (!indicators.length) {
    clearIndicatorSeries(pane);
    pane.indicatorDiagnostics = {};
    renderDataDiagnostics(pane);
    return;
  }

  const params = new URLSearchParams({
    source: pane.sourceSelect.value,
    symbol: pane.symbolSelect.value,
    timeframe: pane.timeframeSelect.value,
    indicators: indicators.join(","),
    limit: String(Math.max(pane.candles.length, 300)),
    chart_candles_count: String(pane.candles.length),
    first_chart_candle_time: String(pane.candles[0]?.time || ""),
    last_chart_candle_time: String(pane.candles[pane.candles.length - 1]?.time || ""),
  });
  const response = await fetch(`/api/indicators?${params}`);
  const payload = await response.json();
  if (requestId !== pane.requestId || indicatorRequestId !== pane.indicatorRequestId) return;
  if (!response.ok) throw new Error(payload.error || "Indicator request failed");

  const oldOverlaySeries = pane.overlaySeries;
  const oldIndicatorCharts = pane.indicatorCharts;
  const nextOverlaySeries = [];
  const nextIndicatorCharts = [];
  const nextPaneElements = [];

  payload.overlays.forEach((overlay) => {
    const series = addSeries(pane.chart, overlay.type, seriesOptions(overlay));
    series.setData(normalizeSeriesData(overlay.data));
    nextOverlaySeries.push(series);
  });

  payload.panes.forEach((indicatorPane) => {
    const paneEl = document.createElement("div");
    paneEl.className = "indicator-pane";
    paneEl.innerHTML = `<div class="indicator-title">${indicatorPane.title}</div><div class="indicator-chart"></div>`;
    nextPaneElements.push({ element: paneEl, pane: indicatorPane });
  });

  oldOverlaySeries.forEach((series) => {
    try {
      pane.chart.removeSeries(series);
    } catch {
      // A pane can be destroyed while an async indicator request is still returning.
    }
  });
  oldIndicatorCharts.forEach((item) => item.chart.remove());
  pane.indicatorPanesEl.innerHTML = "";
  nextPaneElements.forEach((item) => {
    pane.indicatorPanesEl.appendChild(item.element);
    const chart = createBaseChart(item.element.querySelector(".indicator-chart"));
    item.pane.series.forEach((seriesConfig) => {
      const series = addSeries(chart, seriesConfig.type, seriesOptions(seriesConfig));
      series.setData(normalizeSeriesData(seriesConfig.data));
    });
    nextIndicatorCharts.push({ chart, element: item.element });
  });
  pane.overlaySeries = nextOverlaySeries;
  pane.indicatorCharts = nextIndicatorCharts;
  syncIndicatorTimeRanges(pane);
  pane.indicatorDiagnostics = payload.diagnostics || {};
  renderDataDiagnostics(pane);
}

function normalizeSeriesData(data) {
  return (data || []).map((point) => {
    const normalized = { ...point, time: normalizeMarkerTime(point.time) };
    if (point.value === null || point.value === undefined || Number.isNaN(Number(point.value))) {
      delete normalized.value;
    } else {
      normalized.value = Number(point.value);
    }
    return normalized;
  }).filter((point) => point.time !== null);
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
    lineWidth: series.guide ? 1 : 2,
    lineStyle: series.guide && LightweightCharts.LineStyle ? LightweightCharts.LineStyle.Dashed : undefined,
    priceLineVisible: false,
    lastValueVisible: !series.guide,
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

function clearBacktestOverlaySeries(pane) {
  pane.backtestOverlaySeries.forEach((series) => {
    try {
      pane.chart.removeSeries(series);
    } catch {
      // Async backtest overlays may return after a pane has been rebuilt.
    }
  });
  pane.backtestOverlaySeries = [];
}

function updateSignalBadge(pane, payload) {
  pane.signalBadge.textContent = `${payload.label} ${payload.score}`;
  pane.signalBadge.setAttribute("role", "button");
  pane.signalBadge.tabIndex = 0;
  pane.signalBadge.title = [
    "Click for score details.",
    ...(payload.components || []).map((item) => `${item.name}: ${item.score}`),
    ...(payload.warnings || []),
  ].join("\n");
  pane.signalBadge.classList.remove("buy", "sell", "neutral");
  pane.signalBadge.classList.add(payload.tone || "neutral");
}

function resetSignalBadge(pane) {
  pane.signalBadge.textContent = "NEUTRAL";
  pane.signalBadge.title = "";
  pane.lastSignalPayload = null;
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
  clearBacktestOverlaySeries(pane);
  if (pane.series && pane.series.setMarkers) {
    updateChartMarkers(pane);
  } else if (pane.signalMarkerPrimitive) {
    updateChartMarkers(pane);
  }
}

function currentVisibleRange(chart) {
  try {
    return chart.timeScale().getVisibleRange?.() || null;
  } catch {
    return null;
  }
}

function restoreVisibleRange(chart, range) {
  try {
    if (range?.from && range?.to) chart.timeScale().setVisibleRange(range);
  } catch {
    // If the chart library rejects a stale range, leave the current viewport as-is.
  }
}

function syncIndicatorTimeRanges(pane) {
  if (pane.rangeSyncing) return;
  const range = currentVisibleRange(pane.chart);
  if (!range) return;
  pane.rangeSyncing = true;
  try {
    pane.indicatorCharts.forEach((item) => restoreVisibleRange(item.chart, range));
  } finally {
    pane.rangeSyncing = false;
  }
}

function openBacktestControls(pane) {
  const currentLimit = Math.max(pane.candles.length || 5000, 5000);
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
        <input id="modal-limit-input" type="number" min="100" max="20000" value="${currentLimit}">
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
        <span>Allow shorts when strategy supports them</span>
      </label>
      <p class="modal-note">Short mode enables explicit short-side strategy rules where implemented; long-only presets remain long-only.</p>
      <div class="backtest-actions">
        <button id="modal-run-backtest" type="button">Run Backtest</button>
        <button id="modal-test-presets" type="button">Test presets</button>
        <button id="modal-optimize-strategy" type="button">Optimize</button>
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
  document.querySelector("#modal-optimize-strategy").addEventListener("click", () => optimizeStrategy(pane, presetSelect.value));
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
      chart_candles_count: String(pane.candles.length),
      first_chart_candle_time: String(pane.candles[0]?.time || ""),
      last_chart_candle_time: String(pane.candles[pane.candles.length - 1]?.time || ""),
      ...settings,
    });
    const response = await fetch(`/api/backtest?${params}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Backtest failed");

    pane.backtestMarkers = markersFromBacktestPayload(payload);
    renderBacktestOverlays(pane, payload);
    pane.backtestDiagnostics = payload.diagnostics?.overlay_rendering || payload.overlayDiagnostics || {};
    updateChartMarkers(pane);
    renderDataDiagnostics(pane);
    resultsEl.innerHTML = renderBacktestResults(payload);
  } catch (error) {
    resultsEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  } finally {
    pane.backtestButton.disabled = false;
    pane.backtestButton.textContent = "Backtest";
  }
}

function renderBacktestOverlays(pane, payload) {
  clearBacktestOverlaySeries(pane);
  (payload.overlays || []).forEach((overlay) => {
    const series = addSeries(pane.chart, overlay.type || "line", seriesOptions(overlay));
    series.setData(normalizeSeriesData(overlay.data));
    pane.backtestOverlaySeries.push(series);
  });
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

async function optimizeStrategy(pane, presetId) {
  const resultsEl = document.querySelector("#modal-results") || backtestContent;
  resultsEl.innerHTML = `<p class="pane-status">Running staged optimization for ${pane.symbolSelect.value}...</p>`;
  const settings = backtestSettings(presetId);
  const params = new URLSearchParams({
    source: pane.sourceSelect.value,
    symbol: pane.symbolSelect.value,
    timeframe: pane.timeframeSelect.value,
    period: "365d",
    preset: settings.preset,
    limit: settings.limit || "9000",
    max_combos: "1000",
  });
  try {
    const response = await fetch(`/api/optimize?${params}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Optimization failed");
    resultsEl.innerHTML = renderOptimizationSummary(payload);
  } catch (error) {
    resultsEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
}

function openBacktestModal(html) {
  backtestContent.innerHTML = html;
  backtestModal.hidden = false;
}

function openSignalDetails(pane) {
  const payload = pane.lastSignalPayload;
  if (!payload) return;
  openBacktestModal(renderSignalDetails(pane, payload));
}

function renderSignalDetails(pane, payload) {
  const components = payload.components || [];
  const details = payload.details || [];
  const warnings = payload.warnings || [];
  const componentRows = components.map((item) => `
    <tr>
      <td>${escapeHtml(item.name)}</td>
      <td class="${Number(item.score) >= 0 ? "positive" : "negative"}">${formatSigned(item.score)}</td>
    </tr>
  `).join("");
  const detailRows = details.map((item) => `
    <tr>
      <td>${escapeHtml(item.name)}</td>
      <td>${escapeHtml(item.value)}</td>
      <td>${escapeHtml(item.theory)}</td>
    </tr>
  `).join("");
  return `
    <div class="signal-detail-header">
      <div>
        <h3>${escapeHtml(pane.symbolSelect.value)} ${escapeHtml(pane.timeframeSelect.value)}</h3>
        <p>Technical-analysis hint only. This is not financial advice and no trade is placed.</p>
      </div>
      <div class="signal-badge ${payload.tone || "neutral"}">${escapeHtml(payload.label)} ${escapeHtml(String(payload.score))}</div>
    </div>
    ${warnings.length ? `<ul class="backtest-warnings">${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
    <h3 class="modal-section-title">Score Components</h3>
    <table class="trade-table">
      <thead><tr><th>Component</th><th>Score</th></tr></thead>
      <tbody>${componentRows || `<tr><td colspan="2">No score components available.</td></tr>`}</tbody>
    </table>
    <h3 class="modal-section-title">Why This Score</h3>
    <table class="trade-table signal-detail-table">
      <thead><tr><th>Input</th><th>Value</th><th>Rule</th></tr></thead>
      <tbody>${detailRows || `<tr><td colspan="3">No signal details available.</td></tr>`}</tbody>
    </table>
  `;
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
  const overlayDiagnostics = diagnostics.overlay_rendering || payload.overlayDiagnostics || {};
  const overlayMetrics = [
    ["Chart candles", overlayDiagnostics.chartCandlesCount],
    ["Backtest candles", overlayDiagnostics.backtestCandlesCount],
    ["First chart candle", formatDateTime(overlayDiagnostics.firstChartCandleTime)],
    ["Last chart candle", formatDateTime(overlayDiagnostics.lastChartCandleTime)],
    ["First overlay", formatDateTime(overlayDiagnostics.firstOverlayTime)],
    ["Last overlay", formatDateTime(overlayDiagnostics.lastOverlayTime)],
    ["Dropped bars", overlayDiagnostics.droppedBarsReason],
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
    <h3 class="modal-section-title">Data Diagnostics</h3>
    <div class="metric-grid diagnostics-grid">
      ${overlayMetrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value ?? "-"}</strong></div>`).join("")}
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

function renderOptimizationSummary(payload) {
  const topRows = (payload.top5 || []).map((row) => `
    <tr>
      <td>${escapeHtml(JSON.stringify(row.params))}</td>
      <td>${row.score}</td>
      <td>${row.train?.trades ?? "-"}</td>
      <td>${formatSigned(row.test?.totalReturn ?? 0)}%</td>
      <td>${row.test?.profitFactor ?? "-"}</td>
      <td>${row.test?.maxDrawdown ?? "-"}%</td>
      <td>${row.tradeAudit?.ok ? "ok" : "check"}</td>
    </tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Optimization Summary</h3>
    <div class="metric-grid">
      <div><span>Candles</span><strong>${payload.candlesUsed ?? "-"}</strong></div>
      <div><span>Stage 1</span><strong>${payload.combinationsTested?.stage1 ?? "-"}</strong></div>
      <div><span>Stage 2</span><strong>${payload.combinationsTested?.stage2 ?? "-"}</strong></div>
      <div><span>Valid</span><strong>${payload.validCandidates ?? 0}</strong></div>
    </div>
    <p class="modal-note">${escapeHtml(payload.robustnessAssessment || "Research output only; not financial advice.")}</p>
    <table class="trade-table">
      <thead><tr><th>Params</th><th>Score</th><th>Train Trades</th><th>Test Return</th><th>Test PF</th><th>Test DD</th><th>Audit</th></tr></thead>
      <tbody>${topRows}</tbody>
    </table>
  `;
}

function formatIsoDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function renderDataDiagnostics(pane) {
  if (!pane.dataDiagnosticsEl) return;
  const indicator = pane.indicatorDiagnostics || {};
  const backtest = pane.backtestDiagnostics || {};
  const firstEma200 = indicator.firstNonNullOverlayTime?.["EMA 200"];
  const lastOverlay = backtest.lastOverlayTime || indicator.lastOverlayTime;
  const overlayPoints = backtest.overlayPoints || indicator.overlayPoints || {};
  const indicatorCount = overlayPoints["EMA 200"] || overlayPoints["EMA 50"] || Object.values(overlayPoints)[0] || 0;
  pane.dataDiagnosticsEl.innerHTML = `
    <span>Candles ${pane.candles.length || 0}</span>
    <span>Indicators ${indicatorCount || 0}</span>
    <span>First ${formatDateTime(pane.candles[0]?.time)}</span>
    <span>EMA200 ${formatDateTime(firstEma200)}</span>
    <span>Last ${formatDateTime(pane.candles[pane.candles.length - 1]?.time)}</span>
    <span>Overlay last ${formatDateTime(lastOverlay)}</span>
  `;
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
  if (!timestamp) return "-";
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
      pane.candleDiagnostics = candles.diagnostics || {};
      pane.candles = candles.candles || [];
      if (!pane.candles.length) {
        pane.status.textContent = "No yfinance candles returned";
        return;
      }
      pane.series.setData(pane.candles);
      updateTicker(pane, pane.candles[pane.candles.length - 1].close);
      await renderIndicators(pane, pane.requestId);
      await renderSignals(pane, pane.requestId);
      renderDataDiagnostics(pane);
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
  if (grid) {
    grid.innerHTML = `<div class="fatal">${error.message}</div>`;
  } else {
    console.error(error);
  }
});
