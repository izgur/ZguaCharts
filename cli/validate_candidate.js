const fs = require("fs");
const path = require("path");

const argsUtil = require("./args");
const runtime = require("./runtime");
const data = require("../core/data");
const backtest = require("../core/backtest");
const tradeAudit = require("../core/backtest/tradeAudit");

const args = argsUtil.parseArgs(process.argv.slice(2));
const options = {
  source: args.source || "bybit",
  strategy: args.strategy || "SimpleAtrTrendV2",
  regimeMode: args["regime-mode"] || "looseBtcBull",
  days: Number(args.days || 365),
  symbols: list(args.symbols || "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"),
  intervals: list(args.intervals || "15m,1h,4h"),
  limit: Number(args.limit || 20000),
  outputDir: args.output || "reports",
  from: args.from || argsUtil.daysToFrom(args.days || 365),
  to: args.to || new Date().toISOString(),
  fillModel: args["fill-model"] || "next-open",
  makerFeePct: Number(args["maker-fee-pct"] || 0.02),
  takerFeePct: Number(args["taker-fee-pct"] || 0.055),
  slippageBps: Number(args["slippage-bps"] || 2)
};

const candidateParams = {
  useRsiFilter: true,
  atrMultiplier: 3,
  emaFast: 20,
  emaSlow: 110,
  emaTrend: 100,
  rsiMin: 43,
  rsiMax: 70,
  cooldownBars: 3,
  minHoldBars: 3
};

function list(value) {
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir);
}

function limitForInterval(interval) {
  if (interval === "15m") return Math.min(options.limit, 20000);
  if (interval === "1h") return Math.min(options.limit, 9000);
  if (interval === "4h") return Math.min(options.limit, 3000);
  return options.limit;
}

function paramsFor(costs) {
  return Object.assign({}, candidateParams, {
    regimeMode: options.regimeMode,
    fillModel: options.fillModel,
    makerFeePct: costs.makerFeePct,
    takerFeePct: costs.takerFeePct,
    slippageBps: costs.slippageBps
  });
}

function realisticCosts() {
  return {
    makerFeePct: options.makerFeePct,
    takerFeePct: options.takerFeePct,
    slippageBps: options.slippageBps
  };
}

function zeroCosts() {
  return { makerFeePct: 0, takerFeePct: 0, slippageBps: 0 };
}

function stressCosts() {
  return {
    makerFeePct: options.makerFeePct * 2,
    takerFeePct: options.takerFeePct * 2,
    slippageBps: options.slippageBps * 2
  };
}

function loadData() {
  return data.fetchCandles({
    source: options.source,
    symbol: "BTCUSDT",
    interval: "4h",
    from: options.from,
    to: options.to,
    limit: 3000
  }).then((regimeCandles) => {
    const matrix = {};
    const jobs = [];
    options.symbols.forEach((symbol) => options.intervals.forEach((interval) => {
      jobs.push(() => data.fetchCandles({
        source: options.source,
        symbol,
        interval,
        from: options.from,
        to: options.to,
        limit: limitForInterval(interval)
      }).then((candles) => {
        matrix[symbol + ":" + interval] = {
          symbol,
          interval,
          candles: data.normalizeCandles(candles)
        };
      }));
    }));
    return jobs.reduce((p, job) => p.then(job), Promise.resolve()).then(() => ({ matrix, regimeCandles }));
  });
}

function runOne(ds, regimeCandles, costs) {
  const result = backtest.runBacktestOnCandles({
    symbol: ds.symbol,
    interval: ds.interval,
    strategy: options.strategy,
    candles: ds.candles,
    regimeCandles,
    params: paramsFor(costs)
  });
  const audit = tradeAudit.auditTrades(result, ds.candles);
  const row = metrics(result, ds, audit);
  row.viable = viable(row);
  row.rejectedReasons = rejectionReasons(row);
  return { result, audit, row };
}

function validateMatrix(loaded, costs) {
  const rows = [];
  const audits = [];
  Object.keys(loaded.matrix).forEach((key) => {
    const outcome = runOne(loaded.matrix[key], loaded.regimeCandles, costs);
    rows.push(outcome.row);
    audits.push({
      symbol: outcome.row.symbol,
      interval: outcome.row.interval,
      ok: outcome.audit.ok,
      errors: outcome.audit.errors,
      warnings: outcome.audit.warnings,
      tradesChecked: outcome.audit.tradesChecked
    });
  });
  return {
    costs,
    rows,
    viableRows: rows.filter((r) => r.viable).length,
    viableSymbols: unique(rows.filter((r) => r.viable).map((r) => r.symbol)),
    viableIntervals: unique(rows.filter((r) => r.viable).map((r) => r.interval)),
    auditOk: audits.every((a) => a.ok),
    audits,
    rejectedRows: rows.filter((r) => !r.viable).map((r) => ({
      symbol: r.symbol,
      interval: r.interval,
      reasons: r.rejectedReasons
    }))
  };
}

