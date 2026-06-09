const argsUtil = require("./args");
const runtime = require("./runtime");
const search = require("../core/research/stabilityFirstChallengerSearch");

const args = argsUtil.parseArgs(process.argv.slice(2));

function boolArg(name, fallback) {
  const value = args[name];
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function parseJson(raw, fallback) {
  if (!raw) return fallback;
  return JSON.parse(raw);
}

async function main() {
  const payload = await search.buildReport({
    source: args.source || "bybit",
    symbols: args.symbols || "ETHUSDT,BTCUSDT",
    timeframes: args.timeframes || "1h,4h",
    strategies: args.strategies || "all",
    period: args.period || "365d",
    folds: Number(args.folds || 4),
    maxCombosPerStrategy: Number(args.maxCombosPerStrategy || args.max_combos_per_strategy || 50),
    topN: Number(args.topN || args.top_n || 20),
    limit: args.limit || "auto",
    includeStress: boolArg("includeStress", true),
    includeRecentWindows: boolArg("includeRecentWindows", true),
    includeReproAudit: boolArg("includeReproAudit", true),
    reproReruns: Number(args.reproReruns || args.repro_reruns || 2),
    save: boolArg("save", false),
    activeStrategy: args.activeStrategy || "SimpleAtrTrendV2",
    activeSymbol: args.activeSymbol || "ETHUSDT",
    activeTimeframe: args.activeTimeframe || "1h",
    activeParams: parseJson(args.activeParams, {}),
    makerFeePct: Number(args.makerFeePct || args["maker-fee-pct"] || 0),
    takerFeePct: Number(args.takerFeePct || args["taker-fee-pct"] || 0.055),
    slippageBps: Number(args.slippageBps || args["slippage-bps"] || 2),
    paperEnabled: boolArg("paperEnabled", false)
  });
  process.stdout.write(JSON.stringify(payload, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}

main().catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
