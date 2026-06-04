const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");
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

function timeframeHours(timeframe) {
  const raw = String(timeframe || "1h").trim().toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 60) / 60;
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1);
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24;
  return 1;
}

function autoLimitFor(timeframe, days, cap) {
  const bars = Math.ceil(Number(days || 365) * 24 / timeframeHours(timeframe));
  return Math.max(100, Math.min(Number(cap || 5000), bars));
}

function clampInt(value, min) {
  return Math.max(min, Math.round(Number(value || min)));
}

function normalizeEmaOrder(params) {
  const out = Object.assign({}, params);
  out.emaFast = clampInt(out.emaFast, 2);
  out.emaSlow = Math.max(clampInt(out.emaSlow, 3), out.emaFast + 1);
  out.emaTrend = Math.max(clampInt(out.emaTrend, 4), out.emaSlow + 1);
  out.cooldownBars = clampInt(out.cooldownBars, 1);
  out.minHoldBars = clampInt(out.minHoldBars, 1);
  return out;
}

function scaledParams(base, timeframe) {
  const factor = 1 / timeframeHours(timeframe);
  return normalizeEmaOrder(Object.assign({}, base, {
    emaFast: Number(base.emaFast || 30) * factor,
    emaSlow: Number(base.emaSlow || 80) * factor,
    emaTrend: Number(base.emaTrend || 200) * factor,
    cooldownBars: Number(base.cooldownBars || 6) * factor,
    minHoldBars: Number(base.minHoldBars || 3) * factor
  }));
}

function presetSpec(family, name, timeframe, params) {
  return { presetFamily: family, presetName: name, timeframe, params: normalizeEmaOrder(params) };
}

function generatePresets(base, timeframes) {
  const rows = [];
  if (timeframes.includes("1h")) rows.push(presetSpec("baseline_1h", "active_1h_params", "1h", base));
  timeframes.forEach((timeframe) => {
    rows.push(presetSpec("same_candle_params", "same_candles_" + timeframe, timeframe, base));
    rows.push(presetSpec("time_normalized_from_1h", "normalized_" + timeframe, timeframe, scaledParams(base, timeframe)));
  });
  ["15m", "1h"].filter((timeframe) => timeframes.includes(timeframe)).forEach((timeframe) => {
    [
      { emaFast: 12, emaSlow: 50, emaTrend: 100, cooldownBars: 2, minHoldBars: 1, atrMultiplier: 1.8 },
      { emaFast: 20, emaSlow: 50, emaTrend: 150, cooldownBars: 3, minHoldBars: 2, atrMultiplier: 1.8 },
      { emaFast: 20, emaSlow: 80, emaTrend: 200, cooldownBars: 4, minHoldBars: 2, atrMultiplier: 2.2 },
      { emaFast: 12, emaSlow: 80, emaTrend: 200, cooldownBars: 3, minHoldBars: 1, atrMultiplier: 2.2 }
    ].forEach((patch, index) => rows.push(presetSpec("fast_native", "fast_native_" + timeframe + "_" + (index + 1), timeframe, Object.assign({}, base, patch))));
  });
  ["1h", "4h"].filter((timeframe) => timeframes.includes(timeframe)).forEach((timeframe) => {
    [
      { emaFast: 20, emaSlow: 80, emaTrend: 150, atrMultiplier: 1.8, cooldownBars: 4, minHoldBars: 2 },
      { emaFast: 30, emaSlow: 80, emaTrend: 200, atrMultiplier: 2.2, cooldownBars: 6, minHoldBars: 3 },
      { emaFast: 50, emaSlow: 100, emaTrend: 200, atrMultiplier: 2.6, cooldownBars: 8, minHoldBars: 4 },
      { emaFast: 30, emaSlow: 100, emaTrend: 200, atrMultiplier: 1.8, cooldownBars: 6, minHoldBars: 3 }
    ].forEach((patch, index) => rows.push(presetSpec("swing_native", "swing_native_" + timeframe + "_" + (index + 1), timeframe, Object.assign({}, base, patch))));
  });
  return rows;
}

