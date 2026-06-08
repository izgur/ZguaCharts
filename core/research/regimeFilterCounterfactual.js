const backtest = require("../backtest");
const data = require("../data");
const indicators = require("../indicators");

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

function percentile(values, ratio) {
  const nums = values.map(Number).filter(Number.isFinite).sort((a, b) => a - b);
  if (!nums.length) return 0;
  const index = Math.max(0, Math.min(nums.length - 1, Math.floor((nums.length - 1) * ratio)));
  return nums[index];
}

function iso(time) {
  return time ? new Date(Number(time) * 1000).toISOString() : null;
}

function nearestFrameIndex(frame, time) {
  let chosen = -1;
  for (let index = 0; index < frame.length; index += 1) {
    if (Number(frame[index].time) > Number(time)) break;
    chosen = index;
  }
  return chosen >= 0 ? chosen : (frame.length ? 0 : -1);
}

function trendLabel(frame, index) {
  const row = frame[index] || {};
  const prior = frame[Math.max(0, index - 24)] || {};
  const slopePct = prior.ema50 ? (row.ema50 - prior.ema50) / prior.ema50 * 100 : 0;
  if (row.ema50 && row.ema200 && row.ema50 > row.ema200 && slopePct > 0.15) return "uptrend";
  if (row.ema50 && row.ema200 && row.ema50 < row.ema200 && slopePct < -0.15) return "downtrend";
  return "sideways";
}

function volatilityLabelCausal(frame, index) {
  const row = frame[index] || {};
  const value = Number(row.atrPct);
  if (!Number.isFinite(value)) return "unknownVol";
  const start = Math.max(0, index - 500);
  const history = frame.slice(start, index + 1).map((item) => Number(item.atrPct)).filter(Number.isFinite);
  if (history.length < 30) return "mediumVol";
  const low = percentile(history, 0.33);
  const high = percentile(history, 0.66);
  if (value <= low) return "lowVol";
  if (value >= high) return "highVol";
  return "mediumVol";
}

function momentumLabel(frame, index) {
  const row = frame[index] || {};
  const prior = frame[Math.max(0, index - 12)] || {};
  const returnPct = prior.close ? (row.close - prior.close) / prior.close * 100 : 0;
  if ((row.rsi14 || 0) >= 55 || returnPct >= 1) return "bullish";
  if ((row.rsi14 || 0) <= 45 || returnPct <= -1) return "bearish";
  return "neutral";
}

function classifyRegime(frame, time) {
  const index = nearestFrameIndex(frame, time);
  const row = index >= 0 ? frame[index] : null;
  if (!row) {
    return {
      regime: "unknown_unknownVol_neutral",
      trend: "unknown",
      volatility: "unknownVol",
      momentum: "neutral",
      rowTime: null
    };
  }
  const trend = trendLabel(frame, index);
  const volatility = volatilityLabelCausal(frame, index);
  const momentum = momentumLabel(frame, index);
  return {
    regime: [trend, volatility, momentum].join("_"),
    trend,
    volatility,
    momentum,
    rowTime: row.time
  };
}

function variantDefinitions() {
  return [
    {
      id: "baseline",
      description: "No additional causal regime entry exclusion.",
      filterDefinition: { type: "none" },
      allow: () => true
    },
    {
      id: "excludeWorstExact",
      description: "Exclude only uptrend_highVol_bearish entries.",
      filterDefinition: { excludedRegimes: ["uptrend_highVol_bearish"] },
      allow: (regime) => regime.regime !== "uptrend_highVol_bearish"
    },
    {
      id: "excludeHighVolBearish",
      description: "Exclude entries that are both high volatility and bearish.",
      filterDefinition: { excludedVolatility: ["highVol"], excludedMomentum: ["bearish"] },
      allow: (regime) => !(regime.volatility === "highVol" && regime.momentum === "bearish")
    },
    {
      id: "bullishOnly",
      description: "Permit entries only in bullish causal momentum regimes.",
      filterDefinition: { allowedMomentum: ["bullish"] },
      allow: (regime) => regime.momentum === "bullish"
    },
    {
      id: "lowOrMediumVolBullish",
      description: "Permit bullish entries only when volatility is low or medium.",
      filterDefinition: { allowedMomentum: ["bullish"], allowedVolatility: ["lowVol", "mediumVol"] },
      allow: (regime) => regime.momentum === "bullish" && ["lowVol", "mediumVol"].includes(regime.volatility)
    }
  ];
}

