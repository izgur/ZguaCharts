const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");
const tradeAudit = require("../core/backtest/tradeAudit");
const argsUtil = require("./args");
const runtime = require("./runtime");

const FAMILIES = [
  "EmaPullbackContinuation",
  "TrendBreakoutRetest",
  "VolatilitySqueezeBreakout",
  "MeanReversionInBullRegime",
  "MomentumContinuation"
];

const args = argsUtil.parseArgs(process.argv.slice(2));
const options = {
  source: args.source || "bybit",
  days: Number(args.days || 365),
  symbols: csvList(args.symbols || "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"),
  intervals: csvList(args.intervals || "15m,1h,4h"),
  limit: Number(args.limit || 20000),
  outputDir: args.output || "reports",
  from: args.from || argsUtil.daysToFrom(args.days || 365),
  to: args.to || new Date().toISOString()
};

function csvList(value) {
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

function loadMatrixCandles() {
  const matrix = {};
  return data.fetchCandles({
    source: options.source,
    symbol: "BTCUSDT",
    interval: "4h",
    from: options.from,
    to: options.to,
    limit: 3000
  }).then(function (regimeCandles) {
    const jobs = [];
    options.symbols.forEach(function (symbol) {
      options.intervals.forEach(function (interval) {
        jobs.push(function () {
          return data.fetchCandles({
            source: options.source,
            symbol,
            interval,
            from: options.from,
            to: options.to,
            limit: limitForInterval(interval)
          }).then(function (candles) {
            matrix[symbol + ":" + interval] = candles;
          });
        });
      });
    });
    return runSequential(jobs).then(function () {
      return { matrix, regimeCandles };
    });
  });
}

function runSequential(jobs) {
  return jobs.reduce(function (promise, job) {
    return promise.then(job);
  }, Promise.resolve());
}

function runFamilyRow(family, symbol, interval, candles, regimeCandles) {
  const result = backtest.runBacktestOnCandles({
    symbol,
    interval,
    strategy: family,
    candles,
    regimeCandles,
    params: {}
  });
  const audit = tradeAudit.auditTrades(result, candles);
  const row = {
    family,
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
    blockerCounts: result.diagnostics ? result.diagnostics.blockerCounts : {},
    tradeList: result.tradeList,
    markers: result.markers
  };
  row.viable = isViable(row);
  return row;
}

function isViable(row) {
  const minTrades = row.interval === "4h" ? 20 : 50;
  return row.trades >= minTrades &&
    row.totalReturn > 0 &&
    row.profitFactor > 1.15 &&
    row.maxDrawdown < 15 &&
    row.auditOk === true;
}

function walkForward(family, symbol, regimeCandles) {
  const candles = labCandles[symbol + ":1h"] || [];
  const folds = 4;
  const foldSize = Math.floor(candles.length / (folds + 1));
  const out = [];
  for (let fold = 1; fold <= folds; fold += 1) {
    const train = candles.slice(0, foldSize * fold);
    const test = candles.slice(foldSize * fold, foldSize * (fold + 1));
    const trainResult = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: family, candles: train, regimeCandles, params: {} });
    const testResult = backtest.runBacktestOnCandles({ symbol, interval: "1h", strategy: family, candles: test, regimeCandles, params: {} });
    out.push({
      fold,
      trainReturn: trainResult.totalReturn,
      testReturn: testResult.totalReturn,
      trainPF: trainResult.profitFactor,
      testPF: testResult.profitFactor,
      trainTrades: trainResult.trades,
      testTrades: testResult.trades
    });
  }
  return out;
}

function walkVerdict(folds) {
  const negative = folds.filter((fold) => fold.testReturn <= 0).length;
  const lowTrades = folds.filter((fold) => fold.testTrades < 5).length;
  const collapse = folds.filter((fold) => fold.trainPF > 1.5 && fold.testPF < 1).length;
  return {
    accepted: negative < Math.ceil(folds.length / 2) && lowTrades <= 1 && collapse <= 1,
    negativeTestFolds: negative,
    lowTradeFolds: lowTrades,
    pfCollapseFolds: collapse,
    folds
  };
}

