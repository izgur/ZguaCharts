const fs = require("fs");
const path = require("path");
const data = require("../data");
const backtest = require("../backtest");
const reporting = require("../reporting");
const tradeAudit = require("../backtest/tradeAudit");

function optimize(options) {
  options = options || {};
  var ranges = options.ranges || defaultRanges();
  var combos = expandGrid(ranges).filter(validCombo);
  return data.fetchCandles({
    source: options.source || "bybit",
    symbol: options.symbol,
    interval: options.interval,
    from: options.from,
    to: options.to,
    limit: options.limit
  }).then(function (candles) {
    var split = splitCandles(candles, options.trainRatio || 0.7);
    var rows = combos.map(function (params) {
      var train = backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: params,
        candles: split.train
      });
      var test = backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: params,
        candles: split.test
      });
      return buildOptimizationRow(options, params, train, test);
    });
    if (rows.length && rows.every(function (row) { return row.train.trades === 0 && row.test.trades === 0; })) {
      throw new Error("Optimizer stopped: every tested combination produced 0 trades. Run AlwaysLongTest or backtest --debug before optimizing this strategy.");
    }
    var validRows = rows.filter(validCandidate);
    var ranked = rankResults(validRows.length ? validRows : rows);
    var summary = buildSummary(ranked, rows, validRows);
    summary.walkForward = ranked[0]
      ? walkForward(candles, options, ranked[0].params, options.walkForwardFolds || 3)
      : [];
    if (options.outputDir) reporting.writeOptimizationReport(options.outputDir, ranked, summary, options.reportPrefix);
    return {
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      trainRatio: options.trainRatio || 0.7,
      combinations: combos.length,
      totalResults: rows.length,
      validCandidates: validRows.length,
      optimizedPerformance: ranked[0] || null,
      unseenTestPerformance: ranked[0] ? ranked[0].test : null,
      summary: summary,
      results: ranked
    };
  });
}

function optimizeStaged(options) {
  options = options || {};
  options.strategy = options.strategy || "ConservativeTrendLoose";
  options.outputDir = options.outputDir || "reports";
  options.progressEvery = Number(options.progressEvery || 50);
  options.maxCombos = Number(options.maxCombos || 1000);

  return Promise.resolve(data.fetchCandles({
    source: options.source || "bybit",
    symbol: options.symbol,
    interval: options.interval,
    from: options.from,
    to: options.to,
    limit: options.limit
  })).then(function (candles) {
    var stage1Combos = expandGrid(stage1Ranges()).map(normalizeParamAliases).filter(validCombo);
    var stage1 = evaluateCombos("stage1", stage1Combos, candles, options);
    writeResultSet(options.outputDir, "loose-stage1-results", stage1);

    var stage2Combos = buildStage2Combos(stage1.ranked.slice(0, 10), options.maxCombos);
    var stage2 = evaluateCombos("stage2", stage2Combos, candles, options);
    writeResultSet(options.outputDir, "loose-stage2-results", stage2);

    var top5 = stage2.ranked.slice(0, 5);
    var stage3 = validateTopCandidates(top5, candles, options);
    writeJson(options.outputDir, "loose-stage3-validation.json", stage3);

    var summary = buildStagedSummary(candles, stage1, stage2, stage3);
    writeJson(options.outputDir, "loose-ranked-summary.json", summary);

    return summary;
  });
}

function stage1Ranges() {
  return {
    emaFast: [8, 12, 20],
    emaSlow: [50, 100, 200],
    rsiMin: [35, 45],
    rsiMax: [65, 75],
    minHoldBars: [3, 12],
    cooldownBars: [0, 6],
    useBreakout: [true, false],
    useVolumeFilter: [false]
  };
}