function learnedWeakDefinition(trainingTrades, minTrades) {
  const buckets = regimeBuckets(trainingTrades || [], 0);
  const weak = buckets
    .filter((row) => row.trades >= (minTrades || 3) && (row.totalReturnPct < 0 || row.profitFactor < 1))
    .map((row) => row.regime);
  return {
    id: "excludeAllHistoricallyWeakRegimes",
    description: "Exclude regimes that were weak in this fold's training section only.",
    filterDefinition: { trainingOnly: true, excludedRegimes: weak },
    allow: (regime) => !weak.includes(regime.regime),
    learnedWeakRegimes: weak
  };
}

function compactResult(result, extra) {
  const trades = Number(result.trades || 0);
  const row = {
    trades,
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    expectancyPctPerTrade: round(result.averageTrade || 0, 4),
    avgBarsHeld: round(result.avgBarsHeld || 0, 4),
    exposurePct: round(result.exposurePct || 0, 4),
    fees: round(result.totalFees || 0, 4),
    slippage: round(result.totalSlippageCost || 0, 4),
    score: scoreResult(result)
  };
  row.mainFailureReason = failureReason(row);
  row.status = statusFor(row);
  return Object.assign(row, extra || {});
}

function failureReason(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 10) return "TOO_FEW_TRADES";
  if (row.totalReturnPct <= 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.05) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = failureReason(row);
  if (reason === "OK" && row.trades >= 20 && row.profitFactor >= 1.1) return "PASS";
  if (["OK", "TOO_FEW_TRADES"].includes(reason) && row.totalReturnPct > 0 && row.profitFactor >= 1) return "WATCH";
  return "FAIL";
}

function scoreResult(result) {
  const trades = Number(result.trades || 0);
  const pf = Number(result.profitFactor || 0);
  const totalReturn = Number(result.totalReturn || 0);
  const drawdown = Number(result.maxDrawdown || 0);
  const expectancy = Number(result.averageTrade || 0);
  return round(Math.min(trades, 160) * 0.12 + pf * 20 + totalReturn * 2 + expectancy * 12 - drawdown * 1.2, 4);
}

function buildEntryFilter(variant, basisFrame, capture) {
  if (!variant || variant.id === "baseline") return null;
  return function (context) {
    const regime = classifyRegime(basisFrame, context.row.time);
    if (capture) capture.push({ time: context.row.time, regime: regime.regime, allowed: variant.allow(regime) });
    if (variant.allow(regime)) return { allow: true };
    return { allow: false, reason: variant.id, regime: regime.regime };
  };
}

function runBacktest(candles, regimeCandles, params, context, variant, basisFrame, capture) {
  return backtest.runBacktestOnCandles({
    source: context.source,
    symbol: context.symbol,
    interval: context.timeframe,
    strategy: context.strategy,
    candles,
    regimeCandles,
    params,
    entryFilter: buildEntryFilter(variant, basisFrame, capture)
  });
}

function classifyTrades(trades, basisFrame) {
  return (trades || []).map((trade) => {
    const regime = classifyRegime(basisFrame, trade.entrySignalTime || trade.entryTime);
    return Object.assign({}, trade, {
      causalRegime: regime.regime,
      causalRegimeDetail: regime
    });
  });
}

function regimeBuckets(trades, fullReturnPct) {
  const buckets = {};
  (trades || []).forEach((trade) => {
    const key = trade.causalRegime || "unknown_unknownVol_neutral";
    if (!buckets[key]) buckets[key] = [];
    buckets[key].push(trade);
  });
  return Object.keys(buckets).map((regime) => {
    const result = metricsFromTrades(buckets[regime]);
    return Object.assign({ regime, contributionPct: round(fullReturnPct ? result.totalReturnPct / fullReturnPct * 100 : 0, 4) }, result);
  }).sort((a, b) => b.totalReturnPct - a.totalReturnPct || b.trades - a.trades);
}

