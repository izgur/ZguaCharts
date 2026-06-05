const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

const REPLACEMENT_RULES = {
  minTrades: 40,
  minProfitFactor: 1.1,
  minReturnPct: 0,
  maxDrawdownPct: 25,
  minScoreDiff: 10
};

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

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
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
    status: "FAIL",
    mainFailureReason: null,
    warnings: result.warnings || []
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  return row;
}

function errorActivity(candidate, message) {
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
    status: "ERROR",
    mainFailureReason: "ERROR",
    warnings: [String(message || "Candles were unavailable for this candidate.")]
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
    totalReturnPct: round(buckets[key].returnPct, 4)
  })).sort((a, b) => b.totalReturnPct - a.totalReturnPct);
  const enough = regimes.filter((row) => row.trades >= 5);
  const negative = enough.filter((row) => row.totalReturnPct < 0);
  const status = enough.length < 2 ? "UNKNOWN" : negative.length >= Math.ceil(enough.length / 2) ? "HIGH" : negative.length ? "MEDIUM" : "LOW";
  return {
    regimeDependencyStatus: status,
    bestRegime: regimes[0] || null,
    worstRegime: regimes.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct)[0] || null,
    regimeCount: regimes.length,
    regimes: regimes.slice(0, 12)
  };
}

function replacementEligibility(activity, baselineActivity, scorecard, candidate) {
  const reasons = [];
  if (!["REPRODUCIBLE", "WATCH"].includes(candidate.reproducibilityStatus)) reasons.push("reproducibility_not_passing");
  if (!["PASS", "WARN"].includes(activity.status)) reasons.push("activity_not_pass_or_warn");
  if (activity.trades < REPLACEMENT_RULES.minTrades) reasons.push(`trades_below_${REPLACEMENT_RULES.minTrades}`);
  if (activity.profitFactor < REPLACEMENT_RULES.minProfitFactor) reasons.push(`profit_factor_below_${REPLACEMENT_RULES.minProfitFactor}`);
  if (activity.totalReturnPct <= REPLACEMENT_RULES.minReturnPct) reasons.push("non_positive_return");
  if (activity.maxDrawdownPct > REPLACEMENT_RULES.maxDrawdownPct) reasons.push(`drawdown_above_${REPLACEMENT_RULES.maxDrawdownPct}`);
  if (activity.totalReturnPct <= Number(baselineActivity.totalReturnPct || 0)) reasons.push("return_below_baseline");
  if (scorecard.overallScore < 70) reasons.push("overall_score_below_review_gate");
  return { eligible: reasons.length === 0, reasons, rules: REPLACEMENT_RULES };
}

function scorecardFor(candidate, activity, stress, walk, regime) {
  const activityScore = activity.status === "PASS" ? 25 : activity.status === "WARN" ? 14 : 0;
  const reproducibilityScore = candidate.reproducibilityStatus === "REPRODUCIBLE" ? 25 : candidate.reproducibilityStatus === "WATCH" ? 14 : 0;
  const stressScore = !stress ? 10 : stress.status === "RESILIENT" ? 20 : stress.status === "WATCH" ? 10 : 0;
  const walkForwardScore = !walk ? 10 : walk.status === "STABLE" ? 20 : walk.status === "WATCH" ? 10 : 0;
  const regimeScore = !regime ? 10 : regime.regimeDependencyStatus === "LOW" ? 10 : regime.regimeDependencyStatus === "MEDIUM" ? 5 : regime.regimeDependencyStatus === "UNKNOWN" ? 4 : 0;
  return {
    activityScore,
    reproducibilityScore,
    stressScore,
    walkForwardScore,
    regimeScore,
    overallScore: round(activityScore + reproducibilityScore + stressScore + walkForwardScore + regimeScore, 4)
  };
}

function verdictFor(activity, stress, walk, regime, eligibility, scorecard, candidate) {
  if (!["REPRODUCIBLE", "WATCH"].includes(candidate.reproducibilityStatus)) {
    return { action: "DISCARD", reason: "Candidate failed reproducibility before deeper review." };
  }
  if (!["PASS", "WARN"].includes(activity.status)) {
    return { action: "DISCARD", reason: `Activity confirmation failed: ${activity.mainFailureReason || activity.status}.` };
  }
  if (eligibility.eligible && (!stress || stress.status === "RESILIENT") && (!walk || walk.status === "STABLE") && (!regime || ["LOW", "UNKNOWN"].includes(regime.regimeDependencyStatus))) {
    return { action: "REVIEW_FOR_PROMOTION", reason: "Candidate clears reproducibility, activity, stress, walk-forward, regime, and replacement gates for research review only." };
  }
  if (activity.trades < REPLACEMENT_RULES.minTrades || candidate.reproducibilityStatus === "WATCH") {
    return { action: "WATCH", reason: "Candidate is interesting but evidence is still thin or reproducibility is WATCH." };
  }
  if (scorecard.overallScore >= 50) {
    return { action: "RESEARCH_MORE", reason: "Candidate has promising evidence but did not clear all conservative promotion-review gates." };
  }
  return { action: "DISCARD", reason: "Deep checks are not strong enough to continue prioritizing this candidate." };
}

