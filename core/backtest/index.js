const data = require("../data");
const indicators = require("../indicators");
const strategies = require("../strategies");

function runBacktest(options) {
  options = options || {};
  return data.fetchCandles({
    source: options.source || "bybit",
    symbol: options.symbol,
    interval: options.interval,
    from: options.from,
    to: options.to,
    limit: options.limit,
    candles: options.candles
  }).then(function (candles) {
    return runBacktestOnCandles(Object.assign({}, options, { candles: candles }));
  });
}

function runBacktestOnCandles(options) {
  var strategy = strategies.getStrategy(options.strategy || "ConservativeTrend");
  var params = Object.assign({}, strategy.params || {}, options.params || {});
  var frame = indicators.buildIndicatorFrame(data.normalizeCandles(options.candles || []), params);
  var warmup = strategy.requiresIndicators === false ? 0 : Math.min(250, Math.max(50, Math.floor(frame.length * 0.2)));
  var feePct = Number(params.feePct || options.feePct || 0);
  var slippagePct = Number(params.slippagePct || options.slippagePct || 0);
  var debug = options.debug === true || options.debug === "true";
  var debugState = createDebugState(frame, strategy, params);
  var equity = 1;
  var equityCurve = [];
  var tradeList = [];
  var position = null;
  var cooldown = 0;

  for (var i = 0; i < frame.length; i += 1) {
    var row = frame[i];
    collectSignalPreview(debugState, strategy, frame, i, row, params);
    if (position) {
      updateTrailing(position, row);
      var exit = checkExit(strategy, frame, i, row, position, params);
      if (exit) {
        equity = closePosition(tradeList, equity, position, row, exit.price, exit.reason, feePct, slippagePct);
        position = null;
        cooldown = Number(params.cooldownBars || 0);
      }
    }

    if (debugState.conservativeTrendDiagnostics) {
      collectConservativeTrendDiagnostics(debugState, i, row, position, cooldown, warmup, evaluateEntry(strategy, frame, i, row, params));
    }

    if (!position) {
      var entryResult = evaluateEntry(strategy, frame, i, row, params);
      if (entryResult.passed) debugState.entrySignalsCount += 1;
      var blockReasons = engineBlockReasons(i, warmup, cooldown, entryResult);
      if (blockReasons.length) recordSkips(debugState, blockReasons);
      if (entryResult.passed && i >= warmup && cooldown <= 0) {
        position = openPosition(strategy, frame, i, row, params, feePct, slippagePct);
      }
    }

    equityCurve.push({
      time: row.time,
      equity: round(position ? equity * (row.close / position.entryPrice) : equity, 8)
    });
    if (!position && cooldown > 0) cooldown -= 1;
  }

  if (position && frame.length) {
    equity = closePosition(tradeList, equity, position, frame[frame.length - 1], frame[frame.length - 1].close, "End of data", feePct, slippagePct);
  }

  return formatResult({
    symbol: options.symbol,
    interval: options.interval,
    strategy: strategy.name,
    params: params,
    equity: equity,
    equityCurve: equityCurve,
    tradeList: tradeList,
    candlesLoaded: frame.length,
    warmup: warmup,
    debugDiagnostics: debug ? finalizeDebugState(debugState, frame, strategy, params) : null
  });
}

function evaluateEntry(strategy, frame, index, row, params) {
  if (strategy.entryDiagnostics) return strategy.entryDiagnostics({ frame: frame, index: index, row: row, params: params });
  var passed = strategy.entry({ frame: frame, index: index, row: row, params: params });
  return { passed: passed, reasons: passed ? [] : ["entry_condition_false"], values: {} };
}

function evaluateExit(strategy, frame, index, row, position, params) {
  if (strategy.exitDiagnostics) return strategy.exitDiagnostics({ frame: frame, index: index, row: row, position: position, params: params });
  var passed = strategy.exit({ frame: frame, index: index, row: row, position: position, params: params });
  return { passed: passed, reasons: passed ? [] : ["exit_condition_false"], values: {} };
}

function openPosition(strategy, frame, index, row, params, feePct, slippagePct) {
  var entryPrice = row.close * (1 + slippagePct / 100);
  var risk = strategy.risk({ frame: frame, index: index, row: row, params: params, entryPrice: entryPrice });
  return {
    entryIndex: index,
    entryTime: row.time,
    entryPrice: entryPrice,
    stop: risk.stop,
    takeProfit: risk.takeProfit,
    trailingActivation: risk.trailingActivation,
    trailingDistance: risk.trailingDistance,
    highestClose: row.close,
    trailingStop: null,
    feePct: feePct
  };
}

