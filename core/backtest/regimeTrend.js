const data = require("../data");
const indicators = require("../indicators");
const regime = require("../regime");
const marketAssessment = require('./marketAssessment');

const MTF_CONTEXT_CACHE = new Map();
const MTF_CONTEXT_CACHE_LIMIT = 12;

const BLOCKERS = [
  "regimeNotBullish",
  "regimeNotBearish",
  "neutralRegime",
  "emaTrendFailed",
  "donchianBreakoutFailed",
  "adxTooLow",
  "volumeTooLow",
  "fundingTooHigh",
  "openInterestNotRising",
  "atrMissing",
  "stopTooClose",
  "maxOpenTradesReached",
  "dailyLossLimitReached",
  "alreadyInPosition",
  "cooldownBlocked",
  "researchRegimeFilterBlocked",
  "causalRegimeFilterBlocked",
  "pullbackReclaimFailed",
  "retestFailed",
  "squeezeFailed",
  "rangeBreakoutFailed",
  "adxNotRising",
  "meanReversionFailed",
  "momentumStrengthFailed"
  ,"relativeStrengthFailed",
  "fibPullbackFailed",
  "structureBreakFailed",
  "vwapConfirmationFailed",
  "marketTooChoppy",
  "volumeFlowWeak",
  "volatilityRegimeBlocked",
  "stochRsiBlocked",
  "keltnerSqueezeBlocked",
  "mtfTrendBlocked",
  "mtfTimingBlocked",
  "rangeRegimeBlocked",
  "vwapReversionFailed"
];

function run(options) {
  var strategyName = options.strategy || "RegimeFilteredTrendStrategy";
  var params = Object.assign({}, defaultParams(strategyName), options.params || {});
  if (options.assessmentOnly) delete params.researchExitMode;
  params = normalizeExecutionParams(params);
  params.strategyName = strategyName;
  params.executionTimeframe = options.executionInterval || options.executionTimeframe || options.execution_timeframe || options.interval || options.timeframe;
  if (typeof options.customEntry === "function") params.customEntry = options.customEntry;
  var candles = data.normalizeCandles(options.candles || []);
  if (params.researchExitMode) {
    var researchAsOf = Number(options.asOf || Math.floor(Date.now()/1000));
    var researchStep = executionIntervalSeconds(options.interval||options.timeframe);
    candles = candles.filter(bar=>bar.time+researchStep<=researchAsOf);
    options = Object.assign({},options,{executionCandles:(options.executionCandles||[]).filter(bar=>bar.time+300<=researchAsOf)});
  }
  var regimeCandles = data.normalizeCandles(options.regimeCandles || []);
  var benchmarkCandles = data.normalizeCandles(options.benchmarkCandles || []);
  var mapped = regime.mapRegimeToCandles(candles, regimeCandles, benchmarkCandles, {
    interval: options.interval || options.timeframe, benchmarkInterval: options.benchmarkInterval
  });
  var frameParams = Object.assign({}, params);
  ["riskPct", "accountEquity", "maxNotional", "maxPositionPct", "maxDailyLossPct"].forEach(function (key) { delete frameParams[key]; });
  var frameKey = options.frameCache ? JSON.stringify([mapped, frameParams]) : null;
  var frame = options.frameCache && options.frameCache.get(frameKey);
  if (!frame) {
    frame = indicators.buildIndicatorFrame(mapped, Object.assign({}, params, {
      emaReclaim: params.emaFast || 20,
      emaTrendFast: params.emaSlow || 50,
      emaSlow: params.emaTrend || params.emaTrendLength,
      atrPeriod: 14,
      adxPeriod: 14
    })).map(function (row, index) {
      return Object.assign({}, row, {
        btcRegime: mapped[index] ? mapped[index].btcRegime : "neutral",
        btcRegimeTime: mapped[index] ? mapped[index].btcRegimeTime : null
      });
    });
    frame = attachMultiTimeframeContext(frame, params);
    if (options.frameCache) {
      options.frameCache.set(frameKey, frame);
      while (options.frameCache.size > 12) options.frameCache.delete(options.frameCache.keys().next().value);
    }
  }
  var execution = buildExecutionContext(options, frame);
  if (params.researchExitMode && !['staged','adaptive','momentum'].includes(params.researchExitMode)) throw new Error('Unknown research exit mode');
  if (params.researchExitMode && (!execution.active || execution.interval !== '5m')) throw new Error('Research exits require real 5m execution candles');
  if (params.researchExitMode && frame.length > 200) {
    var requiredStart = frame[200].time;
    var requiredEnd = frame[frame.length-1].time + executionIntervalSeconds(options.interval||options.timeframe);
    var bars = execution.candles.filter(bar=>bar.time>=requiredStart && bar.time<requiredEnd);
    if (bars.length !== Math.round((requiredEnd-requiredStart)/300) || bars[0].time!==requiredStart || bars.some((bar,index)=>index && bar.time-bars[index-1].time!==300)) {
      throw new Error('Research exits need complete 5m coverage after warmup; shorten the period or collect missing history.');
    }
  }
  var decisionOptions = Object.assign({}, options, {executionCandles: execution.candles});
  var state = {
    cash: Number(params.accountEquity),
    peak: Number(params.accountEquity),
    equityCurve: [],
    trades: [],
    position: null,
    pendingEntry: null,
    pendingExit: null,
    cooldown: 0,
    exposureBars: 0,
    grossPnl: 0,
    totalFees: 0,
    totalSlippage: 0,
    dailyRealizedPnl: {},
    diagnostics: createDiagnostics(frame.length, params)
  };
  state.diagnostics.execution = execution.diagnostics;
  var resume = options.resumeState;
  var checkpointIdentity = JSON.stringify([strategyName, options.interval, execution.interval, params]);
  var startIndex = 0;
  if (resume) {
    if (resume.schemaVersion !== 1 || (resume.identity && resume.identity !== checkpointIdentity)) {
      throw new Error("Paper checkpoint configuration changed; explicit migration is required.");
    }
    var anchorIndex = frame.findIndex(function (row) { return Number(row.time) === Number(resume.lastSignalTime); });
    if (anchorIndex < 0) throw new Error("Paper checkpoint candle is missing from signal history.");
    var saved = JSON.parse(JSON.stringify(resume.state));
    Object.keys(saved).forEach(function (key) { state[key] = saved[key]; });
    var shift = anchorIndex - Number(resume.lastSignalIndex);
    if (state.position) {
      state.position.entryIndex += shift;
      state.position.entrySignalIndex += shift;
      state.position.executionCursor = 0;
    }
    [state.pendingEntry, state.pendingExit].forEach(function (pending) {
      if (pending) {
        pending.signalIndex = frame.findIndex(function (row) { return Number(row.time) === Number(pending.signalTime); });
        if (pending.signalIndex < 0) throw new Error("Pending paper order is missing its signal candle.");
      }
    });
    startIndex = anchorIndex + 1;
  }
  var processedSignalCandles = 0;
  var readinessRows = [];
  var readinessTrigger = null;

  for (var i = startIndex; i < frame.length; i += 1) {
    processedSignalCandles += 1;
    var row = frame[i];
    if (state.pendingExit && state.position) {
      closePosition(state, frame, i, row.open, state.pendingExit.reason, params, state.pendingExit);
      state.pendingExit = null;
    }
    if (state.pendingEntry && !state.position) {
      openPosition(state, frame, state.pendingEntry.signalIndex, i, state.pendingEntry.side, params, executionFillRow(execution, row.time));
      state.pendingEntry = null;
    }
    if (state.position) {
      state.exposureBars += 1;
      var signalCloseTime = row.time + executionIntervalSeconds(options.interval || options.timeframe);
      var lastClosedExecutionTime = signalCloseTime - executionIntervalSeconds(execution.interval);
      if (execution.active && closeOnExecutionCandle(state, frame, i, params, execution, lastClosedExecutionTime)) {
        // Lower-timeframe stop/take-profit already closed the position before the signal close.
      }
      if (!state.position) {
        if (options.collectReadiness) readinessRows.push({time:row.time, closedAt:signalCloseTime, status:'EXIT', score:null, blockers:[], executionTimeframe:execution.interval, positionContext:'hypothetical tactic position; not your account'});
        var flatEquity = currentEquity(state, row);
        state.peak = Math.max(state.peak, flatEquity);
        state.equityCurve.push({
          time: row.time,
          equity: round(flatEquity, 4),
          equityPct: round((flatEquity / params.accountEquity - 1) * 100, 4)
        });
        state.atrTrailPoints = state.atrTrailPoints || [];
        state.atrTrailPoints.push({ time: row.time, value: null });
        if (!state.position && state.cooldown > 0) state.cooldown -= 1;
        continue;
      }
      var exit = exitSignal(state.position, frame, i, params, execution);
      if (exit) {
        if (shouldDeferExit(exit, params, i, frame.length)) {
          state.pendingExit = {
            signalIndex: i,
            signalTime: row.time,
            signalPrice: exit.price,
            reason: exit.reason,
            kind: exit.kind
          };
        } else {
          closePosition(state, frame, i, exit.price, exit.reason, params, {
            signalIndex: i,
            signalTime: row.time,
            signalPrice: exit.price,
            kind: exit.kind
          });
        }
      }
      if (state.position) updateTrailing(state.position, row, params);
    }

    var entry = applyResearchEntryFilter(entrySignal(frame, i, state, params), frame, i, state, params, decisionOptions);
    if (options.collectReadiness) {
      var flatState = Object.assign({}, state, {position: null, pendingEntry: null, cooldown: 0});
      var setup = applyResearchEntryFilter(entrySignal(frame, i, flatState, params), frame, i, flatState, params, decisionOptions);
      if (!setup.passed) readinessTrigger = null;
      else if (!readinessTrigger || readinessTrigger.side !== setup.side) readinessTrigger = {index: i, price: row.close, side: setup.side, time: row.time};
      readinessRows.push(entryReadiness(frame, i, state, params, execution, entry, setup, readinessTrigger));
    }
    collectDiagnostics(state.diagnostics, frame, i, entry, state);
    if (!state.position && !state.pendingEntry && entry.passed && dailyLossAllowsEntry(state, row, params)) {
      if (shouldDeferEntry(params, i, frame.length)) {
        state.pendingEntry = {
          signalIndex: i,
          signalTime: row.time,
          signalPrice: row.close,
          side: entry.side
        };
      } else {
        openPosition(state, frame, i, i, entry.side, params, executionFillRow(execution, row.time));
      }
    } else if (!state.position && entry.passed && !dailyLossAllowsEntry(state, row, params)) {
      state.diagnostics.blockerCounts.dailyLossLimitReached += 1;
    }

    var equity = currentEquity(state, row);
    state.peak = Math.max(state.peak, equity);
    state.equityCurve.push({
      time: row.time,
      equity: round(equity, 4),
      equityPct: round((equity / params.accountEquity - 1) * 100, 4)
    });
    state.atrTrailPoints = state.atrTrailPoints || [];
    state.atrTrailPoints.push({
      time: row.time,
      value: state.position ? round(state.position.trailingStop, 8) : null
    });
    if (!state.position && state.cooldown > 0) state.cooldown -= 1;
  }

  if (state.position && execution.active && execution.candles.length && frame.length) {
    var tailIndex = frame.length - 1;
    var tailTime = execution.candles[execution.candles.length - 1].time;
    closeOnExecutionCandle(state, frame, tailIndex, params, execution, tailTime);
  }

  var checkpoint = null;
  if (options.incremental && frame.length) {
    var persisted = {};
    ["cash", "peak", "position", "pendingEntry", "pendingExit", "cooldown", "exposureBars", "grossPnl", "totalFees", "totalSlippage", "dailyRealizedPnl"].forEach(function (key) { persisted[key] = state[key]; });
    checkpoint = JSON.parse(JSON.stringify({
      schemaVersion: 1,
      identity: checkpointIdentity,
      lastSignalTime: frame[frame.length - 1].time,
      lastSignalIndex: frame.length - 1,
      state: persisted
    }));
    // End-of-data projection is display-only; the persisted position stays open.
    state = Object.assign({}, state, { position: state.position && Object.assign({}, state.position) });
  }
  if (state.position && frame.length) {
    var lastIndex = frame.length - 1;
    closePosition(state, frame, lastIndex, frame[lastIndex].close, "End of data", params, {
      signalIndex: lastIndex,
      signalTime: frame[lastIndex].time,
      signalPrice: frame[lastIndex].close,
      kind: "end"
    });
  }

  if (params.researchExitMode && state.equityCurve.length) {
    var finalPoint = state.equityCurve[state.equityCurve.length-1];
    finalPoint.equity = round(state.cash,4);
    finalPoint.equityPct = round((state.cash/params.accountEquity-1)*100,4);
  }
  var result = formatResult(options, params, frame, state);
  if (options.collectReadiness) {
    var reviewState = {cash:Number(params.accountEquity),position:null,pendingEntry:null,pendingExit:null,cooldown:0,dailyRealizedPnl:{}};
    var review = frame.length ? entrySignal(frame,frame.length-1,reviewState,params) : null;
    result.marketAssessment = marketAssessment.assess(indicators.buildIndicatorFrame(candles,{}),execution,params,{signalSeconds:executionIntervalSeconds(options.interval||options.timeframe),asOf:options.assessmentAsOf,
      tacticSetup:review && {passed:review.passed,side:review.side,blockers:review.blockers}});
  }
  if (options.collectReadiness) result.entryReadiness = {version: 'entry-readiness-v1', validation: 'experimental; not calibrated', rows: readinessRows};
  if (checkpoint) {
    result.checkpoint = checkpoint;
    result.processedSignalCandles = processedSignalCandles;
  }
  return result;
}

function applyResearchEntryFilter(entry, frame, index, state, params, options) {
  if (!entry || !entry.passed) return entry;
  if (params.strategyName === 'RelativeStrengthFreshV3') {
    var closeTime = frame[index].time + executionIntervalSeconds(options.interval || options.timeframe);
    var step = executionIntervalSeconds(options.executionInterval || options.executionTimeframe);
    var lower = closedExecutionBars(options.executionCandles || [], frame[index].time, closeTime, step);
    var expected = Math.round((closeTime - frame[index].time) / step);
    var covered = step === 300 && lower.length === expected;
    for (var k = 1; k < lower.length; k++) if (lower[k].time - lower[k-1].time !== step) covered = false;
    if (!covered || !(lower[lower.length-1].close > lower[lower.length-1].open)) return {passed:false, side:'long', blockers:[covered ? 'executionTimingBlocked' : 'executionCoverageMissing']};
  }
  if (typeof options.entryFilter === "function") {
    var decision = options.entryFilter({
      frame: frame,
      index: index,
      row: frame[index],
      state: state,
      params: params,
      entry: entry
    });
    if (!(decision === true || (decision && decision.allow === true))) {
      var reason = decision && decision.reason ? String(decision.reason) : "researchRegimeFilterBlocked";
      return {
        passed: false,
        side: entry.side || "long",
        blockers: ["researchRegimeFilterBlocked"],
        researchFilter: {
          blocked: true,
          reason: reason,
          regime: decision && decision.regime ? decision.regime : null
        }
      };
    }
  }
  var causalDecision = applyCausalRegimeFilter(frame, index, params);
  if (causalDecision.allow) return entry;
  return {
    passed: false,
    side: entry.side || "long",
    blockers: ["causalRegimeFilterBlocked"],
    causalRegimeFilter: {
      blocked: true,
      reason: causalDecision.reason,
      regime: causalDecision.regime,
      definition: causalDecision.definition
    }
  };
}

