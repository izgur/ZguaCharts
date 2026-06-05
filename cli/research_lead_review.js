const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

const RULES = {
  minTrades: 40,
  minProfitFactor: 1.1,
  minReturnPct: 0,
  maxDrawdownPct: 25,
  minReturnEdgePct: 1,
  minProfitFactorEdge: 0.05,
  minScore: 70
};

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
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
  return Math.max(100, Math.min(Number(cap || 5000), bars));
}

function classify(row) {
  if (row.status === "ERROR") return "ERROR";
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
    equity: round(result.finalEquity || result.equity || 0, 4),
    realizedPnl: round(result.realizedPnl || 0, 4),
    status: "FAIL",
    mainFailureReason: null,
    warnings: result.warnings || []
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  return row;
}

function errorResult(candidate, message) {
  return {
    symbol: candidate.symbol,
    timeframe: candidate.timeframe,
    strategy: candidate.strategy,
    trades: 0,
    tradesPerMonth: 0,
    totalReturnPct: 0,
    profitFactor: 0,
    maxDrawdownPct: 0,
    winRate: 0,
    avgBarsHeld: 0,
    expectancyPctPerTrade: 0,
    equity: 0,
    realizedPnl: 0,
    status: "ERROR",
    mainFailureReason: "ERROR",
    warnings: [String(message || "Backtest could not run.")]
  };
}

function run(candidate, candles, regimeCandles, costs) {
  return backtest.runBacktestOnCandles({
    source: candidate.source,
    symbol: candidate.symbol,
    interval: candidate.timeframe,
    strategy: candidate.strategy,
    candles,
    regimeCandles,
    params: Object.assign({}, candidate.params || {}, costs),
    feePct: costs.feePct,
    slippagePct: costs.slippagePct
  });
}

function score(row) {
  if (!row || row.status === "ERROR") return 0;
  return round(
    Math.max(0, row.profitFactor - 1) * 35
      + Math.max(0, row.totalReturnPct) * 3
      + Math.min(20, row.trades / 3)
      - Math.max(0, row.maxDrawdownPct - 10) * 1.5,
    4
  );
}

function stressReview(candidate, candles, regimeCandles, costs, days) {
  const scenarios = [
    { scenario: "baseline", feePct: costs.feePct, slippagePct: costs.slippagePct },
    { scenario: "noSlippage", feePct: costs.feePct, slippagePct: 0 },
    { scenario: "doubleSlippage", feePct: costs.feePct, slippagePct: costs.slippagePct * 2 },
    { scenario: "tripleSlippage", feePct: costs.feePct, slippagePct: costs.slippagePct * 3 },
    { scenario: "doubleFees", feePct: costs.feePct * 2, slippagePct: costs.slippagePct },
    { scenario: "doubleFeesDoubleSlippage", feePct: costs.feePct * 2, slippagePct: costs.slippagePct * 2 },
    { scenario: "highStress", feePct: costs.feePct * 2, slippagePct: costs.slippagePct * 3 },
    { scenario: "zeroFees", feePct: 0, slippagePct: costs.slippagePct }
  ];
  const rows = scenarios.map((scenario) => Object.assign(
    { scenario: scenario.scenario, feePct: round(scenario.feePct, 6), slippagePct: round(scenario.slippagePct, 6) },
    compactResult(run(candidate, candles, regimeCandles, scenario), candidate, days)
  ));
  const baseline = rows.find((row) => row.scenario === "baseline") || rows[0];
  rows.forEach((row) => {
    row.degradationVsBaseline = {
      returnDiffPct: round(row.totalReturnPct - baseline.totalReturnPct, 4),
      profitFactorDiff: round(row.profitFactor - baseline.profitFactor, 4),
      drawdownDiffPct: round(row.maxDrawdownPct - baseline.maxDrawdownPct, 4)
    };
  });
  const failed = rows.filter((row) => !["PASS", "WARN"].includes(row.status));
  const keyPass = ["doubleSlippage", "doubleFees"].every((name) => {
    const row = rows.find((item) => item.scenario === name);
    return row && ["PASS", "WARN"].includes(row.status);
  });
  const status = baseline.status !== "PASS" ? "FAIL" : keyPass && failed.length <= 1 ? "RESILIENT" : failed.length ? "WATCH" : "RESILIENT";
  return {
    status,
    rows,
    survivingScenarios: rows.filter((row) => ["PASS", "WARN"].includes(row.status)).map((row) => row.scenario),
    failedScenarios: failed.map((row) => row.scenario),
    worstPassingScenario: rows.filter((row) => ["PASS", "WARN"].includes(row.status)).sort((a, b) => a.totalReturnPct - b.totalReturnPct)[0] || null,
    firstFailureScenario: failed[0] || null,
    recommendation: status === "RESILIENT" ? "Lead survives the core cost stress scenarios for research purposes." : "Cost stress should be reviewed before any promotion discussion."
  };
}