function evaluateCombos(stageName, combos, candles, options) {
  var split = splitCandles(candles, options.trainRatio || 0.7);
  var started = Date.now();
  var bestScore = -Infinity;
  var rows = [];
  combos.forEach(function (params, index) {
    var normalized = normalizeParamAliases(params);
    var train = backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: normalized,
      candles: split.train
    });
    var test = backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: normalized,
      candles: split.test
    });
    var row = buildOptimizationRow(options, normalized, train, test);
    rows.push(row);
    bestScore = Math.max(bestScore, row.score);
    if (options.progressEvery && ((index + 1) % options.progressEvery === 0 || index === combos.length - 1)) {
      printProgress(stageName, index + 1, combos.length, normalized, bestScore, rows.filter(validCandidate).length, started);
    }
  });
  var validRows = rows.filter(validCandidate);
  var ranked = rankResults(validRows.length ? validRows : rows.slice());
  return {
    stage: stageName,
    tested: rows.length,
    validCandidates: validRows.length,
    ranked: ranked,
    allResults: rows
  };
}

function printProgress(stageName, tested, total, params, bestScore, validCount, started) {
  var elapsed = round((Date.now() - started) / 1000, 1);
  process.stderr.write(JSON.stringify({
    stage: stageName,
    currentCombo: params,
    tested: tested,
    total: total,
    bestScoreSoFar: round(bestScore, 6),
    validCandidatesSoFar: validCount,
    elapsedSeconds: elapsed
  }) + "\n");
}

function buildStage2Combos(topRows, maxCombos) {
  var byKey = {};
  topRows.forEach(function (row) {
    nearbyCombos(row.params).forEach(function (combo) {
      combo = normalizeParamAliases(combo);
      if (!validCombo(combo)) return;
      byKey[stableParamKey(combo)] = combo;
    });
  });
  return Object.keys(byKey).map(function (key) { return byKey[key]; }).slice(0, maxCombos);
}

function nearbyCombos(params) {
  var ranges = {
    emaFast: uniqueNumbers([params.emaFast - 4, params.emaFast - 2, params.emaFast, params.emaFast + 2, params.emaFast + 4], 2, 80),
    emaSlow: uniqueNumbers([params.emaSlow - 50, params.emaSlow - 20, params.emaSlow, params.emaSlow + 20, params.emaSlow + 50], 10, 300),
    rsiMin: uniqueNumbers([params.rsiMin - 5, params.rsiMin, params.rsiMin + 5], 10, 70),
    rsiMax: uniqueNumbers([params.rsiMax - 5, params.rsiMax, params.rsiMax + 5], 35, 90),
    minHoldBars: uniqueNumbers([params.minHoldBars - 3, params.minHoldBars, params.minHoldBars + 3], 0, 48),
    cooldownBars: uniqueNumbers([params.cooldownBars - 3, params.cooldownBars, params.cooldownBars + 3], 0, 48),
    requireBreakout: [params.requireBreakout === true],
    requireVolume: [false]
  };
  return expandGrid(ranges);
}

function uniqueNumbers(values, min, max) {
  var seen = {};
  return values.map(function (value) {
    return Math.round(Number(value));
  }).filter(function (value) {
    if (!Number.isFinite(value) || value < min || value > max || seen[value]) return false;
    seen[value] = true;
    return true;
  });
}

function validateTopCandidates(topRows, candles, options) {
  var split = splitCandles(candles, options.trainRatio || 0.7);
  return topRows.map(function (row, index) {
    var full = backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: row.params,
      candles: candles
    });
    var audit = tradeAudit.auditTrades(full, candles);
    var train = metrics(backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: row.params,
      candles: split.train
    }));
    var test = metrics(backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: row.params,
      candles: split.test
    }));
    return {
      rank: index + 1,
      params: row.params,
      score: robustnessScore(train, test),
      valid: validCandidate({ train: train, test: test }),
      train: train,
      test: test,
      full: metrics(full),
      walkForward: walkForward(candles, options, row.params, options.walkForwardFolds || 3),
      tradeAudit: audit
    };
  });
}

function buildStagedSummary(candles, stage1, stage2, stage3) {
  var top5 = stage3.slice(0, 5);
  var valid = stage2.allResults.filter(validCandidate);
  return {
    candlesUsed: candles.length,
    candleRange: {
      first: candles.length ? candles[0].time : null,
      last: candles.length ? candles[candles.length - 1].time : null
    },
    combinationsTested: {
      stage1: stage1.tested,
      stage2: stage2.tested,
      stage3: stage3.length
    },
    validCandidates: valid.length,
    top5: top5,
    bestTestResult: top5.length ? top5.slice().sort(function (a, b) {
      return b.test.totalReturn - a.test.totalReturn;
    })[0] : null,
    robustnessAssessment: assessRobustness(top5, valid.length)
  };
}

