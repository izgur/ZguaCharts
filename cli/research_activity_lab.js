const backtest = require("../core/backtest");
const data = require("../core/data");
const optimizer = require("../core/optimizer");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function parseCsv(value, fallback) {
  if (!value) return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function classify(row) {
  if (row.status === "ERROR") return "ERROR";
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor <= 1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  if (row.expectancyPctPerTrade <= 0 && row.feesEstimatedPct > Math.abs(row.totalReturnPct)) return "FEES_TOO_HIGH";
  const codes = (row.rejectionReasons || []).map((item) => String(item.code || item).toLowerCase());
  if (codes.some((code) => code.includes("overfit") || code.includes("unstable"))) return "OVERFIT_RISK";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK") return row.profitFactor >= 1.1 && row.totalReturnPct > 0 ? "PASS" : "WARN";
  if (reason === "NO_TRADES") return "NO_TRADES";
  return "FAIL";
}

function compactMetrics(base, context) {
  const days = Math.max(1, Number(context.days || 365));
  const trades = Number(base.trades || 0);
  const feePct = Number(context.feePct || 0);
  const slippagePct = Number(context.slippagePct || 0);
  const row = {
    strategy: context.strategy,
    symbol: context.symbol,
    timeframe: context.timeframe,
    mode: context.mode,
    status: base.status || "FAIL",
    trades,
    tradesPerDay: round(trades / days, 4),
    tradesPerMonth: round(trades / days * 30, 2),
    avgBarsHeld: round(base.avgBarsHeld || 0, 4),
    totalReturnPct: round(base.totalReturn || base.totalReturnPct || 0, 4),
    profitFactor: round(base.profitFactor || 0, 4),
    winRate: round(base.winRate || 0, 4),
    maxDrawdownPct: round(base.maxDrawdown || base.maxDrawdownPct || 0, 4),
    expectancyPctPerTrade: trades ? round((base.totalReturn || base.totalReturnPct || 0) / trades, 4) : 0,
    feesEstimatedPct: round(trades * ((feePct + slippagePct) * 2), 4),
    score: round(base.score || 0, 5),
    qualityStatus: base.qualityStatus || base.status || "FAIL",
    mainFailureReason: null,
    warnings: base.warnings || [],
    rejectionReasons: base.rejectionReasons || [],
    params: base.params || {}
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  row.qualityStatus = row.qualityStatus === "PASS" || row.qualityStatus === "WARN" ? row.qualityStatus : row.status;
  return row;
}

function optimizerRow(row, context) {
  const full = row.full || {};
  const test = row.test || {};
  return compactMetrics({
    status: row.qualityStatus || (row.valid ? "PASS" : "FAIL"),
    qualityStatus: row.qualityStatus || (row.valid ? "PASS" : "FAIL"),
    params: row.params || {},
    trades: Number(full.trades || test.trades || row.trades || 0),
    profitFactor: Number(full.profitFactor || test.profitFactor || row.profitFactor || 0),
    totalReturn: Number(full.totalReturn || test.totalReturn || row.totalReturn || 0),
    maxDrawdown: Number(full.maxDrawdown || test.maxDrawdown || row.maxDrawdown || 0),
    winRate: Number(full.winRate || test.winRate || row.winRate || 0),
    avgBarsHeld: Number(full.avgBarsHeld || test.avgBarsHeld || row.avgBarsHeld || 0),
    score: Number(row.score || 0),
    warnings: (row.qualityWarnings || row.warnings || []).map((item) => item.label || item.code || String(item)),
    rejectionReasons: (row.rejectionReasons || []).map((item) => ({ code: item.code || String(item), label: item.label || item.code || String(item) }))
  }, context);
}

function backtestCurrent(symbol, timeframe, strategy, options) {
  const params = strategy === options.activeStrategy ? Object.assign({}, options.activeParams) : {};
  return data.fetchCandles({
    source: options.source,
    symbol,
    interval: timeframe,
    from: options.from,
    to: options.to,
    limit: options.limit
  }).then((candles) => {
    const result = backtest.runBacktestOnCandles({
      source: options.source,
      symbol,
      interval: timeframe,
      strategy,
      candles,
      params: Object.assign({}, params, { feePct: options.feePct, slippagePct: options.slippagePct }),
      feePct: options.feePct,
      slippagePct: options.slippagePct
    });
    return compactMetrics(result, {
      symbol,
      timeframe,
      strategy,
      mode: "current_params",
      days: options.days,
      feePct: options.feePct,
      slippagePct: options.slippagePct
    });
  }).catch((error) => compactMetrics({
    status: "ERROR",
    qualityStatus: "FAIL",
    warnings: [error.message],
    rejectionReasons: [{ code: "ERROR", label: error.message }],
    score: -999
  }, {
    symbol,
    timeframe,
    strategy,
    mode: "current_params",
    days: options.days,
    feePct: options.feePct,
    slippagePct: options.slippagePct
  }));
}

function optimizeOne(symbol, timeframe, strategy, options) {
  return optimizer.optimize({
    source: options.source,
    symbol,
    interval: timeframe,
    from: options.from,
    to: options.to,
    strategy,
    limit: options.limit,
    maxCombos: options.maxCombos,
    feePct: options.feePct,
    slippagePct: options.slippagePct,
    trainRatio: 0.7
  }).then((result) => {
    const selected = result.optimizedPerformance ? [result.optimizedPerformance] : [];
    const rejected = result.rejectedCandidates || [];
    const row = (selected.concat(rejected)[0]);
    if (!row) {
      return compactMetrics({
        status: "NO_TRADES",
        qualityStatus: "FAIL",
        warnings: result.warnings || ["No optimizer candidate rows returned."],
        rejectionReasons: [{ code: "NO_TRADES", label: "No optimizer candidate rows returned." }]
      }, { symbol, timeframe, strategy, mode: "optimized", days: options.days, feePct: options.feePct, slippagePct: options.slippagePct });
    }
    const compact = optimizerRow(row, { symbol, timeframe, strategy, mode: "optimized", days: options.days, feePct: options.feePct, slippagePct: options.slippagePct });
    compact.optimizerWarnings = result.warnings || [];
    return compact;
  }).catch((error) => compactMetrics({
    status: "ERROR",
    qualityStatus: "FAIL",
    warnings: [error.message],
    rejectionReasons: [{ code: "ERROR", label: error.message }],
    score: -999
  }, { symbol, timeframe, strategy, mode: "optimized", days: options.days, feePct: options.feePct, slippagePct: options.slippagePct }));
}

const symbols = parseCsv(args.symbols, ["ETHUSDT", "BTCUSDT"]);
const timeframes = parseCsv(args.timeframes, ["15m", "1h"]);
const strategies = parseCsv(args.strategies, ["SimpleAtrTrendV2"]);
const optimize = String(args.optimize || "false").toLowerCase() === "true";
const days = periodDays(args.period);
const options = {
  source: args.source || "bybit",
  days,
  period: args.period || "365d",
  from: args.from || argsUtil.daysToFrom(days),
  to: args.to || new Date().toISOString(),
  limit: args.limit && args.limit !== "auto" ? Number(args.limit) : 5000,
  maxCombos: Number(args.maxCombos || args.max_combos || args["max-combos"] || (optimize ? 50 : 24)),
  feePct: Number(args.feePct || args["fee-pct"] || 0.055),
  slippagePct: Number(args.slippagePct || args["slippage-pct"] || 0.02),
  activeStrategy: args.activeStrategy || "SimpleAtrTrendV2",
  activeParams: args.activeParams ? JSON.parse(args.activeParams) : {}
};

const combos = [];
symbols.forEach((symbol) => {
  timeframes.forEach((timeframe) => {
    strategies.forEach((strategy) => {
      combos.push({ symbol, timeframe, strategy });
    });
  });
});

const capped = combos.slice(0, Math.max(1, options.maxCombos));
const warnings = combos.length > capped.length ? [`Activity lab capped ${combos.length} requested combo(s) to ${capped.length}.`] : [];

capped.reduce((promise, combo) => {
  return promise.then((rows) => {
    const job = optimize
      ? optimizeOne(combo.symbol, combo.timeframe, combo.strategy, options)
      : backtestCurrent(combo.symbol, combo.timeframe, combo.strategy, options);
    return job.then((row) => rows.concat([row]));
  });
}, Promise.resolve([])).then((rows) => {
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbols,
      timeframes,
      strategies,
      period: options.period,
      optimize,
      maxCombos: options.maxCombos,
      limit: args.limit || "auto",
      feePct: options.feePct,
      slippagePct: options.slippagePct
    },
    rows,
    warnings
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
