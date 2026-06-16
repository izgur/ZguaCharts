const data = require("../data");
const indicators = require("../indicators");
const regime = require("../regime");

const BLOCKERS = [
  "regimeNotBullish",
  "regimeNotBearish",
  "neutralRegime",
  "emaTrendFailed",
  "donchianBreakoutFailed",
  "adxTooLow",
  "volumeTooLow",
  "atrMissing",
  "stopTooClose",
  "maxOpenTradesReached",
  "alreadyInPosition",
  "cooldownBlocked",
  "researchRegimeFilterBlocked",
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
  "vwapConfirmationFailed"
];

function run(options) {
  var strategyName = options.strategy || "RegimeFilteredTrendStrategy";
  var params = Object.assign({}, defaultParams(strategyName), options.params || {});
  params = normalizeExecutionParams(params);
  params.strategyName = strategyName;
  var candles = data.normalizeCandles(options.candles || []);
  var regimeCandles = data.normalizeCandles(options.regimeCandles || []);
  var mapped = regime.mapRegimeToCandles(candles, regimeCandles);
  var frame = indicators.buildIndicatorFrame(mapped, {
    emaReclaim: params.emaFast || 20,
    emaTrendFast: params.emaSlow || 50,
    emaSlow: params.emaTrend || params.emaTrendLength,
    atrPeriod: 14,
    adxPeriod: 14
  }).map(function (row, index) {
    return Object.assign({}, row, {
      btcRegime: mapped[index] ? mapped[index].btcRegime : "neutral",
      btcRegimeTime: mapped[index] ? mapped[index].btcRegimeTime : null
    });
  });
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
    diagnostics: createDiagnostics(frame.length, params)
  };

  for (var i = 0; i < frame.length; i += 1) {
    var row = frame[i];
    if (state.pendingExit && state.position) {
      closePosition(state, frame, i, row.open, state.pendingExit.reason, params, state.pendingExit);
      state.pendingExit = null;
    }
    if (state.pendingEntry && !state.position) {
      openPosition(state, frame, state.pendingEntry.signalIndex, i, state.pendingEntry.side, params);
      state.pendingEntry = null;
    }
    if (state.position) {
      state.exposureBars += 1;
      var exit = exitSignal(state.position, frame, i, params);
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

    var entry = applyResearchEntryFilter(entrySignal(frame, i, state, params), frame, i, state, params, options);
    collectDiagnostics(state.diagnostics, frame, i, entry, state);
    if (!state.position && !state.pendingEntry && entry.passed) {
      if (shouldDeferEntry(params, i, frame.length)) {
        state.pendingEntry = {
          signalIndex: i,
          signalTime: row.time,
          signalPrice: row.close,
          side: entry.side
        };
      } else {
        openPosition(state, frame, i, i, entry.side, params);
      }
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

  if (state.position && frame.length) {
    var lastIndex = frame.length - 1;
    closePosition(state, frame, lastIndex, frame[lastIndex].close, "End of data", params, {
      signalIndex: lastIndex,
      signalTime: frame[lastIndex].time,
      signalPrice: frame[lastIndex].close,
      kind: "end"
    });
  }

  return formatResult(options, params, frame, state);
}

function applyResearchEntryFilter(entry, frame, index, state, params, options) {
  if (!entry || !entry.passed || typeof options.entryFilter !== "function") return entry;
  var decision = options.entryFilter({
    frame: frame,
    index: index,
    row: frame[index],
    state: state,
    params: params,
    entry: entry
  });
  if (decision === true || (decision && decision.allow === true)) return entry;
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

function defaultParams(strategyName) {
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
    cooldownBars: 0
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
  return out;
}

function entrySignal(frame, index, state, params) {
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
  if (params.strategyName === "SimpleAtrTrendV2") return simpleAtrTrendV2Entry(frame, index, state, params);
  if (params.strategyName === "FibPullbackContinuationV1") return fibPullbackContinuationEntry(frame, index, state, params);
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
  if (!(previous.rsi14 < params.rsiPullbackLevel && row.rsi14 > params.rsiReclaimLevel)) blockers.push("pullbackReclaimFailed");
  return signalFromBlockers(blockers);
}

function emaBounceV2Entry(frame, index, state, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  var blockers = baseV2Blockers(row, state, params);
  var touched = row.low <= row.ema50 + row.atr14 * params.emaBounceAtr || row.low <= row.ema100 + row.atr14 * params.emaBounceAtr;
  var reclaim = previous.close < previous.ema20 && row.close > row.ema20;
  if (!(touched && reclaim)) blockers.push("pullbackReclaimFailed");
  return signalFromBlockers(blockers);
}

function breakoutRetestV2Entry(frame, index, state, params) {
  var blockers = baseV2Blockers(frame[index], state, params);
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
  var lookback = params.rsLookback;
  if (index < lookback || !frame[index - lookback].btcClose4h || !row.btcClose4h) {
    blockers.push("relativeStrengthFailed");
  } else {
    var symbolReturn = (row.close - frame[index - lookback].close) / frame[index - lookback].close;
    var btcReturn = (row.btcClose4h - frame[index - lookback].btcClose4h) / frame[index - lookback].btcClose4h;
    if (!(symbolReturn > btcReturn + params.rsThreshold && row.rsi14 >= params.rsiMin && row.rsi14 <= params.rsiMax && row.close > row.ema20)) blockers.push("relativeStrengthFailed");
  }
  return signalFromBlockers(blockers);
}

function simpleAtrTrendV2Entry(frame, index, state, params) {
  var row = frame[index];
  var blockers = baseV2Blockers(row, state, params);
  if (!(row.ema20 > row.ema50 && row.close > row.ema50)) blockers.push("emaTrendFailed");
  if (params.useRsiFilter && !(row.rsi14 >= params.rsiMin && row.rsi14 <= params.rsiMax)) blockers.push("pullbackReclaimFailed");
  return signalFromBlockers(blockers);
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

function exitSignal(position, frame, index, params) {
  var row = frame[index];
  var previous = index > 0 ? frame[index - 1] : row;
  if (position.side === "long") {
    if (row.low <= position.trailingStop) return { price: position.trailingStop, reason: "ATR trailing stop", kind: "stop" };
    var barsHeld = row.__index - position.entryIndex;
    var canRuleExit = barsHeld >= Number(params.minHoldBars || 0);
    if (position.takeProfit && row.high >= position.takeProfit) return { price: position.takeProfit, reason: "ATR take profit", kind: "take-profit" };
    if (canRuleExit && ["RegimePullbackTrend", "EmaPullbackContinuation", "MeanReversionInBullRegime", "MomentumContinuation", "PullbackReclaimV2", "RangeExpansionV2", "RelativeStrengthV2", "SimpleAtrTrendV2", "FibPullbackContinuationV1"].includes(params.strategyName) && row.close < row.ema50) return { price: row.close, reason: "EMA50 exit", kind: "close-signal" };
    if (params.strategyName === "EmaBounceV2" && row.close < row.ema100) return { price: row.close, reason: "EMA100 exit", kind: "close-signal" };
    if (crossesBelowDonchian(row, previous, params.donchianExit)) return { price: row.close, reason: "Donchian exit", kind: "close-signal" };
  } else {
    if (row.high >= position.trailingStop) return { price: position.trailingStop, reason: "ATR trailing stop", kind: "stop" };
    if (crossesAboveDonchian(row, previous, params.donchianExit)) return { price: row.close, reason: "Donchian exit", kind: "close-signal" };
  }
  return null;
}

function shouldDeferEntry(params, index, frameLength) {
  return (params.fillModel === "next-open" || params.fillModel === "conservative") && index < frameLength - 1;
}

function shouldDeferExit(exit, params, index, frameLength) {
  return exit.kind === "close-signal" && (params.fillModel === "next-open" || params.fillModel === "conservative") && index < frameLength - 1;
}

function openPosition(state, frame, signalIndex, fillIndex, side, params) {
  var signalRow = frame[signalIndex];
  var fillRow = frame[fillIndex];
  var stopDistance = signalRow.atr14 * params.atrMultiplier;
  var size = state.cash * params.riskPct / stopDistance;
  var baseEntry = entryBasePrice(signalRow, fillRow, side, params);
  var rawEntry = applySlippage(baseEntry, side, "entry", params);
  var notional = rawEntry * size;
  if (!Number.isFinite(size) || size <= 0 || notional > params.maxNotional) return;
  var fee = notional * params.takerFeePct / 100;
  var slippagePaid = Math.abs(rawEntry - baseEntry) * size;
  state.cash -= fee;
  state.totalFees += fee;
  state.totalSlippage += slippagePaid;
  state.position = {
    side: side,
    entryIndex: fillIndex,
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
    trailingStop: side === "long" ? rawEntry - stopDistance : rawEntry + stopDistance,
    takeProfit: params.strategyName === "FibPullbackContinuationV1" ? rawEntry + signalRow.atr14 * Number(params.takeProfitAtr || 2.8) : null,
    bestClose: fillRow.close,
    strategyName: params.strategyName,
    entryFee: fee,
    entrySlippage: slippagePaid
  };
}

function closePosition(state, frame, fillIndex, exitPrice, reason, params, signal) {
  var position = state.position;
  var fillRow = frame[fillIndex];
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
  var net = gross - exitFee;
  state.cash += net;
  state.grossPnl += grossBeforeCosts;
  state.totalFees += exitFee;
  state.totalSlippage += exitSlippage;
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
    pnl: round(net, 4),
    accountEquity: Number(params.accountEquity),
    grossReturnPct: round(grossBeforeCosts / params.accountEquity * 100, 4),
    returnPct: round(net / params.accountEquity * 100, 4),
    barsHeld: fillRow.__index - position.entryIndex,
    exitReason: reason
  });
  state.position = null;
  state.cooldown = Number(params.cooldownBars || 0);
}

function entryBasePrice(signalRow, fillRow, side, params) {
  if (params.fillModel === "close") return signalRow.close;
  if (params.fillModel === "conservative") {
    return side === "long"
      ? Math.max(signalRow.close, fillRow.open)
      : Math.min(signalRow.close, fillRow.open);
  }
  return fillRow.open;
}

function exitBasePrice(fillRow, signalPrice, side, params, kind) {
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
  var distance = row.atr14 * params.atrMultiplier;
  if (position.side === "long") {
    position.bestClose = Math.max(position.bestClose, row.close);
    position.trailingStop = Math.max(position.trailingStop, position.bestClose - distance);
  } else {
    position.bestClose = Math.min(position.bestClose, row.close);
    position.trailingStop = Math.min(position.trailingStop, position.bestClose + distance);
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
  });
  return markers;
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
  BLOCKERS: BLOCKERS
};
