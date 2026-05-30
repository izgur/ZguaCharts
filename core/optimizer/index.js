const fs = require("fs");
const path = require("path");
const data = require("../data");
const backtest = require("../backtest");
const reporting = require("../reporting");
const tradeAudit = require("../backtest/tradeAudit");

const OPTIMIZER_QUALITY_POLICY = {
  minTestTrades: 10,
  minFullTrades: 20,
  minProfitFactor: 1.05,
  minTestReturnPct: 0,
  maxDrawdownPct: 25,
  maxTrainTestReturnGapPct: 25,
  maxNegativeWalkForwardFoldRatio: 0.5
};

const REJECTION_LABELS = {
  zero_trades: "Zero trades",
  too_few_test_trades: "Too few test trades",
  too_few_full_trades: "Too few full-period trades",
  test_profit_factor_below_min: "Test profit factor below minimum",
  full_profit_factor_below_min: "Full-period profit factor below minimum",
  negative_test_return: "Negative test return",
  high_drawdown: "High drawdown",
  train_test_overfit_gap: "Train/test overfit gap",
  unstable_walk_forward: "Unstable walk-forward",
  missing_metrics: "Missing metrics",
  invalid_candidate: "Invalid candidate",
  zero_trade_diagnostics: "Zero-trade diagnostics available",
  train_only_success_test_failure: "Train-only success but test failed"
};

function optimize(options) {
  options = options || {};
  var grid = selectOptimizerGrid(options.strategy, options.ranges, Number(options.maxCombos || 1000));
  var combos = grid.combos;
  return data.fetchCandles({
    source: options.source || "bybit",
    symbol: options.symbol,
    interval: options.interval,
    from: options.from,
    to: options.to,
    limit: options.limit
  }).then(function (candles) {
    var split = splitCandles(candles, options.trainRatio || 0.7);
    var rows = evaluateOptimizerCombos(combos, candles, options);
    rows = rows.map(function (row) { return ensureCandidateQuality(row); });
    var allZero = allRowsZeroTrade(rows);
    var qualitySummary = buildQualitySummary(rows);
    var fallbackReason = tradeDiscoveryFallbackReason(rows, allZero, qualitySummary);
    if (fallbackReason && !grid.metadata.fallbackUsed) {
      var fallbackGrid = selectOptimizerGrid(options.strategy, null, Math.min(Number(options.maxCombos || 1000), 60), true, fallbackReason);
      if (fallbackGrid.combos.length) {
        var fallbackRows = evaluateOptimizerCombos(fallbackGrid.combos, candles, options).map(function (row) { return ensureCandidateQuality(row); });
        rows = fallbackRows.length ? fallbackRows : rows;
        grid = fallbackGrid;
        combos = fallbackGrid.combos;
        allZero = allRowsZeroTrade(rows);
        if (allZero) grid.metadata.fallbackReason = "Initial and fallback grids both produced zero trades.";
      }
    }
    var zeroTradeSummary = aggregateZeroTradeSummary(rows);
    qualitySummary = buildQualitySummary(rows);
    var gridAudit = buildGridAudit(options.strategy, options.interval, grid.metadata, rows, zeroTradeSummary, qualitySummary);
    var acceptableRows = acceptableOptimizerRows(rows);
    var ranked = rankResults(acceptableRows);
    var rejectedRanked = rankResults(rows.filter(function (row) { return row.qualityStatus === "FAIL"; })).slice(0, 20);
    var selected = ranked[0] || null;
    var summary = buildSummary(ranked, rows, acceptableRows);
    summary.walkForward = selected && !allZero
      ? walkForward(candles, options, selected.params, options.walkForwardFolds || 3)
      : [];
    summary.qualitySummary = qualitySummary;
    summary.gridAudit = gridAudit;
    summary.warnings = optimizerRunWarnings(grid.metadata, zeroTradeSummary, allZero, qualitySummary);
    if (options.outputDir) reporting.writeOptimizationReport(options.outputDir, ranked, summary, options.reportPrefix);
    return {
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      trainRatio: options.trainRatio || 0.7,
      combinations: combos.length,
      totalResults: rows.length,
      validCandidates: acceptableRows.length,
      optimizedPerformance: selected,
      unseenTestPerformance: selected ? selected.test : null,
      optimizerGrid: Object.assign({}, grid.metadata, {
        candidateCountTested: rows.length
      }),
      qualityPolicy: optimizerQualityPolicy(),
      qualitySummary: qualitySummary,
      gridAudit: gridAudit,
      zeroTradeSummary: zeroTradeSummary,
      allZeroTradeCandidates: allZero,
      warnings: summary.warnings,
      summary: summary,
      results: ranked,
      rejectedCandidates: rejectedRanked
    };
  });
}

function evaluateOptimizerCombos(combos, candles, options) {
  var split = splitCandles(candles, options.trainRatio || 0.7);
  return combos.map(function (params) {
      var train = backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: paramsWithCosts(options, params),
        candles: split.train,
        debug: true
      });
      var test = backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: paramsWithCosts(options, params),
        candles: split.test,
        debug: true
      });
      return buildOptimizationRow(options, params, train, test);
    });
}