function analyze(rows, walkForwards) {
  const byFamily = {};
  FAMILIES.forEach(function (family) {
    const familyRows = rows.filter((row) => row.family === family);
    const viable = familyRows.filter((row) => row.viable);
    const btc1hViable = viable.some((row) => row.symbol === "BTCUSDT" && row.interval === "1h");
    const promisingByRows = viable.length >= 3 || (btc1hViable && viable.length >= 2);
    const wf = walkForwards[family] || null;
    const promising = promisingByRows && (!wf || wf.BTCUSDT.accepted || wf.ETHUSDT.accepted);
    byFamily[family] = {
      family,
      rows: familyRows.length,
      viableRows: viable.length,
      medianProfitFactor: median(familyRows.map((row) => row.profitFactor)),
      medianDrawdown: median(familyRows.map((row) => row.maxDrawdown)),
      totalTrades: familyRows.reduce((sum, row) => sum + row.trades, 0),
      promising,
      rejectionReason: promising ? null : rejectionReason(familyRows, viable, wf),
      commonBlockers: commonBlockers(familyRows),
      bestRows: familyRows.slice().sort((a, b) => b.profitFactor - a.profitFactor).slice(0, 3)
    };
  });
  const families = Object.values(byFamily);
  const promisingFamilies = families.filter((item) => item.promising).map((item) => item.family);
  const ranked = families.slice().sort(rankFamily);
  const best = ranked[0] || null;
  const worst = families.slice().sort((a, b) => a.viableRows - b.viableRows || a.medianProfitFactor - b.medianProfitFactor)[0] || null;
  const bestRawRow = rows.slice().sort((a, b) => b.profitFactor - a.profitFactor || b.totalReturn - a.totalReturn)[0] || null;
  return {
    bestStrategyFamily: best ? best.family : null,
    worstStrategyFamily: worst ? worst.family : null,
    viableRowsCount: rows.filter((row) => row.viable).length,
    promisingFamilies,
    rejectedFamilies: families.filter((item) => !item.promising).map((item) => ({ family: item.family, reason: item.rejectionReason })),
    commonBlockers: commonBlockers(rows),
    bestMarketsTimeframes: rows.slice().sort((a, b) => b.profitFactor - a.profitFactor || b.totalReturn - a.totalReturn).slice(0, 10).map(compactRow),
    walkForwardVerdicts: walkForwards,
    optimizationJustified: promisingFamilies.length > 0,
    bestRawRow: compactRow(bestRawRow),
    familySummaries: families.map(function (item) {
      return {
        family: item.family,
        viableRows: item.viableRows,
        medianProfitFactor: item.medianProfitFactor,
        medianDrawdown: item.medianDrawdown,
        totalTrades: item.totalTrades,
        promising: item.promising,
        rejectionReason: item.rejectionReason
      };
    })
  };
}

function nextActions(summary) {
  if (!summary.promisingFamilies.length) {
    const blocker = summary.commonBlockers[0] || { reason: "unknown", count: 0 };
    return {
      recommendedNextExperiment: "Design a new family with less restrictive entry timing and more frequent, testable signals.",
      why: "No Strategy Lab family passed the viability and walk-forward gates.",
      exactStrategyToOptimize: null,
      parameterRanges: null,
      optimizationJustified: false,
      rejectionReason: "No promising family. Dominant blocker: " + blocker.reason + " (" + blocker.count + ")."
    };
  }
  const family = summary.bestStrategyFamily;
  return {
    recommendedNextExperiment: "Run staged optimization for " + family + " only.",
    why: "This was the top-ranked promising family after matrix validation and walk-forward checks.",
    exactStrategyToOptimize: family,
    parameterRanges: rangesForFamily(family),
    optimizationJustified: true,
    stageLimits: { stage1MaxCombos: 500, stage2MaxCombos: 1000, validateTop: 5 }
  };
}

function rangesForFamily(family) {
  if (family === "EmaPullbackContinuation") return { rsiPullbackLevel: [38, 42, 45], rsiReclaimLevel: [48, 50, 52], atrMultiplier: [2, 2.5, 3] };
  if (family === "TrendBreakoutRetest") return { retestLookback: [4, 8, 12], retestAtr: [0.4, 0.6, 0.8], atrMultiplier: [2, 2.5, 3] };
  if (family === "VolatilitySqueezeBreakout") return { squeezeLookback: [60, 100, 150], squeezePercentile: [0.15, 0.25, 0.35], atrMultiplier: [2, 2.5, 3] };
  if (family === "MeanReversionInBullRegime") return { rsiOversold: [28, 32, 35], atrMultiplier: [1.8, 2.2, 2.8] };
  if (family === "MomentumContinuation") return { rsiMin: [48, 50, 52], rsiMax: [66, 70, 74], adxThreshold: [14, 18, 22] };
  return {};
}

