const backtest = require("../core/backtest");
const data = require("../core/data");
const tradeAudit = require("../core/backtest/tradeAudit");
const reporting = require("../core/reporting");
const argsUtil = require("./args");
const runtime = require("./runtime");

var cliArgs = argsUtil.parseArgs(process.argv.slice(2));

runtime.readStdinIfPresent().then(function (raw) {
  var args = cliArgs;
  var input = raw.trim() ? JSON.parse(raw) : {};
  var params = args.params ? JSON.parse(args.params) : input.params;
  var options = {
    source: args.source || input.source || "bybit",
    symbol: args.symbol || input.symbol || "BTCUSDT",
    interval: args.interval || input.interval || input.timeframe || "1h",
    from: args.from || input.from || (args.days ? argsUtil.daysToFrom(args.days) : input.from),
    to: args.to || input.to || new Date().toISOString(),
    strategy: args.strategy || input.strategy || input.preset || "ConservativeTrend",
    params: params || {},
    limit: Number(args.limit || input.limit || 5000),
    candles: input.candles,
    debug: args.debug === true || input.debug === true
  };
  return data.fetchCandles(options).then(function (candles) {
    return {
      candles: candles,
      result: backtest.runBacktestOnCandles(Object.assign({}, options, { candles: candles }))
    };
  });
}).then(function (result) {
  var payload = result.result;
  if (payload.diagnostics && payload.diagnostics.debug) {
    reporting.writeDebugReport(payload, "reports");
  }
  if (cliArgs["audit-trades"] === true) {
    payload.tradeAudit = tradeAudit.auditTrades(payload, result.candles);
  }
  process.stdout.write(JSON.stringify(payload, null, 2));
  runtime.finishCli({
    debugHandles: cliArgs["debug-handles"] === true,
    forceExit: cliArgs["force-exit"] === true,
    exitCode: 0
  });
}).catch(function (error) {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({
    debugHandles: cliArgs["debug-handles"] === true,
    forceExit: cliArgs["force-exit"] === true,
    exitCode: 1
  });
});
