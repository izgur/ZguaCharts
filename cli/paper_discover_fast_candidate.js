const optimizer = require("../core/optimizer");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function parseCsv(value, fallback) {
  if (!value) return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function compactRow(row, symbol, timeframe, strategy) {
  const test = row.test || {};
  const full = row.full || {};
  return {
    symbol,
    timeframe,
    strategy,
    status: row.qualityStatus || (row.valid ? "PASS" : "FAIL"),
    qualityStatus: row.qualityStatus || (row.valid ? "PASS" : "FAIL"),
    params: row.params || {},
    train: row.train || {},
    test,
    full,
    trades: Number(full.trades || test.trades || row.trades || 0),
    profitFactor: Number(full.profitFactor || test.profitFactor || row.profitFactor || 0),
    totalReturnPct: Number(full.totalReturn || test.totalReturn || row.totalReturn || 0),
    maxDrawdownPct: Number(full.maxDrawdown || test.maxDrawdown || row.maxDrawdown || 0),
    winRate: Number(full.winRate || test.winRate || row.winRate || 0),
    score: Number(row.score || 0),
    warnings: (row.qualityWarnings || []).map((item) => item.label || item.code || String(item)),
    rejectionReasons: (row.rejectionReasons || []).map((item) => ({
      code: item.code || String(item),
      label: item.label || item.code || String(item)
    }))
  };
}

function discoverOne(symbol, timeframe, options) {
  return optimizer.optimize({
    source: options.source,
    symbol,
    interval: timeframe,
    from: options.from,
    to: options.to,
    strategy: options.strategy,
    limit: options.limit,
    maxCombos: options.maxCombos,
    feePct: options.feePct,
    slippagePct: options.slippagePct,
    trainRatio: 0.7
  }).then((result) => {
    const selected = result.optimizedPerformance ? [result.optimizedPerformance] : [];
    const rejected = result.rejectedCandidates || [];
    const rows = selected.concat(rejected).slice(0, options.rowsPerMarket).map((row) => compactRow(row, symbol, timeframe, options.strategy));
    return {
      symbol,
      timeframe,
      ok: true,
      combinations: result.combinations,
      validCandidates: result.validCandidates,
      qualitySummary: result.qualitySummary,
      warnings: result.warnings || [],
      rows
    };
  }).catch((error) => ({
    symbol,
    timeframe,
    ok: false,
    error: error.stack || error.message,
    rows: [{
      symbol,
      timeframe,
      strategy: options.strategy,
      status: "ERROR",
      qualityStatus: "FAIL",
      params: {},
      train: {},
      test: {},
      full: {},
      trades: 0,
      profitFactor: 0,
      totalReturnPct: 0,
      maxDrawdownPct: 0,
      winRate: 0,
      score: -999,
      warnings: [error.message],
      rejectionReasons: [{ code: "optimizer_error", label: error.message }]
    }]
  }));
}

const symbols = parseCsv(args.symbols, ["ETHUSDT", "BTCUSDT"]);
const timeframes = parseCsv(args.timeframes, ["15m"]);
const maxCombos = Number(args["max-combos"] || args.max_combos || args.maxCombos || 100);
const options = {
  source: args.source || "bybit",
  symbols,
  timeframes,
  strategy: args.strategy || "SimpleAtrTrendV2",
  period: args.period || "365d",
  from: args.from || argsUtil.daysToFrom(Number(String(args.period || "365d").replace(/d$/i, "")) || 365),
  to: args.to || new Date().toISOString(),
  limit: args.limit && args.limit !== "auto" ? Number(args.limit) : 5000,
  maxCombos,
  feePct: Number(args["fee-pct"] || args.feePct || 0),
  slippagePct: Number(args["slippage-pct"] || args.slippagePct || 0),
  rowsPerMarket: Number(args.rows || args.rowsPerMarket || 3)
};

const jobs = [];
symbols.forEach((symbol) => {
  timeframes.forEach((timeframe) => {
    jobs.push(() => discoverOne(symbol, timeframe, options));
  });
});

jobs.reduce((promise, job) => promise.then((items) => job().then((item) => items.concat([item]))), Promise.resolve([]))
  .then((markets) => {
    const rows = markets.reduce((all, item) => all.concat(item.rows || []), []);
    process.stdout.write(JSON.stringify({
      ok: markets.every((item) => item.ok),
      search: {
        symbols,
        timeframes,
        strategy: options.strategy,
        period: options.period,
        maxCombos,
        limit: args.limit || "auto"
      },
      markets,
      rows,
      warnings: markets.reduce((all, item) => all.concat(item.warnings || []), [])
    }, null, 2));
    runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
  }).catch((error) => {
    process.stderr.write(error.stack || error.message);
    runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
  });