function classify(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK") return "PASS";
  if (row.trades >= 20 && row.totalReturnPct >= 0 && row.profitFactor >= 1) return "WARN";
  if (reason === "NO_TRADES") return "NO_TRADES";
  return "FAIL";
}

function compactResult(result, candidate, days) {
  const trades = Number(result.trades || 0);
  const row = {
    symbol: candidate.symbol,
    timeframe: candidate.timeframe,
    strategy: candidate.strategy,
    trades,
    tradesPerMonth: round(trades / Math.max(1, days) * 30, 2),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    avgBarsHeld: round(result.avgBarsHeld || 0, 4),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades, 4) : 0,
    status: "FAIL",
    mainFailureReason: null,
    warnings: result.warnings || []
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  return row;
}

function run(candidate, candles, regimeCandles, costs) {
  return backtest.runBacktestOnCandles({
    source: candidate.source,
    symbol: candidate.symbol,
    interval: candidate.timeframe,
    strategy: candidate.strategy,
    candles,
    regimeCandles,
    params: Object.assign({}, candidate.params, costs),
    feePct: costs.feePct,
    slippagePct: costs.slippagePct
  });
}

function stressRows(candidate, candles, regimeCandles, costs, days) {
  const scenarios = [
    { scenario: "baseline", feePct: costs.feePct, slippagePct: costs.slippagePct },
    { scenario: "doubleSlippage", feePct: costs.feePct, slippagePct: costs.slippagePct * 2 },
    { scenario: "doubleFees", feePct: costs.feePct * 2, slippagePct: costs.slippagePct },
    { scenario: "highStress", feePct: costs.feePct * 2, slippagePct: costs.slippagePct * 3 }
  ];
  const rows = scenarios.map((scenario) => Object.assign(
    { scenario: scenario.scenario, feePct: round(scenario.feePct, 6), slippagePct: round(scenario.slippagePct, 6) },
    compactResult(run(candidate, candles, regimeCandles, scenario), candidate, days)
  ));
  const failed = rows.filter((row) => !["PASS", "WARN"].includes(row.status));
  return {
    status: failed.length ? "WATCH" : "RESILIENT",
    baseline: rows.find((row) => row.scenario === "baseline") || null,
    failedScenarios: failed.map((row) => row.scenario),
    rows
  };
}

function foldSlices(candles, folds) {
  const count = Math.max(1, Math.min(Number(folds || 4), 12));
  const size = Math.max(1, Math.floor(candles.length / count));
  const out = [];
  for (let i = 0; i < count; i += 1) {
    const start = i * size;
    const end = i === count - 1 ? candles.length : Math.min(candles.length, (i + 1) * size);
    const slice = candles.slice(start, end);
    if (slice.length) out.push({ fold: i + 1, candles: slice });
  }
  return out;
}

function walkForward(candidate, candles, regimeCandles, costs, days) {
  const folds = foldSlices(candles, 4).map((item) => Object.assign({
    fold: item.fold,
    startTime: item.candles.length ? new Date(item.candles[0].time * 1000).toISOString() : null,
    endTime: item.candles.length ? new Date(item.candles[item.candles.length - 1].time * 1000).toISOString() : null
  }, compactResult(run(candidate, item.candles, regimeCandles, costs), candidate, days)));
  const passFoldCount = folds.filter((row) => ["PASS", "WARN"].includes(row.status)).length;
  const failFoldCount = folds.length - passFoldCount;
  const negativeFoldCount = folds.filter((row) => row.totalReturnPct < 0).length;
  const status = failFoldCount > passFoldCount || negativeFoldCount > Math.floor(folds.length / 2)
    ? "FRAGILE"
    : failFoldCount || negativeFoldCount ? "WATCH" : "STABLE";
  return { status, passFoldCount, failFoldCount, negativeFoldCount, folds };
}