function optimizeStaged(options) {
  options = options || {};
  if (options.strategy === "RegimeFilteredTrendStrategy") return optimizeRegimeStaged(options);
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

function optimizeRegimeStaged(options) {
  options = options || {};
  options.strategy = "RegimeFilteredTrendStrategy";
  options.outputDir = options.outputDir || "reports";
  options.progressEvery = Number(options.progressEvery || 50);
  options.maxCombos = Number(options.maxCombos || 1000);
  return loadCandlesWithRegime(options).then(function (loaded) {
    var stage1Combos = expandGrid(regimeStage1Ranges()).filter(validRegimeCombo).slice(0, 500);
    var stage1 = evaluateRegimeCombos("stage1", stage1Combos, loaded, options);
    writeResultSet(options.outputDir, "regime-stage1-results", stage1);

    var stage2Combos = buildRegimeStage2Combos(stage1.ranked.slice(0, 10), options.maxCombos);
    var stage2 = evaluateRegimeCombos("stage2", stage2Combos, loaded, options);
    writeResultSet(options.outputDir, "regime-stage2-results", stage2);

    var top5 = stage2.ranked.slice(0, 5);
    var stage3 = validateRegimeTopCandidates(top5, loaded, options);
    writeJson(options.outputDir, "regime-stage3-validation.json", stage3);

    var summary = buildRegimeSummary(loaded.candles, stage1, stage2, stage3);
    writeJson(options.outputDir, "regime-ranked-summary.json", summary);
    return summary;
  });
}

function loadCandlesWithRegime(options) {
  if (options.candles && options.regimeCandles) {
    return Promise.resolve({
      candles: data.normalizeCandles(options.candles),
      regimeCandles: data.normalizeCandles(options.regimeCandles)
    });
  }
  return Promise.resolve(data.fetchCandles({
    source: options.source || "bybit",
    symbol: options.symbol,
    interval: options.interval,
    from: options.from,
    to: options.to,
    limit: options.limit
  })).then(function (candles) {
    return data.fetchCandles({
      source: options.source || "bybit",
      symbol: "BTCUSDT",
      interval: "4h",
      from: options.from,
      to: options.to,
      limit: Math.ceil((options.limit || candles.length) / 4) + 250
    }).then(function (regimeCandles) {
      return { candles: candles, regimeCandles: regimeCandles };
    });
  });
}

function regimeStage1Ranges() {
  return {
    donchianEntry: [20, 55, 100],
    donchianExit: [10, 20, 55],
    adxThreshold: [14, 18, 22, 26],
    atrMultiplier: [2.0, 2.5, 3.0, 3.5],
    emaTrendLength: [100, 150, 200],
    volumeFilter: [true, false],
    shortMode: [false]
  };
}

function evaluateRegimeCombos(stageName, combos, loaded, options) {
  var split = splitCandles(loaded.candles, options.trainRatio || 0.7);
  var regimeSplit = splitCandles(loaded.regimeCandles, options.trainRatio || 0.7);
  var started = Date.now();
  var bestScore = -Infinity;
  var rows = [];
  combos.forEach(function (params, index) {
    var train = backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, params),
      candles: split.train,
      regimeCandles: regimeSplit.train
    });
    var test = backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, params),
      candles: split.test,
      regimeCandles: regimeSplit.test
    });
    var row = buildRegimeOptimizationRow(options, params, train, test);
    rows.push(row);
    bestScore = Math.max(bestScore, row.score);
    if (options.progressEvery && ((index + 1) % options.progressEvery === 0 || index === combos.length - 1)) {
      printProgress(stageName, index + 1, combos.length, params, bestScore, rows.filter(validRegimeCandidate).length, started);
    }
  });
  var validRows = rows.filter(validRegimeCandidate);
  return {
    stage: stageName,
    tested: rows.length,
    validCandidates: validRows.length,
    ranked: rankResults(validRows.length ? validRows : rows.slice()),
    allResults: rows
  };
}

function buildRegimeStage2Combos(topRows, maxCombos) {
  var byKey = {};
  topRows.forEach(function (row) {
    var p = row.params;
    var ranges = {
      donchianEntry: uniqueNumbers([p.donchianEntry - 15, p.donchianEntry, p.donchianEntry + 15], 10, 120),
      donchianExit: uniqueNumbers([p.donchianExit - 10, p.donchianExit, p.donchianExit + 10], 5, 80),
      adxThreshold: uniqueNumbers([p.adxThreshold - 4, p.adxThreshold, p.adxThreshold + 4], 8, 40),
      atrMultiplier: uniqueValues([p.atrMultiplier - 0.5, p.atrMultiplier, p.atrMultiplier + 0.5], 1, 6),
      emaTrendLength: uniqueNumbers([p.emaTrendLength - 50, p.emaTrendLength, p.emaTrendLength + 50], 50, 250),
      volumeFilter: [p.volumeFilter === true],
      shortMode: [false]
    };
    expandGrid(ranges).forEach(function (combo) {
      if (!validRegimeCombo(combo)) return;
      byKey[stableParamKey(combo)] = combo;
    });
  });
  return Object.keys(byKey).map(function (key) { return byKey[key]; }).slice(0, maxCombos);
}

function validateRegimeTopCandidates(topRows, loaded, options) {
  var split = splitCandles(loaded.candles, options.trainRatio || 0.7);
  var regimeSplit = splitCandles(loaded.regimeCandles, options.trainRatio || 0.7);
  return topRows.map(function (row, index) {
    var full = backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, row.params),
      candles: loaded.candles,
      regimeCandles: loaded.regimeCandles
    });
    var audit = tradeAudit.auditTrades(full, loaded.candles);
    var train = metrics(backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, row.params),
      candles: split.train,
      regimeCandles: regimeSplit.train
    }));
    var test = metrics(backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, row.params),
      candles: split.test,
      regimeCandles: regimeSplit.test
    }));
    return {
      rank: index + 1,
      params: paramsWithCosts(options, row.params),
      score: robustnessScore(train, test),
      valid: validRegimeCandidate({ train: train, test: test, full: metrics(full), walkForward: [] }),
      train: train,
      test: test,
      full: metrics(full),
      diagnostics: full.diagnostics,
      walkForward: walkForwardRegime(loaded, options, row.params, options.walkForwardFolds || 3),
      tradeAudit: audit
    };
  });
}

function buildRegimeSummary(candles, stage1, stage2, stage3) {
  var top5 = stage3.slice(0, 5);
  var valid = stage2.allResults.filter(validRegimeCandidate);
  return {
    candlesUsed: candles.length,
    candleRange: {
      first: candles.length ? candles[0].time : null,
      last: candles.length ? candles[candles.length - 1].time : null
    },
    combinationsTested: { stage1: stage1.tested, stage2: stage2.tested, stage3: stage3.length },
    validCandidates: valid.length,
    top5: top5,
    bestTestResult: top5.length ? top5.slice().sort(function (a, b) { return b.test.totalReturn - a.test.totalReturn; })[0] : null,
    robustnessAssessment: assessRegimeRobustness(top5, valid.length)
  };
}

function buildRegimeOptimizationRow(options, params, train, test) {
  var trainMetrics = metrics(train);
  var testMetrics = metrics(test);
  return {
    symbol: options.symbol,
    interval: options.interval,
    strategy: options.strategy,
    params: paramsWithCosts(options, params),
    totalReturn: train.totalReturn,
    maxDrawdown: train.maxDrawdown,
    profitFactor: train.profitFactor,
    winRate: train.winRate,
    trades: train.trades,
    sharpeRatio: train.sharpeRatio,
    train: trainMetrics,
    test: testMetrics,
    score: robustnessScore(trainMetrics, testMetrics),
    valid: validRegimeCandidate({ train: trainMetrics, test: testMetrics })
  };
}

function validRegimeCombo(params) {
  return Number(params.donchianExit) < Number(params.donchianEntry);
}

function validRegimeCandidate(row) {
  var fullOk = !row.full || (row.full.profitFactor > 1.05);
  var walkOk = !row.walkForward || row.walkForward.filter(function (fold) {
    return fold.test.totalReturn <= 0 || fold.test.profitFactor <= 1;
  }).length < Math.ceil(row.walkForward.length / 2);
  return row.train.trades >= 40 &&
    row.test.trades >= 20 &&
    row.test.totalReturn > 0 &&
    row.test.profitFactor > 1.15 &&
    row.test.maxDrawdown < 20 &&
    fullOk &&
    walkOk;
}

