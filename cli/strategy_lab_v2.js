const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");
const tradeAudit = require("../core/backtest/tradeAudit");
const argsUtil = require("./args");
const runtime = require("./runtime");

const FAMILIES = [
  "PullbackReclaimV2",
  "EmaBounceV2",
  "BreakoutRetestV2",
  "RangeExpansionV2",
  "RelativeStrengthV2",
  "SimpleAtrTrendV2"
];

const REGIME_MODES = ["strictBtcBull", "looseBtcBull", "symbolTrend", "symbolFastTrend", "noRegime"];
const args = argsUtil.parseArgs(process.argv.slice(2));
const options = {
  source: args.source || "bybit",
  days: Number(args.days || 365),
  symbols: list(args.symbols || "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"),
  intervals: list(args.intervals || "15m,1h,4h"),
  limit: Number(args.limit || 20000),
  outputDir: args.output || "reports",
  from: args.from || argsUtil.daysToFrom(args.days || 365),
  to: args.to || new Date().toISOString()
};

function list(value) {
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function limitForInterval(interval) {
  if (interval === "15m") return Math.min(options.limit, 20000);
  if (interval === "1h") return Math.min(options.limit, 9000);
  if (interval === "4h") return Math.min(options.limit, 3000);
  return options.limit;
}

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir);
}

function loadData() {
  const matrix = {};
  return data.fetchCandles({ source: options.source, symbol: "BTCUSDT", interval: "4h", from: options.from, to: options.to, limit: 3000 }).then(function (regimeCandles) {
    const jobs = [];
    options.symbols.forEach(function (symbol) {
      options.intervals.forEach(function (interval) {
        jobs.push(function () {
          return data.fetchCandles({ source: options.source, symbol, interval, from: options.from, to: options.to, limit: limitForInterval(interval) }).then(function (candles) {
            matrix[symbol + ":" + interval] = candles;
          });
        });
      });
    });
    return jobs.reduce((p, job) => p.then(job), Promise.resolve()).then(() => ({ matrix, regimeCandles }));
  });
}

function runRow(family, regimeMode, symbol, interval, candles, regimeCandles) {
  const result = backtest.runBacktestOnCandles({
    symbol,
    interval,
    strategy: family,
    candles,
    regimeCandles,
    params: { regimeMode }
  });
  const audit = tradeAudit.auditTrades(result, candles);
  const row = {
    family,
    regimeMode,
    symbol,
    interval,
    candles: candles.length,
    trades: result.trades,
    totalReturn: result.totalReturn,
    profitFactor: result.profitFactor,
    maxDrawdown: result.maxDrawdown,
    winRate: result.winRate,
    avgTrade: result.averageTrade,
    avgBarsHeld: result.avgBarsHeld,
    exposurePct: result.exposurePct,
    auditOk: audit.ok,
    primaryBlocker: result.diagnostics ? result.diagnostics.primaryBlocker : null,
    blockerCounts: result.diagnostics ? result.diagnostics.blockerCounts : {}
  };
  row.viable = viable(row);
  row.preferredTradeCount = row.interval === "4h" ? row.trades >= 20 : row.trades >= 80;
  return row;
}

function viable(row) {
  const minTrades = row.interval === "4h" ? 20 : 50;
  return row.trades >= minTrades &&
    row.totalReturn > 0 &&
    row.profitFactor > 1.12 &&
    row.maxDrawdown < 15 &&
    row.auditOk === true;
}

function walkForward(key, loaded) {
  const parts = key.split("|");
  const family = parts[0];
  const regimeMode = parts[1];
  const symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"];
  const verdicts = {};
  symbols.forEach(function (symbol) {
    const candles = loaded.matrix[symbol + ":1h"] || [];
    const folds = [];
    const foldSize = Math.floor(candles.length / 5);
    for (let fold = 1; fold <= 4; fold += 1) {
      const train = candles.slice(0, foldSize * fold);
      const test = candles.slice(foldSize * fold, foldSize * (fold + 1));
      const trainResult = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: family, candles: train, regimeCandles: loaded.regimeCandles, params: { regimeMode } });
      const testResult = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: family, candles: test, regimeCandles: loaded.regimeCandles, params: { regimeMode } });
      folds.push({ fold, trainReturn: trainResult.totalReturn, testReturn: testResult.totalReturn, trainPF: trainResult.profitFactor, testPF: testResult.profitFactor, trainTrades: trainResult.trades, testTrades: testResult.trades });
    }
    const positive = folds.filter((f) => f.testReturn > 0).length;
    const lowTrades = folds.filter((f) => f.testTrades < 5).length;
    const collapse = folds.filter((f) => f.trainPF > 1.5 && f.testPF < 1).length;
    verdicts[symbol] = { accepted: positive >= 2 && lowTrades <= 1 && collapse <= 1, positiveTestFolds: positive, lowTradeFolds: lowTrades, pfCollapseFolds: collapse, folds };
  });
  const acceptedSymbols = Object.values(verdicts).filter((item) => item.accepted).length;
  return { accepted: acceptedSymbols >= 2, acceptedSymbols, symbols: verdicts };
}

