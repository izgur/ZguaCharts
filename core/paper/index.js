const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const data = require("../data");
const indicators = require("../indicators");
const regime = require("../regime");
const backtest = require("../backtest");

const DEFAULT_STATE_PATH = path.join(process.cwd(), "data", "paper-state.json");
const DEFAULT_REPORT_DIR = path.join(process.cwd(), "reports");
const DEFAULT_CONFIG_PATH = path.join(process.cwd(), "config", "paper-candidate.default.json");
const LOCAL_CONFIG_PATH = path.join(process.cwd(), "config", "local", "paper-candidate.json");

function resolveConfigPath(configPath) {
  return configPath || LOCAL_CONFIG_PATH;
}

function loadConfig(configPath) {
  const resolvedPath = resolveConfigPath(configPath);
  const defaults = readJson(DEFAULT_CONFIG_PATH, {});
  if (!fs.existsSync(resolvedPath)) {
    return Object.assign({}, defaults);
  }
  return Object.assign({}, defaults, readJson(resolvedPath, {}));
}

function ensureRuntimeConfig(configPath) {
  const resolvedPath = resolveConfigPath(configPath);
  if (!fs.existsSync(resolvedPath)) {
    ensureDir(path.dirname(resolvedPath));
    const defaults = readJson(DEFAULT_CONFIG_PATH, {});
    fs.writeFileSync(resolvedPath, JSON.stringify(defaults, null, 2) + "\n");
  }
  return resolvedPath;
}

