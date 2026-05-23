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
    if (String(options.strategy).indexOf("Regime") === 0 && !input.regimeCandles) {
      return data.fetchCandles({
        source: options.source,
        symbol: "BTCUSDT",
        interval: "4h",
        from: options.from,
        to: options.to,
        limit: Math.ceil(options.limit / 4) + 250
      }).then(function (regimeCandles) {
        return { candles: candles, regimeCandles: regimeCandles };
      });
    }
    return { candles: candles, regimeCandles: input.regimeCandles };
  }).then(function (loaded) {
    return {
      candles: loaded.candles,
      result: backtest.runBacktestOnCandles(Object.assign({}, options, {
        candles: loaded.candles,
        regimeCandles: loaded.regimeCandles
      }))
    };
  });
}).then(function (result) {
  var payload = result.result;
  if (payload.diagnostics && payload.diagnostics.debug) {
    reporting.writeDebugReport(payload, "reports");
  }
  if (String(payload.strategy).indexOf("Regime") === 0) {
    reporting.writeRegimeDebugReport(payload, "reports");
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
