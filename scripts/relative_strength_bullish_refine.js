const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");

function arg(name, fallback) {
  const flag = "--" + name;
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] !== undefined ? process.argv[index + 1] : fallback;
}

function boolArg(name, fallback) {
  const value = arg(name, fallback ? "true" : "false");
  return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
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
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades) : 0
  };
  return Object.assign(row, extra || {});
}

function quality(row) {
  if (row.trades < 40) return -100000 + row.trades;
  if (row.totalReturnPct <= 0) return -90000 + row.totalReturnPct;
  const ddPenalty = Math.max(0, row.maxDrawdownPct - 22) * 12 + Math.max(0, row.maxDrawdownPct - 25) * 40;
  return round(row.totalReturnPct * 0.8 + row.profitFactor * 45 + row.expectancyPctPerTrade * 18 - row.maxDrawdownPct * 5 - ddPenalty + Math.min(row.trades, 140) * 0.25);
}

function passStatus(row, minTrades) {
  if (row.trades < (minTrades || 20)) return "FAIL_TRADES";
  if (row.totalReturnPct <= 0) return "FAIL_RETURN";
  if (row.profitFactor < 1.1) return "FAIL_PF";
  if (row.maxDrawdownPct > 25) return "WARN_DD";
  return "PASS";
}

function sliceByTime(rows, from, to) {
  return rows.filter((row) => row.time >= from && row.time <= to);
}

function summarizeDeep(candles, regimeCandles, params, context) {
  const costs = [
    { scenario: "baseline", feePct: 0.055, slippagePct: 0.02 },
    { scenario: "doubleSlippage", feePct: 0.055, slippagePct: 0.04 },
    { scenario: "doubleFees", feePct: 0.11, slippagePct: 0.02 },
    { scenario: "highStress", feePct: 0.11, slippagePct: 0.06 }
  ];
  const stress = costs.map((cost) => {
    const result = backtest.runBacktestOnCandles({
      source: context.source,
      symbol: context.symbol,
      interval: context.timeframe,
      strategy: "RelativeStrengthV2",
      candles,
      regimeCandles,
      params: Object.assign({}, params, cost)
    });
    const row = resultRow(result, params, { scenario: cost.scenario });
    row.status = passStatus(row, 40);
    return row;
  });

  const first = candles[0].time;
  const last = candles[candles.length - 1].time;
  const span = Math.floor((last - first + 1) / 4);
  const folds = [0, 1, 2, 3].map((fold) => {
    const from = first + span * fold;
    const to = fold === 3 ? last : first + span * (fold + 1) - 1;
    const result = backtest.runBacktestOnCandles({
      source: context.source,
      symbol: context.symbol,
      interval: context.timeframe,
      strategy: "RelativeStrengthV2",
      candles: sliceByTime(candles, from, to),
      regimeCandles: sliceByTime(regimeCandles, from, to),
      params: Object.assign({}, params, { feePct: 0.055, slippagePct: 0.02 })
    });
    const row = resultRow(result, params, { fold: fold + 1, startTime: new Date(from * 1000).toISOString(), endTime: new Date(to * 1000).toISOString() });
    row.status = passStatus(row, 20);
    return row;
  });

  const passStress = stress.filter((row) => row.status === "PASS").length;
  const warnStress = stress.filter((row) => row.status === "WARN_DD").length;
  const passFolds = folds.filter((row) => row.status === "PASS").length;
  const positiveFolds = folds.filter((row) => row.totalReturnPct > 0 && row.profitFactor >= 1.1).length;
  const minFoldTrades = Math.min.apply(null, folds.map((row) => row.trades));
  const maxStressDd = Math.max.apply(null, stress.map((row) => row.maxDrawdownPct));
  const baseline = stress[0];
  const deepScore = round(
    baseline.totalReturnPct * 0.65 +
    baseline.profitFactor * 55 -
    baseline.maxDrawdownPct * 7 -
    Math.max(0, maxStressDd - 25) * 45 +
    passStress * 14 +
    warnStress * 4 +
    passFolds * 18 +
    positiveFolds * 8 +
    Math.min(minFoldTrades, 30) * 1.5
  );
  return {
    stress,
    folds,
    passStress,
    warnStress,
    passFolds,
    positiveFolds,
    minFoldTrades,
    maxStressDd: round(maxStressDd),
    deepScore
  };
}

