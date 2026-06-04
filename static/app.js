const STORAGE_KEY = "tvk-dashboard-state";
const UI_PREF_KEY = "zgua-ui-preferences";
// Frontend boundary:
// This file owns UI state, chart rendering, marker rendering, websocket candle
// display, and API calls only. Indicator formulas, signal scoring, strategy
// rules, optimizer rankings, and backtest metrics belong in Python backend
// modules or the shared Node core under /core.
const BYBIT_WS = "wss://stream.bybit.com/v5/public/linear";
const HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws";
const CHART_COUNTS = [1, 2, 4, 6, 8];
const CHART_LIBRARY_URLS = [
  "/static/lightweight-charts.standalone.production.js",
  "https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js",
  "https://cdn.jsdelivr.net/npm/lightweight-charts/dist/lightweight-charts.standalone.production.js",
];
const WATCHLIST_REFRESH_MS = 30000;

const grid = document.querySelector("#chart-grid");
const countSelect = document.querySelector("#chart-count");
const template = document.querySelector("#pane-template");
const routePages = Array.from(document.querySelectorAll(".route-page"));
const navLinks = Array.from(document.querySelectorAll("[data-route]"));
const globalSourceSelect = document.querySelector("#global-source-select");
const globalSymbolSelect = document.querySelector("#global-symbol-select");
const globalTimeframeSelect = document.querySelector("#global-timeframe-select");
const globalIndicatorButton = document.querySelector("#global-indicator-button");
const globalIndicatorMenu = document.querySelector("#global-indicator-menu");
const globalIndicatorList = document.querySelector("#global-indicator-list");
const globalSignalToggle = document.querySelector("#global-signal-toggle");
const activeSignalsList = document.querySelector("#active-signals-list");
const watchlistContent = document.querySelector("#watchlist-content");
const watchlistAddCurrent = document.querySelector("#watchlist-add-current");
const watchlistAddAll = document.querySelector("#watchlist-add-all");
const watchlistSection = document.querySelector("#watchlist-section");
const activeSignalsSection = document.querySelector("#active-signals-section");
const bottomPanel = document.querySelector(".bottom-panel");
const bottomPanelContent = document.querySelector("#bottom-panel-content");
const backtestChartHost = document.querySelector("#backtest-chart-host");
const backtestResults = document.querySelector("#backtest-results");
const backtestModal = document.querySelector("#backtest-modal");
const backtestClose = document.querySelector("#backtest-close");
const backtestHistoryButton = document.querySelector("#backtest-history-button");
const backtestTitle = document.querySelector("#backtest-title");
const backtestContent = document.querySelector("#backtest-content");
const paperTabButton = document.querySelector("#paper-tab-button");
const paperPanel = document.querySelector("#paper-panel");
const paperPanelClose = document.querySelector("#paper-panel-close");
const paperPanelContent = document.querySelector("#paper-panel-content");

let config = null;
let state = loadState();
let uiPrefs = loadUiPrefs();
let panes = [];
let chartsInitialized = false;
let chartsToolbarInitialized = false;
let backtestInitialized = false;
let analysisInitialized = false;
let learningInitialized = false;
let opsInitialized = false;
let backtestPane = null;
let lastStrategyRankingPayload = null;
let lastOptimizationPayload = null;
let lastResearchSuggestion = null;
let lastLearningReport = null;
let lastPaperReplacementSuggestion = null;
let watchlistQuotes = new Map();
let watchlistRefreshTimer = null;
let watchlistRefreshInFlight = false;

window.api = window.api || {
  get: apiGet,
};

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

function loadUiPrefs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(UI_PREF_KEY)) || {};
    return {
      watchlist: Array.isArray(parsed.watchlist) && parsed.watchlist.length ? parsed.watchlist : ["BTCUSDT"],
      watchlistOpen: parsed.watchlistOpen !== false,
      activeSignalsOpen: parsed.activeSignalsOpen !== false,
      syncIndicators: parsed.syncIndicators !== false,
    };
  } catch {
    return { watchlist: ["BTCUSDT"], watchlistOpen: true, activeSignalsOpen: true, syncIndicators: true };
  }
}

function saveUiPrefs() {
  localStorage.setItem(UI_PREF_KEY, JSON.stringify(uiPrefs));
}

function hasElement(...elements) {
  return elements.every(Boolean);
}

async function apiGet(url) {
  const response = await fetch(url);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : { error: await response.text() };
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

async function apiPost(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : { error: await response.text() };
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

async function boot() {
  await loadChartLibrary();

  const response = await fetch("/api/config");
  config = await response.json();

  if (!hasElement(grid, countSelect, template, backtestModal, backtestClose, backtestContent)) {
    throw new Error("Dashboard HTML is incomplete. Please restart Flask and hard-refresh the page.");
  }

  setupNavigation();
  setupSidebar();
  backtestClose.addEventListener("click", closeBacktestModal);
  backtestHistoryButton?.addEventListener("click", openBacktestHistory);
  backtestModal.addEventListener("click", (event) => {
    if (event.target === backtestModal) closeBacktestModal();
  });
  if (hasElement(paperTabButton, paperPanel, paperPanelClose, paperPanelContent)) {
    paperTabButton.addEventListener("click", openPaperPanel);
    paperPanelClose.addEventListener("click", () => {
      paperPanel.hidden = true;
    });
    paperPanelContent.addEventListener("click", (event) => {
      const button = event.target.closest("[data-paper-action]");
      if (button) handlePaperAction(button.dataset.paperAction);
      const replacementButton = event.target.closest("[data-promote-replacement]");
      if (replacementButton && lastPaperReplacementSuggestion?.candidate) {
        promoteResearchCandidate(lastPaperReplacementSuggestion.candidate, lastPaperReplacementSuggestion, "#paper-health-result");
      }
    });
  }
  bottomPanel?.querySelector("summary")?.addEventListener("click", (event) => {
    event.preventDefault();
    openBottomPanelModal();
  });

  document.addEventListener("click", (event) => {
    const diagnoseButton = event.target?.closest?.("[data-diagnose-backtest]");
    if (diagnoseButton) {
      runBacktestDiagnosis(diagnoseButton);
      return;
    }
    const infoButton = event.target?.closest?.(".indicator-info-button");
    const syncButton = event.target?.closest?.(".indicator-sync-button");
    if (infoButton || syncButton) {
      const paneNode = event.target.closest(".pane");
      const pane = panes.find((item) => item.node === paneNode);
      if (pane && infoButton) {
        const indicatorPane = pane.indicatorInfoById.get(infoButton.dataset.indicatorId);
        if (indicatorPane) openIndicatorInfo(indicatorPane);
        return;
      }
      if (pane && syncButton) {
        pane.syncIndicators = !pane.syncIndicators;
        uiPrefs.syncIndicators = pane.syncIndicators;
        syncButton.textContent = pane.syncIndicators ? "Sync on" : "Sync off";
        saveUiPrefs();
        syncIndicatorTimeRanges(pane);
        return;
      }
    }
    panes.forEach((pane) => {
      if (!pane.indicatorMenu.contains(event.target) && event.target !== pane.indicatorButton) {
        pane.indicatorMenu.hidden = true;
      }
    });
    if (globalIndicatorMenu && globalIndicatorButton && !globalIndicatorMenu.contains(event.target) && event.target !== globalIndicatorButton) {
      globalIndicatorMenu.hidden = true;
    }
  }, true);

  window.addEventListener("popstate", () => showPage(pathToPage(window.location.pathname)));
  showPage(pathToPage(window.location.pathname));
  window.setInterval(() => refreshWatchlistData(), WATCHLIST_REFRESH_MS);
}

function setupNavigation() {
  navLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      const page = link.dataset.route;
      if (!page) return;
      event.preventDefault();
      const path = page === "charts" ? "/charts" : `/${page}`;
      history.pushState({}, "", path);
      showPage(page);
    });
  });
}

function pathToPage(pathname) {
  if (pathname === "/backtest") return "backtest";
  if (pathname === "/analysis") return "analysis";
  if (pathname === "/learning") return "learning";
  if (pathname === "/ops") return "ops";
  if (pathname === "/settings") return "settings";
  return "charts";
}

function showPage(page) {
  routePages.forEach((section) => {
    section.hidden = section.dataset.page !== page;
  });
  navLinks.forEach((link) => {
    link.classList.toggle("active", link.dataset.route === page);
  });
  if (page === "charts") initChartsPage();
  if (page === "backtest") initBacktestPage();
  if (page === "analysis") renderAnalysisPage();
  if (page === "learning") renderLearningPage();
  if (page === "ops") renderOpsPage();
  if (page === "settings") renderSettingsPage();
}

function initChartsPage() {
  if (!chartsToolbarInitialized) setupChartsToolbar();
  if (!chartsInitialized) {
    countSelect.value = CHART_COUNTS.includes(state.count) ? String(state.count) : "1";
    renderPanes(Number(countSelect.value));
    chartsInitialized = true;
  }
}

function setupSidebar() {
  if (watchlistSection) {
    watchlistSection.open = uiPrefs.watchlistOpen;
    watchlistSection.addEventListener("toggle", () => {
      uiPrefs.watchlistOpen = watchlistSection.open;
      saveUiPrefs();
    });
  }
  if (activeSignalsSection) {
    activeSignalsSection.open = uiPrefs.activeSignalsOpen;
    activeSignalsSection.addEventListener("toggle", () => {
      uiPrefs.activeSignalsOpen = activeSignalsSection.open;
      saveUiPrefs();
    });
  }
  watchlistAddCurrent?.addEventListener("click", () => {
    const symbol = panes[0]?.symbolSelect?.value || globalSymbolSelect?.value || "BTCUSDT";
    addToWatchlist(symbol);
  });
  watchlistAddAll?.addEventListener("click", () => {
    const symbols = config?.sources?.bybit?.symbols || [];
    uiPrefs.watchlist = Array.from(new Set([...uiPrefs.watchlist, ...symbols]));
    saveUiPrefs();
    renderWatchlist();
    refreshWatchlistData(true);
  });
  renderWatchlist();
  refreshWatchlistData(true);
}

function setupChartsToolbar() {
  chartsToolbarInitialized = true;
  populateGlobalMarketControls();
  populateGlobalIndicatorMenu();
  countSelect.addEventListener("change", () => renderPanes(Number(countSelect.value)));
  [globalSourceSelect, globalSymbolSelect, globalTimeframeSelect].forEach((select) => {
    select?.addEventListener("change", applyGlobalMarketControls);
  });
  globalIndicatorButton?.addEventListener("click", (event) => {
    event.stopPropagation();
    globalIndicatorMenu.hidden = !globalIndicatorMenu.hidden;
  });
  globalIndicatorList?.addEventListener("change", applyGlobalIndicatorControls);
  globalSignalToggle?.addEventListener("change", applyGlobalSignalToggle);
}

function populateGlobalMarketControls() {
  if (!hasElement(globalSourceSelect, globalSymbolSelect, globalTimeframeSelect)) return;
  globalSourceSelect.innerHTML = Object.entries(config.sources)
    .map(([value, item]) => `<option value="${value}">${item.label}</option>`)
    .join("");
  globalSourceSelect.value = config.sources[state.panes?.[0]?.source] ? state.panes[0].source : "bybit";
  populateGlobalSymbolAndTimeframe(state.panes?.[0] || {});
}

function populateGlobalSymbolAndTimeframe(saved = {}) {
  const sourceConfig = config.sources[globalSourceSelect.value];
  globalSymbolSelect.innerHTML = sourceConfig.symbols.map((symbol) => `<option value="${symbol}">${symbol}</option>`).join("");
  globalTimeframeSelect.innerHTML = sourceConfig.timeframes.map((timeframe) => `<option value="${timeframe}">${timeframe}</option>`).join("");
  globalSymbolSelect.value = sourceConfig.symbols.includes(saved.symbol) ? saved.symbol : sourceConfig.symbols[0];
  globalTimeframeSelect.value = sourceConfig.timeframes.includes(saved.timeframe) ? saved.timeframe : sourceConfig.timeframes[0];
}

function populateGlobalIndicatorMenu() {
  if (!globalIndicatorList) return;
  globalIndicatorList.innerHTML = (config.indicators || [])
    .map((indicator) => `
      <label class="indicator-option">
        <input type="checkbox" value="${indicator.id}">
        <span>${indicator.label}</span>
      </label>
    `)
    .join("");
  restoreGlobalIndicators(state.panes?.[0]?.indicators || []);
  if (globalSignalToggle) globalSignalToggle.checked = state.panes?.[0]?.signalMarkers !== false;
  updateGlobalIndicatorButton();
}