function checkExit(strategy, frame, index, row, position, params) {
  if (position.stop !== null && row.low <= position.stop) return { price: position.stop, reason: "ATR stop" };
  if (position.takeProfit !== null && row.high >= position.takeProfit) return { price: position.takeProfit, reason: "Take profit" };
  if (position.trailingStop !== null && row.low <= position.trailingStop) return { price: position.trailingStop, reason: "Trailing stop" };
  if (index - position.entryIndex < Number(params.minHoldBars || 0)) return null;
  if (evaluateExit(strategy, frame, index, row, position, params).passed) {
    return { price: row.close, reason: "Strategy exit" };
  }
  return null;
}

function updateTrailing(position, row) {
  position.highestClose = Math.max(position.highestClose, row.close);
  if (!position.trailingDistance || position.trailingDistance > 1e20) return;
  if (row.close >= position.entryPrice + position.trailingActivation) {
    var next = position.highestClose - position.trailingDistance;
    position.trailingStop = position.trailingStop === null ? next : Math.max(position.trailingStop, next);
  }
}

function closePosition(trades, equity, position, row, exitPrice, reason, feePct, slippagePct) {
  var adjustedExit = exitPrice * (1 - slippagePct / 100);
  var gross = (adjustedExit - position.entryPrice) / position.entryPrice;
  var net = gross - (position.feePct + feePct) / 100;
  trades.push({
    entryTime: position.entryTime,
    exitTime: row.time,
    entryPrice: round(position.entryPrice, 8),
    exitPrice: round(adjustedExit, 8),
    returnPct: round(net * 100, 4),
    barsHeld: row.__index !== undefined ? row.__index - position.entryIndex : null,
    exitReason: reason
  });
  return equity * (1 + net);
}

function formatResult(payload) {
  var trades = payload.tradeList;
  var returns = trades.map(function (trade) { return trade.returnPct; });
  var wins = returns.filter(function (value) { return value > 0; });
  var losses = returns.filter(function (value) { return value < 0; });
  var totalReturn = round((payload.equity - 1) * 100, 4);
  var result = {
    totalReturn: totalReturn,
    trades: trades.length,
    winRate: round(trades.length ? wins.length / trades.length * 100 : 0, 4),
    averageWin: round(avg(wins), 4),
    averageLoss: round(avg(losses), 4),
    maxDrawdown: round(maxDrawdown(payload.equityCurve) * 100, 4),
    profitFactor: round(profitFactor(wins, losses), 4),
    sharpeRatio: round(sharpeRatio(payload.equityCurve), 4),
    avgBarsHeld: round(avg(trades.map(function (trade) { return trade.barsHeld || 0; })), 4),
    equityCurve: payload.equityCurve,
    tradeList: trades,
    diagnostics: {
      symbol: payload.symbol,
      interval: payload.interval,
      strategy: payload.strategy,
      params: payload.params,
      candlesLoaded: payload.candlesLoaded,
      warmupCandles: payload.warmup
    },
    warnings: []
  };
  if (trades.length === 0 || trades.length < 5) {
    result.warnings.push("WARNING: Strategy statistically invalid due to insufficient trades.");
  }
  if (payload.debugDiagnostics) {
    Object.keys(payload.debugDiagnostics).forEach(function (key) {
      result.diagnostics[key] = payload.debugDiagnostics[key];
    });
    result.diagnostics.debug = payload.debugDiagnostics;
  }
  // Compatibility for the existing Flask/plain JS modal.
  result.total_return_pct = result.totalReturn;
  result.number_of_trades = result.trades;
  result.win_rate = result.winRate;
  result.average_win = result.averageWin;
  result.average_loss = result.averageLoss;
  result.max_drawdown = result.maxDrawdown;
  result.profit_factor = result.profitFactor;
  result.average_bars_held = result.avgBarsHeld;
  result.trade_list = trades.map(function (trade) {
    return {
      entry_time: trade.entryTime,
      exit_time: trade.exitTime,
      entry_price: trade.entryPrice,
      exit_price: trade.exitPrice,
      return_pct: trade.returnPct,
      bars_held: trade.barsHeld,
      exit_reason: trade.exitReason
    };
  });
  result.markers = markersFromTrades(result.trade_list);
  result.preset = payload.strategy;
  result.preset_id = payload.strategy;
  return result;
}