function walkForwardRegime(loaded, options, params, folds) {
  var results = [];
  var foldSize = Math.floor(loaded.candles.length / (folds + 1));
  if (foldSize < 50) return results;
  for (var fold = 0; fold < folds; fold += 1) {
    var trainEnd = foldSize * (fold + 1);
    var testEnd = Math.min(loaded.candles.length, trainEnd + foldSize);
    var train = loaded.candles.slice(0, trainEnd);
    var test = loaded.candles.slice(trainEnd, testEnd);
    var trainStartTime = train.length ? train[0].time : 0;
    var testStartTime = test.length ? test[0].time : 0;
    results.push({
      fold: fold + 1,
      trainCandles: train.length,
      testCandles: test.length,
      train: metrics(backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: paramsWithCosts(options, params),
        candles: train,
        regimeCandles: loaded.regimeCandles.filter(function (c) { return c.time <= train[train.length - 1].time && c.time >= trainStartTime - 86400; })
      })),
      test: metrics(backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: paramsWithCosts(options, params),
        candles: test,
        regimeCandles: loaded.regimeCandles.filter(function (c) { return c.time <= test[test.length - 1].time && c.time >= testStartTime - 86400; })
      }))
    });
  }
  return results;
}

function assessRegimeRobustness(top5, validCount) {
  if (!validCount) return "No RegimeFilteredTrendStrategy candidates survived the out-of-sample filters.";
  if (top5.some(function (row) { return !row.tradeAudit.ok; })) return "A top regime candidate failed trade audit; do not trust this run.";
  if (top5.some(function (row) { return row.full.profitFactor <= 1.05 || row.full.totalReturn <= 0; })) {
    return "Top regime candidates still fail full-history robustness. Not robust.";
  }
  if (top5.some(function (row) {
    return row.walkForward.filter(function (fold) { return fold.test.totalReturn <= 0 || fold.test.profitFactor <= 1; }).length >= 2;
  })) return "Top regime candidates are weak across walk-forward folds. Treat as overfit/unstable.";
  return "Regime candidates survived basic filters, but this is research only, not proof of profitability.";
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
      params: paramsWithCosts(options, normalized),
      candles: split.train
    });
    var test = backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, normalized),
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

