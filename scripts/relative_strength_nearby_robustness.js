const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");

function arg(name, fallback) {
  const flag = "--" + name;
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] !== undefined ? process.argv[index + 1] : fallback;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function timeframeHours(timeframe) {
  const raw = String(timeframe || "4h").trim().toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 60) / 60;
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1);
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24;
  return 4;
}

function minTradesForTimeframe(timeframe) {
  const hours = timeframeHours(timeframe);
  if (hours <= 0.25) return { screen: 200, fold: 40 };
  if (hours <= 1) return { screen: 100, fold: 20 };
  return { screen: 40, fold: 10 };
}

function cartesian(grid) {
  const keys = Object.keys(grid);
  const rows = [];
  function visit(index, row) {
    if (index >= keys.length) {
      rows.push(Object.assign({}, row));
      return;
    }
    const key = keys[index];
    grid[key].forEach((value) => {
      row[key] = value;
      visit(index + 1, row);
    });
  }
  visit(0, {});
  return rows;
}

function median(values) {
  const nums = values.map(Number).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!nums.length) return 0;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
}

function percentile(values, pct) {
  const nums = values.map(Number).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!nums.length) return 0;
  const index = Math.min(nums.length - 1, Math.max(0, Math.floor((nums.length - 1) * pct)));
  return nums[index];
}

function status(row, minTrades) {
  if (row.trades < minTrades) return "FAIL_TRADES";
  if (row.totalReturnPct <= 0) return "FAIL_RETURN";
  if (row.profitFactor < 1.1) return "FAIL_PF";
  if (row.maxDrawdownPct > 25) return "FAIL_DD";
  return "PASS";
}

function quality(row) {
  if (row.status !== "PASS") return -100000 + row.totalReturnPct;
  return round(
    row.totalReturnPct * 0.7 +
    row.profitFactor * 50 -
    row.maxDrawdownPct * 5 +
    Math.min(row.trades, 160) * 0.2 +
    row.expectancyPctPerTrade * 20
  );
}

function resultRow(result, params, extra) {
  const trades = Number(result.trades || 0);
  const row = {
    params,
    trades,
    totalReturnPct: round(result.totalReturn || 0),
    profitFactor: round(result.profitFactor || 0),
    maxDrawdownPct: round(result.maxDrawdown || 0),
    winRate: round(result.winRate || 0),
    avgBarsHeld: round(result.avgBarsHeld || 0),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades) : 0,
    warnings: result.warnings || []
  };
  return Object.assign(row, extra || {});
}

function sliceByTime(rows, from, to) {
  return rows.filter((row) => row.time >= from && row.time <= to);
}

function runOne(candles, regimeCandles, context, params, costs, label, minTrades) {
  const withCosts = Object.assign({}, params, costs);
  const benchmarkSymbol = params.rsBenchmarkSymbol || params.benchmarkSymbol || context.benchmarkSymbol || null;
  const benchmarkCandles = benchmarkSymbol && context.benchmarkCandlesBySymbol
    ? context.benchmarkCandlesBySymbol[benchmarkSymbol]
    : null;
  const result = backtest.runBacktestOnCandles({
    source: context.source,
    symbol: context.symbol,
    interval: context.timeframe,
    strategy: "RelativeStrengthV2",
    candles,
    regimeCandles,
    benchmarkCandles,
    params: withCosts
  });
  const row = resultRow(result, params, { label });
  row.status = status(row, minTrades);
  row.score = quality(row);
  return row;
}

function walkForward(candles, regimeCandles, context, params, minTrades) {
  const first = candles[0].time;
  const last = candles[candles.length - 1].time;
  const span = Math.floor((last - first + 1) / 4);
  const folds = [0, 1, 2, 3].map((fold) => {
    const from = first + span * fold;
    const to = fold === 3 ? last : first + span * (fold + 1) - 1;
    return runOne(
      sliceByTime(candles, from, to),
      sliceByTime(regimeCandles, from, to),
      context,
      params,
      { feePct: 0.055, slippagePct: 0.02 },
      "fold-" + String(fold + 1),
      minTrades && minTrades.fold ? minTrades.fold : 10
    );
  });
  const passCount = folds.filter((row) => row.status === "PASS").length;
  return {
    folds,
    passCount,
    positiveCount: folds.filter((row) => row.totalReturnPct > 0 && row.profitFactor >= 1.1).length,
    worstReturnPct: round(Math.min.apply(null, folds.map((row) => row.totalReturnPct))),
    worstDrawdownPct: round(Math.max.apply(null, folds.map((row) => row.maxDrawdownPct))),
    medianReturnPct: round(median(folds.map((row) => row.totalReturnPct))),
    medianProfitFactor: round(median(folds.map((row) => row.profitFactor)))
  };
}

