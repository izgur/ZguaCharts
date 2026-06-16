const registry = {};

function registerStrategy(strategy) {
  if (!strategy || !strategy.name) throw new Error("Strategy requires a name");
  registry[normalizeName(strategy.name)] = strategy;
  return strategy;
}

function getStrategy(name) {
  var key = normalizeName(name || "ConservativeTrend");
  if (!registry[key]) throw new Error("Unknown strategy: " + name);
  return registry[key];
}

function listStrategies() {
  return Object.keys(registry).map(function (key) { return registry[key]; });
}

function normalizeName(name) {
  return String(name).replace(/[^a-zA-Z0-9]/g, "").toLowerCase();
}

function score(row) {
  var value = 0;
  value += row.ema50 > row.ema200 ? 20 : -20;
  value += row.ema9 > row.ema21 ? 15 : -15;
  value += row.supertrendDirection > 0 ? 15 : -15;
  value += row.rsi14 > 50 ? 10 : -10;
  if (row.rsi14 > 70) value -= 10;
  if (row.rsi14 < 30) value += 10;
  value += row.macdLine > row.macdSignal ? 15 : -15;
  value += row.vwap && row.close > row.vwap ? 10 : -10;
  value += row.volumeMa20 && row.volume > row.volumeMa20 * 1.2 ? 5 : 0;
  return Math.max(-100, Math.min(100, value));
}

function previous(frame, index) {
  return index > 0 ? frame[index - 1] : frame[index];
}

registerStrategy({
  name: "RegimeFilteredTrendStrategy",
  label: "Regime Filtered Trend Strategy",
  params: {
    accountEquity: 10000,
    riskPct: 0.005,
    atrMultiplier: 2.5,
    takerFeePct: 0,
    makerFeePct: 0,
    slippagePct: 0,
    maxOpenTrades: 1,
    maxNotional: 100000,
    donchianEntry: 55,
    donchianExit: 20,
    adxThreshold: 18,
    emaTrendLength: 200,
    volumeFilter: true,
    shortMode: false
  },
  entry: function () { return false; },
  exit: function () { return false; },
  risk: function () {
    return { stop: null, takeProfit: null, trailingActivation: null, trailingDistance: null };
  }
});

[
  "RegimeDonchian20",
  "RegimeDonchianCloseConfirm",
  "RegimePullbackTrend",
  "EmaPullbackContinuation",
  "TrendBreakoutRetest",
  "VolatilitySqueezeBreakout",
  "MeanReversionInBullRegime",
  "MomentumContinuation",
  "PullbackReclaimV2",
  "EmaBounceV2",
  "BreakoutRetestV2",
  "RangeExpansionV2",
  "RelativeStrengthV2",
  "SimpleAtrTrendV2",
  "FibPullbackContinuationV1"
].forEach(function (name) {
  registerStrategy({
    name: name,
    label: name.replace(/([a-z])([A-Z])/g, "$1 $2"),
    params: {},
    entry: function () { return false; },
    exit: function () { return false; },
    risk: function () {
      return { stop: null, takeProfit: null, trailingActivation: null, trailingDistance: null };
    }
  });
});

registerStrategy({
  name: "AlwaysLongTest",
  label: "Always Long Test",
  requiresIndicators: false,
  params: {
    minHoldBars: 0,
    cooldownBars: 0
  },
  entry: function (ctx) {
    return ctx.index % 50 === 10;
  },
  exit: function (ctx) {
    return ctx.index % 50 === 30;
  },
  entryDiagnostics: function (ctx) {
    var target = ctx.index % 50 === 10;
    return {
      passed: target,
      reasons: target ? [] : ["not_test_entry_candle"],
      values: { modulo: ctx.index % 50 }
    };
  },
  exitDiagnostics: function (ctx) {
    var target = ctx.index % 50 === 30;
    return {
      passed: target,
      reasons: target ? [] : ["not_test_exit_candle"],
      values: { modulo: ctx.index % 50 }
    };
  },
  risk: function () {
    return {
      stop: null,
      takeProfit: null,
      trailingActivation: null,
      trailingDistance: null
    };
  }
});

