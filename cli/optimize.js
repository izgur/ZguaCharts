const optimizer = require("../core/optimizer");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));
const outputDir = args.output || "reports";
const options = {
  source: args.source || "bybit",
  symbol: args.symbol || "BTCUSDT",
  interval: args.interval || "1h",
  from: args.from || argsUtil.daysToFrom(args.days || 365),
  to: args.to || new Date().toISOString(),
  strategy: args.strategy || "ConservativeTrend",
  limit: Number(args.limit || 5000),
  trainRatio: Number(args.trainRatio || 0.7),
  ranges: optimizer.parseRanges(args.ranges),
  outputDir: outputDir,
  reportPrefix: args["report-prefix"] || args.reportPrefix || "",
  maxCombos: Number(args["max-combos"] || args.maxCombos || 1000),
  progressEvery: Number(args["progress-every"] || args.progressEvery || 50),
  feePct: Number(args["fee-pct"] || args.feePct || 0),
  slippagePct: Number(args["slippage-pct"] || args.slippagePct || 0)
};

var run = args.staged === true || args.mode === "staged" ? optimizer.optimizeStaged : optimizer.optimize;

run(options).then(function (result) {
  process.stdout.write(JSON.stringify(result, null, 2));
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