function applyCausalRegimeFilter(frame, index, params) {
  var definition = normalizeCausalRegimeFilter(params.causalRegimeFilter || params.entryRegimeFilter);
  if (!definition) return { allow: true };
  var regimeInfo = classifyCausalRegime(frame, index);
  if (definition.allowedMomentum && definition.allowedMomentum.indexOf(regimeInfo.momentum) < 0) {
    return { allow: false, reason: "causalRegimeFilterBlocked", regime: regimeInfo, definition: definition };
  }
  if (definition.allowedVolatility && definition.allowedVolatility.indexOf(regimeInfo.volatility) < 0) {
    return { allow: false, reason: "causalRegimeFilterBlocked", regime: regimeInfo, definition: definition };
  }
  if (definition.allowedTrend && definition.allowedTrend.indexOf(regimeInfo.trend) < 0) {
    return { allow: false, reason: "causalRegimeFilterBlocked", regime: regimeInfo, definition: definition };
  }
  if (definition.excludedRegimes && definition.excludedRegimes.indexOf(regimeInfo.regime) >= 0) {
    return { allow: false, reason: "causalRegimeFilterBlocked", regime: regimeInfo, definition: definition };
  }
  return { allow: true, regime: regimeInfo, definition: definition };
}

function normalizeCausalRegimeFilter(raw) {
  if (!raw || raw === true) return null;
  if (typeof raw === "string") {
    var key = raw.trim();
    if (!key || key === "none" || key === "off" || key === "false") return null;
    if (key === "bullishOnly") return { allowedMomentum: ["bullish"] };
    if (key === "lowOrMediumVolBullish") return { allowedMomentum: ["bullish"], allowedVolatility: ["lowVol", "mediumVol"] };
    return { excludedRegimes: [key] };
  }
  if (typeof raw !== "object") return null;
  var definition = {};
  ["allowedMomentum", "allowedVolatility", "allowedTrend", "excludedRegimes"].forEach(function (key) {
    if (raw[key] === undefined || raw[key] === null) return;
    definition[key] = Array.isArray(raw[key]) ? raw[key].map(String) : [String(raw[key])];
  });
  return Object.keys(definition).length ? definition : null;
}

function classifyCausalRegime(frame, index) {
  var row = frame[index] || null;
  if (!row) {
    return {
      regime: "unknown_unknownVol_neutral",
      trend: "unknown",
      volatility: "unknownVol",
      momentum: "neutral",
      rowTime: null
    };
  }
  var trend = causalTrendLabel(frame, index);
  var volatility = causalVolatilityLabel(frame, index);
  var momentum = causalMomentumLabel(frame, index);
  return {
    regime: [trend, volatility, momentum].join("_"),
    trend: trend,
    volatility: volatility,
    momentum: momentum,
    rowTime: row.time
  };
}

function causalTrendLabel(frame, index) {
  var row = frame[index] || {};
  var prior = frame[Math.max(0, index - 24)] || {};
  var slopePct = prior.ema50 ? (row.ema50 - prior.ema50) / prior.ema50 * 100 : 0;
  if (row.ema50 && row.ema200 && row.ema50 > row.ema200 && slopePct > 0.15) return "uptrend";
  if (row.ema50 && row.ema200 && row.ema50 < row.ema200 && slopePct < -0.15) return "downtrend";
  return "sideways";
}

function causalVolatilityLabel(frame, index) {
  var row = frame[index] || {};
  var value = Number(row.atrPct);
  if (!Number.isFinite(value)) return "unknownVol";
  var start = Math.max(0, index - 500);
  var history = frame.slice(start, index + 1).map(function (item) {
    return Number(item.atrPct);
  }).filter(Number.isFinite).sort(function (a, b) { return a - b; });
  if (history.length < 30) return "mediumVol";
  var low = history[Math.max(0, Math.min(history.length - 1, Math.floor((history.length - 1) * 0.33)))];
  var high = history[Math.max(0, Math.min(history.length - 1, Math.floor((history.length - 1) * 0.66)))];
  if (value <= low) return "lowVol";
  if (value >= high) return "highVol";
  return "mediumVol";
}

function causalMomentumLabel(frame, index) {
  var row = frame[index] || {};
  var prior = frame[Math.max(0, index - 12)] || {};
  var returnPct = prior.close ? (row.close - prior.close) / prior.close * 100 : 0;
  if ((row.rsi14 || 0) >= 55 || returnPct >= 1) return "bullish";
  if ((row.rsi14 || 0) <= 45 || returnPct <= -1) return "bearish";
  return "neutral";
}

function defaultParams(strategyName) {
  if (strategyName === 'RelativeStrengthFreshV3') return Object.assign({}, defaultParams('RelativeStrengthV2'), {
    freshnessMaxBars: 0, freshnessMaxDriftAtr: 0.5, readinessCostMultiple: 3,
    takerFeePct: 0.055, slippagePct: 0.02, slippageBps: 2, maxPositionPct: 100, maxNotional: 10000
  });
  var base = {
    accountEquity: 10000,
    riskPct: 0.005,
    atrMultiplier: 2.5,
    takerFeePct: 0,
    makerFeePct: 0,
    slippagePct: 0,
    slippageBps: null,
    fillModel: "next-open",
    maxOpenTrades: 1,
    maxNotional: 100000,
    donchianEntry: 55,
    donchianExit: 20,
    adxThreshold: 18,
    emaTrendLength: 200,
    volumeFilter: true,
    shortMode: false,
    cooldownBars: 0,
    indicatorFilterPreset: "none",
    useChoppinessFilter: false,
    maxChoppiness: 61.8,
    useVolumeFlowFilter: false,
    minCmf: 0,
    requireObvAboveMa: true,
    useAtrPercentileFilter: false,
    minAtrPctRank: 15,
    maxAtrPctRank: 85,
    useStochRsiFilter: false,
    stochRsiMin: 20,
    stochRsiMax: 85,
    useKeltnerSqueezeFilter: false,
    maxSqueezeRatio: 1,
    keltnerSqueezeLookback: 20
  };
  if (strategyName === "RegimeDonchian20") {
    base.donchianEntry = 20;
    base.donchianExit = 10;
    base.volumeFilter = false;
  }
  if (strategyName === "RegimeDonchianCloseConfirm") {
    base.donchianEntry = 20;
    base.donchianConfirmAlt = 55;
    base.donchianExit = 20;
    base.volumeFilter = false;
  }
  if (strategyName === "RegimePullbackTrend") {
    base.volumeFilter = false;
    base.rsiPullbackLevel = 45;
    base.rsiReclaimLevel = 50;
  }
  if (strategyName === "EmaPullbackContinuation") {
    base.volumeFilter = false;
    base.rsiPullbackLevel = 45;
    base.rsiReclaimLevel = 50;
    base.donchianExit = 20;
  }
  if (strategyName === "TrendBreakoutRetest") {
    base.donchianEntry = 20;
    base.donchianExit = 10;
    base.volumeFilter = false;
    base.retestLookback = 8;
    base.retestAtr = 0.6;
  }
  if (strategyName === "VolatilitySqueezeBreakout") {
    base.volumeFilter = false;
    base.squeezeLookback = 100;
    base.squeezePercentile = 0.25;
    base.rangeLookback = 20;
    base.adxThreshold = 14;
  }
  if (strategyName === "MeanReversionInBullRegime") {
    base.volumeFilter = false;
    base.rsiOversold = 35;
    base.donchianExit = 20;
  }
  if (strategyName === "MomentumContinuation") {
    base.volumeFilter = true;
    base.rsiMin = 50;
    base.rsiMax = 70;
    base.adxThreshold = 16;
    base.minBodyPct = 0.35;
  }
  if (strategyName.indexOf("V2") !== -1) {
    base.regimeMode = base.regimeMode || "looseBtcBull";
    base.volumeFilter = false;
    base.adxThreshold = 0;
    base.atrMultiplier = 2.4;
  }
  if (strategyName === "PullbackReclaimV2") {
    base.rsiPullbackLevel = 45;
    base.rsiReclaimLevel = 50;
    base.useAdx = false;
  }
  if (strategyName === "EmaBounceV2") {
    base.emaBounceAtr = 0.8;
  }
  if (strategyName === "BreakoutRetestV2") {
    base.donchianEntry = 20;
    base.donchianExit = 10;
    base.retestLookback = 10;
    base.retestAtr = 0.8;
  }
  if (strategyName === "RangeExpansionV2") {
    base.squeezeLookback = 80;
    base.squeezePercentile = 0.35;
    base.rangeLookback = 20;
    base.rangeSma = 20;
    base.closeHighPct = 0.7;
  }
  if (strategyName === "RelativeStrengthV2") {
    base.rsLookback = 24;
    base.rsThreshold = 0;
    base.rsiMin = 48;
    base.rsiMax = 74;
  }
  if (strategyName === "SimpleAtrTrendV2") {
    base.useRsiFilter = true;
    base.emaFast = 20;
    base.emaSlow = 50;
    base.emaTrend = 200;
    base.rsiMin = 45;
    base.rsiMax = 70;
    base.cooldownBars = 0;
    base.minHoldBars = 1;
  }
  if (strategyName === "FibPullbackContinuationV1") {
    base.regimeMode = "symbolFastTrend";
    base.volumeFilter = false;
    base.useAdx = false;
    base.atrMultiplier = 1.8;
    base.takeProfitAtr = 2.8;
    base.rsiPullbackLevel = 45;
    base.rsiReclaimLevel = 53;
    base.goldenPocketTolerancePct = 0.35;
    base.requireAnchoredVwap = true;
    base.cooldownBars = 4;
    base.minHoldBars = 2;
    base.donchianExit = 20;
  }
  if (strategyName === "MtfPullback15mV1") {
    base.regimeMode = "symbolFastTrend";
    base.volumeFilter = false;
    base.useAdx = false;
    base.atrMultiplier = 1.8;
    base.takeProfitAtr = 2.8;
    base.cooldownBars = 8;
    base.minHoldBars = 2;
    base.donchianEntry = 20;
    base.donchianExit = 20;
    base.mtfRequire1h = true;
    base.mtfRequire4h = true;
    base.mtf1hTrendEma = 50;
    base.mtf4hTrendEma = 100;
    base.mtfMinRsi = 45;
    base.mtfDirection = "long";
    base.mtfEntryMode = "pullback";
    base.rsiPullbackLevel = 42;
    base.rsiReclaimLevel = 50;
    base.emaBounceAtr = 0.8;
    base.indicatorFilterPreset = "pullbackTiming";
    base.fillModel = "next-open";
  }
  if (strategyName === "VwapReversion15mV1") {
    base.regimeMode = "noRegime";
    base.volumeFilter = false;
    base.atrMultiplier = 1.4;
    base.takeProfitAtr = 1.8;
    base.cooldownBars = 4;
    base.minHoldBars = 1;
    base.donchianExit = 20;
    base.reversionDirection = "both";
    base.minVwapDistanceAtr = 0.8;
    base.rsiOversold = 32;
    base.rsiOverbought = 68;
    base.stochRsiOversold = 20;
    base.stochRsiOverbought = 80;
    base.maxAdx = 28;
    base.minChoppiness = 45;
    base.requireBbTouch = true;
    base.requireCmfConfirm = false;
    base.fillModel = "next-open";
  }
  if (strategyName === "VwapReversion15mFastV1") {
    base.regimeMode = "noRegime";
    base.volumeFilter = false;
    base.atrMultiplier = 1.1;
    base.takeProfitAtr = 1.2;
    base.cooldownBars = 0;
    base.minHoldBars = 0;
    base.donchianExit = 10;
    base.reversionDirection = "both";
    base.minVwapDistanceAtr = 0.35;
    base.rsiOversold = 42;
    base.rsiOverbought = 58;
    base.stochRsiOversold = 45;
    base.stochRsiOverbought = 55;
    base.maxAdx = 45;
    base.minChoppiness = 0;
    base.requireBbTouch = false;
    base.requireCmfConfirm = false;
    base.fillModel = "next-open";
  }
  if (strategyName === "RobustTrendFollowerV1") {
    base.regimeMode = "looseBtcBull";
    base.emaMid = 100;
    base.emaSlow = 200;
    base.donchianEntry = 55;
    base.donchianExit = 20;
    base.adxThreshold = 20;
    base.volumeFilter = true;
    base.volumeMultiplier = 1.2;
    base.volumeMa = 20;
    base.useFundingFilter = false;
    base.maxFundingRate = 0.0005;
    base.useOpenInterestFilter = false;
    base.minOpenInterestChangePct = 0;
    base.atrMultiplier = 2.5;
    base.useDonchianTrail = true;
    base.riskPct = 0.005;
    base.cooldownBars = 0;
    base.minHoldBars = 1;
    base.shortMode = false;
    base.fillModel = "next-open";
  }
  if (strategyName === "BtcMeanReversion4hV1") {
    base.regimeMode = "symbolTrend";
    base.volumeFilter = false;
    base.atrMultiplier = 1.6;
    base.takeProfitAtr = 2.2;
    base.cooldownBars = 2;
    base.minHoldBars = 1;
    base.donchianExit = 20;
    base.rsiOversold = 40;
    base.rsiReclaim = 45;
    base.maxAdx = 100;
    base.minChoppiness = 0;
    base.requireBbTouch = false;
    base.requireCmfConfirm = false;
    base.fillModel = "next-open";
  }
  if (strategyName === "SqueezePullbackHold4hV1") {
    base.regimeMode = "symbolTrend";
    base.volumeFilter = false;
    base.atrMultiplier = 4;
    base.takeProfitAtr = 99;
    base.cooldownBars = 0;
    base.minHoldBars = 0;
    base.maxHoldBars = 24;
    base.squeezeMode = "squeezeOn";
    base.pullbackEma = 50;
    base.requireTrend = false;
    base.requireMomentum = false;
    base.minRsi = 0;
    base.riskPct = 0.005;
    base.fillModel = "next-open";
  }
  if (strategyName === "BtcRegimeController4hV1") {
    base.regimeMode = "symbolTrend";
    base.volumeFilter = false;
    base.atrMultiplier = 2.3;
    base.takeProfitAtr = 3.5;
    base.cooldownBars = 2;
    base.minHoldBars = 1;
    base.maxHoldBars = 36;
    base.donchianEntry = 20;
    base.donchianExit = 10;
    base.retestLookback = 5;
    base.rsiPullbackLevel = 42;
    base.rsiReclaimLevel = 50;
    base.rsiOversold = 38;
    base.rsiReclaim = 45;
    base.maxMeanReversionAdx = 32;
    base.minTrendAdx = 12;
    base.controllerMode = "adaptive";
    base.riskPct = 0.005;
    base.fillModel = "next-open";
  }
  return base;
}