registerStrategy({
  name: "ConservativeTrend",
  label: "Conservative Trend",
  params: {
    emaFast: 9,
    emaMomentumSlow: 21,
    emaTrendFast: 50,
    emaSlow: 200,
    rsiMin: 45,
    rsiMax: 68,
    stopAtr: 2.5,
    takeProfitAtr: 4,
    trailingActivationAtr: 1.5,
    trailingAtr: 2,
    minHoldBars: 4,
    cooldownBars: 5,
    breakoutLookback: 20,
    requireVolume: false,
    volumeMultiplier: 1
  },
  entry: function (ctx) {
    return conservativeEntryDiagnostics(ctx).passed;
  },
  exit: function (ctx) {
    var r = ctx.row;
    return r.close < r.ema50 || r.supertrendDirection < 0;
  },
  entryDiagnostics: conservativeEntryDiagnostics,
  exitDiagnostics: function (ctx) {
    var r = ctx.row;
    var closeBelowEma50 = r.close < r.ema50;
    var supertrendBearish = r.supertrendDirection < 0;
    return {
      passed: closeBelowEma50 || supertrendBearish,
      reasons: [],
      values: {
        closeBelowEma50: closeBelowEma50,
        supertrendBearish: supertrendBearish
      }
    };
  },
  risk: atrRisk
});

registerStrategy({
  name: "ConservativeTrendLoose",
  label: "Conservative Trend Loose",
  params: {
    emaFast: 9,
    emaMomentumSlow: 21,
    emaTrendFast: 50,
    emaSlow: 200,
    rsiMin: 30,
    rsiMax: 78,
    stopAtr: 3,
    takeProfitAtr: 4,
    trailingActivationAtr: 2,
    trailingAtr: 2.5,
    minHoldBars: 4,
    cooldownBars: 3,
    breakoutLookback: 20,
    requireBreakout: false,
    requireVolume: false,
    volumeMultiplier: 1
  },
  entry: function (ctx) {
    return conservativeLooseEntryDiagnostics(ctx).passed;
  },
  exit: function (ctx) {
    var r = ctx.row;
    return r.close < r.ema50 || r.supertrendDirection < 0;
  },
  entryDiagnostics: conservativeLooseEntryDiagnostics,
  exitDiagnostics: function (ctx) {
    var r = ctx.row;
    var closeBelowEma50 = r.close < r.ema50;
    var supertrendBearish = r.supertrendDirection < 0;
    return {
      passed: closeBelowEma50 || supertrendBearish,
      reasons: [],
      values: {
        closeBelowEma50: closeBelowEma50,
        supertrendBearish: supertrendBearish
      }
    };
  },
  risk: atrRisk
});

registerStrategy({
  name: "MomentumScalping",
  label: "Momentum Scalping",
  params: {
    emaFast: 9,
    emaMomentumSlow: 21,
    emaTrendFast: 50,
    emaSlow: 200,
    rsiMin: 48,
    rsiMax: 72,
    scoreThreshold: 55,
    stopAtr: 1.8,
    takeProfitAtr: 2.5,
    trailingActivationAtr: 1,
    trailingAtr: 1.3,
    minHoldBars: 3,
    cooldownBars: 3
  },
  entry: function (ctx) {
    var r = ctx.row;
    var p = ctx.params;
    return r.close > r.ema50 &&
      r.ema9 > r.ema21 &&
      r.macdLine > r.macdSignal &&
      r.rsi14 >= p.rsiMin &&
      r.rsi14 <= p.rsiMax &&
      (!r.vwap || r.close > r.vwap) &&
      score(r) >= p.scoreThreshold &&
      r.close <= r.ema21 + 1.2 * r.atr14;
  },
  exit: function (ctx) {
    var r = ctx.row;
    var prev = previous(ctx.frame, ctx.index);
    return (prev.macdLine >= prev.macdSignal && r.macdLine < r.macdSignal) || r.close < r.ema21;
  },
  risk: atrRisk
});

registerStrategy({
  name: "MeanReversion",
  label: "Mean Reversion",
  params: {
    emaSlow: 200,
    rsiLimit: 32,
    stopAtr: 1.5,
    takeProfitAtr: 2,
    minHoldBars: 2,
    cooldownBars: 5
  },
  entry: function (ctx) {
    var r = ctx.row;
    var prev = previous(ctx.frame, ctx.index);
    var emaFlat = Math.abs(r.ema200 - prev.ema200) <= r.atr14;
    return prev.close < prev.bbLower &&
      r.close > r.bbLower &&
      r.rsi14 < ctx.params.rsiLimit &&
      (r.close > r.ema200 || emaFlat);
  },
  exit: function (ctx) {
    return ctx.row.high >= ctx.row.bbMiddle;
  },
  risk: function (ctx) {
    var base = atrRisk(ctx);
    base.takeProfit = Math.min(base.takeProfit, ctx.row.bbMiddle || base.takeProfit);
    return base;
  }
});

