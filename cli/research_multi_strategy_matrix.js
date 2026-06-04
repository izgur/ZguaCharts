const backtest = require("../core/backtest");
const data = require("../core/data");
const strategiesRegistry = require("../core/strategies");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

const AUTO_STRATEGIES = [
  "SimpleAtrTrendV2",
  "PullbackReclaimV2",
  "EmaBounceV2",
  "BreakoutRetestV2",
  "RangeExpansionV2",
  "RelativeStrengthV2",
  "EmaPullbackContinuation",
  "TrendBreakoutRetest",
  "VolatilitySqueezeBreakout",
  "MeanReversionInBullRegime",
  "MomentumContinuation",
  "RegimeDonchian20",
  "RegimeDonchianCloseConfirm",
  "RegimePullbackTrend",
  "ConservativeTrend",
  "ConservativeTrendLoose",
  "MomentumScalping",
  "MeanReversion",
  "PullbackTrend"
];

function parseCsv(value, fallback) {
  if (!value || value === "auto") return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function timeframeHours(timeframe) {
  const raw = String(timeframe || "1h").trim().toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 60) / 60;
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1);
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24;
  return 1;
}

function autoLimitFor(timeframe, days, cap) {
  const bars = Math.ceil(Number(days || 365) * 24 / timeframeHours(timeframe));
  return Math.max(100, Math.min(Number(cap || 5000), bars));
}

function strategyInfo(name) {
  try {
    return strategiesRegistry.getStrategy(name);
  } catch (error) {
    return null;
  }
}

function classify(row) {
  if (row.status === "ERROR" || row.status === "UNSUPPORTED") return row.status;
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK") return "PASS";
  if (row.trades >= 20 && row.totalReturnPct >= 0 && row.profitFactor >= 1) return "WARN";
  if (reason === "NO_TRADES") return "NO_TRADES";
  return reason === "UNSUPPORTED" ? "UNSUPPORTED" : "FAIL";
}

function scoreRow(row) {
  const statusBonus = row.status === "PASS" ? 30 : row.status === "WARN" ? 12 : -25;
  let score = statusBonus
    + Math.min(Number(row.trades || 0), 150) * 0.14
    + Math.min(Number(row.tradesPerMonth || 0), 12) * 1.7
    + Number(row.profitFactor || 0) * 18
    + Number(row.totalReturnPct || 0) * 2.2
    + Number(row.expectancyPctPerTrade || 0) * 10
    - Number(row.maxDrawdownPct || 0) * 1.25;
  if (row.trades < 20) score -= 18;
  if (row.tradesPerMonth < 2) score -= 8;
  if (row.profitFactor < 1.1) score -= 18;
  if (row.totalReturnPct < 0) score -= 20;
  if (row.maxDrawdownPct > 25) score -= 20;
  return round(score, 5);
}

function compactResult(result, context) {
  const trades = Number(result.trades || 0);
  const row = {
    rank: null,
    strategy: context.strategy,
    symbol: context.symbol,
    timeframe: context.timeframe,
    paramsSource: context.paramsSource,
    status: "FAIL",
    trades,
    tradesPerMonth: round(trades / Math.max(1, context.days) * 30, 2),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    avgBarsHeld: round(result.avgBarsHeld || 0, 4),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades, 4) : 0,
    score: 0,
    qualityStatus: "FAIL",
    mainFailureReason: null,
    warnings: result.warnings || [],
    isActiveBaseline: !!context.isActiveBaseline
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  row.qualityStatus = row.status === "PASS" || row.status === "WARN" ? row.status : "FAIL";
  row.score = scoreRow(row);
  return row;
}

function unsupportedRow(context, reason) {
  return {
    rank: null,
    strategy: context.strategy,
    symbol: context.symbol,
    timeframe: context.timeframe,
    paramsSource: "unsupported",
    status: "UNSUPPORTED",
    trades: 0,
    tradesPerMonth: 0,
    totalReturnPct: 0,
    profitFactor: 0,
    maxDrawdownPct: 0,
    winRate: 0,
    avgBarsHeld: 0,
    expectancyPctPerTrade: 0,
    score: -999,
    qualityStatus: "FAIL",
    mainFailureReason: "UNSUPPORTED",
    warnings: [reason],
    isActiveBaseline: false
  };
}

