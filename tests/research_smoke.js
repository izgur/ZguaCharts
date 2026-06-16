const assert = require("assert");
const backtest = require("../core/backtest");
const indicators = require("../core/indicators");
const tradeAudit = require("../core/backtest/tradeAudit");
const optimizer = require("../core/optimizer");
const reporting = require("../core/reporting");

function syntheticCandles(count) {
  const candles = [];
  let price = 100;
  for (let i = 0; i < count; i += 1) {
    price += Math.sin(i / 12) * 0.4 + 0.08;
    candles.push({
      time: 1700000000 + i * 3600,
      open: price - 0.2,
      high: price + 0.8,
      low: price - 0.8,
      close: price,
      volume: 1000 + (i % 20) * 20
    });
  }
  return candles;
}

const result = backtest.runBacktestOnCandles({
  symbol: "TEST",
  interval: "1h",
  strategy: "ConservativeTrend",
  candles: syntheticCandles(600)
});

assert.strictEqual(typeof result.totalReturn, "number");
assert.strictEqual(Array.isArray(result.equityCurve), true);
assert.strictEqual(Array.isArray(result.tradeList), true);
assert.ok(result.diagnostics.candlesLoaded === 600);

const alwaysLong = backtest.runBacktestOnCandles({
  symbol: "TEST",
  interval: "1h",
  strategy: "AlwaysLongTest",
  candles: syntheticCandles(160),
  debug: true
});

assert.ok(alwaysLong.trades > 0, "AlwaysLongTest must prove the engine can produce trades");
assert.ok(alwaysLong.tradeList.length > 0, "AlwaysLongTest tradeList must not be empty");
assert.ok(alwaysLong.equityCurve.length > 0, "AlwaysLongTest equityCurve must not be empty");
assert.ok(alwaysLong.diagnostics.debug.entrySignalsCount > 0, "AlwaysLongTest should expose entry signals in debug mode");
const alwaysAudit = tradeAudit.auditTrades(alwaysLong, syntheticCandles(160));
assert.ok(alwaysAudit.ok, "AlwaysLongTest lifecycle audit must pass: " + alwaysAudit.errors.join("; "));

const featureFrame = indicators.buildIndicatorFrame(syntheticCandles(220), {
  swingLeft: 2,
  swingRight: 2,
  goldenPocketTolerancePct: 1
});
const featureRows = featureFrame.filter((row) => row.lastSwingHigh && row.lastSwingLow);
assert.ok(featureRows.length > 0, "indicator frame should expose confirmed swing context");
assert.ok(featureRows.some((row) => row.fib618 !== null), "indicator frame should expose fibonacci retracement levels");
assert.ok(featureRows.some((row) => row.fibExt1618 !== null), "indicator frame should expose fibonacci extension levels");
assert.ok(featureRows.some((row) => row.pivot !== null), "indicator frame should expose pivot levels");
assert.ok(featureRows.some((row) => row.anchoredVwapFromSwingLow !== null), "indicator frame should expose anchored VWAP from swing low");
assert.ok(featureRows.some((row) => ["up", "down", "unknown"].includes(row.marketStructureTrend)), "indicator frame should expose market structure flags");

const fibResult = backtest.runBacktestOnCandles({
  symbol: "TEST",
  interval: "15m",
  strategy: "FibPullbackContinuationV1",
  candles: syntheticCandles(900),
  params: {
    regimeMode: "symbolFastTrend",
    swingLeft: 2,
    swingRight: 2,
    goldenPocketTolerancePct: 1,
    rsiPullbackLevel: 48,
    rsiReclaimLevel: 50,
    requireAnchoredVwap: false,
    fillModel: "next-open"
  },
  debug: true
});
assert.strictEqual(fibResult.strategy, "FibPullbackContinuationV1");
assert.strictEqual(fibResult.interval, "15m");
assert.ok(fibResult.diagnostics.blockerCounts.fibPullbackFailed >= 0, "Fib strategy diagnostics should include fib blockers");

const loose = backtest.runBacktestOnCandles({
  symbol: "TEST",
  interval: "1h",
  strategy: "ConservativeTrendLoose",
  candles: syntheticCandles(700),
  debug: true
});
const looseAudit = tradeAudit.auditTrades(loose, syntheticCandles(700));
assert.ok(looseAudit.ok, "ConservativeTrendLoose lifecycle audit must pass: " + looseAudit.errors.join("; "));

