const fs = require("fs");
const path = require("path");

const data = require("../core/data");
const indicators = require("../core/indicators");
const regime = require("../core/regime");
const backtest = require("../core/backtest");
const tradeAudit = require("../core/backtest/tradeAudit");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));
const options = {
  source: args.source || "bybit",
  strategy: args.strategy || "SimpleAtrTrendV2",
  regimeMode: args["regime-mode"] || "looseBtcBull",
  days: Number(args.days || 365),
  symbols: list(args.symbols || "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"),
  intervals: list(args.intervals || "15m,1h,4h"),
  limit: Number(args.limit || 20000),
  stage1Max: Number(args["stage1-max"] || 500),
  stage2Max: Number(args["stage2-max"] || 1000),
  validateTop: Number(args["validate-top"] || 5),
  outputDir: args.output || "reports",
  from: args.from || argsUtil.daysToFrom(args.days || 365),
  to: args.to || new Date().toISOString()
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

function loadData() {
  const matrix = {};
  return data.fetchCandles({ source: options.source, symbol: "BTCUSDT", interval: "4h", from: options.from, to: options.to, limit: 3000 }).then(function (regimeCandles) {
    const jobs = [];
    options.symbols.forEach((symbol) => options.intervals.forEach((interval) => {
      jobs.push(function () {
        return data.fetchCandles({ source: options.source, symbol, interval, from: options.from, to: options.to, limit: limitForInterval(interval) }).then((candles) => {
          matrix[symbol + ":" + interval] = prepareDataset(symbol, interval, candles, regimeCandles);
        });
      });
    }));
    return jobs.reduce((p, job) => p.then(job), Promise.resolve()).then(() => ({ matrix, regimeCandles }));
  });
}

function prepareDataset(symbol, interval, candles, regimeCandles) {
  const normalized = data.normalizeCandles(candles);
  const mapped = regime.mapRegimeToCandles(normalized, regimeCandles);
  const close = mapped.map((c) => c.close);
  return {
    symbol,
    interval,
    candles: mapped,
    close,
    atr14: indicators.atr(mapped, 14),
    rsi14: indicators.rsi(close, 14),
    emaCache: {},
    regimeBull: mapped.map((c) => c.btcClose4h && c.btcEma200_4h && c.btcClose4h > c.btcEma200_4h)
  };
}

function ema(dataset, period) {
  if (!dataset.emaCache[period]) dataset.emaCache[period] = indicators.ema(dataset.close, period);
  return dataset.emaCache[period];
}

function stage1Combos() {
  const raw = expand({
    useRsiFilter: [true, false],
    atrMultiplier: [1.8, 2.0, 2.3, 2.5, 2.8, 3.0, 3.3],
    emaFast: [10, 20, 30],
    emaSlow: [50, 80, 100],
    emaTrend: [150, 200],
    rsiMin: [45, 48, 50],
    rsiMax: [68, 70, 74],
    cooldownBars: [0, 3, 6],
    minHoldBars: [1, 3, 6]
  }).filter(validCombo);
  return sample(raw.map(normalizeCombo), options.stage1Max);
}

function validCombo(p) {
  return p.emaFast < p.emaSlow && p.rsiMin < p.rsiMax;
}

function normalizeCombo(p) {
  const out = Object.assign({}, p);
  if (!out.useRsiFilter) {
    out.rsiMin = null;
    out.rsiMax = null;
  }
  return out;
}

function key(p) {
  return JSON.stringify(p);
}

function expand(ranges) {
  const keys = Object.keys(ranges);
  let combos = [{}];
  keys.forEach((name) => {
    const next = [];
    combos.forEach((base) => ranges[name].forEach((value) => next.push(Object.assign({}, base, { [name]: value }))));
    combos = next;
  });
  const byKey = {};
  combos.forEach((combo) => { byKey[key(normalizeCombo(combo))] = normalizeCombo(combo); });
  return Object.keys(byKey).map((id) => byKey[id]);
}

function sample(items, max) {
  if (items.length <= max) return items;
  const out = [];
  const step = items.length / max;
  for (let i = 0; i < max; i += 1) out.push(items[Math.floor(i * step)]);
  return out;
}

function evaluateCombos(name, combos, matrix) {
  const rows = combos.map((params, index) => {
    const matrixRows = Object.keys(matrix).map((datasetKey) => evaluateDataset(matrix[datasetKey], params));
    const row = aggregate(params, matrixRows);
    row.rankInput = index + 1;
    return row;
  }).sort((a, b) => b.score - a.score);
  writeResultSet("v2-opt-" + name + "-results", rows);
  return rows;
}

function evaluateDataset(ds, params) {
  const fast = ema(ds, params.emaFast);
  const slow = ema(ds, params.emaSlow);
  const trend = ema(ds, params.emaTrend);
  let cash = 10000;
  let position = null;
  let cooldown = 0;
  let exposure = 0;
  let peak = 10000;
  let maxDd = 0;
  const trades = [];
  const blockerCounts = {};
  for (let i = 0; i < ds.candles.length; i += 1) {
    const c = ds.candles[i];
    if (position) {
      exposure += 1;
      const trail = Math.max(position.trail, c.close - ds.atr14[i] * params.atrMultiplier);
      position.trail = trail;
      const barsHeld = i - position.entryIndex;
      if (c.low <= trail || (barsHeld >= params.minHoldBars && c.close < slow[i])) {
        const exit = c.low <= trail ? trail : c.close;
        const pnl = (exit - position.entry) * position.size;
        cash += pnl;
        trades.push({ returnPct: pnl / 10000 * 100, barsHeld });
        position = null;
        cooldown = params.cooldownBars;
      }
    }
    if (!position) {
      const blockers = [];
      if (cooldown > 0) blockers.push("cooldownBlocked");
      if (!ds.regimeBull[i]) blockers.push("emaTrendFailed");
      if (!(fast[i] > slow[i] && c.close > slow[i] && c.close > trend[i])) blockers.push("emaTrendFailed");
      if (params.useRsiFilter && !(ds.rsi14[i] >= params.rsiMin && ds.rsi14[i] <= params.rsiMax)) blockers.push("pullbackReclaimFailed");
      if (!ds.atr14[i] || ds.atr14[i] <= 0) blockers.push("atrMissing");
      blockers.forEach((reason) => { blockerCounts[reason] = (blockerCounts[reason] || 0) + 1; });
      if (!blockers.length) {
        const stopDistance = ds.atr14[i] * params.atrMultiplier;
        const size = cash * 0.005 / stopDistance;
        if (Number.isFinite(size) && size > 0) position = { entryIndex: i, entry: c.close, size, trail: c.close - stopDistance };
      }
    }
    if (!position && cooldown > 0) cooldown -= 1;
    const equity = position ? cash + (c.close - position.entry) * position.size : cash;
    peak = Math.max(peak, equity);
    maxDd = Math.max(maxDd, peak ? (peak - equity) / peak * 100 : 0);
  }
  if (position) {
    const last = ds.candles[ds.candles.length - 1];
    const pnl = (last.close - position.entry) * position.size;
    cash += pnl;
    trades.push({ returnPct: pnl / 10000 * 100, barsHeld: ds.candles.length - 1 - position.entryIndex });
  }
  const returns = trades.map((t) => t.returnPct);
  const wins = returns.filter((v) => v > 0);
  const losses = returns.filter((v) => v < 0);
  const row = {
    symbol: ds.symbol,
    interval: ds.interval,
    candles: ds.candles.length,
    trades: trades.length,
    totalReturn: round((cash / 10000 - 1) * 100),
    profitFactor: round(profitFactor(wins, losses)),
    maxDrawdown: round(maxDd),
    winRate: round(trades.length ? wins.length / trades.length * 100 : 0),
    avgTrade: round(avg(returns)),
    avgBarsHeld: round(avg(trades.map((t) => t.barsHeld))),
    exposurePct: round(ds.candles.length ? exposure / ds.candles.length * 100 : 0),
    auditOk: null,
    auditMode: "fast-evaluator-no-trade-audit",
    blockerCounts
  };
  row.viable = viable(row);
  return row;
}

function viable(row) {
  const minTrades = row.interval === "4h" ? 20 : 50;
  return row.trades >= minTrades && row.totalReturn > 0 && row.profitFactor > 1.12 && row.maxDrawdown < 15 && row.auditOk !== false;
}

function aggregate(params, rows) {
  const viableRows = rows.filter((r) => r.viable);
  const viableSymbols = new Set(viableRows.map((r) => r.symbol));
  const viableIntervals = new Set(viableRows.map((r) => r.interval));
  const auditFailures = rows.filter((r) => r.auditOk === false).length;
  const unauditedRows = rows.filter((r) => r.auditOk === null).length;
  const score = viableRows.length * 10 +
    median(rows.map((r) => r.profitFactor)) * 3 +
    median(rows.map((r) => r.totalReturn)) -
    median(rows.map((r) => r.maxDrawdown)) * 0.5 +
    Math.log(1 + rows.reduce((sum, r) => sum + r.trades, 0)) -
    auditFailures * 20 -
    (viableSymbols.size <= 1 ? 8 : 0) -
    (viableIntervals.size <= 1 ? 8 : 0);
  return {
    strategy: options.strategy,
    regimeMode: options.regimeMode,
    params,
    score: round(score),
    viableRows: viableRows.length,
    viableSymbols: Array.from(viableSymbols),
    viableIntervals: Array.from(viableIntervals),
    medianProfitFactor: round(median(rows.map((r) => r.profitFactor))),
    medianReturn: round(median(rows.map((r) => r.totalReturn))),
    medianDrawdown: round(median(rows.map((r) => r.maxDrawdown))),
    totalTrades: rows.reduce((sum, r) => sum + r.trades, 0),
    auditFailures,
    unauditedRows,
    rows
  };
}

function buildStage2(top) {
  const byKey = {};
  top.slice(0, 10).forEach((row) => {
    const p = row.params;
    expand({
      useRsiFilter: [p.useRsiFilter],
      atrMultiplier: near(p.atrMultiplier, [0, -0.4, -0.2, 0.2, 0.4], 1.2, 5),
      emaFast: near(p.emaFast, [0, -5, 5], 5, 60),
      emaSlow: near(p.emaSlow, [0, -20, -10, 10, 20], 20, 150),
      emaTrend: near(p.emaTrend, [0, -50, -25, 25, 50], 75, 300),
      rsiMin: p.useRsiFilter ? near(p.rsiMin, [0, -2, 2], 30, 60) : [null],
      rsiMax: p.useRsiFilter ? near(p.rsiMax, [0, -2, 2], 55, 85) : [null],
      cooldownBars: near(p.cooldownBars, [0, -3, 3], 0, 12),
      minHoldBars: near(p.minHoldBars, [0, -2, 2, -3, 3], 1, 10)
    }).filter((combo) => combo.emaFast < combo.emaSlow && (!combo.useRsiFilter || combo.rsiMin < combo.rsiMax)).forEach((combo) => {
      byKey[key(normalizeCombo(combo))] = normalizeCombo(combo);
    });
  });
  return sample(Object.keys(byKey).map((id) => byKey[id]), options.stage2Max);
}

function near(value, deltas, min, max) {
  return Array.from(new Set(deltas.map((d) => {
    const next = Number(value) + d;
    return Number.isInteger(value) ? Math.round(next) : Math.round(next * 10) / 10;
  }).filter((v) => v >= min && v <= max)));
}

function validateTop(top, loaded) {
  return top.slice(0, options.validateTop).map((row, index) => {
    const full = validateMatrix(row.params, loaded, { feePct: 0, slippagePct: 0 });
    const stress = validateMatrix(row.params, loaded, { feePct: 0.12, slippagePct: 0.06 });
    const wf = walkForward(row.params, loaded);
    const split = splitTests(row.params, loaded);
    const rejected = rejectionReasons(full, stress, wf, split);
    return { rank: index + 1, params: row.params, score: row.score, full, walkForward: wf, split, stress, rejected, accepted: rejected.length === 0 };
  });
}

function validateMatrix(params, loaded, costs) {
  const rows = Object.keys(loaded.matrix).map((k) => {
    const ds = loaded.matrix[k];
    const result = backtest.runBacktestOnCandles({ symbol: ds.symbol, interval: ds.interval, strategy: options.strategy, candles: ds.candles, regimeCandles: loaded.regimeCandles, params: Object.assign({}, params, { regimeMode: options.regimeMode, feePct: costs.feePct, slippagePct: costs.slippagePct }) });
    const audit = tradeAudit.auditTrades(result, ds.candles);
    const row = metrics(result, ds, audit);
    row.viable = viable(row);
    return row;
  });
  return aggregate(params, rows);
}

function metrics(result, ds, audit) {
  return {
    symbol: ds.symbol,
    interval: ds.interval,
    candles: ds.candles.length,
    trades: result.trades,
    totalReturn: result.totalReturn,
    profitFactor: result.profitFactor,
    maxDrawdown: result.maxDrawdown,
    winRate: result.winRate,
    avgTrade: result.averageTrade,
    avgBarsHeld: result.avgBarsHeld,
    exposurePct: result.exposurePct,
    auditOk: audit.ok,
    blockerCounts: result.diagnostics ? result.diagnostics.blockerCounts : {}
  };
}

function walkForward(params, loaded) {
  const out = {};
  ["BTCUSDT", "ETHUSDT", "BNBUSDT"].filter((symbol) => loaded.matrix[symbol + ":1h"]).forEach((symbol) => {
    const ds = loaded.matrix[symbol + ":1h"];
    const foldSize = Math.floor(ds.candles.length / 5);
    const folds = [];
    for (let fold = 1; fold <= 4; fold += 1) {
      const train = ds.candles.slice(0, foldSize * fold);
      const test = ds.candles.slice(foldSize * fold, foldSize * (fold + 1));
      const trainResult = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: options.strategy, candles: train, regimeCandles: loaded.regimeCandles, params: Object.assign({}, params, { regimeMode: options.regimeMode }) });
      const testResult = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: options.strategy, candles: test, regimeCandles: loaded.regimeCandles, params: Object.assign({}, params, { regimeMode: options.regimeMode }) });
      folds.push({ fold, trainReturn: trainResult.totalReturn, testReturn: testResult.totalReturn, trainPF: trainResult.profitFactor, testPF: testResult.profitFactor, trainTrades: trainResult.trades, testTrades: testResult.trades });
    }
    const positive = folds.filter((f) => f.testReturn > 0).length;
    const lowTrades = folds.filter((f) => f.testTrades < 5).length;
    const collapse = folds.filter((f) => f.trainPF > 1.5 && f.testPF < 1).length;
    out[symbol] = { accepted: positive >= 2 && lowTrades <= 1 && collapse <= 1, positiveTestFolds: positive, lowTradeFolds: lowTrades, pfCollapseFolds: collapse, folds };
  });
  out.acceptedSymbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"].filter((s) => out[s] && out[s].accepted).length;
  return out;
}