function readJson(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function runPaperTick(options) {
  options = options || {};
  const configPath = resolveConfigPath(options.configPath);
  const config = loadConfig(configPath);
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const reportDir = options.reportDir || DEFAULT_REPORT_DIR;
  ensureDir(path.dirname(statePath));
  ensureDir(reportDir);
  const state = loadState(statePath, config);
  state.warnings = [];

  const markets = normalizeMarkets(config);
  const refreshPromise = options.refreshFirst
    ? refreshPaperCandles({ configPath, reportDir, advanceBaseline: false, statePath })
    : Promise.resolve(null);
  return refreshPromise.then(() => data.fetchCandles({
    source: config.source || "bybit",
    symbol: "BTCUSDT",
    interval: "4h",
    limit: 3000
  })).then((regimeCandles) => {
    const jobs = markets.map((market) => () => processMarket(config, state, market, regimeCandles, options));
    return jobs.reduce((p, job) => p.then((events) => job().then((next) => events.concat(next))), Promise.resolve([]));
  }).then((events) => {
    if (!config.enabled) {
      state.warnings.push("Paper simulation disabled in config. Signals/freshness checked, but virtual trades were not opened or closed.");
      events = events.filter((event) => event.eventType === "SIGNAL" || event.eventType === "WARNING" || event.eventType === "SKIP");
      if (options.dryRun) {
        return summary(state, config, events, "disabled-dry-run");
      }
      appendJournal(reportDir, events);
      state.updatedAt = new Date().toISOString();
      writeState(statePath, state);
      writeStatusReports(reportDir, state, config, events);
      return summary(state, config, events, "disabled-watch");
    }
    if (options.dryRun) {
      return summary(state, config, events, "dry-run");
    }
    appendJournal(reportDir, events);
    state.updatedAt = new Date().toISOString();
    state.accountEquity = round(Number(config.accountEquity || 10000) + state.realizedPnl + state.unrealizedPnl, 4);
    state.equityCurve.push({ time: Math.floor(Date.now() / 1000), equity: state.accountEquity });
    if (state.equityCurve.length > 5000) state.equityCurve = state.equityCurve.slice(-5000);
    writeState(statePath, state);
    writeStatusReports(reportDir, state, config, events);
    return summary(state, config, events, "processed");
  });
}

function initializePaper(options) {
  options = options || {};
  const configPath = resolveConfigPath(options.configPath);
  const config = loadConfig(configPath);
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const reportDir = options.reportDir || DEFAULT_REPORT_DIR;
  ensureDir(path.dirname(statePath));
  ensureDir(reportDir);
  const state = loadState(statePath, config);
  const markets = normalizeMarkets(config);
  state.startedAt = state.startedAt || new Date().toISOString();
  state.warnings = ["Initialized paper baselines. Historical trades were not imported."];
  state.openPositions = [];
  state.closedTrades = [];
  state.pendingSignals = [];
  state.skippedSignals = 0;
  state.realizedPnl = 0;
  state.unrealizedPnl = 0;
  state.cumulativeFees = 0;
  state.cumulativeSlippage = 0;
  state.accountEquity = Number(config.accountEquity || 10000);
  state.equityCurve = [];
  state.processedCandles = 0;
  state.freshness = {};
  state.lastProcessedCandleTime = {};
  const freshness = {};
  const jobs = markets.map((market) => () => fetchMarketCandles(config, market, { forceRefresh: options.forceRefresh === true }).then((payload) => {
    const candles = latestClosedCandles(payload.candles, market.interval);
    freshness[marketKey(market)] = freshnessForMarket(market, candles, payload.metadata);
    if (candles.length) state.lastProcessedCandleTime[marketKey(market)] = candles[candles.length - 1].time;
  }));
  return jobs.reduce((p, job) => p.then(job), Promise.resolve()).then(() => {
    state.updatedAt = new Date().toISOString();
    writeState(statePath, state);
    writeStatusReports(reportDir, state, config, [], freshness);
    return {
      status: "initialized",
      marketsInitialized: Object.keys(state.lastProcessedCandleTime).length,
      activeMarkets: markets.filter((market) => market.mode !== "watch").length,
      watchMarkets: markets.filter((market) => market.mode === "watch").length,
      lastProcessedCandleTime: state.lastProcessedCandleTime,
      importedHistoricalTrades: 0,
      freshness: freshness
    };
  });
}

function refreshPaperCandles(options) {
  options = options || {};
  const configPath = resolveConfigPath(options.configPath);
  const config = loadConfig(configPath);
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const reportDir = options.reportDir || DEFAULT_REPORT_DIR;
  const state = loadState(statePath, config);
  const markets = normalizeMarkets(config).filter((market) => !options.activeOnly || market.mode !== "watch");
  const freshness = {};
  const jobs = markets.map((market) => () => fetchMarketCandles(config, market, { forceRefresh: true }).then((payload) => {
    const candles = latestClosedCandles(payload.candles, market.interval);
    freshness[marketKey(market)] = freshnessForMarket(market, candles, payload.metadata);
    if (options.advanceBaseline && candles.length) state.lastProcessedCandleTime[marketKey(market)] = candles[candles.length - 1].time;
  }));
  return jobs.reduce((p, job) => p.then(job), Promise.resolve()).then(() => {
    ensureDir(reportDir);
    fs.writeFileSync(path.join(reportDir, "paper-freshness.json"), JSON.stringify({
      refreshedAt: new Date().toISOString(),
      advanceBaseline: !!options.advanceBaseline,
      freshness
    }, null, 2));
    if (options.advanceBaseline) {
      state.updatedAt = new Date().toISOString();
      writeState(statePath, state);
    }
    writeStatusReports(reportDir, state, config, [], freshness);
    return {
      status: "refreshed",
      activeOnly: !!options.activeOnly,
      marketsRefreshed: Object.keys(freshness).length,
      fetchedFromBybit: Object.keys(freshness).filter((key) => freshness[key].fetchedFromBybit).length,
      freshness
    };
  });
}

function setPaperEnabled(options, enabled) {
  options = options || {};
  const configPath = ensureRuntimeConfig(options.configPath);
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const config = loadConfig(configPath);
  const state = loadState(statePath, config);
  const missing = normalizeMarkets(config).filter((market) => !state.lastProcessedCandleTime[marketKey(market)]).map(marketKey);
  if (enabled && missing.length) {
    const error = new Error("Paper state is not initialized. Run npm run paper:init first.");
    error.missingMarkets = missing;
    throw error;
  }
  config.enabled = !!enabled;
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");
  return { status: enabled ? "enabled" : "disabled", enabled: !!enabled };
}

function getPaperStatus(options) {
  options = options || {};
  const configPath = resolveConfigPath(options.configPath);
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const reportDir = options.reportDir || DEFAULT_REPORT_DIR;
  const config = loadConfig(configPath);
  const state = loadState(statePath, config);
  const freshnessReport = path.join(reportDir, "paper-freshness.json");
  let freshness = state.freshness || {};
  if ((!freshness || !Object.keys(freshness).length) && fs.existsSync(freshnessReport)) {
    try {
      freshness = JSON.parse(fs.readFileSync(freshnessReport, "utf8")).freshness || {};
    } catch {
      freshness = {};
    }
  }
  const status = statusPayload(state, config, [], freshness);
  const journal = path.join(reportDir, "paper-journal.jsonl");
  if (fs.existsSync(journal)) {
    const lines = fs.readFileSync(journal, "utf8").split(/\r?\n/).filter(Boolean);
    status.lastJournalEvent = lines.length ? JSON.parse(lines[lines.length - 1]) : null;
  } else {
    status.lastJournalEvent = null;
  }
  return status;
}

function activeSignalDiagnostics(options) {
  options = options || {};
  const configPath = resolveConfigPath(options.configPath);
  const config = loadConfig(configPath);
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const state = loadState(statePath, config);
  const active = normalizeMarkets(config).find((market) => market.mode !== "watch");
  const limit = Math.max(1, Math.min(Number(options.limit || 20), 100));
  if (!active) {
    return Promise.resolve({
      ok: false,
      error: "No active paper market is configured.",
      paperEnabled: !!config.enabled,
      realTradingEnabled: false,
      candidate: configSummary(config),
      warnings: ["No active market found in paper candidate config."]
    });
  }
  return Promise.all([
    fetchMarketCandles(config, active, { forceRefresh: options.refresh === true }),
    data.fetchCandles({
      source: config.source || "bybit",
      symbol: "BTCUSDT",
      interval: "4h",
      limit: 3000,
      forceRefresh: options.refresh === true
    })
  ]).then(([payload, regimeCandles]) => {
    let candles = latestClosedCandles(payload.candles || [], active.interval);
    const freshness = freshnessForMarket(active, candles, payload.metadata);
    const params = Object.assign({}, config.params || {}, {
      regimeMode: canonicalRegimeMode(config),
      fillModel: config.fillModel || "next-open",
      makerFeePct: Number(config.makerFeePct || 0),
      takerFeePct: Number(config.takerFeePct || 0),
      slippageBps: Number(config.slippageBps || 0),
      accountEquity: Number(config.accountEquity || 10000),
      riskPct: Number(config.riskPct || 0.005),
      maxOpenTrades: Number(config.maxOpenTrades || 1),
      maxNotional: Number(config.maxNotionalPerTrade || config.maxNotional || 100000)
    });
    const result = backtest.runBacktestOnCandles({
      symbol: active.symbol,
      interval: active.interval,
      strategy: config.strategy,
      candles,
      regimeCandles,
      params
    });
    const frame = buildDiagnosticFrame(candles, regimeCandles, result.params || params);
    const previews = ((result.diagnostics || {}).lastSignalsPreview || []).slice(-limit);
    const previewByTime = {};
    previews.forEach((item) => { previewByTime[Number(item.time)] = item; });
    const trades = result.tradeList || [];
    const recentRows = frame.slice(-limit).map((row) => diagnosticCandle(row, previewByTime[Number(row.time)], result.params || params, trades));
    const latestRow = frame.length ? frame[frame.length - 1] : null;
    const latestPreview = latestRow ? previewByTime[Number(latestRow.time)] : null;
    const latestTrade = latestRow ? signalTradeForTime(trades, latestRow.time) : null;
    const latestSignal = signalForRow(latestPreview, latestTrade, latestRow ? latestRow.time : null);
    const openPosition = (state.openPositions || []).find((position) => position.key === marketKey(active) || (position.symbol === active.symbol && position.interval === active.interval));
    const checks = latestRow ? checksForSimpleAtrTrendV2(latestRow, result.params || params, state, active, latestPreview) : [];
    const reason = latestReason(latestSignal, latestPreview, latestTrade, checks);
    const warnings = [];
    if (!candles.length) warnings.push("No closed active-market candles were available for diagnostics.");
    if (freshness.isStale) warnings.push("Active-market candle data is stale; refresh=true can attempt to refresh cache before diagnostics.");
    return {
      ok: true,
      paperEnabled: !!config.enabled,
      realTradingEnabled: false,
      candidate: configSummary(config),
      activeMarket: {
        symbol: active.symbol,
        timeframe: active.interval,
        source: config.source || "bybit",
        marketKey: marketKey(active),
        freshness
      },
      latestCandle: latestRow ? compactCandle(latestRow) : null,
      diagnostics: {
        strategy: config.strategy,
        params: result.params || params,
        signal: latestSignal,
        reason,
        checks,
        indicatorSnapshot: latestRow ? indicatorSnapshot(latestRow, result.params || params) : {},
        positionState: {
          hasOpenPosition: !!openPosition,
          side: openPosition ? (openPosition.side || "long") : null,
          barsHeld: openPosition && openPosition.entrySignalTime && latestRow ? Math.max(0, Math.round((latestRow.time - Number(openPosition.entrySignalTime)) / data.intervalToMs(active.interval) * 1000)) : null
        },
        blockerCounts: (result.diagnostics || {}).blockerCounts || {},
        primaryBlocker: (result.diagnostics || {}).primaryBlocker || null
      },
      recentCandles: recentRows,
      nextAction: nextSignalDiagnosticAction(latestSignal, freshness, latestPreview),
      warnings
    };
  });
}

const CANONICAL_BLOCKERS = [
  "emaTrendFailed",
  "pullbackReclaimFailed",
  "rsiBlocked",
  "regimeBlocked",
  "cooldownBlocked",
  "volumeBlocked",
  "atrBlocked",
  "noSetup",
  "positionBlocked",
  "unknown"
];

function blockerAnalytics(options) {
  options = options || {};
  const configPath = resolveConfigPath(options.configPath);
  const config = loadConfig(configPath);
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const state = loadState(statePath, config);
  const active = normalizeMarkets(config).find((market) => market.mode !== "watch") || {};
  const symbol = options.symbol || active.symbol || "ETHUSDT";
  const interval = options.timeframe || options.interval || active.interval || "1h";
  const strategy = options.strategy || config.strategy || "SimpleAtrTrendV2";
  const includeRecentCandles = options.includeRecentCandles !== false;
  const recentLimit = Math.max(1, Math.min(Number(options.recentLimit || 50), 200));
  const limit = options.limit && options.limit !== "auto" ? Number(options.limit) : autoCandleLimit(options.period || "365d", interval);
  const market = Object.assign({}, active, { symbol, interval, mode: "active", limit });
  const warnings = [];
  if (active.symbol && (active.symbol !== symbol || active.interval !== interval)) {
    warnings.push("Selected market differs from the active promoted paper market; this report is read-only research only.");
  }
  if (strategy !== config.strategy) {
    warnings.push("Selected strategy differs from the active promoted paper strategy; this report is read-only research only.");
  }
  return Promise.all([
    fetchMarketCandles(config, market, { forceRefresh: options.refresh === true }),
    data.fetchCandles({
      source: config.source || "bybit",
      symbol: "BTCUSDT",
      interval: "4h",
      limit: 3000,
      forceRefresh: options.refresh === true
    })
  ]).then(([payload, regimeCandles]) => {
    let candles = latestClosedCandles(payload.candles || [], market.interval);
    const freshness = freshnessForMarket(market, candles, payload.metadata);
    if (!candles.length) warnings.push("No closed candles were available for blocker analytics.");
    if (freshness.isStale) warnings.push("Selected market candle data is stale; blocker analytics may not reflect the latest closed candle.");
    const params = Object.assign({}, config.params || {}, {
      regimeMode: canonicalRegimeMode(config),
      fillModel: config.fillModel || "next-open",
      makerFeePct: Number(config.makerFeePct || 0),
      takerFeePct: Number(config.takerFeePct || 0),
      slippageBps: Number(config.slippageBps || 0),
      accountEquity: Number(config.accountEquity || 10000),
      riskPct: Number(config.riskPct || 0.005),
      maxOpenTrades: Number(config.maxOpenTrades || 1),
      maxNotional: Number(config.maxNotionalPerTrade || config.maxNotional || 100000)
    });
    const result = backtest.runBacktestOnCandles({
      symbol,
      interval,
      strategy,
      candles,
      regimeCandles,
      params
    });
    const diagnostics = result.diagnostics || {};
    const rawCounts = diagnostics.blockerCounts || {};
    const candlesAnalyzed = Number(diagnostics.candlesEvaluated || candles.length || 0);
    const frame = buildDiagnosticFrame(candles, regimeCandles, result.params || params);
    const trades = result.tradeList || [];
    const firstPreview = diagnostics.firstSignalsPreview || [];
    const lastPreview = diagnostics.lastSignalsPreview || [];
    const previewByTime = {};
    firstPreview.concat(lastPreview).forEach((item) => { previewByTime[Number(item.time)] = item; });
    const recentFrame = frame.slice(-recentLimit);
    const recentRows = includeRecentCandles
      ? recentFrame.map((row) => blockerAnalyticsCandle(row, previewByTime[Number(row.time)], result.params || params, trades))
      : [];
    const recentBlockerCounts = {};
    recentRows.forEach((row) => {
      (row.blockers || []).forEach((blocker) => {
        recentBlockerCounts[blocker] = (recentBlockerCounts[blocker] || 0) + 1;
      });
    });
    const nearMisses = buildNearMisses(diagnostics.nearMissCandles || [], result.params || params);
    const tradeCount = Number(result.trades || trades.length || 0);
    const entrySignals = tradeCount;
    const exitSignals = trades.filter((trade) => trade.exitReason && trade.exitReason !== "End of data").length || tradeCount;
    const signalCandles = entrySignals + exitSignals;
    const holdCandles = Math.max(0, candlesAnalyzed - signalCandles);
    const blockers = canonicalBlockerRows(rawCounts, recentBlockerCounts, candlesAnalyzed, holdCandles);
    const days = candles.length > 1 ? Math.max(1, (candles[candles.length - 1].time - candles[0].time) / 86400) : 365;
    const mainBlockerRow = blockers.slice().sort((a, b) => b.count - a.count)[0] || null;
    const summary = {
      candlesAnalyzed,
      holdCandles,
      signalCandles,
      entrySignals,
      exitSignals,
      tradeCount,
      signalRatePct: round(candlesAnalyzed ? signalCandles / candlesAnalyzed * 100 : 0, 4),
      approximateSignalsPerMonth: round(signalCandles / days * 30, 2),
      mainBlocker: mainBlockerRow && mainBlockerRow.count ? mainBlockerRow.name : (diagnostics.primaryBlocker || "unknown"),
      recommendation: blockerAnalyticsRecommendation(mainBlockerRow, signalCandles, tradeCount, days, freshness)
    };
    return {
      ok: true,
      realTradingEnabled: false,
      paperEnabled: !!config.enabled,
      candidate: Object.assign(configSummary(config), {
        activeSymbols: normalizeMarkets(config).filter((item) => item.mode !== "watch"),
        watchSymbols: normalizeMarkets(config).filter((item) => item.mode === "watch")
      }),
      search: {
        symbol,
        timeframe: interval,
        strategy,
        period: options.period || "365d",
        limit: options.limit || "auto",
        includeRecentCandles,
        recentLimit,
        source: config.source || "bybit"
      },
      activeMarket: {
        symbol,
        timeframe: interval,
        marketKey: marketKey(market),
        freshness
      },
      summary,
      blockers,
      nearMisses,
      recentCandles: recentRows,
      warnings
    };
  });
}

function canonicalBlockerName(raw) {
  const name = String(raw || "").trim();
  if (CANONICAL_BLOCKERS.indexOf(name) >= 0) return name;
  if (name === "regimeNotBullish" || name === "regimeNotBearish" || name === "neutralRegime") return "regimeBlocked";
  if (name === "volumeTooLow") return "volumeBlocked";
  if (name === "atrMissing" || name === "stopTooClose") return "atrBlocked";
  if (name === "maxOpenTradesReached" || name === "alreadyInPosition") return "positionBlocked";
  if (name === "donchianBreakoutFailed" || name === "retestFailed" || name === "squeezeFailed" || name === "rangeBreakoutFailed" || name === "meanReversionFailed" || name === "momentumStrengthFailed" || name === "relativeStrengthFailed" || name === "adxTooLow" || name === "adxNotRising") return "noSetup";
  return "unknown";
}

function canonicalBlockerRows(rawCounts, recentCounts, candlesAnalyzed, holdCandles) {
  const counts = {};
  CANONICAL_BLOCKERS.forEach((name) => { counts[name] = 0; });
  Object.keys(rawCounts || {}).forEach((raw) => {
    const canonical = canonicalBlockerName(raw);
    counts[canonical] = (counts[canonical] || 0) + Number(rawCounts[raw] || 0);
  });
  return CANONICAL_BLOCKERS.map((name) => {
    const count = Number(counts[name] || 0);
    const pctOfCandles = candlesAnalyzed ? Math.min(100, count / candlesAnalyzed * 100) : 0;
    const pctOfHoldCandles = holdCandles ? Math.min(100, count / holdCandles * 100) : 0;
    return {
      name,
      count,
      pctOfCandles: round(pctOfCandles, 4),
      pctOfHoldCandles: round(pctOfHoldCandles, 4),
      recentCount: Number(recentCounts[name] || 0),
      severity: pctOfCandles >= 30 || pctOfHoldCandles >= 60 ? "HIGH" : pctOfCandles >= 10 || pctOfHoldCandles >= 25 ? "MEDIUM" : "LOW",
      detail: blockerDetail(name)
    };
  }).sort((a, b) => b.count - a.count || CANONICAL_BLOCKERS.indexOf(a.name) - CANONICAL_BLOCKERS.indexOf(b.name));
}

function blockerDetail(name) {
  const details = {
    emaTrendFailed: "EMA trend alignment blocked an entry.",
    pullbackReclaimFailed: "Pullback/reclaim gate did not complete.",
    rsiBlocked: "RSI filter blocked the setup.",
    regimeBlocked: "Regime filter blocked the setup.",
    cooldownBlocked: "Cooldown prevented a fresh entry.",
    volumeBlocked: "Volume filter blocked the setup.",
    atrBlocked: "ATR/stop-distance requirements blocked the setup.",
    noSetup: "No qualifying setup pattern was present.",
    positionBlocked: "Position or max-open-trade state blocked a new entry.",
    unknown: "Unmapped strategy diagnostic blocker."
  };
  return details[name] || details.unknown;
}

function blockerAnalyticsCandle(row, preview, params, trades) {
  const candle = diagnosticCandle(row, preview, params, trades);
  const blockers = uniqueList((preview && preview.blockedBy || []).map(canonicalBlockerName));
  const checks = checksForSimpleAtrTrendV2(row, params, {}, {}, preview);
  return {
    time: candle.time,
    close: candle.close,
    signal: candle.signal,
    reason: candle.reason,
    blockers,
    passedChecks: checks.filter((check) => check.pass === true).map((check) => check.name)
  };
}

function buildNearMisses(items, params) {
  return (items || []).slice(-20).map((item) => {
    const failed = uniqueList((item.blockedBy || []).map(canonicalBlockerName));
    return {
      time: new Date(Number(item.time) * 1000).toISOString(),
      close: item.close,
      failedBlockers: failed,
      passedChecks: CANONICAL_BLOCKERS.filter((name) => failed.indexOf(name) < 0),
      nearMissScore: Math.max(0, round((CANONICAL_BLOCKERS.length - failed.length) / CANONICAL_BLOCKERS.length * 100, 2)),
      detail: failed.length ? "Near miss blocked by " + failed.join(", ") + "." : "Near miss with no mapped blockers.",
      regimeMode: params.regimeMode
    };
  });
}

function blockerAnalyticsRecommendation(mainBlocker, signalCandles, tradeCount, days, freshness) {
  if (freshness && freshness.isStale) {
    return { action: "REFRESH_MARKET_DATA", reason: "Market data is stale; refresh before reading recent blocker behavior." };
  }
  if (!tradeCount) {
    return { action: "REVIEW_BLOCKERS", reason: "No historical trades were produced by this read-only blocker run. Inspect the dominant blocker before considering any candidate change." };
  }
  const tradesPerMonth = tradeCount / Math.max(1, days) * 30;
  if (tradesPerMonth < 3) {
    return { action: "EXPECT_SLOW_FORWARD_TEST", reason: "The strategy trades infrequently in this market/timeframe, so 1h paper observation may need more time." };
  }
  if (mainBlocker && mainBlocker.severity === "HIGH") {
    return { action: "MONITOR_DOMINANT_BLOCKER", reason: mainBlocker.name + " is the dominant historical blocker. This is diagnostic only; no strategy rule changed." };
  }
  if (signalCandles > 0) {
    return { action: "CONTINUE_OBSERVING", reason: "Historical diagnostics produced signals. Continue paper observation without enabling real trading." };
  }
  return { action: "OBSERVE_MORE", reason: "Blocker analytics is diagnostic only and does not recommend promotion or real trading." };
}

function autoCandleLimit(period, interval) {
  const days = Number(String(period || "365d").replace(/d$/i, "")) || 365;
  const seconds = data.intervalToMs(interval || "1h") / 1000;
  const candles = Math.ceil(days * 86400 / Math.max(60, seconds)) + 300;
  return Math.max(600, Math.min(candles, 50000));
}

function buildDiagnosticFrame(candles, regimeCandles, params) {
  const normalized = data.normalizeCandles(candles || []);
  const mapped = regime.mapRegimeToCandles(normalized, data.normalizeCandles(regimeCandles || []));
  return indicators.buildIndicatorFrame(mapped, {
    emaReclaim: params.emaFast || 20,
    emaTrendFast: params.emaSlow || 50,
    emaSlow: params.emaTrend || params.emaTrendLength,
    atrPeriod: 14,
    adxPeriod: 14
  }).map((row, index) => Object.assign({}, row, {
    btcRegime: mapped[index] ? mapped[index].btcRegime : "neutral",
    btcRegimeTime: mapped[index] ? mapped[index].btcRegimeTime : null
  }));
}

function compactCandle(row) {
  return {
    time: row.time,
    isoTime: new Date(Number(row.time) * 1000).toISOString(),
    open: row.open,
    high: row.high,
    low: row.low,
    close: row.close,
    volume: row.volume
  };
}

function signalTradeForTime(trades, time) {
  return (trades || []).find((trade) => Number(trade.entrySignalTime || trade.entryTime) === Number(time))
    || (trades || []).find((trade) => Number(trade.exitSignalTime || trade.exitTime) === Number(time))
    || null;
}

function signalForRow(preview, trade, rowTime) {
  const time = Number(rowTime || (preview && preview.time) || 0);
  if (trade && Number(trade.exitSignalTime || trade.exitTime) === time) return "EXIT";
  if (trade && Number(trade.entrySignalTime || trade.entryTime) === time) return trade.side === "short" ? "SHORT" : "BUY";
  if (preview && preview.entry) return "BUY";
  return preview ? "HOLD" : "NONE";
}

function diagnosticCandle(row, preview, params, trades) {
  const trade = signalTradeForTime(trades, row.time);
  const signal = signalForRow(preview, trade, row.time);
  return {
    time: new Date(Number(row.time) * 1000).toISOString(),
    close: row.close,
    signal,
    reason: latestReason(signal, preview, trade, checksForSimpleAtrTrendV2(row, params, {}, {}, preview)),
    summaryChecks: uniqueList(preview && preview.blockedBy || []).slice(0, 4)
  };
}

function checksForSimpleAtrTrendV2(row, params, state, market, preview) {
  const stopDistance = Number(row.atr14 || 0) * Number(params.atrMultiplier || 0);
  const size = stopDistance > 0 ? Number(params.accountEquity || 10000) * Number(params.riskPct || 0.005) / stopDistance : null;
  const notional = size ? row.close * size : null;
  const openPosition = state && Array.isArray(state.openPositions)
    ? state.openPositions.find((position) => position.key === marketKey(market) || (position.symbol === market.symbol && position.interval === market.interval))
    : null;
  const checks = [
    {
      name: "atr available",
      pass: !!(row.atr14 && row.atr14 > 0),
      value: round(row.atr14 || 0, 8),
      threshold: "> 0",
      detail: "ATR must be available so the strategy can size the virtual position and stop."
    },
    {
      name: "regime trend",
      pass: trendModePasses(row, params),
      value: trendModeValue(row, params),
      threshold: params.regimeMode || "looseBtcBull",
      detail: "Matches the SimpleAtrTrendV2 regimeMode gate used by the paper strategy."
    },
    {
      name: "ema trend",
      pass: !!(row.ema20 > row.ema50 && row.close > row.ema50),
      value: { close: round(row.close, 8), ema20: round(row.ema20, 8), ema50: round(row.ema50, 8) },
      threshold: "ema20 > ema50 and close > ema50",
      detail: "SimpleAtrTrendV2 requires short-term EMA trend alignment."
    },
    {
      name: "rsi range",
      pass: params.useRsiFilter ? !!(row.rsi14 >= params.rsiMin && row.rsi14 <= params.rsiMax) : null,
      value: round(row.rsi14 || 0, 4),
      threshold: params.useRsiFilter ? String(params.rsiMin) + "..." + String(params.rsiMax) : "disabled",
      detail: "When enabled, RSI must be inside the configured pullback/reclaim range."
    },
    {
      name: "volume filter",
      pass: params.volumeFilter ? !!(row.volumeMa20 && row.volume > row.volumeMa20) : null,
      value: { volume: round(row.volume || 0, 4), volumeMa20: round(row.volumeMa20 || 0, 4) },
      threshold: params.volumeFilter ? "volume > volumeMa20" : "disabled",
      detail: "Volume filtering is checked only when enabled in params."
    },
    {
      name: "position state",
      pass: !openPosition,
      value: openPosition ? "open position exists" : "flat",
      threshold: "flat",
      detail: "A new active entry requires no open virtual position for this market."
    },
    {
      name: "position size",
      pass: Number.isFinite(size) && size > 0 && notional <= Number(params.maxNotional || 100000),
      value: { stopDistance: round(stopDistance, 8), notional: round(notional || 0, 4) },
      threshold: "<= " + String(params.maxNotional || 100000),
      detail: "Risk sizing must produce a finite size within max notional."
    }
  ];
  const blockers = uniqueList(preview && preview.blockedBy || []);
  blockers.forEach((blocker) => {
    if (!checks.some((check) => check.detail.indexOf(blocker) >= 0 || check.name === blocker)) {
      checks.push({
        name: blocker,
        pass: false,
        value: blocker,
        threshold: "not present",
        detail: "Backtest diagnostic blocker from the active paper strategy path."
      });
    }
  });
  return checks;
}

function trendModePasses(row, params) {
  const mode = params.regimeMode || "strictBtcBull";
  if (mode === "strictBtcBull") return row.btcRegime === "bullish";
  if (mode === "looseBtcBull") return row.btcClose4h && row.btcEma200_4h && row.btcClose4h > row.btcEma200_4h;
  if (mode === "symbolTrend") return row.close > row.ema200;
  if (mode === "symbolFastTrend") return row.ema50 > row.ema200;
  if (mode === "noRegime") return true;
  return row.btcRegime === "bullish";
}

function trendModeValue(row, params) {
  const mode = params.regimeMode || "strictBtcBull";
  if (mode === "symbolFastTrend") return { ema50: round(row.ema50, 8), ema200: round(row.ema200, 8) };
  if (mode === "symbolTrend") return { close: round(row.close, 8), ema200: round(row.ema200, 8) };
  if (mode === "looseBtcBull") return { btcClose4h: round(row.btcClose4h || 0, 8), btcEma200_4h: round(row.btcEma200_4h || 0, 8) };
  return row.btcRegime;
}

function indicatorSnapshot(row, params) {
  return {
    close: row.close,
    emaFast: row.ema20,
    emaSlow: row.ema50,
    emaTrend: row.ema200,
    atr: row.atr14,
    rsi: row.rsi14,
    adx: row.adx14,
    volume: row.volume,
    volumeMa20: row.volumeMa20,
    btcRegime: row.btcRegime,
    regimeMode: params.regimeMode
  };
}

function latestReason(signal, preview, trade, checks) {
  if (signal === "BUY" || signal === "SHORT") return "Entry signal matched the active paper strategy path on this candle.";
  if (signal === "EXIT") return (trade && trade.exitReason) || "Exit signal matched the active paper strategy path on this candle.";
  const blockers = uniqueList(preview && preview.blockedBy || []);
  if (blockers.length) return "No entry signal because " + blockers.slice(0, 4).join(", ") + ".";
  const failed = (checks || []).filter((check) => check.pass === false).map((check) => check.name);
  if (failed.length) return "No entry signal because " + failed.slice(0, 4).join(", ") + ".";
  return signal === "NONE" ? "No closed candle diagnostics were available." : "No entry or exit signal on the latest active-market candle.";
}

function nextSignalDiagnosticAction(signal, freshness, preview) {
  if (freshness && freshness.isStale) {
    return { action: "CHECK_STRATEGY_ACTIVITY", reason: "Active-market data is stale; rerun diagnostics with refresh=true before judging recent signal activity." };
  }
  if (signal === "BUY" || signal === "SHORT" || signal === "EXIT") {
    return { action: "READY_TO_TICK", reason: "A strategy signal is visible in diagnostics. Paper tick still remains simulated only." };
  }
  const blockers = uniqueList(preview && preview.blockedBy || []);
  if (blockers.length) {
    return { action: "OBSERVE_MORE", reason: "The latest active candle is blocked by " + blockers.slice(0, 3).join(", ") + "." };
  }
  return { action: "WAIT", reason: "No actionable active-market strategy signal is visible on the latest closed candle." };
}

function uniqueList(items) {
  const seen = {};
  return (items || []).filter((item) => {
    const key = String(item);
    if (seen[key]) return false;
    seen[key] = true;
    return true;
  });
}

function processMarket(config, state, market, regimeCandles, options) {
  return fetchMarketCandles(config, market, { forceRefresh: options.forceRefresh === true }).then((payload) => {
    let candles = payload.candles;
    const warnings = [];
    if (!options.includeOpenCandle) candles = latestClosedCandles(candles, market.interval);
    const freshness = freshnessForMarket(market, candles, payload.metadata);
    state.freshness = state.freshness || {};
    state.freshness[marketKey(market)] = freshness;
    if (!candles.length) return warningEvent(config, state, market, "No closed candles available.");
    if (freshness.isStale && !options.allowStale) {
      return warningEvent(config, state, market, "Market data is stale; skipping paper processing.");
    }
    if (freshness.isStale && options.allowStale) {
      warnings.push("Market data is stale; --allow-stale override used.");
    }
    warnOnGaps(candles, market, warnings);
    const key = marketKey(market);
    const lastProcessed = Number(state.lastProcessedCandleTime[key] || 0);
    if (!lastProcessed && !options.allowBootstrapImport) {
      return warningEvent(config, state, market, "Market not initialized. Run npm run paper:init first.");
    }
    const newCandles = candles.filter((candle) => candle.time > lastProcessed);
    if (!newCandles.length) {
      return warningEvents(config, state, market, warnings);
    }

    const params = Object.assign({}, config.params || {}, {
      regimeMode: canonicalRegimeMode(config),
      fillModel: config.fillModel || "next-open",
      makerFeePct: Number(config.makerFeePct || 0),
      takerFeePct: Number(config.takerFeePct || 0),
      slippageBps: Number(config.slippageBps || 0),
      accountEquity: Number(config.accountEquity || 10000),
      riskPct: Number(config.riskPct || 0.005),
      maxOpenTrades: Number(config.maxOpenTrades || 1),
      maxNotional: Number(config.maxNotionalPerTrade || config.maxNotional || 100000)
    });
    const result = backtest.runBacktestOnCandles({
      symbol: market.symbol,
      interval: market.interval,
      strategy: config.strategy,
      candles,
      regimeCandles,
      params
    });
    const events = warningEvents(config, state, market, warnings);
    if (!config.enabled) {
      addDisabledSignalEvents(config, state, market, result, lastProcessed, events);
    } else {
      applyResultToState(config, state, market, result, lastProcessed, events);
    }
    state.lastProcessedCandleTime[key] = candles[candles.length - 1].time;
    state.processedCandles += newCandles.length;
    return events;
  });
}

function addDisabledSignalEvents(config, state, market, result, lastProcessed, events) {
  const key = marketKey(market);
  const trades = result.tradeList || [];
  trades.forEach((trade) => {
    const candleTime = Number(trade.entrySignalTime || trade.entryTime || 0);
    if (candleTime > lastProcessed) {
      addEvent(events, config, market, "SIGNAL", "Paper disabled; signal observed only.", trade);
      state.pendingSignals.push({ marketKey: key, candleTime, symbol: market.symbol, interval: market.interval });
    }
  });
  if (state.pendingSignals.length > 500) state.pendingSignals = state.pendingSignals.slice(-500);
}

function applyResultToState(config, state, market, result, lastProcessed, events) {
  const key = marketKey(market);
  const trades = result.tradeList || [];
  const mode = market.mode === "watch" ? "watch" : "active";
  if (mode === "watch") {
    trades.forEach((trade) => {
      const candleTime = Number(trade.entrySignalTime || trade.entryTime || 0);
      if (candleTime > lastProcessed) {
        addEvent(events, config, market, "SIGNAL", "Watch-only signal; no virtual trade opened.", trade);
        state.pendingSignals.push({ marketKey: key, candleTime, symbol: market.symbol, interval: market.interval });
      }
    });
    if (state.pendingSignals.length > 500) state.pendingSignals = state.pendingSignals.slice(-500);
    return;
  }
  state.openPositions = state.openPositions.filter((position) => position.key !== key);
  trades.forEach((trade) => {
    if (trade.exitReason === "End of data") {
      state.openPositions.push(openPositionFromTrade(key, market, trade));
      addEvent(events, config, market, "SIGNAL", "Open virtual position carried forward", trade);
      return;
    }
    const id = tradeId(market, trade);
    if (state.closedTrades.some((item) => item.id === id)) return;
    if (Number(trade.exitFillTime || trade.exitTime) <= lastProcessed) return;
    if (state.openPositions.some((position) => position.key === key)) {
      addEvent(events, config, market, "SKIP", "open position already exists for market", trade);
      state.skippedSignals = Number(state.skippedSignals || 0) + 1;
      return;
    }
    if (state.openPositions.length >= Number(config.maxOpenTrades || 1)) {
      addEvent(events, config, market, "SKIP", "maxOpenTrades reached", trade);
      state.skippedSignals = Number(state.skippedSignals || 0) + 1;
      return;
    }
    const closed = Object.assign({ id, key, symbol: market.symbol, interval: market.interval }, trade);
    state.closedTrades.push(closed);
    state.realizedPnl = round(state.realizedPnl + Number(trade.pnl || trade.netPnl || 0), 4);
    state.cumulativeFees = round(state.cumulativeFees + Number(trade.feePaid || 0), 4);
    state.cumulativeSlippage = round(state.cumulativeSlippage + Number(trade.slippagePaid || 0), 4);
    addEvent(events, config, market, "ENTRY", "Virtual entry from audited strategy", trade);
    addEvent(events, config, market, "EXIT", trade.exitReason || "Virtual exit", trade);
  });
  state.unrealizedPnl = round(state.openPositions.reduce((sum, position) => sum + Number(position.unrealizedPnl || 0), 0), 4);
}

function openPositionFromTrade(key, market, trade) {
  return {
    id: tradeId(market, trade),
    key,
    symbol: market.symbol,
    interval: market.interval,
    side: trade.side || "long",
    entrySignalTime: trade.entrySignalTime,
    entryFillTime: trade.entryFillTime || trade.entryTime,
    entryFillPrice: trade.entryFillPrice || trade.entryPrice,
    lastPrice: trade.exitSignalPrice || trade.exitPrice,
    unrealizedPnl: trade.pnl || 0,
    fillModel: trade.fillModel,
    size: trade.size,
    notional: trade.notional
  };
}

function addEvent(events, config, market, eventType, reason, trade) {
  const candleTime = eventType === "EXIT"
    ? Number(trade.exitFillTime || trade.exitTime || trade.exitSignalTime || 0)
    : Number(trade.entryFillTime || trade.entryTime || trade.entrySignalTime || 0);
  const tradeKey = tradeId(market, trade);
  const event = {
    eventId: eventId(market, eventType, reason, candleTime, tradeKey),
    tradeId: tradeKey,
    marketKey: marketKey(market),
    candleTime,
    processedAt: new Date().toISOString(),
    mode: market.mode === "watch" ? "watch" : "active",
    timestamp: new Date().toISOString(),
    symbol: market.symbol,
    interval: market.interval,
    eventType,
    reason,
    signalPrice: trade.entrySignalPrice || trade.exitSignalPrice || "",
    fillPrice: eventType === "EXIT" ? (trade.exitFillPrice || trade.exitPrice || "") : (trade.entryFillPrice || trade.entryPrice || ""),
    size: trade.size || "",
    feePaid: trade.feePaid || 0,
    slippagePaid: trade.slippagePaid || 0,
    netPnl: eventType === "EXIT" ? (trade.pnl || trade.netPnl || 0) : "",
    accountEquity: config.accountEquity,
    strategy: config.strategy,
    paramsHash: paramsHash(config)
  };
  if (events.some((item) => item.eventId === event.eventId)) return;
  events.push(event);
}

function warningEvent(config, state, market, message) {
  return warningEvents(config, state, market, [message]);
}

function warningEvents(config, state, market, warnings) {
  return warnings.map((message) => {
    state.warnings.push(market.symbol + " " + market.interval + ": " + message);
    return {
      eventId: eventId(market, "WARNING", message, 0, ""),
      tradeId: "",
      marketKey: marketKey(market),
      candleTime: "",
      processedAt: new Date().toISOString(),
      mode: market.mode === "watch" ? "watch" : "active",
      timestamp: new Date().toISOString(),
      symbol: market.symbol,
      interval: market.interval,
      eventType: "WARNING",
      reason: message,
      signalPrice: "",
      fillPrice: "",
      size: "",
      feePaid: 0,
      slippagePaid: 0,
      netPnl: "",
      accountEquity: state.accountEquity,
      strategy: config.strategy,
      paramsHash: paramsHash(config)
    };
  });
}

function fetchMarketCandles(config, market, options) {
  return data.fetchCandles({
    source: config.source || "bybit",
    symbol: market.symbol,
    interval: market.interval,
    limit: market.limit || 600,
    forceRefresh: options && options.forceRefresh,
    withMetadata: true
  }).then((payload) => {
    if (Array.isArray(payload)) return { candles: data.normalizeCandles(payload), metadata: {} };
    return { candles: data.normalizeCandles(payload.candles), metadata: payload.metadata || {} };
  });
}

function latestClosedCandles(candles, interval) {
  const intervalSeconds = data.intervalToMs(interval) / 1000;
  const nowSeconds = Math.floor(Date.now() / 1000);
  return (candles || []).filter((candle) => Number(candle.time || 0) + intervalSeconds <= nowSeconds);
}

function freshnessForMarket(market, candles, metadata) {
  const latest = candles.length ? candles[candles.length - 1].time : null;
  const intervalSeconds = data.intervalToMs(market.interval) / 1000;
  const age = latest ? Math.max(0, Math.floor(Date.now() / 1000) - latest) : null;
  const threshold = staleThresholdSeconds(market.interval);
  return {
    latestCandleTime: latest,
    expectedIntervalSeconds: intervalSeconds,
    latestClosedCandleAgeSeconds: age,
    staleThresholdSeconds: threshold,
    isStale: age === null ? true : age > threshold,
    cacheHit: !!(metadata && metadata.cacheHit),
    cacheMiss: !!(metadata && metadata.cacheMiss),
    fetchedFromBybit: !!(metadata && metadata.fetchedFromBybit),
    bybitRequests: metadata ? Number(metadata.bybitRequests || 0) : 0
  };
}

function staleThresholdSeconds(interval) {
  if (String(interval) === "15m") return 45 * 60;
  if (String(interval) === "1h") return 3 * 60 * 60;
  if (String(interval) === "4h" || String(interval) === "240") return 10 * 60 * 60;
  return Math.max(3 * data.intervalToMs(interval) / 1000, 45 * 60);
}

function warnOnGaps(candles, market, warnings) {
  const expected = data.intervalToMs(market.interval) / 1000;
  for (let i = Math.max(1, candles.length - 30); i < candles.length; i += 1) {
    if (candles[i].time - candles[i - 1].time > expected * 1.5) {
      warnings.push("Large candle gap detected near " + candles[i].time);
      return;
    }
  }
  const lastAge = Math.floor(Date.now() / 1000) - candles[candles.length - 1].time;
  if (lastAge > expected * 3) warnings.push("Candle cache may be stale.");
}

function loadState(file, config) {
  if (!fs.existsSync(file)) return initialState(config);
  try {
    return Object.assign(initialState(config), JSON.parse(fs.readFileSync(file, "utf8")));
  } catch {
    return initialState(config);
  }
}

function initialState(config) {
  return {
    accountEquity: Number(config.accountEquity || 10000),
    openPositions: [],
    closedTrades: [],
    pendingSignals: [],
    skippedSignals: 0,
    lastProcessedCandleTime: {},
    cumulativeFees: 0,
    cumulativeSlippage: 0,
    realizedPnl: 0,
    unrealizedPnl: 0,
    equityCurve: [],
    warnings: [],
    processedCandles: 0,
    startedAt: new Date().toISOString(),
    updatedAt: null
  };
}

function writeState(file, state) {
  fs.writeFileSync(file, JSON.stringify(state, null, 2));
}

function appendJournal(reportDir, events) {
  if (!events.length) return;
  ensureDir(reportDir);
  const jsonl = path.join(reportDir, "paper-journal.jsonl");
  fs.appendFileSync(jsonl, events.map((event) => JSON.stringify(event)).join("\n") + "\n");
  const csvFile = path.join(reportDir, "paper-journal.csv");
  const cols = ["timestamp", "symbol", "interval", "eventType", "reason", "signalPrice", "fillPrice", "size", "feePaid", "slippagePaid", "netPnl", "accountEquity", "strategy", "paramsHash"];
  if (!fs.existsSync(csvFile)) fs.writeFileSync(csvFile, cols.join(",") + "\n");
  fs.appendFileSync(csvFile, events.map((event) => cols.map((col) => csv(event[col])).join(",")).join("\n") + "\n");
}

function writeStatusReports(reportDir, state, config, events, freshness) {
  ensureDir(reportDir);
  fs.writeFileSync(path.join(reportDir, "paper-status.json"), JSON.stringify(statusPayload(state, config, events, freshness), null, 2));
  fs.writeFileSync(path.join(reportDir, "paper-summary.json"), JSON.stringify(summaryPayload(state, config), null, 2));
}

function statusPayload(state, config, events, freshness) {
  const markets = normalizeMarkets(config);
  return {
    initialized: markets.every((market) => !!state.lastProcessedCandleTime[marketKey(market)]),
    enabled: !!config.enabled,
    activeMarkets: markets.filter((market) => market.mode !== "watch").map(marketKey),
    watchMarkets: markets.filter((market) => market.mode === "watch").map(marketKey),
    openPositions: state.openPositions,
    closedTrades: state.closedTrades.slice(-50),
    closedTradesCount: state.closedTrades.length,
    equity: state.accountEquity,
    realizedPnL: state.realizedPnl,
    unrealizedPnL: state.unrealizedPnl,
    totalFees: state.cumulativeFees,
    totalSlippage: state.cumulativeSlippage,
    latestEvents: events.slice(-25),
    lastSignals: events.slice(-25),
    lastProcessedCandle: state.lastProcessedCandleTime,
    freshness: freshness || state.freshness || {},
    warnings: state.warnings,
    candidate: configSummary(config)
  };
}

function summaryPayload(state, config) {
  const trades = state.closedTrades;
  const wins = trades.filter((trade) => Number(trade.pnl || trade.netPnl || 0) > 0);
  const markets = normalizeMarkets(config);
  return {
    startedAt: state.startedAt,
    updatedAt: state.updatedAt,
    paperUptimePeriod: { startedAt: state.startedAt, updatedAt: state.updatedAt },
    processedCandles: state.processedCandles,
    activeEntries: trades.length + state.openPositions.length,
    watchSignals: state.pendingSignals.length,
    skippedSignals: Number(state.skippedSignals || 0),
    entries: trades.length + state.openPositions.length,
    exits: trades.length,
    winRate: round(trades.length ? wins.length / trades.length * 100 : 0),
    netReturn: round((state.accountEquity / Number(config.accountEquity || 10000) - 1) * 100),
    maxDrawdown: round(maxDrawdown(state.equityCurve)),
    feesPaid: state.cumulativeFees,
    slippagePaid: state.cumulativeSlippage,
    totalFees: state.cumulativeFees,
    totalSlippage: state.cumulativeSlippage,
    equity: state.accountEquity,
    activeMarkets: markets.filter((market) => market.mode !== "watch").length,
    watchMarkets: markets.filter((market) => market.mode === "watch").length,
    expectedBacktestMetrics: {
      source: "reports/candidate-validation.json",
      note: "Compare cautiously; paper simulation is forward-only and simulated."
    }
  };
}

function summary(state, config, events, status) {
  return {
    status,
    enabled: !!config.enabled,
    events: events.length,
    openPositions: state.openPositions.length,
    closedTrades: state.closedTrades.length,
    accountEquity: state.accountEquity,
    realizedPnL: state.realizedPnl,
    unrealizedPnL: state.unrealizedPnl,
    warnings: state.warnings,
    freshness: state.freshness || {}
  };
}

function rebuildStateFromJournal(config, journalFile) {
  const state = initialState(config);
  if (!fs.existsSync(journalFile)) return state;
  fs.readFileSync(journalFile, "utf8").split(/\r?\n/).filter(Boolean).forEach((line) => {
    const event = JSON.parse(line);
    if (event.eventType === "EXIT") {
      state.realizedPnl = round(state.realizedPnl + Number(event.netPnl || 0), 4);
      state.cumulativeFees = round(state.cumulativeFees + Number(event.feePaid || 0), 4);
      state.cumulativeSlippage = round(state.cumulativeSlippage + Number(event.slippagePaid || 0), 4);
    }
  });
  state.accountEquity = round(Number(config.accountEquity || 10000) + state.realizedPnl, 4);
  return state;
}

function normalizeMarkets(config) {
  return (config.symbols || []).map((item) => typeof item === "string"
    ? { symbol: item, interval: "1h", mode: "active" }
    : item);
}

function configSummary(config) {
  return {
    enabled: !!config.enabled,
    strategy: config.strategy,
    regimeMode: canonicalRegimeMode(config),
    fillModel: config.fillModel,
    makerFeePct: config.makerFeePct,
    takerFeePct: config.takerFeePct,
    slippageBps: config.slippageBps,
    paramsHash: paramsHash(config)
  };
}

function paramsHash(config) {
  return crypto.createHash("sha1").update(JSON.stringify({ strategy: config.strategy, regimeMode: canonicalRegimeMode(config), params: config.params })).digest("hex").slice(0, 12);
}

function canonicalRegimeMode(config) {
  return (config.params && config.params.regimeMode) || config.regimeMode;
}

function tradeId(market, trade) {
  return [market.symbol, market.interval, trade.entryFillTime || trade.entryTime, trade.exitFillTime || trade.exitTime, trade.exitReason].join(":");
}

function eventId(market, eventType, reason, candleTime, tradeKey) {
  return crypto.createHash("sha1").update([marketKey(market), eventType, reason, candleTime, tradeKey].join("|")).digest("hex").slice(0, 16);
}

function marketKey(market) {
  return market.symbol + ":" + market.interval;
}

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function csv(value) {
  const text = String(value === undefined || value === null ? "" : value);
  return /[",\n]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
}

function maxDrawdown(curve) {
  let peak = curve.length ? curve[0].equity : 0;
  let worst = 0;
  curve.forEach((point) => {
    peak = Math.max(peak, point.equity);
    worst = Math.max(worst, peak ? (peak - point.equity) / peak * 100 : 0);
  });
  return worst;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round((Number(value) || 0) * factor) / factor;
}

module.exports = {
  runPaperTick,
  initializePaper,
  refreshPaperCandles,
  activeSignalDiagnostics,
  blockerAnalytics,
  setPaperEnabled,
  getPaperStatus,
  rebuildStateFromJournal,
  initialState,
  statusPayload,
  summaryPayload
};
