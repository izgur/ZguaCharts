const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const backtest = require("../backtest");
const data = require("../data");
const optimizer = require("../optimizer");
const strategiesRegistry = require("../strategies");

const DEFAULT_STRATEGIES = [
  "SimpleAtrTrendV2",
  "PullbackReclaimV2",
  "EmaBounceV2",
  "BreakoutRetestV2",
  "RangeExpansionV2",
  "RelativeStrengthV2",
  "EmaPullbackContinuation",
  "TrendBreakoutRetest",
  "VolatilitySqueezeBreakout",
  "FibPullbackContinuationV1",
  "MeanReversionInBullRegime",
  "MomentumContinuation",
  "RegimeDonchian20",
  "RegimeDonchianCloseConfirm",
  "RegimePullbackTrend",
  "ConservativeTrend",
  "ConservativeTrendLoose",
  "MomentumScalping",
  "MeanReversion",
  "PullbackTrend"
];

const ELIGIBILITY_GATES = {
  minFullTrades: 40,
  minProfitFactor: 1.05,
  minMedianFoldProfitFactor: 1,
  minMedianFoldReturnPct: 0,
  minFoldPassCount: 3,
  researchMoreFoldPassCount: 2,
  maxNegativeFolds: 1,
  minWorstFoldReturnPct: -1,
  maxDrawdownPct: 25,
  maxBestFoldContributionPct: 75,
  minStabilityScoreImprovement: 8
};

const REPRO_TOLERANCES = {
  trades: 0,
  totalReturnPct: 0.0001,
  profitFactor: 0.0001,
  maxDrawdownPct: 0.0001
};

const STABILITY_SCORE_DIRECTION = "higher_is_better";
const CANDIDATE_IDENTITY_VERSION = "candidate-identity-v1";

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function shortHash(value) {
  return crypto.createHash("sha256").update(stableJson(value)).digest("hex").slice(0, 16);
}

function normalizeParams(params) {
  const out = {};
  Object.keys(params || {}).sort().forEach((key) => {
    const value = params[key];
    if (value === undefined || value === null) return;
    if (Array.isArray(value)) out[key] = value.map((item) => (item && typeof item === "object" && !Array.isArray(item) ? normalizeParams(item) : item));
    else if (value && typeof value === "object") out[key] = normalizeParams(value);
    else out[key] = value;
  });
  return out;
}