function splitTests(params, loaded) {
  const out = {};
  ["BTCUSDT", "ETHUSDT"].filter((symbol) => loaded.matrix[symbol + ":1h"]).forEach((symbol) => {
    const ds = loaded.matrix[symbol + ":1h"];
    const cut = Math.floor(ds.candles.length * 0.7);
    const train = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: options.strategy, candles: ds.candles.slice(0, cut), regimeCandles: loaded.regimeCandles, params: Object.assign({}, params, { regimeMode: options.regimeMode }) });
    const test = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: options.strategy, candles: ds.candles.slice(cut), regimeCandles: loaded.regimeCandles, params: Object.assign({}, params, { regimeMode: options.regimeMode }) });
    out[symbol] = { train: compactMetrics(train), test: compactMetrics(test) };
  });
  return out;
}

function rejectionReasons(full, stress, wf, split) {
  const reasons = [];
  if (full.viableRows < 3) reasons.push("fewer than 3 viable rows");
  if (full.viableSymbols.length < 2) reasons.push("fewer than 2 viable symbols");
  if (wf.acceptedSymbols < 2) reasons.push("walk-forward accepted symbols < 2 of 3");
  if (split.BTCUSDT && split.ETHUSDT && split.BTCUSDT.test.totalReturn < 0 && split.ETHUSDT.test.totalReturn < 0) reasons.push("BTCUSDT and ETHUSDT split tests are negative");
  if (full.auditFailures > 0) reasons.push("trade audit failure");
  if (stress.viableRows === 0 || stress.medianReturn <= 0) reasons.push("performance disappears under 2x fees/slippage");
  if (full.rows.some((r) => r.maxDrawdown > 15)) reasons.push("drawdown > 15");
  return reasons;
}