function uniqueValues(values, min, max) {
  var seen = {};
  return values.map(function (value) {
    return Number(Number(value).toFixed(4));
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
      params: paramsWithCosts(options, row.params),
      candles: candles
    });
    var audit = tradeAudit.auditTrades(full, candles);
    var train = metrics(backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, row.params),
      candles: split.train
    }));
    var test = metrics(backtest.runBacktestOnCandles({
      symbol: options.symbol,
      interval: options.interval,
      strategy: options.strategy,
      params: paramsWithCosts(options, row.params),
      candles: split.test
    }));
    return {
      rank: index + 1,
      params: paramsWithCosts(options, row.params),
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

function optimizerGridCatalog() {
  var trendGrid = {
    strategyKey: "trend",
    gridName: "Trend-following",
    humanName: "Trend-following",
    params: {
      emaFast: [8, 12, 20],
      emaMomentumSlow: [21],
      emaTrendFast: [50],
      emaSlow: [80, 100, 150, 200],
      rsiMin: [35, 38, 45, 50],
      rsiMax: [65, 70, 78, 82],
      stopAtr: [2, 2.5, 3],
      takeProfitAtr: [3, 4],
      trailingAtr: [1.5, 2, 2.5],
      minHoldBars: [0, 2, 4, 8],
      cooldownBars: [0, 2, 3, 6],
      requireVolume: [false],
      requireBreakout: [false, true]
    },
    maxCombinations: 500,
    notes: ["Uses existing trend/risk parameters with wider RSI and optional breakout to reduce zero-trade scans."],
    riskLevel: "medium"
  };
  var v2TrendGrid = {
    strategyKey: "v2_trend",
    gridName: "V2 ATR trend",
    humanName: "V2 ATR trend",
    params: {
      regimeMode: ["looseBtcBull", "symbolTrend", "symbolFastTrend"],
      useRsiFilter: [true, false],
      atrMultiplier: [1.8, 2.3, 2.8, 3.3],
      emaFast: [8, 10, 20, 30],
      emaSlow: [40, 50, 80, 110],
      emaTrend: [80, 100, 150, 200],
      rsiMin: [35, 40, 45, 48],
      rsiMax: [68, 72, 76, 82],
      cooldownBars: [0, 2, 3, 6],
      minHoldBars: [0, 1, 3, 6],
      volumeFilter: [false]
    },
    maxCombinations: 500,
    notes: ["Matches existing SimpleAtrTrendV2-style parameters and avoids strict BTC-only gating by testing symbol trend modes."],
    riskLevel: "medium"
  };
  var breakoutGrid = {
    strategyKey: "breakout",
    gridName: "Breakout/retest",
    humanName: "Breakout/retest",
    params: {
      regimeMode: ["looseBtcBull", "symbolTrend", "symbolFastTrend"],
      donchianEntry: [10, 20, 55],
      donchianExit: [5, 10, 20],
      adxThreshold: [0, 10, 14, 18],
      atrMultiplier: [1.8, 2.3, 2.8, 3.3],
      emaTrendLength: [50, 100, 150, 200],
      retestLookback: [3, 5, 8, 12],
      retestAtr: [0.5, 0.8, 1.2, 1.6],
      volumeFilter: [false],
      shortMode: [false]
    },
    maxCombinations: 500,
    notes: ["Tests existing Donchian/ADX/retest parameters with volume optional to avoid over-filtering."],
    riskLevel: "medium-high"
  };
  var meanReversionGrid = {
    strategyKey: "mean_reversion",
    gridName: "Mean reversion",
    humanName: "Mean reversion",
    params: {
      emaSlow: [100, 150, 200],
      rsiLimit: [25, 30, 32, 35, 40],
      rsiOversold: [28, 32, 35, 38],
      stopAtr: [1.2, 1.5, 2],
      takeProfitAtr: [1.5, 2, 2.5],
      minHoldBars: [1, 2, 4],
      cooldownBars: [0, 3, 6],
      volumeFilter: [false]
    },
    maxCombinations: 250,
    notes: ["Uses existing RSI/ATR mean-reversion parameters only; no new rules are introduced."],
    riskLevel: "medium"
  };
  var pullbackGrid = {
    strategyKey: "pullback",
    gridName: "Pullback/reclaim",
    humanName: "Pullback/reclaim",
    params: {
      regimeMode: ["looseBtcBull", "symbolTrend", "symbolFastTrend"],
      rsiPullbackLevel: [35, 38, 42, 45, 48],
      rsiReclaimLevel: [45, 48, 50, 53],
      rsiMin: [32, 35, 38, 42],
      rsiMax: [55, 60, 65, 70],
      atrMultiplier: [1.8, 2.3, 2.8, 3.3],
      stopAtr: [1.8, 2, 2.5],
      takeProfitAtr: [2.5, 3, 4],
      trailingAtr: [1.5, 2, 2.5],
      cooldownBars: [0, 2, 3, 6],
      minHoldBars: [0, 1, 3, 6],
      volumeFilter: [false]
    },
    maxCombinations: 500,
    notes: ["Wider pullback/reclaim thresholds for existing pullback strategies."],
    riskLevel: "medium"
  };
  var oscillatorGrid = {
    strategyKey: "oscillator",
    gridName: "Oscillator-style",
    humanName: "Oscillator-style",
    params: {
      rsiMin: [40, 45, 50],
      rsiMax: [65, 70, 75],
      rsiLimit: [28, 32, 36],
      rsiOversold: [28, 32, 36],
      atrMultiplier: [2, 2.5, 3],
      stopAtr: [1.5, 2, 2.5],
      takeProfitAtr: [2, 3],
      useRsiFilter: [true, false],
      cooldownBars: [0, 3],
      minHoldBars: [1, 3]
    },
    maxCombinations: 300,
    notes: ["There is no dedicated Cipher B strategy yet; this grid only covers existing RSI/oscillator-like parameters."],
    riskLevel: "medium"
  };
  var fallbackGrid = {
    strategyKey: "default_fallback",
    gridName: "Default fallback",
    humanName: "Default fallback",
    params: defaultRanges(),
    maxCombinations: 150,
    notes: ["Small generic grid for unknown strategies. It may be ignored by strategies that do not use these parameters."],
    riskLevel: "low"
  };
  return {
    default_fallback: fallbackGrid,
    ConservativeTrend: trendGrid,
    ConservativeTrendLoose: Object.assign({}, trendGrid, { strategyKey: "trend_loose", gridName: "Loose trend-following" }),
    MomentumScalping: Object.assign({}, trendGrid, {
      strategyKey: "momentum",
      gridName: "Momentum scalping",
      params: Object.assign({}, trendGrid.params, {
        scoreThreshold: [45, 55, 65],
        stopAtr: [1.5, 1.8, 2.2],
        takeProfitAtr: [2, 2.5, 3],
        trailingActivationAtr: [0.8, 1, 1.5],
        trailingAtr: [1, 1.3, 1.8],
        minHoldBars: [1, 3, 5]
      })
    }),
    MeanReversion: meanReversionGrid,
    MeanReversionInBullRegime: meanReversionGrid,
    PullbackTrend: pullbackGrid,
    RegimePullbackTrend: pullbackGrid,
    EmaPullbackContinuation: pullbackGrid,
    PullbackReclaimV2: pullbackGrid,
    EmaBounceV2: Object.assign({}, pullbackGrid, {
      strategyKey: "ema_bounce_v2",
      gridName: "EMA bounce V2",
      params: Object.assign({}, pullbackGrid.params, { emaBounceAtr: [0.4, 0.8, 1.2] })
    }),
    RegimeFilteredTrendStrategy: breakoutGrid,
    RegimeDonchian20: breakoutGrid,
    RegimeDonchianCloseConfirm: breakoutGrid,
    TrendBreakoutRetest: breakoutGrid,
    VolatilitySqueezeBreakout: Object.assign({}, breakoutGrid, {
      strategyKey: "volatility_squeeze",
      gridName: "Volatility squeeze breakout",
      params: {
        regimeMode: ["looseBtcBull", "symbolTrend", "symbolFastTrend"],
        squeezeLookback: [50, 80, 100],
        squeezePercentile: [0.2, 0.35, 0.5, 0.65],
        rangeLookback: [10, 20, 40],
        rangeSma: [10, 20],
        closeHighPct: [0.55, 0.65, 0.75, 0.85],
        adxThreshold: [0, 10, 14, 18],
        atrMultiplier: [2, 2.5, 3],
        volumeFilter: [false]
      }
    }),
    BreakoutRetestV2: breakoutGrid,
    RangeExpansionV2: Object.assign({}, oscillatorGrid, {
      strategyKey: "range_expansion_v2",
      gridName: "Range expansion V2",
      params: {
        regimeMode: ["looseBtcBull", "symbolTrend", "symbolFastTrend", "noRegime"],
        squeezeLookback: [40, 60, 80, 120],
        squeezePercentile: [0.2, 0.35, 0.5, 0.65],
        rangeLookback: [10, 20, 40],
        rangeSma: [10, 20, 30],
        closeHighPct: [0.5, 0.6, 0.7, 0.85],
        atrMultiplier: [2, 2.5, 3],
        volumeFilter: [false]
      }
    }),
    RelativeStrengthV2: Object.assign({}, oscillatorGrid, {
      strategyKey: "relative_strength_v2",
      gridName: "Relative strength V2",
      params: {
        regimeMode: ["looseBtcBull", "symbolTrend", "symbolFastTrend", "noRegime"],
        rsLookback: [8, 12, 24, 48],
        rsThreshold: [-0.02, -0.01, 0, 0.01],
        rsiMin: [38, 42, 48, 52],
        rsiMax: [68, 74, 80, 84],
        atrMultiplier: [2, 2.5, 3],
        volumeFilter: [false]
      }
    }),
    SimpleAtrTrendV2: v2TrendGrid
  };
}

function availableOptimizerGrids() {
  var catalog = optimizerGridCatalog();
  return Object.keys(catalog).sort().map(function (key) {
    var grid = catalog[key];
    return gridMetadata(grid, grid.params, 0, 0, false, null, false);
  });
}

function optimizerGridMetadataCatalog() {
  var catalog = optimizerGridCatalog();
  var grids = availableOptimizerGrids();
  var fallbackStrategies = [
    "RegimeFilteredTrendStrategy",
    "SimpleAtrTrendV2",
    "ConservativeTrendLoose",
    "PullbackReclaimV2",
    "EmaBounceV2",
    "BreakoutRetestV2",
    "RangeExpansionV2",
    "RelativeStrengthV2"
  ];
  var fallbackGrids = fallbackStrategies.map(function (strategy) {
    var grid = fallbackGridForStrategy(strategy, catalog);
    var metadata = gridMetadata(grid, grid.params, 0, 0, false, true, "Trade-discovery fallback metadata preview.");
    metadata.strategy = strategy;
    return metadata;
  });
  return { grids: grids, tradeDiscoveryFallbacks: fallbackGrids };
}

function selectOptimizerGrid(strategy, explicitRanges, maxCombos, fallbackMode, fallbackReason) {
  var catalog = optimizerGridCatalog();
  var grid = explicitRanges
    ? {
      strategyKey: "custom",
      gridName: "Custom ranges",
      humanName: "Custom ranges",
      params: explicitRanges,
      maxCombinations: maxCombos,
      notes: ["Ranges supplied by caller."],
      riskLevel: "custom"
    }
    : (catalog[strategy] || catalog.default_fallback);
  if (fallbackMode) {
    grid = fallbackGridForStrategy(strategy, catalog);
  }
  var ranges = grid.params || defaultRanges();
  var expanded = expandGrid(ranges).map(normalizeParamAliases).filter(validCombo);
  var planned = expanded.length;
  var limit = Math.max(1, Math.min(Number(maxCombos || grid.maxCombinations || 1000), Number(grid.maxCombinations || maxCombos || 1000)));
  var sampled = stableSample(expanded, limit);
  return {
    combos: sampled,
    metadata: gridMetadata(grid, ranges, planned, sampled.length, sampled.length < planned, fallbackMode === true, fallbackReason || null)
  };
}

function fallbackGridForStrategy(strategy, catalog) {
  if (String(strategy || "").indexOf("V2") !== -1) {
    return {
      strategyKey: "trade_discovery_v2_fallback",
      gridName: "Trade-discovery V2 fallback",
      humanName: "Trade-discovery V2 fallback",
      fallbackType: "TRADE_DISCOVERY",
      params: {
        regimeMode: ["symbolTrend", "symbolFastTrend", "noRegime"],
        useRsiFilter: [false, true],
        atrMultiplier: [1.8, 2.5, 3.3],
        emaFast: [10, 20],
        emaSlow: [40, 50, 100],
        emaTrend: [80, 100, 200],
        rsiMin: [35, 45],
        rsiMax: [72, 82],
        cooldownBars: [0],
        minHoldBars: [0, 1],
        volumeFilter: [false]
      },
      maxCombinations: 60,
      notes: ["Trade-discovery fallback keeps existing formulas but relaxes existing filters. noRegime is diagnostic only."],
      warning: "Fallback grid is for diagnosing trade generation, not immediate promotion.",
      riskLevel: "diagnostic"
    };
  }
  if (String(strategy || "").toLowerCase().indexOf("mean") !== -1) return catalog.MeanReversion;
  if (["RegimeFilteredTrendStrategy", "RegimeDonchian20", "RegimeDonchianCloseConfirm", "TrendBreakoutRetest", "BreakoutRetestV2"].indexOf(String(strategy || "")) !== -1) {
    return {
      strategyKey: "trade_discovery_breakout_fallback",
      gridName: "Trade-discovery breakout fallback",
      humanName: "Trade-discovery breakout fallback",
      fallbackType: "TRADE_DISCOVERY",
      params: {
        donchianEntry: [5, 10, 20],
        donchianExit: [3, 5, 10],
        adxThreshold: [0, 8, 12],
        atrMultiplier: [1.8, 2.5, 3.3],
        emaTrendLength: [20, 50, 100],
        retestLookback: [3, 5, 8],
        retestAtr: [0.8, 1.2, 1.8],
        volumeFilter: [false],
        shortMode: [false]
      },
      maxCombinations: 60,
      notes: ["Trade-discovery fallback for breakout/regime strategies uses shorter existing Donchian/EMA thresholds and disables optional volume filtering."],
      warning: "Fallback grid is for diagnosing trade generation, not immediate promotion.",
      riskLevel: "diagnostic"
    };
  }
  return {
    strategyKey: "trade_discovery_fallback",
    gridName: "Trade-discovery fallback",
    humanName: "Trade-discovery fallback",
    fallbackType: "TRADE_DISCOVERY",
    params: {
      emaFast: [9, 20],
      emaMomentumSlow: [21],
      emaTrendFast: [50],
      emaSlow: [100, 200],
      rsiMin: [30, 45],
      rsiMax: [70, 80],
      stopAtr: [2, 3],
      takeProfitAtr: [3],
      trailingAtr: [2],
      minHoldBars: [1, 4],
      cooldownBars: [0],
      requireVolume: [false],
      requireBreakout: [false]
    },
    maxCombinations: 60,
    notes: ["Small trade-discovery fallback with wider existing thresholds. It does not force trades or change formulas."],
    warning: "Fallback grid is for diagnosing trade generation, not immediate promotion.",
    riskLevel: "diagnostic"
  };
}

function gridMetadata(grid, ranges, planned, tested, sampled, fallbackUsed, fallbackReason) {
  return {
    strategyKey: grid.strategyKey,
    gridName: grid.gridName || grid.humanName,
    humanName: grid.humanName || grid.gridName,
    params: ranges,
    paramCount: Object.keys(ranges || {}).length,
    candidateCountPlanned: planned || expandGrid(ranges || {}).filter(validCombo).length,
    candidateCountTested: tested || 0,
    maxCombinations: grid.maxCombinations,
    fallbackUsed: fallbackUsed === true,
    fallbackReason: fallbackReason || null,
    fallbackType: grid.fallbackType || null,
    warning: grid.warning || null,
    sampled: sampled === true,
    notes: grid.notes || [],
    riskLevel: grid.riskLevel || "unknown"
  };
}

function stableSample(combos, maxCount) {
  if (combos.length <= maxCount) return combos;
  if (maxCount <= 1) return combos.slice(0, 1);
  var output = [];
  var lastIndex = combos.length - 1;
  for (var i = 0; i < maxCount; i += 1) {
    var index = Math.floor(i * lastIndex / (maxCount - 1));
    output.push(combos[index]);
  }
  var byKey = {};
  output.forEach(function (combo) { byKey[stableParamKey(combo)] = combo; });
  return Object.keys(byKey).sort().map(function (key) { return byKey[key]; });
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
  if (params.rsiPullbackLevel !== undefined && params.rsiReclaimLevel !== undefined && Number(params.rsiPullbackLevel) >= Number(params.rsiReclaimLevel)) {
    return false;
  }
  if (params.donchianExit !== undefined && params.donchianEntry !== undefined && Number(params.donchianExit) >= Number(params.donchianEntry)) {
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
        params: paramsWithCosts(options, params),
        candles: train
      })),
      test: metrics(backtest.runBacktestOnCandles({
        symbol: options.symbol,
        interval: options.interval,
        strategy: options.strategy,
        params: paramsWithCosts(options, params),
        candles: test
      }))
    });
  }
  return results;
}