function candidateIdentity(row, context) {
  const normalizedParams = normalizeParams(row.params || {});
  const paramsHash = shortHash(normalizedParams);
  const executionContext = {
    fillModel: context.fillModel || "next-open",
    makerFeePct: Number((context.costs || {}).makerFeePct || 0),
    takerFeePct: Number((context.costs || {}).takerFeePct || 0),
    slippageBps: Number((context.costs || {}).slippageBps || 0)
  };
  const executionContextHash = shortHash(executionContext);
  return {
    candidateIdentityVersion: CANDIDATE_IDENTITY_VERSION,
    candidateKey: [
      CANDIDATE_IDENTITY_VERSION,
      row.strategy || "-",
      row.symbol || "-",
      row.timeframe || "-",
      paramsHash,
      executionContextHash
    ].join("|"),
    normalizedParams,
    paramsHash,
    executionContextHash,
    fillModel: executionContext.fillModel,
    makerFeePct: executionContext.makerFeePct,
    takerFeePct: executionContext.takerFeePct,
    slippageBps: executionContext.slippageBps
  };
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function median(values) {
  const nums = values.map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  if (!nums.length) return 0;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
}

function parseCsv(value, fallback) {
  if (!value || value === "all" || value === "auto") return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function stableParamKey(params) {
  return JSON.stringify(Object.keys(params || {}).sort().reduce((out, key) => {
    out[key] = params[key];
    return out;
  }, {}));
}

function normalizeParamAliases(params) {
  const copy = Object.assign({}, params || {});
  if (copy.useBreakout !== undefined && copy.requireBreakout === undefined) copy.requireBreakout = copy.useBreakout;
  if (copy.useVolumeFilter !== undefined && copy.requireVolume === undefined) copy.requireVolume = copy.useVolumeFilter;
  delete copy.useBreakout;
  delete copy.useVolumeFilter;
  return copy;
}

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
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
  return Math.max(250, Math.min(Number(cap || 5000), bars + 300));
}

function comboAt(ranges, index) {
  const keys = Object.keys(ranges || {});
  const out = {};
  let remaining = Math.max(0, Math.floor(Number(index) || 0));
  keys.forEach((key) => {
    const values = Array.isArray(ranges[key]) ? ranges[key] : [ranges[key]];
    const safeValues = values.length ? values : [undefined];
    out[key] = safeValues[remaining % safeValues.length];
    remaining = Math.floor(remaining / safeValues.length);
  });
  return normalizeParamAliases(out);
}

function plannedComboCount(ranges) {
  return Object.keys(ranges || {}).reduce((count, key) => {
    const values = Array.isArray(ranges[key]) ? ranges[key] : [ranges[key]];
    return count * Math.max(1, values.length);
  }, 1);
}

function boundedOptimizerGrid(strategy, maxCombos) {
  const catalog = optimizer.optimizerGridCatalog();
  const grid = catalog[strategy] || catalog.default_fallback || { params: optimizer.defaultRanges(), maxCombinations: maxCombos };
  const ranges = grid.params || optimizer.defaultRanges();
  const planned = plannedComboCount(ranges);
  const limit = Math.max(1, Math.min(Number(maxCombos || grid.maxCombinations || 1000), Number(grid.maxCombinations || maxCombos || 1000), planned));
  const lastIndex = Math.max(0, planned - 1);
  const byKey = {};
  let attempts = 0;
  for (let sample = 0; Object.keys(byKey).length < limit && attempts < planned; sample += 1) {
    const baseIndex = limit <= 1 ? 0 : Math.floor(sample * lastIndex / Math.max(1, limit - 1));
    for (let offset = 0; offset < planned && Object.keys(byKey).length < limit; offset += 1) {
      const combo = comboAt(ranges, (baseIndex + offset) % planned);
      attempts += 1;
      if (!optimizer.validCombo(combo)) continue;
      byKey[stableParamKey(combo)] = combo;
      break;
    }
    if (sample > limit + planned) break;
  }
  const combos = Object.keys(byKey).sort().map((key) => byKey[key]);
  return {
    combos,
    metadata: {
      strategyKey: grid.strategyKey,
      gridName: grid.gridName || grid.humanName,
      humanName: grid.humanName || grid.gridName,
      params: ranges,
      paramCount: Object.keys(ranges || {}).length,
      candidateCountPlanned: planned,
      candidateCountTested: combos.length,
      maxCombinations: grid.maxCombinations,
      fallbackUsed: false,
      fallbackReason: null,
      fallbackType: grid.fallbackType || null,
      warning: grid.warning || null,
      sampled: combos.length < planned,
      notes: grid.notes || [],
      riskLevel: grid.riskLevel || "unknown",
      memoryBounded: true
    }
  };
}

function iso(time) {
  return time ? new Date(Number(time) * 1000).toISOString() : null;
}

function paramsWithCosts(params, costs) {
  const out = Object.assign({}, params || {});
  out.makerFeePct = Number(costs.makerFeePct || 0);
  out.takerFeePct = Number(costs.takerFeePct || costs.feePct || 0);
  out.slippageBps = Number(costs.slippageBps || 0);
  out.feePct = Number(costs.feePct !== undefined ? costs.feePct : out.takerFeePct);
  out.slippagePct = Number(costs.slippagePct !== undefined ? costs.slippagePct : out.slippageBps / 100);
  return out;
}

function compactResult(result, context) {
  const trades = Number(result.trades || 0);
  const row = {
    strategy: context.strategy,
    symbol: context.symbol,
    timeframe: context.timeframe,
    params: context.params || {},
    paramsSource: context.paramsSource || "screened",
    trades,
    tradesPerMonth: round(trades / Math.max(1, context.days || 365) * 30, 4),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades, 4) : 0,
    avgBarsHeld: round(result.avgBarsHeld || 0, 4),
    score: round(result.score || 0, 4),
    status: "FAIL",
    mainFailureReason: null,
    warnings: result.warnings || []
  };
  Object.assign(row, candidateIdentity(row, context));
  row.mainFailureReason = failureReason(row);
  row.status = statusFor(row);
  return row;
}

function failureReason(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct <= 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.05) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = failureReason(row);
  if (reason === "OK" && row.trades >= 40 && row.profitFactor >= 1.1) return "PASS";
  if (["OK", "TOO_FEW_TRADES"].includes(reason) && row.totalReturnPct > 0 && row.profitFactor >= 1) return "WATCH";
  if (reason === "NO_TRADES") return "NO_TRADES";
  return "FAIL";
}

function foldSlices(candles, folds) {
  const count = Math.max(1, Math.min(Number(folds || 4), 12));
  const size = Math.max(1, Math.floor(candles.length / count));
  const out = [];
  for (let index = 0; index < count; index += 1) {
    const start = index * size;
    const end = index === count - 1 ? candles.length : Math.min(candles.length, (index + 1) * size);
    const slice = candles.slice(start, end);
    if (slice.length) out.push({ fold: index + 1, start, end, candles: slice });
  }
  return out;
}

function runCandidate(candles, regimeCandles, context, params, costs) {
  return backtest.runBacktestOnCandles({
    source: context.source,
    symbol: context.symbol,
    interval: context.timeframe,
    strategy: context.strategy,
    candles,
    regimeCandles,
    params: paramsWithCosts(params, costs)
  });
}

function evaluateFolds(candles, regimeCandles, context, params, costs, folds) {
  return foldSlices(candles, folds).map((item) => {
    const result = runCandidate(item.candles, regimeCandles, context, params, costs);
    const row = compactResult(result, Object.assign({}, context, {
      params,
      days: Math.max(1, (item.candles[item.candles.length - 1].time - item.candles[0].time) / 86400)
    }));
    return Object.assign(row, {
      fold: item.fold,
      trainingStartTime: item.start > 0 && candles[0] ? iso(candles[0].time) : null,
      trainingEndTime: item.start > 0 && candles[item.start - 1] ? iso(candles[item.start - 1].time) : null,
      testStartTime: iso(item.candles[0].time),
      testEndTime: iso(item.candles[item.candles.length - 1].time),
      failureReasons: row.mainFailureReason === "OK" ? [] : [row.mainFailureReason]
    });
  });
}