function normalizeExecutionParams(params) {
  var out = Object.assign({}, params);
  out.fillModel = String(out.fillModel || out["fill-model"] || "next-open");
  if (!["close", "next-open", "conservative"].includes(out.fillModel)) out.fillModel = "next-open";
  if (out.feePct !== undefined) out.takerFeePct = Number(out.feePct);
  if (out.slippageBps === null || out.slippageBps === undefined) {
    out.slippageBps = out.slippagePct !== undefined ? Number(out.slippagePct) * 100 : 0;
  }
  out.takerFeePct = Number(out.takerFeePct || 0);
  out.makerFeePct = Number(out.makerFeePct || 0);
  out.slippageBps = Number(out.slippageBps || 0);
  out.slippagePct = out.slippageBps / 100;
  out.roundTripCostPct = round(out.takerFeePct * 2 + out.slippagePct * 2, 6);
  out.maxPositionPct = Number(out.maxPositionPct || 0);
  out.maxDailyLossPct = Number(out.maxDailyLossPct || 0);
  out.initialStopAtrMultiplier = Number(out.initialStopAtrMultiplier || out.atrMultiplier || 0);
  out.trailingAtrMultiplier = Number(out.trailingAtrMultiplier || out.atrMultiplier || 0);
  out.trailingActivationR = Math.max(0, Number(out.trailingActivationR || 0));
  out.breakEvenAtR = Math.max(0, Number(out.breakEvenAtR || 0));
  return out;
}

function dailyLossAllowsEntry(state, row, params) {
  if (!(params.maxDailyLossPct > 0)) return true;
  var key = new Date(Number(row.time) * 1000).toISOString().slice(0, 10);
  var limit = Number(params.accountEquity) * params.maxDailyLossPct / 100;
  return Number(state.dailyRealizedPnl[key] || 0) > -limit;
}

function entrySignal(frame, index, state, params) {
  if (typeof params.customEntry === "function") return params.customEntry(frame.slice(0, index + 1), index, state, params);
  if (params.strategyName === "RegimeDonchian20") return donchian20Entry(frame, index, state, params);
  if (params.strategyName === "RegimeDonchianCloseConfirm") return closeConfirmEntry(frame, index, state, params);
  if (params.strategyName === "RegimePullbackTrend") return pullbackTrendEntry(frame, index, state, params);
  if (params.strategyName === "EmaPullbackContinuation") return emaPullbackContinuationEntry(frame, index, state, params);
  if (params.strategyName === "TrendBreakoutRetest") return trendBreakoutRetestEntry(frame, index, state, params);
  if (params.strategyName === "VolatilitySqueezeBreakout") return volatilitySqueezeBreakoutEntry(frame, index, state, params);
  if (params.strategyName === "MeanReversionInBullRegime") return meanReversionBullEntry(frame, index, state, params);
  if (params.strategyName === "MomentumContinuation") return momentumContinuationEntry(frame, index, state, params);
  if (params.strategyName === "PullbackReclaimV2") return pullbackReclaimV2Entry(frame, index, state, params);
  if (params.strategyName === "EmaBounceV2") return emaBounceV2Entry(frame, index, state, params);
  if (params.strategyName === "BreakoutRetestV2") return breakoutRetestV2Entry(frame, index, state, params);
  if (params.strategyName === "RangeExpansionV2") return rangeExpansionV2Entry(frame, index, state, params);
  if (params.strategyName === "RelativeStrengthV2") return relativeStrengthV2Entry(frame, index, state, params);
  if (params.strategyName === 'RelativeStrengthFreshV3') return relativeStrengthFreshV3Entry(frame, index, state, params);
  if (params.strategyName === "SimpleAtrTrendV2") return simpleAtrTrendV2Entry(frame, index, state, params);
  if (params.strategyName === "FibPullbackContinuationV1") return fibPullbackContinuationEntry(frame, index, state, params);
  if (params.strategyName === "MtfPullback15mV1") return mtfPullback15mEntry(frame, index, state, params);
  if (params.strategyName === "VwapReversion15mV1") return vwapReversion15mEntry(frame, index, state, params);
  if (params.strategyName === "VwapReversion15mFastV1") return vwapReversion15mEntry(frame, index, state, params);
  if (params.strategyName === "RobustTrendFollowerV1") return robustTrendFollowerV1Entry(frame, index, state, params);
  if (params.strategyName === "BtcMeanReversion4hV1") return btcMeanReversion4hEntry(frame, index, state, params);
  if (params.strategyName === "SqueezePullbackHold4hV1") return squeezePullbackHold4hEntry(frame, index, state, params);
  if (params.strategyName === "BtcRegimeController4hV1") return btcRegimeController4hEntry(frame, index, state, params);
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (row.btcRegime === "neutral") blockers.push("neutralRegime");
  if (row.btcRegime !== "bullish") blockers.push("regimeNotBullish");
  if (!(row.close > trendEma(row, params))) blockers.push("emaTrendFailed");
  if (!breaksAboveDonchian(row, previous, params.donchianEntry)) blockers.push("donchianBreakoutFailed");
  if (!(row.adx14 > params.adxThreshold)) blockers.push("adxTooLow");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");
  if (blockers.length === 0) return { passed: true, side: "long", blockers: [] };

  if (params.shortMode) {
    var shortBlockers = [];
    if (state.position) shortBlockers.push("alreadyInPosition");
    if (state.cooldown > 0) shortBlockers.push("cooldownBlocked");
    if (!row.atr14 || row.atr14 <= 0) shortBlockers.push("atrMissing");
    if (row.btcRegime === "neutral") shortBlockers.push("neutralRegime");
    if (row.btcRegime !== "bearish") shortBlockers.push("regimeNotBearish");
    if (!(row.close < trendEma(row, params))) shortBlockers.push("emaTrendFailed");
    if (!breaksBelowDonchian(row, previous, params.donchianEntry)) shortBlockers.push("donchianBreakoutFailed");
    if (!(row.adx14 > params.adxThreshold)) shortBlockers.push("adxTooLow");
    if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) shortBlockers.push("volumeTooLow");
    if (!validPositionSize(row, params)) shortBlockers.push("stopTooClose");
    if (shortBlockers.length === 0) return { passed: true, side: "short", blockers: [] };
  }

  return { passed: false, side: "long", blockers: blockers };
}

function baseLongBlockers(row, state, params) {
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (row.btcRegime === "neutral") blockers.push("neutralRegime");
  if (row.btcRegime !== "bullish") blockers.push("regimeNotBullish");
  if (!(row.close > trendEma(row, params))) blockers.push("emaTrendFailed");
  if (!(row.adx14 > params.adxThreshold)) blockers.push("adxTooLow");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");
  return blockers;
}

function baseV2Blockers(row, state, params) {
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (!trendPasses(row, params)) blockers.push("emaTrendFailed");
  if (params.useAdx && !(row.adx14 > params.adxThreshold)) blockers.push("adxTooLow");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");
  return blockers;
}

function appendIndicatorFilterBlockers(frame, index, blockers, params) {
  var row = frame[index];
  var preset = String(params.indicatorFilterPreset || "none");
  var usesTrendQuality = preset === "trendQuality" || preset === "pullbackTiming" || preset === "breakoutQuality";
  var usesPullbackTiming = preset === "pullbackTiming";
  var usesBreakoutQuality = preset === "breakoutQuality";
  var useChoppinessFilter = params.useChoppinessFilter || usesTrendQuality;
  var useVolumeFlowFilter = params.useVolumeFlowFilter || preset === "trendQuality" || usesBreakoutQuality;
  var useAtrPercentileFilter = params.useAtrPercentileFilter || usesTrendQuality;
  var useStochRsiFilter = params.useStochRsiFilter || usesPullbackTiming;
  var useKeltnerSqueezeFilter = params.useKeltnerSqueezeFilter || usesBreakoutQuality;
  if (useChoppinessFilter && !(row.choppiness14 !== null && row.choppiness14 !== undefined && row.choppiness14 <= Number(params.maxChoppiness || 61.8))) {
    blockers.push("marketTooChoppy");
  }
  if (useVolumeFlowFilter) {
    var cmfPass = row.cmf20 !== null && row.cmf20 !== undefined && Number(row.cmf20) >= Number(params.minCmf || 0);
    var obvPass = params.requireObvAboveMa === false || (row.obvMa20 !== null && row.obvMa20 !== undefined && Number(row.obv) >= Number(row.obvMa20));
    if (!(cmfPass && obvPass)) blockers.push("volumeFlowWeak");
  }
  if (useAtrPercentileFilter) {
    var rank = Number(row.atrPctRank100);
    if (!(Number.isFinite(rank) && rank >= Number(params.minAtrPctRank || 0) && rank <= Number(params.maxAtrPctRank || 100))) blockers.push("volatilityRegimeBlocked");
  }
  if (useStochRsiFilter) {
    var k = Number(row.stochRsiK);
    if (!(Number.isFinite(k) && k >= Number(params.stochRsiMin || 0) && k <= Number(params.stochRsiMax || 100))) blockers.push("stochRsiBlocked");
  }
  if (useKeltnerSqueezeFilter && !recentKeltnerSqueeze(frame, index, params.keltnerSqueezeLookback || params.squeezeLookback || 20, params.maxSqueezeRatio || 1)) {
    blockers.push("keltnerSqueezeBlocked");
  }
  return blockers;
}

function recentKeltnerSqueeze(frame, index, lookback, maxRatio) {
  var from = Math.max(0, index - Number(lookback || 20));
  for (var i = from; i <= index; i += 1) {
    var ratio = Number(frame[i] && frame[i].squeezeRatio);
    if (Number.isFinite(ratio) && ratio <= Number(maxRatio || 1)) return true;
  }
  return false;
}

function trendPasses(row, params) {
  var mode = params.regimeMode || "strictBtcBull";
  if (mode === "strictBtcBull") return row.btcRegime === "bullish";
  if (mode === "looseBtcBull") return row.btcClose4h && row.btcEma200_4h && row.btcClose4h > row.btcEma200_4h;
  if (mode === "symbolTrend") return row.close > row.ema200;
  if (mode === "symbolFastTrend") return row.ema50 > row.ema200;
  if (mode === "noRegime") return true;
  return row.btcRegime === "bullish";
}

function donchian20Entry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseLongBlockers(row, state, params);
  if (!breaksAboveDonchian(row, previous, 20)) blockers.push("donchianBreakoutFailed");
  return blockers.length === 0
    ? { passed: true, side: "long", blockers: [] }
    : { passed: false, side: "long", blockers: blockers };
}

function closeConfirmEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var beforePrevious = index > 1 ? frame[index - 2] : previous;
  var blockers = baseLongBlockers(row, state, params);
  var level20 = beforePrevious.donchianHigh20;
  var level55 = beforePrevious.donchianHigh55;
  var broke20 = level20 !== null && level20 !== undefined && previous.close > level20;
  var broke55 = level55 !== null && level55 !== undefined && previous.close > level55;
  var confirmed20 = broke20 && row.close > level20;
  var confirmed55 = broke55 && row.close > level55;
  if (!(confirmed20 || confirmed55)) blockers.push("donchianBreakoutFailed");
  return blockers.length === 0
    ? { passed: true, side: "long", blockers: [] }
    : { passed: false, side: "long", blockers: blockers };
}

function pullbackTrendEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseLongBlockers(row, state, params);
  var reclaimed = previous.rsi14 < params.rsiPullbackLevel && row.rsi14 > params.rsiReclaimLevel;
  if (!reclaimed) blockers.push("pullbackReclaimFailed");
  return blockers.length === 0
    ? { passed: true, side: "long", blockers: [] }
    : { passed: false, side: "long", blockers: blockers };
}

function emaPullbackContinuationEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseLongBlockers(row, state, params);
  if (!(row.ema50 > row.ema200)) blockers.push("emaTrendFailed");
  var reclaimed = previous.rsi14 < params.rsiPullbackLevel && row.rsi14 > params.rsiReclaimLevel;
  if (!reclaimed) blockers.push("pullbackReclaimFailed");
  return signalFromBlockers(blockers);
}

function trendBreakoutRetestEntry(frame, index, state, params) {
  var row = frame[index];
  var blockers = baseLongBlockers(row, state, params);
  var breakout = findRecentBreakout(frame, index, 20, params.retestLookback || 8);
  if (!breakout) blockers.push("donchianBreakoutFailed");
  if (breakout) {
    var near = Math.abs(row.low - breakout.level) <= row.atr14 * params.retestAtr;
    var reclaim = row.close > breakout.level;
    if (!(near && reclaim)) blockers.push("retestFailed");
  }
  return signalFromBlockers(blockers);
}

function volatilitySqueezeBreakoutEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseLongBlockers(row, state, params);
  appendIndicatorFilterBlockers(frame, index, blockers, params);
  var squeeze = isSqueezed(frame, index, params.squeezeLookback, params.squeezePercentile);
  if (!squeeze) blockers.push("squeezeFailed");
  if (!breaksAboveDonchian(row, previous, params.rangeLookback || 20)) blockers.push("rangeBreakoutFailed");
  if (!(row.adx14 > previous.adx14 && row.adx14 > params.adxThreshold)) blockers.push("adxNotRising");
  return signalFromBlockers(blockers);
}

function meanReversionBullEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseLongBlockers(row, state, params);
  var oversold = previous.rsi14 < params.rsiOversold;
  var reclaimed = row.close > row.ema20 || row.rsi14 > previous.rsi14;
  if (!(oversold && reclaimed)) blockers.push("meanReversionFailed");
  return signalFromBlockers(blockers);
}

function momentumContinuationEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseLongBlockers(row, state, params);
  if (!(row.close > row.ema50 && row.ema50 > row.ema200)) blockers.push("emaTrendFailed");
  if (!(row.rsi14 >= params.rsiMin && row.rsi14 <= params.rsiMax)) blockers.push("pullbackReclaimFailed");
  if (!(row.adx14 > previous.adx14 && row.adx14 > params.adxThreshold)) blockers.push("adxNotRising");
  var body = Math.abs(row.close - row.open);
  var range = Math.max(row.high - row.low, 1e-9);
  if (!(row.close > row.open && body / range >= params.minBodyPct)) blockers.push("momentumStrengthFailed");
  return signalFromBlockers(blockers);
}

function pullbackReclaimV2Entry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseV2Blockers(row, state, params);
  appendIndicatorFilterBlockers(frame, index, blockers, params);
  if (!(previous.rsi14 < params.rsiPullbackLevel && row.rsi14 > params.rsiReclaimLevel)) blockers.push("pullbackReclaimFailed");
  return signalFromBlockers(blockers);
}

function emaBounceV2Entry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseV2Blockers(row, state, params);
  appendIndicatorFilterBlockers(frame, index, blockers, params);
  var touched = row.low <= row.ema50 + row.atr14 * params.emaBounceAtr || row.low <= row.ema100 + row.atr14 * params.emaBounceAtr;
  var reclaim = previous.close < previous.ema20 && row.close > row.ema20;
  if (!(touched && reclaim)) blockers.push("pullbackReclaimFailed");
  return signalFromBlockers(blockers);
}

function breakoutRetestV2Entry(frame, index, state, params) {
  var blockers = baseV2Blockers(frame[index], state, params);
  appendIndicatorFilterBlockers(frame, index, blockers, params);
  var breakout = findRecentBreakout(frame, index, 20, params.retestLookback);
  if (!breakout) blockers.push("donchianBreakoutFailed");
  if (breakout) {
    var row = frame[index];
    if (!(Math.abs(row.low - breakout.level) <= row.atr14 * params.retestAtr && row.close > breakout.level)) blockers.push("retestFailed");
  }
  return signalFromBlockers(blockers);
}

function rangeExpansionV2Entry(frame, index, state, params) {
  var row = frame[index];
  var blockers = baseV2Blockers(row, state, params);
  appendIndicatorFilterBlockers(frame, index, blockers, params);
  if (!isSqueezed(frame, index, params.squeezeLookback, params.squeezePercentile)) blockers.push("squeezeFailed");
  var range = row.high - row.low;
  var avgRange = averageRange(frame, index, params.rangeSma);
  var closesHigh = range > 0 && (row.close - row.low) / range >= params.closeHighPct;
  var previous = index > 0 ? frame[index - 1] : row;
  if (!(range > avgRange && closesHigh && breaksAboveDonchian(row, previous, params.rangeLookback))) blockers.push("rangeBreakoutFailed");
  if (!(row.adx14 > previous.adx14)) blockers.push("adxNotRising");
  return signalFromBlockers(blockers);
}

function relativeStrengthV2Entry(frame, index, state, params) {
  var row = frame[index];
  var blockers = baseV2Blockers(row, state, params);
  appendIndicatorFilterBlockers(frame, index, blockers, params);
  var lookback = params.rsLookback;
  var benchmarkClose = row.rsBenchmarkClose4h || row.btcClose4h;
  var previousBenchmarkClose = index >= lookback ? (frame[index - lookback].rsBenchmarkClose4h || frame[index - lookback].btcClose4h) : null;
  if (index < lookback || !previousBenchmarkClose || !benchmarkClose) {
    blockers.push("relativeStrengthFailed");
  } else {
    var symbolReturn = (row.close - frame[index - lookback].close) / frame[index - lookback].close;
    var benchmarkReturn = (benchmarkClose - previousBenchmarkClose) / previousBenchmarkClose;
    if (!(symbolReturn > benchmarkReturn + params.rsThreshold && row.rsi14 >= params.rsiMin && row.rsi14 <= params.rsiMax && row.close > row.ema20)) blockers.push("relativeStrengthFailed");
  }
  return signalFromBlockers(blockers);
}

function simpleAtrTrendV2Entry(frame, index, state, params) {
  var row = frame[index];
  var blockers = baseV2Blockers(row, state, params);
  appendIndicatorFilterBlockers(frame, index, blockers, params);
  if (!(row.ema20 > row.ema50 && row.close > row.ema50)) blockers.push("emaTrendFailed");
  if (params.useRsiFilter && !(row.rsi14 >= params.rsiMin && row.rsi14 <= params.rsiMax)) blockers.push("pullbackReclaimFailed");
  return signalFromBlockers(blockers);
}

function relativeStrengthFreshV3Entry(frame, index, state, params) {
  var entry = relativeStrengthV2Entry(frame, index, state, params);
  if (!entry.passed) return entry;
  var flat = Object.assign({}, state, {position: null, cooldown: 0});
  var first = index;
  while (first > 0 && relativeStrengthV2Entry(frame, first - 1, flat, params).passed) first--;
  var row = frame[index], drift = (row.close - frame[first].close) / row.atr14;
  var costs = readinessCosts(params), stopPct = row.atr14 * params.atrMultiplier / row.close * 100;
  var blockers = [];
  if (index - first > Number(params.freshnessMaxBars ?? 0)) blockers.push('setupExpired');
  if (drift > Number(params.freshnessMaxDriftAtr ?? 0.5)) blockers.push('entryOverextended');
  if (!(stopPct > costs * Number(params.readinessCostMultiple ?? 3))) blockers.push('costsTooHigh');
  return {passed: blockers.length === 0, side: 'long', blockers: blockers};
}

function readinessCosts(params) {
  var slippage = params.slippageBps === null || params.slippageBps === undefined ? Number(params.slippagePct || 0) : Number(params.slippageBps) / 100;
  return 2 * (Number(params.takerFeePct || 0) + slippage);
}

function closedExecutionBars(candles, start, end, step) {
  // Normalized execution history is sorted. Avoid scanning it for every signal bar.
  function bound(time) {
    var low = 0, high = candles.length;
    while (low < high) {var mid = (low + high) >>> 1;if (candles[mid].time < time) low = mid + 1;else high = mid;}
    return low;
  }
  return candles.slice(bound(start), bound(end - step + 1));
}

// Display-only diagnostic; the original entry, execution and paper state are unchanged.
function entryReadiness(frame, index, state, params, execution, entry, setup, trigger) {
  var row = frame[index], interval = executionIntervalSeconds(execution.signalInterval || params.signalTimeframe || execution.interval);
  var end = row.time + interval;
  var lower = closedExecutionBars(execution.candles, row.time, end, executionIntervalSeconds(execution.interval));
  var covered = execution.active && lower.length === Math.round(interval / executionIntervalSeconds(execution.interval));
  for (var j = 1; j < lower.length; j++) if (lower[j].time - lower[j-1].time !== executionIntervalSeconds(execution.interval)) covered = false;
  var costs = readinessCosts(params), stopPct = row.atr14 * params.atrMultiplier / row.close * 100;
  var age = trigger ? index - trigger.index : null;
  var drift = trigger && row.atr14 > 0 ? Math.abs(row.close - trigger.price) / row.atr14 : null;
  var blockers = [...new Set(entry.blockers || [])];
  if (!dailyLossAllowsEntry(state, row, params)) blockers.push('dailyLossLimitReached');
  if (index < 199 || !Number.isFinite(row.atr14) || !(row.atr14 > 0) || !(row.ema200 > 0)) blockers.push('warmupMissing');
  if (!covered) blockers.push('executionCoverageMissing');
  if (!(costs > 0 && stopPct > costs * Number(params.readinessCostMultiple ?? 3))) blockers.push('costsUnavailableOrTooHigh');
  if (trigger && (age > Number(params.freshnessMaxBars ?? 0) || drift > Number(params.freshnessMaxDriftAtr ?? 0.5))) blockers.push('setupExpired');
  var status = 'WAIT';
  if (blockers.includes('warmupMissing') || blockers.includes('executionCoverageMissing')) status = 'UNAVAILABLE';
  else if (state.pendingExit) status = 'EXIT';
  else if (state.position || state.pendingEntry) status = 'HOLD';
  else if (blockers.includes('setupExpired')) status = 'LATE';
  else if (setup.passed && !blockers.length) status = String(entry.side || 'long').toUpperCase();
  var score = status === 'LONG' || status === 'SHORT' ? Math.round(100 - Math.min(10, costs / stopPct * 20 + (drift || 0) * 5)) : null;
  return {time: row.time, closedAt: end, status: status, score: score, side: entry.side || 'long',
    eligible: entry.passed, setupPassed: setup.passed, blockers: blockers, setupAgeBars: age,
    triggerTime: trigger && trigger.time, triggerPrice: trigger && trigger.price, driftAtr: drift,
    stopDistancePct: Number.isFinite(stopPct) ? stopPct : null, roundTripCostPct: costs,
    executionTimeframe: execution.interval, executionCovered: covered,
    checks: {close:row.close,ema20:row.ema20,ema50:row.ema50,ema200:row.ema200,rsi:row.rsi14,
      rsiMin:params.rsiMin,rsiMax:params.rsiMax,adx:row.adx14,benchmarkClosedTime:row.rsBenchmarkTime,
      mandatoryTacticRulesPassed:entry.passed,costToStopRatio:stopPct>0?costs/stopPct:null},
    positionContext: state.position || state.pendingEntry ? 'hypothetical tactic position; not your account' : 'flat simulation'};
}

function fibPullbackContinuationEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseV2Blockers(row, state, params);
  if (params.regimeMode === "noRegime") blockers.push("regimeNotBullish");
  if (!(row.marketStructureTrend === "up" || row.structureBreakUp || row.higherLow)) blockers.push("structureBreakFailed");
  var hasPocket = row.goldenPocketLow !== null && row.goldenPocketLow !== undefined && row.goldenPocketHigh !== null && row.goldenPocketHigh !== undefined;
  var inPocket = hasPocket && row.low <= row.goldenPocketHigh + row.atr14 * 0.15 && row.close >= row.goldenPocketLow;
  var nearPocket = hasPocket && row.nearGoldenPocket === true;
  var reclaim = row.close > row.open && row.close > row.ema20 && row.rsi14 >= params.rsiReclaimLevel && previous.rsi14 <= params.rsiPullbackLevel;
  if (!((inPocket || nearPocket) && reclaim)) blockers.push("fibPullbackFailed");
  if (params.requireAnchoredVwap && !(row.anchoredVwapFromSwingLow && row.close >= row.anchoredVwapFromSwingLow)) blockers.push("vwapConfirmationFailed");
  return signalFromBlockers(blockers);
}

function mtfPullback15mEntry(frame, index, state, params) {
  var direction = String(params.mtfDirection || "long");
  if (direction === "short") return mtfPullback15mSideEntry(frame, index, state, params, "short");
  if (direction === "both") {
    var longEntry = mtfPullback15mSideEntry(frame, index, state, params, "long");
    if (longEntry.passed) return longEntry;
    var shortEntry = mtfPullback15mSideEntry(frame, index, state, params, "short");
    if (shortEntry.passed) return shortEntry;
    return {
      passed: false,
      side: "long",
      blockers: dedupeBlockers((longEntry.blockers || []).concat(shortEntry.blockers || []))
    };
  }
  return mtfPullback15mSideEntry(frame, index, state, params, "long");
}

function mtfPullback15mSideEntry(frame, index, state, params, side) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseMtfBlockers(row, state, params, side);
  appendMtfIndicatorFilterBlockers(frame, index, blockers, params, side);
  if (!mtfTrendPasses(row, params, side)) blockers.push("mtfTrendBlocked");

  var mode = String(params.mtfEntryMode || "pullback");
  var touchBuffer = row.atr14 * Number(params.emaBounceAtr || 0.8);
  var touchedEma = side === "long"
    ? row.low <= row.ema20 + touchBuffer || row.low <= row.ema50 + touchBuffer
    : row.high >= row.ema20 - touchBuffer || row.high >= row.ema50 - touchBuffer;
  var longPullbackLevel = Number(params.rsiPullbackLevel || 42);
  var longReclaimLevel = Number(params.rsiReclaimLevel || 50);
  var shortPullbackLevel = 100 - longPullbackLevel;
  var shortReclaimLevel = 100 - longReclaimLevel;
  var rsiReclaim = side === "long"
    ? previous.rsi14 <= longPullbackLevel && row.rsi14 >= longReclaimLevel
    : previous.rsi14 >= shortPullbackLevel && row.rsi14 <= shortReclaimLevel;
  var emaReclaim = side === "long"
    ? previous.close < previous.ema20 && row.close > row.ema20
    : previous.close > previous.ema20 && row.close < row.ema20;
  var breakout = side === "long"
    ? breaksAboveRecentHigh(frame, index, Number(params.donchianEntry || 20)) && row.close > row.open && row.rsi14 >= longReclaimLevel
    : breaksBelowRecentLow(frame, index, Number(params.donchianEntry || 20)) && row.close < row.open && row.rsi14 <= shortReclaimLevel;
  var pullback = touchedEma && (rsiReclaim || emaReclaim);
  if (mode === "pullback" && !pullback) blockers.push("mtfTimingBlocked");
  if (mode === "breakout" && !breakout) blockers.push("mtfTimingBlocked");
  if (mode === "either" && !(pullback || breakout)) blockers.push("mtfTimingBlocked");
  return blockers.length === 0
    ? { passed: true, side: side, blockers: [] }
    : { passed: false, side: side, blockers: blockers };
}

function baseMtfBlockers(row, state, params, side) {
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (side === "long" && !(row.ema20 > row.ema50 && row.close > row.ema50)) blockers.push("emaTrendFailed");
  if (side === "short" && !(row.ema20 < row.ema50 && row.close < row.ema50)) blockers.push("emaTrendFailed");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");
  return blockers;
}

function appendMtfIndicatorFilterBlockers(frame, index, blockers, params, side) {
  if (side === "long") return appendIndicatorFilterBlockers(frame, index, blockers, params);
  var originalPreset = params.indicatorFilterPreset;
  if (originalPreset === "pullbackTiming") {
    var row = frame[index];
    var k = Number(row.stochRsiK);
    if (!(Number.isFinite(k) && k >= 100 - Number(params.stochRsiMax || 85) && k <= 100 - Number(params.stochRsiMin || 20))) blockers.push("stochRsiBlocked");
    return blockers;
  }
  if (originalPreset === "trendQuality") {
    var shortRow = frame[index];
    if (!(shortRow.choppiness14 !== null && shortRow.choppiness14 !== undefined && shortRow.choppiness14 <= Number(params.maxChoppiness || 61.8))) blockers.push("marketTooChoppy");
    var rank = Number(shortRow.atrPctRank100);
    if (!(Number.isFinite(rank) && rank >= Number(params.minAtrPctRank || 0) && rank <= Number(params.maxAtrPctRank || 100))) blockers.push("volatilityRegimeBlocked");
  }
  return blockers;
}