registerStrategy({
  name: "PullbackTrend",
  label: "Pullback Trend",
  params: {
    emaTrendFast: 50,
    emaSlow: 200,
    rsiMin: 38,
    rsiMax: 55,
    stopAtr: 2,
    takeProfitAtr: 3,
    trailingActivationAtr: 1.5,
    trailingAtr: 2,
    minHoldBars: 4,
    cooldownBars: 5
  },
  entry: function (ctx) {
    var r = ctx.row;
    var prev = previous(ctx.frame, ctx.index);
    var nearEma21 = Math.abs(r.close - r.ema21) <= 0.5 * r.atr14 || r.low <= r.ema21 + 0.5 * r.atr14;
    var nearEma50 = Math.abs(r.close - r.ema50) <= 0.5 * r.atr14 || r.low <= r.ema50 + 0.5 * r.atr14;
    return r.ema50 > r.ema200 &&
      r.close > r.ema200 &&
      r.rsi14 >= ctx.params.rsiMin &&
      r.rsi14 <= ctx.params.rsiMax &&
      (nearEma21 || nearEma50) &&
      r.close > r.open &&
      r.macdHistogram > prev.macdHistogram &&
      r.close <= r.ema21 + 0.5 * r.atr14;
  },
  exit: function (ctx) {
    return ctx.row.close < ctx.row.ema50;
  },
  risk: function (ctx) {
    var base = atrRisk(ctx);
    var start = Math.max(0, ctx.index - 10);
    var swingLow = ctx.frame.slice(start, ctx.index + 1).reduce(function (lowest, row) {
      return Math.min(lowest, row.low);
    }, ctx.row.low);
    base.stop = Math.min(base.stop, swingLow);
    return base;
  }
});

function atrRisk(ctx) {
  var p = ctx.params;
  var atr = ctx.row.atr14 || 0;
  return {
    stop: ctx.entryPrice - (p.stopAtr || 2) * atr,
    takeProfit: ctx.entryPrice + (p.takeProfitAtr || 3) * atr,
    trailingActivation: (p.trailingActivationAtr || 999) * atr,
    trailingDistance: (p.trailingAtr || 999) * atr
  };
}

function conservativeEntryDiagnostics(ctx) {
  return conservativeConditionDiagnostics(ctx, false);
}

function conservativeLooseEntryDiagnostics(ctx) {
  return conservativeConditionDiagnostics(ctx, true);
}

function conservativeConditionDiagnostics(ctx, loose) {
  var r = ctx.row;
  var p = ctx.params;
  var trendFast = loose ? r.ema9 : r.ema50;
  var trendSlow = r.ema200;
  var emaTrend = loose
    ? r.close > trendSlow * 0.985 && trendFast > trendSlow * 0.985
    : r.close > trendSlow && trendFast > trendSlow && r.supertrendDirection > 0;
  var rsi = r.rsi14 >= p.rsiMin && r.rsi14 <= p.rsiMax;
  var breakoutLevel = recentHigh(ctx.frame, ctx.index, p.breakoutLookback || 20);
  var momentumBreakout = r.macdHistogram > 0 && r.close <= r.ema21 + (p.stopAtr || 2.5) * 0.6 * r.atr14;
  var priceBreakout = breakoutLevel === null ? false : r.close > breakoutLevel;
  var breakout = loose && p.requireBreakout === false ? momentumBreakout || priceBreakout || r.close > r.ema21 : momentumBreakout;
  var volume = p.requireVolume ? r.volumeMa20 && r.volume > r.volumeMa20 * (p.volumeMultiplier || 1) : true;
  var conditions = {
    emaTrend: !!emaTrend,
    rsi: !!rsi,
    breakout: !!breakout,
    volume: !!volume
  };
  var reasons = Object.keys(conditions).filter(function (key) { return !conditions[key]; });
  return {
    passed: reasons.length === 0,
    reasons: reasons,
    values: {
      close: r.close,
      emaFast: trendFast,
      emaSlow: trendSlow,
      ema21: r.ema21,
      rsi: r.rsi14,
      breakoutLevel: breakoutLevel,
      volume: r.volume,
      volumeMa20: r.volumeMa20,
      macdHistogram: r.macdHistogram,
      supertrendDirection: r.supertrendDirection,
      atr14: r.atr14,
      conditions: conditions
    }
  };
}

function recentHigh(frame, index, lookback) {
  if (index <= 0) return null;
  var start = Math.max(0, index - lookback);
  var rows = frame.slice(start, index);
  if (!rows.length) return null;
  return rows.reduce(function (highest, row) {
    return Math.max(highest, row.high);
  }, rows[0].high);
}

module.exports = {
  registerStrategy: registerStrategy,
  getStrategy: getStrategy,
  listStrategies: listStrategies,
  score: score
};