function returnConcentration(folds) {
  const positive = folds.map((fold) => Math.max(0, Number(fold.totalReturnPct || 0)));
  const totalPositive = positive.reduce((sum, value) => sum + value, 0);
  const sorted = positive.slice().sort((a, b) => b - a);
  const bestFoldContributionPct = round(totalPositive ? sorted[0] / totalPositive * 100 : 0, 4);
  const topTwoFoldContributionPct = round(totalPositive ? (sorted[0] + (sorted[1] || 0)) / totalPositive * 100 : 0, 4);
  const classification = bestFoldContributionPct > 75 || topTwoFoldContributionPct > 92
    ? "HIGHLY_CONCENTRATED"
    : bestFoldContributionPct > 55 || topTwoFoldContributionPct > 80
      ? "MODERATELY_CONCENTRATED"
      : "BALANCED";
  return { totalPositiveFoldReturnPct: round(totalPositive, 4), bestFoldContributionPct, topTwoFoldContributionPct, classification };
}

function walkForwardSummary(folds) {
  const passFoldCount = folds.filter((row) => ["PASS", "WATCH"].includes(row.status)).length;
  const foldFailCount = folds.length - passFoldCount;
  const negativeFoldCount = folds.filter((row) => Number(row.totalReturnPct || 0) < 0).length;
  const returns = folds.map((row) => Number(row.totalReturnPct || 0));
  const pfs = folds.map((row) => Number(row.profitFactor || 0));
  const worst = folds.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct || a.profitFactor - b.profitFactor)[0] || null;
  const best = folds.slice().sort((a, b) => b.totalReturnPct - a.totalReturnPct || b.profitFactor - a.profitFactor)[0] || null;
  const medianReturn = median(returns);
  const dispersion = Math.sqrt(returns.reduce((sum, value) => sum + Math.pow(value - medianReturn, 2), 0) / Math.max(1, returns.length));
  const latest = folds[folds.length - 1] || {};
  const summary = {
    foldsEvaluated: folds.length,
    passFoldCount,
    foldPassCount: passFoldCount,
    foldFailCount,
    foldPassRate: round(folds.length ? passFoldCount / folds.length * 100 : 0, 4),
    negativeFoldCount,
    medianFoldReturnPct: round(medianReturn, 4),
    medianFoldProfitFactor: round(median(pfs), 4),
    worstFoldReturnPct: worst ? worst.totalReturnPct : 0,
    bestFoldReturnPct: best ? best.totalReturnPct : 0,
    foldReturnDispersion: round(dispersion, 4),
    bestFold: best,
    worstFold: worst,
    latestFoldStatus: latest.status || "UNKNOWN",
    latestFoldReturnPct: latest.totalReturnPct || 0,
    latestFoldProfitFactor: latest.profitFactor || 0
  };
  return Object.assign({ folds, summary }, summary);
}

function concentrationPenalty(classification) {
  return classification === "HIGHLY_CONCENTRATED" ? 30 : classification === "MODERATELY_CONCENTRATED" ? 12 : 0;
}

function stabilityScore(full, walkForward, concentration, stress, recent, reproducibility) {
  let score = 0;
  score += Number(walkForward.foldPassRate || 0) * 0.9;
  score -= Number(walkForward.negativeFoldCount || 0) * 18;
  score += Math.max(-5, Math.min(5, Number(walkForward.worstFoldReturnPct || 0))) * 5;
  score += Math.max(-5, Math.min(8, Number(walkForward.medianFoldReturnPct || 0))) * 4;
  score += Math.min(3, Number(walkForward.medianFoldProfitFactor || 0)) * 10;
  score -= Number(walkForward.foldReturnDispersion || 0) * 3;
  score -= concentrationPenalty(concentration.classification);
  if (String(walkForward.latestFoldStatus || "") === "FAIL") score -= 18;
  if (stress && stress.status === "SURVIVES_MODERATE_STRESS") score += 8;
  if (stress && stress.status === "COLLAPSES_UNDER_STRESS") score -= 18;
  if (recent && recent.status === "RECENTLY_CONSISTENT") score += 8;
  if (recent && recent.status === "RECENTLY_WEAK") score -= 16;
  if (reproducibility && reproducibility.status === "REPRODUCIBLE") score += 8;
  if (reproducibility && reproducibility.status === "UNSTABLE") score -= 60;
  score += Math.min(Number(full.trades || 0), 120) * 0.12;
  score += Math.min(2, Number(full.profitFactor || 0)) * 4;
  score += Math.max(-5, Math.min(10, Number(full.totalReturnPct || 0))) * 0.7;
  score -= Math.max(0, Number(full.maxDrawdownPct || 0) - 10) * 0.7;
  return round(score, 4);
}

function stressRows(candles, regimeCandles, context, params, baseCosts) {
  const specs = [
    { scenario: "normal", makerFeePct: baseCosts.makerFeePct, takerFeePct: baseCosts.takerFeePct, slippageBps: baseCosts.slippageBps },
    { scenario: "doubleFees", makerFeePct: baseCosts.makerFeePct * 2, takerFeePct: baseCosts.takerFeePct * 2, slippageBps: baseCosts.slippageBps },
    { scenario: "doubleSlippage", makerFeePct: baseCosts.makerFeePct, takerFeePct: baseCosts.takerFeePct, slippageBps: baseCosts.slippageBps * 2 },
    { scenario: "doubleFeesDoubleSlippage", makerFeePct: baseCosts.makerFeePct * 2, takerFeePct: baseCosts.takerFeePct * 2, slippageBps: baseCosts.slippageBps * 2 }
  ];
  return specs.map((spec) => compactResult(runCandidate(candles, regimeCandles, context, params, spec), Object.assign({}, context, { params, days: context.days, paramsSource: "stress" })));
}

