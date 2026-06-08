const fs = require("fs");
const path = require("path");
const argsUtil = require("./args");
const runtime = require("./runtime");
const lab = require("../core/research/regimeFilterCounterfactual");

const args = argsUtil.parseArgs(process.argv.slice(2));

function boolArg(name, fallback) {
  const value = args[name];
  if (value === undefined) return fallback;
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
}

function parseParams(raw) {
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (error) {
    throw new Error("params/baseParams must be valid JSON: " + error.message);
  }
}

function saveReport(payload) {
  const dir = path.join(__dirname, "..", "reports", "research-regime-filters");
  fs.mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const file = path.join(dir, `regime-filter-counterfactual-${stamp}.json`);
  fs.writeFileSync(file, JSON.stringify(payload, null, 2));
  return path.relative(path.join(__dirname, ".."), file);
}

async function main() {
  const baseParams = parseParams(args.params || args.baseParams);
  const payload = await lab.buildReport({
    source: args.source || "bybit",
    symbol: args.symbol || "ETHUSDT",
    timeframe: args.timeframe || args.interval || "1h",
    strategy: args.strategy || "SimpleAtrTrendV2",
    period: args.period || "365d",
    folds: Number(args.folds || 4),
    limit: args.limit || "auto",
    params: baseParams,
    makerFeePct: Number(args.makerFeePct || args["maker-fee-pct"] || 0),
    takerFeePct: Number(args.takerFeePct || args["taker-fee-pct"] || 0),
    slippageBps: Number(args.slippageBps || args["slippage-bps"] || 0),
    includeStress: boolArg("includeStress", false),
    includeRecentWindows: boolArg("includeRecentWindows", false)
  });
  if (boolArg("save", false)) {
    payload.savedPath = saveReport(payload);
  }
  process.stdout.write(JSON.stringify(payload, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}

main().catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