function percentile(values, ratio) {
  const nums = values.map(Number).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!nums.length) return 0;
  return nums[Math.max(0, Math.min(nums.length - 1, Math.floor((nums.length - 1) * ratio)))];
}

function nearestFrameRow(frame, time) {
  let chosen = null;
  for (let i = 0; i < frame.length; i += 1) {
    if (Number(frame[i].time) > Number(time)) break;
    chosen = frame[i];
  }
  return chosen || frame[0] || null;
}

function regimeSummary(candidate, candles, regimeCandles, costs) {
  const result = run(candidate, candles, regimeCandles, costs);
  const frame = indicators.buildIndicatorFrame(candles, candidate.params);
  const atrValues = frame.map((row) => row.atrPct);
  const thresholds = { low: percentile(atrValues, 0.33), high: percentile(atrValues, 0.66) };
  const buckets = {};
  (result.tradeList || []).forEach((trade) => {
    const row = nearestFrameRow(frame, trade.entryTime) || {};
    const index = Number(row.__index || 0);
    const prior = frame[Math.max(0, index - 24)] || {};
    const slopePct = prior.ema50 ? (row.ema50 - prior.ema50) / prior.ema50 * 100 : 0;
    const trend = row.ema50 && row.ema200 && row.ema50 > row.ema200 && slopePct > 0.15 ? "uptrend" : row.ema50 && row.ema200 && row.ema50 < row.ema200 && slopePct < -0.15 ? "downtrend" : "sideways";
    const vol = Number(row.atrPct) <= thresholds.low ? "lowVol" : Number(row.atrPct) >= thresholds.high ? "highVol" : "mediumVol";
    const momentum = (row.rsi14 || 0) >= 55 ? "bullish" : (row.rsi14 || 0) <= 45 ? "bearish" : "neutral";
    const key = [trend, vol, momentum].join("_");
    buckets[key] = buckets[key] || { regime: key, trades: 0, returnPct: 0 };
    buckets[key].trades += 1;
    buckets[key].returnPct += Number(trade.returnPct || 0);
  });
  const regimes = Object.keys(buckets).map((key) => ({
    regime: key,
    trades: buckets[key].trades,
    totalReturnPct: round(buckets[key].returnPct, 4)
  })).sort((a, b) => b.totalReturnPct - a.totalReturnPct);
  const enough = regimes.filter((row) => row.trades >= 5);
  const negative = enough.filter((row) => row.totalReturnPct < 0);
  const status = enough.length < 2 ? "UNKNOWN" : negative.length >= Math.ceil(enough.length / 2) ? "HIGH" : negative.length ? "MEDIUM" : "LOW";
  return {
    regimeDependencyStatus: status,
    bestRegime: regimes[0] || null,
    worstRegime: regimes.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct)[0] || null,
    regimeCount: regimes.length
  };
}

function candidateScore(activity, stress, wf, regime) {
  let score = 0;
  score += activity.status === "PASS" ? 30 : activity.status === "WARN" ? 12 : -25;
  score += Math.min(activity.trades, 150) * 0.1;
  score += Math.min(activity.tradesPerMonth, 12) * 1.8;
  score += activity.profitFactor * 16;
  score += activity.totalReturnPct * 2;
  score -= activity.maxDrawdownPct * 1.3;
  if (activity.trades < 40) score -= 8;
  if (activity.tradesPerMonth < 4) score -= 10;
  if (stress.status !== "RESILIENT") score -= 8;
  if (wf.status === "WATCH") score -= 8;
  if (wf.status === "FRAGILE") score -= 18;
  if (regime.regimeDependencyStatus === "MEDIUM") score -= 6;
  if (regime.regimeDependencyStatus === "HIGH") score -= 14;
  return round(score, 4);
}

function diff(a, b) {
  return round(Number(b || 0) - Number(a || 0), 4);
}