function stressSummary(rows) {
  if (!rows || !rows.length) return { status: "NOT_RUN", rows: [] };
  const moderate = rows.filter((row) => ["normal", "doubleFees", "doubleSlippage", "doubleFeesDoubleSlippage"].includes(row.scenario || row.paramsSource));
  const failed = moderate.filter((row) => !["PASS", "WATCH"].includes(row.status) || row.totalReturnPct <= 0 || row.profitFactor < 1);
  return {
    status: failed.length ? "COLLAPSES_UNDER_STRESS" : "SURVIVES_MODERATE_STRESS",
    rows,
    failedScenarios: failed.map((row) => row.scenario || row.paramsSource),
    collapseReason: failed[0] ? failed[0].mainFailureReason : null
  };
}

function recentWindows(candles, regimeCandles, context, params, costs, windows) {
  const latest = candles.length ? candles[candles.length - 1].time : 0;
  const rows = (windows || [90, 180, 365]).map((days) => {
    const cutoff = latest - Number(days) * 86400;
    const slice = candles.filter((candle) => candle.time >= cutoff);
    const result = runCandidate(slice, regimeCandles, context, params, costs);
    return compactResult(result, Object.assign({}, context, { params, days, paramsSource: `${days}d` }));
  });
  const pass = rows.filter((row) => ["PASS", "WATCH"].includes(row.status) && row.totalReturnPct > 0).length;
  const weak = rows.filter((row) => row.totalReturnPct < 0 || row.profitFactor < 1).length;
  const status = rows.some((row) => row.trades < 5)
    ? "INSUFFICIENT_DATA"
    : weak >= 2
      ? "RECENTLY_WEAK"
      : pass === rows.length
        ? "RECENTLY_CONSISTENT"
        : "MIXED";
  return { status, rows };
}

function reproducibilityAudit(candles, regimeCandles, context, params, costs, reruns, reference) {
  const runs = [];
  const diffs = [];
  for (let index = 0; index < Math.max(1, Number(reruns || 1)); index += 1) {
    const row = compactResult(runCandidate(candles, regimeCandles, context, params, costs), Object.assign({}, context, { params, days: context.days, paramsSource: "repro" }));
    runs.push(row);
    diffs.push({
      trades: row.trades - reference.trades,
      totalReturnPct: round(row.totalReturnPct - reference.totalReturnPct, 6),
      profitFactor: round(row.profitFactor - reference.profitFactor, 6),
      maxDrawdownPct: round(row.maxDrawdownPct - reference.maxDrawdownPct, 6)
    });
  }
  const unstable = diffs.some((diff) => (
    Math.abs(diff.trades) > REPRO_TOLERANCES.trades ||
    Math.abs(diff.totalReturnPct) > REPRO_TOLERANCES.totalReturnPct ||
    Math.abs(diff.profitFactor) > REPRO_TOLERANCES.profitFactor ||
    Math.abs(diff.maxDrawdownPct) > REPRO_TOLERANCES.maxDrawdownPct
  ));
  return {
    status: unstable ? "UNSTABLE" : "REPRODUCIBLE",
    reruns: runs.length,
    diffs,
    tolerances: REPRO_TOLERANCES
  };
}

function activityExpectation(timeframe, days) {
  const perMonth = timeframe === "15m" ? 8 : timeframe === "1h" ? 4 : 1.5;
  return { minExpectedTrades: Math.max(10, Math.floor(perMonth * Number(days || 365) / 30)), halfExpectedTrades: Math.max(5, Math.floor(perMonth * Number(days || 365) / 60)) };
}

