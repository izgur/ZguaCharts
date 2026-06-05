const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");
const optimizer = require("../core/optimizer");
const strategiesRegistry = require("../core/strategies");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

const AUTO_STRATEGIES = [
  "SimpleAtrTrendV2",
  "PullbackReclaimV2",
  "EmaBounceV2",
  "BreakoutRetestV2",
  "RangeExpansionV2",
  "RelativeStrengthV2",
  "EmaPullbackContinuation",
  "TrendBreakoutRetest",
  "VolatilitySqueezeBreakout",
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

const REPLACEMENT_RULES = {
  minTrades: 40,
  minProfitFactor: 1.1,
  maxDrawdownPct: 25,
  minReturnPct: 0,
  minPracticalScoreMargin: 10,
  maxTradesPerMonthDropRatio: 0.5,
  strongImprovementReturnPct: 2,
  strongImprovementProfitFactor: 0.2
};

const REPRO_TOLERANCES = {
  tradeTolerance: 1,
  returnPctTolerance: 0.5,
  profitFactorTolerance: 0.15,
  drawdownPctTolerance: 0.5
};

function parseCsv(value, fallback) {
  if (!value || value === "auto") return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

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

function strategyExists(name) {
  try {
    strategiesRegistry.getStrategy(name);
    return true;
  } catch (error) {
    return false;
  }
}

function classify(row) {
  if (row.status === "ERROR" || row.status === "UNSUPPORTED") return row.status;
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
  return reason === "UNSUPPORTED" ? "UNSUPPORTED" : "FAIL";
}

function scoreRow(row) {
  const statusBonus = row.status === "PASS" ? 30 : row.status === "WARN" ? 12 : -25;
  let score = statusBonus
    + Math.min(Number(row.trades || 0), 150) * 0.14
    + Math.min(Number(row.tradesPerMonth || 0), 12) * 1.7
    + Number(row.profitFactor || 0) * 18
    + Number(row.totalReturnPct || 0) * 2.2
    + Number(row.expectancyPctPerTrade || 0) * 10
    - Number(row.maxDrawdownPct || 0) * 1.25;
  if (row.trades < 20) score -= 18;
  if (row.tradesPerMonth < 2) score -= 8;
  if (row.profitFactor < 1.1) score -= 18;
  if (row.totalReturnPct < 0) score -= 20;
  if (row.maxDrawdownPct > 25) score -= 20;
  return round(score, 5);
}

function compactResult(result, context) {
  const trades = Number(result.trades || 0);
  const row = {
    rank: null,
    practicalRank: null,
    strategy: context.strategy,
    symbol: context.symbol,
    timeframe: context.timeframe,
    params: context.params,
    paramsSource: context.paramsSource,
    rawRank: null,
    rawCandidate: !context.isActiveBaseline,
    reproducibilityStatus: "NOT_CHECKED",
    reproducibilityReasons: [],
    reproducibilityDiffs: {},
    qualityGateStatus: "RAW_ONLY",
    qualityGateReasons: [],
    finalCandidateTier: context.isActiveBaseline ? "BASELINE" : "RAW",
    status: "FAIL",
    replacementEligible: false,
    replacementRejectionReasons: [],
    evidenceTier: "INSUFFICIENT",
    trades,
    tradesPerMonth: round(trades / Math.max(1, context.days) * 30, 2),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    avgBarsHeld: round(result.avgBarsHeld || 0, 4),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades, 4) : 0,
    score: 0,
    practicalScore: 0,
    mainFailureReason: null,
    warnings: result.warnings || [],
    isActiveBaseline: !!context.isActiveBaseline
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  row.score = scoreRow(row);
  return row;
}

function errorRow(context, message) {
  return {
    rank: null,
    practicalRank: null,
    strategy: context.strategy,
    symbol: context.symbol,
    timeframe: context.timeframe,
    params: {},
    paramsSource: "error",
    rawRank: null,
    rawCandidate: !context.isActiveBaseline,
    reproducibilityStatus: "FAIL",
    reproducibilityReasons: ["error"],
    reproducibilityDiffs: {},
    qualityGateStatus: "REJECTED",
    qualityGateReasons: ["error"],
    finalCandidateTier: "REJECTED",
    status: "ERROR",
    replacementEligible: false,
    replacementRejectionReasons: ["error"],
    evidenceTier: "INSUFFICIENT",
    trades: 0,
    tradesPerMonth: 0,
    totalReturnPct: 0,
    profitFactor: 0,
    maxDrawdownPct: 0,
    winRate: 0,
    avgBarsHeld: 0,
    expectancyPctPerTrade: 0,
    score: -999,
    practicalScore: -999,
    mainFailureReason: "ERROR",
    warnings: [message],
    isActiveBaseline: false
  };
}

function evidenceTier(row, rules) {
  if (row.status === "UNSUPPORTED" || row.status === "ERROR") return "INSUFFICIENT";
  if (row.trades < 20 || row.profitFactor <= 0) return "INSUFFICIENT";
  if (row.trades >= rules.minTrades && row.profitFactor >= 1.2 && row.totalReturnPct > 2 && row.maxDrawdownPct <= rules.maxDrawdownPct) return "STRONG";
  if (row.trades >= rules.minTrades && row.profitFactor >= rules.minProfitFactor && row.totalReturnPct > 0) return "MEDIUM";
  return "WEAK";
}

function practicalScoreRow(row, baseline, rules, activeEvidence) {
  let score = Number(row.score || 0);
  const baselineReturn = Number(activeEvidence.totalReturnPct ?? (baseline ? baseline.totalReturnPct : 0) ?? 0);
  const baselinePf = Number(activeEvidence.profitFactor ?? (baseline ? baseline.profitFactor : 0) ?? 0);
  score += Math.min(Number(row.trades || 0), 100) * 0.04;
  score += (Number(row.totalReturnPct || 0) - baselineReturn) * 1.8;
  score += (Number(row.profitFactor || 0) - baselinePf) * 10;
  if (row.trades < rules.minTrades) score -= (rules.minTrades - Number(row.trades || 0)) * 0.8;
  if (row.totalReturnPct <= baselineReturn) score -= Math.min(30, (baselineReturn - Number(row.totalReturnPct || 0)) * 2);
  if (row.isActiveBaseline) score += 5;
  return round(score, 5);
}

function replacementRejectionReasons(row, baseline, rules, activeEvidence) {
  const reasons = [];
  const baselineReturn = Number(activeEvidence.totalReturnPct ?? (baseline ? baseline.totalReturnPct : 0) ?? 0);
  const baselinePf = Number(activeEvidence.profitFactor ?? (baseline ? baseline.profitFactor : 0) ?? 0);
  const baselineTradesPerMonth = Number((baseline ? baseline.tradesPerMonth : 0) || 0);
  const practicalMargin = Number(row.practicalScore || 0) - Number(baseline ? baseline.practicalScore : 0);
  if (row.isActiveBaseline) reasons.push("active_baseline_not_replacement");
  if (!["PASS", "WARN"].includes(row.status)) reasons.push("status_not_pass_or_warn");
  if (Number(row.trades || 0) < rules.minTrades) reasons.push(`trades_below_${rules.minTrades}`);
  if (Number(row.profitFactor || 0) < rules.minProfitFactor) reasons.push(`profit_factor_below_${rules.minProfitFactor}`);
  if (Number(row.totalReturnPct || 0) <= rules.minReturnPct) reasons.push("non_positive_return");
  if (Number(row.maxDrawdownPct || 0) > rules.maxDrawdownPct) reasons.push(`drawdown_above_${rules.maxDrawdownPct}`);
  const beatsReturn = Number(row.totalReturnPct || 0) > baselineReturn;
  const beatsPractical = practicalMargin >= rules.minPracticalScoreMargin;
  if (!beatsReturn && !beatsPractical) reasons.push("weak_return_vs_baseline");
  else if (!beatsReturn) reasons.push("return_below_promoted_baseline");
  const tradesPerMonth = Number(row.tradesPerMonth || 0);
  const strongImprovement = Number(row.totalReturnPct || 0) >= baselineReturn + rules.strongImprovementReturnPct
    && Number(row.profitFactor || 0) >= baselinePf + rules.strongImprovementProfitFactor;
  if (baselineTradesPerMonth > 0 && tradesPerMonth < baselineTradesPerMonth * rules.maxTradesPerMonthDropRatio && !strongImprovement) {
    reasons.push("trade_frequency_worse_than_baseline");
  }
  return reasons;
}

function maxAbs(values) {
  return values.reduce((max, value) => Math.max(max, Math.abs(Number(value || 0))), 0);
}

function statusBand(status) {
  if (status === "PASS" || status === "WARN") return "PASSING";
  if (status === "NO_TRADES") return "NO_TRADES";
  return "FAILING";
}

function compactRerunResult(result) {
  const row = {
    status: "FAIL",
    trades: Number(result.trades || 0),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    mainFailureReason: null
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  return row;
}

function reproDiffSummary(original, reruns) {
  return {
    tradesDiffMax: maxAbs(reruns.map((row) => Number(row.trades || 0) - Number(original.trades || 0))),
    returnDiffMax: round(maxAbs(reruns.map((row) => Number(row.totalReturnPct || 0) - Number(original.totalReturnPct || 0))), 4),
    profitFactorDiffMax: round(maxAbs(reruns.map((row) => Number(row.profitFactor || 0) - Number(original.profitFactor || 0))), 4),
    drawdownDiffMax: round(maxAbs(reruns.map((row) => Number(row.maxDrawdownPct || 0) - Number(original.maxDrawdownPct || 0))), 4),
    statusChanged: reruns.some((rerun) => rerun.status !== original.status)
  };
}

function reproStatus(original, reruns, diffs) {
  if (!reruns.length || reruns.some((row) => row.status === "ERROR")) return "FAIL";
  const materiallyDifferent = diffs.tradesDiffMax > REPRO_TOLERANCES.tradeTolerance
    || diffs.returnDiffMax > REPRO_TOLERANCES.returnPctTolerance
    || diffs.profitFactorDiffMax > REPRO_TOLERANCES.profitFactorTolerance
    || diffs.drawdownDiffMax > REPRO_TOLERANCES.drawdownPctTolerance;
  const bandChanged = reruns.some((row) => statusBand(row.status) !== statusBand(original.status));
  if (original.status === "PASS" && reruns.some((row) => row.status === "FAIL")) return "UNSTABLE";
  if (diffs.statusChanged || bandChanged || materiallyDifferent) return "UNSTABLE";
  const modest = diffs.tradesDiffMax > 0
    || diffs.returnDiffMax > REPRO_TOLERANCES.returnPctTolerance / 2
    || diffs.profitFactorDiffMax > REPRO_TOLERANCES.profitFactorTolerance / 2
    || diffs.drawdownDiffMax > REPRO_TOLERANCES.drawdownPctTolerance / 2;
  return modest ? "WATCH" : "REPRODUCIBLE";
}

function reproReasons(original, status, diffs, reruns) {
  const reasons = [];
  if (original.status === "PASS" && reruns.some((row) => row.status === "FAIL")) reasons.push("original_pass_rerun_fail");
  if (diffs.statusChanged) reasons.push("status_changed");
  if (diffs.tradesDiffMax > REPRO_TOLERANCES.tradeTolerance) reasons.push("trades_mismatch");
  if (diffs.returnDiffMax > REPRO_TOLERANCES.returnPctTolerance) reasons.push("return_mismatch");
  if (diffs.profitFactorDiffMax > REPRO_TOLERANCES.profitFactorTolerance) reasons.push("profit_factor_mismatch");
  if (diffs.drawdownDiffMax > REPRO_TOLERANCES.drawdownPctTolerance) reasons.push("drawdown_mismatch");
  if (status === "FAIL") reasons.push("rerun_failed");
  return reasons;
}

function auditRowReproducibility(row, candles, regimeCandles, context, reruns) {
  if (row.isActiveBaseline) return;
  const rerunRows = [];
  for (let index = 0; index < reruns; index += 1) {
    try {
      rerunRows.push(compactRerunResult(backtest.runBacktestOnCandles({
        source: context.source,
        symbol: row.symbol,
        interval: row.timeframe,
        strategy: row.strategy,
        candles,
        regimeCandles,
        params: Object.assign({}, row.params, context.costs),
        feePct: context.costs.feePct,
        slippagePct: context.costs.slippagePct
      })));
    } catch (error) {
      rerunRows.push({
        status: "ERROR",
        trades: 0,
        totalReturnPct: 0,
        profitFactor: 0,
        maxDrawdownPct: 0,
        winRate: 0,
        mainFailureReason: "ERROR",
        error: error.message
      });
    }
  }
  const original = {
    status: row.status,
    trades: row.trades,
    totalReturnPct: row.totalReturnPct,
    profitFactor: row.profitFactor,
    maxDrawdownPct: row.maxDrawdownPct,
    winRate: row.winRate
  };
  row.reproducibilityReruns = rerunRows;
  row.reproducibilityDiffs = reproDiffSummary(original, rerunRows);
  row.reproducibilityStatus = reproStatus(original, rerunRows, row.reproducibilityDiffs);
  row.reproducibilityReasons = reproReasons(original, row.reproducibilityStatus, row.reproducibilityDiffs, rerunRows);
}

function applyQualityGate(row, includeReproAudit, requireReproducible) {
  row.qualityGateReasons = [];
  if (row.isActiveBaseline) {
    row.qualityGateStatus = "RAW_ONLY";
    row.finalCandidateTier = "BASELINE";
    return;
  }
  if (!includeReproAudit) {
    row.reproducibilityStatus = row.reproducibilityStatus || "NOT_CHECKED";
    row.qualityGateStatus = "RAW_ONLY";
    row.finalCandidateTier = "RAW";
    if (requireReproducible) row.qualityGateReasons.push("reproducibility_not_checked");
    return;
  }
  if (!row.reproducibilityStatus || row.reproducibilityStatus === "NOT_CHECKED") {
    row.reproducibilityStatus = "NOT_CHECKED";
    row.qualityGateStatus = "RAW_ONLY";
    row.finalCandidateTier = "RAW";
    row.qualityGateReasons.push("reproducibility_not_checked");
    return;
  }
  if (row.reproducibilityStatus === "REPRODUCIBLE") {
    row.qualityGateStatus = "REPRODUCIBLE";
    row.finalCandidateTier = "REPRODUCIBLE";
  } else if (row.reproducibilityStatus === "WATCH") {
    row.qualityGateStatus = "WATCH";
    row.finalCandidateTier = "REPRODUCIBLE";
  } else {
    row.qualityGateStatus = "REJECTED";
    row.finalCandidateTier = "REJECTED";
    row.qualityGateReasons.push("reproducibility_" + String(row.reproducibilityStatus || "not_checked").toLowerCase());
  }
  (row.reproducibilityReasons || []).forEach((reason) => {
    if (row.qualityGateReasons.indexOf(reason) === -1) row.qualityGateReasons.push(reason);
  });
}

function annotateReplacementSemantics(rows, activeEvidence, rules) {
  const baseline = rows.find((row) => row.isActiveBaseline) || null;
  rows.forEach((row) => {
    row.evidenceTier = evidenceTier(row, rules);
  });
  rows.forEach((row) => {
    row.practicalScore = practicalScoreRow(row, baseline, rules, activeEvidence);
  });
  if (baseline) baseline.practicalScore = practicalScoreRow(baseline, baseline, rules, activeEvidence);
  rows.forEach((row) => {
    row.replacementRejectionReasons = replacementRejectionReasons(row, baseline, rules, activeEvidence);
    if (row.qualityGateStatus === "REJECTED" && !row.isActiveBaseline) row.replacementRejectionReasons.push("quality_gate_rejected");
    if (row.qualityGateStatus === "RAW_ONLY" && !row.isActiveBaseline && row.qualityGateReasons.includes("reproducibility_not_checked")) {
      row.replacementRejectionReasons.push("reproducibility_not_checked");
    }
    row.replacementEligible = row.replacementRejectionReasons.length === 0;
    if (row.replacementEligible) row.finalCandidateTier = "REPLACEMENT_ELIGIBLE";
    row.practicalRank = null;
  });
  const practicalRows = rows
    .filter((row) => row.isActiveBaseline || row.replacementEligible)
    .sort((a, b) => Number(b.practicalScore || 0) - Number(a.practicalScore || 0));
  practicalRows.forEach((row, index) => {
    row.practicalRank = index + 1;
  });
  return rows;
}

function sortRows(a, b) {
  const ap = a.status === "PASS" ? 2 : a.status === "WARN" ? 1 : 0;
  const bp = b.status === "PASS" ? 2 : b.status === "WARN" ? 1 : 0;
  return bp - ap || b.score - a.score || b.profitFactor - a.profitFactor || b.totalReturnPct - a.totalReturnPct;
}

function boundedGridCombos(strategy, maxCombos) {
  const grid = optimizer.optimizerGridCatalog()[strategy];
  if (!grid || !grid.params) return { combos: [], metadata: null };
  const keys = Object.keys(grid.params);
  const lengths = keys.map((key) => Math.max(1, (grid.params[key] || []).length));
  const total = lengths.reduce((product, length) => product * length, 1);
  const wanted = Math.max(1, Math.min(Number(maxCombos || 50), Number(grid.maxCombinations || maxCombos || 50), total));
  const seen = new Set();
  const combos = [];
  const attempts = Math.max(wanted * 8, wanted + 20);
  for (let i = 0; i < attempts && combos.length < wanted; i += 1) {
    let cursor = Math.min(total - 1, Math.floor(i * total / attempts));
    const combo = {};
    keys.forEach((key, keyIndex) => {
      const values = grid.params[key] || [];
      const length = lengths[keyIndex];
      const valueIndex = cursor % length;
      cursor = Math.floor(cursor / length);
      combo[key] = values[valueIndex];
    });
    const key = JSON.stringify(combo);
    if (seen.has(key)) continue;
    if (optimizer.validCombo && !optimizer.validCombo(combo)) continue;
    seen.add(key);
    combos.push(combo);
  }
  for (let i = 0; i < total && combos.length < wanted && i < wanted * 50; i += 1) {
    let cursor = i;
    const combo = {};
    keys.forEach((key, keyIndex) => {
      const values = grid.params[key] || [];
      const length = lengths[keyIndex];
      const valueIndex = cursor % length;
      cursor = Math.floor(cursor / length);
      combo[key] = values[valueIndex];
    });
    const key = JSON.stringify(combo);
    if (seen.has(key)) continue;
    if (optimizer.validCombo && !optimizer.validCombo(combo)) continue;
    seen.add(key);
    combos.push(combo);
  }
  return {
    combos,
    metadata: {
      gridName: grid.gridName,
      plannedCombinations: total,
      sampledCombinations: combos.length,
      sampled: combos.length < total
    }
  };
}

function stressSummary(row, candles, regimeCandles, context) {
  const scenarios = [
    { name: "baseline", feePct: context.costs.feePct, slippagePct: context.costs.slippagePct },
    { name: "doubleSlippage", feePct: context.costs.feePct, slippagePct: context.costs.slippagePct * 2 },
    { name: "doubleFees", feePct: context.costs.feePct * 2, slippagePct: context.costs.slippagePct },
    { name: "highStress", feePct: context.costs.feePct * 2, slippagePct: context.costs.slippagePct * 3 }
  ];
  const rows = scenarios.map((scenario) => compactResult(backtest.runBacktestOnCandles({
    source: context.source,
    symbol: row.symbol,
    interval: row.timeframe,
    strategy: row.strategy,
    candles,
    regimeCandles,
    params: Object.assign({}, row.params, { feePct: scenario.feePct, slippagePct: scenario.slippagePct }),
    feePct: scenario.feePct,
    slippagePct: scenario.slippagePct
  }), {
    strategy: row.strategy,
    symbol: row.symbol,
    timeframe: row.timeframe,
    params: row.params,
    paramsSource: row.paramsSource,
    days: context.days,
    isActiveBaseline: row.isActiveBaseline
  }));
  const failed = rows.filter((item) => !["PASS", "WARN"].includes(item.status));
  return {
    status: failed.length ? "WATCH" : "RESILIENT",
    failedScenarios: failed.map((item, index) => scenarios[index] ? scenarios[index].name : item.status),
    baseline: rows[0] || null
  };
}

function walkForwardSummary(row, candles, regimeCandles, context) {
  const count = 4;
  const size = Math.max(1, Math.floor(candles.length / count));
  const folds = [];
  for (let index = 0; index < count; index += 1) {
    const start = index * size;
    const end = index === count - 1 ? candles.length : Math.min(candles.length, (index + 1) * size);
    const slice = candles.slice(start, end);
    if (!slice.length) continue;
    folds.push(Object.assign({ fold: index + 1 }, compactResult(backtest.runBacktestOnCandles({
      source: context.source,
      symbol: row.symbol,
      interval: row.timeframe,
      strategy: row.strategy,
      candles: slice,
      regimeCandles,
      params: row.params,
      feePct: context.costs.feePct,
      slippagePct: context.costs.slippagePct
    }), {
      strategy: row.strategy,
      symbol: row.symbol,
      timeframe: row.timeframe,
      params: row.params,
      paramsSource: row.paramsSource,
      days: context.days,
      isActiveBaseline: row.isActiveBaseline
    })));
  }
  const passFoldCount = folds.filter((fold) => ["PASS", "WARN"].includes(fold.status)).length;
  const failFoldCount = folds.length - passFoldCount;
  const negativeFoldCount = folds.filter((fold) => fold.totalReturnPct < 0).length;
  return {
    status: failFoldCount > passFoldCount || negativeFoldCount > Math.floor(folds.length / 2) ? "FRAGILE" : failFoldCount || negativeFoldCount ? "WATCH" : "STABLE",
    passFoldCount,
    failFoldCount,
    negativeFoldCount
  };
}

function buildSummary(rows, skippedStrategies, activeEvidence, rules, options) {
  options = options || {};
  const active = rows.find((row) => row.isActiveBaseline) || null;
  const ranked = rows.slice().sort(sortRows);
  const eligible = rows.filter((row) => row.replacementEligible).sort((a, b) => Number(b.practicalScore || 0) - Number(a.practicalScore || 0));
  const bestOverall = ranked[0] || null;
  const rawRows = rows.filter((row) => !row.isActiveBaseline && row.rawCandidate);
  const bestRawCandidate = rawRows.slice().sort(sortRows)[0] || null;
  const reproducibleRows = rawRows.filter((row) => row.reproducibilityStatus === "REPRODUCIBLE" || row.reproducibilityStatus === "WATCH");
  const bestReproducibleCandidate = reproducibleRows.slice().sort((a, b) => Number(b.practicalScore || b.score || 0) - Number(a.practicalScore || a.score || 0))[0] || null;
  const bestReplacementCandidate = eligible[0] || null;
  const passCount = rows.filter((row) => row.status === "PASS" || row.status === "WARN").length;
  const failCount = rows.length - passCount;
  const rawCandidateCount = rawRows.length;
  const reproducibleCandidateCount = rawRows.filter((row) => row.reproducibilityStatus === "REPRODUCIBLE").length;
  const watchCandidateCount = rawRows.filter((row) => row.reproducibilityStatus === "WATCH").length;
  const unstableCandidateCount = rawRows.filter((row) => row.reproducibilityStatus === "UNSTABLE").length;
  const rejectedByQualityGateCount = rawRows.filter((row) => row.qualityGateStatus === "REJECTED").length;
  const qualityGateExplanation = options.includeReproAudit
    ? `Reproducibility audit checked the top ${options.reproTopN} raw optimizer row(s) with ${options.reproReruns} rerun(s); unstable or failed rows are rejected before replacement review.`
    : "Reproducibility audit was not requested; optimizer rows are raw research leads only.";
  let recommendation = { action: "NO_ACTION", reason: "No optimizer batch rows were evaluated." };
  if (active && bestReplacementCandidate && Number(bestReplacementCandidate.practicalScore || 0) > Number(active.practicalScore || 0) + rules.minPracticalScoreMargin) {
    recommendation = {
      action: "REVIEW_NEW_CANDIDATE",
      reason: `${bestReplacementCandidate.strategy} ${bestReplacementCandidate.symbol} ${bestReplacementCandidate.timeframe} passed replacement eligibility and beats the active baseline practical score for research review only.`
    };
  } else if (active) {
    recommendation = {
      action: "KEEP_BASELINE",
      reason: bestRawCandidate
        ? `Best raw optimizer row ${bestRawCandidate.strategy} ${bestRawCandidate.symbol} ${bestRawCandidate.timeframe} is not a replacement: ${(bestRawCandidate.replacementRejectionReasons || bestRawCandidate.qualityGateReasons || []).join(", ") || "did not beat practical gates"}.`
        : "The active promoted candidate remains the best practical decision after this bounded research batch."
    };
  } else if (rows.length) {
    recommendation = { action: "RESEARCH_MORE", reason: "Rows were evaluated, but no active baseline was available for practical replacement review." };
  }
  return {
    bestOverall,
    bestRawCandidate,
    bestReproducibleCandidate,
    bestReplacementCandidate,
    activeBaselineRank: active ? active.rank : null,
    activeBaselinePracticalRank: active ? active.practicalRank : null,
    rawCandidateCount,
    reproducibleCandidateCount,
    watchCandidateCount,
    unstableCandidateCount,
    rejectedByQualityGateCount,
    replacementEligibleCount: eligible.length,
    replacementRules: rules,
    passCount,
    failCount,
    skippedCount: skippedStrategies.length,
    qualityGateExplanation,
    recommendation
  };
}

function saveResult(payload) {
  const dir = path.join("reports", "research-batches");
  fs.mkdirSync(dir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "-");
  const file = path.join(dir, `multi-strategy-optimizer-batch-${stamp}.json`);
  fs.writeFileSync(file, JSON.stringify(payload, null, 2));
  return file.replace(/\\/g, "/");
}

const days = periodDays(args.period);
const symbols = parseCsv(args.symbols, ["ETHUSDT", "BTCUSDT", "SOLUSDT"]);
const timeframes = parseCsv(args.timeframes, ["1h", "4h"]);
const requestedStrategies = parseCsv(args.strategies, AUTO_STRATEGIES);
const maxCandidates = Math.max(1, Math.min(Number(args.maxCandidates || args.max_candidates || 100), 200));
const maxCombosPerStrategy = Math.max(1, Math.min(Number(args.maxCombosPerStrategy || args.max_combos_per_strategy || 50), 250));
const topN = Math.max(1, Math.min(Number(args.topN || args.top_n || 20), 100));
const source = args.source || "bybit";
const activeParams = args.activeParams ? JSON.parse(args.activeParams) : {};
const active = {
  symbol: args.activeSymbol || "ETHUSDT",
  timeframe: args.activeTimeframe || "1h",
  strategy: args.activeStrategy || "SimpleAtrTrendV2"
};
const activeEvidence = {
  trades: args.activeBaselineTrades !== undefined ? Number(args.activeBaselineTrades) : null,
  totalReturnPct: args.activeBaselineReturnPct !== undefined ? Number(args.activeBaselineReturnPct) : null,
  profitFactor: args.activeBaselineProfitFactor !== undefined ? Number(args.activeBaselineProfitFactor) : null,
  maxDrawdownPct: args.activeBaselineMaxDrawdownPct !== undefined ? Number(args.activeBaselineMaxDrawdownPct) : null
};
const costs = {
  feePct: Number(args.feePct || args["fee-pct"] || 0.055),
  slippagePct: Number(args.slippagePct || args["slippage-pct"] || 0.02)
};
const includeStress = String(args.includeStress || "false").toLowerCase() === "true";
const includeWalkForward = String(args.includeWalkForward || "false").toLowerCase() === "true";
const includeReproAudit = String(args.includeReproAudit || args.include_repro_audit || "false").toLowerCase() === "true";
const reproTopN = Math.max(1, Math.min(Number(args.reproTopN || args.repro_top_n || 5), 20));
const reproReruns = Math.max(1, Math.min(Number(args.reproReruns || args.repro_reruns || 1), 5));
const requireReproducible = String(args.requireReproducible || args.require_reproducible || "false").toLowerCase() === "true";
const save = String(args.save || "false").toLowerCase() === "true";
const from = args.from || argsUtil.daysToFrom(days);
const to = args.to || new Date().toISOString();
const limit = args.limit && args.limit !== "auto" ? Number(args.limit) : null;
const catalog = optimizer.optimizerGridCatalog();
const discoveredStrategies = strategiesRegistry.listStrategies().map((strategy) => strategy.name).filter((name) => name !== "AlwaysLongTest");
const selectedStrategies = requestedStrategies.filter((strategy) => strategy !== "AlwaysLongTest");
const skippedStrategies = [];
const optimizableStrategies = selectedStrategies.filter((strategy) => {
  if (!strategyExists(strategy)) {
    skippedStrategies.push({ strategy, reason: "not_registered" });
    return false;
  }
  if (!catalog[strategy]) {
    skippedStrategies.push({ strategy, reason: "no_bounded_optimizer_grid" });
    return false;
  }
  return true;
});

const combos = [];
symbols.forEach((symbol) => {
  timeframes.forEach((timeframe) => {
    optimizableStrategies.forEach((strategy) => combos.push({ symbol, timeframe, strategy }));
  });
});
const cappedCombos = combos.slice(0, maxCandidates);
const candleJobs = {};
cappedCombos.concat([{ symbol: active.symbol, timeframe: active.timeframe, strategy: active.strategy }]).forEach((combo) => {
  const key = combo.symbol + ":" + combo.timeframe;
  if (!candleJobs[key]) {
    candleJobs[key] = data.fetchCandles({
      source,
      symbol: combo.symbol,
      interval: combo.timeframe,
      from,
      to,
      limit: limit || autoLimitFor(combo.timeframe, days, 1000)
    }).then((candles) => data.normalizeCandles(candles || []));
  }
});

Promise.all([
  Promise.all(Object.keys(candleJobs).map((key) => candleJobs[key].then((candles) => [key, candles]))),
  data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: limit || autoLimitFor("4h", days, 1000) }).then((candles) => data.normalizeCandles(candles || []))
]).then(async ([entries, regimeCandles]) => {
  const candlesByKey = {};
  entries.forEach(([key, candles]) => { candlesByKey[key] = candles; });
  const warnings = [];
  if (combos.length > cappedCombos.length) warnings.push(`Optimizer batch capped ${combos.length} requested strategy/market combination(s) to ${cappedCombos.length}.`);
  if (skippedStrategies.length) warnings.push(`${skippedStrategies.length} strategy family/families skipped because no safe bounded grid was available.`);

  const rows = [];
  const activeCandles = candlesByKey[active.symbol + ":" + active.timeframe] || [];
  try {
    rows.push(compactResult(backtest.runBacktestOnCandles({
      source,
      symbol: active.symbol,
      interval: active.timeframe,
      strategy: active.strategy,
      candles: activeCandles,
      regimeCandles,
      params: Object.assign({}, activeParams, costs),
      feePct: costs.feePct,
      slippagePct: costs.slippagePct
    }), {
      strategy: active.strategy,
      symbol: active.symbol,
      timeframe: active.timeframe,
      params: activeParams,
      paramsSource: "activeBaseline",
      days,
      isActiveBaseline: true
    }));
  } catch (error) {
    rows.push(errorRow({ strategy: active.strategy, symbol: active.symbol, timeframe: active.timeframe }, error.message));
  }

  cappedCombos.forEach((combo) => {
    const candles = candlesByKey[combo.symbol + ":" + combo.timeframe] || [];
    try {
      const grid = boundedGridCombos(combo.strategy, maxCombosPerStrategy);
      const candidates = grid.combos.map((params) => {
        const withCosts = Object.assign({}, params, costs);
        const result = backtest.runBacktestOnCandles({
          source,
          symbol: combo.symbol,
          interval: combo.timeframe,
          strategy: combo.strategy,
          candles,
          regimeCandles,
          params: withCosts,
          feePct: costs.feePct,
          slippagePct: costs.slippagePct
        });
        const row = compactResult(result, {
          strategy: combo.strategy,
          symbol: combo.symbol,
          timeframe: combo.timeframe,
          params,
          paramsSource: "optimized",
          days,
          isActiveBaseline: false
        });
        row.optimizerGrid = {
          gridName: grid.metadata ? grid.metadata.gridName : null,
          plannedCombinations: grid.metadata ? grid.metadata.plannedCombinations : 0,
          sampledCombinations: grid.metadata ? grid.metadata.sampledCombinations : 0,
          sampled: grid.metadata ? grid.metadata.sampled : false
        };
        return row;
      });
      const best = candidates.sort(sortRows)[0] || errorRow(combo, "No optimizer candidates were evaluated.");
      rows.push(best);
    } catch (error) {
      rows.push(errorRow(combo, error.message));
    }
  });

  rows.sort(sortRows);
  rows.forEach((row, index) => {
    row.rank = index + 1;
    row.rawRank = row.isActiveBaseline ? null : index + 1;
  });
  if (includeReproAudit) {
    for (const row of rows.filter((item) => !item.isActiveBaseline).slice(0, reproTopN)) {
      const fallbackCandles = candlesByKey[row.symbol + ":" + row.timeframe] || [];
      let candles = fallbackCandles;
      try {
        candles = await data.fetchCandles({
          source,
          symbol: row.symbol,
          interval: row.timeframe,
          from,
          to,
          limit: limit || autoLimitFor(row.timeframe, days, 5000)
        }).then((raw) => data.normalizeCandles(raw || []));
      } catch (error) {
        warnings.push(`Reproducibility audit used optimizer-window candles for ${row.strategy} ${row.symbol} ${row.timeframe} because full-window refresh failed: ${error.message}`);
      }
      auditRowReproducibility(row, candles, regimeCandles, { source, costs }, reproReruns);
    }
  }
  rows.forEach((row) => applyQualityGate(row, includeReproAudit, requireReproducible));
  annotateReplacementSemantics(rows, activeEvidence, REPLACEMENT_RULES);
  const enriched = new Set(rows.slice(0, 3).map((row) => row.rank));
  const activeRow = rows.find((row) => row.isActiveBaseline);
  if (activeRow) enriched.add(activeRow.rank);
  rows.forEach((row) => {
    if (enriched.has(row.rank)) {
      const candles = candlesByKey[row.symbol + ":" + row.timeframe] || [];
      if (includeStress) row.stress = stressSummary(row, candles, regimeCandles, { source, days, costs });
      if (includeWalkForward) row.walkForward = walkForwardSummary(row, candles, regimeCandles, { source, days, costs });
    }
  });
  const outputRows = rows.slice(0, topN);
  if (activeRow && !outputRows.some((row) => row.isActiveBaseline)) outputRows.push(activeRow);
  const summary = buildSummary(rows, skippedStrategies, activeEvidence, REPLACEMENT_RULES, { includeReproAudit, reproTopN, reproReruns, requireReproducible });
  const payload = {
    ok: true,
    paperEnabled: null,
    realTradingEnabled: false,
    activeBaseline: activeRow || null,
    search: {
      symbols,
      timeframes,
      strategies: selectedStrategies,
      period: args.period || "365d",
      maxCandidates,
      maxCombosPerStrategy,
      evaluatedCombos: cappedCombos.length,
      topN,
      includeWalkForward,
      includeStress,
      includeReproAudit,
      reproTopN,
      reproReruns,
      requireReproducible,
      reproducibilityTolerances: REPRO_TOLERANCES,
      save
    },
    discoveredStrategies,
    skippedStrategies,
    rows: outputRows,
    summary,
    warnings
  };
  if (save) payload.savedPath = saveResult(payload);
  process.stdout.write(JSON.stringify(payload, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