function perturbParams(params) {
  const numeric = Object.keys(params || {}).filter((key) => Number.isFinite(Number(params[key])) && !["regimeMode"].includes(key));
  const out = [{ label: "base", params: Object.assign({}, params) }];
  numeric.slice(0, 8).forEach((key) => {
    [-0.1, 0.1].forEach((ratio) => {
      const next = Object.assign({}, params);
      next[key] = Number(params[key]) * (1 + ratio);
      if (Number.isInteger(Number(params[key]))) next[key] = Math.max(1, Math.round(next[key]));
      out.push({ label: `${key}_${ratio > 0 ? "plus" : "minus"}10pct`, params: next });
    });
  });
  return out.slice(0, 25);
}

function robustnessReview(candidate, candles, regimeCandles, costs, days) {
  const variants = perturbParams(candidate.params || {}).map((variant) => {
    const variantCandidate = Object.assign({}, candidate, { params: variant.params });
    return Object.assign({ variant: variant.label }, compactResult(run(variantCandidate, candles, regimeCandles, costs), variantCandidate, days));
  });
  const passing = variants.filter((row) => ["PASS", "WARN"].includes(row.status));
  const negative = variants.filter((row) => row.totalReturnPct < 0);
  const status = variants.length <= 1 ? "UNKNOWN" : passing.length / variants.length >= 0.7 && !negative.length ? "ROBUST" : passing.length / variants.length >= 0.5 ? "WATCH" : "FRAGILE";
  return {
    status,
    variantsTested: variants.length,
    passCount: passing.length,
    failCount: variants.length - passing.length,
    negativeCount: negative.length,
    bestVariant: variants.slice().sort((a, b) => b.totalReturnPct - a.totalReturnPct)[0] || null,
    worstVariant: variants.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct)[0] || null,
    variants: variants.slice(0, 12)
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

function walkForwardReview(candidate, candles, regimeCandles, costs, days) {
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
  return {
    status,
    passFoldCount,
    failFoldCount,
    negativeFoldCount,
    worstFold: folds.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct)[0] || null,
    bestFold: folds.slice().sort((a, b) => b.totalReturnPct - a.totalReturnPct)[0] || null,
    folds
  };
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

function regimeReview(candidate, candles, regimeCandles, costs) {
  const result = run(candidate, candles, regimeCandles, costs);
  const frame = indicators.buildIndicatorFrame(candles, candidate.params || {});
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
    totalReturnPct: round(buckets[key].returnPct, 4),
    contributionPct: round(buckets[key].returnPct, 4),
    status: buckets[key].returnPct > 0 ? "PASS" : "FAIL",
    mainFailureReason: buckets[key].returnPct > 0 ? "OK" : "NEGATIVE_RETURN"
  })).sort((a, b) => b.totalReturnPct - a.totalReturnPct);
  const enough = regimes.filter((row) => row.trades >= 5);
  const negative = enough.filter((row) => row.totalReturnPct < 0);
  const status = enough.length < 2 ? "UNKNOWN" : negative.length >= Math.ceil(enough.length / 2) ? "HIGH" : negative.length ? "MEDIUM" : "LOW";
  return {
    regimeDependencyStatus: status,
    bestRegime: regimes[0] || null,
    worstRegime: regimes.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct)[0] || null,
    highestTradeCountRegime: regimes.slice().sort((a, b) => b.trades - a.trades)[0] || null,
    regimes: regimes.slice(0, 12),
    recommendation: status === "LOW" ? "Regime dependency looks low in this read-only breakdown." : "Regime dependency should be reviewed before promotion discussion."
  };
}