function compactMetrics(result) {
  return { totalReturn: result.totalReturn, profitFactor: result.profitFactor, maxDrawdown: result.maxDrawdown, trades: result.trades };
}

function writeResultSet(name, rows) {
  ensureDir(options.outputDir);
  fs.writeFileSync(path.join(options.outputDir, name + ".json"), JSON.stringify(rows, null, 2));
  fs.writeFileSync(path.join(options.outputDir, name + ".csv"), csv(rows));
}

function rankedSummary(stage1, stage2, stage3) {
  const accepted = stage3.filter((r) => r.accepted);
  const best = accepted[0] || stage3[0] || null;
  return { strategy: options.strategy, regimeMode: options.regimeMode, stage1Tested: stage1.length, stage2Tested: stage2.length, topValidated: stage3.length, acceptedCandidates: accepted.length, best };
}

function nextActions(summary) {
  if (!summary.acceptedCandidates) return { optimizationJustified: false, paperTradingSimulation: false, nextAction: "Do not paper trade yet; inspect rejected top candidates and consider broader validation or simpler exits.", reason: summary.best ? summary.best.rejected : ["no candidates"] };
  return { optimizationJustified: true, paperTradingSimulation: true, nextAction: "Prepare paper-trading simulation for the accepted top candidate with small notional and continued out-of-sample monitoring.", params: summary.best.params };
}