function mtfTrendPasses(row, params, side) {
  var minRsi = Number(params.mtfMinRsi || 45);
  if (params.mtfRequire1h !== false) {
    var ema1h = row["mtf1hEma" + Number(params.mtf1hTrendEma || 50)];
    if (side === "long" && !(row.mtf1hClose && ema1h && row.mtf1hClose > ema1h && row.mtf1hEma20 > row.mtf1hEma50 && row.mtf1hRsi14 >= minRsi)) return false;
    if (side === "short" && !(row.mtf1hClose && ema1h && row.mtf1hClose < ema1h && row.mtf1hEma20 < row.mtf1hEma50 && row.mtf1hRsi14 <= 100 - minRsi)) return false;
  }
  if (params.mtfRequire4h !== false) {
    var ema4h = row["mtf4hEma" + Number(params.mtf4hTrendEma || 100)];
    if (side === "long" && !(row.mtf4hClose && ema4h && row.mtf4hClose > ema4h && row.mtf4hEma20 > row.mtf4hEma50 && row.mtf4hRsi14 >= minRsi)) return false;
    if (side === "short" && !(row.mtf4hClose && ema4h && row.mtf4hClose < ema4h && row.mtf4hEma20 < row.mtf4hEma50 && row.mtf4hRsi14 <= 100 - minRsi)) return false;
  }
  return true;
}

function dedupeBlockers(blockers) {
  var seen = {};
  return blockers.filter(function (blocker) {
    if (seen[blocker]) return false;
    seen[blocker] = true;
    return true;
  });
}

function vwapReversion15mEntry(frame, index, state, params) {
  var direction = String(params.reversionDirection || "both");
  if (direction === "long") return vwapReversion15mSideEntry(frame, index, state, params, "long");
  if (direction === "short") return vwapReversion15mSideEntry(frame, index, state, params, "short");
  var longEntry = vwapReversion15mSideEntry(frame, index, state, params, "long");
  if (longEntry.passed) return longEntry;
  var shortEntry = vwapReversion15mSideEntry(frame, index, state, params, "short");
  if (shortEntry.passed) return shortEntry;
  return {
    passed: false,
    side: "long",
    blockers: dedupeBlockers((longEntry.blockers || []).concat(shortEntry.blockers || []))
  };
}

function vwapReversion15mSideEntry(frame, index, state, params, side) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (!row.vwap || !row.bbUpper || !row.bbLower || !row.ema20) blockers.push("vwapReversionFailed");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");

  var maxAdx = Number(params.maxAdx || 28);
  var minChoppiness = Number(params.minChoppiness || 0);
  if (!(row.adx14 <= maxAdx && (!minChoppiness || row.choppiness14 >= minChoppiness))) blockers.push("rangeRegimeBlocked");

  var distanceAtr = Number(params.minVwapDistanceAtr || 0.8);
  var longExtended = row.vwap && row.close <= row.vwap - row.atr14 * distanceAtr;
  var shortExtended = row.vwap && row.close >= row.vwap + row.atr14 * distanceAtr;
  if (params.requireBbTouch !== false) {
    longExtended = longExtended && row.low <= row.bbLower;
    shortExtended = shortExtended && row.high >= row.bbUpper;
  }

  var longTiming = row.rsi14 <= Number(params.rsiOversold || 32) ||
    row.stochRsiK <= Number(params.stochRsiOversold || 20) ||
    (previous.rsi14 < row.rsi14 && row.close > row.open);
  var shortTiming = row.rsi14 >= Number(params.rsiOverbought || 68) ||
    row.stochRsiK >= Number(params.stochRsiOverbought || 80) ||
    (previous.rsi14 > row.rsi14 && row.close < row.open);
  if (params.requireCmfConfirm) {
    if (side === "long" && !(row.cmf20 !== null && row.cmf20 >= Number(params.minCmf || -0.05))) longTiming = false;
    if (side === "short" && !(row.cmf20 !== null && row.cmf20 <= -Number(params.minCmf || -0.05))) shortTiming = false;
  }

  if (side === "long" && !(longExtended && longTiming)) blockers.push("vwapReversionFailed");
  if (side === "short" && !(shortExtended && shortTiming)) blockers.push("vwapReversionFailed");
  return blockers.length === 0
    ? { passed: true, side: side, blockers: [] }
    : { passed: false, side: side, blockers: blockers };
}

function robustTrendFollowerV1Entry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (!(row.btcClose4h && row.btcEma200_4h && row.btcClose4h > row.btcEma200_4h)) blockers.push("regimeNotBullish");
  if (!(row.close > row.ema100)) blockers.push("emaTrendFailed");
  if (!breaksAboveDonchian(row, previous, params.donchianEntry || 55)) blockers.push("donchianBreakoutFailed");
  if (!(row.adx14 > params.adxThreshold)) blockers.push("adxTooLow");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20 * Number(params.volumeMultiplier || 1))) blockers.push("volumeTooLow");
  if (params.useFundingFilter && !(row.fundingRate !== null && row.fundingRate !== undefined && Number(row.fundingRate) <= Number(params.maxFundingRate))) blockers.push("fundingTooHigh");
  if (params.useOpenInterestFilter && !(row.openInterestChangePct !== null && row.openInterestChangePct !== undefined && Number(row.openInterestChangePct) >= Number(params.minOpenInterestChangePct || 0))) blockers.push("openInterestNotRising");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");
  return signalFromBlockers(blockers);
}

function btcMeanReversion4hEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (!row.bbLower || !row.bbMiddle || !row.vwap || !row.ema20) blockers.push("meanReversionFailed");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (!trendPasses(row, params)) blockers.push("regimeNotBullish");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");

  var maxAdx = Number(params.maxAdx || 45);
  var minChoppiness = Number(params.minChoppiness || 0);
  if (!(row.adx14 <= maxAdx && (!minChoppiness || row.choppiness14 >= minChoppiness))) blockers.push("rangeRegimeBlocked");

  var rsiOversold = Number(params.rsiOversold || 35);
  var rsiReclaim = Number(params.rsiReclaim || 42);
  var lookback = Math.max(1, Number(params.pullbackLookback || 3));
  var recentOversold = false;
  var recentBandTouch = false;
  var recentStretch = false;
  for (var j = Math.max(0, index - lookback); j <= index; j += 1) {
    var item = frame[j];
    if (!item) continue;
    recentOversold = recentOversold || item.rsi14 <= rsiOversold;
    recentBandTouch = recentBandTouch || item.low <= item.bbLower || item.close < item.bbLower;
    recentStretch = recentStretch || item.close <= item.ema20 || item.close <= item.vwap || recentBandTouch;
  }
  var improvingMomentum = row.rsi14 >= rsiReclaim || (previous.rsi14 <= rsiOversold && row.rsi14 > previous.rsi14);
  var reclaimed = row.close > row.open || row.close > row.bbLower || row.close > previous.high || row.close > row.ema20;
  if (params.requireBbTouch !== false && !recentBandTouch) blockers.push("meanReversionFailed");
  if (!(recentOversold && recentStretch && improvingMomentum && reclaimed)) blockers.push("meanReversionFailed");
  if (params.requireCmfConfirm && !(row.cmf20 !== null && row.cmf20 !== undefined && row.cmf20 >= Number(params.minCmf || -0.05))) blockers.push("volumeFlowWeak");
  return signalFromBlockers(blockers);
}

function squeezePullbackHold4hEntry(frame, index, state, params) {
  var row = frame[index];
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");

  var ema = Number(params.pullbackEma || 50) >= 100 ? row.ema100 : Number(params.pullbackEma || 50) <= 20 ? row.ema20 : row.ema50;
  var squeezePass = params.squeezeMode === "bbWidthLow"
    ? isSqueezed(frame, index, Number(params.squeezeLookback || 80), Number(params.squeezePercentile || 0.25))
    : row.squeezeOn === true;
  var pullbackReclaim = ema && row.low <= ema && row.close > ema;
  if (!squeezePass) blockers.push("squeezeFailed");
  if (!pullbackReclaim) blockers.push("pullbackReclaimFailed");
  if (params.requireTrend && !(row.close > row.ema200 && row.ema50 > row.ema200)) blockers.push("emaTrendFailed");
  if (params.requireMomentum && !(row.rsi14 >= Number(params.minRsi || 55))) blockers.push("momentumStrengthFailed");
  return signalFromBlockers(blockers);
}

function btcRegimeController4hEntry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = [];
  if (state.position) blockers.push("alreadyInPosition");
  if (state.cooldown > 0) blockers.push("cooldownBlocked");
  if (state.position && params.maxOpenTrades <= 1) blockers.push("maxOpenTradesReached");
  if (!row.atr14 || row.atr14 <= 0) blockers.push("atrMissing");
  if (row.atr14 && row.atr14 * params.atrMultiplier <= 0) blockers.push("stopTooClose");
  if (params.volumeFilter && !(row.volumeMa20 && row.volume > row.volumeMa20)) blockers.push("volumeTooLow");
  if (!validPositionSize(row, params)) blockers.push("stopTooClose");
  if (blockers.length) return { passed: false, side: "long", blockers: blockers };

  var btcBull = row.btcClose4h && row.btcEma200_4h && row.btcClose4h > row.btcEma200_4h;
  var symbolBull = row.close > row.ema200 && row.ema50 > row.ema200;
  var fastBull = row.close > row.ema50 && row.ema20 > row.ema50;
  var bear = row.close < row.ema200 && row.ema50 < row.ema200 && !btcBull;
  var trendAdx = Number(row.adx14 || 0);
  var mode = String(params.controllerMode || "adaptive");
  if (bear && mode !== "forceMeanReversion") return { passed: false, side: "long", blockers: ["bearNoTrade"] };

  var allowTrend = mode === "adaptive" || mode === "trendOnly";
  var allowPullback = mode === "adaptive" || mode === "pullbackOnly";
  var allowMeanReversion = mode === "adaptive" || mode === "meanReversionOnly" || mode === "forceMeanReversion";

  if (allowTrend && (btcBull || symbolBull) && trendAdx >= Number(params.minTrendAdx || 12)) {
    var trendBreakout = breaksAboveDonchian(row, previous, Number(params.donchianEntry || 20));
    var momentumOk = row.rsi14 >= Number(params.trendRsiMin || 50) && row.close > row.open;
    if (trendBreakout && momentumOk) return { passed: true, side: "long", blockers: [], controllerBranch: "trendBreakout" };
  }

  if (allowPullback && (btcBull || symbolBull || fastBull)) {
    var reclaim = previous.rsi14 <= Number(params.rsiPullbackLevel || 42) && row.rsi14 >= Number(params.rsiReclaimLevel || 50);
    var emaReclaim = (row.low <= row.ema50 || row.low <= row.ema20) && row.close > row.ema20;
    if (reclaim && emaReclaim) return { passed: true, side: "long", blockers: [], controllerBranch: "bullPullback" };
  }

  if (allowMeanReversion && !bear) {
    var recentOversold = false;
    var recentBandTouch = false;
    var lookback = Number(params.meanReversionLookback || 5);
    for (var i = Math.max(0, index - lookback); i <= index; i += 1) {
      var item = frame[i];
      recentOversold = recentOversold || item.rsi14 <= Number(params.rsiOversold || 38);
      recentBandTouch = recentBandTouch || item.low <= item.bbLower || item.close < item.bbLower || item.low <= item.ema100;
    }
    var quietTrend = trendAdx <= Number(params.maxMeanReversionAdx || 32) || row.choppiness14 >= Number(params.minMeanReversionChoppiness || 45);
    var meanReclaim = row.rsi14 >= Number(params.rsiReclaim || 45) && row.close > row.open;
    if (quietTrend && recentOversold && recentBandTouch && meanReclaim) return { passed: true, side: "long", blockers: [], controllerBranch: "meanReversion" };
  }

  return { passed: false, side: "long", blockers: ["noControllerBranch"] };
}

function averageRange(frame, index, period) {
  if (index < period) return Infinity;
  var sum = 0;
  for (var i = index - period; i < index; i += 1) sum += frame[i].high - frame[i].low;
  return sum / period;
}

function signalFromBlockers(blockers) {
  return blockers.length === 0
    ? { passed: true, side: "long", blockers: [] }
    : { passed: false, side: "long", blockers: blockers };
}

function findRecentBreakout(frame, index, period, lookback) {
  var from = Math.max(1, index - lookback);
  for (var i = index - 1; i >= from; i -= 1) {
    var previous = frame[i - 1];
    var level = previous ? previous["donchianHigh" + period] : null;
    if (level !== null && level !== undefined && frame[i].close > level) return { index: i, level: level };
  }
  return null;
}

function breaksAboveRecentHigh(frame, index, period) {
  if (index <= 0) return false;
  var row = frame[index];
  var previous = frame[index - 1];
  if (previous && previous["donchianHigh" + period] !== null && previous["donchianHigh" + period] !== undefined) {
    return row.close > previous["donchianHigh" + period];
  }
  if (index < period) return false;
  var high = -Infinity;
  for (var i = index - period; i < index; i += 1) high = Math.max(high, frame[i].high);
  return Number.isFinite(high) && row.close > high;
}

function breaksBelowRecentLow(frame, index, period) {
  if (index <= 0) return false;
  var row = frame[index];
  var previous = frame[index - 1];
  if (previous && previous["donchianLow" + period] !== null && previous["donchianLow" + period] !== undefined) {
    return row.close < previous["donchianLow" + period];
  }
  if (index < period) return false;
  var low = Infinity;
  for (var i = index - period; i < index; i += 1) low = Math.min(low, frame[i].low);
  return Number.isFinite(low) && row.close < low;
}

function isSqueezed(frame, index, lookback, percentile) {
  if (index < 30) return false;
  var row = frame[index];
  if (!row.bbMiddle || !row.bbUpper || !row.bbLower) return false;
  var width = (row.bbUpper - row.bbLower) / row.bbMiddle;
  var start = Math.max(0, index - (lookback || 100));
  var widths = [];
  for (var i = start; i < index; i += 1) {
    var item = frame[i];
    if (item.bbMiddle && item.bbUpper && item.bbLower) widths.push((item.bbUpper - item.bbLower) / item.bbMiddle);
  }
  if (widths.length < 20) return false;
  widths.sort(function (a, b) { return a - b; });
  var threshold = widths[Math.floor(widths.length * (percentile || 0.25))];
  return width <= threshold;
}