function restoreGlobalIndicators(ids) {
  const selected = new Set(ids || []);
  globalIndicatorList?.querySelectorAll("input[type='checkbox']").forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function selectedGlobalIndicators() {
  return Array.from(globalIndicatorList?.querySelectorAll("input[type='checkbox']:checked") || []).map((input) => input.value);
}

function updateGlobalIndicatorButton() {
  if (!globalIndicatorButton) return;
  const count = selectedGlobalIndicators().length;
  globalIndicatorButton.textContent = count ? `Indicators ${count}` : "Indicators";
}

function applyGlobalMarketControls() {
  if (!panes.length) return;
  if (document.activeElement === globalSourceSelect) populateGlobalSymbolAndTimeframe({});
  panes.forEach((pane) => {
    pane.sourceSelect.value = globalSourceSelect.value;
    populateSymbolAndTimeframe(pane, {
      symbol: globalSymbolSelect.value,
      timeframe: globalTimeframeSelect.value,
    });
    startPane(pane);
  });
  saveState();
}

function applyGlobalIndicatorControls() {
  updateGlobalIndicatorButton();
  const selected = selectedGlobalIndicators();
  panes.forEach((pane) => {
    restoreIndicators(pane, selected);
    startPane(pane);
  });
  saveState();
}

function applyGlobalSignalToggle() {
  panes.forEach((pane) => {
    pane.signalMarkerToggle.checked = globalSignalToggle.checked;
    updateChartMarkers(pane);
  });
  saveState();
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
  const candidate = payload.candidate || {};
  const activeSymbols = candidate.activeSymbols || [];
  const watchSymbols = candidate.watchSymbols || [];
  const paperEnabled = Boolean(payload.paperEnabled ?? candidate.enabled);
  return `
    <div class="paper-warning">Simulated only. No real order execution, no exchange account connection, no API keys.</div>
    <div class="metric-grid">
      <div class="metric"><span>Enabled</span><strong>${paperEnabled ? "Yes" : "No"}</strong></div>
      <div class="metric"><span>Equity</span><strong>${formatPrice(Number(payload.equity || 0))}</strong></div>
      <div class="metric"><span>Realized PnL</span><strong class="${payload.realizedPnL >= 0 ? "positive" : "negative"}">${formatSigned(payload.realizedPnL)}</strong></div>
      <div class="metric"><span>Unrealized PnL</span><strong class="${payload.unrealizedPnL >= 0 ? "positive" : "negative"}">${formatSigned(payload.unrealizedPnL)}</strong></div>
      <div class="metric"><span>Fees</span><strong>${formatPrice(Number(payload.totalFees || 0))}</strong></div>
      <div class="metric"><span>Slippage</span><strong>${formatPrice(Number(payload.totalSlippage || 0))}</strong></div>
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(candidate.strategy || "-")}</strong></div>
      <div class="metric"><span>Fill</span><strong>${escapeHtml(candidate.fillModel || "-")}</strong></div>
    </div>
    <h3 class="modal-section-title">Current Candidate</h3>
    <table class="trade-table">
      <tbody>
        <tr><th>Source</th><td>${escapeHtml(candidate.source || "-")}</td><th>Promoted</th><td>${candidate.promotedAt ? escapeHtml(new Date(candidate.promotedAt).toLocaleString()) : "-"}</td></tr>
        <tr><th>Active</th><td colspan="3">${activeSymbols.map((item) => `${escapeHtml(item.symbol)} ${escapeHtml(item.interval)}`).join(", ") || "-"}</td></tr>
        <tr><th>Watch</th><td colspan="3">${watchSymbols.map((item) => `${escapeHtml(item.symbol)} ${escapeHtml(item.interval)}`).join(", ") || "-"}</td></tr>
        <tr><th>Ranking</th><td colspan="3">${candidate.promotedFromRanking ? `Rank ${escapeHtml(candidate.promotedFromRanking.rank)} · Score ${escapeHtml(candidate.promotedFromRanking.score)} · PF ${escapeHtml(candidate.promotedFromRanking.profitFactor)} · Trades ${escapeHtml(candidate.promotedFromRanking.trades)}` : "-"}</td></tr>
      </tbody>
    </table>
    <div class="paper-actions">
      <button type="button" data-paper-action="validate">Validate Candidate</button>
      <button type="button" data-paper-action="enable">Enable Paper Simulation</button>
      <button type="button" data-paper-action="disable">Disable Paper Simulation</button>
    </div>
    <div id="paper-validation-result" class="paper-validation-result">
      <p class="modal-note">Validation must pass before paper simulation can be enabled without force. This is simulated only.</p>
    </div>
    <h3 class="modal-section-title">Candidate Health</h3>
    <div class="paper-actions">
      <button type="button" data-paper-action="health">Review Health</button>
      <button type="button" data-paper-action="replacement">Suggest Replacement</button>
    </div>
    <div id="paper-health-result" class="paper-validation-result">
      <p class="modal-note">Health compares forward paper performance against the promoted research baseline.</p>
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

async function handlePaperAction(action) {
  const resultEl = document.querySelector("#paper-validation-result");
  try {
    if (action === "validate") {
      if (resultEl) resultEl.innerHTML = `<p class="pane-status">Validating candidate...</p>`;
      const payload = await apiGet("/api/candidate/validate");
      if (resultEl) resultEl.innerHTML = renderCandidateValidation(payload.validation);
      return;
    }
    if (action === "health") {
      const healthEl = document.querySelector("#paper-health-result");
      if (healthEl) healthEl.innerHTML = `<p class="pane-status">Reviewing paper health...</p>`;
      const payload = await apiGet("/api/candidate/health");
      if (healthEl) healthEl.innerHTML = renderCandidateHealth(payload.health);
      return;
    }
    if (action === "replacement") {
      const healthEl = document.querySelector("#paper-health-result");
      if (healthEl) healthEl.innerHTML = `<p class="pane-status">Searching saved research for replacement...</p>`;
      const payload = await apiPost("/api/research/suggest-replacement", {});
      lastPaperReplacementSuggestion = payload;
      if (healthEl) healthEl.innerHTML = renderReplacementSuggestion(payload);
      return;
    }
    if (action === "enable") {
      const ok = window.confirm("Enable paper simulation for this candidate?\n\nThis is still simulated only. No real exchange orders will be placed.");
      if (!ok) return;
      const payload = await apiPost("/api/paper/enable", {});
      if (resultEl) resultEl.innerHTML = renderPaperControlResult(payload, "Paper simulation enabled.");
      await openPaperPanel();
      await refreshPaperLearningPanels();
      return;
    }
    if (action === "disable") {
      const payload = await apiPost("/api/paper/disable", {});
      if (resultEl) resultEl.innerHTML = renderPaperControlResult(payload, "Paper simulation disabled.");
      await openPaperPanel();
      await refreshPaperLearningPanels();
    }
  } catch (error) {
    const targetEl = action === "health" || action === "replacement"
      ? document.querySelector("#paper-health-result")
      : resultEl;
    if (targetEl) targetEl.innerHTML = `<p class="pane-status">Paper action failed: ${escapeHtml(error.message)}</p>`;
  }
}

async function refreshPaperLearningPanels() {
  await Promise.all([
    apiGet("/api/candidate/current").catch(() => null),
    loadPaperReadiness(),
    loadPaperObservationReport(),
    loadPaperSignalDiagnostics(),
    loadPaperCandidateComparison(),
    loadPaperSimulationControl(),
    loadPaperRuntimeMonitor(),
    loadPaperTickReadiness(),
    loadActivePaperObservation(),
    loadPaperSessionMonitor(),
    loadPaperSessionEventsSummary(),
    loadPaperSessionEventsDetail(),
    loadPaperSessionTrades(),
    loadPaperObservationCounters(),
    loadPaperObservationTargets(),
    loadPaperRunnerInstructions(),
    loadPaperRunnerSummary(),
    loadPaperObservationQuality(),
  ]);
}

function renderCandidateValidation(validation) {
  if (!validation) return `<p class="pane-status">No validation returned.</p>`;
  const statusClass = validation.status === "PASS" ? "positive" : validation.status === "FAIL" ? "negative" : "neutral";
  return `
    <h3 class="modal-section-title">Validation <span class="${statusClass}">${escapeHtml(validation.status)}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Markets</span><strong>${validation.summary?.marketsValidated || 0}</strong></div>
      <div class="metric"><span>Pass</span><strong>${validation.summary?.pass || 0}</strong></div>
      <div class="metric"><span>Warn</span><strong>${validation.summary?.warn || 0}</strong></div>
      <div class="metric"><span>Fail</span><strong>${validation.summary?.fail || 0}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Market</th><th>Status</th><th>Return</th><th>PF</th><th>DD</th><th>Trades</th><th>Reasons</th></tr></thead>
      <tbody>${(validation.rows || []).map((row) => `
        <tr>
          <td>${escapeHtml(row.symbol)} ${escapeHtml(row.timeframe)}</td>
          <td>${escapeHtml(row.status)}</td>
          <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
          <td>${formatNumber(row.profitFactor)}</td>
          <td>${formatNumber(row.maxDrawdown)}%</td>
          <td>${row.trades}</td>
          <td>${escapeHtml((row.reasons || []).join(" ")) || "-"}</td>
        </tr>
      `).join("")}</tbody>
    </table>
  `;
}

function renderCandidateHealth(health) {
  if (!health) return `<p class="pane-status">No health payload returned.</p>`;
  const statusClass = health.status === "HEALTHY" ? "positive" : health.status === "FAILED" || health.status === "DEGRADED" ? "negative" : "neutral";
  return `
    <h3 class="modal-section-title">Health <span class="${statusClass}">${escapeHtml(health.status)}</span></h3>
    <p class="modal-note">${escapeHtml(health.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper trades</span><strong>${health.paper?.closedTrades || 0}</strong></div>
      <div class="metric"><span>Paper PF</span><strong>${formatNumber(health.paper?.profitFactor)}</strong></div>
      <div class="metric"><span>Paper return</span><strong>${formatSigned(health.paper?.totalReturnPct)}%</strong></div>
      <div class="metric"><span>Expected PF</span><strong>${formatNumber(health.expected?.profitFactor)}</strong></div>
      <div class="metric"><span>Expected return</span><strong>${formatSigned(health.expected?.totalReturnPct)}%</strong></div>
      <div class="metric"><span>Recommendation</span><strong>${escapeHtml(health.recommendation?.action || "-")}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Metric</th><th>Expected</th><th>Paper</th></tr></thead>
      <tbody>
        <tr><td>Win rate</td><td>${formatNumber(health.expected?.winRate)}%</td><td>${formatNumber(health.paper?.winRate)}%</td></tr>
        <tr><td>Max drawdown</td><td>${formatNumber(health.expected?.maxDrawdown)}%</td><td>${formatNumber(health.paper?.maxDrawdown)}%</td></tr>
        <tr><td>Trades</td><td>${health.expected?.trades || 0}</td><td>${health.paper?.closedTrades || 0}</td></tr>
        <tr><td>Realized PnL</td><td>-</td><td>${formatSigned(health.paper?.realizedPnL)}</td></tr>
      </tbody>
    </table>
    ${(health.reasons || []).length ? `<ul class="backtest-warnings">${health.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}
  `;
}

function renderReplacementSuggestion(payload) {
  const candidate = payload.candidate;
  return `
    ${renderCandidateHealth(payload.health)}
    <h3 class="modal-section-title">Replacement Suggestion</h3>
    <p class="modal-note"><strong>${escapeHtml(payload.action || "-")}</strong> ${escapeHtml(payload.reason || "")}</p>
    ${candidate ? `
      <table class="trade-table">
        <tbody>
          <tr><th>Candidate</th><td>${escapeHtml(candidate.strategy)} ${escapeHtml(candidate.symbol)} ${escapeHtml(candidate.timeframe)}</td></tr>
          <tr><th>Score</th><td>${formatNumber(candidate.score)}</td></tr>
          <tr><th>PF / Trades</th><td>${formatNumber(candidate.profitFactor)} / ${candidate.trades || 0}</td></tr>
        </tbody>
      </table>
      <button type="button" class="small-action-button" data-promote-replacement="1">Promote Suggested Replacement</button>
    ` : ""}
  `;
}

async function refreshSidebarPaperStatus() {
  if (!activeSignalsList) return;
  try {
    const response = await fetch("/api/paper/status");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Paper status failed");
    const positions = payload.openPositions || [];
    const openTradeHtml = positions.length
      ? `
        <div class="open-trade-row">
          <strong>Open simulated trade</strong>
          <table class="compact-table">
            <tbody>${positions.slice(0, 4).map((position) => `
              <tr>
                <td>${escapeHtml(position.symbol)} ${escapeHtml(position.interval || position.timeframe || "")}</td>
                <td>${escapeHtml(position.side || position.direction || "-")}</td>
                <td>${formatPrice(Number(position.entryFillPrice || position.entryPrice || 0))}</td>
                <td>${formatPrice(Number(position.lastPrice || position.currentPrice || 0))}</td>
                <td class="${Number(position.unrealizedPnl || 0) >= 0 ? "positive" : "negative"}">${formatSigned(position.unrealizedPnl || 0)}</td>
              </tr>
            `).join("")}</tbody>
          </table>
        </div>
      `
      : `<div class="open-trade-row muted-row">No open simulated trades.</div>`;
    activeSignalsList.insertAdjacentHTML("beforeend", openTradeHtml);
  } catch (error) {
    activeSignalsList.insertAdjacentHTML("beforeend", `<div class="open-trade-row muted-row">Paper status unavailable: ${escapeHtml(error.message)}</div>`);
  }
}

function addToWatchlist(symbol) {
  if (!symbol) return;
  uiPrefs.watchlist = Array.from(new Set([...(uiPrefs.watchlist || []), symbol]));
  saveUiPrefs();
  renderWatchlist();
  refreshWatchlistData(true);
}

function removeFromWatchlist(symbol) {
  uiPrefs.watchlist = (uiPrefs.watchlist || []).filter((item) => item !== symbol);
  if (!uiPrefs.watchlist.length) uiPrefs.watchlist = ["BTCUSDT"];
  saveUiPrefs();
  renderWatchlist();
}

function renderWatchlist() {
  if (!watchlistContent) return;
  const symbols = uiPrefs.watchlist?.length ? uiPrefs.watchlist : ["BTCUSDT"];
  const activeSymbol = panes[0]?.symbolSelect?.value || "";
  watchlistContent.innerHTML = `
    <table class="watchlist-table compact-table">
      <thead><tr><th></th><th>Symbol</th><th>Price</th><th>Signal</th></tr></thead>
      <tbody>${symbols.map((symbol) => {
        const quote = watchlistQuotes.get(symbol);
        const price = quote?.price ? formatPrice(Number(quote.price)) : "-";
        const signalClass = quote?.tone || "neutral";
        const signal = quote?.score ?? "-";
        return `
          <tr class="${symbol === activeSymbol ? "active-watch-symbol" : ""}">
            <td><button class="star-button active" type="button" data-remove-watch="${escapeHtml(symbol)}" title="Remove from watchlist">★</button></td>
            <td><button class="link-button watch-symbol-button" type="button" data-watch-symbol="${escapeHtml(symbol)}">${escapeHtml(symbol)}</button></td>
            <td>${price}</td>
            <td class="${signalClass} watch-score">${signal}</td>
          </tr>
        `;
      }).join("")}</tbody>
    </table>
    <p class="sidebar-help">Use ★ Add current or Add all to fill this list.</p>
  `;
  watchlistContent.querySelectorAll("[data-remove-watch]").forEach((button) => {
    button.addEventListener("click", () => removeFromWatchlist(button.dataset.removeWatch));
  });
  watchlistContent.querySelectorAll("[data-watch-symbol]").forEach((button) => {
    button.addEventListener("click", () => setPrimaryChartSymbol(button.dataset.watchSymbol));
  });
}

function scheduleWatchlistRefresh() {
  window.clearTimeout(watchlistRefreshTimer);
  watchlistRefreshTimer = window.setTimeout(() => refreshWatchlistData(), 250);
}

async function refreshWatchlistData(force = false) {
  if (!watchlistContent || watchlistRefreshInFlight) return;
  const symbols = uiPrefs.watchlist?.length ? uiPrefs.watchlist : ["BTCUSDT"];
  const activeSource = panes[0]?.sourceSelect?.value || "bybit";
  const activeTimeframe = panes[0]?.timeframeSelect?.value || "1h";
  const now = Date.now();
  const staleSymbols = symbols.filter((symbol) => force || !watchlistQuotes.get(symbol) || now - watchlistQuotes.get(symbol).updatedAt > WATCHLIST_REFRESH_MS);
  if (!staleSymbols.length) return;
  watchlistRefreshInFlight = true;
  try {
    for (const symbol of staleSymbols) {
      try {
        const source = sourceForWatchSymbol(symbol, activeSource);
        const timeframe = timeframeForWatchSymbol(source, activeTimeframe);
        const [candlesPayload, signalPayload] = await Promise.all([
          apiGet(`/api/candles?${new URLSearchParams({ source, symbol, timeframe, limit: "1", visible_charts: String(visibleChartCount()) })}`),
          apiGet(`/api/signals?${new URLSearchParams({ source, symbol, timeframe, limit: "300", include_timeframes: "false" })}`),
        ]);
        const candle = candlesPayload.candles?.[candlesPayload.candles.length - 1];
        watchlistQuotes.set(symbol, {
          price: candle?.close,
          score: signalPayload.score,
          tone: signalPayload.tone || "neutral",
          direction: signalPayload.signalDirection || "NEUTRAL",
          updatedAt: Date.now(),
        });
        renderWatchlist();
      } catch (error) {
        const prior = watchlistQuotes.get(symbol) || {};
        watchlistQuotes.set(symbol, { ...prior, score: "ERR", tone: "neutral", updatedAt: Date.now() });
      }
    }
  } finally {
    watchlistRefreshInFlight = false;
    renderWatchlist();
  }
}

function sourceForWatchSymbol(symbol, preferredSource) {
  const preferred = config?.sources?.[preferredSource];
  if (preferred?.symbols?.includes(symbol)) return preferredSource;
  if (config?.sources?.bybit?.symbols?.includes(symbol)) return "bybit";
  return Object.entries(config?.sources || {}).find(([, source]) => source.symbols?.includes(symbol))?.[0] || preferredSource || "bybit";
}

function timeframeForWatchSymbol(source, preferredTimeframe) {
  const timeframes = config?.sources?.[source]?.timeframes || [];
  if (timeframes.includes(preferredTimeframe)) return preferredTimeframe;
  return timeframes.includes("1h") ? "1h" : timeframes[0] || "1h";
}

function setPrimaryChartSymbol(symbol) {
  const pane = panes[0];
  if (!pane || !symbol) return;
  if (!Array.from(pane.symbolSelect.options).some((option) => option.value === symbol)) return;
  pane.symbolSelect.value = symbol;
  syncChartsToolbarFromPane(pane);
  startPane(pane);
  saveState();
  refreshWatchlistData(true);
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
  syncChartsToolbarFromPane(panes[0]);
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
    markerDetailEl: node.querySelector(".marker-detail-popover"),
    status: node.querySelector(".pane-status"),
    dataDiagnosticsEl: null,
    chart: null,
    series: null,
    overlaySeries: [],
    backtestOverlaySeries: [],
    indicatorCharts: [],
    indicatorInfoById: new Map(),
    signalMarkers: [],
    backtestMarkers: [],
    markerDetailsByTime: new Map(),
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
    syncIndicators: uiPrefs.syncIndicators !== false,
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
      renderWatchlist();
      refreshWatchlistData(true);
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

function syncChartsToolbarFromPane(pane) {
  if (!pane || !hasElement(globalSourceSelect, globalSymbolSelect, globalTimeframeSelect)) return;
  globalSourceSelect.value = pane.sourceSelect.value;
  populateGlobalSymbolAndTimeframe({
    symbol: pane.symbolSelect.value,
    timeframe: pane.timeframeSelect.value,
  });
  pane.indicatorPanesEl.addEventListener("click", (event) => {
    const infoButton = event.target?.closest?.(".indicator-info-button");
    if (infoButton) {
      const indicatorPane = pane.indicatorInfoById.get(infoButton.dataset.indicatorId);
      if (indicatorPane) openIndicatorInfo(indicatorPane);
    }
    const syncButton = event.target?.closest?.(".indicator-sync-button");
    if (syncButton) {
      pane.syncIndicators = !pane.syncIndicators;
      uiPrefs.syncIndicators = pane.syncIndicators;
      syncButton.textContent = pane.syncIndicators ? "Sync on" : "Sync off";
      saveUiPrefs();
      syncIndicatorTimeRanges(pane);
    }
  });
  restoreGlobalIndicators(selectedIndicators(pane));
  if (globalSignalToggle) globalSignalToggle.checked = pane.signalMarkerToggle.checked;
  updateGlobalIndicatorButton();
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
  if (pane.chart.subscribeCrosshairMove) {
    pane.chart.subscribeCrosshairMove((param) => showMarkerDetailsForCrosshair(pane, param));
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
    pane.status.textContent = candleStatusText(candlePayload);
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
    pane.status.textContent = candleStatusText(candlePayload);
    renderDataDiagnostics(pane);
    await renderIndicators(pane, requestId);
    await renderSignals(pane, requestId);
  } catch (error) {
    if (requestId === pane.requestId) pane.status.textContent = `Older history unavailable: ${error.message}`;
  }
}

async function renderSignals(pane, requestId) {
  // Signals are scored by /api/signals. The browser only renders the returned
  // badge, details, and optional chart markers.
  resetSignalBadge(pane);
  clearSignalMarkers(pane);

  const params = new URLSearchParams({
    source: pane.sourceSelect.value,
    symbol: pane.symbolSelect.value,
    timeframe: pane.timeframeSelect.value,
    limit: String(Math.max(pane.candles.length, 300)),
  });
  const response = await fetch(`/api/signals?${params}`);
  const payload = await response.json();
  if (requestId !== pane.requestId) return;
  if (!response.ok) throw new Error(payload.error || "Signal request failed");

  pane.signalMarkers = payload.markers || [];
  pane.lastSignalPayload = payload;
  watchlistQuotes.set(pane.symbolSelect.value, {
    ...(watchlistQuotes.get(pane.symbolSelect.value) || {}),
    score: payload.score,
    tone: payload.tone || "neutral",
    direction: payload.signalDirection || "NEUTRAL",
    updatedAt: Date.now(),
  });
  updateSignalBadge(pane, payload);
  updateChartMarkers(pane);
  updateChartsPanels(pane, payload);
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

function candleStatusText(payload) {
  const count = payload.candles?.length || 0;
  const warnings = payload.diagnostics?.warnings || [];
  if (payload.diagnostics?.degraded_to_stale_cache) {
    return `${count} cached candles loaded; ${warnings[0] || "data may be stale"}`;
  }
  return `${count} candles loaded`;
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
  // Indicators are calculated by /api/indicators. Keep formula changes in the
  // backend/core modules and render the returned time-aligned series here.
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
  pane.indicatorInfoById = new Map();

  payload.overlays.forEach((overlay) => {
    const series = addSeries(pane.chart, overlay.type, seriesOptions(overlay));
    series.setData(normalizeSeriesData(overlay.data));
    nextOverlaySeries.push(series);
  });

  payload.panes.forEach((indicatorPane) => {
    const indicatorId = indicatorPane.id || indicatorPane.title || `indicator-${nextPaneElements.length}`;
    pane.indicatorInfoById.set(indicatorId, indicatorPane);
    const paneEl = document.createElement("div");
    paneEl.className = "indicator-pane";
    paneEl.innerHTML = `
      <div class="indicator-title">
        <button class="indicator-info-button" type="button" data-indicator-id="${escapeHtml(indicatorId)}">${escapeHtml(indicatorPane.title)}</button>
        <button class="indicator-sync-button" type="button">${pane.syncIndicators ? "Sync on" : "Sync off"}</button>
      </div>
      <div class="indicator-chart"></div>
    `;
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
    let markerSeries = null;
    item.pane.series.forEach((seriesConfig) => {
      const series = addSeries(chart, seriesConfig.type, seriesOptions(seriesConfig));
      series.setData(normalizeSeriesData(seriesConfig.data));
      if (!markerSeries && !seriesConfig.guide) markerSeries = series;
    });
    const paneMarkers = normalizeMarkers(item.pane.markers || []);
    if (markerSeries && paneMarkers.length) setSeriesMarkers(markerSeries, paneMarkers);
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
  const direction = payload.signalDirection || (Number(payload.score) < -25 ? "SHORT" : Number(payload.score) > 25 ? "LONG" : "NEUTRAL");
  pane.signalBadge.textContent = `${direction} ${payload.score}`;
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
  pane.markerDetailsByTime = buildMarkerDetailsMap(visibleMarkers);
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

function normalizeMarkers(markers) {
  return (markers || []).map((marker) => ({
    ...marker,
    time: normalizeMarkerTime(marker.time),
  })).filter((marker) => marker.time !== null);
}

function setSeriesMarkers(series, markers) {
  if (series.setMarkers) {
    series.setMarkers(markers);
    return;
  }
  if (LightweightCharts.createSeriesMarkers) LightweightCharts.createSeriesMarkers(series, markers);
}

function clearSignalMarkers(pane) {
  pane.signalMarkers = [];
  hideMarkerDetails(pane);
  updateChartMarkers(pane);
}

function clearBacktestMarkers(pane) {
  pane.backtestMarkers = [];
  hideMarkerDetails(pane);
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
  if (!pane.syncIndicators) return;
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

function backtestLimitOptions(selected = "auto") {
  const values = ["auto", "1000", "5000", "9000", "20000", "35000", "50000"];
  return values.map((value) => `<option value="${value}" ${String(selected) === value ? "selected" : ""}>${value === "auto" ? "Auto" : value}</option>`).join("");
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
        <select id="modal-limit-input">${backtestLimitOptions("auto")}</select>
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
    limit: document.querySelector("#modal-limit-input")?.value || "auto",
    fee_pct: document.querySelector("#modal-fee-input")?.value || "0",
    slippage_pct: document.querySelector("#modal-slippage-input")?.value || "0",
    allowShorts: document.querySelector("#modal-allow-shorts")?.checked ? "true" : "false",
  };
}

function buildMarkerDetailsMap(markers) {
  const map = new Map();
  markers.forEach((marker) => {
    const time = normalizeMarkerTime(marker.time);
    if (time === null) return;
    if (!map.has(time)) map.set(time, []);
    map.get(time).push(marker);
  });
  return map;
}

function showMarkerDetailsForCrosshair(pane, param) {
  if (!pane.markerDetailEl) return;
  const time = normalizeMarkerTime(param?.time);
  if (time === null) {
    hideMarkerDetails(pane);
    return;
  }
  const markers = pane.markerDetailsByTime?.get(time);
  if (!markers?.length) {
    hideMarkerDetails(pane);
    return;
  }
  pane.markerDetailEl.innerHTML = renderMarkerDetails(markers, time);
  pane.markerDetailEl.hidden = false;
}

function hideMarkerDetails(pane) {
  if (pane.markerDetailEl) pane.markerDetailEl.hidden = true;
}

function renderMarkerDetails(markers, time) {
  return markers.map((marker) => {
    const rows = markerRows(marker);
    return `
      <section>
        <h3>${escapeHtml(marker.text || marker.type || "Marker")} - ${formatDateTime(time)}</h3>
        <p>${escapeHtml(marker.reason || marker.summary || "Backend-returned marker detail.")}</p>
        <dl class="marker-detail-list">
          ${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}
        </dl>
      </section>
    `;
  }).join("");
}

function markerRows(marker) {
  const rows = [];
  if (marker.score !== undefined) rows.push(["Score", String(marker.score)]);
  if (marker.label) rows.push(["Label", marker.label]);
  if (marker.components) {
    marker.components.forEach((item) => rows.push([item.name, formatSigned(item.score)]));
  }
  if (marker.details) {
    marker.details.forEach((item) => rows.push([item.name, `${item.value} (${item.theory})`]));
  }
  if (rows.length) {
    return rows;
  }
  if (marker.trade) {
    return [
      ["Entry", `${formatDateTime(marker.trade.entry_time)} @ ${formatPrice(Number(marker.trade.entry_price || 0))}`],
      ["Exit", `${formatDateTime(marker.trade.exit_time)} @ ${formatPrice(Number(marker.trade.exit_price || 0))}`],
      ["Return", `${formatSigned(marker.trade.return_pct)}%`],
      ["Exit reason", marker.trade.exit_reason || "-"],
    ];
  }
  return [
    ["Score", marker.score ?? "-"],
    ["Label", marker.label || marker.text || "-"],
  ];
}

function initBacktestPage() {
  if (backtestInitialized) return;
  backtestInitialized = true;
  populateLabControls();
  const node = template.content.firstElementChild.cloneNode(true);
  node.classList.add("lab-chart-pane");
  backtestChartHost.appendChild(node);
  backtestPane = createPane(node, 0);
  backtestPane.backtestButton.hidden = true;
  document.querySelector("#lab-run-backtest")?.addEventListener("click", runLabBacktest);
  ["#lab-source-select", "#lab-symbol-select", "#lab-timeframe-select", "#lab-preset-select"].forEach((selector) => {
    document.querySelector(selector)?.addEventListener("change", applyLabControlsToPane);
  });
}

function populateLabControls() {
  const sourceSelect = document.querySelector("#lab-source-select");
  const presetSelect = document.querySelector("#lab-preset-select");
  if (!hasElement(sourceSelect, presetSelect)) return;
  sourceSelect.innerHTML = Object.entries(config.sources)
    .map(([value, item]) => `<option value="${value}">${item.label}</option>`)
    .join("");
  sourceSelect.value = "bybit";
  presetSelect.innerHTML = (config.strategy_presets || [])
    .map((preset) => `<option value="${preset.id}">${preset.label}</option>`)
    .join("");
  presetSelect.value = config.default_strategy_preset;
  populateLabSymbolAndTimeframe();
  sourceSelect.addEventListener("change", populateLabSymbolAndTimeframe);
}

function populateLabSymbolAndTimeframe() {
  const source = document.querySelector("#lab-source-select")?.value || "bybit";
  const symbolSelect = document.querySelector("#lab-symbol-select");
  const timeframeSelect = document.querySelector("#lab-timeframe-select");
  if (!hasElement(symbolSelect, timeframeSelect)) return;
  const sourceConfig = config.sources[source];
  symbolSelect.innerHTML = sourceConfig.symbols.map((symbol) => `<option value="${symbol}">${symbol}</option>`).join("");
  timeframeSelect.innerHTML = sourceConfig.timeframes.map((timeframe) => `<option value="${timeframe}">${timeframe}</option>`).join("");
  symbolSelect.value = sourceConfig.symbols.includes("BTCUSDT") ? "BTCUSDT" : sourceConfig.symbols[0];
  timeframeSelect.value = sourceConfig.timeframes.includes("1h") ? "1h" : sourceConfig.timeframes[0];
}

function applyLabControlsToPane() {
  if (!backtestPane) return;
  backtestPane.sourceSelect.value = document.querySelector("#lab-source-select")?.value || "bybit";
  populateSymbolAndTimeframe(backtestPane, {
    symbol: document.querySelector("#lab-symbol-select")?.value,
    timeframe: document.querySelector("#lab-timeframe-select")?.value,
  });
  backtestPane.presetSelect.value = document.querySelector("#lab-preset-select")?.value || config.default_strategy_preset;
  return startPane(backtestPane);
}

function labBacktestSettings() {
  return {
    preset: document.querySelector("#lab-preset-select")?.value || config.default_strategy_preset,
    period: document.querySelector("#lab-period-input")?.value || "60d",
    limit: document.querySelector("#lab-limit-input")?.value || "auto",
    fee_pct: document.querySelector("#lab-fee-input")?.value || "0",
    slippage_pct: document.querySelector("#lab-slippage-input")?.value || "0",
    allowShorts: document.querySelector("#lab-allow-shorts")?.checked ? "true" : "false",
  };
}

async function runLabBacktest() {
  if (!backtestPane) return;
  const button = document.querySelector("#lab-run-backtest");
  button.disabled = true;
  backtestResults.innerHTML = `<p class="pane-status">Running strategy test...</p>`;
  const settings = labBacktestSettings();
  try {
    await applyLabControlsToPane();
    const params = new URLSearchParams({
      source: document.querySelector("#lab-source-select")?.value || "bybit",
      symbol: document.querySelector("#lab-symbol-select")?.value || "BTCUSDT",
      timeframe: document.querySelector("#lab-timeframe-select")?.value || "1h",
      period: settings.period,
      preset: settings.preset,
      limit: settings.limit,
      fee_pct: settings.fee_pct,
      slippage_pct: settings.slippage_pct,
      allowShorts: settings.allowShorts,
      chart_candles_count: String(backtestPane.candles.length),
      first_chart_candle_time: String(backtestPane.candles[0]?.time || ""),
      last_chart_candle_time: String(backtestPane.candles[backtestPane.candles.length - 1]?.time || ""),
    });
    const payload = await apiGet(`/api/backtest?${params}`);
    backtestPane.backtestMarkers = markersFromBacktestPayload(payload);
    renderBacktestOverlays(backtestPane, payload);
    backtestPane.backtestDiagnostics = payload.diagnostics?.overlay_rendering || payload.overlayDiagnostics || {};
    updateChartMarkers(backtestPane);
    renderDataDiagnostics(backtestPane);
    backtestResults.innerHTML = renderBacktestResults(payload);
  } catch (error) {
    backtestResults.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

async function runBacktest(pane, presetId) {
  // Backtest trades, markers, overlays, metrics, and diagnostics are produced
  // by /api/backtest. The UI renders the payload without duplicating strategy
  // or metric formulas.
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
    const payload = await apiGet(`/api/backtest?${params}`);

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

async function runBacktestDiagnosis(button) {
  const target = button.closest(".zero-trade-panel")?.querySelector(".zero-trade-diagnostics");
  if (target) target.innerHTML = `<p class="pane-status">Running backend diagnosis...</p>`;
  button.disabled = true;
  try {
    const payload = await apiGet(`/api/backtest/diagnose?${button.dataset.diagnoseBacktest || ""}`);
    if (target) target.innerHTML = renderTradeGenerationDiagnostics(payload.tradeGenerationDiagnostics || payload);
  } catch (error) {
    if (target) target.innerHTML = `<p class="pane-status">Diagnosis failed: ${escapeHtml(error.message)}</p>`;
  } finally {
    button.disabled = false;
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
        reason: "Backtest entry returned by the backend strategy engine.",
        trade,
      });
    }
    if (exitTime !== null) {
      markers.push({
        time: exitTime,
        position: "aboveBar",
        color: "#ff5c7a",
        shape: "arrowDown",
        text: "BT SELL",
        reason: `Backtest exit: ${trade.exit_reason || "strategy exit"}.`,
        trade,
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
    try {
      rows.push(await apiGet(`/api/backtest?${params}`));
    } catch (error) {
      rows.push({ preset: preset.label || preset.id, total_return_pct: 0, number_of_trades: 0, win_rate: 0, max_drawdown: 0, profit_factor: 0, average_bars_held: 0, error: error.message });
    }
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

function openBacktestModal(html, title = "Backtest", className = "") {
  if (backtestTitle) backtestTitle.textContent = title;
  backtestContent.innerHTML = html;
  backtestModal.classList.toggle("signal-modal", className === "signal-modal");
  backtestModal.hidden = false;
}

async function openBacktestHistory() {
  if (backtestTitle) backtestTitle.textContent = "Backtest History";
  backtestModal.classList.remove("signal-modal");
  backtestModal.hidden = false;
  backtestContent.innerHTML = `<p class="pane-status">Loading backtest history...</p>`;
  try {
    const payload = await apiGet("/api/backtest-history?limit=150");
    backtestContent.innerHTML = renderBacktestHistory(payload);
  } catch (error) {
    backtestContent.innerHTML = `
      <p class="pane-status">Backend history could not load: ${escapeHtml(error.message)}</p>
      <p class="modal-note">History is stored by the Flask backend after successful /api/backtest runs.</p>
    `;
  }
}

function renderBacktestHistory(payload) {
  const summaries = payload.strategySummary || [];
  const runs = payload.runs || [];
  const summaryRows = summaries.map((row) => `
    <tr>
      <td>${escapeHtml(row.strategy)}</td>
      <td>${row.tests}</td>
      <td>${row.totalTrades}</td>
      <td class="${row.sumReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.sumReturnPct)}%</td>
      <td class="${row.avgReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.avgReturnPct)}%</td>
      <td>${row.avgWinRate}%</td>
      <td>${row.avgProfitFactor}</td>
      <td>${row.worstDrawdown}%</td>
    </tr>
  `).join("");
  const runRows = runs.map((run) => `
    <tr>
      <td>${escapeHtml(new Date(run.createdAt).toLocaleString())}</td>
      <td>${escapeHtml(run.strategy)}</td>
      <td>${escapeHtml(run.symbol)} ${escapeHtml(run.timeframe)}</td>
      <td>${escapeHtml(run.period || "-")}</td>
      <td class="${run.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(run.totalReturnPct)}%</td>
      <td>${run.trades}</td>
      <td>${run.winRate}%</td>
      <td>${run.profitFactor}</td>
      <td>${run.maxDrawdown}%</td>
    </tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Strategy Totals</h3>
    <p class="modal-note">Stored locally from completed <code>/api/backtest</code> runs. Summary values are backend-calculated.</p>
    <table class="trade-table">
      <thead><tr><th>Strategy</th><th>Tests</th><th>Trades</th><th>Sum return</th><th>Avg return</th><th>Avg win</th><th>Avg PF</th><th>Worst DD</th></tr></thead>
      <tbody>${summaryRows || `<tr><td colspan="8">No backtests recorded yet.</td></tr>`}</tbody>
    </table>
    <h3 class="modal-section-title">Recent Backtests</h3>
    <table class="trade-table">
      <thead><tr><th>Time</th><th>Strategy</th><th>Market</th><th>Period</th><th>Return</th><th>Trades</th><th>Win</th><th>PF</th><th>DD</th></tr></thead>
      <tbody>${runRows || `<tr><td colspan="9">Run a backtest to create history.</td></tr>`}</tbody>
    </table>
  `;
}

function openBottomPanelModal() {
  const html = bottomPanelContent?.innerHTML?.trim();
  if (!html) return;
  openBacktestModal(`<div class="signal-modal-content">${html}</div>`, "Indicators and Signal Details", "signal-modal");
}

function openIndicatorInfo(indicatorPane) {
  const legend = indicatorPane.legend || {};
  const signal = indicatorPane.signal || {};
  const lines = legend.lines || [];
  const zones = legend.zones || [];
  const signals = legend.signals || [];
  openBacktestModal(`
    <div class="indicator-info-modal">
      <div class="signal-detail-header compact">
        <div>
          <h3>${escapeHtml(indicatorPane.title || "Indicator")}</h3>
          <p>${escapeHtml(legend.summary || "Backend-calculated indicator. The UI renders returned values only.")}</p>
        </div>
        <div class="signal-badge ${signal.action === "SHORT" ? "sell" : signal.action === "LONG" ? "buy" : "neutral"}">${escapeHtml(signal.action || "INFO")}</div>
      </div>
      <div class="indicator-info-grid">
        <section>
          <h3 class="modal-section-title">Color / Line Legend</h3>
          <table class="trade-table compact-table">
            <tbody>${lines.map((line) => `
              <tr><td><span class="color-dot" style="background:${escapeHtml(line.color || "#9ca8b7")}"></span>${escapeHtml(line.name)}</td><td>${escapeHtml(line.meaning)}</td></tr>
            `).join("") || `<tr><td colspan="2">No legend returned.</td></tr>`}</tbody>
          </table>
        </section>
        <section>
          <h3 class="modal-section-title">Value Zones</h3>
          <table class="trade-table compact-table">
            <tbody>${zones.map((zone) => `
              <tr><td>${escapeHtml(zone.zone)}</td><td>${escapeHtml(zone.range ?? zone.value ?? "-")}</td><td>${escapeHtml(zone.meaning)}</td></tr>
            `).join("") || `<tr><td colspan="3">No zones returned.</td></tr>`}</tbody>
          </table>
        </section>
        <section>
          <h3 class="modal-section-title">Signals</h3>
          <table class="trade-table compact-table">
            <tbody>${signals.map((item) => `
              <tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.meaning)}</td></tr>
            `).join("") || `<tr><td colspan="2">No signal notes returned.</td></tr>`}</tbody>
          </table>
        </section>
        <section>
          <h3 class="modal-section-title">Current Values</h3>
          <table class="trade-table compact-table">
            <tbody>${Object.entries(signal.values || {}).map(([key, value]) => `
              <tr><td>${escapeHtml(key)}</td><td>${escapeHtml(value)}</td></tr>
            `).join("") || `<tr><td colspan="2">No current values returned.</td></tr>`}</tbody>
          </table>
        </section>
      </div>
    </div>
  `, "Indicator Details", "signal-modal");
}

function openSignalDetails(pane) {
  const payload = pane.lastSignalPayload;
  if (!payload) return;
  if (bottomPanelContent && !document.querySelector("#charts-page")?.hidden) {
    bottomPanelContent.innerHTML = renderSignalDetails(pane, payload);
  }
  openBacktestModal(renderSignalDetails(pane, payload), "Signal Details", "signal-modal");
}

function updateChartsPanels(pane, payload) {
  if (pane.index !== 0) return;
  if (activeSignalsList) {
    const direction = payload.signalDirection || (Number(payload.score) < -25 ? "SHORT" : Number(payload.score) > 25 ? "LONG" : "NEUTRAL");
    activeSignalsList.innerHTML = `
      <div class="sidebar-signal">
        <strong>${escapeHtml(pane.symbolSelect.value)} ${escapeHtml(pane.timeframeSelect.value)}</strong>
        <span class="${payload.tone || "neutral"}">${escapeHtml(direction)} ${escapeHtml(payload.label)} ${escapeHtml(String(payload.score))}</span>
      </div>
    `;
    refreshSidebarPaperStatus();
  }
  if (bottomPanelContent) {
    bottomPanelContent.innerHTML = renderSignalDetails(pane, payload);
  }
  renderWatchlist();
}

function renderSignalDetails(pane, payload) {
  const warnings = payload.warnings || [];
  const direction = payload.signalDirection || (Number(payload.score) < -25 ? "SHORT" : Number(payload.score) > 25 ? "LONG" : "NEUTRAL");
  return `
    <div class="signal-detail-header">
      <div>
        <h3>${escapeHtml(pane.symbolSelect.value)} ${escapeHtml(pane.timeframeSelect.value)}</h3>
        <p>Technical-analysis hint only. This is not financial advice and no trade is placed.</p>
      </div>
      <div class="signal-badge ${payload.tone || "neutral"}">${escapeHtml(direction)} ${escapeHtml(String(payload.score))}</div>
    </div>
    ${warnings.length ? `<ul class="backtest-warnings">${warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
    <section>
      <h3 class="modal-section-title">Score Components - All Timeframes</h3>
      ${renderTimeframeSignalMatrix(payload)}
    </section>
  `;
}

function renderTimeframeSignalMatrix(payload) {
  const matrix = payload.timeframeMatrix || [];
  if (!matrix.length) return `<p class="pane-status">No timeframe matrix returned.</p>`;
  const componentNames = Array.from(new Set(matrix.flatMap((item) => (item.components || []).map((component) => component.name))));
  const header = matrix.map((item) => `
    <th class="${item.selected ? "selected-timeframe" : ""}">
      ${escapeHtml(item.timeframe)}
      <br><span class="${item.tone || "neutral"}">${escapeHtml(item.label || "-")}</span>
    </th>
  `).join("");
  const directionRow = `
    <tr>
      <td>Signal</td>
      ${matrix.map((item) => `<td class="${item.selected ? "selected-timeframe" : ""}">${item.error ? "ERR" : escapeHtml(item.signalDirection || "NEUTRAL")}</td>`).join("")}
    </tr>
  `;
  const longRow = `
    <tr>
      <td>LONG signal %</td>
      ${matrix.map((item) => `<td class="${item.selected ? "selected-timeframe" : ""}">${item.error ? "ERR" : `${item.longSignalPct ?? item.buySuggestionPct ?? 0}%`}</td>`).join("")}
    </tr>
  `;
  const shortRow = `
    <tr>
      <td>SHORT signal %</td>
      ${matrix.map((item) => `<td class="${item.selected ? "selected-timeframe" : ""}">${item.error ? "ERR" : `${item.shortSignalPct ?? 0}%`}</td>`).join("")}
    </tr>
  `;
  const scoreRow = `
    <tr>
      <td>Total score</td>
      ${matrix.map((item) => `<td class="${item.selected ? "selected-timeframe" : ""} ${Number(item.score) >= 0 ? "positive" : "negative"}">${item.score ?? "-"}</td>`).join("")}
    </tr>
  `;
  const rows = componentNames.map((name) => `
    <tr>
      <td>${escapeHtml(name)}</td>
      ${matrix.map((item) => {
        const component = (item.components || []).find((entry) => entry.name === name);
        const value = component ? formatSigned(component.score) : "-";
        return `<td class="${item.selected ? "selected-timeframe" : ""} ${Number(component?.score || 0) >= 0 ? "positive" : "negative"}">${value}</td>`;
      }).join("")}
    </tr>
  `).join("");
  return `
    <p class="modal-note">Backend signal score by timeframe. LONG/SHORT percentages are technical hints only, not financial advice.</p>
    <table class="trade-table timeframe-score-table">
      <thead><tr><th>Metric</th>${header}</tr></thead>
      <tbody>${directionRow}${longRow}${shortRow}${scoreRow}${rows}</tbody>
    </table>
  `;
}

function closeBacktestModal() {
  backtestModal.hidden = true;
}

function renderBacktestResults(payload) {
  const diagnostics = normalizedBacktestDiagnostics(payload);
  const coverage = diagnostics.historicalCoverage || diagnostics.historical_coverage || {};
  const overlayDiagnostics = diagnostics.overlay_rendering || payload.overlayDiagnostics || {};
  const warningsList = [
    ...(diagnostics.warnings || []),
    ...coverageWarnings(payload, diagnostics, coverage)
  ];
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
  const diagnosticMetrics = [
    ["Preset", diagnostics.preset],
    ["Strategy", diagnostics.strategy],
    ["Source", diagnostics.source],
    ["Symbol", diagnostics.symbol],
    ["First candle", formatBacktestTime(diagnostics.firstCandleTime || diagnostics.first_candle_time || diagnostics.first_candle_date)],
    ["Last candle", formatBacktestTime(diagnostics.lastCandleTime || diagnostics.last_candle_time || diagnostics.last_candle_date)],
    ["Candles loaded", diagnostics.candlesLoaded],
    ["Timeframe", diagnostics.timeframe],
    ["Actual days", diagnostics.actualDays],
    ["Reliability", diagnostics.backtest_reliability],
    ["Warmup skipped", diagnostics.warmup_candles_skipped],
    ["Warmup %", `${diagnostics.warmup_pct ?? 0}%`],
    ["Average ATR %", diagnostics.average_atr_pct],
    ["Average volume", formatCompact(diagnostics.average_volume)],
    ["Trades/day", diagnostics.trades_per_day],
    ["Fee/side", `${formatNumber(diagnostics.feePct ?? diagnostics.fee_pct_per_side ?? payload.fee_pct ?? 0, 4)}%`],
    ["Slippage/side", `${formatNumber(diagnostics.slippagePct ?? diagnostics.slippage_pct_per_side ?? payload.slippage_pct ?? 0, 4)}%`],
    ["Raw score", diagnostics.raw_latest_score],
    ["Smoothed score", diagnostics.smoothed_latest_score],
  ];
  const coverageMetrics = [
    ["Requested period", coverage.requested_period || diagnostics.period || payload.period],
    ["Requested limit", coverage.requested_limit],
    ["Required candles", coverage.period_required_candles],
    ["Effective limit", coverage.effective_limit],
    ["Provider cap", coverage.provider_max_candles],
    ["Returned candles", coverage.returned_candles ?? diagnostics.actual_returned_candles ?? diagnostics.candlesLoaded],
    ["Coverage days", coverage.approximate_days_returned !== undefined ? `~${coverage.approximate_days_returned}d` : undefined],
    ["First candle", formatBacktestTime(coverage.first_candle_time || diagnostics.firstCandleTime)],
    ["Last candle", formatBacktestTime(coverage.last_candle_time || diagnostics.lastCandleTime)],
    ["Full period", coverage.full_period_covered === undefined ? "-" : (coverage.full_period_covered ? "yes" : "no")],
  ];
  const overlayMetrics = [
    ["Chart candles", overlayDiagnostics.chartCandlesCount],
    ["Backtest candles", overlayDiagnostics.backtestCandlesCount ?? diagnostics.candlesLoaded],
    ["First chart candle", formatBacktestTime(overlayDiagnostics.firstChartCandleTime)],
    ["Last chart candle", formatBacktestTime(overlayDiagnostics.lastChartCandleTime)],
    ["First backtest candle", formatBacktestTime(diagnostics.firstCandleTime)],
    ["Last backtest candle", formatBacktestTime(diagnostics.lastCandleTime)],
    ["First overlay", formatBacktestTime(overlayDiagnostics.firstOverlayTime)],
    ["Last overlay", formatBacktestTime(overlayDiagnostics.lastOverlayTime)],
    ["Dropped bars", overlayDiagnostics.droppedBarsReason],
  ];
  const skipped = diagnostics.skipped_trade_reasons || {};
  const skippedRows = Object.entries(skipped).sort((a, b) => b[1] - a[1]).map(([reason, count]) => `
    <tr><td>${formatReason(reason)}</td><td>${count}</td></tr>
  `).join("");
  const warnings = Array.from(new Set(warningsList.filter(Boolean))).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const trades = normalizedTrades(payload);
  const zeroTradePanel = trades.length === 0 ? renderZeroTradePanel(payload) : "";
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
      ${diagnosticMetrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${displayValue(value)}</strong></div>`).join("")}
    </div>
    <h3 class="modal-section-title">History Coverage</h3>
    <div class="metric-grid diagnostics-grid">
      ${coverageMetrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${displayValue(value)}</strong></div>`).join("")}
    </div>
    <h3 class="modal-section-title">Data Diagnostics</h3>
    <div class="metric-grid diagnostics-grid">
      ${overlayMetrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${displayValue(value)}</strong></div>`).join("")}
    </div>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
    <h3 class="modal-section-title">Skipped Entry Reasons</h3>
    <table class="trade-table skipped-table">
      <tbody>${skippedRows || `<tr><td>No skipped score>=70 candles.</td><td>0</td></tr>`}</tbody>
    </table>
    ${zeroTradePanel}
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

function renderZeroTradePanel(payload) {
  const diagnostics = normalizedBacktestDiagnostics(payload);
  const tradeDiag = payload.tradeGenerationDiagnostics;
  const params = new URLSearchParams({
    source: payload.source || diagnostics.source || "bybit",
    symbol: payload.symbol || diagnostics.symbol || "BTCUSDT",
    timeframe: payload.timeframe || diagnostics.timeframe || "1h",
    period: payload.period || diagnostics.period || "365d",
    preset: payload.preset_id || diagnostics.preset || payload.preset || "conservative_trend",
    limit: diagnostics.requestedLimitRaw || diagnostics.requestedLimit || "auto",
    fee_pct: payload.fee_pct ?? diagnostics.feePct ?? "0",
    slippage_pct: payload.slippage_pct ?? diagnostics.slippagePct ?? "0",
    allowShorts: diagnostics.allowShorts || "false",
    debug: "true",
  });
  return `
    <section class="zero-trade-panel">
      <h3 class="modal-section-title">Why no trades?</h3>
      <p class="modal-note">Backend-owned trade-generation diagnostics. Strategy formulas are not calculated in the browser.</p>
      ${tradeDiag ? renderTradeGenerationDiagnostics(tradeDiag) : `<p class="pane-status">No detailed diagnosis is attached yet.</p>`}
      <button type="button" class="small-action-button" data-diagnose-backtest="${escapeHtml(params.toString())}">Diagnose Backtest</button>
      <div class="zero-trade-diagnostics"></div>
    </section>
  `;
}

function renderTradeGenerationDiagnostics(payload) {
  const summary = payload.summary || {};
  const diagnostics = payload.diagnostics || {};
  const counters = payload.reasonCounters || {};
  const strategy = payload.strategy || {};
  const counterRows = Object.entries(counters)
    .sort((a, b) => Number(b[1]) - Number(a[1]))
    .map(([reason, count]) => `<tr><td>${escapeHtml(formatReason(reason))}</td><td>${count}</td></tr>`)
    .join("");
  const actions = (payload.suggestedActions || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  return `
    <div class="metric-grid diagnostics-grid">
      <div class="metric"><span>Likely reason</span><strong>${escapeHtml(summary.likelyReason || "-")}</strong></div>
      <div class="metric"><span>Confidence</span><strong>${escapeHtml(summary.confidence || "LOW")}</strong></div>
      <div class="metric"><span>Candles</span><strong>${displayValue(diagnostics.candlesLoaded)}</strong></div>
      <div class="metric"><span>Usable candles</span><strong>${displayValue(diagnostics.usableCandlesAfterWarmup)}</strong></div>
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(strategy.name || "-")}</strong></div>
      <div class="metric"><span>Primary blocker</span><strong>${escapeHtml(diagnostics.primaryBlocker || "-")}</strong></div>
    </div>
    <table class="trade-table skipped-table">
      <thead><tr><th>Reason counter</th><th>Count</th></tr></thead>
      <tbody>${counterRows || `<tr><td>No counters returned.</td><td>0</td></tr>`}</tbody>
    </table>
    ${actions ? `<h3 class="modal-section-title">Suggested Actions</h3><ul class="backtest-warnings">${actions}</ul>` : ""}
  `;
}

function renderPresetComparison(results) {
  if (!results.length) return "<p>No preset results returned.</p>";
  const rows = results.map((item) => `
    <tr>
      <td>${escapeHtml(item.preset)}</td>
      <td class="${item.total_return_pct >= 0 ? "positive" : "negative"}">${formatSigned(item.total_return_pct)}%</td>
      <td>${item.number_of_trades}</td>
      <td>${item.win_rate}%</td>
      <td>${item.max_drawdown}%</td>
      <td>${item.profit_factor}</td>
      <td>${item.average_bars_held}</td>
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

function normalizedBacktestDiagnostics(payload) {
  const diagnostics = { ...(payload.diagnostics || {}) };
  const coverage = diagnostics.historicalCoverage || diagnostics.historical_coverage || payload.historicalCoverage || payload.historical_coverage || {};
  const candlesLoaded = diagnostics.candlesLoaded
    ?? diagnostics.number_of_candles_loaded
    ?? payload.candlesLoaded
    ?? coverage.returned_candles
    ?? diagnostics.actual_returned_candles;
  const firstCandleTime = diagnostics.firstCandleTime
    ?? diagnostics.first_candle_time
    ?? payload.firstCandleTime
    ?? coverage.first_candle_time;
  const lastCandleTime = diagnostics.lastCandleTime
    ?? diagnostics.last_candle_time
    ?? payload.lastCandleTime
    ?? coverage.last_candle_time;
  const actualDays = diagnostics.actualDays
    ?? diagnostics.actual_days_returned
    ?? payload.actualDays
    ?? coverage.approximate_days_returned;
  return {
    ...diagnostics,
    source: diagnostics.source || payload.source || "N/A",
    symbol: diagnostics.symbol || payload.symbol || "N/A",
    timeframe: diagnostics.timeframe || diagnostics.interval || payload.timeframe || "N/A",
    period: diagnostics.period || payload.period || "N/A",
    candlesLoaded,
    number_of_candles_loaded: candlesLoaded,
    firstCandleTime,
    lastCandleTime,
    actualDays,
    feePct: diagnostics.feePct ?? diagnostics.fee_pct_per_side ?? payload.fee_pct ?? 0,
    slippagePct: diagnostics.slippagePct ?? diagnostics.slippage_pct_per_side ?? payload.slippage_pct ?? 0,
    preset: diagnostics.preset || payload.preset || payload.preset_id || "N/A",
    strategy: diagnostics.strategy || payload.strategy || payload.preset || "N/A",
    historicalCoverage: coverage,
    historical_coverage: coverage
  };
}

function coverageWarnings(payload, diagnostics, coverage) {
  const warnings = [];
  const selectedDays = parsePeriodDays(diagnostics.period || payload.period);
  const actualDays = Number(diagnostics.actualDays ?? coverage.approximate_days_returned);
  if (selectedDays && Number.isFinite(actualDays) && actualDays > 0) {
    if (actualDays > selectedDays * 1.25 || actualDays < selectedDays * 0.75) {
      warnings.push(`Selected period is ${diagnostics.period || payload.period}, but returned history spans about ${actualDays} days.`);
    }
  }
  if (coverage.period_capped) warnings.push("Selected history was capped by the provider/cache candle limit.");
  (coverage.warnings || []).forEach((warning) => warnings.push(warning));
  return warnings;
}

function parsePeriodDays(period) {
  const text = String(period || "").trim().toLowerCase();
  if (!text || text === "max" || text === "all") return null;
  const number = Number.parseFloat(text);
  if (!Number.isFinite(number)) return null;
  if (text.endsWith("w")) return number * 7;
  if (text.endsWith("mo")) return number * 30;
  if (text.endsWith("m")) return number * 30;
  if (text.endsWith("y")) return number * 365;
  return number;
}

function displayValue(value) {
  if (value === undefined || value === null || value === "") return "N/A";
  if (Number.isNaN(value)) return "N/A";
  return escapeHtml(value);
}

function renderAnalysisPage() {
  if (!analysisInitialized) {
    setupAnalysisControls();
    analysisInitialized = true;
  }
  loadResearchRuns();
}

function renderLearningPage() {
  if (!learningInitialized) {
    setupLearningControls();
    learningInitialized = true;
  }
  loadLearningConfig();
  loadLearningAuditSummary();
  loadLearningAudit();
  loadLearningDecisions();
  loadLearningReports();
}

function renderOpsPage() {
  if (!opsInitialized) {
    setupOpsControls();
    opsInitialized = true;
  }
  loadSystemHealth(true);
  loadMarketCacheStatus();
  loadResearchDataReadiness();
}

function setupOpsControls() {
  document.querySelector("#ops-refresh-full")?.addEventListener("click", () => loadSystemHealth(false));
  document.querySelector("#ops-refresh-quick")?.addEventListener("click", () => loadSystemHealth(true));
  document.querySelector("#cache-validate-symbols")?.addEventListener("click", validateBybitSymbols);
  document.querySelector("#cache-refresh-status")?.addEventListener("click", loadMarketCacheStatus);
  document.querySelector("#cache-prefetch-selected")?.addEventListener("click", prefetchMarketCache);
  document.querySelector("#readiness-check")?.addEventListener("click", loadResearchDataReadiness);
}

async function loadSystemHealth(quick = true) {
  const status = document.querySelector("#ops-status");
  const summary = document.querySelector("#ops-summary");
  const body = document.querySelector("#ops-checks-body");
  const details = document.querySelector("#ops-details");
  if (!status || !summary || !body || !details) return;
  status.textContent = quick ? "Running quick backend health check..." : "Running full backend health check...";
  summary.innerHTML = "";
  body.innerHTML = `<tr><td colspan="4">Loading diagnostics...</td></tr>`;
  details.innerHTML = "";
  try {
    const payload = await apiGet(quick ? "/api/system/health/quick" : "/api/system/health");
    renderSystemHealth(payload, quick);
  } catch (error) {
    status.textContent = `System health check failed: ${error.message}`;
    body.innerHTML = `<tr><td colspan="4">Health check failed: ${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderSystemHealth(payload, quick) {
  const status = document.querySelector("#ops-status");
  const summary = document.querySelector("#ops-summary");
  const body = document.querySelector("#ops-checks-body");
  const details = document.querySelector("#ops-details");
  if (!status || !summary || !body || !details) return;
  const counts = payload.summary || {};
  status.textContent = `${quick ? "Quick" : "Full"} health check ${payload.ok ? "passed" : "needs attention"} at ${formatLearningTime(payload.generatedAt)}.`;
  summary.innerHTML = `
    <div class="metric"><span>Overall</span><strong class="${payload.ok ? "positive" : "negative"}">${payload.ok ? "OK" : "Attention"}</strong></div>
    <div class="metric"><span>PASS</span><strong class="positive">${counts.pass || 0}</strong></div>
    <div class="metric"><span>WARN</span><strong class="neutral">${counts.warn || 0}</strong></div>
    <div class="metric"><span>FAIL</span><strong class="negative">${counts.fail || 0}</strong></div>
  `;
  const checks = payload.checks || [];
  body.innerHTML = checks.map((check) => `
    <tr>
      <td class="${healthTone(check.status)}">${escapeHtml(check.status || "-")}</td>
      <td>${escapeHtml(check.label || check.id || "-")}</td>
      <td>${escapeHtml(check.message || "")}</td>
      <td><code>${escapeHtml(compactJson(check.details || {}))}</code></td>
    </tr>
  `).join("") || `<tr><td colspan="4">No checks returned.</td></tr>`;
  details.innerHTML = renderOpsDetails(checks);
}

function cacheParams() {
  return {
    symbols: document.querySelector("#cache-symbols")?.value || "BTCUSDT,ETHUSDT,SOLUSDT",
    timeframes: document.querySelector("#cache-timeframes")?.value || "15m,1h,4h",
    period: document.querySelector("#cache-period")?.value || "max",
    limit: document.querySelector("#cache-limit")?.value || "50000",
  };
}

function readinessParams() {
  return {
    symbols: document.querySelector("#readiness-symbols")?.value || "BTCUSDT,ETHUSDT,SOLUSDT",
    timeframes: document.querySelector("#readiness-timeframes")?.value || "15m,1h,4h",
    period: document.querySelector("#readiness-period")?.value || "365d",
    limit: document.querySelector("#readiness-limit")?.value || "auto",
  };
}

async function validateBybitSymbols() {
  const host = document.querySelector("#cache-symbol-validation");
  const status = document.querySelector("#cache-status");
  const params = cacheParams();
  if (status) status.textContent = "Validating Bybit symbols...";
  try {
    const payload = await apiGet(`/api/market/bybit/symbols?${new URLSearchParams({ symbols: params.symbols })}`);
    if (host) host.innerHTML = renderBybitValidation(payload);
    if (status) status.textContent = `Validation complete. Invalid: ${(payload.invalidSymbols || []).length}.`;
  } catch (error) {
    if (status) status.textContent = `Symbol validation failed: ${error.message}`;
  }
}

async function loadMarketCacheStatus() {
  const status = document.querySelector("#cache-status");
  const summary = document.querySelector("#cache-summary");
  const body = document.querySelector("#cache-rows-body");
  const params = cacheParams();
  if (!summary || !body) return;
  if (status) status.textContent = "Loading market cache status...";
  try {
    const payload = await apiGet(`/api/market/cache/status?${new URLSearchParams({ source: "bybit", symbols: params.symbols, timeframes: params.timeframes })}`);
    renderMarketCacheStatus(payload);
    if (status) status.textContent = "Market cache status loaded.";
  } catch (error) {
    if (status) status.textContent = `Cache status failed: ${error.message}`;
    body.innerHTML = `<tr><td colspan="8">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function prefetchMarketCache() {
  const status = document.querySelector("#cache-status");
  const params = cacheParams();
  if (status) status.textContent = "Prefetching selected history. This can take a while...";
  try {
    const payload = await apiPost("/api/market/cache/prefetch", {
      source: "bybit",
      symbols: csvStringValues(params.symbols),
      timeframes: csvStringValues(params.timeframes),
      period: params.period,
      limit: Number(params.limit || 50000),
      force: false,
    });
    renderPrefetchResult(payload);
    await loadMarketCacheStatus();
    if (status) status.textContent = `Prefetch complete. OK: ${payload.summary?.ok || 0}, errors: ${payload.summary?.errors || 0}.`;
  } catch (error) {
    if (status) status.textContent = `Prefetch failed: ${error.message}`;
  }
}

function csvStringValues(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function renderBybitValidation(payload) {
  const aliases = payload.suggestedAliases || {};
  return `
    <h3 class="modal-section-title">Bybit Symbol Validation</h3>
    <div class="metric-grid">
      <div class="metric"><span>Configured</span><strong>${(payload.configuredSymbols || []).length}</strong></div>
      <div class="metric"><span>Valid</span><strong class="positive">${(payload.validSymbols || []).length}</strong></div>
      <div class="metric"><span>Invalid</span><strong class="${(payload.invalidSymbols || []).length ? "negative" : "positive"}">${(payload.invalidSymbols || []).length}</strong></div>
      <div class="metric"><span>Aliases</span><strong>${Object.keys(aliases).length}</strong></div>
    </div>
    <table class="trade-table">
      <tbody>
        <tr><th>Invalid</th><td>${escapeHtml((payload.invalidSymbols || []).join(", ") || "-")}</td></tr>
        <tr><th>Aliases</th><td>${escapeHtml(Object.entries(aliases).map(([from, to]) => `${from} -> ${to}`).join(", ") || "-")}</td></tr>
      </tbody>
    </table>
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

function renderMarketCacheStatus(payload) {
  const summary = document.querySelector("#cache-summary");
  const body = document.querySelector("#cache-rows-body");
  if (!summary || !body) return;
  const item = payload.summary || {};
  summary.innerHTML = `
    <div class="metric"><span>Symbols</span><strong>${item.symbols || 0}</strong></div>
    <div class="metric"><span>Timeframes</span><strong>${item.timeframes || 0}</strong></div>
    <div class="metric"><span>Cached candles</span><strong>${formatCompact(item.totalCachedCandles || 0)}</strong></div>
    <div class="metric"><span>Missing</span><strong>${item.missingPairs || 0}</strong></div>
    <div class="metric"><span>Partial</span><strong>${item.partialPairs || 0}</strong></div>
    <div class="metric"><span>Stale</span><strong>${item.stalePairs || 0}</strong></div>
  `;
  body.innerHTML = (payload.rows || []).map((row) => `
    <tr>
      <td>${escapeHtml(row.symbol || "-")}</td>
      <td>${escapeHtml(row.timeframe || "-")}</td>
      <td class="${row.status === "OK" ? "positive" : row.status === "MISSING" ? "negative" : "neutral"}">${escapeHtml(row.status || "-")}</td>
      <td>${row.cachedCandles || 0}</td>
      <td>${formatNumber(row.approximateDays)}</td>
      <td>${formatDateTime(row.firstCandleTime)}</td>
      <td>${formatDateTime(row.lastCandleTime)}</td>
      <td>${escapeHtml((row.warnings || []).join("; "))}</td>
    </tr>
  `).join("") || `<tr><td colspan="8">No cache rows returned.</td></tr>`;
}

async function loadResearchDataReadiness() {
  const status = document.querySelector("#readiness-status");
  const summary = document.querySelector("#readiness-summary");
  const body = document.querySelector("#readiness-rows-body");
  const params = readinessParams();
  if (!summary || !body) return;
  if (status) status.textContent = "Checking research data readiness...";
  try {
    const payload = await apiGet(`/api/research/data-readiness?${new URLSearchParams({ source: "bybit", ...params })}`);
    renderResearchDataReadiness(payload);
    if (status) status.textContent = "Research data readiness loaded.";
  } catch (error) {
    if (status) status.textContent = `Readiness check failed: ${error.message}`;
    body.innerHTML = `<tr><td colspan="10">${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderResearchDataReadiness(payload) {
  const summary = document.querySelector("#readiness-summary");
  const body = document.querySelector("#readiness-rows-body");
  if (!summary || !body) return;
  const item = payload.summary || {};
  summary.innerHTML = `
    <div class="metric"><span>Ready</span><strong class="positive">${item.readyPairs || 0}</strong></div>
    <div class="metric"><span>Partial</span><strong class="neutral">${item.partialPairs || 0}</strong></div>
    <div class="metric"><span>Missing</span><strong class="${item.missingPairs ? "negative" : "positive"}">${item.missingPairs || 0}</strong></div>
    <div class="metric"><span>Stale</span><strong class="neutral">${item.stalePairs || 0}</strong></div>
    <div class="metric"><span>Capped</span><strong class="neutral">${item.cappedPairs || 0}</strong></div>
    <div class="metric"><span>Errors</span><strong class="${item.errorPairs ? "negative" : "positive"}">${item.errorPairs || 0}</strong></div>
  `;
  body.innerHTML = (payload.rows || []).map((row) => `
    <tr>
      <td>${escapeHtml(row.symbol || "-")}</td>
      <td>${escapeHtml(row.timeframe || "-")}</td>
      <td class="${readinessTone(row.status)}">${escapeHtml(row.status || "-")}</td>
      <td>${row.cachedCandles || 0}</td>
      <td>${row.requiredCandles ?? "N/A"}</td>
      <td>${formatNumber(row.approximateDays || 0)}</td>
      <td>${formatDateTime(row.firstCandleTime)}</td>
      <td>${formatDateTime(row.lastCandleTime)}</td>
      <td>${escapeHtml((row.warnings || []).join("; "))}</td>
      <td>${escapeHtml(row.recommendedAction || "-")}</td>
    </tr>
  `).join("") || `<tr><td colspan="10">No readiness rows returned.</td></tr>`;
}

function readinessTone(status) {
  if (status === "READY") return "positive";
  if (status === "MISSING" || status === "ERROR") return "negative";
  return "neutral";
}

function renderPrefetchResult(payload) {
  const host = document.querySelector("#cache-symbol-validation");
  if (!host) return;
  const rows = (payload.results || []).slice(0, 20).map((item) => `
    <tr>
      <td>${escapeHtml(item.symbol || "-")}</td>
      <td>${escapeHtml(item.timeframe || "-")}</td>
      <td class="${item.status === "OK" ? "positive" : "negative"}">${escapeHtml(item.status || "-")}</td>
      <td>${item.candles || 0}</td>
      <td>${escapeHtml(item.error || (item.warnings || []).join("; ") || "-")}</td>
    </tr>
  `).join("");
  host.innerHTML = `
    <h3 class="modal-section-title">Prefetch Result</h3>
    <div class="metric-grid">
      <div class="metric"><span>Pairs</span><strong>${payload.summary?.pairsRequested || 0}</strong></div>
      <div class="metric"><span>OK</span><strong class="positive">${payload.summary?.ok || 0}</strong></div>
      <div class="metric"><span>Errors</span><strong class="${payload.summary?.errors ? "negative" : "positive"}">${payload.summary?.errors || 0}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Symbol</th><th>Timeframe</th><th>Status</th><th>Candles</th><th>Message</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="5">No prefetch rows returned.</td></tr>`}</tbody>
    </table>
  `;
}

function healthTone(status) {
  if (status === "PASS") return "positive";
  if (status === "FAIL") return "negative";
  return "neutral";
}

function compactJson(value) {
  const json = JSON.stringify(value);
  return json.length > 260 ? `${json.slice(0, 260)}...` : json;
}

function findHealthCheck(checks, id) {
  return checks.find((check) => check.id === id) || {};
}

function renderOpsDetails(checks) {
  const generated = findHealthCheck(checks, "generated_data_sizes").details || {};
  const learning = findHealthCheck(checks, "learning_runner_config").details || {};
  const candidate = findHealthCheck(checks, "paper_candidate_summary").details || {};
  const auto = findHealthCheck(checks, "auto_promotion").details || {};
  const firstActive = (candidate.activeSymbols || [])[0] || {};
  const fileRows = Object.entries(generated).map(([name, info]) => `
    <tr><th>${escapeHtml(name)}</th><td>${info.exists ? `${formatNumber(info.sizeKb)} KB` : "missing"}</td><td>${escapeHtml(info.updatedAt || "-")}</td></tr>
  `).join("");
  return `
    <div class="metric-grid">
      <div class="metric"><span>Learning enabled</span><strong>${escapeHtml(String(learning.enabled ?? false))}</strong></div>
      <div class="metric"><span>Next run</span><strong>${escapeHtml(formatLearningTime(learning.nextRunAt))}</strong></div>
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(firstActive.symbol || "")} ${escapeHtml(firstActive.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Auto-promote</span><strong>${escapeHtml(String(auto.autoPromote ?? false))}</strong></div>
    </div>
    <h3 class="modal-section-title">Generated Data Sizes</h3>
    <table class="trade-table">
      <thead><tr><th>File</th><th>Size</th><th>Updated</th></tr></thead>
      <tbody>${fileRows || `<tr><td colspan="3">No generated files reported.</td></tr>`}</tbody>
    </table>
    <p class="modal-note">Diagnostics are read-only. Secrets are not read or displayed, and no paper or real trading action is performed.</p>
  `;
}

function setupLearningControls() {
  document.querySelector("#learning-run-button")?.addEventListener("click", runLearningCycle);
  document.querySelector("#learning-save-config")?.addEventListener("click", saveLearningConfig);
  document.querySelector("#learning-tick-button")?.addEventListener("click", runLearningTick);
  document.querySelector("#learning-audit-summary-button")?.addEventListener("click", loadLearningAuditSummary);
  document.querySelector("#candidate-review-refresh")?.addEventListener("click", loadCandidateReview);
  document.querySelector("#candidate-review-preview")?.addEventListener("click", previewCandidatePromotion);
  document.querySelector("#research-candidate-review-report-refresh")?.addEventListener("click", loadResearchCandidateReviewReport);
  document.querySelector("#candidate-stability-refresh")?.addEventListener("click", loadCandidateStability);
  document.querySelector("#paper-readiness-refresh")?.addEventListener("click", loadPaperReadiness);
  document.querySelector("#paper-observation-report-refresh")?.addEventListener("click", loadPaperObservationReport);
  document.querySelector("#paper-signal-diagnostics-refresh")?.addEventListener("click", loadPaperSignalDiagnostics);
  document.querySelector("#research-blocker-analytics-refresh")?.addEventListener("click", loadResearchBlockerAnalytics);
  document.querySelector("#paper-candidate-comparison-refresh")?.addEventListener("click", loadPaperCandidateComparison);
  document.querySelector("#paper-fast-discovery-refresh")?.addEventListener("click", loadPaperFastDiscovery);
  document.querySelector("#research-candidate-leaderboard-refresh")?.addEventListener("click", loadResearchCandidateLeaderboard);
  document.querySelector("#research-fee-slippage-stress-refresh")?.addEventListener("click", loadResearchFeeSlippageStress);
  document.querySelector("#research-walk-forward-review-refresh")?.addEventListener("click", loadResearchWalkForwardReview);
  document.querySelector("#research-activity-lab-refresh")?.addEventListener("click", loadResearchActivityLab);
  document.querySelector("#research-parameter-robustness-refresh")?.addEventListener("click", loadResearchParameterRobustness);
  document.querySelector("#research-strategy-variant-lab-refresh")?.addEventListener("click", loadResearchStrategyVariantLab);
  document.querySelector("#paper-control-refresh")?.addEventListener("click", loadPaperSimulationControl);
  document.querySelector("#paper-enable-preview")?.addEventListener("click", previewPaperEnable);
  document.querySelector("#paper-enable-run")?.addEventListener("click", enablePaperSimulation);
  document.querySelector("#paper-disable-run")?.addEventListener("click", disablePaperSimulation);
  document.querySelector("#paper-runtime-refresh")?.addEventListener("click", loadPaperRuntimeMonitor);
  document.querySelector("#paper-tick-readiness-refresh")?.addEventListener("click", loadPaperTickReadiness);
  document.querySelector("#paper-tick-readiness-panel")?.addEventListener("click", handlePaperTickReadinessAction);
  document.querySelector("#paper-active-observation-refresh")?.addEventListener("click", loadActivePaperObservation);
  document.querySelector("#paper-session-refresh")?.addEventListener("click", loadPaperSessionMonitor);
  document.querySelector("#paper-session-events-refresh")?.addEventListener("click", loadPaperSessionEventsSummary);
  document.querySelector("#paper-session-events-detail-refresh")?.addEventListener("click", loadPaperSessionEventsDetail);
  document.querySelector("#paper-session-trades-refresh")?.addEventListener("click", loadPaperSessionTrades);
  document.querySelector("#paper-observation-counters-refresh")?.addEventListener("click", loadPaperObservationCounters);
  document.querySelector("#paper-observation-targets-refresh")?.addEventListener("click", loadPaperObservationTargets);
  document.querySelector("#paper-runner-instructions-refresh")?.addEventListener("click", loadPaperRunnerInstructions);
  document.querySelector("#paper-runner-summary-refresh")?.addEventListener("click", loadPaperRunnerSummary);
  document.querySelector("#paper-observation-refresh")?.addEventListener("click", loadPaperObservationQuality);
  document.querySelector("#learning-evidence-refresh")?.addEventListener("click", loadLearningEvidence);
  document.querySelector("#learning-audit-button")?.addEventListener("click", loadLearningAudit);
  document.querySelector("#learning-auto-status-button")?.addEventListener("click", loadAutoPromoteStatus);
  document.querySelector("#learning-auto-run-button")?.addEventListener("click", runAutoPromote);
  document.querySelector("#learning-decisions-refresh")?.addEventListener("click", loadLearningDecisions);
  document.querySelector("#learning-recommendation")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-promote-learning]");
    if (button && lastLearningReport?.recommendation?.candidate) {
      promoteResearchCandidate(lastLearningReport.recommendation.candidate, lastLearningReport, "#learning-recommendation");
    }
  });
  document.querySelector("#learning-reports-body")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-load-learning-report]");
    if (button) loadLearningReport(button.dataset.loadLearningReport);
  });
}

async function loadLearningConfig() {
  const status = document.querySelector("#learning-status");
  try {
    const config = await apiGet("/api/learning/config");
    const scheduleStatus = await apiGet("/api/learning/status");
    document.querySelector("#learning-symbols").value = (config.symbols || []).join(",");
    document.querySelector("#learning-timeframes").value = (config.timeframes || []).join(",");
    document.querySelector("#learning-period").value = config.period || "365d";
    document.querySelector("#learning-min-trades").value = config.minTrades ?? 20;
    document.querySelector("#learning-max-ranking").value = config.maxRankingRuns ?? 20;
    document.querySelector("#learning-max-opt").value = config.maxOptimizationCombos ?? 300;
    document.querySelector("#learning-enabled").checked = Boolean(config.enabled);
    document.querySelector("#learning-schedule-enabled").checked = Boolean(config.schedule?.enabled);
    document.querySelector("#learning-interval-minutes").value = config.schedule?.intervalMinutes ?? 1440;
    document.querySelector("#learning-run-hour").value = config.schedule?.runAtHour ?? 3;
    document.querySelector("#learning-run-minute").value = config.schedule?.runAtMinute ?? 0;
    document.querySelector("#learning-auto-promote").checked = Boolean(config.autoPromote);
    loadAutoPromoteStatus();
    if (status) status.textContent = `Learning config loaded. Last: ${formatLearningTime(scheduleStatus.lastRunAt)} Next: ${formatLearningTime(scheduleStatus.nextRunAt)}. Auto-promotion is ${config.autoPromote ? "enabled for candidate-only mode" : "disabled"}.`;
  } catch (error) {
    if (status) status.textContent = `Learning config unavailable: ${error.message}`;
  }
}

async function saveLearningConfig() {
  const status = document.querySelector("#learning-status");
  try {
    const payload = learningConfigPayload();
    const config = await apiPost("/api/learning/config", payload);
    if (status) status.textContent = `Learning config saved. Schedule ${config.schedule?.enabled ? "enabled" : "disabled"}. Auto-promotion remains disabled.`;
    await loadLearningConfig();
  } catch (error) {
    if (status) status.textContent = `Could not save learning config: ${error.message}`;
  }
}

async function runLearningTick() {
  const status = document.querySelector("#learning-status");
  if (status) status.textContent = "Checking scheduled learning tick...";
  try {
    const payload = await apiPost("/api/learning/tick", {});
    if (payload.report) {
      lastLearningReport = payload.report;
      renderLearningReport(payload.report);
      loadLearningAuditSummary();
      loadLearningAudit();
      loadLearningDecisions();
      loadLearningReports();
    }
    if (status) status.textContent = `${payload.ran ? "Learning tick ran" : "Learning tick skipped"}: ${payload.reason} Next: ${formatLearningTime(payload.nextRunAt)}`;
  } catch (error) {
    if (status) status.textContent = `Learning tick failed: ${error.message}`;
  }
}

function learningConfigPayload() {
  return {
    enabled: document.querySelector("#learning-enabled")?.checked || false,
    symbols: csvInputValues("#learning-symbols"),
    timeframes: csvInputValues("#learning-timeframes"),
    period: document.querySelector("#learning-period")?.value || "365d",
    minTrades: Number(document.querySelector("#learning-min-trades")?.value || 20),
    maxRankingRuns: Number(document.querySelector("#learning-max-ranking")?.value || 20),
    maxOptimizationCombos: Number(document.querySelector("#learning-max-opt")?.value || 300),
    schedule: {
      enabled: document.querySelector("#learning-schedule-enabled")?.checked || false,
      mode: "interval",
      intervalMinutes: Number(document.querySelector("#learning-interval-minutes")?.value || 1440),
      runAtHour: Number(document.querySelector("#learning-run-hour")?.value || 3),
      runAtMinute: Number(document.querySelector("#learning-run-minute")?.value || 0),
      timezone: "local",
    },
    autoPromote: document.querySelector("#learning-auto-promote")?.checked || false,
    autoPromoteMode: "candidate_only",
    autoEnablePaper: false,
  };
}

function formatLearningTime(value) {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleString();
  } catch (error) {
    return value;
  }
}

async function runLearningCycle() {
  const status = document.querySelector("#learning-status");
  const summary = document.querySelector("#learning-summary");
  const recommendation = document.querySelector("#learning-recommendation");
  if (status) status.textContent = "Running backend learning cycle...";
  if (summary) summary.innerHTML = "";
  if (recommendation) recommendation.innerHTML = `<p class="pane-status">Research cycle running. This may take a while.</p>`;
  const body = {
    symbols: csvInputValues("#learning-symbols"),
    timeframes: csvInputValues("#learning-timeframes"),
    period: document.querySelector("#learning-period")?.value || "365d",
    minTrades: Number(document.querySelector("#learning-min-trades")?.value || 20),
    maxRankingRuns: Number(document.querySelector("#learning-max-ranking")?.value || 20),
    maxOptimizationCombos: Number(document.querySelector("#learning-max-opt")?.value || 300),
  };
  try {
    const report = await apiPost("/api/learning/run", body);
    lastLearningReport = report;
    renderLearningReport(report);
    loadLearningAuditSummary();
    loadLearningAudit();
    loadLearningDecisions();
    loadLearningReports();
    if (status) status.textContent = `Learning cycle ${report.status}. Recommendation: ${report.recommendation?.action || "-"}.`;
  } catch (error) {
    if (status) status.textContent = `Learning cycle failed: ${error.message}`;
    if (recommendation) recommendation.innerHTML = `<p class="pane-status">${escapeHtml(error.message)}</p>`;
  }
}

function csvInputValues(selector) {
  return (document.querySelector(selector)?.value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function loadLearningReports() {
  const body = document.querySelector("#learning-reports-body");
  if (!body) return;
  try {
    const payload = await apiGet("/api/learning/reports?limit=20");
    body.innerHTML = (payload.reports || []).map((report) => `
      <tr>
        <td>${report.createdAt ? escapeHtml(new Date(report.createdAt).toLocaleString()) : "-"}</td>
        <td>${escapeHtml(report.status || "-")}</td>
        <td>${escapeHtml(report.recommendation?.action || "-")}</td>
        <td>${(report.rankingRunIds || []).length}</td>
        <td>${(report.optimizationRunIds || []).length}</td>
        <td><button type="button" class="small-action-button" data-load-learning-report="${escapeHtml(report.id)}">Load</button></td>
      </tr>
    `).join("") || `<tr><td colspan="6">No learning reports yet.</td></tr>`;
  } catch (error) {
    body.innerHTML = `<tr><td colspan="6">Learning reports could not load: ${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadLearningDecisions() {
  const summary = document.querySelector("#learning-decision-summary");
  const body = document.querySelector("#learning-decisions-body");
  if (!summary || !body) return;
  try {
    const payload = await apiGet("/api/learning/decisions?limit=50");
    renderLearningDecisionSummary(payload.summary || {});
    body.innerHTML = (payload.decisions || []).map((decision) => {
      const candidate = decision.candidate || {};
      const candidateLabel = candidate.strategy
        ? `${candidate.strategy} ${candidate.symbol || ""} ${candidate.timeframe || ""}`.trim()
        : "-";
      return `
        <tr>
          <td>${decision.createdAt ? escapeHtml(new Date(decision.createdAt).toLocaleString()) : "-"}</td>
          <td>${escapeHtml(decision.action || "-")}</td>
          <td>${escapeHtml(candidateLabel)}</td>
          <td>${escapeHtml(decision.auditStatus || "-")}</td>
          <td>${formatNumber(decision.robustnessScore)}</td>
          <td class="${decision.promoted ? "positive" : "neutral"}">${decision.promoted ? "yes" : "no"}</td>
          <td>${escapeHtml(decision.reason || "")}</td>
        </tr>
      `;
    }).join("") || `<tr><td colspan="7">No learning decisions recorded yet.</td></tr>`;
  } catch (error) {
    summary.innerHTML = "";
    body.innerHTML = `<tr><td colspan="7">Decision log could not load: ${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderLearningDecisionSummary(summary) {
  const host = document.querySelector("#learning-decision-summary");
  if (!host) return;
  const churn = summary.candidateChurn || {};
  const latestPromoted = summary.latestPromotedCandidate || {};
  host.innerHTML = `
    <div class="metric"><span>Total decisions</span><strong>${summary.totalDecisions || 0}</strong></div>
    <div class="metric"><span>Auto-promoted</span><strong>${summary.autoPromotions || 0}</strong></div>
    <div class="metric"><span>Rejected</span><strong>${summary.rejectedAutoPromotions || 0}</strong></div>
    <div class="metric"><span>Latest action</span><strong>${escapeHtml(summary.latestAction || "-")}</strong></div>
    <div class="metric"><span>Candidate churn</span><strong>${churn.uniqueCandidates || 0}/${churn.observations || 0}</strong></div>
    <div class="metric"><span>Last promoted</span><strong>${latestPromoted.strategy ? `${escapeHtml(latestPromoted.strategy)} ${escapeHtml(latestPromoted.symbol || "")}` : "-"}</strong></div>
  `;
}

async function loadLearningAudit() {
  const host = document.querySelector("#learning-audit");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading learning quality audit...</p>`;
    const payload = await apiGet("/api/learning/audit");
    host.innerHTML = renderLearningAudit(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Learning audit could not load: ${escapeHtml(error.message)}</p>`;
  }
}

async function loadLearningAuditSummary() {
  const host = document.querySelector("#learning-audit-summary");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading audit summary...</p>`;
    const payload = await apiGet("/api/learning/audit-summary");
    host.innerHTML = renderLearningAuditSummary(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Audit summary could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderLearningAuditSummary(payload) {
  const latest = payload.latestLearningReport || {};
  const opt = payload.optimizerQuality || {};
  const gridAudit = payload.gridAudit || {};
  const zero = payload.zeroTrade || {};
  const readiness = payload.readiness || {};
  const next = payload.nextAction || {};
  const recommended = payload.latestLearningRecommendationCandidate || {};
  const latestOptimizer = payload.latestOptimizerRun || payload.latestOptimizationRun || {};
  const best = payload.bestSavedCandidate || {};
  const current = payload.currentPaperCandidate || {};
  const comparison = payload.candidateComparison || {};
  const commands = (next.commands || []).map((command) => `<code>${escapeHtml(command)}</code>`).join("<br>");
  const rejectionRows = (opt.topRejectionReasons || []).slice(0, 5).map((item) => `
    <tr><td>${escapeHtml(item.label || item.reason || "-")}</td><td>${item.count || 0}</td></tr>
  `).join("");
  const recommendedRobustnessRows = renderCandidateRobustnessRows(recommended);
  const comparisonNotes = (comparison.notes || []).map(escapeHtml).join("; ");
  return `
    <h3 class="modal-section-title">Audit Summary <span class="${payload.ok ? "positive" : "negative"}">${payload.ok ? "OK" : "Check"}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Learning</span><strong>${escapeHtml(latest.status || "none")}</strong></div>
      <div class="metric"><span>Optimizer</span><strong>${escapeHtml(opt.latestSelectedStatus || "UNKNOWN")}</strong></div>
      <div class="metric"><span>Grid audit</span><strong>${escapeHtml(gridAudit.diagnosis || "UNKNOWN")}</strong></div>
      <div class="metric"><span>PASS/WARN/FAIL</span><strong>${opt.passCandidates || 0}/${opt.warnCandidates || 0}/${opt.failCandidates || 0}</strong></div>
      <div class="metric"><span>Zero-trade</span><strong>${zero.hasZeroTradeProblem ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Paper</span><strong>${current.enabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <tbody>
        <tr><th>Latest recommended candidate</th><td>${recommended.strategy ? `${escapeHtml(recommended.strategy)} ${escapeHtml(recommended.symbol || "")} ${escapeHtml(recommended.timeframe || "")} - ${escapeHtml(recommended.qualityStatus || "-")} - PF ${formatNumber(recommended.profitFactor)} - T ${recommended.trades || 0}` : "-"}</td></tr>
        ${recommendedRobustnessRows}
        <tr><th>Best saved candidate</th><td>${best.strategy ? `${escapeHtml(best.strategy)} ${escapeHtml(best.symbol || "")} ${escapeHtml(best.timeframe || "")} - PF ${formatNumber(best.profitFactor)} - T ${best.trades || 0}` : "-"}</td></tr>
        <tr><th>Current paper candidate</th><td>${current.strategy ? `${escapeHtml(current.strategy)} ${escapeHtml((current.activeSymbols || [])[0]?.symbol || "")}` : "-"}</td></tr>
        <tr><th>Latest optimizer run</th><td>${latestOptimizer.id ? `${escapeHtml((latestOptimizer.strategies || [])[0] || "-")} ${escapeHtml((latestOptimizer.symbols || [])[0] || "")} ${escapeHtml((latestOptimizer.timeframes || [])[0] || "")} - ${escapeHtml(latestOptimizer.latestSelectedStatus || opt.latestSelectedStatus || "UNKNOWN")}` : "-"}</td></tr>
        <tr><th>Candidate comparison</th><td>${comparison.recommendedStrategy ? `${escapeHtml(comparison.recommendedStrategy || "-")} ${escapeHtml(comparison.recommendedSymbol || "")} ${escapeHtml(comparison.recommendedTimeframe || "")} vs ${escapeHtml(comparison.currentPaperStrategy || "-")} ${escapeHtml(comparison.currentPaperSymbol || "")} - same ${comparison.sameAsCurrentPaper ? "yes" : "no"} - better ${comparison.recommendedBetterThanCurrent === null || comparison.recommendedBetterThanCurrent === undefined ? "unknown" : comparison.recommendedBetterThanCurrent ? "yes" : "no"}` : "-"}</td></tr>
        ${comparisonNotes ? `<tr><th>Comparison notes</th><td>${comparisonNotes}</td></tr>` : ""}
        <tr><th>Safe for manual review</th><td class="${readiness.safeForManualReview ? "positive" : "neutral"}">${readiness.safeForManualReview ? "yes" : "no"}</td></tr>
        <tr><th>Manual inspection candidate</th><td class="${payload.latestLearningRecommendationUsableForManualInspection ? "positive" : "neutral"}">${payload.latestLearningRecommendationUsableForManualInspection ? "yes" : "no"}</td></tr>
        <tr><th>Suggested commands</th><td>${commands || "-"}</td></tr>
      </tbody>
    </table>
    <h3 class="modal-section-title">Top Rejection Reasons</h3>
    <table class="trade-table">
      <thead><tr><th>Reason</th><th>Count</th></tr></thead>
      <tbody>${rejectionRows || `<tr><td colspan="2">No optimizer rejection reasons available.</td></tr>`}</tbody>
    </table>
    ${(zero.suggestedActions || []).length ? `<ul class="backtest-warnings">${zero.suggestedActions.map((action) => `<li>${escapeHtml(action)}</li>`).join("")}</ul>` : ""}
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

async function loadCandidateReview() {
  const host = document.querySelector("#candidate-review-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading candidate review...</p>`;
    const payload = await apiGet("/api/candidate/review");
    host.innerHTML = renderCandidateReview(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Candidate review could not load: ${escapeHtml(error.message)}</p>`;
  }
}

async function previewCandidatePromotion() {
  const host = document.querySelector("#candidate-review-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Building config-only promotion preview...</p>`;
    const payload = await apiPost("/api/candidate/promote-preview", {});
    host.innerHTML = renderPromotionPreview(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Promotion preview failed: ${escapeHtml(error.message)}</p>`;
  }
}

async function loadResearchCandidateReviewReport() {
  const host = document.querySelector("#research-candidate-review-report-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading candidate review report...</p>`;
    const payload = await apiGet("/api/research/candidate-review-report?includeDetails=false");
    host.innerHTML = renderResearchCandidateReviewReport(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Candidate review report could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderResearchCandidateReviewReport(payload) {
  const verdict = payload.verdict || {};
  const scores = payload.scores || {};
  const evidence = payload.evidence || {};
  const activity = evidence.activity || {};
  const robust = evidence.robustness || {};
  const blockers = evidence.blockers || {};
  const variants = evidence.variants || {};
  const paper = evidence.paper || {};
  const paperEvidence = paper.evidence || {};
  const signal = evidence.signalDiagnostics || {};
  const next = verdict.nextAction || {};
  const tone = verdict.status === "READY_FOR_LONGER_PAPER" || verdict.status === "KEEP_OBSERVING" ? "positive" : verdict.status === "RESEARCH_ALTERNATIVES" ? "neutral" : "negative";
  const list = (items) => (items || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  const topBlockers = (blockers.topBlockers || []).map((item) => `${item.name}:${item.count}`).join(", ");
  const bestTradeoff = variants.bestTradeoff || {};
  const mostActive = variants.mostActivePassing || {};
  return `
    <h3 class="modal-section-title">Candidate Review Report <span class="${tone}">${escapeHtml(verdict.status || "UNKNOWN")}</span></h3>
    <p class="modal-note"><strong>${escapeHtml(verdict.title || "-")}</strong> ${escapeHtml(verdict.summary || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Overall score</span><strong>${formatNumber(scores.overallScore)}</strong></div>
      <div class="metric"><span>Activity</span><strong>${formatNumber(scores.activityScore)}</strong></div>
      <div class="metric"><span>Robustness</span><strong>${formatNumber(scores.robustnessScore)}</strong></div>
      <div class="metric"><span>Paper</span><strong>${formatNumber(scores.paperScore)}</strong></div>
      <div class="metric"><span>Variant</span><strong>${formatNumber(scores.variantScore)}</strong></div>
      <div class="metric"><span>Blocker</span><strong>${formatNumber(scores.blockerScore)}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
    </div>
    <table class="trade-table">
      <tbody>
        <tr><th>Next action</th><td><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</td></tr>
        <tr><th>Activity</th><td>${escapeHtml(activity.status || "-")} &middot; ${activity.trades ?? 0} trades &middot; PF ${formatNumber(activity.profitFactor)} &middot; Return ${formatSigned(activity.totalReturnPct || 0)}% &middot; ${formatNumber(activity.tradesPerMonth)}/mo</td></tr>
        <tr><th>Robustness</th><td>${escapeHtml(robust.status || "-")} &middot; Pass ${formatNumber((robust.passRate || 0) * 100)}% &middot; Median PF ${formatNumber(robust.medianProfitFactor)} &middot; Median return ${formatSigned(robust.medianReturnPct || 0)}%</td></tr>
        <tr><th>Blockers</th><td>Main ${escapeHtml(blockers.mainBlocker || "-")} &middot; ${escapeHtml(topBlockers || "-")}</td></tr>
        <tr><th>Variants</th><td>Best tradeoff ${escapeHtml(bestTradeoff.variantName || "-")} &middot; Most active ${escapeHtml(mostActive.variantName || "-")} ${formatNumber(mostActive.tradesPerMonth)}/mo</td></tr>
        <tr><th>Paper evidence</th><td>${paperEvidence.runnerTicksRun ?? paperEvidence.ticksObserved ?? 0} useful tick(s) &middot; ${paperEvidence.signalsObserved ?? 0} signal(s) &middot; ${paperEvidence.closedTrades ?? 0} closed trade(s)</td></tr>
        <tr><th>Latest signal</th><td>${escapeHtml(signal.signal || "-")} &middot; ${escapeHtml(signal.reason || "")}</td></tr>
      </tbody>
    </table>
    <div class="metric-grid">
      <div class="metric"><span>Strengths</span><strong>${(payload.strengths || []).length}</strong></div>
      <div class="metric"><span>Weaknesses</span><strong>${(payload.weaknesses || []).length}</strong></div>
      <div class="metric"><span>Risks</span><strong>${(payload.risks || []).length}</strong></div>
      <div class="metric"><span>Recommendations</span><strong>${(payload.recommendations || []).length}</strong></div>
    </div>
    ${payload.strengths?.length ? `<p class="modal-note"><strong>Strengths</strong></p><ul class="modal-note-list">${list(payload.strengths)}</ul>` : ""}
    ${payload.weaknesses?.length ? `<p class="modal-note"><strong>Weaknesses</strong></p><ul class="backtest-warnings">${list(payload.weaknesses)}</ul>` : ""}
    ${payload.risks?.length ? `<p class="modal-note"><strong>Risks</strong></p><ul class="backtest-warnings">${list(payload.risks)}</ul>` : ""}
    ${payload.recommendations?.length ? `<p class="modal-note"><strong>Recommendations</strong></p><ul class="modal-note-list">${list(payload.recommendations)}</ul>` : ""}
    ${payload.warnings?.length ? `<p class="modal-note"><strong>Warnings</strong></p><ul class="backtest-warnings">${list(payload.warnings)}</ul>` : ""}
  `;
}

function renderDiffRows(rows, emptyText) {
  return (rows || []).map((item) => `
    <tr><td>${escapeHtml(item.field || "-")}</td><td>${escapeHtml(formatDiffValue(item.current))}</td><td>${escapeHtml(formatDiffValue(item.recommended ?? item.preview))}</td></tr>
  `).join("") || `<tr><td colspan="3">${escapeHtml(emptyText)}</td></tr>`;
}

function formatDiffValue(value) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return compactJson(value);
  return String(value);
}

function renderCandidateReview(payload) {
  const rec = payload.recommendedCandidate || {};
  const current = payload.currentPaperCandidate || {};
  const comparison = payload.comparison || {};
  const readiness = payload.readiness || {};
  const next = payload.nextAction || {};
  const active = (current.activeSymbols || [])[0] || {};
  return `
    <h3 class="modal-section-title">Candidate Review <span class="${readiness.canPromoteConfigOnly ? "positive" : "neutral"}">${escapeHtml(next.action || "-")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Recommended</span><strong>${rec.strategy ? `${escapeHtml(rec.strategy)} ${escapeHtml(rec.symbol || "")} ${escapeHtml(rec.timeframe || "")}` : "-"}</strong></div>
      <div class="metric"><span>Current</span><strong>${current.strategy ? `${escapeHtml(current.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Audit</span><strong>${escapeHtml(readiness.auditStatus || "-")}</strong></div>
      <div class="metric"><span>Paper</span><strong>${readiness.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Config-only</span><strong>${readiness.canPromoteConfigOnly ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Enable paper</span><strong>${readiness.canEnablePaper ? "yes" : "no"}</strong></div>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <tbody>
        <tr><th>Comparison</th><td>${escapeHtml(comparison.summary || "-")}</td></tr>
        <tr><th>Same strategy</th><td>${comparison.sameStrategy ? "yes" : "no"}</td></tr>
        <tr><th>Same symbol</th><td>${comparison.sameSymbol ? "yes" : "no"}</td></tr>
        <tr><th>Same timeframe</th><td>${comparison.sameTimeframe ? "yes" : "no"}</td></tr>
        <tr><th>Expected metrics</th><td>PF ${formatMaybeNumber(rec.profitFactor)} / Train ${formatMaybeNumber(candidateMetric(rec, "train", "totalReturn"))}% / Test ${formatMaybeNumber(candidateMetric(rec, "test", "totalReturn", ["totalReturnPct"]))}% / Full ${formatMaybeNumber(candidateMetric(rec, "full", "totalReturn"))}% / Test trades ${candidateMetric(rec, "test", "trades", ["trades"]) ?? "-"}</td></tr>
      </tbody>
    </table>
    <h3 class="modal-section-title">Parameter Differences</h3>
    <table class="trade-table"><thead><tr><th>Field</th><th>Current</th><th>Recommended</th></tr></thead><tbody>${renderDiffRows(comparison.paramDiffs, "No parameter differences.")}</tbody></table>
    <h3 class="modal-section-title">Expected Metric Differences</h3>
    <table class="trade-table"><thead><tr><th>Metric</th><th>Current</th><th>Preview</th></tr></thead><tbody>${renderDiffRows(comparison.expectedMetricDiffs, "No expected metric differences.")}</tbody></table>
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

function renderPromotionPreview(payload) {
  const preview = payload.candidateConfigPreview || {};
  const expected = payload.expectedBaselineMetrics || {};
  const active = (preview.symbols || []).find((item) => item.mode === "active") || {};
  return `
    <h3 class="modal-section-title">Promotion Preview <span class="${payload.paperRemainsDisabled ? "positive" : "negative"}">${payload.paperRemainsDisabled ? "Paper disabled" : "Check paper"}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(preview.strategy || "-")}</strong></div>
      <div class="metric"><span>Primary</span><strong>${escapeHtml(active.symbol || "-")} ${escapeHtml(active.interval || "")}</strong></div>
      <div class="metric"><span>Enabled</span><strong>${preview.enabled ? "yes" : "no"}</strong></div>
      <div class="metric"><span>PF</span><strong>${formatMaybeNumber(expected.profitFactor)}</strong></div>
      <div class="metric"><span>Trades</span><strong>${expected.trades ?? "-"}</strong></div>
      <div class="metric"><span>Return</span><strong>${formatMaybeNumber(expected.totalReturnPct)}%</strong></div>
    </div>
    <p class="modal-note">${escapeHtml(payload.message || "Dry run only; no config was written.")}</p>
    <table class="trade-table"><thead><tr><th>Field</th><th>Current</th><th>Preview</th></tr></thead><tbody>${renderDiffRows(payload.changedFields, "No config fields would change.")}</tbody></table>
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

async function loadCandidateStability() {
  const host = document.querySelector("#candidate-stability-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Running bounded stability validation...</p>`;
    const payload = await apiGet("/api/candidate/stability");
    host.innerHTML = renderCandidateStability(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Candidate stability could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderCandidateStability(payload) {
  const candidate = payload.candidate || {};
  const validation = payload.validation || {};
  const comparison = payload.comparisonToCurrent || {};
  const next = payload.nextAction || {};
  const statusTone = validation.status === "PASS" ? "positive" : validation.status === "FAIL" ? "negative" : "neutral";
  const rows = (validation.windows || []).map((row) => `
    <tr>
      <td>${escapeHtml(row.period || row.label || "-")}</td>
      <td class="${row.status === "PASS" ? "positive" : row.status === "FAIL" ? "negative" : "neutral"}">${escapeHtml(row.status || "UNKNOWN")}</td>
      <td>${row.trades ?? "-"}</td>
      <td>${formatMaybeNumber(row.profitFactor)}</td>
      <td>${formatMaybeNumber(row.totalReturnPct)}%</td>
      <td>${formatMaybeNumber(row.maxDrawdownPct)}%</td>
      <td>${formatMaybeNumber(row.winRate)}%</td>
    </tr>
  `).join("");
  const diffRows = (comparison.metricDiffs || []).map((item) => `
    <tr><td>${escapeHtml(item.field || "-")}</td><td>${formatMaybeNumber(item.candidate)}</td><td>${formatMaybeNumber(item.current)}</td><td>${formatMaybeNumber(item.diff)}</td></tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Candidate Stability <span class="${statusTone}">${escapeHtml(validation.status || "UNKNOWN")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(candidate.symbol || "")} ${escapeHtml(candidate.timeframe || "")}` : "-"}</strong></div>
      <div class="metric"><span>Trades</span><strong>${validation.aggregate?.trades ?? "-"}</strong></div>
      <div class="metric"><span>PF</span><strong>${formatMaybeNumber(validation.aggregate?.profitFactor)}</strong></div>
      <div class="metric"><span>Return</span><strong>${formatMaybeNumber(validation.aggregate?.totalReturnPct)}%</strong></div>
      <div class="metric"><span>Drawdown</span><strong>${formatMaybeNumber(validation.aggregate?.maxDrawdownPct)}%</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <p class="modal-note">${escapeHtml(validation.summary || "")}</p>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <thead><tr><th>Window</th><th>Status</th><th>Trades</th><th>PF</th><th>Return</th><th>Drawdown</th><th>Win</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="7">No validation windows returned.</td></tr>`}</tbody>
    </table>
    <h3 class="modal-section-title">Comparison To Current</h3>
    <p class="modal-note">${escapeHtml(comparison.summary || "-")}</p>
    <table class="trade-table">
      <thead><tr><th>Metric</th><th>Candidate</th><th>Current</th><th>Diff</th></tr></thead>
      <tbody>${diffRows || `<tr><td colspan="4">No current-candidate comparison available.</td></tr>`}</tbody>
    </table>
    ${(validation.robustnessFlags || []).length ? `<p class="modal-note"><strong>Flags:</strong> ${validation.robustnessFlags.map(escapeHtml).join(", ")}</p>` : ""}
    ${(validation.warnings || []).length ? `<ul class="backtest-warnings">${validation.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

async function loadPaperReadiness() {
  const host = document.querySelector("#paper-readiness-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Checking paper readiness...</p>`;
    const payload = await apiGet("/api/paper/readiness");
    host.innerHTML = renderPaperReadiness(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper readiness could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperReadiness(payload) {
  const candidate = payload.candidate || {};
  const summary = payload.summary || {};
  const next = payload.nextAction || {};
  const active = (candidate.activeSymbols || [])[0] || {};
  const tone = payload.status === "READY_FOR_PAPER_REVIEW" ? "positive" : payload.status === "BLOCKED" ? "negative" : "neutral";
  const rows = (payload.checks || []).map((check) => `
    <tr>
      <td>${escapeHtml(check.name || "-")}</td>
      <td class="${check.pass ? "positive" : check.severity === "BLOCK" ? "negative" : "neutral"}">${check.pass ? "yes" : "no"}</td>
      <td>${escapeHtml(check.severity || "-")}</td>
      <td>${escapeHtml(check.detail || "-")}</td>
    </tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Paper Readiness <span class="${tone}">${escapeHtml(payload.status || "UNKNOWN")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Ready</span><strong>${payload.ready ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Blocking</span><strong>${summary.blockingIssues ?? 0}</strong></div>
      <div class="metric"><span>Warnings</span><strong>${summary.warnings ?? 0}</strong></div>
      <div class="metric"><span>Paper</span><strong>${summary.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${summary.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Validation</span><strong>${escapeHtml(summary.validationStatus || "-")}</strong></div>
      <div class="metric"><span>Stability</span><strong>${escapeHtml(summary.stabilityStatus || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <thead><tr><th>Check</th><th>Pass</th><th>Severity</th><th>Detail</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4">No readiness checks returned.</td></tr>`}</tbody>
    </table>
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

async function loadPaperSimulationControl() {
  const host = document.querySelector("#paper-control-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper simulation control...</p>`;
    const payload = await apiGet("/api/paper/status");
    host.innerHTML = renderPaperSimulationControl(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper simulation control could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperSimulationControl(payload) {
  const candidate = payload.candidate || {};
  const readiness = payload.readiness || {};
  const next = readiness.nextAction || {};
  const summary = readiness.summary || {};
  const active = (candidate.activeSymbols || [])[0] || {};
  const tone = payload.paperEnabled ? "positive" : readiness.ready ? "positive" : readiness.status === "BLOCKED" ? "negative" : "neutral";
  return `
    <h3 class="modal-section-title">Paper Simulation Control <span class="${tone}">${payload.paperEnabled ? "PAPER ENABLED" : escapeHtml(readiness.status || "UNKNOWN")}</span></h3>
    <div class="paper-warning">Paper simulation only. No real trades. Real trading disabled.</div>
    <div class="metric-grid">
      <div class="metric"><span>Paper enabled</span><strong>${payload.paperEnabled ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Readiness</span><strong>${escapeHtml(readiness.status || "-")}</strong></div>
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Blocking</span><strong>${summary.blockingIssues ?? 0}</strong></div>
      <div class="metric"><span>Warnings</span><strong>${summary.warnings ?? 0}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
  `;
}

function renderPaperControlResult(payload, fallbackMessage) {
  const changed = (payload.changedFields || []).map((item) => `
    <tr><td>${escapeHtml(item.field || "-")}</td><td>${escapeHtml(formatDiffValue(item.current))}</td><td>${escapeHtml(formatDiffValue(item.preview))}</td></tr>
  `).join("");
  const readiness = payload.readiness || {};
  const candidate = payload.candidateConfig || payload.previewConfig || {};
  const active = (candidate.symbols || []).find((item) => item.mode === "active") || {};
  return `
    <p class="pane-status">${escapeHtml(payload.message || fallbackMessage)}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper enabled</span><strong>${payload.paperEnabled ?? payload.previewConfig?.enabled ?? "-"}</strong></div>
      <div class="metric"><span>Would enable</span><strong>${payload.wouldEnablePaper === undefined ? "-" : payload.wouldEnablePaper ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled || payload.paperRemainsRealOnlyFalse === false ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Readiness</span><strong>${escapeHtml(readiness.status || "-")}</strong></div>
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Backup</span><strong>${escapeHtml(payload.backupPath || "-")}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Field</th><th>Current</th><th>Preview</th></tr></thead>
      <tbody>${changed || `<tr><td colspan="3">No config fields changed.</td></tr>`}</tbody>
    </table>
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

async function previewPaperEnable() {
  const host = document.querySelector("#paper-control-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Building paper enable preview...</p>`;
    const payload = await apiPost("/api/paper/enable-preview", {});
    host.innerHTML = renderPaperControlResult(payload, "Preview only; paper simulation was not enabled.");
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper enable preview failed: ${escapeHtml(error.message)}</p>`;
  }
}

async function enablePaperSimulation() {
  const host = document.querySelector("#paper-control-panel");
  if (!host) return;
  const ok = window.confirm("Enable paper simulation for this candidate?\n\nPaper simulation only. No real trades will be placed.");
  if (!ok) return;
  try {
    host.innerHTML = `<p class="pane-status">Enabling paper simulation...</p>`;
    const payload = await apiPost("/api/paper/enable", {});
    host.innerHTML = renderPaperControlResult(payload, "Paper simulation enabled.");
    await refreshPaperLearningPanels();
    if (paperPanel && !paperPanel.hidden) await openPaperPanel();
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper enable failed: ${escapeHtml(error.message)}</p>`;
  }
}

async function disablePaperSimulation() {
  const host = document.querySelector("#paper-control-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Disabling paper simulation...</p>`;
    const payload = await apiPost("/api/paper/disable", {});
    host.innerHTML = renderPaperControlResult(payload, "Paper simulation disabled.");
    await refreshPaperLearningPanels();
    if (paperPanel && !paperPanel.hidden) await openPaperPanel();
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper disable failed: ${escapeHtml(error.message)}</p>`;
  }
}

async function loadPaperObservationReport() {
  const host = document.querySelector("#paper-observation-report-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper observation report...</p>`;
    const payload = await apiGet("/api/paper/observation-report");
    host.innerHTML = renderPaperObservationReport(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper observation report could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperObservationReport(payload) {
  const verdict = payload.verdict || {};
  const evidence = payload.evidence || {};
  const progress = payload.progress || {};
  const performance = payload.performance || {};
  const baseline = payload.baseline || {};
  const active = payload.activeMarket || {};
  const next = verdict.nextAction || {};
  const tone = verdict.status === "READY_FOR_REVIEW" ? "positive" : verdict.status === "PAUSE_RECOMMENDED" || payload.realTradingEnabled ? "negative" : verdict.status === "WATCH" ? "negative" : "neutral";
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const info = (payload.informationalWarnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const baselineText = baseline.available
    ? `PF ${baseline.expectedProfitFactor ?? "-"} · Return ${baseline.expectedReturnPct ?? "-"}% · Trades ${baseline.expectedTrades ?? "-"}`
    : "No baseline";
  const paperText = `PF ${performance.profitFactor ?? "-"} · Return ${performance.returnPct ?? "-"}% · Drawdown ${performance.maxDrawdownPct ?? "-"}%`;
  return `
    <h3 class="modal-section-title">Paper Observation Report <span class="${tone}">${escapeHtml(verdict.status || "UNKNOWN")}</span></h3>
    <p class="modal-note"><strong>${escapeHtml(verdict.title || "Paper observation")}</strong> ${escapeHtml(verdict.summary || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Active market</span><strong>${escapeHtml(active.symbol || "-")} ${escapeHtml(active.timeframe || "")}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Tick readiness</span><strong>${escapeHtml(active.tickReadinessStatus || "-")}</strong></div>
      <div class="metric"><span>Session hours</span><strong>${evidence.sessionAgeHours ?? 0} / ${progress.minSessionHours ?? "-"}</strong></div>
      <div class="metric"><span>Ticks</span><strong>${evidence.ticksObserved ?? 0} / ${progress.minPaperTicks ?? "-"}</strong></div>
      <div class="metric"><span>Closed trades</span><strong>${evidence.closedTrades ?? 0} / ${progress.minClosedTrades ?? "-"}</strong></div>
      <div class="metric"><span>Signals</span><strong>${evidence.signalsObserved ?? 0}</strong></div>
      <div class="metric"><span>Open positions</span><strong>${evidence.openPositions ?? 0}</strong></div>
      <div class="metric"><span>Active warnings</span><strong>${evidence.activeWarnings ?? 0}</strong></div>
      <div class="metric"><span>Stop rules</span><strong>${escapeHtml(evidence.stopRulesStatus || "-")}</strong></div>
      <div class="metric"><span>Targets</span><strong>${escapeHtml(evidence.observationTargetStatus || "-")}</strong></div>
    </div>
    <table class="trade-table">
      <tbody>
        <tr><th>Next action</th><td><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</td></tr>
        <tr><th>Remaining</th><td>${progress.remainingSessionHours ?? 0}h · ${progress.remainingPaperTicks ?? 0} tick(s) · ${progress.remainingClosedTrades ?? 0} closed trade(s)</td></tr>
        <tr><th>Paper performance</th><td>${escapeHtml(paperText)}</td></tr>
        <tr><th>Baseline</th><td>${escapeHtml(baselineText)}</td></tr>
      </tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
    ${info ? `<ul class="modal-note-list">${info}</ul>` : ""}
  `;
}

async function loadPaperSignalDiagnostics() {
  const host = document.querySelector("#paper-signal-diagnostics-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading active signal diagnostics...</p>`;
    const payload = await apiGet("/api/paper/active-signal-diagnostics");
    host.innerHTML = renderPaperSignalDiagnostics(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Active signal diagnostics could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperSignalDiagnostics(payload) {
  const diagnostic = payload.diagnostics || {};
  const active = payload.activeMarket || {};
  const latest = payload.latestCandle || {};
  const snapshot = diagnostic.indicatorSnapshot || {};
  const position = diagnostic.positionState || {};
  const next = payload.nextAction || {};
  const signal = diagnostic.signal || "UNKNOWN";
  const tone = ["BUY", "SHORT", "EXIT"].includes(signal) ? "positive" : payload.ok === false ? "negative" : "neutral";
  const checks = (diagnostic.checks || []).slice(0, 8).map((check) => `
    <tr>
      <td>${escapeHtml(check.name || "-")}</td>
      <td class="${check.pass === true ? "positive" : check.pass === false ? "negative" : "neutral"}">${check.pass === null || check.pass === undefined ? "n/a" : check.pass ? "pass" : "block"}</td>
      <td><code>${escapeHtml(compactJson(check.value ?? "-"))}</code></td>
      <td>${escapeHtml(check.threshold || "-")}</td>
    </tr>
  `).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Active Signal Diagnostics <span class="${tone}">${escapeHtml(signal)}</span></h3>
    <p class="modal-note">${escapeHtml(diagnostic.reason || payload.error || "No diagnostic reason returned.")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Active market</span><strong>${escapeHtml(active.symbol || "-")} ${escapeHtml(active.timeframe || "")}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Latest candle</span><strong>${escapeHtml(latest.isoTime || "-")}</strong></div>
      <div class="metric"><span>Close</span><strong>${latest.close ?? "-"}</strong></div>
      <div class="metric"><span>Position</span><strong>${position.hasOpenPosition ? escapeHtml(position.side || "open") : "flat"}</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <table class="trade-table">
      <tbody>
        <tr><th>Next action</th><td><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</td></tr>
        <tr><th>Snapshot</th><td>Close ${snapshot.close ?? "-"} · EMA fast ${snapshot.emaFast ?? "-"} · EMA slow ${snapshot.emaSlow ?? "-"} · EMA trend ${snapshot.emaTrend ?? "-"} · ATR ${snapshot.atr ?? "-"} · RSI ${snapshot.rsi ?? "-"}</td></tr>
      </tbody>
    </table>
    <table class="trade-table">
      <thead><tr><th>Check</th><th>Status</th><th>Value</th><th>Threshold</th></tr></thead>
      <tbody>${checks || `<tr><td colspan="4">No checks returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadResearchBlockerAnalytics() {
  const host = document.querySelector("#research-blocker-analytics-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading strategy blocker analytics...</p>`;
    const payload = await apiGet("/api/research/blocker-analytics");
    host.innerHTML = renderResearchBlockerAnalytics(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Strategy blocker analytics could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderResearchBlockerAnalytics(payload) {
  const search = payload.search || {};
  const summary = payload.summary || {};
  const recommendation = summary.recommendation || {};
  const candidate = payload.candidate || {};
  const active = (candidate.activeSymbols || [])[0] || {};
  const blockers = (payload.blockers || []).slice(0, 10).map((row) => {
    const tone = row.severity === "HIGH" ? "negative" : row.severity === "MEDIUM" ? "neutral" : "positive";
    return `
      <tr>
        <td>${escapeHtml(row.name || "-")}</td>
        <td>${row.count ?? 0}</td>
        <td>${formatNumber(row.pctOfCandles)}%</td>
        <td>${formatNumber(row.pctOfHoldCandles)}%</td>
        <td>${row.recentCount ?? 0}</td>
        <td class="${tone}">${escapeHtml(row.severity || "-")}</td>
        <td>${escapeHtml(row.detail || "-")}</td>
      </tr>
    `;
  }).join("");
  const recent = (payload.recentCandles || []).slice(-10).map((row) => `
    <tr>
      <td>${escapeHtml(row.time || "-")}</td>
      <td>${formatNumber(row.close)}</td>
      <td>${escapeHtml(row.signal || "-")}</td>
      <td>${escapeHtml((row.blockers || []).join(", ") || "-")}</td>
      <td>${escapeHtml(row.reason || "-")}</td>
    </tr>
  `).join("");
  const nearMisses = (payload.nearMisses || []).slice(-8).map((row) => `
    <tr>
      <td>${escapeHtml(row.time || "-")}</td>
      <td>${formatNumber(row.close)}</td>
      <td>${formatNumber(row.nearMissScore)}</td>
      <td>${escapeHtml((row.failedBlockers || []).join(", ") || "-")}</td>
      <td>${escapeHtml(row.detail || "-")}</td>
    </tr>
  `).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Strategy Blocker Analytics <span class="neutral">${escapeHtml(recommendation.action || "OBSERVE")}</span></h3>
    <p class="modal-note"><strong>Active:</strong> ${escapeHtml(candidate.strategy || "-")} ${escapeHtml(active.symbol || search.symbol || "-")} ${escapeHtml(active.interval || search.timeframe || "")}. ${escapeHtml(recommendation.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Market</span><strong>${escapeHtml(search.symbol || "-")} ${escapeHtml(search.timeframe || "-")}</strong></div>
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(search.strategy || "-")}</strong></div>
      <div class="metric"><span>Candles</span><strong>${summary.candlesAnalyzed ?? 0}</strong></div>
      <div class="metric"><span>Trades</span><strong>${summary.tradeCount ?? 0}</strong></div>
      <div class="metric"><span>Signal rate</span><strong>${formatNumber(summary.signalRatePct)}%</strong></div>
      <div class="metric"><span>Signals/month</span><strong>${formatNumber(summary.approximateSignalsPerMonth)}</strong></div>
      <div class="metric"><span>Main blocker</span><strong>${escapeHtml(summary.mainBlocker || "-")}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Blocker</th><th>Count</th><th>% Candles</th><th>% Holds</th><th>Recent</th><th>Severity</th><th>Detail</th></tr></thead>
      <tbody>${blockers || `<tr><td colspan="7">No blocker rows returned.</td></tr>`}</tbody>
    </table>
    <table class="trade-table">
      <thead><tr><th>Recent candle</th><th>Close</th><th>Signal</th><th>Blockers</th><th>Reason</th></tr></thead>
      <tbody>${recent || `<tr><td colspan="5">No recent candle diagnostics returned.</td></tr>`}</tbody>
    </table>
    <table class="trade-table">
      <thead><tr><th>Near miss</th><th>Close</th><th>Score</th><th>Failed blockers</th><th>Detail</th></tr></thead>
      <tbody>${nearMisses || `<tr><td colspan="5">No near misses returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadPaperCandidateComparison() {
  const host = document.querySelector("#paper-candidate-comparison-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading candidate timeframe comparison...</p>`;
    const payload = await apiGet("/api/paper/candidate-comparison");
    host.innerHTML = renderPaperCandidateComparison(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Candidate comparison could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperCandidateComparison(payload) {
  const active = payload.activePaperCandidate || {};
  const activeMarket = (active.activeSymbols || [])[0] || {};
  const recommendation = payload.recommendation || {};
  const rows = (payload.rows || []).map((row) => {
    const tone = row.status === "PASS" ? "positive" : row.status === "WARN" ? "neutral" : "negative";
    const flags = [];
    if (row.comparableToActive) flags.push("active");
    if (row.diagnostics?.moreActiveThanCurrent) flags.push("more active");
    if (row.diagnostics?.enoughTrades) flags.push("enough trades");
    return `
      <tr>
        <td>${escapeHtml(row.symbol || "-")} ${escapeHtml(row.timeframe || "-")}</td>
        <td class="${tone}">${escapeHtml(row.status || "-")}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatNumber(row.winRate)}%</td>
        <td>${formatNumber(row.score)}</td>
        <td>${escapeHtml(flags.join(", ") || "-")}</td>
      </tr>
    `;
  }).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Candidate Timeframe Comparison <span class="neutral">${escapeHtml(recommendation.action || "NO_ACTION")}</span></h3>
    <p class="modal-note"><strong>Active:</strong> ${escapeHtml(active.strategy || "-")} ${escapeHtml(activeMarket.symbol || "-")} ${escapeHtml(activeMarket.interval || "")}. ${escapeHtml(recommendation.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Rows</span><strong>${payload.rows?.length || 0}</strong></div>
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(payload.request?.strategy || "-")}</strong></div>
      <div class="metric"><span>Period</span><strong>${escapeHtml(payload.request?.period || "-")}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Market</th><th>Status</th><th>Trades</th><th>PF</th><th>Return</th><th>DD</th><th>Win</th><th>Score</th><th>Notes</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="9">No comparison rows returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadPaperFastDiscovery() {
  const host = document.querySelector("#paper-fast-discovery-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading fast candidate discovery...</p>`;
    const payload = await apiGet("/api/paper/discover-fast-candidate");
    host.innerHTML = renderPaperFastDiscovery(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Fast candidate discovery could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperFastDiscovery(payload) {
  const search = payload.search || {};
  const best = payload.bestCandidate || {};
  const recommendation = payload.recommendation || {};
  const rows = (payload.rows || []).map((row) => {
    const tone = row.qualityStatus === "PASS" ? "positive" : row.qualityStatus === "WARN" ? "neutral" : "negative";
    return `
      <tr>
        <td>${escapeHtml(row.symbol || "-")} ${escapeHtml(row.timeframe || "-")}</td>
        <td class="${tone}">${escapeHtml(row.qualityStatus || row.status || "-")}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatNumber(row.winRate)}%</td>
        <td>${formatNumber(row.score)}</td>
      </tr>
    `;
  }).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const bestText = payload.bestCandidate
    ? `${best.symbol} ${best.timeframe} PF ${best.profitFactor} Return ${best.totalReturnPct}% Trades ${best.trades}`
    : "No reviewable fast candidate";
  return `
    <h3 class="modal-section-title">Fast Candidate Discovery <span class="neutral">${escapeHtml(recommendation.action || "NO_ACTION")}</span></h3>
    <p class="modal-note"><strong>${escapeHtml(bestText)}</strong> ${escapeHtml(recommendation.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Symbols</span><strong>${escapeHtml((search.symbols || []).join(", ") || "-")}</strong></div>
      <div class="metric"><span>Timeframes</span><strong>${escapeHtml((search.timeframes || []).join(", ") || "-")}</strong></div>
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(search.strategy || "-")}</strong></div>
      <div class="metric"><span>Max combos</span><strong>${search.maxCombos ?? "-"}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Market</th><th>Quality</th><th>Trades</th><th>PF</th><th>Return</th><th>DD</th><th>Win</th><th>Score</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="8">No discovery rows returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadResearchCandidateLeaderboard() {
  const host = document.querySelector("#research-candidate-leaderboard-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading research candidate leaderboard...</p>`;
    const payload = await apiGet("/api/research/candidate-leaderboard");
    host.innerHTML = renderResearchCandidateLeaderboard(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Research candidate leaderboard could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderResearchCandidateLeaderboard(payload) {
  const search = payload.search || {};
  const summary = payload.summary || {};
  const recommendation = summary.recommendation || {};
  const best = summary.bestOverall || {};
  const active = summary.activeCandidate || {};
  const rows = (payload.rows || []).slice(0, 12).map((row) => {
    const tone = row.status === "PASS" ? "positive" : row.status === "WARN" ? "neutral" : "negative";
    const activeFlag = row.isActivePaperCandidate ? "yes" : "";
    return `
      <tr>
        <td>${row.rank ?? "-"}</td>
        <td>${escapeHtml(row.strategy || "-")}</td>
        <td>${escapeHtml(row.symbol || "-")} ${escapeHtml(row.timeframe || "-")}</td>
        <td class="${tone}">${escapeHtml(row.status || "-")}</td>
        <td>${activeFlag}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.tradesPerMonth)}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatNumber(row.score)}</td>
        <td>${escapeHtml(row.mainFailureReason || "-")}</td>
      </tr>
    `;
  }).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const bestText = best.symbol ? `${best.strategy} ${best.symbol} ${best.timeframe} PF ${best.profitFactor} Return ${best.totalReturnPct}%` : "-";
  return `
    <h3 class="modal-section-title">Research Candidate Leaderboard <span class="neutral">${escapeHtml(recommendation.action || "NO_ACTION")}</span></h3>
    <p class="modal-note"><strong>Best:</strong> ${escapeHtml(bestText)}. ${escapeHtml(recommendation.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Active rank</span><strong>${summary.activeCandidateRank ?? "-"}</strong></div>
      <div class="metric"><span>PASS/WARN</span><strong>${summary.passCount ?? 0}</strong></div>
      <div class="metric"><span>FAIL/ERROR</span><strong>${summary.failCount ?? 0}</strong></div>
      <div class="metric"><span>Rows</span><strong>${payload.rows?.length || 0}</strong></div>
      <div class="metric"><span>Symbols</span><strong>${escapeHtml((search.symbols || []).join(", ") || "-")}</strong></div>
      <div class="metric"><span>Timeframes</span><strong>${escapeHtml((search.timeframes || []).join(", ") || "-")}</strong></div>
      <div class="metric"><span>Active</span><strong>${active.symbol ? `${escapeHtml(active.symbol)} ${escapeHtml(active.timeframe || "")}` : "-"}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Rank</th><th>Strategy</th><th>Market</th><th>Status</th><th>Active</th><th>Trades</th><th>/Month</th><th>PF</th><th>Return</th><th>DD</th><th>Score</th><th>Reason</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="12">No leaderboard rows returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadResearchFeeSlippageStress() {
  const host = document.querySelector("#research-fee-slippage-stress-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading fee/slippage stress lab...</p>`;
    const payload = await apiGet("/api/research/fee-slippage-stress");
    host.innerHTML = renderResearchFeeSlippageStress(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Fee/slippage stress lab could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderResearchFeeSlippageStress(payload) {
  const search = payload.search || {};
  const stress = payload.stress || {};
  const rec = stress.recommendation || {};
  const base = (payload.rows || []).find((row) => row.scenario === "baseline") || {};
  const worst = stress.worstPassingScenario || {};
  const firstFail = stress.firstFailureScenario || {};
  const tone = stress.status === "RESILIENT" ? "positive" : stress.status === "FAIL" || stress.status === "FRAGILE" ? "negative" : "neutral";
  const rows = (payload.rows || []).map((row) => {
    const rowTone = row.status === "PASS" ? "positive" : row.status === "WARN" ? "neutral" : "negative";
    const degrade = row.degradationVsBaseline || {};
    return `
      <tr>
        <td>${escapeHtml(row.scenario || "-")}</td>
        <td class="${rowTone}">${escapeHtml(row.status || "-")}</td>
        <td>${formatNumber(row.takerFeePct)}%</td>
        <td>${formatNumber(row.slippageBps)}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatSigned(degrade.returnDiffPct || 0)}%</td>
        <td>${formatSigned(degrade.profitFactorDiff || 0)}</td>
        <td>${escapeHtml(row.mainFailureReason || "-")}</td>
      </tr>
    `;
  }).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Fee/Slippage Stress Lab <span class="${tone}">${escapeHtml(stress.status || "UNKNOWN")}</span></h3>
    <p class="modal-note"><strong>${escapeHtml(rec.action || "-")}</strong> ${escapeHtml(rec.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Market</span><strong>${escapeHtml(search.symbol || "-")} ${escapeHtml(search.timeframe || "-")}</strong></div>
      <div class="metric"><span>Baseline</span><strong>${base.status ? `${escapeHtml(base.status)} PF ${formatNumber(base.profitFactor)}` : "-"}</strong></div>
      <div class="metric"><span>Worst pass</span><strong>${worst.scenario ? `${escapeHtml(worst.scenario)} PF ${formatNumber(worst.profitFactor)}` : "-"}</strong></div>
      <div class="metric"><span>First failure</span><strong>${firstFail.scenario ? `${escapeHtml(firstFail.scenario)} ${escapeHtml(firstFail.mainFailureReason || "")}` : "-"}</strong></div>
      <div class="metric"><span>Survived</span><strong>${(stress.survivingScenarios || []).length}</strong></div>
      <div class="metric"><span>Failed</span><strong>${(stress.failedScenarios || []).length}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Scenario</th><th>Status</th><th>Taker fee</th><th>Slip bps</th><th>Trades</th><th>PF</th><th>Return</th><th>DD</th><th>Return diff</th><th>PF diff</th><th>Reason</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="11">No fee/slippage stress rows returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadResearchWalkForwardReview() {
  const host = document.querySelector("#research-walk-forward-review-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading walk-forward review...</p>`;
    const payload = await apiGet("/api/research/walk-forward-review");
    host.innerHTML = renderResearchWalkForwardReview(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Walk-forward review could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderResearchWalkForwardReview(payload) {
  const search = payload.search || {};
  const full = payload.full || {};
  const stability = payload.stability || {};
  const rec = stability.recommendation || {};
  const worst = stability.worstFold || {};
  const best = stability.bestFold || {};
  const tone = stability.status === "STABLE" ? "positive" : stability.status === "FAIL" || stability.status === "FRAGILE" ? "negative" : "neutral";
  const windowRows = (payload.recentWindows || []).map((row) => {
    const rowTone = row.status === "PASS" ? "positive" : row.status === "WARN" ? "neutral" : "negative";
    return `
      <tr>
        <td>${escapeHtml(row.label || "-")}</td>
        <td class="${rowTone}">${escapeHtml(row.status || "-")}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatNumber(row.winRate)}%</td>
        <td>${escapeHtml(row.mainFailureReason || "-")}</td>
      </tr>
    `;
  }).join("");
  const foldRows = (payload.folds || []).map((row) => {
    const rowTone = row.status === "PASS" ? "positive" : row.status === "WARN" ? "neutral" : "negative";
    return `
      <tr>
        <td>${row.fold ?? "-"}</td>
        <td class="${rowTone}">${escapeHtml(row.status || "-")}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${escapeHtml((row.startTime || "").slice(0, 10))}</td>
        <td>${escapeHtml((row.endTime || "").slice(0, 10))}</td>
        <td>${escapeHtml(row.mainFailureReason || "-")}</td>
      </tr>
    `;
  }).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Walk-Forward Review <span class="${tone}">${escapeHtml(stability.status || "UNKNOWN")}</span></h3>
    <p class="modal-note"><strong>${escapeHtml(rec.action || "-")}</strong> ${escapeHtml(rec.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Market</span><strong>${escapeHtml(search.symbol || "-")} ${escapeHtml(search.timeframe || "-")}</strong></div>
      <div class="metric"><span>Full</span><strong>${escapeHtml(full.status || "-")} PF ${formatNumber(full.profitFactor)}</strong></div>
      <div class="metric"><span>Full return</span><strong>${formatSigned(full.totalReturnPct || 0)}%</strong></div>
      <div class="metric"><span>Pass/fail folds</span><strong>${stability.passFoldCount ?? 0}/${stability.failFoldCount ?? 0}</strong></div>
      <div class="metric"><span>Negative folds</span><strong>${stability.negativeFoldCount ?? 0}</strong></div>
      <div class="metric"><span>Median fold PF</span><strong>${formatNumber(stability.medianFoldProfitFactor)}</strong></div>
      <div class="metric"><span>Worst fold</span><strong>${worst.fold ? `#${worst.fold} ${formatSigned(worst.totalReturnPct || 0)}%` : "-"}</strong></div>
      <div class="metric"><span>Best fold</span><strong>${best.fold ? `#${best.fold} ${formatSigned(best.totalReturnPct || 0)}%` : "-"}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Window</th><th>Status</th><th>Trades</th><th>PF</th><th>Return</th><th>DD</th><th>Win</th><th>Reason</th></tr></thead>
      <tbody>${windowRows || `<tr><td colspan="8">No recent windows returned.</td></tr>`}</tbody>
    </table>
    <table class="trade-table">
      <thead><tr><th>Fold</th><th>Status</th><th>Trades</th><th>PF</th><th>Return</th><th>DD</th><th>Start</th><th>End</th><th>Reason</th></tr></thead>
      <tbody>${foldRows || `<tr><td colspan="9">No folds returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadResearchActivityLab() {
  const host = document.querySelector("#research-activity-lab-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading backtest activity lab...</p>`;
    const payload = await apiGet("/api/research/activity-lab");
    host.innerHTML = renderResearchActivityLab(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Backtest activity lab could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderResearchActivityLab(payload) {
  const search = payload.search || {};
  const active = payload.activePaperCandidate || {};
  const activeMarket = (active.activeSymbols || [])[0] || {};
  const summary = payload.summary || {};
  const recommendation = summary.recommendation || {};
  const rows = (payload.rows || []).map((row) => {
    const tone = row.status === "PASS" ? "positive" : row.status === "WARN" ? "neutral" : "negative";
    return `
      <tr>
        <td>${escapeHtml(row.strategy || "-")}</td>
        <td>${escapeHtml(row.symbol || "-")} ${escapeHtml(row.timeframe || "-")}</td>
        <td>${escapeHtml(row.mode || "-")}</td>
        <td class="${tone}">${escapeHtml(row.status || "-")}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.tradesPerMonth)}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatNumber(row.expectancyPctPerTrade)}%</td>
        <td>${escapeHtml(row.mainFailureReason || "-")}</td>
      </tr>
    `;
  }).join("");
  const best15 = summary.best15m || {};
  const best1h = summary.best1h || {};
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Backtest Activity Lab <span class="neutral">${escapeHtml(recommendation.action || "NO_ACTION")}</span></h3>
    <p class="modal-note"><strong>Active:</strong> ${escapeHtml(active.strategy || "-")} ${escapeHtml(activeMarket.symbol || "-")} ${escapeHtml(activeMarket.interval || "")}. ${escapeHtml(recommendation.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Mode</span><strong>${search.optimize ? "optimized" : "current params"}</strong></div>
      <div class="metric"><span>Symbols</span><strong>${escapeHtml((search.symbols || []).join(", ") || "-")}</strong></div>
      <div class="metric"><span>Timeframes</span><strong>${escapeHtml((search.timeframes || []).join(", ") || "-")}</strong></div>
      <div class="metric"><span>Strategies</span><strong>${escapeHtml((search.strategies || []).join(", ") || "-")}</strong></div>
      <div class="metric"><span>Fee/slip</span><strong>${formatNumber(search.feePct)}% / ${formatNumber(search.slippagePct)}%</strong></div>
      <div class="metric"><span>Rows</span><strong>${payload.rows?.length || 0}</strong></div>
      <div class="metric"><span>Best 15m</span><strong>${best15.symbol ? `${escapeHtml(best15.symbol)} ${formatNumber(best15.tradesPerMonth)}/mo` : "-"}</strong></div>
      <div class="metric"><span>Best 1h</span><strong>${best1h.symbol ? `${escapeHtml(best1h.symbol)} ${formatNumber(best1h.tradesPerMonth)}/mo` : "-"}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Strategy</th><th>Market</th><th>Mode</th><th>Status</th><th>Trades</th><th>/Month</th><th>PF</th><th>Return</th><th>DD</th><th>Expectancy</th><th>Reason</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="11">No activity lab rows returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadResearchParameterRobustness() {
  const host = document.querySelector("#research-parameter-robustness-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading parameter robustness lab...</p>`;
    const payload = await apiGet("/api/research/parameter-robustness");
    host.innerHTML = renderResearchParameterRobustness(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Parameter robustness lab could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderChangedParams(params) {
  const keys = Object.keys(params || {});
  if (!keys.length) return "base";
  return keys.map((key) => `${key}=${params[key]}`).join(", ");
}

function renderResearchParameterRobustness(payload) {
  const search = payload.search || {};
  const base = payload.baseResult || {};
  const robust = payload.robustness || {};
  const recommendation = robust.recommendation || {};
  const best = robust.bestVariant || {};
  const worst = robust.worstVariant || {};
  const tone = robust.status === "ROBUST" ? "positive" : robust.status === "FAIL" || robust.status === "FRAGILE" ? "negative" : "neutral";
  const rows = (payload.variants || []).slice(0, 30).map((row) => {
    const rowTone = row.status === "PASS" ? "positive" : row.status === "WARN" ? "neutral" : "negative";
    return `
      <tr>
        <td class="${rowTone}">${escapeHtml(row.status || "-")}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.tradesPerMonth)}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatNumber(row.expectancyPctPerTrade)}%</td>
        <td>${formatNumber(row.score)}</td>
        <td>${escapeHtml(row.mainFailureReason || "-")}</td>
        <td>${escapeHtml(renderChangedParams(row.changedParams || {}))}</td>
      </tr>
    `;
  }).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Parameter Robustness Lab <span class="${tone}">${escapeHtml(robust.status || "UNKNOWN")}</span></h3>
    <p class="modal-note"><strong>${escapeHtml(recommendation.action || "-")}</strong> ${escapeHtml(recommendation.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Market</span><strong>${escapeHtml(search.symbol || "-")} ${escapeHtml(search.timeframe || "-")}</strong></div>
      <div class="metric"><span>Strategy</span><strong>${escapeHtml(search.strategy || "-")}</strong></div>
      <div class="metric"><span>Variants</span><strong>${robust.testedVariants ?? 0}</strong></div>
      <div class="metric"><span>Pass rate</span><strong>${formatNumber((robust.passRate || 0) * 100)}%</strong></div>
      <div class="metric"><span>Median PF</span><strong>${formatNumber(robust.medianProfitFactor)}</strong></div>
      <div class="metric"><span>Median return</span><strong>${formatSigned(robust.medianReturnPct || 0)}%</strong></div>
      <div class="metric"><span>Median DD</span><strong>${formatNumber(robust.medianMaxDrawdownPct)}%</strong></div>
      <div class="metric"><span>Median trades</span><strong>${formatNumber(robust.medianTrades)}</strong></div>
      <div class="metric"><span>Base</span><strong>${escapeHtml(base.status || "-")} PF ${formatNumber(base.profitFactor)}</strong></div>
      <div class="metric"><span>Best</span><strong>${escapeHtml(best.status || "-")} PF ${formatNumber(best.profitFactor)}</strong></div>
      <div class="metric"><span>Worst</span><strong>${escapeHtml(worst.status || "-")} PF ${formatNumber(worst.profitFactor)}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Status</th><th>Trades</th><th>/Month</th><th>PF</th><th>Return</th><th>DD</th><th>Expectancy</th><th>Score</th><th>Reason</th><th>Changed Params</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="10">No robustness variants returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadResearchStrategyVariantLab() {
  const host = document.querySelector("#research-strategy-variant-lab-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading strategy variant lab...</p>`;
    const payload = await apiGet("/api/research/strategy-variant-lab");
    host.innerHTML = renderResearchStrategyVariantLab(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Strategy variant lab could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderResearchStrategyVariantLab(payload) {
  const search = payload.search || {};
  const base = payload.baseCandidate || {};
  const active = (base.activeSymbols || [])[0] || {};
  const summary = payload.summary || {};
  const recommendation = summary.recommendation || {};
  const baseline = summary.baseline || {};
  const bestTradeoff = summary.bestTradeoff || {};
  const mostActive = summary.mostActivePassing || {};
  const rows = (payload.rows || []).map((row) => {
    const tone = row.status === "PASS" ? "positive" : row.status === "WARN" || row.status === "SKIPPED" ? "neutral" : "negative";
    const blockers = (row.blockerSummary || []).map((item) => `${item.name}:${item.count}`).join(", ");
    return `
      <tr>
        <td>${escapeHtml(row.variantName || "-")}</td>
        <td class="${tone}">${escapeHtml(row.status || "-")}</td>
        <td>${row.experimental ? "yes" : "no"}</td>
        <td>${row.trades ?? 0}</td>
        <td>${formatNumber(row.tradesPerMonth)}</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.maxDrawdownPct)}%</td>
        <td>${formatNumber(row.expectancyPctPerTrade)}%</td>
        <td>${formatNumber(row.score)}</td>
        <td>${escapeHtml(row.mainFailureReason || "-")}</td>
        <td>${escapeHtml(blockers || "-")}</td>
      </tr>
    `;
  }).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Strategy Variant Lab <span class="neutral">${escapeHtml(recommendation.action || "NO_ACTION")}</span></h3>
    <p class="modal-note"><strong>Active:</strong> ${escapeHtml(base.strategy || search.baseStrategy || "-")} ${escapeHtml(active.symbol || search.symbol || "-")} ${escapeHtml(active.interval || search.timeframe || "")}. ${escapeHtml(recommendation.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Market</span><strong>${escapeHtml(search.symbol || "-")} ${escapeHtml(search.timeframe || "-")}</strong></div>
      <div class="metric"><span>Variants</span><strong>${payload.rows?.length || 0}</strong></div>
      <div class="metric"><span>Baseline</span><strong>${baseline.variantName ? `${baseline.trades ?? 0} trades PF ${formatNumber(baseline.profitFactor)}` : "-"}</strong></div>
      <div class="metric"><span>Best tradeoff</span><strong>${bestTradeoff.variantName ? `${escapeHtml(bestTradeoff.variantName)} PF ${formatNumber(bestTradeoff.profitFactor)}` : "-"}</strong></div>
      <div class="metric"><span>Most active pass</span><strong>${mostActive.variantName ? `${escapeHtml(mostActive.variantName)} ${formatNumber(mostActive.tradesPerMonth)}/mo` : "-"}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Variant</th><th>Status</th><th>Experimental</th><th>Trades</th><th>/Month</th><th>PF</th><th>Return</th><th>DD</th><th>Expectancy</th><th>Score</th><th>Reason</th><th>Top Blockers</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="12">No strategy variant rows returned.</td></tr>`}</tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
  `;
}

async function loadPaperRuntimeMonitor() {
  const host = document.querySelector("#paper-runtime-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper runtime monitor...</p>`;
    const [runtime, stopRules] = await Promise.all([
      apiGet("/api/paper/runtime-status"),
      apiGet("/api/paper/stop-rules"),
    ]);
    host.innerHTML = renderPaperRuntimeMonitor(runtime, stopRules);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper runtime monitor could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperRuntimeMonitor(runtime, stopRules) {
  const candidate = runtime.candidate || {};
  const active = (candidate.activeSymbols || [])[0] || {};
  const health = runtime.health || {};
  const journal = runtime.journal || {};
  const lastTick = runtime.lastTick || {};
  const lastSignal = runtime.lastSignal || {};
  const next = runtime.nextAction || {};
  const stopNext = stopRules.nextAction || {};
  const healthTone = health.status === "BLOCKED" ? "negative" : health.status === "WATCH" ? "neutral" : "positive";
  const stopTone = stopRules.status === "STOP_RECOMMENDED" ? "negative" : stopRules.status === "WATCH" ? "neutral" : "positive";
  const initCommand = runtime.initializationStatus === "NEEDS_INIT" ? `<p class="modal-note"><strong>Init command:</strong> npm run paper:init</p>` : "";
  const stopRows = (stopRules.rules || []).map((rule) => `
    <tr>
      <td>${escapeHtml(rule.name || "-")}</td>
      <td class="${rule.pass ? "positive" : rule.severity === "STOP" ? "negative" : "neutral"}">${rule.pass ? "yes" : "no"}</td>
      <td>${escapeHtml(rule.severity || "-")}</td>
      <td>${escapeHtml(rule.detail || "-")}</td>
    </tr>
  `).join("");
  const reasonRows = (health.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
  const activeWarningRows = (journal.activeWarnings || []).map((warning) => `<li>${escapeHtml(warning.reason || warning.message || "-")}</li>`).join("");
  const watchWarningRows = (journal.watchWarnings || []).map((warning) => `<li>${escapeHtml(warning.reason || warning.message || "-")}</li>`).join("");
  return `
    <h3 class="modal-section-title">Paper Runtime Monitor <span class="${healthTone}">${escapeHtml(health.status || "UNKNOWN")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Initialized</span><strong>${runtime.initialized ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Init status</span><strong>${escapeHtml(runtime.initializationStatus || "-")}</strong></div>
      <div class="metric"><span>Paper</span><strong>${runtime.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${runtime.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Last tick</span><strong>${escapeHtml(lastTick.updatedAt || "-")}</strong></div>
      <div class="metric"><span>Last signal</span><strong>${escapeHtml(lastSignal.processedAt || "-")}</strong></div>
      <div class="metric"><span>Stop rules</span><strong class="${stopTone}">${escapeHtml(stopRules.status || "-")}</strong></div>
      <div class="metric"><span>Active warnings</span><strong>${journal.activeWarningCount ?? (journal.activeWarnings || []).length}</strong></div>
      <div class="metric"><span>Watch warnings</span><strong>${journal.watchWarningCount ?? (journal.watchWarnings || []).length}</strong></div>
      <div class="metric"><span>Stale watch</span><strong>${journal.staleWatchWarningCount ?? (journal.staleWatchWarnings || []).length}</strong></div>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <p class="modal-note"><strong>${escapeHtml(stopNext.action || "-")}</strong> ${escapeHtml(stopNext.reason || "")}</p>
    ${initCommand}
    ${reasonRows ? `<ul class="backtest-warnings">${reasonRows}</ul>` : ""}
    ${activeWarningRows ? `<p class="modal-note"><strong>Active warnings:</strong></p><ul class="backtest-warnings">${activeWarningRows}</ul>` : ""}
    ${watchWarningRows ? `<p class="modal-note"><strong>Watch warnings:</strong> informational only.</p><ul class="backtest-warnings">${watchWarningRows}</ul>` : ""}
    <table class="trade-table">
      <thead><tr><th>Stop Rule</th><th>Pass</th><th>Severity</th><th>Detail</th></tr></thead>
      <tbody>${stopRows || `<tr><td colspan="4">No stop rules returned.</td></tr>`}</tbody>
    </table>
  `;
}

async function loadPaperTickReadiness() {
  const host = document.querySelector("#paper-tick-readiness-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper tick readiness...</p>`;
    const payload = await apiGet("/api/paper/tick-readiness");
    host.innerHTML = renderPaperTickReadiness(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper tick readiness could not load: ${escapeHtml(error.message)}</p>`;
  }
}

async function handlePaperTickReadinessAction(event) {
  const runButton = event.target.closest("[data-paper-run-once]");
  if (runButton) {
    await runPaperOnceFromPanel();
    return;
  }
  const button = event.target.closest("[data-paper-refresh-active]");
  if (!button) return;
  const host = document.querySelector("#paper-tick-readiness-panel");
  if (!host) return;
  const thenTick = button.dataset.paperRefreshActive === "tick";
  try {
    host.innerHTML = `<p class="pane-status">${thenTick ? "Refreshing active market and checking tick usefulness..." : "Refreshing active market..."}</p>`;
    const payload = await apiPost(`/api/paper/refresh-active-market${thenTick ? "?thenTick=true" : ""}`, {});
    host.innerHTML = renderPaperTickReadiness(payload.after ? {
      paperEnabled: payload.paperEnabled,
      realTradingEnabled: payload.realTradingEnabled,
      freshness: payload.after.freshness,
      tickReadiness: payload.after.tickReadiness,
      nextAction: payload.nextAction,
    } : payload) + renderPaperRefreshResult(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Active market refresh failed: ${escapeHtml(error.message)}</p>`;
  }
}

async function runPaperOnceFromPanel() {
  const host = document.querySelector("#paper-tick-readiness-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Running safe paper once...</p>`;
    const payload = await apiPost("/api/paper/run-once", {});
    await Promise.all([
      loadPaperSimulationControl(),
      loadPaperRuntimeMonitor(),
      loadActivePaperObservation(),
      loadPaperSessionMonitor(),
      loadPaperSessionEventsSummary(),
      loadPaperSessionEventsDetail(),
      loadPaperSessionTrades(),
      loadPaperObservationTargets(),
      loadPaperRunnerInstructions(),
      loadPaperRunnerSummary(),
      loadPaperObservationQuality(),
    ]);
    host.innerHTML = renderPaperTickReadiness(payload.tickReadinessAfter || payload.tickReadinessAfterRefresh || payload.tickReadinessBefore || payload) + renderPaperRunOnceResult(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper run-once failed: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperTickReadiness(payload) {
  const readiness = payload.tickReadiness || {};
  const next = payload.nextAction || {};
  const active = ((payload.freshness || {}).active || [])[0] || {};
  const status = readiness.status || "UNKNOWN";
  const tone = status === "READY" ? "positive" : status === "BLOCKED" || status === "DATA_STALE" || status === "NOT_INITIALIZED" ? "negative" : "neutral";
  const rows = [
    ...(readiness.reasons || []).map((detail) => ({ type: "Reason", detail })),
    ...(readiness.blockingWarnings || []).map((detail) => ({ type: "Active warning", detail })),
    ...(readiness.informationalWarnings || []).map((detail) => ({ type: "Watch info", detail })),
  ].map((item) => `<tr><td>${escapeHtml(item.type)}</td><td>${escapeHtml(item.detail || "-")}</td></tr>`).join("");
  return `
    <h3 class="modal-section-title">Paper Tick Readiness <span class="${tone}">${escapeHtml(status)}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Useful now</span><strong>${readiness.usefulNow ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Active market</span><strong>${active.marketKey ? escapeHtml(active.marketKey) : "-"}</strong></div>
      <div class="metric"><span>Latest candle</span><strong>${escapeHtml(active.latestCandleAt || "-")}</strong></div>
      <div class="metric"><span>Last processed</span><strong>${escapeHtml(active.lastProcessedCandleAt || "-")}</strong></div>
      <div class="metric"><span>Next closed</span><strong>${escapeHtml(readiness.nextExpectedClosedCandleTime || active.nextExpectedClosedCandleAt || "-")}</strong></div>
      <div class="metric"><span>Next useful</span><strong>${escapeHtml(readiness.nextUsefulTickAt || "-")}</strong></div>
      <div class="metric"><span>Wait</span><strong>${readiness.secondsUntilNextUsefulTick === null || readiness.secondsUntilNextUsefulTick === undefined ? "-" : formatDurationSeconds(readiness.secondsUntilNextUsefulTick)}</strong></div>
      <div class="metric"><span>Stale</span><strong>${active.isStale ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Active warn</span><strong>${(readiness.activeWarnings || []).length}</strong></div>
      <div class="metric"><span>Watch info</span><strong>${(readiness.watchWarnings || []).length}</strong></div>
      <div class="metric"><span>Command</span><strong>${escapeHtml(next.recommendedCommand || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>Active:</strong> ${escapeHtml(readiness.activeMarketReason || "-")}</p>
    <div class="button-row">
      <button type="button" data-paper-run-once="true">Run Paper Once</button>
      <button type="button" data-paper-refresh-active="refresh">Refresh Active Market</button>
      <button type="button" data-paper-refresh-active="tick">Refresh + Tick if Useful</button>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <thead><tr><th>Type</th><th>Detail</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="2">No tick readiness reasons returned.</td></tr>`}</tbody>
    </table>
  `;
}

function renderPaperRunOnceResult(payload) {
  const summary = payload.summary || {};
  const next = payload.nextAction || {};
  const refresh = payload.refresh || {};
  const tick = payload.tickResult || {};
  return `
    <h3 class="modal-section-title">Paper Run Once <span class="${payload.ok ? "positive" : "negative"}">${payload.ok ? "OK" : "CHECK"}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Tick ran</span><strong>${payload.tickRan ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Refresh</span><strong>${refresh.ok === undefined ? refresh.attempted === false ? "skipped" : "-" : refresh.ok ? "ok" : "failed"}</strong></div>
      <div class="metric"><span>Before</span><strong>${escapeHtml(summary.readinessBefore || "-")}</strong></div>
      <div class="metric"><span>After</span><strong>${escapeHtml(summary.readinessAfter || "-")}</strong></div>
      <div class="metric"><span>Stop before</span><strong>${escapeHtml(summary.stopRulesBefore || "-")}</strong></div>
      <div class="metric"><span>Stop after</span><strong>${escapeHtml(summary.stopRulesAfter || "-")}</strong></div>
      <div class="metric"><span>Targets</span><strong>${escapeHtml(summary.observationTargetStatus || payload.observationTargets?.status || "-")}</strong></div>
      <div class="metric"><span>Processed</span><strong>${summary.processedCandlesDelta ?? tick.summary?.processedCandlesDelta ?? "-"}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
  `;
}

function renderPaperRefreshResult(payload) {
  const refresh = payload.refresh || {};
  const rows = (refresh.markets || []).map((market) => `
    <tr>
      <td>${escapeHtml(market.symbol || "-")} ${escapeHtml(market.timeframe || "")}</td>
      <td>${escapeHtml(market.status || "-")}</td>
      <td>${market.candlesBefore ?? "-"}</td>
      <td>${market.candlesAfter ?? "-"}</td>
      <td>${escapeHtml(String(market.latestCandleTimeBefore ?? "-"))}</td>
      <td>${escapeHtml(String(market.latestCandleTimeAfter ?? "-"))}</td>
    </tr>
  `).join("");
  const thenTick = payload.thenTick || {};
  return `
    <h3 class="modal-section-title">Active Market Refresh <span class="${payload.ok ? "positive" : "negative"}">${payload.ok ? "OK" : "FAILED"}</span></h3>
    <p class="modal-note"><strong>${escapeHtml(payload.nextAction?.action || "-")}</strong> ${escapeHtml(payload.nextAction?.reason || "")}</p>
    ${thenTick.requested ? `<p class="modal-note"><strong>Refresh + tick:</strong> ${thenTick.ran ? "tick ran" : "tick not run"} - ${escapeHtml(thenTick.reason || "")}</p>` : ""}
    <table class="trade-table">
      <thead><tr><th>Market</th><th>Status</th><th>Candles Before</th><th>Candles After</th><th>Latest Before</th><th>Latest After</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6">No active market refresh rows returned.</td></tr>`}</tbody>
    </table>
  `;
}

async function loadActivePaperObservation() {
  const host = document.querySelector("#paper-active-observation-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading active paper observation...</p>`;
    const payload = await apiGet("/api/paper/active-observation");
    host.innerHTML = renderActivePaperObservation(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Active paper observation could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderActivePaperObservation(payload) {
  const market = payload.activeMarket || {};
  const session = payload.session || {};
  const tick = payload.tickReadiness || {};
  const signals = payload.signals || {};
  const warnings = payload.warnings || {};
  const trades = payload.trades || {};
  const targets = payload.observationTargets || {};
  const targetProgress = targets.progress || {};
  const next = payload.nextAction || {};
  const status = tick.status || targets.status || "UNKNOWN";
  const tone = status === "READY" || targets.status === "READY_FOR_PAPER_REVIEW" ? "positive" : status === "DATA_STALE" || targets.status === "PAUSE_RECOMMENDED" ? "negative" : "neutral";
  const latestSignal = signals.latest || {};
  const latestWarning = warnings.latest || {};
  return `
    <h3 class="modal-section-title">Active Paper Observation <span class="${tone}">${escapeHtml(status)}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Active market</span><strong>${escapeHtml(market.marketKey || `${market.symbol || "-"}:${market.timeframe || "-"}`)}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Session</span><strong>${escapeHtml(session.status || "-")}</strong></div>
      <div class="metric"><span>Tick readiness</span><strong>${escapeHtml(tick.status || "-")}</strong></div>
      <div class="metric"><span>Useful now</span><strong>${tick.usefulNow ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Signals</span><strong>${signals.count ?? 0}</strong></div>
      <div class="metric"><span>Warnings</span><strong>${warnings.count ?? 0}</strong></div>
      <div class="metric"><span>Trade events</span><strong>${trades.tradeEventCount ?? 0}</strong></div>
      <div class="metric"><span>Open trades</span><strong>${trades.openCount ?? 0}</strong></div>
      <div class="metric"><span>Closed trades</span><strong>${trades.closedCount ?? 0}</strong></div>
      <div class="metric"><span>Target</span><strong>${escapeHtml(targets.status || "-")}</strong></div>
      <div class="metric"><span>Ticks target</span><strong>${targetProgress.ticksObserved ?? 0} / ${targetProgress.targetTicks ?? "-"}</strong></div>
      <div class="metric"><span>Closed target</span><strong>${targetProgress.closedTrades ?? 0} / ${targetProgress.targetClosedTrades ?? "-"}</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>Active:</strong> ${escapeHtml(tick.activeMarketReason || "-")}</p>
    <p class="modal-note"><strong>Latest signal:</strong> ${latestSignal.processedAt ? `${escapeHtml(latestSignal.processedAt)} ${escapeHtml(latestSignal.action || latestSignal.signal || "")} ${formatMaybeNumber(latestSignal.price)}` : "none"}</p>
    <p class="modal-note"><strong>Latest warning:</strong> ${latestWarning.reason ? escapeHtml(latestWarning.reason) : "none"}</p>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
  `;
}

async function loadPaperSessionMonitor() {
  const host = document.querySelector("#paper-session-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper session monitor...</p>`;
    const [summary, events] = await Promise.all([
      apiGet("/api/paper/session-summary"),
      apiGet("/api/paper/recent-events?limit=20"),
    ]);
    host.innerHTML = renderPaperSessionMonitor(summary, events);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper session monitor could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperSessionMonitor(summary, eventsPayload) {
  const session = summary.session || {};
  const activity = summary.activity || {};
  const performance = summary.performance || {};
  const baseline = summary.baselineComparison || {};
  const next = summary.nextAction || {};
  const status = session.status || "UNKNOWN";
  const tone = status === "RUNNING" ? "positive" : status === "STOP_RECOMMENDED" ? "negative" : "neutral";
  const rows = (eventsPayload.events || []).slice().reverse().map((event) => `
    <tr>
      <td>${escapeHtml(event.timestamp || "-")}</td>
      <td>${escapeHtml(event.eventType || "-")}</td>
      <td>${escapeHtml(event.symbol || "-")} ${escapeHtml(event.interval || "")}</td>
      <td>${event.currentSession ? "yes" : "no"}</td>
      <td>${event.stale ? "yes" : "no"}</td>
      <td>${escapeHtml(event.reason || event.message || "-")}</td>
    </tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Paper Session Monitor <span class="${tone}">${escapeHtml(status)}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${summary.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Duration</span><strong>${escapeHtml(formatDurationSeconds(session.durationSeconds || 0))}</strong></div>
      <div class="metric"><span>Ticks</span><strong>${activity.ticks ?? 0}</strong></div>
      <div class="metric"><span>Processed</span><strong>${activity.processedCandles ?? 0}</strong></div>
      <div class="metric"><span>Signals</span><strong>${activity.signals ?? 0}</strong></div>
      <div class="metric"><span>Open</span><strong>${activity.openPositions ?? 0}</strong></div>
      <div class="metric"><span>Closed</span><strong>${activity.closedTrades ?? 0}</strong></div>
      <div class="metric"><span>Realized</span><strong>${formatSigned(performance.realizedPnl || 0)}</strong></div>
      <div class="metric"><span>Unrealized</span><strong>${formatSigned(performance.unrealizedPnl || 0)}</strong></div>
      <div class="metric"><span>Return</span><strong>${formatMaybeNumber(performance.returnPct)}%</strong></div>
      <div class="metric"><span>Baseline</span><strong>${escapeHtml(baseline.status || "-")}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${summary.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
    </div>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <thead><tr><th>Time</th><th>Type</th><th>Market</th><th>Session</th><th>Stale</th><th>Message</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6">No paper events available.</td></tr>`}</tbody>
    </table>
    ${(summary.warnings || []).length ? `<ul class="backtest-warnings">${summary.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

async function loadPaperSessionEventsSummary() {
  const host = document.querySelector("#paper-session-events-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper session events summary...</p>`;
    const payload = await apiGet("/api/paper/session-events-summary");
    host.innerHTML = renderPaperSessionEventsSummary(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper session events summary could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperSessionEventsSummary(payload) {
  const counts = payload.counts || {};
  const session = payload.session || {};
  const next = payload.nextAction || {};
  const tone = counts.currentSessionEvents > 0 ? "positive" : "neutral";
  const warningRows = (payload.recentWarnings || payload.warnings || []).slice(-10).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const eventRows = (payload.recentEvents || []).slice().reverse().map((event) => `
    <tr>
      <td>${escapeHtml(event.timestamp || "-")}</td>
      <td>${escapeHtml(event.eventType || "-")}</td>
      <td>${escapeHtml(event.marketKey || "-")}</td>
      <td>${event.stale ? "yes" : "no"}</td>
      <td>${escapeHtml(event.reason || event.message || "-")}</td>
    </tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Paper Session Events Summary <span class="${tone}">${counts.currentSessionEvents ?? 0} events</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Signals</span><strong>${counts.signals ?? 0}</strong></div>
      <div class="metric"><span>Warnings</span><strong>${counts.warnings ?? 0}</strong></div>
      <div class="metric"><span>State warnings</span><strong>${counts.stateWarnings ?? 0}</strong></div>
      <div class="metric"><span>Opened</span><strong>${counts.openedVirtualTrades ?? 0}</strong></div>
      <div class="metric"><span>Closed</span><strong>${counts.closedVirtualTrades ?? 0}</strong></div>
      <div class="metric"><span>Session events</span><strong>${counts.currentSessionEvents ?? 0}</strong></div>
      <div class="metric"><span>Stale events</span><strong>${counts.staleEvents ?? 0}</strong></div>
      <div class="metric"><span>Active events</span><strong>${counts.activeMarketEvents ?? 0}</strong></div>
      <div class="metric"><span>Watch events</span><strong>${counts.watchMarketEvents ?? 0}</strong></div>
      <div class="metric"><span>Latest</span><strong>${escapeHtml(payload.latestEventTime || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>Session:</strong> ${escapeHtml(session.startedAt || "-")} to ${escapeHtml(session.endedAt || "running")}</p>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    ${warningRows ? `<ul class="backtest-warnings">${warningRows}</ul>` : ""}
    <table class="trade-table">
      <thead><tr><th>Time</th><th>Type</th><th>Market</th><th>Stale</th><th>Message</th></tr></thead>
      <tbody>${eventRows || `<tr><td colspan="5">No current-session events returned.</td></tr>`}</tbody>
    </table>
  `;
}

async function loadPaperSessionEventsDetail() {
  const host = document.querySelector("#paper-session-events-detail-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper session events...</p>`;
    const payload = await apiGet("/api/paper/session-events?limit=10&currentSession=all");
    host.innerHTML = renderPaperSessionEventsDetail(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper session events could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperSessionEventsDetail(payload) {
  const counts = payload.counts || {};
  const filters = payload.filters || {};
  const rows = (payload.events || []).slice().reverse().map((event) => `
    <tr>
      <td>${escapeHtml(event.processedAt || "-")}</td>
      <td>${escapeHtml(event.eventType || "-")}</td>
      <td>${escapeHtml(event.marketKey || "-")}</td>
      <td>${escapeHtml(event.marketRole || "-")}</td>
      <td>${event.currentSession ? "yes" : "no"}</td>
      <td>${event.stale ? "yes" : "no"}</td>
      <td>${escapeHtml(event.action || event.signal || "-")}</td>
      <td>${formatMaybeNumber(event.price)}</td>
      <td>${escapeHtml(event.reason || "-")}</td>
    </tr>
  `).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Paper Session Events <span class="neutral">${(payload.events || []).length} shown</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Signals</span><strong>${counts.signals ?? 0}</strong></div>
      <div class="metric"><span>Warnings</span><strong>${counts.warnings ?? 0}</strong></div>
      <div class="metric"><span>State warnings</span><strong>${counts.stateWarnings ?? 0}</strong></div>
      <div class="metric"><span>Open events</span><strong>${counts.openTrades ?? 0}</strong></div>
      <div class="metric"><span>Close events</span><strong>${counts.closeTrades ?? 0}</strong></div>
      <div class="metric"><span>Active</span><strong>${counts.active ?? 0}</strong></div>
      <div class="metric"><span>Watch</span><strong>${counts.watch ?? 0}</strong></div>
      <div class="metric"><span>Current</span><strong>${counts.currentSession ?? 0}</strong></div>
      <div class="metric"><span>Stale</span><strong>${counts.stale ?? 0}</strong></div>
      <div class="metric"><span>Filter</span><strong>${escapeHtml(`${filters.type || "all"} / ${filters.market || "all"}`)}</strong></div>
    </div>
    <p class="modal-note"><strong>Session:</strong> ${escapeHtml(payload.sessionStartedAt || "-")} to ${escapeHtml(payload.sessionEndedAt || "running")}</p>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
    <table class="trade-table">
      <thead><tr><th>Time</th><th>Type</th><th>Market</th><th>Role</th><th>Session</th><th>Stale</th><th>Action</th><th>Price</th><th>Reason</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="9">No paper session events match the current filters.</td></tr>`}</tbody>
    </table>
  `;
}

async function loadPaperSessionTrades() {
  const host = document.querySelector("#paper-session-trades-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper session trades...</p>`;
    const payload = await apiGet("/api/paper/session-trades");
    host.innerHTML = renderPaperSessionTrades(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper session trades could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperSessionTrades(payload) {
  const totals = payload.totals || {};
  const tradeRows = (payload.recentTradeEvents || []).slice(-10).reverse().map((event) => `
    <tr>
      <td>${escapeHtml(event.processedAt || "-")}</td>
      <td>${escapeHtml(event.eventType || "-")}</td>
      <td>${escapeHtml(event.marketKey || "-")}</td>
      <td>${escapeHtml(event.side || "-")}</td>
      <td>${formatMaybeNumber(event.price)}</td>
      <td>${formatMaybeNumber(event.pnl)}</td>
      <td>${event.currentSession ? "yes" : "no"}</td>
      <td>${escapeHtml(event.reason || "-")}</td>
    </tr>
  `).join("");
  const openRows = (payload.openTrades || []).slice(-10).map((trade) => `
    <tr><td>${escapeHtml(trade.tradeId || "-")}</td><td>${escapeHtml(trade.symbol || "-")}</td><td>${escapeHtml(trade.side || "-")}</td><td>${formatMaybeNumber(trade.entryPrice)}</td><td>${formatMaybeNumber(trade.size)}</td><td>${escapeHtml(trade.openedAt || "-")}</td></tr>
  `).join("");
  const closedRows = (payload.closedTrades || []).slice(-10).reverse().map((trade) => `
    <tr><td>${escapeHtml(trade.tradeId || "-")}</td><td>${escapeHtml(trade.symbol || "-")}</td><td>${formatMaybeNumber(trade.entryPrice)}</td><td>${formatMaybeNumber(trade.exitPrice)}</td><td>${formatMaybeNumber(trade.pnl)}</td><td>${escapeHtml(trade.closedAt || "-")}</td></tr>
  `).join("");
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Paper Session Trades <span class="neutral">${totals.closedTrades ?? 0} closed</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Open trades</span><strong>${(payload.openTrades || []).length}</strong></div>
      <div class="metric"><span>Closed trades</span><strong>${totals.closedTrades ?? 0}</strong></div>
      <div class="metric"><span>Trade events</span><strong>${totals.recentTradeEvents ?? 0}</strong></div>
      <div class="metric"><span>Session trade events</span><strong>${totals.currentSessionTradeEvents ?? 0}</strong></div>
      <div class="metric"><span>Realized</span><strong>${formatSigned(totals.realizedPnl || 0)}</strong></div>
      <div class="metric"><span>Fees</span><strong>${formatMaybeNumber(totals.fees)}</strong></div>
      <div class="metric"><span>Win rate</span><strong>${formatMaybeNumber(totals.winRate)}%</strong></div>
      <div class="metric"><span>Avg win</span><strong>${formatMaybeNumber(totals.avgWin)}</strong></div>
      <div class="metric"><span>Avg loss</span><strong>${formatMaybeNumber(totals.avgLoss)}</strong></div>
    </div>
    <p class="modal-note"><strong>Session:</strong> ${escapeHtml(payload.sessionStartedAt || "-")} to ${escapeHtml(payload.sessionEndedAt || "running")}</p>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
    <h3 class="modal-section-title">Latest Trade Events</h3>
    <table class="trade-table">
      <thead><tr><th>Time</th><th>Type</th><th>Market</th><th>Side</th><th>Price</th><th>PnL</th><th>Session</th><th>Reason</th></tr></thead>
      <tbody>${tradeRows || `<tr><td colspan="8">No virtual trade events available yet.</td></tr>`}</tbody>
    </table>
    <h3 class="modal-section-title">Open Virtual Trades</h3>
    <table class="trade-table"><thead><tr><th>ID</th><th>Symbol</th><th>Side</th><th>Entry</th><th>Size</th><th>Opened</th></tr></thead><tbody>${openRows || `<tr><td colspan="6">No open virtual trades.</td></tr>`}</tbody></table>
    <h3 class="modal-section-title">Closed Virtual Trades</h3>
    <table class="trade-table"><thead><tr><th>ID</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Closed</th></tr></thead><tbody>${closedRows || `<tr><td colspan="6">No closed virtual trades.</td></tr>`}</tbody></table>
  `;
}

async function loadPaperObservationCounters() {
  const host = document.querySelector("#paper-observation-counters-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper observation counters...</p>`;
    const payload = await apiGet("/api/paper/observation-counters");
    host.innerHTML = renderPaperObservationCounters(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper observation counters could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperObservationCounters(payload) {
  const sources = payload.counterSources || {};
  const runnerSource = sources.runnerLog || {};
  const runner = payload.runnerCounters || {};
  const session = payload.sessionCounters || {};
  const active = payload.activeMarketCounters || {};
  const consistency = payload.consistency || {};
  const tone = consistency.status === "OK" ? "positive" : "neutral";
  const warningRows = (consistency.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Paper Observation Counters <span class="${tone}">${escapeHtml(consistency.status || "UNKNOWN")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Runner iterations</span><strong>${runner.iterations ?? 0}</strong></div>
      <div class="metric"><span>Runner ticks run</span><strong>${runner.ticksRun ?? 0}</strong></div>
      <div class="metric"><span>Runner ticks skipped</span><strong>${runner.ticksSkipped ?? 0}</strong></div>
      <div class="metric"><span>Runner errors</span><strong>${runner.errors ?? 0}</strong></div>
      <div class="metric"><span>Processed delta</span><strong>${runner.processedCandleDeltaTotal ?? 0}</strong></div>
      <div class="metric"><span>Session paper ticks</span><strong>${session.paperTicks ?? "-"}</strong></div>
      <div class="metric"><span>Session signals</span><strong>${session.signals ?? 0}</strong></div>
      <div class="metric"><span>Closed trades</span><strong>${session.closedTrades ?? 0}</strong></div>
      <div class="metric"><span>Open positions</span><strong>${session.openPositions ?? 0}</strong></div>
      <div class="metric"><span>Trade events</span><strong>${session.currentSessionTradeEvents ?? 0}</strong></div>
      <div class="metric"><span>Active market</span><strong>${escapeHtml(active.marketKey || `${active.symbol || "-"}:${active.timeframe || "-"}`)}</strong></div>
      <div class="metric"><span>Active signals</span><strong>${active.signals ?? 0}</strong></div>
      <div class="metric"><span>Active warnings</span><strong>${active.warnings ?? 0}</strong></div>
      <div class="metric"><span>Active candle count</span><strong>${active.processedCandleCount ?? "-"}</strong></div>
    </div>
    <p class="modal-note"><strong>Runner log:</strong> ${escapeHtml(runnerSource.path || "-")} (${escapeHtml(runnerSource.selectedBy || "-")}, ${runnerSource.entriesRead ?? 0} entries)</p>
    <p class="modal-note"><strong>Active candle count:</strong> ${escapeHtml(active.processedCandleCountExplanation || "No explanation returned.")}</p>
    ${warningRows ? `<ul class="backtest-warnings">${warningRows}</ul>` : ""}
  `;
}

async function loadPaperObservationTargets() {
  const host = document.querySelector("#paper-observation-targets-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper observation targets...</p>`;
    const payload = await apiGet("/api/paper/observation-targets");
    host.innerHTML = renderPaperObservationTargets(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper observation targets could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperObservationTargets(payload) {
  const candidate = payload.candidate || {};
  const active = (candidate.activeSymbols || [])[0] || {};
  const targets = payload.targets || {};
  const progress = payload.progress || {};
  const readiness = payload.readiness || {};
  const next = payload.nextAction || {};
  const status = payload.status || "UNKNOWN";
  const tone = status === "READY_FOR_PAPER_REVIEW" ? "positive" : status === "PAUSE_RECOMMENDED" ? "negative" : "neutral";
  const rows = [
    ...(payload.blockingIssues || []).map((detail) => ({ type: "Blocking", detail })),
    ...(payload.warnings || []).map((detail) => ({ type: "Warning", detail })),
    ...(payload.informationalWarnings || []).map((detail) => ({ type: "Info", detail })),
  ].map((item) => `<tr><td>${escapeHtml(item.type)}</td><td>${escapeHtml(item.detail || "-")}</td></tr>`).join("");
  return `
    <h3 class="modal-section-title">Paper Observation Targets <span class="${tone}">${escapeHtml(status)}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Session age</span><strong>${formatMaybeNumber(progress.sessionAgeHours)} / ${formatMaybeNumber(targets.minSessionHours)}h</strong></div>
      <div class="metric"><span>Ticks</span><strong>${progress.ticksObserved ?? 0} / ${targets.minPaperTicks ?? "-"}</strong></div>
      <div class="metric"><span>Closed trades</span><strong>${progress.closedTrades ?? 0} / ${targets.minClosedTrades ?? "-"}</strong></div>
      <div class="metric"><span>Preferred trades</span><strong>${progress.closedTrades ?? 0} / ${targets.preferredClosedTrades ?? "-"}</strong></div>
      <div class="metric"><span>Signals</span><strong>${progress.signalsObserved ?? 0}</strong></div>
      <div class="metric"><span>Open</span><strong>${progress.openPositions ?? 0}</strong></div>
      <div class="metric"><span>Active warnings</span><strong>${progress.activeWarningCount ?? 0}</strong></div>
      <div class="metric"><span>Watch warnings</span><strong>${progress.watchWarningCount ?? 0}</strong></div>
      <div class="metric"><span>Quality</span><strong>${escapeHtml(progress.observationQualityStatus || "-")}</strong></div>
      <div class="metric"><span>Minimum met</span><strong>${readiness.minimumTargetsMet ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Meaningful</span><strong>${readiness.meaningfulEvidence ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>Active market:</strong> ${escapeHtml(progress.activeMarket || "-")} latest ${escapeHtml(progress.latestClosedCandleTime || "-")} processed ${escapeHtml(progress.lastProcessedCandleTime || "-")}</p>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <thead><tr><th>Type</th><th>Detail</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="2">No observation target issues returned.</td></tr>`}</tbody>
    </table>
  `;
}

async function loadPaperRunnerInstructions() {
  const host = document.querySelector("#paper-runner-instructions-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper runner instructions...</p>`;
    const payload = await apiGet("/api/paper/runner-instructions");
    host.innerHTML = renderPaperRunnerInstructions(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper runner instructions could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperRunnerInstructions(payload) {
  const candidate = payload.candidate || {};
  const active = (candidate.activeSymbols || [])[0] || {};
  const targets = payload.observationTargets || {};
  const progress = targets.progress || {};
  const next = payload.nextAction || {};
  const tone = targets.status === "READY_FOR_PAPER_REVIEW" ? "positive" : targets.status === "PAUSE_RECOMMENDED" ? "negative" : "neutral";
  const warnings = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  const notes = (payload.notes || []).map((note) => `<li>${escapeHtml(note)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Paper Runner Instructions <span class="${tone}">${escapeHtml(targets.status || "UNKNOWN")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Target status</span><strong>${escapeHtml(targets.status || "-")}</strong></div>
      <div class="metric"><span>Ticks</span><strong>${progress.ticksObserved ?? 0} / ${progress.targetTicks ?? "-"}</strong></div>
      <div class="metric"><span>Closed trades</span><strong>${progress.closedTrades ?? 0} / ${progress.targetClosedTrades ?? "-"}</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <table class="trade-table">
      <tbody>
        <tr><th>One-shot command</th><td><code>${escapeHtml(payload.oneShotCommand || "-")}</code></td></tr>
        <tr><th>Loop command</th><td><code>${escapeHtml(payload.loopCommand || "-")}</code></td></tr>
        <tr><th>Next action</th><td><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</td></tr>
      </tbody>
    </table>
    ${warnings ? `<ul class="backtest-warnings">${warnings}</ul>` : ""}
    ${notes ? `<ul class="modal-note-list">${notes}</ul>` : ""}
  `;
}

async function loadPaperRunnerSummary() {
  const host = document.querySelector("#paper-runner-summary-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper runner summary...</p>`;
    const payload = await apiGet("/api/paper/runner-summary");
    host.innerHTML = renderPaperRunnerSummary(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper runner summary could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperRunnerSummary(payload) {
  const counts = payload.counts || {};
  const latest = payload.latestIteration || {};
  const latestSummary = payload.latestSummary || {};
  const next = payload.nextAction || {};
  const tone = payload.exists ? counts.errors > 0 ? "negative" : counts.iterations > 0 ? "positive" : "neutral" : "neutral";
  const skipRows = (payload.recentSkipReasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join("");
  const actionRows = (payload.recentActions || []).slice().reverse().map((item) => `
    <tr>
      <td>${item.iteration ?? "-"}</td>
      <td>${escapeHtml(item.timestamp || "-")}</td>
      <td>${escapeHtml(item.action || "-")}</td>
      <td>${escapeHtml(item.reason || "-")}</td>
    </tr>
  `).join("");
  const warningRows = (payload.warnings || []).map((warning) => `<li>${escapeHtml(warning)}</li>`).join("");
  return `
    <h3 class="modal-section-title">Paper Runner Summary <span class="${tone}">${payload.exists ? `${counts.iterations ?? 0} iterations` : "no log"}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Entries read</span><strong>${payload.entriesRead ?? 0}</strong></div>
      <div class="metric"><span>Iterations</span><strong>${counts.iterations ?? 0}</strong></div>
      <div class="metric"><span>Summaries</span><strong>${counts.summaries ?? 0}</strong></div>
      <div class="metric"><span>Ticks run</span><strong>${counts.ticksRun ?? 0}</strong></div>
      <div class="metric"><span>Ticks skipped</span><strong>${counts.ticksSkipped ?? 0}</strong></div>
      <div class="metric"><span>Errors</span><strong>${counts.errors ?? 0}</strong></div>
      <div class="metric"><span>Refresh OK</span><strong>${counts.refreshOk ?? 0}</strong></div>
      <div class="metric"><span>Refresh skipped</span><strong>${counts.refreshSkipped ?? 0}</strong></div>
      <div class="metric"><span>Wait candle</span><strong>${counts.waitForNextCandle ?? 0}</strong></div>
      <div class="metric"><span>Paper disabled</span><strong>${counts.paperDisabled ?? 0}</strong></div>
      <div class="metric"><span>Stop blocks</span><strong>${counts.stopRuleBlocks ?? 0}</strong></div>
      <div class="metric"><span>Latest target</span><strong>${escapeHtml(latest.observationTargetStatus || latestSummary.finalObservationTargetStatus || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>Log:</strong> ${escapeHtml(payload.logFile || "-")}</p>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    ${skipRows ? `<p class="modal-note"><strong>Recent skip reasons:</strong></p><ul class="backtest-warnings">${skipRows}</ul>` : ""}
    ${warningRows ? `<ul class="backtest-warnings">${warningRows}</ul>` : ""}
    <table class="trade-table">
      <thead><tr><th>Iteration</th><th>Time</th><th>Action</th><th>Reason</th></tr></thead>
      <tbody>${actionRows || `<tr><td colspan="4">No runner actions returned.</td></tr>`}</tbody>
    </table>
  `;
}

async function loadPaperObservationQuality() {
  const host = document.querySelector("#paper-observation-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading paper observation quality...</p>`;
    const payload = await apiGet("/api/paper/observation-quality");
    host.innerHTML = renderPaperObservationQuality(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Paper observation quality could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderPaperObservationQuality(payload) {
  const candidate = payload.candidate || {};
  const active = (candidate.activeSymbols || [])[0] || {};
  const evidence = payload.evidence || {};
  const performance = payload.performance || {};
  const baseline = payload.baseline || {};
  const quality = payload.quality || {};
  const next = payload.nextAction || {};
  const status = quality.status || "UNKNOWN";
  const tone = status === "PAUSE_RECOMMENDED" ? "negative" : status === "WATCH" ? "neutral" : status === "DISABLED" || status === "TOO_EARLY" ? "neutral" : "positive";
  const baselineSummary = baseline.available
    ? `PF ${formatMaybeNumber(baseline.expectedProfitFactor)} / Return ${formatMaybeNumber(baseline.expectedReturnPct)}% / Trades ${baseline.expectedTrades ?? "-"} / DD ${formatMaybeNumber(baseline.expectedMaxDrawdownPct)}%`
    : "No promoted baseline available";
  const reasonRows = [
    ...(quality.reasons || []).map((item) => ({ type: "Reason", detail: item })),
    ...(quality.warnings || []).map((item) => ({ type: "Warning", detail: item })),
  ].map((item) => `
    <tr><td>${escapeHtml(item.type)}</td><td>${escapeHtml(item.detail || "-")}</td></tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Paper Observation Quality <span class="${tone}">${escapeHtml(status)}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Score</span><strong>${quality.score ?? 0}</strong></div>
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(active.symbol || "")} ${escapeHtml(active.interval || "")}` : "-"}</strong></div>
      <div class="metric"><span>Paper</span><strong>${payload.paperEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Real trading</span><strong>${payload.realTradingEnabled ? "enabled" : "disabled"}</strong></div>
      <div class="metric"><span>Ticks</span><strong>${evidence.ticks ?? 0}</strong></div>
      <div class="metric"><span>Signals</span><strong>${evidence.signals ?? 0}</strong></div>
      <div class="metric"><span>Closed</span><strong>${evidence.closedTrades ?? 0}</strong></div>
      <div class="metric"><span>Open</span><strong>${evidence.openPositions ?? 0}</strong></div>
      <div class="metric"><span>Return</span><strong>${formatMaybeNumber(performance.returnPct ?? performance.paperTotalReturnPct)}%</strong></div>
      <div class="metric"><span>Drawdown</span><strong>${formatMaybeNumber(performance.maxDrawdownPct)}%</strong></div>
      <div class="metric"><span>Enough trades</span><strong>${evidence.enoughTrades ? "yes" : "no"}</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <p class="modal-note"><strong>Baseline:</strong> ${escapeHtml(baselineSummary)}</p>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || "")}</p>
    <table class="trade-table">
      <thead><tr><th>Type</th><th>Detail</th></tr></thead>
      <tbody>${reasonRows || `<tr><td colspan="2">No observation quality reasons returned.</td></tr>`}</tbody>
    </table>
  `;
}

function formatDurationSeconds(seconds) {
  const total = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

async function loadLearningEvidence() {
  const host = document.querySelector("#learning-evidence-panel");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Loading learning evidence...</p>`;
    const payload = await apiGet("/api/learning/evidence");
    host.innerHTML = renderLearningEvidence(payload);
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Learning evidence could not load: ${escapeHtml(error.message)}</p>`;
  }
}

function renderLearningEvidence(payload) {
  const candidate = payload.latestRecommendedCandidate || {};
  const repeat = payload.repeatability || {};
  const readiness = repeat.readiness || {};
  const metric = repeat.metricStability || {};
  const familyMetric = repeat.familyMetricStability || {};
  const drift = repeat.paramDrift || {};
  const churn = repeat.churn || {};
  const next = payload.nextAction || {};
  const stability = payload.candidateStability || {};
  const tone = ["READY_FOR_CONFIG_REVIEW", "FAMILY_STABLE"].includes(readiness.status) ? "positive" : readiness.status === "BLOCKED" ? "negative" : "neutral";
  const changedParams = (drift.changedParams || []).slice(0, 10).map((item) => `${escapeHtml(item.param)}: ${escapeHtml((item.values || []).map((value) => String(value)).join(" -> "))}`).join("<br>");
  const stableParams = (drift.stableParams || []).slice(0, 10).map((item) => `${escapeHtml(item.param)}: ${escapeHtml(String(item.value))}`).join("<br>");
  const appearanceRows = (repeat.recentAppearances || []).slice(-8).reverse().map((item) => `
    <tr>
      <td>${escapeHtml(formatLearningTime(item.createdAt))}</td>
      <td>${item.exactMatches || item.matches ? "exact" : item.familyMatches ? "family" : "no"}</td>
      <td>${escapeHtml(item.strategy || "-")} ${escapeHtml(item.symbol || "")} ${escapeHtml(item.timeframe || "")}</td>
      <td>${formatMaybeNumber(item.profitFactor)}</td>
      <td>${formatMaybeNumber(item.returnPct)}%</td>
      <td>${item.trades ?? "-"}</td>
    </tr>
  `).join("");
  return `
    <h3 class="modal-section-title">Learning Evidence <span class="${tone}">${escapeHtml(readiness.status || "UNKNOWN")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Candidate</span><strong>${candidate.strategy ? `${escapeHtml(candidate.strategy)} ${escapeHtml(candidate.symbol || "")} ${escapeHtml(candidate.timeframe || "")}` : "-"}</strong></div>
      <div class="metric"><span>Exact repeat</span><strong>${repeat.exactRepeatCount ?? repeat.repeatCount ?? 0}/${repeat.requiredExactRepeatCount ?? repeat.requiredRepeatCount ?? 0}</strong></div>
      <div class="metric"><span>Family repeat</span><strong>${repeat.familyRepeatCount ?? 0}/${repeat.requiredFamilyRepeatCount ?? 0}</strong></div>
      <div class="metric"><span>Reports</span><strong>${repeat.reportsConsidered || 0}</strong></div>
      <div class="metric"><span>Churn</span><strong>${formatMaybeNumber(churn.churnRatio)}</strong></div>
      <div class="metric"><span>Missing</span><strong>${readiness.missingReports || 0}</strong></div>
      <div class="metric"><span>Stability</span><strong>${escapeHtml(stability.status || "not run")}</strong></div>
      <div class="metric"><span>Param drift</span><strong>${escapeHtml(drift.driftStatus || "UNKNOWN")}</strong></div>
      <div class="metric"><span>Next</span><strong>${escapeHtml(next.action || "-")}</strong></div>
    </div>
    <p class="modal-note">Exact <code>${escapeHtml(repeat.exactCandidateKey || repeat.candidateKey || "-")}</code></p>
    <p class="modal-note">Family <code>${escapeHtml(repeat.familyCandidateKey || "-")}</code></p>
    <p class="modal-note"><strong>${escapeHtml(next.action || "-")}</strong> ${escapeHtml(next.reason || readiness.reason || "")}</p>
    <table class="trade-table">
      <tbody>
        <tr><th>PF spread</th><td>${formatMaybeNumber(metric.profitFactorMin)} - ${formatMaybeNumber(metric.profitFactorMax)} (${formatMaybeNumber(metric.profitFactorSpread)})</td></tr>
        <tr><th>Return spread</th><td>${formatMaybeNumber(metric.returnMin)}% - ${formatMaybeNumber(metric.returnMax)}% (${formatMaybeNumber(metric.returnSpread)}%)</td></tr>
        <tr><th>Family PF spread</th><td>${formatMaybeNumber(familyMetric.profitFactorMin)} - ${formatMaybeNumber(familyMetric.profitFactorMax)} (${formatMaybeNumber(familyMetric.profitFactorSpread)})</td></tr>
        <tr><th>Family return spread</th><td>${formatMaybeNumber(familyMetric.returnMin)}% - ${formatMaybeNumber(familyMetric.returnMax)}% (${formatMaybeNumber(familyMetric.returnSpread)}%)</td></tr>
        <tr><th>Max drawdown</th><td>${formatMaybeNumber(metric.drawdownMax)}%</td></tr>
        <tr><th>Trades min</th><td>${metric.tradesMin ?? "-"}</td></tr>
        <tr><th>Unique candidates</th><td>${churn.uniqueCandidates ?? "-"} exact / ${churn.uniqueFamilies ?? "-"} family / ${churn.totalRecommendations ?? "-"} total</td></tr>
        <tr><th>Drift summary</th><td>${escapeHtml(drift.summary || "-")}</td></tr>
      </tbody>
    </table>
    <h3 class="modal-section-title">Parameter Drift</h3>
    <table class="trade-table">
      <thead><tr><th>Changed Params</th><th>Stable Params</th></tr></thead>
      <tbody><tr><td>${changedParams || "-"}</td><td>${stableParams || "-"}</td></tr></tbody>
    </table>
    <h3 class="modal-section-title">Recent Appearances</h3>
    <table class="trade-table">
      <thead><tr><th>Report</th><th>Match</th><th>Candidate</th><th>PF</th><th>Return</th><th>Trades</th></tr></thead>
      <tbody>${appearanceRows || `<tr><td colspan="6">No recommendation appearances available.</td></tr>`}</tbody>
    </table>
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

function candidateMetric(candidate, section, key, fallbackKeys = []) {
  const quality = candidate?.qualityMetrics || {};
  const qualityAliases = {
    totalReturn: `${section}ReturnPct`,
    trades: `${section}Trades`,
    maxDrawdown: `${section}MaxDrawdownPct`,
    profitFactor: `${section}ProfitFactor`,
  };
  const qualityKeys = [qualityAliases[key], `${section}${key.charAt(0).toUpperCase()}${key.slice(1)}`].filter(Boolean);
  for (const qualityKey of qualityKeys) {
    if (quality[qualityKey] !== undefined && quality[qualityKey] !== null) return quality[qualityKey];
  }
  const bucket = candidate?.[section] || {};
  if (bucket[key] !== undefined && bucket[key] !== null) return bucket[key];
  for (const fallback of fallbackKeys) {
    if (candidate?.[fallback] !== undefined && candidate?.[fallback] !== null) return candidate[fallback];
  }
  return null;
}

function candidateReasonLabels(candidate) {
  const reasons = (candidate?.rejectionReasons || []).map((item) => item.label || item.code || item.reason || String(item));
  const warnings = candidate?.warnings || [];
  return [...reasons, ...warnings].filter(Boolean);
}

function candidateRobustnessFlags(candidate) {
  const trainReturn = Number(candidateMetric(candidate, "train", "totalReturn") || 0);
  const testReturn = Number(candidateMetric(candidate, "test", "totalReturn", ["totalReturnPct"]) || 0);
  const fullReturn = Number(candidateMetric(candidate, "full", "totalReturn") || 0);
  const testTrades = Number(candidateMetric(candidate, "test", "trades", ["trades"]) || 0);
  const labels = candidateReasonLabels(candidate).join(" ").toLowerCase();
  const flags = [];
  if (fullReturn < 0 || labels.includes("negative full-period return")) flags.push("Negative full-period return");
  if ((trainReturn < 0 && testReturn > 0) || labels.includes("train/test direction mismatch")) flags.push("Train/test mismatch");
  if ((testTrades >= 10 && testTrades <= 12) || labels.includes("low test-trade evidence")) flags.push("Low test-trade evidence");
  return flags;
}

function formatMaybeNumber(value) {
  return value === null || value === undefined || value === "" ? "-" : formatNumber(value);
}

function renderCandidateRobustnessRows(candidate) {
  if (!candidate || !candidate.strategy) return "";
  const trainReturn = candidateMetric(candidate, "train", "totalReturn");
  const testReturn = candidateMetric(candidate, "test", "totalReturn", ["totalReturnPct"]);
  const fullReturn = candidateMetric(candidate, "full", "totalReturn");
  const trainTestGap = candidate?.qualityMetrics?.trainTestReturnGapPct;
  const fullTrades = candidateMetric(candidate, "full", "trades");
  const testTrades = candidateMetric(candidate, "test", "trades", ["trades"]);
  const flags = candidateRobustnessFlags(candidate);
  const reasons = candidateReasonLabels(candidate);
  return `
    <tr><th>Quality</th><td>${escapeHtml(candidate.qualityStatus || "-")}</td></tr>
    <tr><th>Returns</th><td>Train ${formatMaybeNumber(trainReturn)}% / Test ${formatMaybeNumber(testReturn)}% / Full ${formatMaybeNumber(fullReturn)}%</td></tr>
    <tr><th>Trades</th><td>Test ${testTrades ?? "-"} / Full ${fullTrades ?? "-"}</td></tr>
    <tr><th>Train/test gap</th><td>${formatMaybeNumber(trainTestGap)}%</td></tr>
    ${flags.length ? `<tr><th>Robustness flags</th><td class="negative">${flags.map(escapeHtml).join("; ")}</td></tr>` : ""}
    ${reasons.length ? `<tr><th>Reasons</th><td>${reasons.map(escapeHtml).join("; ")}</td></tr>` : ""}
  `;
}

function renderLearningAudit(payload) {
  const stability = payload.candidateStability || {};
  const trend = payload.scoreTrend || {};
  const summary = payload.summary || {};
  const rec = payload.recommendation || {};
  const best = summary.bestSavedCandidate || {};
  const statusTone = payload.status === "NOT_READY" ? "negative" : payload.status === "WATCH" ? "neutral" : "positive";
  return `
    <h3 class="modal-section-title">Audit <span class="${statusTone}">${escapeHtml(payload.status || "-")}</span></h3>
    <div class="metric-grid">
      <div class="metric"><span>Robustness</span><strong>${formatNumber(summary.robustnessScore)}</strong></div>
      <div class="metric"><span>Reports</span><strong>${summary.learningReports || 0}</strong></div>
      <div class="metric"><span>Repeated</span><strong>${stability.topCandidateCount || 0}</strong></div>
      <div class="metric"><span>Churn</span><strong>${formatNumber(stability.recommendationChurn)}</strong></div>
      <div class="metric"><span>Trend</span><strong>${escapeHtml(trend.direction || "-")}</strong></div>
      <div class="metric"><span>Action</span><strong>${escapeHtml(rec.action || "-")}</strong></div>
    </div>
    <p class="modal-note">${escapeHtml(rec.reason || "")}</p>
    <table class="trade-table">
      <tbody>
        <tr><th>Latest recommendation</th><td>${escapeHtml(payload.latestRecommendation?.action || "-")}</td></tr>
        <tr><th>Previous recommendation</th><td>${escapeHtml(payload.previousRecommendation?.action || "-")}</td></tr>
        <tr><th>Top candidate key</th><td><code>${escapeHtml(stability.topCandidateKey || "-")}</code></td></tr>
        <tr><th>Latest score</th><td>${trend.latestScore === null || trend.latestScore === undefined ? "-" : formatNumber(trend.latestScore)}</td></tr>
        <tr><th>Paper health</th><td>${escapeHtml(payload.paperHealth?.status || "UNKNOWN")}</td></tr>
        ${renderCandidateRobustnessRows(best)}
      </tbody>
    </table>
    ${(payload.warnings || []).length ? `<ul class="backtest-warnings">${payload.warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("")}</ul>` : ""}
  `;
}

async function loadAutoPromoteStatus() {
  const host = document.querySelector("#learning-auto-promote-result");
  if (!host) return;
  try {
    host.innerHTML = `<p class="pane-status">Checking auto-promotion eligibility...</p>`;
    const payload = await apiGet("/api/learning/auto-promote/status");
    host.innerHTML = renderAutoPromoteResult(payload);
    loadLearningDecisions();
  } catch (error) {
    host.innerHTML = `<p class="pane-status">Auto-promote status unavailable: ${escapeHtml(error.message)}</p>`;
  }
}

async function runAutoPromote() {
  const host = document.querySelector("#learning-auto-promote-result");
  if (host) host.innerHTML = `<p class="pane-status">Running auto-promotion evaluator...</p>`;
  try {
    const payload = await apiPost("/api/learning/auto-promote", {});
    if (host) host.innerHTML = renderAutoPromoteResult(payload);
    loadLearningDecisions();
    openPaperPanel();
  } catch (error) {
    if (host) host.innerHTML = `<p class="pane-status">Auto-promote rejected: ${escapeHtml(error.message)}</p>`;
    loadLearningDecisions();
  }
}

function renderAutoPromoteResult(payload) {
  const checks = payload.checks || [];
  const promoted = payload.promoted ?? payload.allowed;
  const candidate = payload.candidate;
  return `
    <h3 class="modal-section-title">Auto-Promote <span class="${promoted ? "positive" : "neutral"}">${promoted ? "Allowed" : "Blocked"}</span></h3>
    <p class="modal-note">${escapeHtml(payload.reason || "")}</p>
    <div class="metric-grid">
      <div class="metric"><span>Enabled</span><strong>${escapeHtml(String(payload.autoPromote ?? payload.attempted ?? false))}</strong></div>
      <div class="metric"><span>Mode</span><strong>${escapeHtml(payload.autoPromoteMode || "candidate_only")}</strong></div>
      <div class="metric"><span>Paper auto-enable</span><strong>${escapeHtml(String(payload.autoEnablePaper || false))}</strong></div>
      <div class="metric"><span>Candidate</span><strong>${candidate ? `${escapeHtml(candidate.strategy || "-")} ${escapeHtml(candidate.symbol || "")}` : "-"}</strong></div>
    </div>
    <table class="trade-table">
      <thead><tr><th>Check</th><th>Pass</th><th>Detail</th></tr></thead>
      <tbody>${checks.map((check) => `
        <tr>
          <td>${escapeHtml(check.name || "-")}</td>
          <td class="${check.passed ? "positive" : "negative"}">${check.passed ? "yes" : "no"}</td>
          <td>${escapeHtml(check.detail || "")}</td>
        </tr>
      `).join("") || `<tr><td colspan="3">No checks returned.</td></tr>`}</tbody>
    </table>
    <p class="modal-note">Auto-promotion only changes the paper candidate config. It does not enable paper simulation and does not trade.</p>
  `;
}

async function loadLearningReport(reportId) {
  const status = document.querySelector("#learning-status");
  try {
    const report = await apiGet(`/api/learning/reports/${encodeURIComponent(reportId)}`);
    lastLearningReport = report;
    renderLearningReport(report);
    if (status) status.textContent = `Loaded learning report ${report.id}.`;
  } catch (error) {
    if (status) status.textContent = `Could not load learning report: ${error.message}`;
  }
}

function renderLearningReport(report) {
  const summary = document.querySelector("#learning-summary");
  const recommendation = document.querySelector("#learning-recommendation");
  const rec = report.recommendation || {};
  const health = report.candidateHealth || {};
  const best = report.bestSavedCandidate || {};
  if (summary) {
    summary.innerHTML = `
      <div class="metric"><span>Status</span><strong>${escapeHtml(report.status || "-")}</strong></div>
      <div class="metric"><span>Ranking runs</span><strong>${(report.rankingRunIds || []).length}</strong></div>
      <div class="metric"><span>Optimization runs</span><strong>${(report.optimizationRunIds || []).length}</strong></div>
      <div class="metric"><span>Health</span><strong>${escapeHtml(health.status || "UNKNOWN")}</strong></div>
      <div class="metric"><span>Best saved</span><strong>${best.strategy ? `${escapeHtml(best.strategy)} ${escapeHtml(best.symbol)} ${escapeHtml(best.timeframe)}` : "-"}</strong></div>
      <div class="metric"><span>Action</span><strong>${escapeHtml(rec.action || "-")}</strong></div>
    `;
  }
  if (recommendation) {
    recommendation.innerHTML = `
      <h3 class="modal-section-title">Recommendation</h3>
      <p class="modal-note"><strong>${escapeHtml(rec.action || "-")}</strong> ${escapeHtml(rec.reason || "")}</p>
      ${rec.candidate ? `
        <table class="trade-table">
          <tbody>
            <tr><th>Candidate</th><td>${escapeHtml(rec.candidate.strategy)} ${escapeHtml(rec.candidate.symbol)} ${escapeHtml(rec.candidate.timeframe)}</td></tr>
            <tr><th>Score</th><td>${formatNumber(rec.candidate.score)}</td></tr>
            <tr><th>PF / Trades</th><td>${formatNumber(rec.candidate.profitFactor)} / ${rec.candidate.trades || 0}</td></tr>
          </tbody>
        </table>
        <button type="button" class="small-action-button" data-promote-learning="1">Promote config only</button>
        <p class="modal-note">Paper remains disabled. No trades will be placed.</p>
      ` : ""}
      ${renderCandidateHealth(health)}
      ${report.autoPromotion ? `<h3 class="modal-section-title">Auto-Promotion</h3>${renderAutoPromoteResult(report.autoPromotion)}` : ""}
      ${(report.warnings || []).length ? `<ul class="backtest-warnings">${report.warnings.map((warning) => `<li>${escapeHtml(typeof warning === "string" ? warning : JSON.stringify(warning))}</li>`).join("")}</ul>` : ""}
      ${(report.errors || []).length ? `<ul class="backtest-warnings">${report.errors.map((error) => `<li>${escapeHtml(typeof error === "string" ? error : JSON.stringify(error))}</li>`).join("")}</ul>` : ""}
    `;
  }
}

function setupAnalysisControls() {
  const sourceFilter = document.querySelector("#analysis-source-filter");
  const symbolFilter = document.querySelector("#analysis-symbol-filter");
  const timeframeFilter = document.querySelector("#analysis-timeframe-filter");
  const presetFilter = document.querySelector("#analysis-preset-filter");
  if (!hasElement(sourceFilter, symbolFilter, timeframeFilter, presetFilter)) return;

  sourceFilter.innerHTML = Object.entries(config.sources)
    .map(([value, item]) => `<option value="${value}">${item.label}</option>`)
    .join("");
  sourceFilter.value = "bybit";
  presetFilter.innerHTML = (config.strategy_presets || [])
    .map((preset) => `<option value="${preset.id}" ${preset.id === config.default_strategy_preset ? "selected" : ""}>${preset.label}</option>`)
    .join("");
  populateAnalysisMarketFilters();
  populateOptimizationControls();
  sourceFilter.addEventListener("change", populateAnalysisMarketFilters);
  document.querySelector("#analysis-run-ranking")?.addEventListener("click", runStrategyRanking);
  document.querySelector("#analysis-run-optimization")?.addEventListener("click", runStrategyOptimization);
  document.querySelector("#analysis-table-body")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-promote-ranking]");
    if (!button) return;
    const index = Number(button.dataset.promoteRanking);
    const row = lastStrategyRankingPayload?.rows?.[index];
    if (row) promoteRankingCandidate(row);
  });
  document.querySelector("#optimization-table-body")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-promote-optimization]");
    if (!button) return;
    const index = Number(button.dataset.promoteOptimization);
    const row = lastOptimizationPayload?.topCandidates?.[index];
    if (row) promoteOptimizedCandidate(row);
  });
  document.querySelector("#research-runs-table-body")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-load-research-run]");
    if (button) loadResearchRun(button.dataset.loadResearchRun);
  });
  document.querySelector("#research-suggest-candidate")?.addEventListener("click", suggestResearchCandidate);
  document.querySelector("#research-suggestion")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-promote-suggestion]");
    if (button && lastResearchSuggestion?.candidate) promoteResearchCandidate(lastResearchSuggestion.candidate, lastResearchSuggestion);
  });
}

function populateAnalysisMarketFilters() {
  const source = document.querySelector("#analysis-source-filter")?.value || "bybit";
  const sourceConfig = config.sources[source];
  const symbolFilter = document.querySelector("#analysis-symbol-filter");
  const timeframeFilter = document.querySelector("#analysis-timeframe-filter");
  if (!hasElement(symbolFilter, timeframeFilter, sourceConfig)) return;
  symbolFilter.innerHTML = (sourceConfig.symbols || [])
    .map((symbol) => `<option value="${symbol}" ${symbol === "BTCUSDT" ? "selected" : ""}>${symbol}</option>`)
    .join("");
  timeframeFilter.innerHTML = (sourceConfig.timeframes || [])
    .map((timeframe) => `<option value="${timeframe}" ${timeframe === "1h" ? "selected" : ""}>${timeframe}</option>`)
    .join("");
  populateOptimizationControls();
}

function populateOptimizationControls() {
  const source = document.querySelector("#analysis-source-filter")?.value || "bybit";
  const sourceConfig = config.sources?.[source] || {};
  const strategySelect = document.querySelector("#opt-strategy-filter");
  const symbolSelect = document.querySelector("#opt-symbol-filter");
  const timeframeSelect = document.querySelector("#opt-timeframe-filter");
  if (!hasElement(strategySelect, symbolSelect, timeframeSelect)) return;
  strategySelect.innerHTML = (config.optimizer_strategy_presets || config.strategy_presets || [])
    .map((preset) => `<option value="${preset.id}" ${preset.id === "SimpleAtrTrendV2" ? "selected" : ""}>${preset.label}</option>`)
    .join("");
  symbolSelect.innerHTML = (sourceConfig.symbols || [])
    .map((symbol) => `<option value="${symbol}" ${symbol === "BTCUSDT" ? "selected" : ""}>${symbol}</option>`)
    .join("");
  timeframeSelect.innerHTML = (sourceConfig.timeframes || [])
    .map((timeframe) => `<option value="${timeframe}" ${timeframe === "1h" ? "selected" : ""}>${timeframe}</option>`)
    .join("");
}

function selectedOptionValues(select) {
  return Array.from(select?.selectedOptions || []).map((option) => option.value).filter(Boolean);
}

async function runStrategyRanking() {
  const status = document.querySelector("#analysis-status");
  const body = document.querySelector("#analysis-table-body");
  const cardsEl = document.querySelector("#analysis-cards");
  if (status) status.textContent = "Running backend strategy ranking...";
  if (cardsEl) cardsEl.innerHTML = "";
  if (body) body.innerHTML = `<tr><td colspan="12">Loading ranking results...</td></tr>`;
  const symbolSelect = document.querySelector("#analysis-symbol-filter");
  const timeframeSelect = document.querySelector("#analysis-timeframe-filter");
  const presetSelect = document.querySelector("#analysis-preset-filter");
  const params = new URLSearchParams({
    source: document.querySelector("#analysis-source-filter")?.value || "bybit",
    symbols: selectedOptionValues(symbolSelect).join(","),
    timeframes: selectedOptionValues(timeframeSelect).join(","),
    presets: selectedOptionValues(presetSelect).join(","),
    period: document.querySelector("#analysis-period-filter")?.value || "365d",
    min_trades: document.querySelector("#analysis-min-trades")?.value || "0",
    limit: document.querySelector("#analysis-limit-filter")?.value || "auto",
    fee_pct: document.querySelector("#analysis-fee-filter")?.value || "0",
    slippage_pct: document.querySelector("#analysis-slippage-filter")?.value || "0",
  });
  try {
    const response = await fetch(`/api/strategy-ranking?${params}`);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Strategy ranking failed");
    lastStrategyRankingPayload = payload;
    renderStrategyRanking(payload);
    loadResearchRuns();
    if (status) {
      const summary = payload.summary || {};
      status.textContent = `Completed ${summary.runsCompleted || 0}/${summary.runsRequested || 0} backend runs. ${summary.validCandidates || 0} valid candidates. ${summary.errors || 0} errors.`;
    }
  } catch (error) {
    if (status) status.textContent = error.message;
    if (body) body.innerHTML = `<tr><td colspan="12">${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderStrategyRanking(payload) {
  const rows = payload.rows || [];
  const cardsPayload = payload.cards || {};
  const cardsEl = document.querySelector("#analysis-cards");
  const body = document.querySelector("#analysis-table-body");
  if (cardsEl) {
    const cards = [
      ["Best overall", cardsPayload.bestOverall],
      ["Best win rate", cardsPayload.bestWinRate],
      ["Lowest drawdown", cardsPayload.lowestDrawdown],
      ["Worst result", cardsPayload.worstResult],
    ];
    cardsEl.innerHTML = cards.map(([label, row]) => `
      <div class="metric">
        <span>${label}</span>
        <strong>${row ? `${escapeHtml(row.strategy)} ${escapeHtml(row.symbol)} ${escapeHtml(row.timeframe)} · ${formatSigned(row.totalReturnPct)}%` : "-"}</strong>
      </div>
    `).join("");
  }
  if (body) {
    body.innerHTML = rows.map((row, index) => `
      <tr class="${row.valid ? "" : "invalid-row"}">
        <td>${row.rank}</td>
        <td>${escapeHtml(row.strategy)}${row.valid ? "" : " <span class=\"status-pill muted\">invalid</span>"}</td>
        <td>${escapeHtml(row.symbol)}</td>
        <td>${escapeHtml(row.timeframe)}</td>
        <td class="${row.totalReturnPct >= 0 ? "positive" : "negative"}">${formatSigned(row.totalReturnPct)}%</td>
        <td>${formatNumber(row.winRate)}%</td>
        <td>${formatNumber(row.maxDrawdown)}%</td>
        <td>${formatNumber(row.profitFactor)}</td>
        <td>${row.trades}</td>
        <td title="${escapeHtml((row.warnings || []).join("; "))}">${formatNumber(row.score)}</td>
        <td title="${escapeHtml((row.dataReadiness?.warnings || []).join("; "))}">${escapeHtml(row.dataReadiness?.status || (row.partialData ? "PARTIAL" : "READY"))}</td>
        <td><button type="button" class="small-action-button" data-promote-ranking="${index}" ${row.valid ? "" : "disabled"}>Promote</button></td>
      </tr>
    `).join("") || `<tr><td colspan="12">No rows matched the filters.</td></tr>`;
  }
}

async function promoteRankingCandidate(row) {
  const status = document.querySelector("#analysis-status");
  const minTrades = Number(lastStrategyRankingPayload?.requested?.minTrades || document.querySelector("#analysis-min-trades")?.value || 10);
  const ok = window.confirm(`Promote ${row.strategy} on ${row.symbol} ${row.timeframe} as the paper candidate?\n\nPaper simulation will stay disabled until you explicitly enable it.`);
  if (!ok) return;
  const requestBody = {
    source: lastStrategyRankingPayload?.source || document.querySelector("#analysis-source-filter")?.value || "bybit",
    symbol: row.symbol,
    timeframe: row.timeframe,
    preset: row.preset,
    strategy: row.strategy,
    period: row.period || lastStrategyRankingPayload?.period,
    params: row.params || {},
    minTrades,
    rankingSnapshot: {
      valid: row.valid,
      rank: row.rank,
      score: row.score,
      totalReturnPct: row.totalReturnPct,
      winRate: row.winRate,
      maxDrawdown: row.maxDrawdown,
      profitFactor: row.profitFactor,
      trades: row.trades,
    },
  };
  try {
    if (status) status.textContent = "Promoting candidate...";
    const payload = await apiPost("/api/candidate/promote", requestBody);
    if (status) status.textContent = `${payload.message} Candidate remains disabled.`;
    if (!paperPanel?.hidden) openPaperPanel();
  } catch (error) {
    if (status) status.textContent = `Promotion failed: ${error.message}`;
  }
}

async function runStrategyOptimization() {
  const status = document.querySelector("#optimization-status");
  const body = document.querySelector("#optimization-table-body");
  if (status) status.textContent = "Running backend strategy optimization...";
  if (body) body.innerHTML = `<tr><td colspan="8">Loading optimization results...</td></tr>`;
  const params = new URLSearchParams({
    source: document.querySelector("#analysis-source-filter")?.value || "bybit",
    symbol: document.querySelector("#opt-symbol-filter")?.value || "BTCUSDT",
    timeframe: document.querySelector("#opt-timeframe-filter")?.value || "1h",
    strategy: document.querySelector("#opt-strategy-filter")?.value || "regime_filtered_trend",
    period: document.querySelector("#opt-period-filter")?.value || "365d",
    limit: document.querySelector("#opt-limit-filter")?.value || "auto",
    max_combos: document.querySelector("#opt-max-combos-filter")?.value || "500",
    train_ratio: document.querySelector("#opt-train-ratio-filter")?.value || "0.7",
    fee_pct: document.querySelector("#analysis-fee-filter")?.value || "0",
    slippage_pct: document.querySelector("#analysis-slippage-filter")?.value || "0",
  });
  try {
    const response = await fetch(`/api/strategy-optimize?${params}`);
    const payload = await response.json();
    if (!response.ok) throw Object.assign(new Error(payload.error || "Strategy optimization failed"), { payload });
    lastOptimizationPayload = payload;
    renderStrategyOptimization(payload);
    loadResearchRuns();
    if (status) {
      const summary = payload.summary || {};
      const readiness = payload.dataReadiness?.summary || {};
      const grid = payload.optimizerGrid || {};
      const gridAudit = payload.gridAudit || {};
      const zero = payload.zeroTradeSummary || {};
      const quality = payload.qualitySummary || {};
      const fallback = grid.fallbackUsed ? " Fallback grid used." : "";
      const zeroText = payload.allZeroTradeCandidates ? ` All candidates had zero trades: ${zero.suggestedGridAction || "inspect diagnostics"}.` : "";
      const qualityText = quality.totalCandidates !== undefined
        ? ` Quality PASS/WARN/FAIL ${quality.passCandidates || 0}/${quality.warnCandidates || 0}/${quality.failCandidates || 0}; selected=${quality.selectedStatus || "n/a"}.`
        : "";
      const gridAuditText = gridAudit.diagnosis ? ` Grid audit: ${gridAudit.diagnosis}.` : "";
      status.textContent = `Optimization complete using ${grid.gridName || "optimizer grid"}. Tested ${grid.candidateCountTested || JSON.stringify(summary.combinationsTested || 0)}/${grid.candidateCountPlanned || "?"} combos. ${summary.validCandidates || 0} acceptable candidates.${qualityText} Data ready ${readiness.readyPairs || 0}/${readiness.totalPairs || 0}; partial=${payload.partialData ? "yes" : "no"}.${fallback}${zeroText}${gridAuditText}`;
    }
  } catch (error) {
    if (status) status.textContent = `Optimization failed: ${error.message}`;
    if (body) {
      const diagnostic = error.payload?.zeroTradeDiagnostics;
      body.innerHTML = `
        <tr><td colspan="8">${escapeHtml(error.message)}</td></tr>
        ${diagnostic ? `<tr><td colspan="8">${renderTradeGenerationDiagnostics(diagnostic)}</td></tr>` : ""}
      `;
    }
  }
}

function renderStrategyOptimization(payload) {
  const body = document.querySelector("#optimization-table-body");
  const rows = payload.topCandidates || [];
  const rejectedRows = payload.rejectedCandidates || [];
  if (!body) return;
  const zeroSummary = payload.zeroTradeSummary || {};
  const qualitySummary = payload.qualitySummary || {};
  const gridAudit = payload.gridAudit || {};
  const topQualityReason = qualitySummary.topRejectionReasons?.[0];
  const metaRows = `
    <tr class="diagnostic-row">
      <td colspan="8">
        Grid: <strong>${escapeHtml(payload.optimizerGrid?.gridName || "-")}</strong>
        · planned ${payload.optimizerGrid?.candidateCountPlanned ?? "-"}
        · tested ${payload.optimizerGrid?.candidateCountTested ?? "-"}
        ${payload.optimizerGrid?.fallbackUsed ? " · fallback used" : ""}
        ${qualitySummary.totalCandidates !== undefined ? ` · quality PASS/WARN/FAIL ${qualitySummary.passCandidates || 0}/${qualitySummary.warnCandidates || 0}/${qualitySummary.failCandidates || 0}` : ""}
        ${qualitySummary.selectedStatus ? ` · selected ${escapeHtml(qualitySummary.selectedStatus)}` : ""}
        ${topQualityReason ? ` · top rejection ${escapeHtml(topQualityReason.label || topQualityReason.reason)}` : ""}
        ${zeroSummary.zeroTradeCandidates ? ` · zero-trade candidates ${zeroSummary.zeroTradeCandidates}/${zeroSummary.totalCandidates}` : ""}
        ${zeroSummary.topReasons?.length ? ` · top reason ${escapeHtml(zeroSummary.topReasons[0].reason)}` : ""}
      </td>
    </tr>
  `;
  const gridAuditRow = gridAudit.diagnosis ? `
    <tr class="diagnostic-row">
      <td colspan="8">Grid audit: <strong>${escapeHtml(gridAudit.diagnosis)}</strong>${(gridAudit.suggestedChanges || []).length ? ` · ${escapeHtml(gridAudit.suggestedChanges[0])}` : ""}</td>
    </tr>
  ` : "";
  const candidateRows = rows.map((row, index) => {
    const quality = row.qualityStatus || (row.valid ? "PASS" : "FAIL");
    const reasons = optimizerReasonText(row);
    return `
    <tr class="${quality === "FAIL" ? "invalid-row" : ""}">
      <td>${row.rank}</td>
      <td>${formatNumber(row.score)} <span class="status-pill muted" title="${escapeHtml(reasons)}">${escapeHtml(quality)}</span></td>
      <td><code>${escapeHtml(JSON.stringify(row.params || {}))}</code></td>
      <td>${formatOptimizationMetric(row.train)}</td>
      <td>${formatOptimizationMetric(row.test)}</td>
      <td>${row.full && Object.keys(row.full).length ? formatOptimizationMetric(row.full) : "-"}</td>
      <td title="${escapeHtml(reasons)}">${escapeHtml(row.overfitWarning || (row.warnings || [])[0] || reasons || "-")}</td>
      <td><button type="button" class="small-action-button" data-promote-optimization="${index}" ${quality === "FAIL" ? "disabled" : ""}>Promote</button></td>
    </tr>
  `;
  }).join("");
  const rejectedPreview = rejectedRows.length ? `
    <tr class="diagnostic-row"><td colspan="8">Rejected candidates preview</td></tr>
    ${rejectedRows.slice(0, 10).map((row) => `
      <tr class="invalid-row">
        <td>R${row.rank}</td>
        <td>${formatNumber(row.score)} <span class="status-pill muted">FAIL</span></td>
        <td><code>${escapeHtml(JSON.stringify(row.params || {}))}</code></td>
        <td>${formatOptimizationMetric(row.train)}</td>
        <td>${formatOptimizationMetric(row.test)}</td>
        <td>${row.full && Object.keys(row.full).length ? formatOptimizationMetric(row.full) : "-"}</td>
        <td colspan="2" title="${escapeHtml(optimizerReasonText(row))}">${escapeHtml(optimizerReasonText(row) || "Rejected by optimizer quality policy")}</td>
      </tr>
    `).join("")}
  ` : "";
  const empty = !candidateRows && !rejectedPreview
    ? `<tr><td colspan="8">No optimization candidates returned.</td></tr>`
    : (!candidateRows ? `<tr><td colspan="8">No acceptable optimizer candidate found.</td></tr>` : "");
  body.innerHTML = metaRows + gridAuditRow + candidateRows + empty + rejectedPreview;
}

function optimizerReasonText(row) {
  const reasons = (row.rejectionReasons || []).map((item) => item.label || item.code || String(item));
  return [...reasons, ...(row.warnings || [])].filter(Boolean).join("; ");
}

function formatOptimizationMetric(metric) {
  if (!metric) return "-";
  return `R ${formatSigned(metric.totalReturn)}% · PF ${formatNumber(metric.profitFactor)} · DD ${formatNumber(metric.maxDrawdown)}% · T ${metric.trades || 0}`;
}

async function promoteOptimizedCandidate(row) {
  const status = document.querySelector("#optimization-status");
  const payload = lastOptimizationPayload || {};
  const minTrades = Number(document.querySelector("#analysis-min-trades")?.value || 20);
  const ok = window.confirm(`Promote optimized ${payload.strategy} on ${payload.symbol} ${payload.timeframe} as the paper candidate?\n\nPaper simulation will stay disabled until validation passes and you explicitly enable it.`);
  if (!ok) return;
  const test = row.test || {};
  const full = row.full || {};
  const requestBody = {
    source: payload.source || "bybit",
    symbol: payload.symbol,
    timeframe: payload.timeframe,
    preset: payload.strategy,
    strategy: payload.strategy,
    period: payload.period,
    params: row.params || {},
    minTrades,
    rankingSnapshot: {
      valid: row.valid,
      rank: row.rank,
      score: row.score,
      totalReturnPct: test.totalReturn ?? full.totalReturn ?? 0,
      winRate: test.winRate ?? full.winRate ?? 0,
      maxDrawdown: test.maxDrawdown ?? full.maxDrawdown ?? 0,
      profitFactor: test.profitFactor ?? full.profitFactor ?? 0,
      trades: test.trades ?? full.trades ?? 0,
    },
    optimizationSnapshot: {
      rank: row.rank,
      score: row.score,
      train: row.train,
      test: row.test,
      full: row.full,
      warnings: row.warnings,
      overfitWarning: row.overfitWarning,
      requested: payload.requested,
    },
  };
  try {
    if (status) status.textContent = "Promoting optimized candidate...";
    const result = await apiPost("/api/candidate/promote", requestBody);
    if (status) status.textContent = `${result.message} Candidate remains disabled.`;
    if (!paperPanel?.hidden) openPaperPanel();
  } catch (error) {
    if (status) status.textContent = `Promotion failed: ${error.message}`;
  }
}

async function loadResearchRuns() {
  const body = document.querySelector("#research-runs-table-body");
  const cards = document.querySelector("#research-summary-cards");
  if (!body || !cards) return;
  try {
    const payload = await apiGet("/api/research/runs?limit=20");
    renderResearchRuns(payload);
  } catch (error) {
    body.innerHTML = `<tr><td colspan="7">Research history could not load: ${escapeHtml(error.message)}</td></tr>`;
  }
}

function renderResearchRuns(payload) {
  const body = document.querySelector("#research-runs-table-body");
  const cards = document.querySelector("#research-summary-cards");
  const summary = payload.summary || {};
  const best = summary.bestSavedCandidate;
  if (cards) {
    cards.innerHTML = `
      <div class="metric"><span>Total runs</span><strong>${summary.totalRuns || 0}</strong></div>
      <div class="metric"><span>Ranking runs</span><strong>${summary.rankingRuns || 0}</strong></div>
      <div class="metric"><span>Optimization runs</span><strong>${summary.optimizationRuns || 0}</strong></div>
      <div class="metric"><span>Best saved</span><strong>${best ? `${escapeHtml(best.strategy)} ${escapeHtml(best.symbol)} ${escapeHtml(best.timeframe)} · ${formatNumber(best.score)}` : "-"}</strong></div>
    `;
  }
  if (!body) return;
  if (cards) loadCandidateHealthSummary();
  body.innerHTML = (payload.runs || []).map((run) => {
    const candidate = run.bestCandidate || {};
    return `
      <tr>
        <td>${run.createdAt ? escapeHtml(new Date(run.createdAt).toLocaleString()) : "-"}</td>
        <td>${escapeHtml(run.type || "-")}</td>
        <td>${escapeHtml((run.symbols || []).join(","))} ${escapeHtml((run.timeframes || []).join(","))}</td>
        <td>${candidate ? `${escapeHtml(candidate.strategy || "-")} ${escapeHtml(candidate.symbol || "")} ${escapeHtml(candidate.timeframe || "")}` : "-"}</td>
        <td>${candidate?.score !== undefined ? formatNumber(candidate.score) : "-"}</td>
        <td>${escapeHtml(run.status || "-")}</td>
        <td><button type="button" class="small-action-button" data-load-research-run="${escapeHtml(run.id)}">Load run</button></td>
      </tr>
    `;
  }).join("") || `<tr><td colspan="7">No saved research runs yet.</td></tr>`;
}

async function loadCandidateHealthSummary() {
  const cards = document.querySelector("#research-summary-cards");
  if (!cards) return;
  const renderCard = (label) => {
    const existing = document.querySelector("#research-health-card");
    const html = `
      <div class="metric" id="research-health-card">
        <span>Candidate health</span>
        <strong>${escapeHtml(label)}</strong>
      </div>
    `;
    if (existing) existing.outerHTML = html;
    else cards.insertAdjacentHTML("beforeend", html);
  };
  try {
    const payload = await apiGet("/api/candidate/health");
    renderCard(payload.health?.status || "UNKNOWN");
  } catch (error) {
    renderCard("Unavailable");
  }
}

async function loadResearchRun(runId) {
  const suggestion = document.querySelector("#research-suggestion");
  try {
    const run = await apiGet(`/api/research/runs/${encodeURIComponent(runId)}`);
    if (run.type === "ranking") {
      const payload = {
        source: run.source,
        period: run.period,
        requested: { symbols: run.symbols || [], timeframes: run.timeframes || [], presets: run.presets || [] },
        summary: run.summary || {},
        cards: { bestOverall: run.bestCandidate, bestWinRate: run.bestCandidate, lowestDrawdown: run.bestCandidate, worstResult: run.bestCandidate },
        rows: run.rows || [],
        errors: run.errors || [],
      };
      lastStrategyRankingPayload = payload;
      renderStrategyRanking(payload);
    } else if (run.type === "optimization") {
      const payload = {
        source: run.source,
        strategy: (run.strategies || [])[0],
        symbol: (run.symbols || [])[0],
        timeframe: (run.timeframes || [])[0],
        period: run.period,
        requested: { limit: run.limit, maxCombos: run.max_combos, trainRatio: run.train_ratio, feePct: run.fee_pct, slippagePct: run.slippage_pct },
        summary: run.summary || {},
        topCandidates: run.topCandidates || [],
      };
      lastOptimizationPayload = payload;
      renderStrategyOptimization(payload);
    }
    if (suggestion) suggestion.textContent = `Loaded saved ${run.type} run ${run.id}.`;
  } catch (error) {
    if (suggestion) suggestion.textContent = `Could not load research run: ${error.message}`;
  }
}

async function suggestResearchCandidate() {
  const suggestion = document.querySelector("#research-suggestion");
  try {
    if (suggestion) suggestion.textContent = "Asking backend for candidate suggestion...";
    const payload = await apiPost("/api/research/suggest-candidate", {});
    lastResearchSuggestion = payload;
    renderResearchSuggestion(payload);
  } catch (error) {
    if (suggestion) suggestion.textContent = `Suggestion failed: ${error.message}`;
  }
}

function renderResearchSuggestion(payload) {
  const suggestion = document.querySelector("#research-suggestion");
  const candidate = payload.candidate;
  if (!suggestion) return;
  suggestion.innerHTML = `
    <strong>${escapeHtml(payload.action || "-")}</strong>
    <span>${escapeHtml(payload.reason || "")}</span>
    ${candidate ? `<button type="button" class="small-action-button" data-promote-suggestion="1">Promote suggested candidate</button>` : ""}
  `;
}

async function promoteResearchCandidate(candidate, suggestionPayload, targetSelector = "#research-suggestion") {
  const suggestion = document.querySelector(targetSelector);
  const ok = window.confirm(`Promote saved ${candidate.strategy} on ${candidate.symbol} ${candidate.timeframe} as the paper candidate?\n\nPaper simulation will stay disabled until validation passes and you explicitly enable it.`);
  if (!ok) return;
  const requestBody = {
    source: candidate.source || "bybit",
    symbol: candidate.symbol,
    timeframe: candidate.timeframe,
    preset: candidate.preset || candidate.strategy,
    strategy: candidate.strategy,
    period: candidate.period,
    params: candidate.params || {},
    minTrades: Number(document.querySelector("#analysis-min-trades")?.value || 20),
    rankingSnapshot: {
      valid: candidate.valid,
      rank: candidate.rank,
      score: candidate.score,
      totalReturnPct: candidate.totalReturnPct,
      winRate: candidate.winRate,
      maxDrawdown: candidate.maxDrawdown,
      profitFactor: candidate.profitFactor,
      trades: candidate.trades,
    },
    optimizationSnapshot: candidate.origin === "optimization" ? {
      researchRunId: candidate.researchRunId,
      score: candidate.score,
      train: candidate.train,
      test: candidate.test,
      full: candidate.full,
      warnings: candidate.warnings,
    } : suggestionPayload?.health ? {
      replacementSuggestedFromHealth: suggestionPayload.health.status,
      researchRunId: candidate.researchRunId,
      score: candidate.score,
      warnings: candidate.warnings,
    } : undefined,
  };
  try {
    const result = await apiPost("/api/candidate/promote", requestBody);
    if (suggestion) suggestion.textContent = `${result.message} Candidate remains disabled.`;
    if (!paperPanel?.hidden) openPaperPanel();
  } catch (error) {
    if (suggestion) suggestion.textContent = `Promotion failed: ${error.message}`;
  }
}

function renderSettingsPage() {
  const settingsContent = document.querySelector("#settings-content");
  if (!settingsContent) return;
  const symbolChips = (id, source) => `
    <div class="symbol-chip-grid">
      ${(source.symbols || []).map((symbol) => `
        <button class="symbol-chip" type="button" data-settings-source="${escapeHtml(id)}" data-settings-symbol="${escapeHtml(symbol)}">${escapeHtml(symbol)}</button>
      `).join("")}
    </div>
  `;
  settingsContent.innerHTML = `
    <div class="metric"><span>Default source</span><strong>Bybit</strong></div>
    <div class="metric"><span>Default chart count</span><strong>1</strong></div>
    <div class="metric"><span>Default preset</span><strong>${escapeHtml(config.default_strategy_preset || "-")}</strong></div>
    <div class="metric"><span>Architecture</span><strong>API-render only</strong></div>
    <section class="settings-panel">
      <h3>Available Sources</h3>
      <table class="trade-table">
        <thead><tr><th>Source</th><th>Symbols</th><th>Timeframes</th></tr></thead>
        <tbody>${Object.entries(config.sources || {}).map(([id, source]) => `
          <tr>
            <td>${escapeHtml(source.label || id)}</td>
            <td>${symbolChips(id, source)}</td>
            <td>${escapeHtml((source.timeframes || []).join(", "))}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    </section>
  `;
  settingsContent.querySelectorAll("[data-settings-symbol]").forEach((button) => {
    button.addEventListener("click", () => {
      showPage("charts");
      history.pushState({}, "", "/charts");
      const pane = panes[0];
      if (!pane) return;
      pane.sourceSelect.value = button.dataset.settingsSource || "bybit";
      populateSymbolAndTimeframe(pane, { symbol: button.dataset.settingsSymbol });
      syncChartsToolbarFromPane(pane);
      startPane(pane);
      saveState();
    });
  });
}

function formatIsoDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function formatBacktestTime(value) {
  if (!value) return "N/A";
  if (typeof value === "number" || /^\d+$/.test(String(value))) {
    return formatDateTime(Number(value));
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "N/A";
  return date.toLocaleString();
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

function formatNumber(value, digits = 2) {
  const number = Number(value || 0);
  return number.toFixed(digits);
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
  watchlistQuotes.set(pane.symbolSelect.value, {
    ...(watchlistQuotes.get(pane.symbolSelect.value) || {}),
    price,
    updatedAt: Date.now(),
  });
  renderWatchlist();

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
