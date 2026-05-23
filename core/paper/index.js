const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const data = require("../data");
const backtest = require("../backtest");

const DEFAULT_STATE_PATH = path.join(process.cwd(), "data", "paper-state.json");
const DEFAULT_REPORT_DIR = path.join(process.cwd(), "reports");

function runPaperTick(options) {
  options = options || {};
  const configPath = options.configPath || path.join(process.cwd(), "config", "paper-candidate.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
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
  const configPath = options.configPath || path.join(process.cwd(), "config", "paper-candidate.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
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
    const candles = payload.candles.slice(0, -1);
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
  const configPath = options.configPath || path.join(process.cwd(), "config", "paper-candidate.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const reportDir = options.reportDir || DEFAULT_REPORT_DIR;
  const state = loadState(statePath, config);
  const markets = normalizeMarkets(config);
  const freshness = {};
  const jobs = markets.map((market) => () => fetchMarketCandles(config, market, { forceRefresh: true }).then((payload) => {
    const candles = payload.candles.slice(0, -1);
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
      marketsRefreshed: Object.keys(freshness).length,
      fetchedFromBybit: Object.keys(freshness).filter((key) => freshness[key].fetchedFromBybit).length,
      freshness
    };
  });
}

function setPaperEnabled(options, enabled) {
  options = options || {};
  const configPath = options.configPath || path.join(process.cwd(), "config", "paper-candidate.json");
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
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
  const configPath = options.configPath || path.join(process.cwd(), "config", "paper-candidate.json");
  const statePath = options.statePath || DEFAULT_STATE_PATH;
  const reportDir = options.reportDir || DEFAULT_REPORT_DIR;
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
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

function processMarket(config, state, market, regimeCandles, options) {
  return fetchMarketCandles(config, market, { forceRefresh: options.forceRefresh === true }).then((payload) => {
    let candles = payload.candles;
    const warnings = [];
    if (!options.includeOpenCandle && candles.length) candles = candles.slice(0, -1);
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
      regimeMode: config.regimeMode,
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
    regimeMode: config.regimeMode,
    fillModel: config.fillModel,
    makerFeePct: config.makerFeePct,
    takerFeePct: config.takerFeePct,
    slippageBps: config.slippageBps,
    paramsHash: paramsHash(config)
  };
}

function paramsHash(config) {
  return crypto.createHash("sha1").update(JSON.stringify({ strategy: config.strategy, regimeMode: config.regimeMode, params: config.params })).digest("hex").slice(0, 12);
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
  setPaperEnabled,
  getPaperStatus,
  rebuildStateFromJournal,
  initialState,
  statusPayload,
  summaryPayload
};