function exitSignal(position, frame, index, params, execution) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var lowerTimeframeExitsActive = execution && execution.active;
  if (position.side === "long") {
    if (!lowerTimeframeExitsActive && row.low <= position.trailingStop) return { price: position.trailingStop, reason: "ATR trailing stop", kind: "stop" };
    var barsHeld = row.__index - position.entryIndex;
    var canRuleExit = barsHeld >= Number(params.minHoldBars || 0);
    if (!lowerTimeframeExitsActive && position.takeProfit && row.high >= position.takeProfit) return { price: position.takeProfit, reason: "ATR take profit", kind: "take-profit" };
    if ((params.strategyName === "SqueezePullbackHold4hV1" || params.strategyName === "BtcRegimeController4hV1") && barsHeld >= Number(params.maxHoldBars || 24)) return { price: row.close, reason: "Max hold exit", kind: "close-signal" };
    if (canRuleExit && (params.strategyName === "VwapReversion15mV1" || params.strategyName === "VwapReversion15mFastV1")) {
      var longTarget = row.vwap || row.ema20;
      if (longTarget && row.high >= longTarget) return { price: longTarget, reason: "VWAP reversion exit", kind: "close-signal" };
    }
    if (canRuleExit && params.strategyName === "BtcMeanReversion4hV1") {
      var meanTarget = row.bbMiddle || row.vwap || row.ema20;
      if (meanTarget && row.high >= meanTarget) return { price: meanTarget, reason: "BTC 4h mean reversion exit", kind: "close-signal" };
    }
    if (canRuleExit && ["RegimePullbackTrend", "EmaPullbackContinuation", "MeanReversionInBullRegime", "MomentumContinuation", "PullbackReclaimV2", "RangeExpansionV2", "RelativeStrengthV2", "RelativeStrengthFreshV3", "SimpleAtrTrendV2", "FibPullbackContinuationV1", "MtfPullback15mV1", "BtcRegimeController4hV1"].includes(params.strategyName) && row.close < row.ema50) return { price: row.close, reason: "EMA50 exit", kind: "close-signal" };
    if (params.strategyName === "EmaBounceV2" && row.close < row.ema100) return { price: row.close, reason: "EMA100 exit", kind: "close-signal" };
    if (crossesBelowDonchian(row, previous, params.donchianExit)) return { price: row.close, reason: "Donchian exit", kind: "close-signal" };
  } else {
    if (!lowerTimeframeExitsActive && row.high >= position.trailingStop) return { price: position.trailingStop, reason: "ATR trailing stop", kind: "stop" };
    var shortBarsHeld = row.__index - position.entryIndex;
    var canShortRuleExit = shortBarsHeld >= Number(params.minHoldBars || 0);
    if (!lowerTimeframeExitsActive && position.takeProfit && row.low <= position.takeProfit) return { price: position.takeProfit, reason: "ATR take profit", kind: "take-profit" };
    if (canShortRuleExit && (params.strategyName === "VwapReversion15mV1" || params.strategyName === "VwapReversion15mFastV1")) {
      var shortTarget = row.vwap || row.ema20;
      if (shortTarget && row.low <= shortTarget) return { price: shortTarget, reason: "VWAP reversion exit", kind: "close-signal" };
    }
    if (canShortRuleExit && params.strategyName === "MtfPullback15mV1" && row.close > row.ema50) return { price: row.close, reason: "EMA50 exit", kind: "close-signal" };
    if (crossesAboveDonchian(row, previous, params.donchianExit)) return { price: row.close, reason: "Donchian exit", kind: "close-signal" };
  }
  return null;
}

function shouldDeferEntry(params, index, frameLength) {
  return params.fillModel === "next-open" || params.fillModel === "conservative";
}

function shouldDeferExit(exit, params, index, frameLength) {
  return exit.kind === "close-signal" && (params.fillModel === "next-open" || params.fillModel === "conservative");
}

function attachMultiTimeframeContext(frame, params) {
  if (params.strategyName !== "MtfPullback15mV1") return frame;
  var oneHour = getCachedHigherTimeframeFrame(frame, 3600, params);
  var fourHour = getCachedHigherTimeframeFrame(frame, 14400, params);
  var oneIndex = -1;
  var fourIndex = -1;
  return frame.map(function (row) {
    while (oneIndex + 1 < oneHour.length && oneHour[oneIndex + 1].time + 3600 <= row.time) oneIndex += 1;
    while (fourIndex + 1 < fourHour.length && fourHour[fourIndex + 1].time + 14400 <= row.time) fourIndex += 1;
    var one = oneIndex >= 0 ? oneHour[oneIndex] : null;
    var four = fourIndex >= 0 ? fourHour[fourIndex] : null;
    return Object.assign({}, row, prefixHtf("mtf1h", one), prefixHtf("mtf4h", four));
  });
}

function getCachedHigherTimeframeFrame(frame, seconds, params) {
  var key = higherTimeframeCacheKey(frame, seconds);
  if (MTF_CONTEXT_CACHE.has(key)) return MTF_CONTEXT_CACHE.get(key);
  var htfFrame = buildClosedHigherTimeframeFrame(frame, seconds, params);
  MTF_CONTEXT_CACHE.set(key, htfFrame);
  if (MTF_CONTEXT_CACHE.size > MTF_CONTEXT_CACHE_LIMIT) {
    var oldest = MTF_CONTEXT_CACHE.keys().next().value;
    MTF_CONTEXT_CACHE.delete(oldest);
  }
  return htfFrame;
}

function higherTimeframeCacheKey(frame, seconds) {
  if (!frame.length) return seconds + "|empty";
  var first = frame[0];
  var last = frame[frame.length - 1];
  return [
    seconds,
    frame.length,
    first.time,
    first.open,
    first.close,
    last.time,
    last.open,
    last.close
  ].join("|");
}

function buildClosedHigherTimeframeFrame(candles, seconds, params) {
  var grouped = [];
  var active = null;
  candles.forEach(function (row) {
    var bucket = Math.floor(Number(row.time) / seconds) * seconds;
    if (!active || active.time !== bucket) {
      if (active) grouped.push(active);
      active = { time: bucket, open: row.open, high: row.high, low: row.low, close: row.close, volume: row.volume || 0 };
    } else {
      active.high = Math.max(active.high, row.high);
      active.low = Math.min(active.low, row.low);
      active.close = row.close;
      active.volume += Number(row.volume || 0);
    }
  });
  if (active) grouped.push(active);
  return indicators.buildIndicatorFrame(grouped, Object.assign({}, params, {
    emaReclaim: 20,
    emaTrendFast: 50,
    emaMid: 100,
    emaSlow: 200
  }));
}

function prefixHtf(prefix, row) {
  var out = {};
  ["Close", "Ema20", "Ema50", "Ema100", "Ema200", "Rsi14", "Adx14"].forEach(function (name) {
    out[prefix + name] = null;
  });
  if (!row) return out;
  out[prefix + "Close"] = row.close;
  out[prefix + "Ema20"] = row.ema20;
  out[prefix + "Ema50"] = row.ema50;
  out[prefix + "Ema100"] = row.ema100;
  out[prefix + "Ema200"] = row.ema200;
  out[prefix + "Rsi14"] = row.rsi14;
  out[prefix + "Adx14"] = row.adx14;
  return out;
}

function buildExecutionContext(options, signalFrame) {
  var signalInterval = options.interval || options.timeframe || "";
  var executionInterval = options.executionInterval || options.executionTimeframe || options.execution_timeframe || signalInterval;
  var executionCandles = data.normalizeCandles(options.executionCandles || []);
  var active = !!(executionInterval && executionInterval !== signalInterval && executionCandles.length);
  return {
    active: active,
    interval: executionInterval,
    signalInterval: signalInterval,
    candles: executionCandles.map(function (row, index) {
      return Object.assign({}, row, { __index: index });
    }),
    cursor: 0,
    diagnostics: {
      active: active,
      signalTimeframe: signalInterval,
      executionTimeframe: executionInterval,
      signalCandles: signalFrame.length,
      executionCandles: executionCandles.length,
      firstExecutionCandleTime: executionCandles.length ? executionCandles[0].time : null,
      lastExecutionCandleTime: executionCandles.length ? executionCandles[executionCandles.length - 1].time : null,
      fillsOnExecutionCandles: 0,
      exitsOnExecutionCandles: 0,
      softExitsOnExecutionCandles: 0
    }
  };
}

function executionFillRow(execution, signalTime) {
  if (!execution.active) return null;
  while (execution.cursor < execution.candles.length - 1 && execution.candles[execution.cursor].time < signalTime) {
    execution.cursor += 1;
  }
  var row = execution.candles[execution.cursor];
  if (!row || row.time < signalTime) return null;
  var maxLag = Math.max(900, executionIntervalSeconds(execution.interval) * 2);
  return row.time <= signalTime + maxLag ? row : null;
}

function executionIntervalSeconds(interval) {
  var text = String(interval || "").trim().toLowerCase();
  var value = Number(text.slice(0, -1));
  if (!Number.isFinite(value) || value <= 0) return 300;
  if (text.endsWith('s')) return value;
  if (text.endsWith("m")) return value * 60;
  if (text.endsWith("h")) return value * 3600;
  if (text.endsWith("d")) return value * 86400;
  return 300;
}

function closeOnExecutionCandle(state, frame, signalIndex, params, execution, signalTime) {
  var position = state.position;
  if (!position) return false;
  var startTime = position.lastExecutionCheckTime || position.entryFillTime || signalTime;
  var endTime = signalTime;
  if (endTime < startTime) return false;
  var i = position.executionCursor || 0;
  while (i < execution.candles.length && execution.candles[i].time <= startTime) i += 1;
  for (; i < execution.candles.length; i += 1) {
    var row = execution.candles[i];
    if (row.time > endTime) break;
    position.executionCursor = i;
    var exit = executionExitSignal(position, row, execution.interval);
    if (!exit) {
      var currentSignalTime = frame[signalIndex] ? Number(frame[signalIndex].time) : Number(signalTime);
      var referenceIndex = Number(row.time) >= currentSignalTime + executionIntervalSeconds(execution.signalInterval)
        ? signalIndex : Math.max(0, signalIndex - 1);
      exit = softExecutionExitSignal(position, row, frame[referenceIndex], params, execution.interval);
    }
    if (exit) {
      execution.diagnostics.exitsOnExecutionCandles += 1;
      if (exit.kind === "soft-execution-exit") execution.diagnostics.softExitsOnExecutionCandles += 1;
      closePositionAtFillRow(state, frame, row, exit.price, exit.reason, params, {
        signalIndex: signalIndex,
        signalTime: frame[signalIndex] ? frame[signalIndex].time : row.time,
        signalPrice: exit.price,
        kind: exit.kind,
        executionTimeframe: execution.interval
      });
      return true;
    }
    if (params.researchExitMode) {
      var reference = frame[referenceIndex];
      researchPartialProfits(state, position, row, params);
      if (params.researchExitMode === 'momentum') {
        var direction = position.side === 'long' ? 1 : -1;
        var failed = direction * (row.close - reference.ema20) < 0;
        position.researchMomentumFailures = failed ? Number(position.researchMomentumFailures || 0) + 1 : 0;
        if (position.researchMomentumFailures >= 2 && direction * (row.close-position.entryPrice) > position.initialStopDistance) {
          closePositionAtFillRow(state,frame,row,row.close,'Research momentum profit exit',params,{signalIndex:signalIndex,signalTime:row.time,signalPrice:row.close,kind:'soft-execution-exit'});
          execution.diagnostics.exitsOnExecutionCandles += 1;
          return true;
        }
      }
      researchUpdateTrail(position,row,reference,params);
    }
    position.lastExecutionCheckTime = row.time;
  }
  position.lastExecutionCheckTime = endTime;
  position.executionCursor = i;
  return false;
}

function executionExitSignal(position, row, executionInterval) {
  var checkLabel = executionInterval || position.executionTimeframe || "execution";
  if (position.side === "long") {
    if (row.low <= position.trailingStop) return { price: Math.min(row.open, position.trailingStop), reason: "ATR trailing stop (" + checkLabel + " check)", kind: "stop" };
    if (position.takeProfit && row.high >= position.takeProfit) return { price: Math.max(row.open, position.takeProfit), reason: "ATR take profit (" + checkLabel + " check)", kind: "take-profit" };
  } else {
    if (row.high >= position.trailingStop) return { price: Math.max(row.open, position.trailingStop), reason: "ATR trailing stop (" + checkLabel + " check)", kind: "stop" };
    if (position.takeProfit && row.low <= position.takeProfit) return { price: Math.min(row.open, position.takeProfit), reason: "ATR take profit (" + checkLabel + " check)", kind: "take-profit" };
  }
  return null;
}

function softExecutionExitSignal(position, row, referenceRow, params, executionInterval) {
  var mode = String(params.executionSoftExitMode || "none").trim();
  if (mode === "none" || !referenceRow) return null;
  var confirmations = Math.max(1, Number(params.executionSoftExitConfirmations || 2));
  var period = Math.max(2, Number(params.donchianExit || 20));
  var ema = Number(referenceRow.ema50);
  var donchian = Number(referenceRow[(position.side === "long" ? "donchianLow" : "donchianHigh") + period]);
  var close = Number(row.close);
  var emaBreach = Number.isFinite(ema) && (position.side === "long" ? close < ema : close > ema);
  var donchianBreach = Number.isFinite(donchian) && (position.side === "long" ? close < donchian : close > donchian);
  var breached = mode === "ema50-two-close"
    ? emaBreach
    : mode === "donchian-two-close"
      ? donchianBreach
      : mode === "ema-or-donchian-two-close" && (emaBreach || donchianBreach);
  position.softExecutionExitCount = breached ? Number(position.softExecutionExitCount || 0) + 1 : 0;
  if (!breached || position.softExecutionExitCount < confirmations) return null;
  var reason = emaBreach && donchianBreach ? "EMA50/Donchian" : (emaBreach ? "EMA50" : "Donchian");
  return {
    price: close,
    reason: reason + " soft exit (" + (executionInterval || "execution") + ", " + confirmations + " closes)",
    kind: "soft-execution-exit"
  };
}