function deepReview(candles, regimeCandles, context, params, minTrades) {
  const stressCosts = [
    { label: "baseline-costs", feePct: 0.055, slippagePct: 0.02 },
    { label: "double-slippage", feePct: 0.055, slippagePct: 0.04 },
    { label: "double-fees", feePct: 0.11, slippagePct: 0.02 },
    { label: "high-stress", feePct: 0.11, slippagePct: 0.06 }
  ];
  const stress = stressCosts.map((item) => runOne(candles, regimeCandles, context, params, item, item.label, minTrades && minTrades.screen ? minTrades.screen : 40));
  const wf = walkForward(candles, regimeCandles, context, params, minTrades);
  const passStress = stress.filter((row) => row.status === "PASS").length;
  const maxStressDd = Math.max.apply(null, stress.map((row) => row.maxDrawdownPct));
  const baseline = stress[0];
  const deepScore = round(
    baseline.score +
    passStress * 15 +
    wf.passCount * 18 +
    wf.positiveCount * 8 -
    Math.max(0, maxStressDd - 22) * 12 -
    Math.max(0, 4 - wf.passCount) * 18
  );
  return {
    params,
    stress,
    walkForward: wf,
    passStress,
    maxStressDd: round(maxStressDd),
    deepScore,
    verdict: passStress >= 3 && wf.passCount >= 3 && maxStressDd <= 25 ? "ROBUST" : passStress >= 2 && wf.positiveCount >= 3 ? "WATCH" : "FRAGILE"
  };
}

function neighborDistance(base, params) {
  return Object.keys(params).filter((key) => params[key] !== base[key]).length;
}

function alignBasketCandles(seriesBySymbol) {
  const symbols = Object.keys(seriesBySymbol || {});
  if (!symbols.length) return [];
  const maps = {};
  symbols.forEach((symbol) => {
    maps[symbol] = new Map((seriesBySymbol[symbol] || []).map((row) => [Number(row.time), row]));
  });
  const times = Array.from(maps[symbols[0]].keys()).filter((time) => symbols.every((symbol) => maps[symbol].has(time))).sort((a, b) => a - b);
  if (!times.length) return [];
  const first = {};
  symbols.forEach((symbol) => {
    first[symbol] = maps[symbol].get(times[0]).close || 1;
  });
  return times.map((time) => {
    const values = symbols.map((symbol) => {
      const row = maps[symbol].get(time);
      return row.close / first[symbol];
    });
    const close = values.reduce((sum, value) => sum + value, 0) / values.length;
    return { time, open: close, high: close, low: close, close, volume: 0 };
  });
}

async function loadBenchmarkCandles(context, grid, limit) {
  const wanted = new Set();
  (grid.rsBenchmarkSymbol || grid.benchmarkSymbol || []).forEach((symbol) => wanted.add(symbol));
  const bySymbol = {};
  for (const benchmarkSymbol of wanted) {
    if (benchmarkSymbol === "BTCUSDT" || benchmarkSymbol === "NONE") continue;
    if (benchmarkSymbol === "CRYPTO_BASKET") {
      const entries = {};
      for (const basketSymbol of ["ETHUSDT", "SOLUSDT", "BNBUSDT"]) {
        entries[basketSymbol] = await data.fetchCandles({ source: context.source, symbol: basketSymbol, interval: context.timeframe, limit });
      }
      bySymbol[benchmarkSymbol] = alignBasketCandles(entries);
    } else {
      bySymbol[benchmarkSymbol] = await data.fetchCandles({ source: context.source, symbol: benchmarkSymbol, interval: context.timeframe, limit });
    }
  }
  return bySymbol;
}