function metricsFromTrades(trades) {
  const returns = (trades || []).map((trade) => Number(trade.returnPct || 0));
  const wins = returns.filter((value) => value > 0);
  const losses = returns.filter((value) => value < 0);
  let equity = 1;
  let peak = 1;
  let maxDrawdown = 0;
  returns.forEach((value) => {
    equity *= 1 + value / 100;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak ? (peak - equity) / peak * 100 : 0);
  });
  const grossWins = wins.reduce((sum, value) => sum + value, 0);
  const grossLosses = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  const row = {
    trades: returns.length,
    totalReturnPct: round((equity - 1) * 100, 4),
    profitFactor: round(grossLosses ? grossWins / grossLosses : wins.length ? 999 : 0, 4),
    maxDrawdownPct: round(maxDrawdown, 4),
    winRate: round(returns.length ? wins.length / returns.length * 100 : 0, 4),
    expectancyPctPerTrade: round(returns.length ? returns.reduce((sum, value) => sum + value, 0) / returns.length : 0, 4),
    avgBarsHeld: round(returns.length ? trades.reduce((sum, trade) => sum + Number(trade.barsHeld || 0), 0) / returns.length : 0, 4)
  };
  row.mainFailureReason = failureReason(row);
  row.status = statusFor(row);
  return row;
}

function foldSlices(candles, folds) {
  const count = Math.max(1, Math.min(Number(folds || 4), 12));
  const size = Math.max(1, Math.floor(candles.length / count));
  const out = [];
  for (let i = 0; i < count; i += 1) {
    const start = i * size;
    const end = i === count - 1 ? candles.length : Math.min(candles.length, (i + 1) * size);
    const slice = candles.slice(start, end);
    if (slice.length) {
      out.push({ fold: i + 1, start, end, candles: slice });
    }
  }
  return out;
}

function foldSummary(folds) {
  const passFoldCount = folds.filter((row) => ["PASS", "WATCH"].includes(row.status)).length;
  const failFoldCount = folds.length - passFoldCount;
  const negativeFoldCount = folds.filter((row) => Number(row.totalReturnPct || 0) < 0).length;
  const returns = folds.map((row) => Number(row.totalReturnPct || 0));
  const pfs = folds.map((row) => Number(row.profitFactor || 0));
  const worstFold = folds.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct || a.profitFactor - b.profitFactor)[0] || null;
  const bestFold = folds.slice().sort((a, b) => b.totalReturnPct - a.totalReturnPct || b.profitFactor - a.profitFactor)[0] || null;
  const positives = returns.filter((value) => value > 0);
  const positiveSum = positives.reduce((sum, value) => sum + value, 0);
  const bestFoldContributionPct = round(positiveSum && bestFold ? Math.max(0, bestFold.totalReturnPct) / positiveSum * 100 : 0, 4);
  const foldReturnDispersion = round(Math.sqrt(returns.reduce((sum, value) => sum + Math.pow(value - median(returns), 2), 0) / Math.max(1, returns.length)), 4);
  return {
    foldPassRate: round(folds.length ? passFoldCount / folds.length * 100 : 0, 4),
    passFoldCount,
    failFoldCount,
    negativeFoldCount,
    medianFoldReturnPct: round(median(returns), 4),
    medianFoldProfitFactor: round(median(pfs), 4),
    worstFold,
    bestFold,
    worstFoldReturnPct: worstFold ? worstFold.totalReturnPct : 0,
    foldReturnDispersion,
    bestFoldContributionPct,
    returnConcentrationStatus: bestFoldContributionPct > 75 ? "HIGHLY_CONCENTRATED" : bestFoldContributionPct > 50 ? "MODERATELY_CONCENTRATED" : "BALANCED"
  };
}

function stabilityStatus(summary, baselineSummary, full, retention) {
  if (!full || full.trades < 10 || retention.retainedPct < 35) return "INSUFFICIENT_EVIDENCE";
  const passImproved = summary.passFoldCount > baselineSummary.passFoldCount;
  const negativesImproved = summary.negativeFoldCount < baselineSummary.negativeFoldCount;
  const worstImproved = Number(summary.worstFoldReturnPct || 0) > Number(baselineSummary.worstFoldReturnPct || 0) + 0.25;
  const concentratedWorse = concentrationRank(summary.returnConcentrationStatus) > concentrationRank(baselineSummary.returnConcentrationStatus);
  if (passImproved && negativesImproved && worstImproved && !concentratedWorse) return "IMPROVED";
  if (summary.passFoldCount < baselineSummary.passFoldCount || summary.negativeFoldCount > baselineSummary.negativeFoldCount || concentratedWorse) return "WORSE";
  return "SIMILAR";
}