function openPosition(state, frame, signalIndex, fillIndex, side, params, executionFill) {
  var signalRow = frame[signalIndex];
  var fillRow = executionFill || frame[fillIndex];
  var stopDistance = signalRow.atr14 * params.initialStopAtrMultiplier;
  var size = state.cash * params.riskPct / stopDistance;
  var baseEntry = entryBasePrice(signalRow, fillRow, side, params);
  var rawEntry = applySlippage(baseEntry, side, "entry", params);
  if (params.maxPositionPct > 0) size = Math.min(size, state.cash * params.maxPositionPct / 100 / rawEntry);
  if (params.maxNotional > 0) size = Math.min(size, params.maxNotional / rawEntry);
  var notional = rawEntry * size;
  if (!Number.isFinite(size) || size <= 0) return;
  var fee = notional * params.takerFeePct / 100;
  var slippagePaid = Math.abs(rawEntry - baseEntry) * size;
  state.cash -= fee;
  state.totalFees += fee;
  state.totalSlippage += slippagePaid;
  state.position = {
    side: side,
    entryIndex: fillIndex,
    entryFillIndex: fillRow.__index,
    entrySignalIndex: signalIndex,
    entrySignalTime: signalRow.time,
    entryFillTime: fillRow.time,
    entrySignalPrice: signalRow.close,
    entryBasePrice: baseEntry,
    entryFillPrice: rawEntry,
    entryTime: fillRow.time,
    entryPrice: rawEntry,
    size: size,
    notional: notional,
    stopDistance: stopDistance,
    initialStopDistance: stopDistance,
    initialStop: side === "long" ? rawEntry - stopDistance : rawEntry + stopDistance,
    trailingStop: side === "long" ? rawEntry - stopDistance : rawEntry + stopDistance,
    takeProfit: ["FibPullbackContinuationV1", "MtfPullback15mV1", "VwapReversion15mV1", "VwapReversion15mFastV1", "BtcMeanReversion4hV1", "SqueezePullbackHold4hV1", "BtcRegimeController4hV1"].includes(params.strategyName)
      ? (side === "long" ? rawEntry + signalRow.atr14 * Number(params.takeProfitAtr || 2.8) : rawEntry - signalRow.atr14 * Number(params.takeProfitAtr || 2.8))
      : null,
    bestClose: fillRow.close,
    trailingActive: params.trailingActivationR <= 0,
    breakEvenActive: false,
    strategyName: params.strategyName,
    executionTimeframe: fillRow === executionFill && fillRow ? (params.executionTimeframe || params.executionInterval || "execution") : null,
    lastExecutionCheckTime: executionFill ? fillRow.time - executionIntervalSeconds(params.executionTimeframe) : fillRow.time,
    softExecutionExitCount: 0,
    entryFee: fee,
    entrySlippage: slippagePaid
  };
  if (executionFill && state.diagnostics.execution) state.diagnostics.execution.fillsOnExecutionCandles += 1;
  if (params.researchExitMode) {
    state.position.originalSize = size;
    state.position.bestClose = rawEntry;
    state.position.researchRealized = {net:0,gross:0,fees:0,slippage:0,weightedExit:0,fills:[]};
  }
}

function researchPartialProfits(state, position, row, params) {
  if (params.researchExitMode !== 'staged') return;
  var sign = position.side === 'long' ? 1 : -1;
  for (var level of [1,2]) {
    var key = 'researchTarget' + level;
    var target = position.entryPrice + sign * position.initialStopDistance * level;
    if (position[key] || (sign === 1 ? row.high < target : row.low > target)) continue;
    var size = Math.min(position.size,position.originalSize*.25);
    var ratio = size/position.size;
    var base = sign === 1 ? Math.max(row.open,target) : Math.min(row.open,target);
    var fill = applySlippage(base,position.side,'exit',params);
    var entryFee = position.entryFee * ratio, entrySlippage = position.entrySlippage * ratio;
    var exitFee = fill*size*params.takerFeePct/100, exitSlippage = Math.abs(fill-base)*size;
    var gross = sign*(fill-position.entryPrice)*size, grossBase = sign*(base-position.entryBasePrice)*size;
    var net = gross-exitFee-entryFee;
    state.cash += gross-exitFee;
    state.grossPnl += grossBase;
    state.totalFees += exitFee;
    state.totalSlippage += exitSlippage;
    var day = new Date(row.time*1000).toISOString().slice(0,10);
    state.dailyRealizedPnl[day] = Number(state.dailyRealizedPnl[day]||0)+net;
    var realized = position.researchRealized;
    realized.net += net; realized.gross += grossBase;
    realized.fees += entryFee+exitFee; realized.slippage += entrySlippage+exitSlippage;
    realized.weightedExit += fill*size;
    realized.fills.push({time:row.time,price:fill,size:size,targetR:level,netPnl:net});
    position.entryFee -= entryFee; position.entrySlippage -= entrySlippage;
    position.size -= size; position.notional = position.size*position.entryPrice;
    position[key] = true;
  }
}

function researchUpdateTrail(position,row,reference,params) {
  var sign = position.side === 'long' ? 1 : -1;
  position.bestClose = sign===1 ? Math.max(position.bestClose,row.close) : Math.min(position.bestClose,row.close);
  var gainR = sign*(position.bestClose-position.entryPrice)/position.initialStopDistance;
  if (gainR < 1) return;
  var fee = params.takerFeePct/100, slip = params.slippagePct/100;
  var breakEven = sign===1 ? position.entryPrice*(1+fee)/(1-fee)/(1-slip) : position.entryPrice*(1-fee)/(1+fee)/(1+slip);
  var multiplier = params.researchExitMode==='adaptive' && gainR>=2 ? .75 : 1.5;
  var candidate = sign===1 ? Math.max(breakEven,position.bestClose-reference.atr14*multiplier) : Math.min(breakEven,position.bestClose+reference.atr14*multiplier);
  // New stop is available only on the next execution candle, never retroactively.
  position.trailingStop = sign===1 ? Math.max(position.trailingStop,candidate) : Math.min(position.trailingStop,candidate);
  position.breakEvenActive = true;
}

function closePosition(state, frame, fillIndex, exitPrice, reason, params, signal) {
  return closePositionAtFillRow(state, frame, frame[fillIndex], exitPrice, reason, params, signal);
}

function closePositionAtFillRow(state, frame, fillRow, exitPrice, reason, params, signal) {
  var position = state.position;
  var signalRow = frame[signal.signalIndex] || fillRow;
  var baseExit = exitBasePrice(fillRow, exitPrice, position.side, params, signal.kind);
  var adjustedExit = applySlippage(baseExit, position.side, "exit", params);
  var grossBeforeCosts = position.side === "long"
    ? (baseExit - position.entryBasePrice) * position.size
    : (position.entryBasePrice - baseExit) * position.size;
  var gross = position.side === "long"
    ? (adjustedExit - position.entryPrice) * position.size
    : (position.entryPrice - adjustedExit) * position.size;
  var exitFee = Math.abs(adjustedExit * position.size) * params.takerFeePct / 100;
  var exitSlippage = Math.abs(adjustedExit - baseExit) * position.size;
  var feePaid = position.entryFee + exitFee;
  var slippagePaid = position.entrySlippage + exitSlippage;
  var cashDelta = gross - exitFee;
  var net = cashDelta - position.entryFee;
  state.cash += cashDelta;
  state.grossPnl += grossBeforeCosts;
  state.totalFees += exitFee;
  state.totalSlippage += exitSlippage;
  var dayKey = new Date(Number(fillRow.time) * 1000).toISOString().slice(0, 10);
  state.dailyRealizedPnl[dayKey] = Number(state.dailyRealizedPnl[dayKey] || 0) + net;
  var realized = position.researchRealized;
  if (realized) {
    net += realized.net; grossBeforeCosts += realized.gross;
    feePaid += realized.fees; slippagePaid += realized.slippage;
  }
  state.trades.push({
    side: position.side,
    entrySignalTime: position.entrySignalTime,
    entryFillTime: position.entryFillTime,
    entrySignalPrice: round(position.entrySignalPrice, 8),
    entryFillPrice: round(position.entryFillPrice, 8),
    exitSignalTime: signal.signalTime || signalRow.time,
    exitFillTime: fillRow.time,
    exitSignalPrice: round(signal.signalPrice, 8),
    exitFillPrice: round(adjustedExit, 8),
    fillModel: params.fillModel,
    slippageBps: params.slippageBps,
    takerFeePct: params.takerFeePct,
    makerFeePct: params.makerFeePct,
    feePaid: round(feePaid, 6),
    slippagePaid: round(slippagePaid, 6),
    grossPnl: round(grossBeforeCosts, 4),
    netPnl: round(net, 4),
    entryTime: position.entryFillTime,
    exitTime: fillRow.time,
    entryPrice: round(position.entryFillPrice, 8),
    exitPrice: round(adjustedExit, 8),
    size: round(position.size, 8),
    notional: round(position.notional, 4),
    initialStop: round(position.initialStop, 8),
    initialStopDistance: round(position.initialStopDistance, 8),
    bestClose: round(position.bestClose, 8),
    trailingStop: round(position.trailingStop, 8),
    trailingActive: !!position.trailingActive,
    breakEvenActive: !!position.breakEvenActive,
    pnl: round(net, 4),
    accountEquity: Number(params.accountEquity),
    grossReturnPct: round(grossBeforeCosts / params.accountEquity * 100, 4),
    returnPct: round(net / params.accountEquity * 100, 4),
    barsHeld: signal.signalIndex !== undefined && signal.signalIndex !== null ? signal.signalIndex - position.entrySignalIndex : fillRow.__index - position.entryIndex,
    exitReason: reason
  });
  if (realized) {
    var trade = state.trades[state.trades.length-1];
    var sign = position.side==='long'?1:-1;
    var size = position.originalSize;
    var weightedExit = (realized.weightedExit+adjustedExit*position.size)/size;
    var excursion = Math.max(0,sign*(position.bestClose-position.entryPrice));
    trade.size = round(size,8);
    trade.notional = round(size*position.entryPrice,4);
    trade.partialExits = realized.fills;
    trade.weightedExitPrice = round(weightedExit,8);
    trade.maxFavorableCloseR = round(excursion/position.initialStopDistance,4);
    trade.profitCapturePct = excursion>0 ? round(sign*(weightedExit-position.entryPrice)/excursion*100,4) : null;
    trade.givebackR = round(Math.max(0,excursion-sign*(weightedExit-position.entryPrice))/position.initialStopDistance,4);
    trade.researchExitMode = params.researchExitMode;
  }
  state.position = null;
  state.cooldown = Number(params.cooldownBars || 0);
}

function entryBasePrice(signalRow, fillRow, side, params) {
  if (fillRow && fillRow.time !== signalRow.time) return fillRow.open;
  if (params.fillModel === "close") return signalRow.close;
  if (params.fillModel === "conservative") {
    return side === "long"
      ? Math.max(signalRow.close, fillRow.open)
      : Math.min(signalRow.close, fillRow.open);
  }
  return fillRow.open;
}

function exitBasePrice(fillRow, signalPrice, side, params, kind) {
  if (kind === "soft-execution-exit") return signalPrice;
  if (kind === "stop") {
    if (side === "long" && fillRow.open < signalPrice) return fillRow.open;
    if (side === "short" && fillRow.open > signalPrice) return fillRow.open;
  }
  if (params.fillModel === "close" || kind === "stop" || kind === "end") {
    if (params.fillModel === "conservative" && kind === "stop") {
      return side === "long"
        ? Math.min(signalPrice, fillRow.open)
        : Math.max(signalPrice, fillRow.open);
    }
    return signalPrice;
  }
  if (params.fillModel === "conservative") {
    return side === "long"
      ? Math.min(signalPrice, fillRow.open)
      : Math.max(signalPrice, fillRow.open);
  }
  return fillRow.open;
}

function applySlippage(price, side, action, params) {
  var pct = params.slippagePct / 100;
  var adverse = (side === "long" && action === "entry") || (side === "short" && action === "exit");
  return adverse ? price * (1 + pct) : price * (1 - pct);
}

function updateTrailing(position, row, params) {
  if (params.researchExitMode) return;
  var riskUnit = Number(position.initialStopDistance || position.stopDistance || 0);
  var favorableMove;
  if (position.side === "long") {
    position.bestClose = Math.max(position.bestClose, row.close);
    favorableMove = position.bestClose - position.entryFillPrice;
    if (params.breakEvenAtR > 0 && riskUnit > 0 && favorableMove >= riskUnit * params.breakEvenAtR) {
      position.trailingStop = Math.max(position.trailingStop, position.entryFillPrice);
      position.breakEvenActive = true;
    }
    if (params.trailingActivationR > 0 && (!riskUnit || favorableMove < riskUnit * params.trailingActivationR)) return;
    position.trailingActive = true;
    var distance = row.atr14 * params.trailingAtrMultiplier;
    var longStop = position.bestClose - distance;
    if (params.strategyName === "RobustTrendFollowerV1" && params.useDonchianTrail && row.donchianLow20 !== null && row.donchianLow20 !== undefined) {
      longStop = Math.max(longStop, row.donchianLow20);
    }
    position.trailingStop = Math.max(position.trailingStop, longStop);
  } else {
    position.bestClose = Math.min(position.bestClose, row.close);
    favorableMove = position.entryFillPrice - position.bestClose;
    if (params.breakEvenAtR > 0 && riskUnit > 0 && favorableMove >= riskUnit * params.breakEvenAtR) {
      position.trailingStop = Math.min(position.trailingStop, position.entryFillPrice);
      position.breakEvenActive = true;
    }
    if (params.trailingActivationR > 0 && (!riskUnit || favorableMove < riskUnit * params.trailingActivationR)) return;
    position.trailingActive = true;
    var shortDistance = row.atr14 * params.trailingAtrMultiplier;
    position.trailingStop = Math.min(position.trailingStop, position.bestClose + shortDistance);
  }
}

function currentEquity(state, row) {
  if (!state.position) return state.cash;
  var p = state.position;
  var unrealized = p.side === "long" ? (row.close - p.entryFillPrice) * p.size : (p.entryFillPrice - row.close) * p.size;
  return state.cash + unrealized;
}

function collectDiagnostics(diagnostics, frame, index, entry, state) {
  var row = frame[index];
  diagnostics.candlesEvaluated += 1;
  collectDonchianDiagnostics(diagnostics, frame, index);
  entry.blockers.forEach(function (reason) {
    diagnostics.blockerCounts[reason] = (diagnostics.blockerCounts[reason] || 0) + 1;
  });
  if (!entry.passed && entry.blockers.length <= 2 && diagnostics.nearMissCandles.length < 20) {
    diagnostics.nearMissCandles.push(preview(row, entry.blockers));
  }
  var item = preview(row, entry.blockers);
  item.entry = entry.passed;
  item.inPosition = !!state.position;
  if (diagnostics.firstSignalsPreview.length < 20) diagnostics.firstSignalsPreview.push(item);
  diagnostics.lastSignalsPreview.push(item);
  if (diagnostics.lastSignalsPreview.length > 20) diagnostics.lastSignalsPreview.shift();
}