function csv(rows) {
  const cols = ["score", "viableRows", "medianProfitFactor", "medianReturn", "medianDrawdown", "totalTrades", "auditFailures", "viableSymbols", "viableIntervals", "params"];
  return [cols.join(",")].concat(rows.map((r) => cols.map((c) => csvValue(c === "params" ? JSON.stringify(r.params) : Array.isArray(r[c]) ? r[c].join("|") : r[c])).join(","))).join("\n");
}

function csvValue(value) {
  const text = String(value === undefined || value === null ? "" : value);
  return /[",\n]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
}

function profitFactor(wins, losses) {
  const win = wins.reduce((s, v) => s + v, 0);
  const loss = Math.abs(losses.reduce((s, v) => s + v, 0));
  return loss ? win / loss : (win ? win : 0);
}

function median(values) {
  const nums = values.filter(Number.isFinite).sort((a, b) => a - b);
  return nums.length ? nums[Math.floor(nums.length / 2)] : 0;
}

function avg(values) {
  return values.length ? values.reduce((s, v) => s + v, 0) / values.length : 0;
}

function round(value) {
  return Math.round((Number(value) || 0) * 10000) / 10000;
}

loadData().then(function (loaded) {
  const stage1 = evaluateCombos("stage1", stage1Combos(), loaded.matrix);
  const stage2 = evaluateCombos("stage2", buildStage2(stage1), loaded.matrix);
  const stage3 = validateTop(stage2, loaded);
  fs.writeFileSync(path.join(options.outputDir, "v2-opt-stage3-validation.json"), JSON.stringify(stage3, null, 2));
  const summary = rankedSummary(stage1, stage2, stage3);
  const actions = nextActions(summary);
  fs.writeFileSync(path.join(options.outputDir, "v2-opt-ranked-summary.json"), JSON.stringify(summary, null, 2));
  fs.writeFileSync(path.join(options.outputDir, "v2-opt-next-actions.json"), JSON.stringify(actions, null, 2));
  process.stdout.write(JSON.stringify({
    stage1CombosTested: stage1.length,
    stage2CombosTested: stage2.length,
    top5Validated: stage3.length,
    bestParameterSet: summary.best ? summary.best.params : null,
    viableRowsCount: summary.best ? summary.best.full.viableRows : 0,
    walkForwardResult: summary.best ? summary.best.walkForward : null,
    stressTestResult: summary.best ? { viableRows: summary.best.stress.viableRows, medianReturn: summary.best.stress.medianReturn } : null,
    paperTradingSimulationReady: actions.paperTradingSimulation,
    nextAction: actions.nextAction
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch(function (error) {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