function buildSummary(rows) {
  const ranked = rows.filter((row) => row.status !== "UNSUPPORTED").sort(sortRows);
  const active = rows.find((row) => row.isActiveBaseline) || null;
  const bestOverall = ranked[0] || null;
  const bestByStrategy = {};
  const bestBySymbol = {};
  const bestByTimeframe = {};
  ranked.forEach((row) => {
    if (!bestByStrategy[row.strategy]) bestByStrategy[row.strategy] = row;
    if (!bestBySymbol[row.symbol]) bestBySymbol[row.symbol] = row;
    if (!bestByTimeframe[row.timeframe]) bestByTimeframe[row.timeframe] = row;
  });
  const passCount = rows.filter((row) => row.status === "PASS" || row.status === "WARN").length;
  const unsupportedCount = rows.filter((row) => row.status === "UNSUPPORTED").length;
  const failCount = rows.length - passCount - unsupportedCount;
  const bestNonBaseline = ranked.find((row) => !row.isActiveBaseline) || null;
  let recommendation = { action: "NO_ACTION", reason: "No candidate rows were evaluated." };
  if (active && bestOverall && bestOverall.isActiveBaseline) {
    recommendation = { action: "KEEP_BASELINE", reason: "The active promoted candidate remains the top row in this read-only matrix." };
  } else if (active && bestNonBaseline && ["PASS", "WARN"].includes(bestNonBaseline.status) && bestNonBaseline.score > active.score + 10 && bestNonBaseline.trades >= 40) {
    recommendation = {
      action: "REVIEW_NEW_STRATEGY",
      reason: `${bestNonBaseline.strategy} ${bestNonBaseline.symbol} ${bestNonBaseline.timeframe} ranks meaningfully above the active baseline for research review only.`
    };
  } else if (passCount) {
    recommendation = { action: "KEEP_BASELINE", reason: "Other rows passed, but none beats the active baseline enough for review." };
  } else {
    recommendation = { action: "RESEARCH_MORE", reason: "No viable matrix row beat the baseline gates." };
  }
  return {
    activeBaselineRank: active ? active.rank : null,
    bestOverall,
    bestByStrategy,
    bestBySymbol,
    bestByTimeframe,
    bestNonBaseline,
    passCount,
    failCount,
    unsupportedCount,
    recommendation
  };
}

function sortRows(a, b) {
  const ap = a.status === "PASS" ? 2 : a.status === "WARN" ? 1 : 0;
  const bp = b.status === "PASS" ? 2 : b.status === "WARN" ? 1 : 0;
  return bp - ap || b.score - a.score || b.profitFactor - a.profitFactor || b.totalReturnPct - a.totalReturnPct;
}

function withOptionalEvidence(row, options) {
  return Object.assign({}, row);
}

function stressSummary(row, candles, regimeCandles, params, context) {
  const scenarios = [
    { name: "baseline", feePct: context.costs.feePct, slippagePct: context.costs.slippagePct },
    { name: "doubleSlippage", feePct: context.costs.feePct, slippagePct: context.costs.slippagePct * 2 },
    { name: "doubleFees", feePct: context.costs.feePct * 2, slippagePct: context.costs.slippagePct },
    { name: "highStress", feePct: context.costs.feePct * 2, slippagePct: context.costs.slippagePct * 3 }
  ];
  const rows = scenarios.map((scenario) => compactResult(backtest.runBacktestOnCandles({
    source: context.source,
    symbol: row.symbol,
    interval: row.timeframe,
    strategy: row.strategy,
    candles,
    regimeCandles,
    params: Object.assign({}, params, { feePct: scenario.feePct, slippagePct: scenario.slippagePct }),
    feePct: scenario.feePct,
    slippagePct: scenario.slippagePct
  }), {
    strategy: row.strategy,
    symbol: row.symbol,
    timeframe: row.timeframe,
    paramsSource: row.paramsSource,
    days: context.days,
    isActiveBaseline: row.isActiveBaseline
  }));
  const failed = rows.filter((item) => !["PASS", "WARN"].includes(item.status));
  return {
    status: failed.length ? "WATCH" : "RESILIENT",
    failedScenarios: failed.map((item, index) => scenarios[index] ? scenarios[index].name : item.status),
    baseline: rows[0] || null
  };
}

function walkForwardSummary(row, candles, regimeCandles, params, context) {
  const count = 4;
  const size = Math.max(1, Math.floor(candles.length / count));
  const folds = [];
  for (let index = 0; index < count; index += 1) {
    const start = index * size;
    const end = index === count - 1 ? candles.length : Math.min(candles.length, (index + 1) * size);
    const slice = candles.slice(start, end);
    if (!slice.length) continue;
    folds.push(Object.assign({ fold: index + 1 }, compactResult(backtest.runBacktestOnCandles({
      source: context.source,
      symbol: row.symbol,
      interval: row.timeframe,
      strategy: row.strategy,
      candles: slice,
      regimeCandles,
      params,
      feePct: context.costs.feePct,
      slippagePct: context.costs.slippagePct
    }), {
      strategy: row.strategy,
      symbol: row.symbol,
      timeframe: row.timeframe,
      paramsSource: row.paramsSource,
      days: context.days,
      isActiveBaseline: row.isActiveBaseline
    })));
  }
  const passFoldCount = folds.filter((fold) => ["PASS", "WARN"].includes(fold.status)).length;
  const failFoldCount = folds.length - passFoldCount;
  const negativeFoldCount = folds.filter((fold) => fold.totalReturnPct < 0).length;
  return {
    status: failFoldCount > passFoldCount || negativeFoldCount > Math.floor(folds.length / 2) ? "FRAGILE" : failFoldCount || negativeFoldCount ? "WATCH" : "STABLE",
    passFoldCount,
    failFoldCount,
    negativeFoldCount
  };
}