function createDiagnostics(candleCount, params) {
  var counts = {};
  BLOCKERS.forEach(function (key) { counts[key] = 0; });
  return {
    strategyName: "RegimeFilteredTrendStrategy",
    paramsUsed: params,
    candlesEvaluated: 0,
    candlesLoaded: candleCount,
    blockerCounts: counts,
    breakoutSummary: createBreakoutSummary(),
    breakoutCandles: [],
    primaryBlocker: null,
    nearMissCandles: [],
    firstSignalsPreview: [],
    lastSignalsPreview: []
  };
}

function finalizeDiagnostics(diagnostics) {
  var primary = Object.keys(diagnostics.blockerCounts).map(function (key) {
    return [key, diagnostics.blockerCounts[key]];
  }).sort(function (a, b) { return b[1] - a[1]; })[0] || ["none", 0];
  diagnostics.primaryBlocker = primary[0] + " (" + primary[1] + "/" + diagnostics.candlesEvaluated + " candles)";
  finalizeBreakoutSummary(diagnostics.breakoutSummary);
  return diagnostics;
}

function preview(row, blockers) {
  return {
    time: row.time,
    close: row.close,
    btcRegime: row.btcRegime,
    emaTrend: row.ema200,
    adx14: row.adx14,
    volume: row.volume,
    volumeMa20: row.volumeMa20,
    fundingRate: row.fundingRate,
    fundingRateTime: row.fundingRateTime,
    openInterest: row.openInterest,
    openInterestTime: row.openInterestTime,
    openInterestChangePct: row.openInterestChangePct,
    previousDonchianHigh20: row.previousDonchianHigh20,
    previousDonchianHigh55: row.previousDonchianHigh55,
    previousDonchianHigh100: row.previousDonchianHigh100,
    currentDonchianHigh20: row.donchianHigh20,
    currentDonchianHigh55: row.donchianHigh55,
    currentDonchianHigh100: row.donchianHigh100,
    breakoutUsesPreviousChannel: true,
    breakoutDistancePct55: row.breakoutDistancePct55,
    donchianHigh55: row.donchianHigh55,
    donchianLow55: row.donchianLow55,
    blockedBy: blockers
  };
}

function formatResult(options, params, frame, state) {
  var finalEquity = state.equityCurve.length ? state.equityCurve[state.equityCurve.length - 1].equity : params.accountEquity;
  var returns = state.trades.map(function (trade) { return trade.returnPct; });
  var wins = returns.filter(function (value) { return value > 0; });
  var losses = returns.filter(function (value) { return value < 0; });
  var totalReturn = (finalEquity / params.accountEquity - 1) * 100;
  var grossReturn = state.grossPnl / params.accountEquity * 100;
  var days = frame.length > 1 ? (frame[frame.length - 1].time - frame[0].time) / 86400 : 0;
  return {
    totalReturn: round(totalReturn, 4),
    netReturn: round(totalReturn, 4),
    grossReturn: round(grossReturn, 4),
    CAGR: days >= 30 ? round((Math.pow(finalEquity / params.accountEquity, 365 / days) - 1) * 100, 4) : null,
    winRate: round(state.trades.length ? wins.length / state.trades.length * 100 : 0, 4),
    profitFactor: round(profitFactor(wins, losses), 4),
    maxDrawdown: round(maxDrawdown(state.equityCurve) * 100, 4),
    sharpeRatio: round(sharpeRatio(state.equityCurve), 4),
    averageTrade: round(avg(returns), 4),
    averageWin: round(avg(wins), 4),
    averageLoss: round(avg(losses), 4),
    avgBarsHeld: round(avg(state.trades.map(function (trade) { return trade.barsHeld; })), 4),
    exposurePct: round(frame.length ? state.exposureBars / frame.length * 100 : 0, 4),
    totalFees: round(state.totalFees, 4),
    totalSlippageCost: round(state.totalSlippage, 4),
    roundTripAverageCostPct: round(state.trades.length ? (state.totalFees + state.totalSlippage) / params.accountEquity * 100 / state.trades.length : 0, 6),
    costSettings: {
      fillModel: params.fillModel,
      makerFeePct: params.makerFeePct,
      takerFeePct: params.takerFeePct,
      slippageBps: params.slippageBps,
      roundTripCostPct: params.roundTripCostPct
    },
    candlesLoaded: frame.length,
    firstCandleTime: frame.length ? frame[0].time : null,
    lastCandleTime: frame.length ? frame[frame.length - 1].time : null,
    numberOfTrades: state.trades.length,
    trades: state.trades.length,
    equityCurve: state.equityCurve,
    tradeList: state.trades,
    markers: markersFromTrades(state.trades),
    overlays: buildOverlays(frame, state),
    overlayDiagnostics: buildOverlayDiagnostics(frame, state),
    diagnostics: finalizeDiagnostics(state.diagnostics),
    strategy: params.strategyName,
    preset: params.strategyName,
    preset_id: params.strategyName,
    symbol: options.symbol,
    interval: options.interval,
    executionInterval: options.executionInterval || options.executionTimeframe || options.interval,
    executionTimeframe: options.executionInterval || options.executionTimeframe || options.interval,
    params: params,
    total_return_pct: round(totalReturn, 4),
    gross_return_pct: round(grossReturn, 4),
    net_return_pct: round(totalReturn, 4),
    number_of_trades: state.trades.length,
    win_rate: round(state.trades.length ? wins.length / state.trades.length * 100 : 0, 4),
    max_drawdown: round(maxDrawdown(state.equityCurve) * 100, 4),
    profit_factor: round(profitFactor(wins, losses), 4),
    average_win: round(avg(wins), 4),
    average_loss: round(avg(losses), 4),
    average_bars_held: round(avg(state.trades.map(function (trade) { return trade.barsHeld; })), 4),
    trade_list: state.trades.map(function (trade) {
      return {
        entry_time: trade.entryTime,
        exit_time: trade.exitTime,
        entry_price: trade.entryPrice,
        exit_price: trade.exitPrice,
        entry_signal_time: trade.entrySignalTime,
        entry_fill_time: trade.entryFillTime,
        entry_signal_price: trade.entrySignalPrice,
        entry_fill_price: trade.entryFillPrice,
        exit_signal_time: trade.exitSignalTime,
        exit_fill_time: trade.exitFillTime,
        exit_signal_price: trade.exitSignalPrice,
        exit_fill_price: trade.exitFillPrice,
        fill_model: trade.fillModel,
        fee_paid: trade.feePaid,
        slippage_paid: trade.slippagePaid,
        return_pct: trade.returnPct,
        gross_return_pct: trade.grossReturnPct,
        bars_held: trade.barsHeld,
        exit_reason: trade.exitReason
      };
    })
  };
}

function buildOverlays(frame, state) {
  return [
    { type: "line", name: "EMA 50", color: "#748ffc", data: lineData(frame, "ema50", 50) },
    { type: "line", name: "EMA 200", color: "#ff8787", data: lineData(frame, "ema200", 200) },
    { type: "line", name: "Donchian High 55", color: "#fab005", data: lineData(frame, "donchianHigh55", 55) },
    { type: "line", name: "Donchian Low 55", color: "#4dabf7", data: lineData(frame, "donchianLow55", 55) },
    { type: "line", name: "ATR Trail", color: "#f06595", data: (state.atrTrailPoints || frame.map(function (row) { return { time: row.time, value: null }; })) }
  ];
}

function lineData(frame, key, warmup) {
  return frame.map(function (row, index) {
    var value = index < warmup - 1 ? null : row[key];
    return {
      time: row.time,
      value: Number.isFinite(value) ? round(value, 8) : null
    };
  });
}

function buildOverlayDiagnostics(frame, state) {
  var overlays = buildOverlays(frame, state);
  var overlayPoints = {};
  var firstOverlayTime = null;
  var lastOverlayTime = null;
  overlays.forEach(function (overlay) {
    overlayPoints[overlay.name] = overlay.data.length;
    overlay.data.forEach(function (point) {
      if (point.value === null || point.value === undefined) return;
      firstOverlayTime = firstOverlayTime === null ? point.time : Math.min(firstOverlayTime, point.time);
      lastOverlayTime = lastOverlayTime === null ? point.time : Math.max(lastOverlayTime, point.time);
    });
  });
  return {
    chartCandlesCount: frame.length,
    backtestCandlesCount: frame.length,
    overlayPoints: overlayPoints,
    firstChartCandleTime: frame.length ? frame[0].time : null,
    lastChartCandleTime: frame.length ? frame[frame.length - 1].time : null,
    firstOverlayTime: firstOverlayTime,
    lastOverlayTime: lastOverlayTime,
    warmupBars: { ema50: 50, ema200: 200, donchianHigh: 55, donchianLow: 55, atrTrail: 14 },
    droppedBarsReason: "none; overlays contain one point per candle with null before warmup/no position"
  };
}

function trendEma(row) {
  return row.ema200;
}

function createBreakoutSummary() {
  return {
    breakoutPass20: 0,
    breakoutPass55: 0,
    breakoutPass100: 0,
    nearBreakout20: 0,
    nearBreakout55: 0,
    nearBreakout100: 0,
    avgBreakoutDistancePct: null,
    medianBreakoutDistancePct: null,
    usesPreviousChannel: true,
    distances55: []
  };
}

function collectDonchianDiagnostics(diagnostics, frame, index) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : null;
  var candleReport = {
    time: row.time,
    close: row.close,
    breakoutUsesPreviousChannel: true
  };
  [20, 55, 100].forEach(function (period) {
    var previousLevel = previous ? previous["donchianHigh" + period] : null;
    var currentLevel = row["donchianHigh" + period];
    row["previousDonchianHigh" + period] = previousLevel;
    var distance = previousLevel ? (row.close - previousLevel) / previousLevel * 100 : null;
    row["breakoutDistancePct" + period] = distance;
    if (previousLevel && row.close > previousLevel) diagnostics.breakoutSummary["breakoutPass" + period] += 1;
    if (distance !== null && distance >= -0.5 && distance <= 0) diagnostics.breakoutSummary["nearBreakout" + period] += 1;
    if (period === 55 && distance !== null && Number.isFinite(distance)) diagnostics.breakoutSummary.distances55.push(distance);
    candleReport["previousDonchianHigh" + period] = previousLevel;
    candleReport["currentDonchianHigh" + period] = currentLevel;
    candleReport["breakoutDistancePct" + period] = distance === null ? null : round(distance, 4);
    candleReport["breakoutPass" + period] = !!(previousLevel && row.close > previousLevel);
    candleReport["nearBreakout" + period] = distance !== null && distance >= -0.5 && distance <= 0;
  });
  diagnostics.breakoutCandles.push(candleReport);
}

function finalizeBreakoutSummary(summary) {
  var distances = summary.distances55.slice().sort(function (a, b) { return a - b; });
  summary.avgBreakoutDistancePct = round(avg(distances), 4);
  summary.medianBreakoutDistancePct = distances.length
    ? round(distances[Math.floor(distances.length / 2)], 4)
    : null;
  delete summary.distances55;
}

function validPositionSize(row, params) {
  var stopDistance = row.atr14 * params.atrMultiplier;
  var size = params.accountEquity * params.riskPct / stopDistance;
  return Number.isFinite(size) && size > 0 && row.close * size <= params.maxNotional;
}

function breaksAboveDonchian(row, previous, period) {
  var level = previous["donchianHigh" + period];
  return level !== null && level !== undefined && row.close > level;
}

function breaksBelowDonchian(row, previous, period) {
  var level = previous["donchianLow" + period];
  return level !== null && level !== undefined && row.close < level;
}

function crossesBelowDonchian(row, previous, period) {
  var level = previous["donchianLow" + period];
  return level !== null && level !== undefined && row.close < level;
}

function crossesAboveDonchian(row, previous, period) {
  var level = previous["donchianHigh" + period];
  return level !== null && level !== undefined && row.close > level;
}

function markersFromTrades(trades) {
  var markers = [];
  trades.forEach(function (trade) {
    var buy = trade.side === "long";
    markers.push({ time: trade.entryTime, position: buy ? "belowBar" : "aboveBar", color: buy ? "#12b886" : "#ff5c7a", shape: buy ? "arrowUp" : "arrowDown", text: buy ? "BUY" : "SHORT" });
    markers.push({ time: trade.exitTime, position: buy ? "aboveBar" : "belowBar", color: buy ? "#ff5c7a" : "#12b886", shape: buy ? "arrowDown" : "arrowUp", text: buy ? "SELL" : "COVER" });
    (trade.partialExits || []).forEach(fill=>markers.push({time:fill.time,position:buy?'aboveBar':'belowBar',color:'#69d7bb',shape:'circle',text:'25% @ '+fill.targetR+'R'}));
  });
  return markers.sort((a,b)=>a.time-b.time);
}

function maxDrawdown(curve) {
  var peak = curve.length ? curve[0].equity : 1;
  var worst = 0;
  curve.forEach(function (point) {
    peak = Math.max(peak, point.equity);
    worst = Math.min(worst, peak ? (point.equity - peak) / peak : 0);
  });
  return Math.abs(worst);
}

function sharpeRatio(curve) {
  if (curve.length < 3) return 0;
  var returns = [];
  for (var i = 1; i < curve.length; i += 1) {
    returns.push((curve[i].equity - curve[i - 1].equity) / curve[i - 1].equity);
  }
  var mean = avg(returns);
  var variance = avg(returns.map(function (value) { return Math.pow(value - mean, 2); }));
  var stdev = Math.sqrt(variance);
  return stdev ? mean / stdev * Math.sqrt(252) : 0;
}

function profitFactor(wins, losses) {
  var winSum = wins.reduce(function (sum, value) { return sum + value; }, 0);
  var lossSum = Math.abs(losses.reduce(function (sum, value) { return sum + value; }, 0));
  if (!lossSum) return winSum ? winSum : 0;
  return winSum / lossSum;
}

function avg(values) {
  if (!values.length) return 0;
  return values.reduce(function (sum, value) { return sum + value; }, 0) / values.length;
}

function round(value, digits) {
  var factor = Math.pow(10, digits || 4);
  return Math.round((Number(value) || 0) * factor) / factor;
}

module.exports = {
  run: run,
  defaultParams: defaultParams,
  BLOCKERS: BLOCKERS,
  testHooks: {
    closeOnExecutionCandle: closeOnExecutionCandle,
    researchPartialProfits: researchPartialProfits,
    researchUpdateTrail: researchUpdateTrail,
    closePositionAtFillRow: closePositionAtFillRow,
    entryReadiness: entryReadiness,
    relativeStrengthFreshV3Entry: relativeStrengthFreshV3Entry,
    exitBasePrice: exitBasePrice,
    softExecutionExitSignal: softExecutionExitSignal,
    shouldDeferEntry: shouldDeferEntry,
    shouldDeferExit: shouldDeferExit
  }
};