function compare(baseline, challenger) {
  const metricDiffs = {
    returnPct: diff(baseline.activity.totalReturnPct, challenger.activity.totalReturnPct),
    profitFactor: diff(baseline.activity.profitFactor, challenger.activity.profitFactor),
    maxDrawdownPct: diff(baseline.activity.maxDrawdownPct, challenger.activity.maxDrawdownPct),
    trades: diff(baseline.activity.trades, challenger.activity.trades),
    tradesPerMonth: diff(baseline.activity.tradesPerMonth, challenger.activity.tradesPerMonth)
  };
  const scoreDiff = round(challenger.score - baseline.score, 4);
  const blockers = [];
  if (challenger.activity.trades < 40) blockers.push("challenger has fewer than 40 backtest trades");
  if (challenger.activity.tradesPerMonth < 4) blockers.push("challenger trades less often than the active 1h baseline");
  if (challenger.walkForward.status !== "STABLE" && challenger.walkForward.status !== "WATCH") blockers.push("challenger walk-forward is weak");
  if (challenger.stress.status !== "RESILIENT") blockers.push("challenger does not survive all bounded cost stress checks");
  let winner = "NO_DECISION";
  if (scoreDiff > 10 && !blockers.length) winner = "CHALLENGER";
  else if (scoreDiff < -6 || blockers.length) winner = "BASELINE";
  else winner = "TIE";
  const tradeoffSummary = `Challenger return ${metricDiffs.returnPct >= 0 ? "+" : ""}${metricDiffs.returnPct}% vs baseline, PF ${metricDiffs.profitFactor >= 0 ? "+" : ""}${metricDiffs.profitFactor}, trades ${metricDiffs.trades >= 0 ? "+" : ""}${metricDiffs.trades}, trades/month ${metricDiffs.tradesPerMonth >= 0 ? "+" : ""}${metricDiffs.tradesPerMonth}.`;
  return {
    winner,
    reason: blockers.length ? blockers.join("; ") : winner === "CHALLENGER" ? "Challenger clears the conservative evidence gates in this read-only comparison." : winner === "TIE" ? "The candidates are close after conservative evidence penalties." : "Baseline remains stronger after conservative evidence penalties.",
    scoreDiff,
    metricDiffs,
    tradeoffSummary
  };
}

const days = periodDays(args.period);
const source = args.source || "bybit";
const baseParams = args.baseParams ? JSON.parse(args.baseParams) : {};
const costs = {
  feePct: Number(args.feePct || args["fee-pct"] || 0.055),
  slippagePct: Number(args.slippagePct || args["slippage-pct"] || 0.02)
};
const baseline = {
  role: "baseline",
  source,
  symbol: args.baselineSymbol || "ETHUSDT",
  timeframe: args.baselineTimeframe || args.baselineInterval || "1h",
  strategy: args.baselineStrategy || "SimpleAtrTrendV2",
  presetName: "active_paper_candidate",
  params: baseParams
};
const challengerPreset = args.challengerPreset || "swing_native_4h_1";
const challengerTimeframe = args.challengerTimeframe || "4h";
const availablePresets = generatePresets(baseParams, ["15m", "1h", "4h"]);
const preset = availablePresets.find((row) => row.presetName === challengerPreset && row.timeframe === challengerTimeframe)
  || availablePresets.find((row) => row.presetName === challengerPreset);
