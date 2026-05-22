const assert = require("assert");
const backtest = require("../core/backtest");
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