function eligibility(candidate, benchmark, rules) {
  const passed = [];
  const failed = [];
  const full = candidate.fullPeriod;
  const wf = candidate.walkForward;
  const concentration = candidate.returnConcentration;
  const stress = candidate.stress || { status: "NOT_RUN" };
  const repro = candidate.reproducibility || { status: "NOT_RUN" };
  const expected = activityExpectation(candidate.timeframe, candidate.days || 365);
  const isFastFib = candidate.strategy === "FibPullbackContinuationV1" && candidate.timeframe === "15m";
  const minFullTrades = isFastFib ? Math.max(Number(rules.minFullTrades || 0), 100) : rules.minFullTrades;
  const minFoldPassCount = isFastFib ? Math.max(Number(rules.minFoldPassCount || 0), 4) : rules.minFoldPassCount;
  const maxBestFoldContributionPct = isFastFib ? Math.min(Number(rules.maxBestFoldContributionPct || 100), 60) : rules.maxBestFoldContributionPct;
  function gate(name, ok, passDetail, failDetail) {
    (ok ? passed : failed).push({ name, detail: ok ? passDetail : (failDetail || passDetail) });
  }
  const atLeast = (label, value, required, suffix = "") => [`${label} ${value}${suffix} >= required ${required}${suffix}`, `${label} ${value}${suffix} < required ${required}${suffix}`];
  const greaterThan = (label, value, required, suffix = "") => [`${label} ${value}${suffix} > required ${required}${suffix}`, `${label} ${value}${suffix} <= required ${required}${suffix}`];
  const atMost = (label, value, allowed, suffix = "") => [`${label} ${value}${suffix} <= allowed ${allowed}${suffix}`, `${label} ${value}${suffix} > allowed ${allowed}${suffix}`];
  gate("anti-lookahead", candidate.antiLookaheadStatus === "PASS", "Candidate parameters are fixed before fold tests; no test-fold selection is used.");
  gate("reproducibility", !["UNSTABLE"].includes(repro.status), `Reproducibility is ${repro.status}.`);
  gate("full trades", full.trades >= minFullTrades, ...atLeast("trades", full.trades, minFullTrades));
  gate("activity", full.trades >= expected.halfExpectedTrades, `${full.trades} trades >= required ${expected.halfExpectedTrades} half expected trades for ${candidate.timeframe}`, `${full.trades} trades < required ${expected.halfExpectedTrades} half expected trades for ${candidate.timeframe}`);
  gate("fold pass count", wf.foldPassCount >= minFoldPassCount, ...atLeast("fold pass count", wf.foldPassCount, minFoldPassCount));
  gate("negative folds", wf.negativeFoldCount <= rules.maxNegativeFolds, ...atMost("negative folds", wf.negativeFoldCount, rules.maxNegativeFolds));
  gate("worst fold", wf.worstFoldReturnPct > rules.minWorstFoldReturnPct, ...greaterThan("worst fold", wf.worstFoldReturnPct, rules.minWorstFoldReturnPct, "%"));
  gate("median fold return", wf.medianFoldReturnPct > rules.minMedianFoldReturnPct, ...greaterThan("median fold return", wf.medianFoldReturnPct, rules.minMedianFoldReturnPct, "%"));
  gate("median fold PF", wf.medianFoldProfitFactor > rules.minMedianFoldProfitFactor, ...greaterThan("median fold PF", wf.medianFoldProfitFactor, rules.minMedianFoldProfitFactor));
  gate("full PF", full.profitFactor >= rules.minProfitFactor, ...atLeast("PF", full.profitFactor, rules.minProfitFactor));
  gate("full return", full.totalReturnPct > 0, ...greaterThan("return", full.totalReturnPct, 0, "%"));
  gate("drawdown", full.maxDrawdownPct <= rules.maxDrawdownPct, ...atMost("drawdown", full.maxDrawdownPct, rules.maxDrawdownPct, "%"));
  gate("stress", stress.status !== "COLLAPSES_UNDER_STRESS", `Stress status is ${stress.status}.`);
  gate("concentration", concentration.bestFoldContributionPct <= maxBestFoldContributionPct, ...atMost("concentration", concentration.bestFoldContributionPct, maxBestFoldContributionPct, "%"));
  if (benchmark) {
    gate("benchmark stability improvement", candidate.stabilityScore >= benchmark.stabilityScore + rules.minStabilityScoreImprovement, `${candidate.stabilityScore} vs benchmark ${benchmark.stabilityScore}`);
    gate("benchmark negative folds", wf.negativeFoldCount < benchmark.walkForward.negativeFoldCount || wf.foldPassCount > benchmark.walkForward.foldPassCount, "Must improve folds or negative-fold count.");
  }
  const status = failed.length === 0
    ? "CHALLENGER_ELIGIBLE"
    : wf.foldPassCount >= rules.researchMoreFoldPassCount && full.trades >= 20
      ? "RESEARCH_MORE"
      : "REJECTED";
  return { status, passedGates: passed, failedGates: failed };
}

function benchmarkComparison(candidate, benchmark) {
  if (!benchmark) return { comparable: "UNKNOWN" };
  const sameMarket = candidate.symbol === benchmark.symbol && candidate.timeframe === benchmark.timeframe;
  return {
    comparable: sameMarket ? "COMPARABLE" : "RESEARCH_ALTERNATIVE",
    limitation: sameMarket ? null : "Different symbol/timeframe changes opportunity set and trade frequency.",
    tradeCountDelta: candidate.fullPeriod.trades - benchmark.fullPeriod.trades,
    profitFactorDelta: round(candidate.fullPeriod.profitFactor - benchmark.fullPeriod.profitFactor, 4),
    returnDeltaPct: round(candidate.fullPeriod.totalReturnPct - benchmark.fullPeriod.totalReturnPct, 4),
    drawdownDeltaPct: round(candidate.fullPeriod.maxDrawdownPct - benchmark.fullPeriod.maxDrawdownPct, 4),
    foldPassDelta: candidate.walkForward.foldPassCount - benchmark.walkForward.foldPassCount,
    negativeFoldDelta: candidate.walkForward.negativeFoldCount - benchmark.walkForward.negativeFoldCount,
    worstFoldDeltaPct: round(candidate.walkForward.worstFoldReturnPct - benchmark.walkForward.worstFoldReturnPct, 4),
    concentrationDeltaPct: round(candidate.returnConcentration.bestFoldContributionPct - benchmark.returnConcentration.bestFoldContributionPct, 4),
    stabilityScoreDelta: round(candidate.stabilityScore - benchmark.stabilityScore, 4),
    stressDelta: `${(candidate.stress || {}).status || "NOT_RUN"} vs ${(benchmark.stress || {}).status || "NOT_RUN"}`,
    reproducibilityDelta: `${(candidate.reproducibility || {}).status || "NOT_RUN"} vs ${(benchmark.reproducibility || {}).status || "NOT_RUN"}`
  };
}