function metrics(result, ds, audit) {
  return {
    symbol: ds.symbol,
    interval: ds.interval,
    candles: ds.candles.length,
    trades: result.trades,
    grossReturn: result.grossReturn,
    netReturn: result.netReturn,
    totalReturn: result.totalReturn,
    profitFactor: result.profitFactor,
    maxDrawdown: result.maxDrawdown,
    winRate: result.winRate,
    avgTrade: result.averageTrade,
    avgBarsHeld: result.avgBarsHeld,
    exposurePct: result.exposurePct,
    totalFees: result.totalFees,
    totalSlippageCost: result.totalSlippageCost,
    roundTripAverageCostPct: result.roundTripAverageCostPct,
    auditOk: audit.ok,
    auditErrors: audit.errors,
    primaryBlocker: result.diagnostics ? result.diagnostics.primaryBlocker : null
  };
}

function viable(row) {
  const minTrades = row.interval === "4h" ? 20 : 50;
  return row.trades >= minTrades &&
    row.totalReturn > 0 &&
    row.profitFactor > 1.12 &&
    row.maxDrawdown < 15 &&
    row.auditOk === true;
}

function rejectionReasons(row) {
  const reasons = [];
  const minTrades = row.interval === "4h" ? 20 : 50;
  if (row.trades < minTrades) reasons.push("too few trades");
  if (row.totalReturn <= 0) reasons.push("net return <= 0");
  if (row.profitFactor <= 1.12) reasons.push("profit factor <= 1.12");
  if (row.maxDrawdown >= 15) reasons.push("max drawdown >= 15");
  if (!row.auditOk) reasons.push("trade audit failed");
  return reasons;
}

function splitTests(loaded, costs) {
  const out = {};
  ["BTCUSDT", "ETHUSDT"].forEach((symbol) => {
    const ds = loaded.matrix[symbol + ":1h"];
    if (!ds) return;
    const cut = Math.floor(ds.candles.length * 0.7);
    const train = backtest.runBacktestOnCandles({
      symbol,
      interval: "1h",
      strategy: options.strategy,
      candles: ds.candles.slice(0, cut),
      regimeCandles: loaded.regimeCandles,
      params: paramsFor(costs)
    });
    const test = backtest.runBacktestOnCandles({
      symbol,
      interval: "1h",
      strategy: options.strategy,
      candles: ds.candles.slice(cut),
      regimeCandles: loaded.regimeCandles,
      params: paramsFor(costs)
    });
    out[symbol] = { train: compact(train), test: compact(test) };
  });
  return out;
}

function walkForward(loaded, costs) {
  const out = {};
  ["BTCUSDT", "ETHUSDT", "BNBUSDT"].forEach((symbol) => {
    const ds = loaded.matrix[symbol + ":1h"];
    if (!ds) return;
    const foldSize = Math.floor(ds.candles.length / 5);
    const folds = [];
    for (let fold = 1; fold <= 4; fold += 1) {
      const trainCandles = ds.candles.slice(0, foldSize * fold);
      const testCandles = ds.candles.slice(foldSize * fold, foldSize * (fold + 1));
      const train = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: options.strategy, candles: trainCandles, regimeCandles: loaded.regimeCandles, params: paramsFor(costs) });
      const test = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: options.strategy, candles: testCandles, regimeCandles: loaded.regimeCandles, params: paramsFor(costs) });
      folds.push({ fold, trainReturn: train.totalReturn, testReturn: test.totalReturn, trainPF: train.profitFactor, testPF: test.profitFactor, trainTrades: train.trades, testTrades: test.trades });
    }
    const positive = folds.filter((f) => f.testReturn > 0).length;
    const lowTrades = folds.filter((f) => f.testTrades < 5).length;
    const collapse = folds.filter((f) => f.trainPF > 1.5 && f.testPF < 1).length;
    out[symbol] = { accepted: positive >= 2 && lowTrades <= 1 && collapse <= 1, positiveTestFolds: positive, lowTradeFolds: lowTrades, pfCollapseFolds: collapse, folds };
  });
  out.acceptedSymbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"].filter((s) => out[s] && out[s].accepted).length;
  return out;
}

function compact(result) {
  return {
    totalReturn: result.totalReturn,
    grossReturn: result.grossReturn,
    netReturn: result.netReturn,
    profitFactor: result.profitFactor,
    maxDrawdown: result.maxDrawdown,
    trades: result.trades
  };
}

function costBreakdown(beforeCost, realistic, stress) {
  return {
    beforeCosts: summarizeCosts(beforeCost.rows),
    realisticCosts: summarizeCosts(realistic.rows),
    stressCosts: summarizeCosts(stress.rows),
    settings: {
      fillModel: options.fillModel,
      realistic: realisticCosts(),
      stress: stressCosts()
    }
  };
}

function summarizeCosts(rows) {
  return {
    grossReturnMedian: round(median(rows.map((r) => r.grossReturn))),
    netReturnMedian: round(median(rows.map((r) => r.netReturn))),
    totalFees: round(rows.reduce((sum, r) => sum + r.totalFees, 0)),
    totalSlippageCost: round(rows.reduce((sum, r) => sum + r.totalSlippageCost, 0)),
    roundTripAverageCostPct: round(median(rows.map((r) => r.roundTripAverageCostPct)), 6)
  };
}