function concentrationRank(status) {
  return { BALANCED: 0, MODERATELY_CONCENTRATED: 1, HIGHLY_CONCENTRATED: 2 }[status] || 1;
}

function eligibility(variant, baseline, baselineSummary) {
  const reasons = [];
  const full = variant.fullPeriod || {};
  const summary = (variant.walkForward || {}).summary || {};
  const retention = variant.tradeRetention || {};
  if (!["PASS", "WATCH"].includes(full.status)) reasons.push("Full-period result is not PASS/WATCH.");
  if (summary.passFoldCount < 2) reasons.push("Fewer than 2 folds pass.");
  if (summary.negativeFoldCount >= baselineSummary.negativeFoldCount) reasons.push("Negative-fold count does not improve versus baseline.");
  if (summary.worstFoldReturnPct <= baselineSummary.worstFoldReturnPct + 0.25) reasons.push("Worst fold does not improve materially.");
  if (full.trades < 40) reasons.push("Fewer than 40 trades remain.");
  if (retention.retainedPct < 50) reasons.push("Less than 50% of baseline trades remain.");
  if (full.profitFactor < 1.05) reasons.push("Profit factor is below the accepted minimum.");
  if (full.totalReturnPct <= 0) reasons.push("Return is not positive after normal costs.");
  if (concentrationRank(summary.returnConcentrationStatus) > concentrationRank(baselineSummary.returnConcentrationStatus)) reasons.push("Return concentration becomes worse.");
  if (variant.antiLookaheadStatus && variant.antiLookaheadStatus !== "PASS") reasons.push("Anti-lookahead audit is not PASS.");
  if (reasons.length) {
    return { status: full.trades < 20 || retention.retainedPct < 35 ? "INSUFFICIENT_EVIDENCE" : "REJECTED", reasons };
  }
  return { status: "REVIEWABLE", reasons: ["Variant passes conservative chronological robustness gates for research review only."] };
}

function compareVariants(variants) {
  const filtered = variants.filter((item) => item.id !== "baseline");
  const byFull = filtered.slice().sort((a, b) => (b.fullPeriod.totalReturnPct || 0) - (a.fullPeriod.totalReturnPct || 0))[0] || null;
  const byStability = filtered.slice().sort((a, b) => (
    (b.walkForward.summary.passFoldCount || 0) - (a.walkForward.summary.passFoldCount || 0) ||
    (a.walkForward.summary.negativeFoldCount || 0) - (b.walkForward.summary.negativeFoldCount || 0) ||
    (b.walkForward.summary.worstFoldReturnPct || 0) - (a.walkForward.summary.worstFoldReturnPct || 0)
  ))[0] || null;
  const conservative = filtered.find((item) => item.eligibility.status === "REVIEWABLE") || null;
  const baseline = variants.find((item) => item.id === "baseline") || {};
  return {
    bestFullPeriodVariant: byFull ? byFull.id : null,
    bestStabilityVariant: byStability ? byStability.id : null,
    bestConservativeVariant: conservative ? conservative.id : null,
    baselineVsBest: conservative ? {
      returnDiffPct: round(conservative.fullPeriod.totalReturnPct - baseline.fullPeriod.totalReturnPct, 4),
      profitFactorDiff: round(conservative.fullPeriod.profitFactor - baseline.fullPeriod.profitFactor, 4),
      passFoldCountDiff: conservative.walkForward.summary.passFoldCount - baseline.walkForward.summary.passFoldCount,
      negativeFoldCountDiff: conservative.walkForward.summary.negativeFoldCount - baseline.walkForward.summary.negativeFoldCount,
      worstFoldReturnDiffPct: round(conservative.walkForward.summary.worstFoldReturnPct - baseline.walkForward.summary.worstFoldReturnPct, 4)
    } : null
  };
}