function buildOptimizationRow(options, params, train, test) {
  var trainMetrics = metrics(train);
  var testMetrics = metrics(test);
  var zeroTradeDiagnostics = train.trades === 0 && test.trades === 0
    ? zeroTradeDiagnosticsForRow(train, test)
    : null;
  var row = {
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
    valid: validCandidate({ train: trainMetrics, test: testMetrics }),
    zeroTradeDiagnostics: zeroTradeDiagnostics
  };
  return ensureCandidateQuality(row);
}

function optimizerQualityPolicy() {
  return Object.assign({}, OPTIMIZER_QUALITY_POLICY);
}

function ensureCandidateQuality(row) {
  var quality = evaluateCandidateQuality(row, OPTIMIZER_QUALITY_POLICY);
  row.qualityStatus = quality.qualityStatus;
  row.isValid = quality.isValid;
  row.valid = quality.isValid;
  row.scorePenalty = quality.scorePenalty;
  row.rejectionReasons = quality.rejectionReasons;
  row.qualityWarnings = quality.warnings;
  row.qualityMetrics = quality.qualityMetrics;
  return row;
}

function evaluateCandidateQuality(row, policy) {
  policy = Object.assign({}, OPTIMIZER_QUALITY_POLICY, policy || {});
  var train = row.train || {};
  var test = row.test || {};
  var hasFull = row.full && Object.keys(row.full).length;
  var full = hasFull ? row.full : Object.assign({}, test, {
    trades: Number(train.trades || 0) + Number(test.trades || 0),
    maxDrawdown: Math.max(Number(train.maxDrawdown || 0), Number(test.maxDrawdown || 0)),
    totalReturn: Number(train.totalReturn || 0) + Number(test.totalReturn || 0)
  });
  var walkForwardRows = Array.isArray(row.walkForward) ? row.walkForward : [];
  var metrics = {
    trainTrades: numberOrNull(train.trades),
    testTrades: numberOrNull(test.trades),
    fullTrades: numberOrNull(full.trades),
    trainReturnPct: numberOrNull(train.totalReturn),
    testReturnPct: numberOrNull(test.totalReturn),
    fullReturnPct: numberOrNull(full.totalReturn),
    testProfitFactor: numberOrNull(test.profitFactor),
    fullProfitFactor: numberOrNull(full.profitFactor),
    testMaxDrawdownPct: numberOrNull(test.maxDrawdown),
    fullMaxDrawdownPct: numberOrNull(full.maxDrawdown),
    trainTestReturnGapPct: Math.abs(Number(train.totalReturn || 0) - Number(test.totalReturn || 0)),
    negativeWalkForwardFoldRatio: negativeWalkForwardFoldRatio(walkForwardRows)
  };
  var hardReasons = [];
  var warnings = [];

  if (missingCoreMetrics(metrics)) hardReasons.push(reason("missing_metrics"));
  if ((Number(metrics.trainTrades || 0) + Number(metrics.testTrades || 0) + Number(metrics.fullTrades || 0)) === 0) {
    hardReasons.push(reason("zero_trades"));
  }
  if (row.zeroTradeDiagnostics) {
    hardReasons.push(reason("zero_trade_diagnostics"));
  }
  if (Number(metrics.testTrades || 0) < policy.minTestTrades) {
    hardReasons.push(reason("too_few_test_trades"));
  }
  if (Number(metrics.fullTrades || 0) < policy.minFullTrades) {
    hardReasons.push(reason("too_few_full_trades"));
  }
  if (Number(metrics.testProfitFactor || 0) <= 1) {
    hardReasons.push(reason("test_profit_factor_below_min"));
  } else if (Number(metrics.testProfitFactor || 0) < policy.minProfitFactor) {
    warnings.push(reason("test_profit_factor_below_min"));
  }
  if (Number(metrics.fullProfitFactor || 0) <= 1) {
    hardReasons.push(reason("full_profit_factor_below_min"));
  } else if (Number(metrics.fullProfitFactor || 0) < policy.minProfitFactor) {
    warnings.push(reason("full_profit_factor_below_min"));
  }
  if (Number(metrics.testReturnPct || 0) < policy.minTestReturnPct) {
    hardReasons.push(reason("negative_test_return"));
  }
  if (Math.max(Number(metrics.testMaxDrawdownPct || 0), Number(metrics.fullMaxDrawdownPct || 0)) > policy.maxDrawdownPct) {
    hardReasons.push(reason("high_drawdown"));
  }
  if (metrics.trainTestReturnGapPct > policy.maxTrainTestReturnGapPct) {
    hardReasons.push(reason("train_test_overfit_gap"));
  }
  if (Number(metrics.trainReturnPct || 0) > 0 && Number(metrics.testReturnPct || 0) <= 0) {
    hardReasons.push(reason("train_only_success_test_failure"));
  }
  if (metrics.negativeWalkForwardFoldRatio !== null && metrics.negativeWalkForwardFoldRatio > policy.maxNegativeWalkForwardFoldRatio) {
    hardReasons.push(reason("unstable_walk_forward"));
  }
  if (row.valid === false && !hardReasons.length) {
    warnings.push(reason("invalid_candidate"));
  }

  hardReasons = uniqueReasons(hardReasons);
  warnings = uniqueReasons(warnings.filter(function (item) {
    return !hardReasons.some(function (reasonItem) { return reasonItem.code === item.code; });
  }));
  var status = hardReasons.length ? "FAIL" : (warnings.length ? "WARN" : "PASS");
  return {
    qualityStatus: status,
    isValid: status !== "FAIL",
    scorePenalty: status === "FAIL" ? 100 + hardReasons.length * 5 : warnings.length * 5,
    rejectionReasons: hardReasons,
    warnings: warnings,
    qualityMetrics: metrics
  };
}

