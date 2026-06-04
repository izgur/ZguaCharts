const paper = require("../core/paper");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function boolArg(value, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  return !["0", "false", "no", "off"].includes(String(value).toLowerCase());
}

paper.blockerAnalytics({
  configPath: args.config || "config/local/paper-candidate.json",
  statePath: args.state || "data/paper-state.json",
  symbol: args.symbol,
  timeframe: args.timeframe || args.interval,
  strategy: args.strategy,
  period: args.period || "365d",
  limit: args.limit || "auto",
  includeRecentCandles: boolArg(args.includeRecentCandles || args["include-recent-candles"], true),
  recentLimit: Number(args.recentLimit || args["recent-limit"] || 50),
  refresh: boolArg(args.refresh, false)
}).then((payload) => {
  process.stdout.write(JSON.stringify(payload, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