function createDebugState(frame, strategy, params) {
  var state = {
    candlesLoaded: frame.length,
    firstCandleTime: frame.length ? frame[0].time : null,
    lastCandleTime: frame.length ? frame[frame.length - 1].time : null,
    strategyName: strategy.name,
    paramsUsed: params,
    indicatorsReadyCount: countIndicatorsReady(frame, strategy),
    entrySignalsCount: 0,
    exitSignalsCount: 0,
    skippedCandlesCount: 0,
    skipReasons: {},
    firstSignalsPreview: [],
    lastSignalsPreview: []
  };
  if (strategy.name === "ConservativeTrend" || strategy.name === "ConservativeTrendLoose") {
    state.conservativeTrendDiagnostics = {
      candlesEvaluated: 0,
      entryConditionCounts: {
        emaTrendPassed: 0,
        emaTrendFailed: 0,
        rsiPassed: 0,
        rsiFailed: 0,
        volumePassed: 0,
        volumeFailed: 0,
        breakoutPassed: 0,
        breakoutFailed: 0,
        cooldownBlocked: 0,
        alreadyInPositionBlocked: 0,
        finalEntriesTriggered: 0
      },
      primaryBlocker: null,
      perCandleConditionPreview: {
        first: [],
        last: []
      },
      nearMissCandles: [],
      recommendedThresholdChanges: []
    };
  }
  return state;
}

function countIndicatorsReady(frame, strategy) {
  if (strategy.requiresIndicators === false) return frame.length;
  var keys = ["ema9", "ema21", "ema50", "ema200", "rsi14", "atr14", "macdLine", "macdSignal", "macdHistogram", "supertrendDirection"];
  return frame.filter(function (row) {
    return keys.every(function (key) {
      return row[key] !== null && row[key] !== undefined && Number.isFinite(Number(row[key]));
    });
  }).length;
}

function collectSignalPreview(debugState, strategy, frame, index, row, params) {
  var entry = evaluateEntry(strategy, frame, index, row, params);
  var exit = evaluateExit(strategy, frame, index, row, null, params);
  if (exit.passed) debugState.exitSignalsCount += 1;
  var preview = {
    index: index,
    time: row.time,
    close: row.close,
    entry: entry.passed,
    exit: exit.passed,
    entryReasons: entry.reasons || [],
    entryValues: entry.values || {},
    exitValues: exit.values || {}
  };
  if (debugState.firstSignalsPreview.length < 10) debugState.firstSignalsPreview.push(preview);
  debugState.lastSignalsPreview.push(preview);
  if (debugState.lastSignalsPreview.length > 10) debugState.lastSignalsPreview.shift();
}

function collectConservativeTrendDiagnostics(debugState, index, row, position, cooldown, warmup, entryResult) {
  var diag = debugState.conservativeTrendDiagnostics;
  var values = entryResult.values || {};
  var conditions = values.conditions || {};
  diag.candlesEvaluated += 1;
  incrementCondition(diag.entryConditionCounts, "emaTrend", conditions.emaTrend);
  incrementCondition(diag.entryConditionCounts, "rsi", conditions.rsi);
  incrementCondition(diag.entryConditionCounts, "volume", conditions.volume);
  incrementCondition(diag.entryConditionCounts, "breakout", conditions.breakout);
  if (cooldown > 0) diag.entryConditionCounts.cooldownBlocked += 1;
  if (position) diag.entryConditionCounts.alreadyInPositionBlocked += 1;
  if (entryResult.passed && index >= warmup && cooldown <= 0 && !position) {
    diag.entryConditionCounts.finalEntriesTriggered += 1;
  }

  var blockedBy = [];
  if (!conditions.emaTrend) blockedBy.push("emaTrend");
  if (!conditions.rsi) blockedBy.push("rsi");
  if (!conditions.breakout) blockedBy.push("breakout");
  if (!conditions.volume) blockedBy.push("volume");
  if (cooldown > 0) blockedBy.push("cooldown");
  if (position) blockedBy.push("alreadyInPosition");

  var preview = {
    time: row.time,
    close: row.close,
    emaFast: values.emaFast,
    emaSlow: values.emaSlow,
    rsi: values.rsi,
    breakoutLevel: values.breakoutLevel,
    volume: values.volume,
    conditions: {
      emaTrend: !!conditions.emaTrend,
      rsi: !!conditions.rsi,
      breakout: !!conditions.breakout,
      volume: !!conditions.volume
    },
    blockedBy: blockedBy
  };
  if (diag.perCandleConditionPreview.first.length < 20) diag.perCandleConditionPreview.first.push(preview);
  diag.perCandleConditionPreview.last.push(preview);
  if (diag.perCandleConditionPreview.last.length > 20) diag.perCandleConditionPreview.last.shift();
  if (blockedBy.length > 0 && blockedBy.length <= 2 && diag.nearMissCandles.length < 12) {
    diag.nearMissCandles.push(preview);
  }
}