const combos = optimizer.expandGrid({ emaFast: [10, 20], emaSlow: [50], rsiMin: [45, 50] });
assert.strictEqual(combos.length, 4);
const grids = optimizer.availableOptimizerGrids();
assert.ok(grids.some((grid) => grid.gridName === "V2 ATR trend"), "optimizer should expose strategy-specific grids");
const v2Grid = optimizer.selectOptimizerGrid("SimpleAtrTrendV2", null, 25);
assert.strictEqual(v2Grid.metadata.gridName, "V2 ATR trend");
assert.ok(v2Grid.metadata.candidateCountTested <= 25, "optimizer grid must respect max combo limit");
const fibGrid = optimizer.selectOptimizerGrid("FibPullbackContinuationV1", null, 20);
assert.strictEqual(fibGrid.metadata.gridName, "Fib pullback continuation 15m");
assert.strictEqual(fibGrid.metadata.timeframeHint, "15m");
assert.strictEqual(fibGrid.metadata.acceptanceNotes.minFullTrades, 100);
assert.ok(fibGrid.combos.every((combo) => combo.fillModel === "next-open"), "Fib grid should use next-open fills");
const fallbackGrid = optimizer.selectOptimizerGrid("AlwaysLongTest", null, 5);
assert.strictEqual(fallbackGrid.metadata.gridName, "Default fallback");
const qualityPolicy = optimizer.optimizerQualityPolicy();
assert.strictEqual(qualityPolicy.minTestTrades, 10);
const zeroQuality = optimizer.evaluateCandidateQuality({
  train: { totalReturn: 0, maxDrawdown: 0, profitFactor: 0, trades: 0, sharpeRatio: 0 },
  test: { totalReturn: 0, maxDrawdown: 0, profitFactor: 0, trades: 0, sharpeRatio: 0 },
  zeroTradeDiagnostics: { summary: { likelyReason: "no_entry_signal" } }
});
assert.strictEqual(zeroQuality.qualityStatus, "FAIL");
assert.ok(zeroQuality.rejectionReasons.some((reason) => reason.code === "zero_trades"));
const passQuality = optimizer.evaluateCandidateQuality({
  train: { totalReturn: 5, maxDrawdown: 3, profitFactor: 1.3, trades: 40, sharpeRatio: 1 },
  test: { totalReturn: 3, maxDrawdown: 2, profitFactor: 1.2, trades: 13, sharpeRatio: 1 },
  full: { totalReturn: 8, maxDrawdown: 4, profitFactor: 1.2, trades: 53, sharpeRatio: 1 }
});
assert.strictEqual(passQuality.qualityStatus, "PASS");
const weakQuality = optimizer.evaluateCandidateQuality({
  train: { totalReturn: -14.2474, maxDrawdown: 6, profitFactor: 0.8, trades: 28, sharpeRatio: -0.5 },
  test: { totalReturn: 8.0155, maxDrawdown: 3, profitFactor: 3.6311, trades: 11, sharpeRatio: 1 },
  full: { totalReturn: -6.2319, maxDrawdown: 8, profitFactor: 1.2, trades: 39, sharpeRatio: 0.1 }
});
assert.strictEqual(weakQuality.qualityStatus, "FAIL");
assert.ok(weakQuality.rejectionReasons.some((reason) => reason.code === "negative_full_return"));
assert.ok(weakQuality.rejectionReasons.some((reason) => reason.code === "strongly_negative_train_return"));
assert.ok(weakQuality.warnings.some((reason) => reason.code === "train_test_direction_mismatch"));
assert.ok(weakQuality.warnings.some((reason) => reason.code === "low_test_trade_evidence"));

const csv = reporting.toCsv([
  {
    symbol: "TEST",
    interval: "1h",
    strategy: "ConservativeTrend",
    params: { emaFast: 10 },
    totalReturn: 1,
    maxDrawdown: 2,
    profitFactor: 3,
    winRate: 4,
    trades: 5,
    sharpeRatio: 6
  }
]);
assert.ok(csv.indexOf("symbol,interval,strategy,params,totalReturn") === 0);

console.log("research smoke tests passed");
