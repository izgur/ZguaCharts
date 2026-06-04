const backtest = require("../core/backtest");
const data = require("../core/data");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function scenarioList(base) {
  return [
    { scenario: "baseline", makerFeePct: base.makerFeePct, takerFeePct: base.takerFeePct, slippageBps: base.slippageBps },
    { scenario: "noSlippage", makerFeePct: base.makerFeePct, takerFeePct: base.takerFeePct, slippageBps: 0 },
    { scenario: "doubleSlippage", makerFeePct: base.makerFeePct, takerFeePct: base.takerFeePct, slippageBps: base.slippageBps * 2 },
    { scenario: "tripleSlippage", makerFeePct: base.makerFeePct, takerFeePct: base.takerFeePct, slippageBps: base.slippageBps * 3 },
    { scenario: "doubleFees", makerFeePct: base.makerFeePct * 2, takerFeePct: base.takerFeePct * 2, slippageBps: base.slippageBps },
    { scenario: "doubleFeesDoubleSlippage", makerFeePct: base.makerFeePct * 2, takerFeePct: base.takerFeePct * 2, slippageBps: base.slippageBps * 2 },
    { scenario: "highStress", makerFeePct: base.makerFeePct * 2, takerFeePct: base.takerFeePct * 2, slippageBps: base.slippageBps * 3 },
    { scenario: "zeroFees", makerFeePct: 0, takerFeePct: 0, slippageBps: base.slippageBps }
  ];
}

function classify(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct <= 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK") return "PASS";
  if (row.trades >= 20 && row.totalReturnPct > 0 && row.profitFactor >= 1 && row.maxDrawdownPct <= 25) return "WARN";
  return "FAIL";
}

function compactResult(result, spec, baseline, context) {
  const trades = Number(result.trades || 0);
  const row = {
    scenario: spec.scenario,
    makerFeePct: round(spec.makerFeePct, 6),
    takerFeePct: round(spec.takerFeePct, 6),
    slippageBps: round(spec.slippageBps, 6),
    status: "FAIL",
    trades,
    tradesPerMonth: round(trades / Math.max(1, context.days) * 30, 2),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades, 4) : 0,
    degradationVsBaseline: {
      returnDiffPct: baseline ? round((result.totalReturn || 0) - baseline.totalReturnPct, 4) : 0,
      profitFactorDiff: baseline ? round((result.profitFactor || 0) - baseline.profitFactor, 4) : 0,
      drawdownDiffPct: baseline ? round((result.maxDrawdown || 0) - baseline.maxDrawdownPct, 4) : 0
    },
    mainFailureReason: null,
    warnings: result.warnings || []
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  return row;
}

function passing(row) {
  return row && (row.status === "PASS" || row.status === "WARN");
}

function stressSummary(rows) {
  const baseline = rows.find((row) => row.scenario === "baseline");
  const doubleSlippage = rows.find((row) => row.scenario === "doubleSlippage");
  const doubleFees = rows.find((row) => row.scenario === "doubleFees");
  const failed = rows.filter((row) => !passing(row));
  const surviving = rows.filter(passing);
  let status = "WATCH";
  if (!passing(baseline)) status = "FAIL";
  else if (baseline.profitFactor < 1.15 || baseline.totalReturnPct < 1) status = "FRAGILE";
  else if (passing(doubleSlippage) && passing(doubleFees)) status = failed.length ? "WATCH" : "RESILIENT";
  else status = "FRAGILE";
  const worstPassingScenario = surviving.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct || a.profitFactor - b.profitFactor)[0] || null;
  const firstFailureScenario = failed[0] || null;
  const recommendation = {
    action: status === "RESILIENT" ? "KEEP_CURRENT_COST_MODEL" : status === "WATCH" ? "WATCH_EXECUTION_COSTS" : status === "FRAGILE" ? "REVIEW_COST_SENSITIVITY" : "RESEARCH_ALTERNATIVES",
    reason: status === "RESILIENT"
      ? "The active candidate survives the configured fee/slippage stress scenarios in this read-only lab."
      : status === "WATCH"
        ? "The baseline passes, but some higher-cost scenarios fail. Continue paper-only observation and monitor execution assumptions."
        : status === "FRAGILE"
          ? "The candidate is sensitive to modest cost stress. Review before trusting the edge."
          : "The baseline cost scenario failed, so this candidate should not be trusted without more research."
  };
  return {
    status,
    survivingScenarios: surviving.map((row) => row.scenario),
    failedScenarios: failed.map((row) => row.scenario),
    worstPassingScenario,
    firstFailureScenario,
    recommendation
  };
}

const symbol = args.symbol || "ETHUSDT";
const timeframe = args.timeframe || args.interval || "1h";
const strategy = args.strategy || "SimpleAtrTrendV2";
const days = periodDays(args.period);
const source = args.source || "bybit";
const baseParams = args.baseParams ? JSON.parse(args.baseParams) : {};
const baseCost = {
  makerFeePct: Number(args.makerFeePct || args["maker-fee-pct"] || 0),
  takerFeePct: Number(args.takerFeePct || args["taker-fee-pct"] || 0),
  slippageBps: Number(args.slippageBps || args["slippage-bps"] || 0)
};

Promise.all([
  data.fetchCandles({
    source,
    symbol,
    interval: timeframe,
    from: args.from || argsUtil.daysToFrom(days),
    to: args.to || new Date().toISOString(),
    limit: args.limit && args.limit !== "auto" ? Number(args.limit) : 5000
  }),
  data.fetchCandles({
    source,
    symbol: "BTCUSDT",
    interval: "4h",
    from: args.from || argsUtil.daysToFrom(days),
    to: args.to || new Date().toISOString(),
    limit: 3000
  })
]).then(([candles, regimeCandles]) => {
  const rows = [];
  let baseline = null;
  scenarioList(baseCost).forEach((spec) => {
    const params = Object.assign({}, baseParams, {
      makerFeePct: spec.makerFeePct,
      takerFeePct: spec.takerFeePct,
      slippageBps: spec.slippageBps
    });
    const result = backtest.runBacktestOnCandles({
      source,
      symbol,
      interval: timeframe,
      strategy,
      candles,
      regimeCandles,
      params
    });
    const row = compactResult(result, spec, baseline, { days });
    if (spec.scenario === "baseline") baseline = row;
    rows.push(row);
  });
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbol,
      timeframe,
      strategy,
      period: args.period || "365d",
      scenarios: args.scenarios || "default"
    },
    baseCostModel: baseCost,
    rows,
    stress: stressSummary(rows),
    warnings: []
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