if (!preset) {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: "Unknown challengerPreset.",
    availablePresets: availablePresets.map((row) => ({ presetFamily: row.presetFamily, presetName: row.presetName, timeframe: row.timeframe })),
    warnings: ["No comparison was run because the challenger preset could not be resolved."]
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
} else {
  const challenger = {
    role: "challenger",
    source,
    symbol: args.challengerSymbol || "ETHUSDT",
    timeframe: args.challengerTimeframe || preset.timeframe,
    strategy: args.challengerStrategy || "SimpleAtrTrendV2",
    presetFamily: preset.presetFamily,
    presetName: preset.presetName,
    params: preset.params
  };
  const from = args.from || argsUtil.daysToFrom(days);
  const to = args.to || new Date().toISOString();
  const explicitLimit = args.limit && args.limit !== "auto" ? Number(args.limit) : null;
  Promise.all([
    data.fetchCandles({ source, symbol: baseline.symbol, interval: baseline.timeframe, from, to, limit: explicitLimit || autoLimitFor(baseline.timeframe, days, 5000) }).then((candles) => data.normalizeCandles(candles || [])),
    data.fetchCandles({ source, symbol: challenger.symbol, interval: challenger.timeframe, from, to, limit: explicitLimit || autoLimitFor(challenger.timeframe, days, 5000) }).then((candles) => data.normalizeCandles(candles || [])),
    data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: explicitLimit || autoLimitFor("4h", days, 3000) }).then((candles) => data.normalizeCandles(candles || []))
  ]).then(([baselineCandles, challengerCandles, regimeCandles]) => {
    const baselineActivity = compactResult(run(baseline, baselineCandles, regimeCandles, costs), baseline, days);
    const challengerActivity = compactResult(run(challenger, challengerCandles, regimeCandles, costs), challenger, days);
    const baselineStress = stressRows(baseline, baselineCandles, regimeCandles, costs, days);
    const challengerStress = stressRows(challenger, challengerCandles, regimeCandles, costs, days);
    const baselineWalk = walkForward(baseline, baselineCandles, regimeCandles, costs, days);
    const challengerWalk = walkForward(challenger, challengerCandles, regimeCandles, costs, days);
    const baselineRegime = regimeSummary(baseline, baselineCandles, regimeCandles, costs);
    const challengerRegime = regimeSummary(challenger, challengerCandles, regimeCandles, costs);
    const baselinePack = { activity: baselineActivity, stress: baselineStress, walkForward: baselineWalk, regime: baselineRegime };
    const challengerPack = { activity: challengerActivity, stress: challengerStress, walkForward: challengerWalk, regime: challengerRegime };
    baselinePack.score = candidateScore(baselineActivity, baselineStress, baselineWalk, baselineRegime);
    challengerPack.score = candidateScore(challengerActivity, challengerStress, challengerWalk, challengerRegime);
    const comparison = compare(baselinePack, challengerPack);
    const recommendation = comparison.winner === "CHALLENGER"
      ? { action: "REVIEW_CHALLENGER", reason: "The challenger is promising enough for research review only. Do not promote automatically." }
      : comparison.winner === "TIE"
        ? { action: "RESEARCH_MORE", reason: "The challenger is interesting but not clearly stronger after conservative penalties." }
        : { action: "KEEP_BASELINE", reason: "The active 1h baseline remains the safer candidate after deep comparison." };
    process.stdout.write(JSON.stringify({
      ok: true,
      search: {
        period: args.period || "365d",
        includeDetails: String(args.includeDetails === undefined ? "true" : args.includeDetails).toLowerCase() !== "false"
      },
      baseline: Object.assign({}, baseline, { score: baselinePack.score }),
      challenger: Object.assign({}, challenger, { score: challengerPack.score }),
      comparison,
      evidence: {
        activity: { baseline: baselineActivity, challenger: challengerActivity },
        stress: { baseline: baselineStress, challenger: challengerStress },
        walkForward: { baseline: baselineWalk, challenger: challengerWalk },
        regime: { baseline: baselineRegime, challenger: challengerRegime },
        robustness: {
          baseline: null,
          challenger: null,
          warning: "Parameter robustness is intentionally omitted from this bounded read-only deep compare; use /api/research/parameter-robustness for the active candidate."
        },
        blockerAnalytics: {
          baseline: null,
          challenger: null,
          warning: "Blocker analytics is not rerun here because this comparison focuses on completed trade evidence."
        }
      },
      recommendation,
      warnings: ["No promotion, paper tick, config write, or real trading action was performed."]
    }, null, 2));
    runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
  }).catch((error) => {
    process.stderr.write(error.stack || error.message);
    runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
  });
}