function acceptableOptimizerRows(rows) {
  var passRows = rows.filter(function (row) { return row.qualityStatus === "PASS"; });
  if (passRows.length) return passRows;
  return rows.filter(function (row) { return row.qualityStatus === "WARN"; });
}

function buildQualitySummary(rows) {
  var counts = {};
  rows.forEach(function (row) {
    counts[row.qualityStatus || "FAIL"] = (counts[row.qualityStatus || "FAIL"] || 0) + 1;
  });
  var reasonCounts = {};
  rows.forEach(function (row) {
    (row.rejectionReasons || []).forEach(function (item) {
      reasonCounts[item.code || String(item)] = (reasonCounts[item.code || String(item)] || 0) + 1;
    });
  });
  var topReasons = Object.keys(reasonCounts).map(function (code) {
    return { reason: code, label: REJECTION_LABELS[code] || code, count: reasonCounts[code] };
  }).sort(function (a, b) { return b.count - a.count; }).slice(0, 8);
  var selectedStatus = counts.PASS ? "PASS" : (counts.WARN ? "WARN" : "NONE");
  var warnings = [];
  if (selectedStatus === "NONE" && rows.length) warnings.push("No acceptable optimizer candidate found; every candidate failed the quality policy.");
  if (counts.FAIL) warnings.push(counts.FAIL + " optimizer candidates failed quality filters.");
  return {
    totalCandidates: rows.length,
    passCandidates: counts.PASS || 0,
    warnCandidates: counts.WARN || 0,
    failCandidates: counts.FAIL || 0,
    selectedStatus: selectedStatus,
    topRejectionReasons: topReasons,
    warnings: warnings
  };
}