function buildSummary(candidates) {
  const reviewForPromotionCount = candidates.filter((row) => row.verdict.action === "REVIEW_FOR_PROMOTION").length;
  const researchMoreCount = candidates.filter((row) => row.verdict.action === "RESEARCH_MORE").length;
  const watchCount = candidates.filter((row) => row.verdict.action === "WATCH").length;
  const discardCount = candidates.filter((row) => row.verdict.action === "DISCARD").length;
  const bestCandidate = candidates.slice().sort((a, b) => Number(b.scorecard.overallScore || 0) - Number(a.scorecard.overallScore || 0))[0] || null;
  let recommendation = { action: "NO_ACTION", reason: "No reproducible/watch candidates were available for drilldown." };
  if (reviewForPromotionCount) recommendation = { action: "REVIEW_CANDIDATE", reason: "At least one reproducible candidate cleared all drilldown gates for research review only." };
  else if (researchMoreCount || watchCount) recommendation = { action: "RESEARCH_MORE", reason: "Reproducible candidates exist, but they need more research or evidence before replacement review." };
  else if (candidates.length) recommendation = { action: "KEEP_BASELINE", reason: "All selected reproducible/watch candidates collapsed under deeper checks." };
  return { selectedCount: candidates.length, reviewForPromotionCount, researchMoreCount, watchCount, discardCount, bestCandidate, recommendation };
}

async function main() {
  const days = periodDays(args.period);
  const source = args.source || "bybit";
  const costs = { feePct: Number(args.feePct || 0.055), slippagePct: Number(args.slippagePct || 0.02) };
  const candidates = args.candidates ? JSON.parse(args.candidates) : [];
  const baseline = args.activeBaseline ? JSON.parse(args.activeBaseline) : { strategy: "SimpleAtrTrendV2", symbol: "ETHUSDT", timeframe: "1h", params: {} };
  const includeStress = String(args.includeStress || "true").toLowerCase() !== "false";
  const includeWalkForward = String(args.includeWalkForward || "true").toLowerCase() !== "false";
  const includeRegime = String(args.includeRegime || "true").toLowerCase() !== "false";
  const limit = args.limit && args.limit !== "auto" ? Number(args.limit) : null;
  const from = args.from || argsUtil.daysToFrom(days);
  const to = args.to || new Date().toISOString();
  const warnings = [];
  const candleJobs = {};
  candidates.concat([baseline]).forEach((candidate) => {
    const key = candidate.symbol + ":" + candidate.timeframe;
    if (!candleJobs[key]) {
      candleJobs[key] = data.fetchCandles({
        source,
        symbol: candidate.symbol,
        interval: candidate.timeframe,
        from,
        to,
        limit: limit || autoLimitFor(candidate.timeframe, days, 5000)
      })
        .then((candles) => ({ candles: data.normalizeCandles(candles || []), error: null }))
        .catch((error) => ({ candles: [], error: error.message || String(error) }));
    }
  });
  const [entries, regimeCandles] = await Promise.all([
    Promise.all(Object.keys(candleJobs).map((key) => candleJobs[key].then((result) => [key, result]))),
    data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: limit || autoLimitFor("4h", days, 3000) })
      .then((candles) => data.normalizeCandles(candles || []))
      .catch((error) => {
        warnings.push(`BTCUSDT 4h regime candles unavailable; running drilldown with empty regime candles: ${error.message || String(error)}`);
        return [];
      })
  ]);
  const candlesByKey = {};
  const candleErrorsByKey = {};
  entries.forEach(([key, result]) => {
    candlesByKey[key] = result.candles || [];
    if (result.error) candleErrorsByKey[key] = result.error;
  });
  const baselineCandidate = Object.assign({ source }, baseline);
  const baselineKey = baseline.symbol + ":" + baseline.timeframe;
  const baselineActivity = candleErrorsByKey[baselineKey]
    ? errorActivity(baselineCandidate, candleErrorsByKey[baselineKey])
    : compactResult(run(baselineCandidate, candlesByKey[baselineKey] || [], regimeCandles, costs), baselineCandidate, days);
  const out = candidates.map((input) => {
    const candidate = Object.assign({ source }, input);
    const key = candidate.symbol + ":" + candidate.timeframe;
    const candles = candlesByKey[key] || [];
    const candidateWarnings = [];
    if (candleErrorsByKey[key]) candidateWarnings.push(`Candles unavailable: ${candleErrorsByKey[key]}`);
    const activity = candleErrorsByKey[key]
      ? errorActivity(candidate, candleErrorsByKey[key])
      : compactResult(run(candidate, candles, regimeCandles, costs), candidate, days);
    const stress = includeStress ? stressRows(candidate, candles, regimeCandles, costs, days) : null;
    const walk = includeWalkForward ? walkForward(candidate, candles, regimeCandles, costs, days) : null;
    const regime = includeRegime ? regimeSummary(candidate, candles, regimeCandles, costs) : null;
    const scorecard = scorecardFor(candidate, activity, stress, walk, regime);
    const eligibility = replacementEligibility(activity, baselineActivity, scorecard, candidate);
    const verdict = verdictFor(activity, stress, walk, regime, eligibility, scorecard, candidate);
    return {
      strategy: candidate.strategy,
      symbol: candidate.symbol,
      timeframe: candidate.timeframe,
      params: candidate.params || {},
      reproducibilityStatus: candidate.reproducibilityStatus || "NOT_CHECKED",
      qualityGateStatus: candidate.qualityGateStatus || "RAW_ONLY",
      finalCandidateTier: candidate.finalCandidateTier || "RAW",
      activity,
      stress,
      walkForward: walk,
      regime,
      replacementEligibility: eligibility,
      scorecard,
      verdict,
      warnings: candidateWarnings
    };
  });
  process.stdout.write(JSON.stringify({
    ok: true,
    activeBaseline: Object.assign({}, baseline, { activity: baselineActivity }),
    candidates: out,
    summary: buildSummary(out),
    warnings: warnings.concat(["No promotion, paper tick, config write, or real trading action was performed."])
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}

main().catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