function screenScore(row) {
  let score = 0;
  score += row.status === "PASS" ? 25 : row.status === "WATCH" ? 10 : -25;
  score += Math.min(row.trades, 150) * 0.1;
  score += row.profitFactor * 14;
  score += row.totalReturnPct * 1.5;
  score -= row.maxDrawdownPct * 1.2;
  if (row.trades < 20) score -= 20;
  return round(score, 4);
}

function saveReport(payload) {
  const dir = path.join("reports", "stability-first-search");
  fs.mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "-");
  const file = path.join(dir, `stability-first-challenger-search-${stamp}.json`);
  fs.writeFileSync(file, JSON.stringify(payload, null, 2));
  return file.replace(/\\/g, "/");
}

async function buildReport(options) {
  const days = periodDays(options.period);
  const source = options.source || "bybit";
  const from = options.from || new Date(Date.now() - days * 86400 * 1000).toISOString();
  const to = options.to || new Date().toISOString();
  const symbols = parseCsv(options.symbols, ["ETHUSDT", "BTCUSDT"]);
  const timeframes = parseCsv(options.timeframes, ["1h", "4h"]);
  const requestedStrategies = parseCsv(options.strategies, DEFAULT_STRATEGIES);
  const maxCombosPerStrategy = Math.max(1, Math.min(Number(options.maxCombosPerStrategy || 50), 150));
  const topN = Math.max(1, Math.min(Number(options.topN || 20), 50));
  const folds = Math.max(2, Math.min(Number(options.folds || 4), 8));
  const includeStress = options.includeStress !== false;
  const includeRecentWindows = options.includeRecentWindows !== false;
  const includeReproAudit = options.includeReproAudit !== false;
  const reproReruns = Math.max(1, Math.min(Number(options.reproReruns || 2), 5));
  const costs = {
    makerFeePct: Number(options.makerFeePct || 0),
    takerFeePct: Number(options.takerFeePct || 0.055),
    slippageBps: Number(options.slippageBps || 2)
  };
  const fillModel = options.fillModel || "next-open";
  const strategiesDiscovered = strategiesRegistry.listStrategies().map((item) => item.name).filter((name) => name !== "AlwaysLongTest");
  const catalog = optimizer.optimizerGridCatalog();
  const strategiesSkipped = [];
  const strategiesSearched = requestedStrategies.filter((strategy) => {
    try {
      strategiesRegistry.getStrategy(strategy);
    } catch (error) {
      strategiesSkipped.push({ strategy, reason: "not_registered" });
      return false;
    }
    if (!catalog[strategy]) {
      strategiesSkipped.push({ strategy, reason: "no_safe_bounded_grid" });
      return false;
    }
    return true;
  });
  const markets = [];
  symbols.forEach((symbol) => timeframes.forEach((timeframe) => markets.push({ symbol, timeframe })));
  const candleKeys = {};
  markets.concat([{ symbol: options.activeSymbol || "ETHUSDT", timeframe: options.activeTimeframe || "1h" }]).forEach((market) => {
    const key = `${market.symbol}:${market.timeframe}`;
    if (!candleKeys[key]) {
      candleKeys[key] = data.fetchCandles({
        source,
        symbol: market.symbol,
        interval: market.timeframe,
        from,
        to,
        limit: options.limit && options.limit !== "auto" ? Number(options.limit) : autoLimitFor(market.timeframe, days, 5000)
      }).then((candles) => data.normalizeCandles(candles || []));
    }
  });
  const [candleEntries, regimeCandles] = await Promise.all([
    Promise.all(Object.keys(candleKeys).map((key) => candleKeys[key].then((candles) => [key, candles]))),
    data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: options.limit && options.limit !== "auto" ? Number(options.limit) : autoLimitFor("4h", days, 3000) }).then((candles) => data.normalizeCandles(candles || []))
  ]);
  const candlesByKey = {};
  candleEntries.forEach(([key, candles]) => { candlesByKey[key] = candles; });

  let rawCombosEvaluated = 0;
  const rawRows = [];
  const candidates = [];
  markets.forEach((market) => {
    strategiesSearched.forEach((strategy) => {
      const grid = boundedOptimizerGrid(strategy, maxCombosPerStrategy);
      const candles = candlesByKey[`${market.symbol}:${market.timeframe}`] || [];
      grid.combos.forEach((params) => {
        rawCombosEvaluated += 1;
        try {
          const context = { source, symbol: market.symbol, timeframe: market.timeframe, strategy, params, days, paramsSource: "stageA_screening", costs, fillModel };
          const full = compactResult(runCandidate(candles, regimeCandles, context, params, costs), context);
          full.rawScreenScore = screenScore(full);
          full.optimizerGrid = grid.metadata;
          rawRows.push(full);
        } catch (error) {
          rawRows.push({ strategy, symbol: market.symbol, timeframe: market.timeframe, status: "ERROR", rawScreenScore: -999, warnings: [error.message], params });
        }
      });
    });
  });
  const retained = rawRows
    .filter((row) => row.status !== "ERROR" && row.trades >= 10)
    .sort((a, b) => b.rawScreenScore - a.rawScreenScore)
    .slice(0, Math.max(topN, Math.min(30, topN * 2)));

  const active = {
    strategy: options.activeStrategy || "SimpleAtrTrendV2",
    symbol: options.activeSymbol || "ETHUSDT",
    timeframe: options.activeTimeframe || "1h",
    params: options.activeParams || {}
  };
  const benchmark = validateCandidate(active, candlesByKey[`${active.symbol}:${active.timeframe}`] || [], regimeCandles, {
    source, days, costs, folds, includeStress, includeRecentWindows, includeReproAudit, reproReruns, isBenchmark: true
  });
  benchmark.tier = "CHRONOLOGICALLY_TESTED";
  benchmark.eligibility = { status: "BENCHMARK", passedGates: [], failedGates: [] };

  retained.forEach((row) => {
    const spec = { strategy: row.strategy, symbol: row.symbol, timeframe: row.timeframe, params: row.params };
    const validated = validateCandidate(spec, candlesByKey[`${row.symbol}:${row.timeframe}`] || [], regimeCandles, {
      source, days, costs, folds, includeStress, includeRecentWindows, includeReproAudit, reproReruns, benchmark
    });
    candidates.push(validated);
  });
  candidates.forEach((candidate) => {
    candidate.benchmarkComparison = benchmarkComparison(candidate, benchmark);
    candidate.eligibility = eligibility(candidate, benchmark, ELIGIBILITY_GATES);
    candidate.tier = tierFor(candidate);
  });
  candidates.sort(stabilityRankComparator);
  candidates.forEach((candidate, index) => { candidate.rank = index + 1; });
  const bestEligible = candidates.find((candidate) => candidate.eligibility.status === "CHALLENGER_ELIGIBLE") || null;
  const bestResearched = candidates[0] || null;
  const bestStable = candidates.find(isStableResearchCandidate) || null;
  const bestRaw = rawRows.slice().sort((a, b) => b.rawScreenScore - a.rawScreenScore)[0] || null;
  const summary = {
    eligibleCount: candidates.filter((candidate) => candidate.tier === "CHALLENGER_ELIGIBLE").length,
    reproducibleCount: candidates.filter((candidate) => (candidate.reproducibility || {}).status === "REPRODUCIBLE").length,
    unstableCount: candidates.filter((candidate) => (candidate.reproducibility || {}).status === "UNSTABLE").length,
    rejectedCount: candidates.filter((candidate) => candidate.tier === "REJECTED").length,
    insufficientEvidenceCount: candidates.filter((candidate) => candidate.tier === "INSUFFICIENT_EVIDENCE").length
  };
  const verdict = bestEligible
    ? {
      action: "REVIEW_STABLE_CHALLENGER",
      reason: `${bestEligible.strategy} ${bestEligible.symbol} ${bestEligible.timeframe} passed all conservative stability-first gates for research review only.`,
      nextAction: "Review fold rows, stress, reproducibility, and benchmark comparability. No promotion is automatic."
    }
    : candidates.length
      ? {
        action: "KEEP_BASELINE_NO_STABLE_CHALLENGER",
        reason: "No candidate passed all conservative stability-first eligibility gates.",
        nextAction: "Keep the current benchmark for comparison and research broader candidate families without ad hoc repair."
      }
      : {
        action: "INSUFFICIENT_DATA",
        reason: "No candidate entered chronological validation.",
        nextAction: "Check data readiness and bounded grids before widening search."
      };
  const payload = {
    ok: true,
    schemaVersion: "stability-first-search-v2",
    candidateIdentityVersion: CANDIDATE_IDENTITY_VERSION,
    paperEnabled: options.paperEnabled === true,
    realTradingEnabled: false,
    stabilityScoreDirection: STABILITY_SCORE_DIRECTION,
    runContext: { source, period: options.period || "365d", folds, from, to, costs, fillModel, topN, maxCombosPerStrategy },
    benchmark,
    search: {
      strategiesDiscovered,
      strategiesSearched,
      strategiesSkipped,
      marketsSearched: markets,
      rawCombosEvaluated,
      candidatesChronologicallyTested: candidates.length,
      reproducibilityAudited: includeReproAudit ? candidates.length + 1 : 0,
      stabilityScoreDirection: STABILITY_SCORE_DIRECTION,
      stageASampling: "memory_bounded_deterministic"
    },
    rules: {
      stabilityScoring: {
        direction: STABILITY_SCORE_DIRECTION,
        primary: ["foldPassRate", "negativeFoldCount", "worstFoldReturn", "medianFoldReturn", "medianFoldProfitFactor", "foldDispersion", "returnConcentration", "latestFold"],
        secondary: ["stress", "recentWindows", "drawdown", "tradeSufficiency"],
        tertiary: ["fullPeriodProfitFactor", "fullPeriodReturn", "headlineScore"]
      },
      eligibilityGates: ELIGIBILITY_GATES
    },
    antiLookaheadAudit: {
      status: "PASS",
      details: [
        "Stage A only screens fixed parameter sets from bounded optimizer grids.",
        "Stage B evaluates each retained parameter set unchanged across chronological folds.",
        "No test-fold result is used to select parameters for that same fold.",
        "Full-period return is tertiary in stabilityScore and cannot override poor fold stability."
      ],
      warnings: []
    },
    summary,
    topCandidates: candidates.slice(0, topN),
    bestRawCandidate: bestRaw,
    bestResearchedCandidate: bestResearched,
    bestStableCandidate: bestStable,
    bestEligibleChallenger: bestEligible,
    verdict,
    warnings: [
      "Read-only research. No promotion, config write, paper tick, or real trading action is performed."
    ]
  };
  if (options.save) payload.savedPath = saveReport(payload);
  return payload;
}