function incrementCondition(counts, name, passed) {
  counts[name + (passed ? "Passed" : "Failed")] += 1;
}

function engineBlockReasons(index, warmup, cooldown, entryResult) {
  var reasons = [];
  if (index < warmup) reasons.push("warmup_active");
  if (cooldown > 0) reasons.push("cooldown_active");
  if (!entryResult.passed) reasons = reasons.concat(entryResult.reasons || ["entry_condition_false"]);
  return reasons;
}

function recordSkips(debugState, reasons) {
  debugState.skippedCandlesCount += 1;
  reasons.forEach(function (reason) {
    debugState.skipReasons[reason] = (debugState.skipReasons[reason] || 0) + 1;
  });
}

function finalizeDebugState(debugState) {
  if (debugState.conservativeTrendDiagnostics) {
    finalizeConservativeTrendDiagnostics(debugState.conservativeTrendDiagnostics);
  }
  return debugState;
}

function finalizeConservativeTrendDiagnostics(diag) {
  var counts = diag.entryConditionCounts;
  var blockers = [
    ["emaTrendFailed", counts.emaTrendFailed],
    ["rsiFailed", counts.rsiFailed],
    ["breakoutFailed", counts.breakoutFailed],
    ["volumeFailed", counts.volumeFailed],
    ["cooldownBlocked", counts.cooldownBlocked],
    ["alreadyInPositionBlocked", counts.alreadyInPositionBlocked]
  ].sort(function (a, b) { return b[1] - a[1]; });
  var primary = blockers[0] || ["none", 0];
  diag.primaryBlocker = "Primary blocker: " + primary[0] + " (" + primary[1] + "/" + diag.candlesEvaluated + " candles)";
  if (counts.finalEntriesTriggered === 0) {
    diag.recommendedThresholdChanges = recommendedThresholdChanges(counts, diag.candlesEvaluated);
  }
}

function recommendedThresholdChanges(counts, candles) {
  var recommendations = [];
  if (counts.emaTrendFailed > candles * 0.5) recommendations.push("Relax trend filter: allow close near EMA200 or remove Supertrend from entry confirmation.");
  if (counts.breakoutFailed > candles * 0.5) recommendations.push("Relax breakout/momentum filter: allow MACD histogram rising, not only positive, or make breakout optional.");
  if (counts.rsiFailed > candles * 0.5) recommendations.push("Widen RSI range, for example 35-75, before tightening again.");
  if (counts.volumeFailed > candles * 0.5) recommendations.push("Make volume filter optional or lower volume multiplier.");
  return recommendations;
}

function markersFromTrades(trades) {
  var markers = [];
  trades.forEach(function (trade) {
    markers.push({ time: trade.entry_time, position: "belowBar", color: "#12b886", shape: "arrowUp", text: "BT BUY" });
    markers.push({ time: trade.exit_time, position: "aboveBar", color: "#ff5c7a", shape: "arrowDown", text: "BT SELL" });
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

function profitFactor(wins, losses) {
  var winSum = wins.reduce(function (sum, value) { return sum + value; }, 0);
  var lossSum = Math.abs(losses.reduce(function (sum, value) { return sum + value; }, 0));
  if (!lossSum) return winSum ? winSum : 0;
  return winSum / lossSum;
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

function avg(values) {
  if (!values.length) return 0;
  return values.reduce(function (sum, value) { return sum + value; }, 0) / values.length;
}

function round(value, digits) {
  var factor = Math.pow(10, digits || 4);
  return Math.round((Number(value) || 0) * factor) / factor;
}

module.exports = {
  runBacktest: runBacktest,
  runBacktestOnCandles: runBacktestOnCandles
};