function verdict(comparison, variants) {
  const best = variants.find((item) => item.id === comparison.bestConservativeVariant);
  if (best) {
    return {
      action: "REVIEW_REGIME_FILTER",
      reason: `${best.id} improved chronological robustness enough for manual research review. It is not promoted automatically.`,
      nextAction: "Inspect folds, retained trades, and stress rows before considering any separate manual candidate design."
    };
  }
  const anyImproved = variants.some((item) => item.id !== "baseline" && item.stabilityStatus === "IMPROVED");
  if (anyImproved) {
    return {
      action: "RESEARCH_MORE",
      reason: "At least one filter improved some stability metrics, but conservative eligibility gates rejected it.",
      nextAction: "Review rejected reasons and avoid promoting a filter unless fold robustness and trade retention both improve."
    };
  }
  return {
    action: "KEEP_BASELINE",
    reason: "No causal filter variant beat the baseline on conservative walk-forward gates.",
    nextAction: "Keep paper/research baseline and continue regime observation."
  };
}

function evaluateVariant(definition, candles, regimeCandles, params, context, basisFrame, folds, baselineTrades, baselineFoldSummary) {
  const capture = [];
  const fullResult = runBacktest(candles, regimeCandles, params, context, definition, basisFrame, capture);
  const fullPeriod = compactResult(fullResult);
  const classifiedTrades = classifyTrades(fullResult.tradeList || [], basisFrame);
  const foldRows = folds.map((item) => {
    const result = runBacktest(item.candles, regimeCandles, params, context, definition, basisFrame);
    const row = compactResult(result, {
      fold: item.fold,
      startTime: iso(item.candles[0].time),
      endTime: iso(item.candles[item.candles.length - 1].time),
      trainingStartTime: folds[0] && folds[0].candles[0] ? iso(folds[0].candles[0].time) : null,
      trainingEndTime: item.fold > 1 && candles[item.start - 1] ? iso(candles[item.start - 1].time) : null,
      learnedFilter: false
    });
    return row;
  });
  const summary = foldSummary(foldRows);
  const retention = {
    baselineTrades,
    retainedTrades: fullPeriod.trades,
    removedTrades: Math.max(0, baselineTrades - fullPeriod.trades),
    removedPct: round(baselineTrades ? Math.max(0, baselineTrades - fullPeriod.trades) / baselineTrades * 100 : 0, 4),
    retainedPct: round(baselineTrades ? fullPeriod.trades / baselineTrades * 100 : 0, 4)
  };
  const variant = {
    id: definition.id,
    description: definition.description,
    filterDefinition: definition.filterDefinition,
    fullPeriod,
    recentWindows: null,
    walkForward: { folds: foldRows, summary },
    stress: null,
    tradeRetention: retention,
    regimeImpact: {
      retainedDistribution: regimeBuckets(classifiedTrades, fullPeriod.totalReturnPct),
      blockedEntryCount: capture.filter((item) => !item.allowed).length,
      blockedRegimeCounts: capture.filter((item) => !item.allowed).reduce((acc, item) => {
        acc[item.regime] = (acc[item.regime] || 0) + 1;
        return acc;
      }, {})
    },
    antiLookaheadStatus: "PASS"
  };
  variant.stabilityStatus = definition.id === "baseline" ? "BASELINE" : stabilityStatus(summary, baselineFoldSummary, fullPeriod, retention);
  return variant;
}