function summarize(rows, walkForwards) {
  const groups = {};
  rows.forEach(function (row) {
    const key = row.family + "|" + row.regimeMode;
    groups[key] = groups[key] || [];
    groups[key].push(row);
  });
  const summaries = Object.keys(groups).map(function (key) {
    const rowsForKey = groups[key];
    const viableRows = rowsForKey.filter((row) => row.viable);
    const matrixPromising = viableRows.length >= 3 ||
      (viableRows.some((row) => row.symbol === "BTCUSDT" && row.interval === "1h") && viableRows.length >= 2);
    const wf = walkForwards[key];
    const noRegime = key.endsWith("|noRegime");
    return {
      key,
      family: key.split("|")[0],
      regimeMode: key.split("|")[1],
      viableRows: viableRows.length,
      medianProfitFactor: median(rowsForKey.map((row) => row.profitFactor)),
      medianDrawdown: median(rowsForKey.map((row) => row.maxDrawdown)),
      totalTrades: rowsForKey.reduce((sum, row) => sum + row.trades, 0),
      promising: matrixPromising && wf && wf.accepted && !noRegime,
      rejectionReason: rejection(matrixPromising, wf, noRegime, viableRows.length),
      commonBlockers: commonBlockers(rowsForKey)
    };
  });
  const promising = summaries.filter((item) => item.promising).sort(rank);
  const bestRaw = rows.slice().sort((a, b) => b.profitFactor - a.profitFactor || b.totalReturn - a.totalReturn)[0] || null;
  return {
    testedFamilies: FAMILIES,
    regimeModesTested: REGIME_MODES,
    viableRows: rows.filter((row) => row.viable).length,
    promisingFamilies: promising.map((item) => ({ family: item.family, regimeMode: item.regimeMode, viableRows: item.viableRows })),
    rejectedFamilies: summaries.filter((item) => !item.promising).map((item) => ({ family: item.family, regimeMode: item.regimeMode, reason: item.rejectionReason })),
    commonBlockers: commonBlockers(rows),
    bestRawRows: rows.slice().sort((a, b) => b.profitFactor - a.profitFactor || b.totalReturn - a.totalReturn).slice(0, 10).map(compact),
    bestRobustCandidate: promising[0] ? { family: promising[0].family, regimeMode: promising[0].regimeMode } : null,
    walkForwardVerdicts: walkForwards,
    optimizationJustified: promising.length > 0,
    groupSummaries: summaries
  };
}

function rejection(matrixPromising, wf, noRegime, viableRows) {
  if (noRegime && matrixPromising) return "noRegime cannot be final recommendation without stronger follow-up validation.";
  if (!viableRows) return "No rows passed viability gates.";
  if (!matrixPromising) return "Too few viable rows across the matrix.";
  if (!wf) return "Walk-forward not run.";
  if (!wf.accepted) return "Walk-forward failed fewer than 2 of 3 symbols.";
  return "Rejected by safety gates.";
}

function nextActions(summary) {
  if (!summary.optimizationJustified) {
    const blocker = summary.commonBlockers[0] || { reason: "unknown", count: 0 };
    return {
      recommendedNextExperiment: "Test adaptive trend filters: EMA20/EMA50 trend plus volatility/range entry, with BTC regime only as a score rather than a hard gate.",
      why: "No v2 family passed matrix and walk-forward gates.",
      exactStrategyToOptimize: null,
      parameterRanges: null,
      optimizationJustified: false,
      rejectionReason: "No robust v2 candidate. Dominant blocker: " + blocker.reason + " (" + blocker.count + ")."
    };
  }
  const candidate = summary.bestRobustCandidate;
  return {
    recommendedNextExperiment: "Prepare staged optimization for " + candidate.family + " using regimeMode=" + candidate.regimeMode + ".",
    why: "This family/mode passed viability and walk-forward gates.",
    exactStrategyToOptimize: candidate.family,
    regimeMode: candidate.regimeMode,
    parameterRanges: ranges(candidate.family),
    optimizationJustified: true,
    stageLimits: { stage1MaxCombos: 500, stage2MaxCombos: 1000, validateTop: 5 }
  };
}