const days = periodDays(args.period);
const symbols = parseCsv(args.symbols, ["ETHUSDT", "BTCUSDT", "SOLUSDT"]);
const timeframes = parseCsv(args.timeframes, ["1h", "4h"]);
const strategies = parseCsv(args.strategies, AUTO_STRATEGIES);
const maxRows = Math.max(1, Math.min(Number(args.maxRows || args.max_rows || 100), 100));
const source = args.source || "bybit";
const activeParams = args.activeParams ? JSON.parse(args.activeParams) : {};
const active = {
  symbol: args.activeSymbol || "ETHUSDT",
  timeframe: args.activeTimeframe || "1h",
  strategy: args.activeStrategy || "SimpleAtrTrendV2"
};
const costs = {
  feePct: Number(args.feePct || args["fee-pct"] || 0.055),
  slippagePct: Number(args.slippagePct || args["slippage-pct"] || 0.02)
};
const from = args.from || argsUtil.daysToFrom(days);
const to = args.to || new Date().toISOString();
const explicitLimit = args.limit && args.limit !== "auto" ? Number(args.limit) : null;
const includeStress = String(args.includeStress || "false").toLowerCase() === "true";
const includeWalkForward = String(args.includeWalkForward || "false").toLowerCase() === "true";

const combos = [];
symbols.forEach((symbol) => {
  timeframes.forEach((timeframe) => {
    strategies.forEach((strategy) => combos.push({ symbol, timeframe, strategy }));
  });
});
const capped = combos.slice(0, maxRows);
const candleJobs = {};
capped.forEach((combo) => {
  const key = combo.symbol + ":" + combo.timeframe;
  if (!candleJobs[key]) {
    candleJobs[key] = data.fetchCandles({
      source,
      symbol: combo.symbol,
      interval: combo.timeframe,
      from,
      to,
      limit: explicitLimit || autoLimitFor(combo.timeframe, days, 1000)
    }).then((candles) => data.normalizeCandles(candles || []));
  }
});

Promise.all([
  Promise.all(Object.keys(candleJobs).map((key) => candleJobs[key].then((candles) => [key, candles]))),
  data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: explicitLimit || autoLimitFor("4h", days, 1000) }).then((candles) => data.normalizeCandles(candles || []))
]).then(([entries, regimeCandles]) => {
  const candlesByKey = {};
  entries.forEach(([key, candles]) => { candlesByKey[key] = candles; });
  const warnings = combos.length > capped.length ? [`Matrix capped ${combos.length} requested row(s) to ${capped.length}.`] : [];
  const rows = capped.map((combo) => {
    const info = strategyInfo(combo.strategy);
    const isActiveBaseline = combo.symbol === active.symbol && combo.timeframe === active.timeframe && combo.strategy === active.strategy;
    if (!info) return unsupportedRow(combo, "Strategy is not registered in core/strategies.");
    if (combo.strategy === "AlwaysLongTest") return unsupportedRow(combo, "AlwaysLongTest is test-only and skipped.");
    const paramsSource = isActiveBaseline ? "activeCandidate" : (Object.keys(info.params || {}).length ? "default" : "default");
    const params = Object.assign({}, isActiveBaseline ? activeParams : (info.params || {}), costs);
    try {
      const result = backtest.runBacktestOnCandles({
        source,
        symbol: combo.symbol,
        interval: combo.timeframe,
        strategy: combo.strategy,
        candles: candlesByKey[combo.symbol + ":" + combo.timeframe] || [],
        regimeCandles,
        params,
        feePct: costs.feePct,
        slippagePct: costs.slippagePct
      });
      const row = withOptionalEvidence(compactResult(result, {
        strategy: combo.strategy,
        symbol: combo.symbol,
        timeframe: combo.timeframe,
        paramsSource,
        days,
        isActiveBaseline
      }), { includeStress, includeWalkForward });
      row._params = params;
      row._candlesKey = combo.symbol + ":" + combo.timeframe;
      return row;
    } catch (error) {
      return Object.assign(unsupportedRow(combo, error.message), { status: "ERROR", mainFailureReason: "ERROR" });
    }
  }).sort(sortRows);
  rows.forEach((row, index) => { row.rank = index + 1; });
  const enriched = new Set(rows.slice(0, 3).map((row) => row.rank));
  const activeRow = rows.find((row) => row.isActiveBaseline);
  if (activeRow) enriched.add(activeRow.rank);
  rows.forEach((row) => {
    if (enriched.has(row.rank) && row._params && row._candlesKey) {
      if (includeStress) row.stress = stressSummary(row, candlesByKey[row._candlesKey] || [], regimeCandles, row._params, { source, days, costs });
      if (includeWalkForward) row.walkForward = walkForwardSummary(row, candlesByKey[row._candlesKey] || [], regimeCandles, row._params, { source, days, costs });
    }
    delete row._params;
    delete row._candlesKey;
  });
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbols,
      timeframes,
      strategies,
      period: args.period || "365d",
      mode: args.mode || "current_or_default_params",
      maxRows,
      includeStress,
      includeWalkForward
    },
    discoveredStrategies: strategiesRegistry.listStrategies().map((strategy) => strategy.name).filter((name) => name !== "AlwaysLongTest"),
    rows,
    summary: buildSummary(rows),
    warnings
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