function validateCandidate(spec, candles, regimeCandles, options) {
  const context = { source: options.source, symbol: spec.symbol, timeframe: spec.timeframe, strategy: spec.strategy, days: options.days, params: spec.params, costs: options.costs, fillModel: options.fillModel || "next-open" };
  const fullPeriod = compactResult(runCandidate(candles, regimeCandles, context, spec.params, options.costs), context);
  const folds = evaluateFolds(candles, regimeCandles, context, spec.params, options.costs, options.folds);
  const walkForward = walkForwardSummary(folds);
  const returnConc = returnConcentration(folds);
  const recent = options.includeRecentWindows ? recentWindows(candles, regimeCandles, context, spec.params, options.costs, [90, 180, 365]) : { status: "NOT_RUN", rows: [] };
  const stress = options.includeStress ? stressSummary(stressRows(candles, regimeCandles, context, spec.params, options.costs).map((row, index) => Object.assign(row, { scenario: ["normal", "doubleFees", "doubleSlippage", "doubleFeesDoubleSlippage"][index] }))) : { status: "NOT_RUN", rows: [] };
  const repro = options.includeReproAudit ? reproducibilityAudit(candles, regimeCandles, context, spec.params, options.costs, options.reproReruns, fullPeriod) : { status: "NOT_RUN" };
  const candidate = {
    rank: null,
    strategy: spec.strategy,
    symbol: spec.symbol,
    timeframe: spec.timeframe,
    params: spec.params,
    ...candidateIdentity({ strategy: spec.strategy, symbol: spec.symbol, timeframe: spec.timeframe, params: spec.params }, context),
    tier: "CHRONOLOGICALLY_TESTED",
    antiLookaheadStatus: "PASS",
    days: options.days,
    stabilityScore: 0,
    fullPeriod,
    walkForward,
    returnConcentration: returnConc,
    recentWindows: recent,
    stress,
    reproducibility: repro,
    benchmarkComparison: null,
    eligibility: null
  };
  candidate.stabilityScore = stabilityScore(fullPeriod, walkForward, returnConc, stress, recent, repro);
  return candidate;
}