function buildGridAudit(strategy, interval, gridMetadata, rows, zeroTradeSummary, qualitySummary) {
  var tooFewTest = countReason(rows, "too_few_test_trades");
  var tooFewFull = countReason(rows, "too_few_full_trades");
  var dominant = (qualitySummary.topRejectionReasons || []).slice(0, 5);
  var dominantCodes = dominant.map(function (item) { return item.reason; });
  var diagnosis = "UNKNOWN";
  if (!rows.length) {
    diagnosis = "UNKNOWN";
  } else if (zeroTradeSummary.zeroTradeCandidates === rows.length || dominantCodes.indexOf("zero_trades") !== -1) {
    diagnosis = "TOO_RESTRICTIVE";
  } else if (dominantCodes.indexOf("too_few_test_trades") !== -1 || dominantCodes.indexOf("too_few_full_trades") !== -1) {
    diagnosis = isLowTimeframe(interval) ? "TIMEFRAME_TOO_LOW" : "PERIOD_TOO_SHORT";
  } else if (dominantCodes.indexOf("test_profit_factor_below_min") !== -1 || dominantCodes.indexOf("full_profit_factor_below_min") !== -1 || dominantCodes.indexOf("high_drawdown") !== -1) {
    diagnosis = "QUALITY_FAILING";
  }
  var suggested = [];
  if (diagnosis === "TOO_RESTRICTIVE") {
    suggested.push("Use the trade-discovery fallback grid to verify trade generation before broader optimization.");
    suggested.push("Prefer looser existing regime/trend filters and shorter Donchian/EMA values before changing strategy formulas.");
  } else if (diagnosis === "PERIOD_TOO_SHORT") {
    suggested.push("Use Auto/50000 candle limits or higher timeframes so train/test windows contain enough trades.");
  } else if (diagnosis === "TIMEFRAME_TOO_LOW") {
    suggested.push("Use a higher timeframe or aggregate more candles before widening the grid.");
  } else if (diagnosis === "QUALITY_FAILING") {
    suggested.push("Inspect candidate metrics before changing the quality policy; weak PF/drawdown failures may indicate strategy edge, not grid breadth.");
  }
  if (gridMetadata.fallbackUsed && gridMetadata.fallbackType === "TRADE_DISCOVERY") {
    suggested.push("Trade-discovery fallback is diagnostic only and should not be promoted directly without normal validation.");
  }
  return {
    strategy: strategy,
    gridName: gridMetadata.gridName,
    plannedCandidates: gridMetadata.candidateCountPlanned || 0,
    testedCandidates: rows.length,
    zeroTradeCandidates: zeroTradeSummary.zeroTradeCandidates || 0,
    tooFewTestTradesCandidates: tooFewTest,
    tooFewFullTradesCandidates: tooFewFull,
    passCandidates: qualitySummary.passCandidates || 0,
    warnCandidates: qualitySummary.warnCandidates || 0,
    failCandidates: qualitySummary.failCandidates || 0,
    dominantReasons: dominant,
    diagnosis: diagnosis,
    appearsTooRestrictive: diagnosis === "TOO_RESTRICTIVE",
    periodOrTimeframeMayBeTooShort: diagnosis === "PERIOD_TOO_SHORT",
    suggestedChanges: dedupeValues(suggested)
  };
}

function allRowsZeroTrade(rows) {
  return rows.length && rows.every(function (row) {
    return Number((row.train || {}).trades || 0) === 0 && Number((row.test || {}).trades || 0) === 0;
  });
}

function tradeDiscoveryFallbackReason(rows, allZero, qualitySummary) {
  if (!rows.length) return null;
  if (allZero) return "Initial grid produced zero trades for every candidate.";
  if ((qualitySummary || {}).selectedStatus !== "NONE") return null;
  var everyCandidateTooFew = rows.every(function (row) {
    return hasReason(row, "too_few_test_trades") || hasReason(row, "too_few_full_trades") || hasReason(row, "zero_trades");
  });
  return everyCandidateTooFew ? "Initial grid produced too few trades for every candidate." : null;
}

function hasReason(row, code) {
  return (row.rejectionReasons || []).some(function (item) { return item.code === code; });
}

function countReason(rows, code) {
  return rows.filter(function (row) {
    return hasReason(row, code);
  }).length;
}

function dedupeValues(values) {
  var seen = {};
  return (values || []).filter(function (value) {
    var key = JSON.stringify(value);
    if (seen[key]) return false;
    seen[key] = true;
    return true;
  });
}

function isLowTimeframe(interval) {
  var value = String(interval || "").toLowerCase();
  return ["1m", "3m", "5m", "15m"].indexOf(value) !== -1;
}

function reason(code) {
  return { code: code, label: REJECTION_LABELS[code] || code };
}

function uniqueReasons(items) {
  var seen = {};
  return items.filter(function (item) {
    if (seen[item.code]) return false;
    seen[item.code] = true;
    return true;
  });
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  var numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function missingCoreMetrics(metrics) {
  return metrics.testTrades === null ||
    metrics.testProfitFactor === null ||
    metrics.testReturnPct === null ||
    metrics.testMaxDrawdownPct === null;
}

function negativeWalkForwardFoldRatio(walkForwardRows) {
  if (!walkForwardRows.length) return null;
  var negative = walkForwardRows.filter(function (fold) {
    var test = fold.test || fold;
    return Number(test.totalReturn || 0) < 0 || Number(test.profitFactor || 0) <= 1;
  }).length;
  return negative / walkForwardRows.length;
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

function paramsWithCosts(options, params) {
  var merged = Object.assign({}, params || {});
  if (options.feePct !== undefined) merged.feePct = Number(options.feePct || 0);
  if (options.slippagePct !== undefined) merged.slippagePct = Number(options.slippagePct || 0);
  return merged;
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
    return (qualitySortValue(b) - qualitySortValue(a)) ||
      (b.score - a.score) ||
      (b.test.profitFactor - a.test.profitFactor) ||
      (b.test.totalReturn - a.test.totalReturn) ||
      (b.drawdownAdjustedReturn - a.drawdownAdjustedReturn);
  });
}