function ranges(family) {
  if (family === "PullbackReclaimV2") return { rsiPullbackLevel: [38, 42, 45], rsiReclaimLevel: [48, 50, 52], atrMultiplier: [2, 2.5, 3] };
  if (family === "EmaBounceV2") return { emaBounceAtr: [0.4, 0.8, 1.2], atrMultiplier: [2, 2.5, 3] };
  if (family === "BreakoutRetestV2") return { retestLookback: [5, 10, 15], retestAtr: [0.5, 0.8, 1.1], atrMultiplier: [2, 2.5, 3] };
  if (family === "RangeExpansionV2") return { squeezePercentile: [0.25, 0.35, 0.45], closeHighPct: [0.6, 0.7, 0.8], atrMultiplier: [2, 2.5, 3] };
  if (family === "RelativeStrengthV2") return { rsLookback: [12, 24, 48], rsiMin: [45, 48, 50], rsiMax: [70, 74, 78] };
  return { useRsiFilter: [true, false], atrMultiplier: [2, 2.5, 3] };
}

function rank(a, b) {
  return b.viableRows - a.viableRows || b.medianProfitFactor - a.medianProfitFactor || a.medianDrawdown - b.medianDrawdown || b.totalTrades - a.totalTrades;
}

function median(values) {
  const nums = values.filter(Number.isFinite).sort((a, b) => a - b);
  return nums.length ? nums[Math.floor(nums.length / 2)] : 0;
}

function commonBlockers(rows) {
  const counts = {};
  rows.forEach((row) => Object.keys(row.blockerCounts || {}).forEach((key) => { counts[key] = (counts[key] || 0) + Number(row.blockerCounts[key] || 0); }));
  return Object.keys(counts).map((key) => ({ reason: key, count: counts[key] })).sort((a, b) => b.count - a.count).slice(0, 10);
}

function compact(row) {
  return row && { family: row.family, regimeMode: row.regimeMode, symbol: row.symbol, interval: row.interval, trades: row.trades, totalReturn: row.totalReturn, profitFactor: row.profitFactor, maxDrawdown: row.maxDrawdown, viable: row.viable };
}

function csv(rows) {
  const cols = ["family", "regimeMode", "symbol", "interval", "candles", "trades", "totalReturn", "profitFactor", "maxDrawdown", "winRate", "avgTrade", "avgBarsHeld", "exposurePct", "auditOk", "primaryBlocker", "viable", "preferredTradeCount"];
  return [cols.join(",")].concat(rows.map((row) => cols.map((col) => csvValue(row[col])).join(","))).join("\n");
}

function csvValue(value) {
  const text = String(value === undefined || value === null ? "" : value);
  return /[",\n]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
}

loadData().then(function (loaded) {
  const rows = [];
  FAMILIES.forEach((family) => REGIME_MODES.forEach((mode) => options.symbols.forEach((symbol) => options.intervals.forEach((interval) => {
    rows.push(runRow(family, mode, symbol, interval, loaded.matrix[symbol + ":" + interval], loaded.regimeCandles));
  }))));

  const matrixCandidates = {};
  rows.forEach(function (row) {
    const key = row.family + "|" + row.regimeMode;
    matrixCandidates[key] = matrixCandidates[key] || [];
    if (row.viable) matrixCandidates[key].push(row);
  });
  const walkForwards = {};
  Object.keys(matrixCandidates).forEach(function (key) {
    const viableRows = matrixCandidates[key];
    const btc1h = viableRows.some((row) => row.symbol === "BTCUSDT" && row.interval === "1h");
    if (viableRows.length >= 3 || (btc1h && viableRows.length >= 2)) walkForwards[key] = walkForward(key, loaded);
  });

  const summary = summarize(rows, walkForwards);
  const actions = nextActions(summary);
  ensureDir(options.outputDir);
  fs.writeFileSync(path.join(options.outputDir, "lab-v2-results.json"), JSON.stringify({ generatedAt: new Date().toISOString(), options, testsRun: rows.length, rows }, null, 2));
  fs.writeFileSync(path.join(options.outputDir, "lab-v2-results.csv"), csv(rows));
  fs.writeFileSync(path.join(options.outputDir, "lab-v2-summary.json"), JSON.stringify(summary, null, 2));
  fs.writeFileSync(path.join(options.outputDir, "lab-v2-next-actions.json"), JSON.stringify(actions, null, 2));
  process.stdout.write(JSON.stringify({
    totalTestsRun: rows.length,
    viableRows: summary.viableRows,
    promisingFamilies: summary.promisingFamilies,
    top10Rows: summary.bestRawRows,
    bestRobustCandidate: summary.bestRobustCandidate,
    nextRecommendedAction: actions.recommendedNextExperiment,
    optimizationJustified: actions.optimizationJustified
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch(function (error) {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