function assessRobustness(top5, validCount) {
  if (!validCount) return "No candidates survived the basic out-of-sample filters. Current evidence looks overfit or not robust.";
  if (top5.some(function (row) { return !row.tradeAudit.ok; })) return "Some top candidates failed trade audit; do not trust this batch yet.";
  if (top5.some(function (row) { return row.full.totalReturn <= 0 || row.full.profitFactor <= 1; })) {
    return "Top candidates pass the final 30% test split, but fail on full-history profitability. This looks regime-dependent and likely overfit.";
  }
  if (top5.some(function (row) {
    return row.walkForward.some(function (fold) { return fold.test.totalReturn <= 0 || fold.test.profitFactor <= 1; });
  })) {
    return "Top candidates are inconsistent across walk-forward folds. Treat this as overfit/unstable, not robust.";
  }
  if (top5.every(function (row) { return row.test.trades < 30; })) return "Some candidates passed, but trade counts are still thin. Treat as fragile, not proven.";
  return "Candidates survived the basic filters, but this is still preliminary research, not proof of profitability.";
}

function normalizeParamAliases(params) {
  var copy = Object.assign({}, params);
  if (copy.useBreakout !== undefined && copy.requireBreakout === undefined) copy.requireBreakout = copy.useBreakout;
  if (copy.useVolumeFilter !== undefined && copy.requireVolume === undefined) copy.requireVolume = copy.useVolumeFilter;
  delete copy.useBreakout;
  delete copy.useVolumeFilter;
  return copy;
}

function stableParamKey(params) {
  return JSON.stringify(Object.keys(params).sort().reduce(function (out, key) {
    out[key] = params[key];
    return out;
  }, {}));
}

function writeResultSet(outputDir, basename, resultSet) {
  writeJson(outputDir, basename + ".json", {
    stage: resultSet.stage,
    tested: resultSet.tested,
    validCandidates: resultSet.validCandidates,
    results: resultSet.ranked
  });
  ensureDir(outputDir);
  fs.writeFileSync(path.join(outputDir, basename + ".csv"), reporting.toCsv(resultSet.ranked));
}

function writeJson(outputDir, filename, payload) {
  ensureDir(outputDir);
  fs.writeFileSync(path.join(outputDir, filename), JSON.stringify(payload, null, 2));
}

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir);
}

function defaultRanges() {
  return {
    emaFast: [10, 20, 30],
    emaSlow: [50, 100, 200],
    rsiMin: [45, 50, 55],
    rsiMax: [60, 68]
  };
}

function expandGrid(ranges) {
  var keys = Object.keys(ranges);
  var combos = [{}];
  keys.forEach(function (key) {
    var values = Array.isArray(ranges[key]) ? ranges[key] : [ranges[key]];
    var next = [];
    combos.forEach(function (combo) {
      values.forEach(function (value) {
        var copy = Object.assign({}, combo);
        copy[key] = value;
        next.push(copy);
      });
    });
    combos = next;
  });
  return combos;
}

function validCombo(params) {
  if (params.emaFast !== undefined && params.emaSlow !== undefined && Number(params.emaFast) >= Number(params.emaSlow)) {
    return false;
  }
  if (params.rsiMin !== undefined && params.rsiMax !== undefined && Number(params.rsiMin) >= Number(params.rsiMax)) {
    return false;
  }
  return true;
}

function splitCandles(candles, trainRatio) {
  var index = Math.max(1, Math.floor(candles.length * trainRatio));
  return {
    train: candles.slice(0, index),
    test: candles.slice(index)
  };
}

function walkForward(candles, options, params, folds) {
  var results = [];
  var foldSize = Math.floor(candles.length / (folds + 1));
  if (foldSize < 20) return results;
  for (var fold = 0; fold < folds; fold += 1) {
    var trainEnd = foldSize * (fold + 1);
    var testEnd = Math.min(candles.length, trainEnd + foldSize);
    var train = candles.slice(0, trainEnd);
    var test = candles.slice(trainEnd, testEnd);
    if (!test.length) continue;
    results.push({
      fold: fold + 1,
      trainCandles: train.length,
      testCandles: test.length,
      train: metrics(backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: params,
        candles: train
      })),
      test: metrics(backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: params,
        candles: test
      }))
    });
  }
  return results;
}