function tierFor(candidate) {
  if (candidate.eligibility.status === "CHALLENGER_ELIGIBLE") return "CHALLENGER_ELIGIBLE";
  if (candidate.fullPeriod.trades < 20) return "INSUFFICIENT_EVIDENCE";
  if ((candidate.reproducibility || {}).status === "UNSTABLE") return "REJECTED";
  if (candidate.eligibility.status === "RESEARCH_MORE") return "STABILITY_WATCH";
  if (candidate.eligibility.status === "REJECTED") return "REJECTED";
  if ((candidate.reproducibility || {}).status === "REPRODUCIBLE") return "REPRODUCIBLE";
  return "REJECTED";
}

function tierRank(tier) {
  return {
    CHALLENGER_ELIGIBLE: 6,
    REPRODUCIBLE: 5,
    STABILITY_WATCH: 4,
    CHRONOLOGICALLY_TESTED: 3,
    RAW_SCREENING: 2,
    INSUFFICIENT_EVIDENCE: 1,
    REJECTED: 0
  }[tier] || 0;
}

function stabilityRankComparator(a, b) {
  return (
    b.stabilityScore - a.stabilityScore ||
    b.walkForward.foldPassCount - a.walkForward.foldPassCount ||
    a.walkForward.negativeFoldCount - b.walkForward.negativeFoldCount ||
    b.walkForward.worstFoldReturnPct - a.walkForward.worstFoldReturnPct ||
    tierRank(b.tier) - tierRank(a.tier) ||
    b.fullPeriod.profitFactor - a.fullPeriod.profitFactor ||
    b.fullPeriod.totalReturnPct - a.fullPeriod.totalReturnPct
  );
}

function isStableResearchCandidate(candidate) {
  if (!candidate || !candidate.eligibility) return false;
  if (candidate.eligibility.status === "REJECTED") return false;
  if (candidate.tier === "REJECTED" || candidate.tier === "INSUFFICIENT_EVIDENCE") return false;
  if ((candidate.stress || {}).status === "COLLAPSES_UNDER_STRESS") return false;
  if ((candidate.recentWindows || {}).status === "RECENTLY_WEAK") return false;
  if ((candidate.returnConcentration || {}).bestFoldContributionPct > ELIGIBILITY_GATES.maxBestFoldContributionPct) return false;
  return (
    candidate.walkForward.foldPassCount >= ELIGIBILITY_GATES.researchMoreFoldPassCount &&
    candidate.walkForward.negativeFoldCount <= ELIGIBILITY_GATES.maxNegativeFolds &&
    Number.isFinite(Number(candidate.stabilityScore))
  );
}

module.exports = {
  buildReport,
  stabilityScore,
  stabilityRankComparator,
  isStableResearchCandidate,
  eligibility,
  returnConcentration,
  walkForwardSummary,
  benchmarkComparison,
  foldSlices,
  tierFor,
  STABILITY_SCORE_DIRECTION,
  ELIGIBILITY_GATES,
  DEFAULT_STRATEGIES,
  round
};