function replacementEligibility(leadActivity, baselineActivity, checks) {
  const reasons = [];
  if (!["PASS", "WARN"].includes(leadActivity.status)) reasons.push("LEAD_ACTIVITY_NOT_PASSING");
  if (leadActivity.trades < RULES.minTrades) reasons.push("LOW_TRADE_COUNT");
  if (leadActivity.profitFactor < RULES.minProfitFactor) reasons.push("WEAK_PROFIT_FACTOR");
  if (leadActivity.totalReturnPct <= RULES.minReturnPct) reasons.push("NON_POSITIVE_RETURN");
  if (leadActivity.maxDrawdownPct > RULES.maxDrawdownPct) reasons.push("HIGH_DRAWDOWN");
  if (leadActivity.totalReturnPct < Number(baselineActivity.totalReturnPct || 0) + RULES.minReturnEdgePct) reasons.push("INSUFFICIENT_RETURN_EDGE");
  if (leadActivity.profitFactor < Number(baselineActivity.profitFactor || 0) + RULES.minProfitFactorEdge) reasons.push("INSUFFICIENT_PROFIT_FACTOR_EDGE");
  if (checks.robustness && !["ROBUST", "UNKNOWN"].includes(checks.robustness.status)) reasons.push("ROBUSTNESS_NOT_CLEAR");
  if (checks.stress && checks.stress.status !== "RESILIENT") reasons.push("COST_STRESS_NOT_CLEAR");
  if (checks.walkForward && checks.walkForward.status !== "STABLE") reasons.push("WALK_FORWARD_NOT_CLEAR");
  if (checks.regime && !["LOW", "UNKNOWN"].includes(checks.regime.regimeDependencyStatus)) reasons.push("REGIME_DEPENDENCY_REVIEW_NEEDED");
  return { eligible: reasons.length === 0, reasons, rules: RULES };
}

function comparisonSummary(baselineActivity, leadActivity) {
  const metricDiffs = {
    trades: round(leadActivity.trades - baselineActivity.trades, 4),
    tradesPerMonth: round(leadActivity.tradesPerMonth - baselineActivity.tradesPerMonth, 4),
    totalReturnPct: round(leadActivity.totalReturnPct - baselineActivity.totalReturnPct, 4),
    profitFactor: round(leadActivity.profitFactor - baselineActivity.profitFactor, 4),
    maxDrawdownPct: round(leadActivity.maxDrawdownPct - baselineActivity.maxDrawdownPct, 4),
    winRate: round(leadActivity.winRate - baselineActivity.winRate, 4)
  };
  const baselineScore = score(baselineActivity);
  const leadScore = score(leadActivity);
  const winner = leadScore > baselineScore + 10 && leadActivity.totalReturnPct > baselineActivity.totalReturnPct ? "LEAD" : baselineScore >= leadScore ? "BASELINE" : "NO_DECISION";
  return {
    baselineSummary: Object.assign({}, baselineActivity, { score: baselineScore }),
    leadSummary: Object.assign({}, leadActivity, { score: leadScore }),
    metricDiffs,
    tradeoffSummary: `Lead return edge ${metricDiffs.totalReturnPct}% with PF edge ${metricDiffs.profitFactor} and trade delta ${metricDiffs.trades}.`,
    winner
  };
}

function verdictFor(leadActivity, comparison, eligibility, checks) {
  if (!["PASS", "WARN"].includes(leadActivity.status)) {
    return { action: "DISCARD_LEAD", reason: `Lead activity failed: ${leadActivity.mainFailureReason}.`, nextAction: "KEEP_BASELINE" };
  }
  if (eligibility.eligible && comparison.winner === "LEAD") {
    return { action: "REVIEW_FOR_PROMOTION", reason: "Lead beats baseline and clears all conservative research gates. This is review-only, not promotion.", nextAction: "REVIEW_RESEARCH_EVIDENCE" };
  }
  if (comparison.winner === "BASELINE") {
    return { action: "KEEP_BASELINE", reason: "The active baseline remains stronger after focused lead review.", nextAction: "CONTINUE_BASELINE_OBSERVATION" };
  }
  if ((checks.walkForward && checks.walkForward.status !== "STABLE") || (checks.robustness && checks.robustness.status !== "ROBUST") || (checks.stress && checks.stress.status !== "RESILIENT")) {
    return { action: "RESEARCH_MORE", reason: "Lead is promising, but robustness, cost stress, or walk-forward evidence needs more review.", nextAction: "RESEARCH_LEAD_MORE" };
  }
  return { action: "RESEARCH_MORE", reason: "Lead has some positive evidence but does not clear conservative replacement gates.", nextAction: "RESEARCH_LEAD_MORE" };
}

async function fetchSet(source, symbol, timeframe, from, to, limit) {
  try {
    const candles = await data.fetchCandles({ source, symbol, interval: timeframe, from, to, limit });
    return { candles: data.normalizeCandles(candles || []), error: null };
  } catch (error) {
    return { candles: [], error: error.message || String(error) };
  }
}