function qualitySortValue(row) {
  if (row.qualityStatus === "PASS") return 3;
  if (row.qualityStatus === "WARN") return 2;
  if (row.qualityStatus === "FAIL") return 1;
  return 0;
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

function aggregateZeroTradeSummary(rows) {
  var summary = {
    zeroTradeCandidates: 0,
    totalCandidates: rows.length,
    topReasons: [],
    suggestedGridAction: "No zero-trade issue detected."
  };
  var counts = {};
  rows.forEach(function (row) {
    if (!row.zeroTradeDiagnostics) return;
    summary.zeroTradeCandidates += 1;
    Object.keys(row.zeroTradeDiagnostics.reasonCounters || {}).forEach(function (reason) {
      counts[reason] = (counts[reason] || 0) + Number(row.zeroTradeDiagnostics.reasonCounters[reason] || 0);
    });
  });
  summary.topReasons = Object.keys(counts).map(function (reason) {
    return { reason: reason, count: counts[reason] };
  }).sort(function (a, b) { return b.count - a.count; }).slice(0, 8);
  if (summary.zeroTradeCandidates === rows.length && rows.length) {
    var top = summary.topReasons[0] ? summary.topReasons[0].reason : "unknown";
    summary.suggestedGridAction = suggestedGridAction(top);
  } else if (summary.zeroTradeCandidates > 0) {
    summary.suggestedGridAction = "Some candidates produced zero trades; inspect top reasons before widening the grid.";
  }
  return summary;
}

function zeroTradeDiagnosticsForRow(train, test) {
  var counters = {};
  collectResultReasons(counters, train);
  collectResultReasons(counters, test);
  var top = Object.keys(counters).map(function (reason) {
    return { reason: reason, count: counters[reason] };
  }).sort(function (a, b) { return b.count - a.count; })[0];
  return {
    summary: {
      likelyReason: top ? (top.reason + " appears to be the dominant blocker (" + top.count + ").") : "No trades and no detailed blocker counters were exposed.",
      confidence: top ? "MEDIUM" : "LOW"
    },
    reasonCounters: counters
  };
}

function collectResultReasons(counters, result) {
  var diagnostics = result.diagnostics || {};
  var debug = diagnostics.debug || {};
  var sources = [diagnostics.blockerCounts, diagnostics.skipReasons, debug.blockerCounts, debug.skipReasons];
  var sawReasons = false;
  sources.forEach(function (source) {
    if (!source) return;
    Object.keys(source).forEach(function (reason) {
      sawReasons = true;
      var canonical = canonicalZeroTradeReason(reason);
      counters[canonical] = (counters[canonical] || 0) + Number(source[reason] || 1);
    });
  });
  if (!sawReasons && result.trades === 0) {
    counters.no_entry_signal = (counters.no_entry_signal || 0) + Number(diagnostics.candlesLoaded || 1);
  }
  if (Number(diagnostics.candlesLoaded || 0) <= Number(diagnostics.warmupCandles || 0)) {
    counters.warmup_not_met = (counters.warmup_not_met || 0) + 1;
  }
}

function canonicalZeroTradeReason(reason) {
  var text = String(reason || "").toLowerCase();
  if (text.indexOf("warmup") !== -1 || text.indexOf("confirmation") !== -1) return "warmup_not_met";
  if (text.indexOf("regime") !== -1 || text.indexOf("btc") !== -1) return "regime_filter_blocked";
  if (text.indexOf("ema") !== -1 || text.indexOf("trend") !== -1 || text.indexOf("donchian") !== -1 || text.indexOf("breakout") !== -1 || text.indexOf("pullback") !== -1 || text.indexOf("reclaim") !== -1 || text.indexOf("rsi") !== -1) return "trend_filter_blocked";
  if (text.indexOf("atr") !== -1 || text.indexOf("adx") !== -1 || text.indexOf("volatility") !== -1 || text.indexOf("squeeze") !== -1 || text.indexOf("range") !== -1) return "volatility_filter_blocked";
  if (text.indexOf("risk") !== -1 || text.indexOf("stop") !== -1 || text.indexOf("notional") !== -1 || text.indexOf("volume") !== -1 || text.indexOf("cooldown") !== -1 || text.indexOf("position") !== -1) return "risk_filter_blocked";
  if (text.indexOf("nan") !== -1 || text.indexOf("invalid") !== -1 || text.indexOf("missing") !== -1) return "invalid_indicator_values";
  if (text.indexOf("short") !== -1) return "short_mode_disabled";
  if (text.indexOf("exit") !== -1) return "no_exit_signal";
  if (text.indexOf("entry") !== -1 || text.indexOf("signal") !== -1 || text.indexOf("false") !== -1) return "no_entry_signal";
  return "unknown";
}

function suggestedGridAction(reason) {
  if (reason === "warmup_not_met") return "Use a longer period or higher candle limit before optimizing.";
  if (reason === "regime_filter_blocked") return "Try a grid with looser regimeMode values or a different timeframe.";
  if (reason === "trend_filter_blocked") return "Use a less restrictive trend/pullback/breakout grid before expanding combinations.";
  if (reason === "volatility_filter_blocked") return "Widen ADX/ATR/squeeze thresholds or use a higher timeframe.";
  if (reason === "risk_filter_blocked") return "Review volume/risk/cooldown constraints before trusting the scan.";
  return "Run a single backtest diagnosis for this strategy and market before widening the grid.";
}

function optimizerRunWarnings(gridMetadata, zeroTradeSummary, allZero, qualitySummary) {
  var warnings = [];
  if (gridMetadata.sampled) warnings.push("Optimizer grid was deterministically sampled/truncated to stay within max-combos.");
  if (gridMetadata.fallbackUsed) warnings.push("Trade-discovery fallback grid was used: " + (gridMetadata.fallbackReason || "initial grid was too restrictive."));
  if (gridMetadata.fallbackType === "TRADE_DISCOVERY") warnings.push(gridMetadata.warning || "Fallback grid is for diagnosing trade generation, not immediate promotion.");
  if (allZero) warnings.push("Every tested optimizer candidate produced zero trades. Do not treat this as a successful optimization.");
  if (zeroTradeSummary && zeroTradeSummary.zeroTradeCandidates) warnings.push(zeroTradeSummary.suggestedGridAction);
  if (qualitySummary && qualitySummary.selectedStatus === "NONE") warnings.push("No acceptable optimizer candidate found after quality filtering.");
  if (qualitySummary && Array.isArray(qualitySummary.warnings)) warnings = warnings.concat(qualitySummary.warnings);
  return warnings.filter(Boolean);
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
  optimizeRegimeStaged: optimizeRegimeStaged,
  optimizerGridCatalog: optimizerGridCatalog,
  availableOptimizerGrids: availableOptimizerGrids,
  optimizerGridMetadataCatalog: optimizerGridMetadataCatalog,
  selectOptimizerGrid: selectOptimizerGrid,
  defaultRanges: defaultRanges,
  expandGrid: expandGrid,
  splitCandles: splitCandles,
  walkForward: walkForward,
  optimizerQualityPolicy: optimizerQualityPolicy,
  evaluateCandidateQuality: evaluateCandidateQuality,
  parseRanges: parseRanges,
  validCombo: validCombo,
  validCandidate: validCandidate,
  robustnessScore: robustnessScore
};