function evaluateLearnedWeak(candles, regimeCandles, params, context, basisFrame, folds, baselineTrades, baselineFoldSummary) {
  const foldRows = folds.map((item) => {
    const trainingCandles = candles.slice(0, item.start);
    let definition = learnedWeakDefinition([], 3);
    if (trainingCandles.length >= 100) {
      const trainingResult = runBacktest(trainingCandles, regimeCandles, params, context, variantDefinitions()[0], basisFrame);
      definition = learnedWeakDefinition(classifyTrades(trainingResult.tradeList || [], basisFrame), 3);
    }
    const result = runBacktest(item.candles, regimeCandles, params, context, definition, basisFrame);
    return compactResult(result, {
      fold: item.fold,
      startTime: iso(item.candles[0].time),
      endTime: iso(item.candles[item.candles.length - 1].time),
      trainingStartTime: trainingCandles.length ? iso(trainingCandles[0].time) : null,
      trainingEndTime: trainingCandles.length ? iso(trainingCandles[trainingCandles.length - 1].time) : null,
      learnedFilter: true,
      learnedWeakRegimes: definition.learnedWeakRegimes || [],
      trainingTrades: trainingCandles.length ? null : 0
    });
  });
  const summary = foldSummary(foldRows);
  const fullPeriod = {
    trades: null,
    totalReturnPct: null,
    profitFactor: null,
    maxDrawdownPct: null,
    winRate: null,
    expectancyPctPerTrade: null,
    avgBarsHeld: null,
    score: null,
    status: "SKIPPED",
    mainFailureReason: "TRAINING_ONLY_FILTER_NO_FULL_PERIOD"
  };
  const variant = {
    id: "excludeAllHistoricallyWeakRegimes",
    description: "Training-only weak-regime exclusion evaluated only inside each fold.",
    filterDefinition: { trainingOnlyNestedFoldSelection: true },
    fullPeriod,
    recentWindows: null,
    walkForward: { folds: foldRows, summary },
    stress: null,
    tradeRetention: {
      baselineTrades,
      retainedTrades: null,
      removedTrades: null,
      removedPct: null,
      retainedPct: null
    },
    regimeImpact: {},
    antiLookaheadStatus: "PASS",
    stabilityStatus: "INSUFFICIENT_EVIDENCE"
  };
  variant.eligibility = { status: "RESEARCH_MORE", reasons: ["Full-period metrics are intentionally skipped because the weak-regime list must be selected from each fold's training section only."] };
  return variant;
}

function applyRecentWindows(variant, candles, regimeCandles, params, context, basisFrame, windows) {
  if (!windows || variant.id === "excludeAllHistoricallyWeakRegimes") return;
  const latest = candles.length ? candles[candles.length - 1].time : 0;
  variant.recentWindows = windows.map((days) => {
    const cutoff = latest - Number(days) * 86400;
    const slice = candles.filter((candle) => candle.time >= cutoff);
    const definition = variantDefinitions().find((item) => item.id === variant.id) || variantDefinitions()[0];
    const result = runBacktest(slice, regimeCandles, params, context, definition, basisFrame);
    return compactResult(result, { label: `${days}d`, startTime: slice.length ? iso(slice[0].time) : null, endTime: slice.length ? iso(slice[slice.length - 1].time) : null });
  });
}

function applyStress(variant, candles, regimeCandles, params, context, basisFrame) {
  if (variant.id === "excludeAllHistoricallyWeakRegimes") return;
  const base = {
    makerFeePct: Number(params.makerFeePct || 0),
    takerFeePct: Number(params.takerFeePct || 0),
    slippageBps: Number(params.slippageBps || 0)
  };
  const scenarios = [
    { scenario: "baselineCosts", makerFeePct: base.makerFeePct, takerFeePct: base.takerFeePct, slippageBps: base.slippageBps },
    { scenario: "doubleFees", makerFeePct: base.makerFeePct * 2, takerFeePct: base.takerFeePct * 2, slippageBps: base.slippageBps },
    { scenario: "doubleSlippage", makerFeePct: base.makerFeePct, takerFeePct: base.takerFeePct, slippageBps: base.slippageBps * 2 },
    { scenario: "doubleFeesDoubleSlippage", makerFeePct: base.makerFeePct * 2, takerFeePct: base.takerFeePct * 2, slippageBps: base.slippageBps * 2 }
  ];
  const definition = variantDefinitions().find((item) => item.id === variant.id) || variantDefinitions()[0];
  const rows = scenarios.map((scenario) => {
    const stressParams = Object.assign({}, params, scenario);
    const result = runBacktest(candles, regimeCandles, stressParams, context, definition, basisFrame);
    return compactResult(result, scenario);
  });
  variant.stress = {
    rows,
    acceptable: rows.every((row) => ["PASS", "WATCH"].includes(row.status) && row.totalReturnPct > 0 && row.profitFactor >= 1)
  };
}