async function main() {
  const days = periodDays(args.period);
  const source = args.source || "bybit";
  const costs = { feePct: Number(args.feePct || 0.055), slippagePct: Number(args.slippagePct || 0.02) };
  const from = args.from || argsUtil.daysToFrom(days);
  const to = args.to || new Date().toISOString();
  const limit = args.limit && args.limit !== "auto" ? Number(args.limit) : null;
  const baseline = {
    source,
    strategy: args.baselineStrategy || "SimpleAtrTrendV2",
    symbol: args.baselineSymbol || "ETHUSDT",
    timeframe: args.baselineTimeframe || "1h",
    params: args.baselineParams ? JSON.parse(args.baselineParams) : {}
  };
  const lead = {
    source,
    strategy: args.leadStrategy || "RelativeStrengthV2",
    symbol: args.leadSymbol || "ETHUSDT",
    timeframe: args.leadTimeframe || "4h",
    params: args.leadParams ? JSON.parse(args.leadParams) : {}
  };
  const includeRobustness = String(args.includeRobustness || "true").toLowerCase() !== "false";
  const includeStress = String(args.includeStress || "true").toLowerCase() !== "false";
  const includeWalkForward = String(args.includeWalkForward || "true").toLowerCase() !== "false";
  const includeRegime = String(args.includeRegime || "true").toLowerCase() !== "false";
  const includeDeepCompare = String(args.includeDeepCompare || "true").toLowerCase() !== "false";
  const warnings = ["No promotion, paper tick, config write, or real trading action was performed."];
  const [baselineFetch, leadFetch, regimeFetch] = await Promise.all([
    fetchSet(source, baseline.symbol, baseline.timeframe, from, to, limit || autoLimitFor(baseline.timeframe, days, 5000)),
    fetchSet(source, lead.symbol, lead.timeframe, from, to, limit || autoLimitFor(lead.timeframe, days, 5000)),
    fetchSet(source, "BTCUSDT", "4h", from, to, limit || autoLimitFor("4h", days, 3000))
  ]);
  if (baselineFetch.error) warnings.push(`Baseline candles unavailable: ${baselineFetch.error}`);
  if (leadFetch.error) warnings.push(`Lead candles unavailable: ${leadFetch.error}`);
  if (regimeFetch.error) warnings.push(`Regime candles unavailable: ${regimeFetch.error}`);
  const baselineActivity = baselineFetch.error
    ? errorResult(baseline, baselineFetch.error)
    : compactResult(run(baseline, baselineFetch.candles, regimeFetch.candles, costs), baseline, days);
  const leadActivity = leadFetch.error
    ? errorResult(lead, leadFetch.error)
    : compactResult(run(lead, leadFetch.candles, regimeFetch.candles, costs), lead, days);
  const robustness = includeRobustness && !leadFetch.error ? robustnessReview(lead, leadFetch.candles, regimeFetch.candles, costs, days) : null;
  const feeSlippageStress = includeStress && !leadFetch.error ? stressReview(lead, leadFetch.candles, regimeFetch.candles, costs, days) : null;
  const walkForward = includeWalkForward && !leadFetch.error ? walkForwardReview(lead, leadFetch.candles, regimeFetch.candles, costs, days) : null;
  const regimeBreakdown = includeRegime && !leadFetch.error ? regimeReview(lead, leadFetch.candles, regimeFetch.candles, costs) : null;
  const comparison = comparisonSummary(baselineActivity, leadActivity);
  const deepCompare = includeDeepCompare ? {
    status: comparison.winner === "LEAD" ? "LEAD_EDGE" : comparison.winner === "BASELINE" ? "BASELINE_EDGE" : "NO_DECISION",
    baseline: comparison.baselineSummary,
    lead: comparison.leadSummary,
    metricDiffs: comparison.metricDiffs,
    tradeoffSummary: comparison.tradeoffSummary,
    winner: comparison.winner
  } : null;
  const replacementEligibilityResult = replacementEligibility(leadActivity, baselineActivity, {
    robustness,
    stress: feeSlippageStress,
    walkForward,
    regime: regimeBreakdown
  });
  const verdict = verdictFor(leadActivity, comparison, replacementEligibilityResult, {
    robustness,
    stress: feeSlippageStress,
    walkForward,
    regime: regimeBreakdown
  });
  process.stdout.write(JSON.stringify({
    ok: !baselineFetch.error && !leadFetch.error,
    baseline: Object.assign({}, baseline, { params: baseline.params }),
    lead: Object.assign({}, lead, { params: lead.params }),
    evidence: {
      activity: { baseline: baselineActivity, lead: leadActivity },
      robustness,
      feeSlippageStress,
      walkForward,
      regimeBreakdown,
      deepCompare
    },
    comparison,
    replacementEligibility: replacementEligibilityResult,
    verdict,
    warnings
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}

main().catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