function readiness(beforeCost, realistic, stress, realisticWf, stressWf, split) {
  const reasons = [];
  if (!realistic.auditOk) reasons.push("full trade audit failed");
  if (realistic.viableRows < 5) reasons.push("fewer than 5 viable rows under realistic costs");
  if (realistic.viableSymbols.length < 2) reasons.push("fewer than 2 viable symbols under realistic costs");
  if (realistic.viableIntervals.length < 2) reasons.push("fewer than 2 viable intervals under realistic costs");
  if (split.BTCUSDT && split.ETHUSDT && split.BTCUSDT.test.totalReturn < 0 && split.ETHUSDT.test.totalReturn < 0) reasons.push("BTCUSDT and ETHUSDT 70/30 tests both negative");
  if (realisticWf.acceptedSymbols < 2) reasons.push("realistic walk-forward accepted fewer than 2 of 3 symbols");
  if (stress.viableRows < 2) reasons.push("stress test has fewer than 2 viable rows");
  if (realistic.rows.some((r) => r.maxDrawdown >= 15)) reasons.push("max drawdown >= 15");
  return {
    paperSimulationReady: reasons.length === 0,
    reasons,
    viableRowsBeforeCosts: beforeCost.viableRows,
    viableRowsAfterRealisticCosts: realistic.viableRows,
    viableRowsAfterStressCosts: stress.viableRows,
    walkForwardRealisticAccepted: realisticWf.acceptedSymbols,
    walkForwardStressAccepted: stressWf.acceptedSymbols
  };
}

function writeCsv(file, rows) {
  const cols = ["symbol", "interval", "candles", "trades", "grossReturn", "netReturn", "profitFactor", "maxDrawdown", "winRate", "avgTrade", "exposurePct", "totalFees", "totalSlippageCost", "roundTripAverageCostPct", "auditOk", "rejectedReasons"];
  fs.writeFileSync(file, [cols.join(",")].concat(rows.map((row) => cols.map((col) => csvValue(Array.isArray(row[col]) ? row[col].join("|") : row[col])).join(","))).join("\n"));
}

function csvValue(value) {
  const text = String(value === undefined || value === null ? "" : value);
  return /[",\n]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
}

function unique(values) {
  return Array.from(new Set(values));
}

function median(values) {
  const nums = values.filter(Number.isFinite).sort((a, b) => a - b);
  return nums.length ? nums[Math.floor(nums.length / 2)] : 0;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round((Number(value) || 0) * factor) / factor;
}

loadData().then((loaded) => {
  ensureDir(options.outputDir);
  const beforeCost = validateMatrix(loaded, zeroCosts());
  const realistic = validateMatrix(loaded, realisticCosts());
  const stress = validateMatrix(loaded, stressCosts());
  const realisticWf = walkForward(loaded, realisticCosts());
  const stressWf = walkForward(loaded, stressCosts());
  const split = splitTests(loaded, realisticCosts());
  const gates = readiness(beforeCost, realistic, stress, realisticWf, stressWf, split);
  const validation = {
    strategy: options.strategy,
    regimeMode: options.regimeMode,
    params: candidateParams,
    fillModel: options.fillModel,
    realisticCostSettings: realisticCosts(),
    beforeCost,
    realistic,
    stress,
    split,
    walkForwardRealistic: realisticWf,
    walkForwardStress: stressWf,
    readiness: gates
  };
  const audit = {
    ok: realistic.auditOk,
    realisticAudits: realistic.audits,
    stressAudits: stress.audits
  };
  fs.writeFileSync(path.join(options.outputDir, "candidate-validation.json"), JSON.stringify(validation, null, 2));
  writeCsv(path.join(options.outputDir, "candidate-validation.csv"), realistic.rows);
  fs.writeFileSync(path.join(options.outputDir, "candidate-audit.json"), JSON.stringify(audit, null, 2));
  fs.writeFileSync(path.join(options.outputDir, "candidate-cost-breakdown.json"), JSON.stringify(costBreakdown(beforeCost, realistic, stress), null, 2));
  fs.writeFileSync(path.join(options.outputDir, "candidate-next-actions.json"), JSON.stringify({
    paperSimulationReady: gates.paperSimulationReady,
    reasons: gates.reasons,
    nextAction: gates.paperSimulationReady
      ? "Candidate passes execution/cost gates; next step is paper-simulation wiring with continued out-of-sample monitoring."
      : "Do not paper trade yet; fix the listed execution/cost gate failures before further optimization."
  }, null, 2));
  process.stdout.write(JSON.stringify({
    auditOk: audit.ok,
    fillModel: options.fillModel,
    realisticCostSettings: realisticCosts(),
    viableRowsBeforeCosts: beforeCost.viableRows,
    viableRowsAfterRealisticCosts: realistic.viableRows,
    viableRowsAfterStressCosts: stress.viableRows,
    walkForwardRealistic: realisticWf,
    walkForwardStress: stressWf,
    paperSimulationReady: gates.paperSimulationReady,
    nextAction: gates.reasons
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