async function main() {
  const source = arg("source", "bybit");
  const symbol = arg("symbol", "BTCUSDT");
  const timeframe = arg("timeframe", "4h");
  const days = Number(arg("days", "1095"));
  const top = Number(arg("top", "20"));
  const quick = boolArg("quick", false);
  const tag = arg("tag", "20260830");
  const outputDir = arg("output", "reports/research-campaigns");
  const limit = Math.ceil(days * 24 / timeframeHours(timeframe));
  const candles = await data.fetchCandles({ source, symbol, interval: timeframe, limit });
  const regimeCandles = await data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", limit });
  const baseParams = {
    regimeMode: "looseBtcBull",
    volumeFilter: false,
    indicatorFilterPreset: "trendQuality",
    causalRegimeFilter: "bullishOnly"
  };
  const grid = quick ? {
    rsLookback: [42, 48, 54, 60],
    rsThreshold: [-0.025, -0.02, -0.015],
    rsiMin: [50, 51, 52, 53, 54, 55],
    rsiMax: [66, 68, 70],
    atrMultiplier: [2.2, 2.5, 2.8]
  } : {
    rsLookback: [36, 42, 48, 54, 60, 72],
    rsThreshold: [-0.03, -0.025, -0.02, -0.015, -0.01],
    rsiMin: [50, 51, 52, 53, 54, 55],
    rsiMax: [66, 68, 70, 72],
    atrMultiplier: [2.2, 2.5, 2.8, 3.1]
  };
  const riskValues = [0.035, 0.04, 0.045, 0.05];
  const combos = cartesian(grid);
  const screened = combos.map((combo, index) => {
    if (index > 0 && index % 240 === 0) {
      process.stderr.write(JSON.stringify({ stage: "screen", tested: index, total: combos.length }) + "\n");
    }
    const params = Object.assign({}, baseParams, combo, { riskPct: 0.05, feePct: 0.055, slippagePct: 0.02 });
    const result = backtest.runBacktestOnCandles({ source, symbol, interval: timeframe, strategy: "RelativeStrengthV2", candles, regimeCandles, params });
    const row = resultRow(result, params, { screenScore: 0 });
    row.screenScore = quality(row);
    return row;
  }).sort((a, b) => b.screenScore - a.screenScore);

  const finalistMap = new Map();
  screened.slice(0, top).forEach((row) => {
    riskValues.forEach((riskPct) => {
      const params = Object.assign({}, row.params, { riskPct });
      delete params.feePct;
      delete params.slippagePct;
      finalistMap.set(JSON.stringify(params), params);
    });
  });

  const finalistParams = Array.from(finalistMap.values());
  const finalists = finalistParams.map((params, index) => {
    if (index > 0 && index % 10 === 0) {
      process.stderr.write(JSON.stringify({ stage: "deep", tested: index, total: finalistParams.length }) + "\n");
    }
    const deep = summarizeDeep(candles, regimeCandles, params, { source, symbol, timeframe });
    return Object.assign({ rankInput: index + 1, params }, deep);
  }).sort((a, b) => b.deepScore - a.deepScore);

  const report = {
    generatedAt: new Date().toISOString(),
    purpose: "Focused refinement of RelativeStrengthV2 BTCUSDT 4h with causalRegimeFilter=bullishOnly.",
    source,
    symbol,
    timeframe,
    days,
    quick,
    candleCount: candles.length,
    grid,
    riskValues,
    screenedCount: screened.length,
    finalistCount: finalists.length,
    best: finalists[0] || null,
    topScreened: screened.slice(0, 25),
    topFinalists: finalists.slice(0, 25),
    notes: [
      "Read-only research. No paper config or live trading config is changed.",
      "RiskPct is included because the main remaining weakness is drawdown under stress, not entry quality alone."
    ]
  };
  fs.mkdirSync(outputDir, { recursive: true });
  const safeTag = String(tag || "run").replace(/[^a-zA-Z0-9_.-]/g, "-");
  const outPath = path.join(outputDir, "btc4h-relativestrength-bullishonly-param-refine-" + safeTag + ".json");
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({
    output: outPath,
    screenedCount: report.screenedCount,
    finalistCount: report.finalistCount,
    best: report.best ? {
      params: report.best.params,
      deepScore: report.best.deepScore,
      baseline: report.best.stress[0],
      maxStressDd: report.best.maxStressDd,
      passStress: report.best.passStress,
      passFolds: report.best.passFolds,
      minFoldTrades: report.best.minFoldTrades
    } : null
  }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
