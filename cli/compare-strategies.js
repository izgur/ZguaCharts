const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");
const tradeAudit = require("../core/backtest/tradeAudit");
const argsUtil = require("./args");
const runtime = require("./runtime");

const STRATEGIES = [
  "RegimeFilteredTrendStrategy",
  "RegimeDonchian20",
  "RegimeDonchianCloseConfirm",
  "RegimePullbackTrend"
];

const args = argsUtil.parseArgs(process.argv.slice(2));
const options = {
  source: args.source || "bybit",
  symbol: args.symbol || "BTCUSDT",
  interval: args.interval || "1h",
  from: args.from || (args.days ? argsUtil.daysToFrom(args.days) : argsUtil.daysToFrom(365)),
  to: args.to || new Date().toISOString(),
  limit: Number(args.limit || 9000)
};

function ensureReports() {
  if (!fs.existsSync("reports")) fs.mkdirSync("reports");
}

function runOne(strategy, candles, regimeCandles, includeDiagnostics) {
  const result = backtest.runBacktestOnCandles({
    symbol: options.symbol,
    interval: options.interval,
    strategy,
    candles,
    regimeCandles,
    params: {}
  });
  const audit = tradeAudit.auditTrades(result, candles);
  const summary = {
    strategy,
    totalReturn: result.totalReturn,
    profitFactor: result.profitFactor,
    maxDrawdown: result.maxDrawdown,
    trades: result.trades,
    winRate: result.winRate,
    exposurePct: result.exposurePct,
    primaryBlocker: result.diagnostics ? result.diagnostics.primaryBlocker : null,
    auditStatus: audit.ok,
    auditErrors: audit.errors,
    breakoutSummary: result.diagnostics ? result.diagnostics.breakoutSummary : null
  };
  if (includeDiagnostics) summary.diagnostics = result.diagnostics;
  return summary;
}

Promise.resolve().then(function () {
  return data.fetchCandles(options);
}).then(function (candles) {
  return data.fetchCandles({
    source: options.source,
    symbol: "BTCUSDT",
    interval: "4h",
    from: options.from,
    to: options.to,
    limit: Math.ceil(options.limit / 4) + 250
  }).then(function (regimeCandles) {
    return { candles, regimeCandles };
  });
}).then(function (loaded) {
  ensureReports();
  const comparison = STRATEGIES.map(function (strategy, index) {
    return runOne(strategy, loaded.candles, loaded.regimeCandles, index === 0);
  });
  const base = comparison[0];
  const diagnostics = {
    symbol: options.symbol,
    interval: options.interval,
    candlesLoaded: loaded.candles.length,
    regimeCandlesLoaded: loaded.regimeCandles.length,
    breakoutUsesPreviousChannel: true,
    note: "Entry breakout compares close against the previous Donchian channel, so the current candle is not included in the threshold.",
    summary: base.breakoutSummary,
    candles: base.diagnostics ? base.diagnostics.breakoutCandles : [],
    comparison: comparison
  };
  delete base.diagnostics;
  const payload = {
    symbol: options.symbol,
    interval: options.interval,
    candlesLoaded: loaded.candles.length,
    regimeCandlesLoaded: loaded.regimeCandles.length,
    strategies: comparison
  };
  fs.writeFileSync(path.join("reports", "strategy-variant-comparison.json"), JSON.stringify(payload, null, 2));
  fs.writeFileSync(path.join("reports", "donchian-diagnostics.json"), JSON.stringify(diagnostics, null, 2));
  process.stdout.write(JSON.stringify(payload, null, 2));
  runtime.finishCli({
    debugHandles: args["debug-handles"] === true,
    forceExit: args["force-exit"] === true,
    exitCode: 0
  });
}).catch(function (error) {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({
    debugHandles: args["debug-handles"] === true,
    forceExit: args["force-exit"] === true,
    exitCode: 1
  });
});