function groupByCore(rows) {
  const groups = new Map();
  rows.forEach((row) => {
    const key = [
      "lookback", row.params.rsLookback,
      "threshold", row.params.rsThreshold,
      "rsiMin", row.params.rsiMin,
      "rsiMax", row.params.rsiMax,
      "atr", row.params.atrMultiplier,
      "benchmark", row.params.rsBenchmarkSymbol || row.params.benchmarkSymbol || "BTCUSDT"
    ].join(":");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  return Array.from(groups.values()).map((group) => {
    const sorted = group.slice().sort((a, b) => b.score - a.score);
    const pass = group.filter((row) => row.status === "PASS");
    return {
      paramsWithoutRisk: {
        rsLookback: sorted[0].params.rsLookback,
        rsThreshold: sorted[0].params.rsThreshold,
        rsiMin: sorted[0].params.rsiMin,
        rsiMax: sorted[0].params.rsiMax,
        atrMultiplier: sorted[0].params.atrMultiplier,
        rsBenchmarkSymbol: sorted[0].params.rsBenchmarkSymbol || sorted[0].params.benchmarkSymbol || "BTCUSDT"
      },
      testedRisks: group.map((row) => row.params.riskPct),
      passCount: pass.length,
      bestRisk: sorted[0].params.riskPct,
      bestScore: sorted[0].score,
      bestReturnPct: sorted[0].totalReturnPct,
      bestProfitFactor: sorted[0].profitFactor,
      bestMaxDrawdownPct: sorted[0].maxDrawdownPct,
      medianReturnPct: round(median(group.map((row) => row.totalReturnPct))),
      medianProfitFactor: round(median(group.map((row) => row.profitFactor))),
      medianDrawdownPct: round(median(group.map((row) => row.maxDrawdownPct)))
    };
  }).sort((a, b) =>
    b.passCount - a.passCount ||
    b.bestScore - a.bestScore ||
    b.medianReturnPct - a.medianReturnPct
  );
}

async function main() {
  const source = arg("source", "bybit");
  const symbol = arg("symbol", "BTCUSDT");
  const timeframe = arg("timeframe", "4h");
  const days = Number(arg("days", "1095"));
  const top = Number(arg("top", "30"));
  const maxScreen = Number(arg("max-screen", "240"));
  const mode = arg("mode", "full-nearby");
  const tag = arg("tag", new Date().toISOString().slice(0, 10).replace(/-/g, ""));
  const outputDir = arg("output", "reports/research-campaigns");
  const limit = Math.ceil(days * 24 / timeframeHours(timeframe));
  const context = { source, symbol, timeframe };
  const base = {
    atrMultiplier: 2.8,
    causalRegimeFilter: "bullishOnly",
    indicatorFilterPreset: "trendQuality",
    regimeMode: "looseBtcBull",
    riskPct: 0.03,
    rsLookback: 36,
    rsThreshold: -0.03,
    rsiMax: 72,
    rsiMin: 55,
    volumeFilter: false
  };
  if (mode === "benchmark" || mode === "benchmark-tight" || mode === "benchmark-1h-tight" || mode === "benchmark-1h-refine" || mode === "benchmark-15m-refine" || mode === "benchmark-15m-entry") {
    base.rsBenchmarkSymbol = "CRYPTO_BASKET";
    base.rsThreshold = -0.02;
    base.rsiMin = 61;
    base.atrMultiplier = 2.7;
  }
  if (mode === "benchmark-1h-tight" || mode === "benchmark-1h-refine") {
    base.riskPct = 0.02;
    base.rsLookback = 156;
    base.rsThreshold = -0.03;
    base.rsiMin = 58;
    base.rsiMax = 72;
    base.atrMultiplier = 2.7;
  }
  if (mode === "benchmark-15m-refine" || mode === "benchmark-15m-entry") {
    base.riskPct = 0.01;
    base.rsLookback = 624;
    base.rsThreshold = -0.025;
    base.rsiMin = 57;
    base.rsiMax = 72;
    base.atrMultiplier = 2.9;
  }
  const grid = mode === "benchmark-1h-refine" ? {
    rsBenchmarkSymbol: ["CRYPTO_BASKET"],
    rsLookback: [144, 150, 156, 162, 168],
    rsThreshold: [-0.04, -0.035, -0.03, -0.025, -0.02],
    rsiMin: [56, 57, 58, 59, 60],
    rsiMax: [70, 72, 74],
    atrMultiplier: [2.5, 2.7, 2.9, 3.0, 3.2],
    riskPct: [0.0125, 0.015, 0.0175, 0.02]
  } : mode === "benchmark-15m-refine" ? {
    rsBenchmarkSymbol: ["CRYPTO_BASKET"],
    rsLookback: [480, 576, 624, 672, 768],
    rsThreshold: [-0.04, -0.035, -0.03, -0.025, -0.02],
    rsiMin: [55, 56, 57, 58, 59, 60],
    rsiMax: [70, 72, 74],
    atrMultiplier: [2.5, 2.7, 2.9, 3.0, 3.2],
    riskPct: [0.005, 0.0075, 0.01, 0.0125, 0.015]
  } : mode === "benchmark-15m-entry" ? {
    rsBenchmarkSymbol: ["CRYPTO_BASKET"],
    rsLookback: [384, 480, 576, 624, 672],
    rsThreshold: [-0.035, -0.03, -0.025, -0.02, -0.015],
    rsiMin: [56, 57, 58, 59, 60],
    rsiMax: [68, 70, 72],
    atrMultiplier: [2.4, 2.6, 2.8, 3.0, 3.2],
    riskPct: [0.005, 0.0075, 0.01, 0.0125]
  } : mode === "benchmark-1h-tight" ? {
    rsBenchmarkSymbol: ["CRYPTO_BASKET"],
    rsLookback: [96, 120, 132, 144, 156, 168, 192],
    rsThreshold: [-0.035, -0.03, -0.025, -0.02, -0.015, -0.01, 0],
    rsiMin: [50, 52, 54, 55, 56, 58, 60],
    rsiMax: [70, 72, 74],
    atrMultiplier: [1.8, 2.1, 2.4, 2.7, 3.0],
    riskPct: [0.01, 0.015, 0.02, 0.025]
  } : mode === "benchmark-tight" ? {
    rsBenchmarkSymbol: ["CRYPTO_BASKET"],
    rsLookback: [30, 36, 42],
    rsThreshold: [-0.03, -0.02, -0.01, 0],
    rsiMin: [58, 60, 61, 62],
    rsiMax: [72, 74, 76],
    atrMultiplier: [2.6, 2.7, 2.8, 2.9],
    riskPct: [0.02, 0.025, 0.03]
  } : mode === "benchmark" ? {
    rsBenchmarkSymbol: ["ETHUSDT", "SOLUSDT", "BNBUSDT", "CRYPTO_BASKET"],
    rsLookback: [24, 30, 36, 42, 48],
    rsThreshold: [-0.08, -0.06, -0.04, -0.02, 0, 0.02],
    rsiMin: [56, 58, 60, 61, 62],
    rsiMax: [70, 72, 74, 76],
    atrMultiplier: [2.5, 2.6, 2.7, 2.8, 2.9],
    riskPct: [0.02, 0.025, 0.03]
  } : mode === "meaningful" ? {
    rsLookback: [36],
    rsThreshold: [-0.03],
    rsiMin: [55, 56, 57, 58, 59, 60, 61, 62],
    rsiMax: [70, 72, 74, 76, 78],
    atrMultiplier: [2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.0],
    riskPct: [0.02, 0.025, 0.03]
  } : {
    rsLookback: [30, 33, 36, 39, 42, 45],
    rsThreshold: [-0.04, -0.035, -0.03, -0.025, -0.02],
    rsiMin: [52, 54, 55, 56, 58],
    rsiMax: [68, 70, 72, 74],
    atrMultiplier: [2.4, 2.6, 2.8, 3.0, 3.2],
    riskPct: [0.02, 0.03]
  };

  const candles = await data.fetchCandles({ source, symbol, interval: timeframe, limit });
  const regimeLimit = Math.ceil(days * 24 / 4);
  const regimeCandles = await data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", limit: regimeLimit });
  context.benchmarkCandlesBySymbol = await loadBenchmarkCandles(context, grid, limit);
  const minTrades = minTradesForTimeframe(timeframe);
  const allCombos = cartesian(grid)
    .map((combo) => Object.assign({}, base, combo))
    .sort((a, b) =>
      neighborDistance(base, a) - neighborDistance(base, b) ||
      Math.abs(Number(a.riskPct) - Number(base.riskPct)) - Math.abs(Number(b.riskPct) - Number(base.riskPct)) ||
      Math.abs(Number(a.rsLookback) - Number(base.rsLookback)) - Math.abs(Number(b.rsLookback) - Number(base.rsLookback)) ||
      Math.abs(Number(a.rsThreshold) - Number(base.rsThreshold)) - Math.abs(Number(b.rsThreshold) - Number(base.rsThreshold)) ||
      Math.abs(Number(a.rsiMin) - Number(base.rsiMin)) - Math.abs(Number(b.rsiMin) - Number(base.rsiMin)) ||
      Math.abs(Number(a.atrMultiplier) - Number(base.atrMultiplier)) - Math.abs(Number(b.atrMultiplier) - Number(base.atrMultiplier))
    );
  const combos = maxScreen > 0 ? allCombos.slice(0, Math.min(maxScreen, allCombos.length)) : allCombos;
  const rows = combos.map((params, index) => {
    if (index > 0 && index % 10 === 0) {
      process.stderr.write(JSON.stringify({ stage: "nearby-screen", tested: index, total: combos.length }) + "\n");
    }
    const row = runOne(candles, regimeCandles, context, params, { feePct: 0.055, slippagePct: 0.02 }, "screen", minTrades.screen);
    row.neighborDistance = neighborDistance(base, params);
    return row;
  });
  const sorted = rows.slice().sort((a, b) => b.score - a.score);
  const baseResult = rows.find((row) => JSON.stringify(row.params) === JSON.stringify(base)) || null;
  const passRows = rows.filter((row) => row.status === "PASS");
  const nearOne = rows.filter((row) => row.neighborDistance <= 1);
  const nearTwo = rows.filter((row) => row.neighborDistance <= 2);
  const clusterSummary = {
    tested: rows.length,
    passCount: passRows.length,
    passRate: round(passRows.length / rows.length, 4),
    nearOnePassRate: round(nearOne.filter((row) => row.status === "PASS").length / Math.max(1, nearOne.length), 4),
    nearTwoPassRate: round(nearTwo.filter((row) => row.status === "PASS").length / Math.max(1, nearTwo.length), 4),
    medianReturnPct: round(median(rows.map((row) => row.totalReturnPct))),
    p25ReturnPct: round(percentile(rows.map((row) => row.totalReturnPct), 0.25)),
    medianProfitFactor: round(median(rows.map((row) => row.profitFactor))),
    medianDrawdownPct: round(median(rows.map((row) => row.maxDrawdownPct))),
    best: sorted[0] || null,
    baseResult
  };

  const deepParams = [];
  const seen = new Set();
  [baseResult].concat(sorted.slice(0, top)).filter(Boolean).forEach((row) => {
    const key = JSON.stringify(row.params);
    if (seen.has(key)) return;
    seen.add(key);
    deepParams.push(row.params);
  });
  const deep = deepParams.map((params, index) => {
    if (index > 0 && index % 10 === 0) {
      process.stderr.write(JSON.stringify({ stage: "deep-review", tested: index, total: deepParams.length }) + "\n");
    }
    return deepReview(candles, regimeCandles, context, params, minTrades);
  }).sort((a, b) => b.deepScore - a.deepScore);

  const verdict = clusterSummary.passRate >= 0.25 && clusterSummary.nearTwoPassRate >= 0.35 && deep.some((row) => row.verdict === "ROBUST")
    ? "ROBUST_CLUSTER"
    : clusterSummary.passRate >= 0.12 && deep.some((row) => row.verdict !== "FRAGILE")
      ? "PROMISING_BUT_SENSITIVE"
      : "FRAGILE";
  const report = {
    generatedAt: new Date().toISOString(),
    purpose: "Nearby-parameter robustness test around RelativeStrengthV2 BTCUSDT 4h bullishOnly risk 0.03.",
    source,
    symbol,
    timeframe,
    days,
    mode,
    benchmarkSymbols: Object.keys(context.benchmarkCandlesBySymbol || {}),
    candleCount: candles.length,
    screenedCount: rows.length,
    possibleNearbyCombos: allCombos.length,
    maxScreen,
    base,
    grid,
    clusterSummary,
    verdict,
    recommendation: verdict === "ROBUST_CLUSTER"
      ? "Keep the RS 4h paper candidates running and prefer the best robust cluster, not the single highest-return row."
      : verdict === "PROMISING_BUT_SENSITIVE"
        ? "Keep paper observation running, but do not increase risk. Inspect the deep rows and favor stable low-DD variants."
        : "Treat the current params as fragile. Keep paper-only observation, but search for a broader stable cluster before promotion.",
    topRows: sorted.slice(0, 50),
    topGroups: groupByCore(rows).slice(0, 25),
    deepReview: deep.slice(0, 25),
    notes: [
      "This is read-only research and does not alter paper config or live trading config.",
      "The grid intentionally tests nearby values around the current best rather than a broad search.",
      "Risk is limited to 0.02 and 0.03 because the earlier 0.05 version had uncomfortable drawdown."
    ]
  };
  fs.mkdirSync(outputDir, { recursive: true });
  const safeTag = String(tag || "run").replace(/[^a-zA-Z0-9_.-]/g, "-");
  const outPath = path.join(outputDir, symbol.toLowerCase() + "-" + timeframe.toLowerCase() + "-relativestrength-nearby-robustness-" + safeTag + ".json");
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({
    output: outPath,
    verdict: report.verdict,
    recommendation: report.recommendation,
    clusterSummary: report.clusterSummary,
    bestDeep: report.deepReview[0] || null
  }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