function rejectionReason(rows, viable, wf) {
  if (!viable.length) return "No matrix rows passed viability gates.";
  if (viable.length < 3) return "Too few viable rows across symbols/timeframes.";
  if (wf && !wf.BTCUSDT.accepted && !wf.ETHUSDT.accepted) return "Walk-forward rejected BTCUSDT and ETHUSDT 1h.";
  return "Did not satisfy promising-family rules.";
}

function commonBlockers(rows) {
  const counts = {};
  rows.forEach(function (row) {
    Object.keys(row.blockerCounts || {}).forEach(function (key) {
      counts[key] = (counts[key] || 0) + Number(row.blockerCounts[key] || 0);
    });
  });
  return Object.keys(counts).map((key) => ({ reason: key, count: counts[key] })).sort((a, b) => b.count - a.count).slice(0, 10);
}

function rankFamily(a, b) {
  return b.viableRows - a.viableRows ||
    b.medianProfitFactor - a.medianProfitFactor ||
    a.medianDrawdown - b.medianDrawdown ||
    b.totalTrades - a.totalTrades;
}

function median(values) {
  const nums = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!nums.length) return 0;
  return nums[Math.floor(nums.length / 2)];
}

function compactRow(row) {
  if (!row) return null;
  return {
    family: row.family,
    symbol: row.symbol,
    interval: row.interval,
    trades: row.trades,
    totalReturn: row.totalReturn,
    profitFactor: row.profitFactor,
    maxDrawdown: row.maxDrawdown,
    viable: row.viable
  };
}

function writeCsv(rows) {
  const columns = ["family", "symbol", "interval", "candles", "trades", "totalReturn", "profitFactor", "maxDrawdown", "winRate", "avgTrade", "avgBarsHeld", "exposurePct", "auditOk", "primaryBlocker", "viable"];
  return [columns.join(",")].concat(rows.map(function (row) {
    return columns.map(function (column) { return csv(row[column]); }).join(",");
  })).join("\n");
}

function csv(value) {
  const text = String(value === undefined || value === null ? "" : value);
  return /[",\n]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
}

let labCandles = {};

loadMatrixCandles().then(function (loaded) {
  labCandles = loaded.matrix;
  const rows = [];
  FAMILIES.forEach(function (family) {
    options.symbols.forEach(function (symbol) {
      options.intervals.forEach(function (interval) {
        rows.push(runFamilyRow(family, symbol, interval, loaded.matrix[symbol + ":" + interval], loaded.regimeCandles));
      });
    });
  });

  const provisionalSummary = analyze(rows, {});
  const promisingByMatrix = provisionalSummary.familySummaries.filter(function (item) {
    return item.viableRows >= 3 || (rows.some((row) => row.family === item.family && row.symbol === "BTCUSDT" && row.interval === "1h" && row.viable) && item.viableRows >= 2);
  }).map((item) => item.family);
  const walkForwards = {};
  promisingByMatrix.forEach(function (family) {
    walkForwards[family] = {
      BTCUSDT: walkVerdict(walkForward(family, "BTCUSDT", loaded.regimeCandles)),
      ETHUSDT: walkVerdict(walkForward(family, "ETHUSDT", loaded.regimeCandles))
    };
  });

  const summary = analyze(rows, walkForwards);
  const actions = nextActions(summary);
  const result = {
    generatedAt: new Date().toISOString(),
    options,
    testsRun: rows.length,
    candlesLoaded: Object.keys(loaded.matrix).reduce(function (acc, key) {
      acc[key] = loaded.matrix[key].length;
      return acc;
    }, {}),
    rows
  };

  ensureDir(options.outputDir);
  fs.writeFileSync(path.join(options.outputDir, "lab-results.json"), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(options.outputDir, "lab-results.csv"), writeCsv(rows));
  fs.writeFileSync(path.join(options.outputDir, "lab-summary.json"), JSON.stringify(summary, null, 2));
  fs.writeFileSync(path.join(options.outputDir, "lab-next-actions.json"), JSON.stringify(actions, null, 2));

  process.stdout.write(JSON.stringify({
    testsRun: rows.length,
    candlesLoaded: result.candlesLoaded,
    viableRows: summary.viableRowsCount,
    promisingFamilies: summary.promisingFamilies,
    rejectedFamilies: summary.rejectedFamilies,
    bestRawRow: summary.bestRawRow,
    bestRobustCandidate: summary.optimizationJustified ? summary.bestStrategyFamily : null,
    nextRecommendedAction: actions.recommendedNextExperiment,
    stagedOptimizationJustified: actions.optimizationJustified
  }, null, 2));
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