function buildOptimizationRow(options, params, train, test) {
  var trainMetrics = metrics(train);
  var testMetrics = metrics(test);
  return {
    symbol: options.symbol,
    interval: options.interval,
    strategy: options.strategy,
    params: params,
    totalReturn: train.totalReturn,
    maxDrawdown: train.maxDrawdown,
    profitFactor: train.profitFactor,
    winRate: train.winRate,
    trades: train.trades,
    sharpeRatio: train.sharpeRatio,
    drawdownAdjustedReturn: drawdownAdjustedReturn(train),
    train: trainMetrics,
    test: testMetrics,
    score: robustnessScore(trainMetrics, testMetrics),
    valid: validCandidate({ train: trainMetrics, test: testMetrics })
  };
}

function metrics(result) {
  return {
    totalReturn: result.totalReturn,
    maxDrawdown: result.maxDrawdown,
    profitFactor: result.profitFactor,
    winRate: result.winRate,
    trades: result.trades,
    sharpeRatio: result.sharpeRatio,
    avgBarsHeld: result.avgBarsHeld
  };
}

function drawdownAdjustedReturn(result) {
  return result.maxDrawdown ? result.totalReturn / result.maxDrawdown : result.totalReturn;
}

function robustnessScore(train, test) {
  return round(
    test.profitFactor * 2 +
    test.sharpeRatio +
    test.totalReturn / 10 -
    test.maxDrawdown / 5 -
    Math.abs(train.totalReturn - test.totalReturn) / 10,
    6
  );
}

function validCandidate(row) {
  return row.train.trades >= 40 &&
    row.test.trades >= 20 &&
    row.test.profitFactor > 1.1 &&
    row.test.maxDrawdown < 15 &&
    row.test.totalReturn > 0 &&
    Math.abs(row.train.totalReturn - row.test.totalReturn) <= 15;
}

function rankResults(rows) {
  return rows.sort(function (a, b) {
    return (b.score - a.score) ||
      (b.test.profitFactor - a.test.profitFactor) ||
      (b.test.totalReturn - a.test.totalReturn) ||
      (b.drawdownAdjustedReturn - a.drawdownAdjustedReturn);
  });
}

function buildSummary(rows, allRows, validRows) {
  return {
    totalResults: allRows ? allRows.length : rows.length,
    validCandidates: validRows ? validRows.length : rows.length,
    bestByProfitFactor: best(rows, "profitFactor"),
    bestBySharpeRatio: best(rows, "sharpeRatio"),
    bestByDrawdownAdjustedReturn: best(rows, "drawdownAdjustedReturn"),
    bestByRobustnessScore: best(rows, "score"),
    warning: validRows && validRows.length && validRows.every(function (row) {
      return row.train.trades < 30 || row.test.trades < 15;
    }) ? "All valid candidates have relatively few trades; treat results as statistically fragile." : null
  };
}

function best(rows, key) {
  if (!rows.length) return null;
  return rows.slice().sort(function (a, b) { return b[key] - a[key]; })[0];
}

function parseRanges(jsonOrPath) {
  if (!jsonOrPath) return defaultRanges();
  var value = String(jsonOrPath);
  var raw = value.charAt(0) === "{" ? value : fs.readFileSync(path.resolve(value), "utf8");
  return JSON.parse(raw);
}

function round(value, digits) {
  var factor = Math.pow(10, digits || 4);
  return Math.round((Number(value) || 0) * factor) / factor;
}

module.exports = {
  optimize: optimize,
  optimizeStaged: optimizeStaged,
  defaultRanges: defaultRanges,
  expandGrid: expandGrid,
  splitCandles: splitCandles,
  walkForward: walkForward,
  parseRanges: parseRanges,
  validCombo: validCombo,
  validCandidate: validCandidate,
  robustnessScore: robustnessScore
};