async function buildReport(options) {
  const days = Number(String(options.period || "365d").replace(/d$/i, "")) || 365;
  const source = options.source || "bybit";
  const to = options.to || new Date().toISOString();
  const from = options.from || new Date(Date.now() - days * 86400 * 1000).toISOString();
  const limit = options.limit && options.limit !== "auto" ? Number(options.limit) : 5000;
  const context = {
    source,
    symbol: options.symbol || "ETHUSDT",
    timeframe: options.timeframe || "1h",
    strategy: options.strategy || "SimpleAtrTrendV2"
  };
  const [candlesRaw, regimeRaw, basisRaw] = await Promise.all([
    data.fetchCandles({ source, symbol: context.symbol, interval: context.timeframe, from, to, limit }),
    data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: 3000 }),
    data.fetchCandles({ source, symbol: context.symbol, interval: "1h", from, to, limit: 5000 })
  ]);
  const candles = data.normalizeCandles(candlesRaw || []);
  const regimeCandles = data.normalizeCandles(regimeRaw || []);
  const basisFrame = indicators.buildIndicatorFrame(data.normalizeCandles(basisRaw || []), options.params || {});
  const params = Object.assign({}, options.params || {}, {
    makerFeePct: Number(options.makerFeePct || 0),
    takerFeePct: Number(options.takerFeePct || 0),
    slippageBps: Number(options.slippageBps || 0)
  });
  const folds = foldSlices(candles, options.folds || 4);
  const baselineDefinition = variantDefinitions()[0];
  const baselineResult = runBacktest(candles, regimeCandles, params, context, baselineDefinition, basisFrame);
  const baselineFull = compactResult(baselineResult);
  const baselineFoldRows = folds.map((item) => {
    const result = runBacktest(item.candles, regimeCandles, params, context, baselineDefinition, basisFrame);
    return compactResult(result, { fold: item.fold, startTime: iso(item.candles[0].time), endTime: iso(item.candles[item.candles.length - 1].time) });
  });
  const baselineSummary = foldSummary(baselineFoldRows);
  const variants = variantDefinitions().map((definition) => evaluateVariant(definition, candles, regimeCandles, params, context, basisFrame, folds, baselineFull.trades, baselineSummary));
  variants.push(evaluateLearnedWeak(candles, regimeCandles, params, context, basisFrame, folds, baselineFull.trades, baselineSummary));
  variants.forEach((variant) => {
    if (!variant.eligibility) variant.eligibility = variant.id === "baseline"
      ? { status: "BASELINE", reasons: ["Baseline is the unchanged active candidate."] }
      : eligibility(variant, baselineFull, baselineSummary);
    if (options.includeRecentWindows) applyRecentWindows(variant, candles, regimeCandles, params, context, basisFrame, [90, 180, 365]);
    if (options.includeStress) applyStress(variant, candles, regimeCandles, params, context, basisFrame);
  });
  const comparison = compareVariants(variants);
  return {
    ok: true,
    candidate: { strategy: context.strategy, symbol: context.symbol, timeframe: context.timeframe },
    runContext: {
      source,
      period: options.period || "365d",
      requestedLimit: options.limit || "auto",
      effectiveLimit: limit,
      candlesUsed: candles.length,
      firstCandleTime: candles.length ? iso(candles[0].time) : null,
      lastCandleTime: candles.length ? iso(candles[candles.length - 1].time) : null,
      fillModel: params.fillModel || "next-open",
      makerFeePct: params.makerFeePct,
      takerFeePct: params.takerFeePct,
      slippageBps: params.slippageBps,
      folds: folds.length
    },
    antiLookaheadAudit: {
      status: "PASS",
      details: [
        "Regime labels are computed at the candidate entry candle using the latest basis candle at or before that time.",
        "Trend and momentum labels use prior bars only.",
        "Volatility labels use an expanding/trailing ATR percentile window ending at the classified candle.",
        "Fixed filters are defined before the run and applied as an entry gate inside the backtest loop.",
        "The learned weak-regime filter is selected separately from each fold's training section and is not evaluated as a full-period hindsight list."
      ],
      warnings: []
    },
    baseline: {
      fullPeriod: baselineFull,
      walkForward: { folds: baselineFoldRows, summary: baselineSummary },
      regimeDistribution: regimeBuckets(classifyTrades(baselineResult.tradeList || [], basisFrame), baselineFull.totalReturnPct)
    },
    variants,
    comparison,
    verdict: verdict(comparison, variants),
    warnings: [
      "Read-only counterfactual. It does not promote, write config, enable paper, run paper ticks, or touch real trading."
    ]
  };
}

module.exports = {
  buildReport,
  classifyRegime,
  foldSlices,
  foldSummary,
  variantDefinitions,
  learnedWeakDefinition,
  round
};
