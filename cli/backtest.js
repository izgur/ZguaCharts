const backtest = require("../core/backtest");
const data = require("../core/data");
const tradeAudit = require("../core/backtest/tradeAudit");
const reporting = require("../core/reporting");
const argsUtil = require("./args");
const runtime = require("./runtime");

var cliArgs = argsUtil.parseArgs(process.argv.slice(2));

function usesRegimeRunner(strategy) {
  return [
    "RegimeFilteredTrendStrategy",
    "RegimeDonchian20",
    "RegimeDonchianCloseConfirm",
    "RegimePullbackTrend",
    "EmaPullbackContinuation",
    "TrendBreakoutRetest",
    "VolatilitySqueezeBreakout",
    "MeanReversionInBullRegime",
    "MomentumContinuation",
    "PullbackReclaimV2",
    "EmaBounceV2",
    "BreakoutRetestV2",
    "RangeExpansionV2",
    "RelativeStrengthV2",
    "RelativeStrengthFreshV3",
    "SimpleAtrTrendV2",
    "FibPullbackContinuationV1",
    "RobustTrendFollowerV1",
    "BtcMeanReversion4hV1",
    "SqueezePullbackHold4hV1",
    "BtcRegimeController4hV1"
  ].includes(strategy);
}

function needsDerivativeEnrichment(options) {
  return options.source === "bybit" && options.strategy === "RobustTrendFollowerV1";
}

runtime.readStdinIfPresent({ waitForEnd: cliArgs["stdin-json"] === true }).then(function (raw) {
  var args = cliArgs;
  var input = raw.trim() ? JSON.parse(raw) : {};
  var params = args.params ? JSON.parse(args.params) : input.params;
  var options = {
    source: args.source || input.source || "bybit",
    symbol: args.symbol || input.symbol || "BTCUSDT",
    interval: args.interval || input.interval || input.timeframe || "1h",
    executionInterval: args.executionInterval || args["execution-interval"] || input.executionInterval || input.executionTimeframe || input.execution_timeframe,
    from: args.from || input.from || (args.days ? argsUtil.daysToFrom(args.days) : input.from),
    to: args.to || input.to || new Date().toISOString(),
    strategy: args.strategy || input.strategy || input.preset || "ConservativeTrend",
    params: params || {},
    limit: Number(args.limit || input.limit || 5000),
    candles: input.candles,
    divergenceEvents: input.divergenceEvents,
    asOf: input.asOf,
    debug: args.debug === true || input.debug === true
  };
  return data.fetchCandles(options).then(function (candles) {
    var enriched = needsDerivativeEnrichment(options)
      ? data.enrichCandlesWithDerivatives(options, candles)
      : Promise.resolve(candles);
    return enriched.then(function (loadedCandles) {
      return loadedCandles;
    });
  }).then(function (candles) {
    if (usesRegimeRunner(options.strategy) && !input.regimeCandles) {
      return data.fetchCandles({
        source: options.source,
        symbol: "BTCUSDT",
        interval: "4h",
        from: options.from,
        to: options.to,
        limit: Math.min(50000, Math.ceil(options.limit * (intervalMinutes(options.interval) || 60) / 240) + 250)
      }).then(function (regimeCandles) {
        return { candles: candles, regimeCandles: regimeCandles };
      });
    }
    return { candles: candles, regimeCandles: input.regimeCandles };
  }).then(function (loaded) {
    if (input.executionCandles || !options.executionInterval || options.executionInterval === options.interval) {
      return Object.assign({}, loaded, { executionCandles: input.executionCandles });
    }
    var signalMinutes = intervalMinutes(options.interval);
    var executionMinutes = intervalMinutes(options.executionInterval);
    var limitMultiplier = signalMinutes && executionMinutes ? Math.max(1, Math.ceil(signalMinutes / executionMinutes)) : 1;
    return data.fetchCandles({
      source: options.source,
      symbol: options.symbol,
      interval: options.executionInterval,
      from: options.from,
      to: options.to,
      limit: Math.min(50000, Math.max(options.limit, options.limit * limitMultiplier))
    }).then(function (executionCandles) {
      return Object.assign({}, loaded, { executionCandles: executionCandles });
    });
  }).then(function (loaded) {
    return {
      candles: loaded.candles,
      result: backtest.runBacktestOnCandles(Object.assign({}, options, {
        candles: loaded.candles,
        regimeCandles: loaded.regimeCandles,
        executionCandles: loaded.executionCandles
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

function intervalMinutes(interval) {
  var text = String(interval || "").trim().toLowerCase();
  var value = Number(text.slice(0, -1));
  if (!Number.isFinite(value)) return null;
  if (text.endsWith("m")) return value;
  if (text.endsWith("h")) return value * 60;
  if (text.endsWith("d")) return value * 1440;
  return null;
}
