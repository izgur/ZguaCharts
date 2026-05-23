const assert = require("assert");
const indicators = require("../core/indicators");
const regime = require("../core/regime");
const backtest = require("../core/backtest");
const optimizer = require("../core/optimizer");
const tradeAudit = require("../core/backtest/tradeAudit");

function candles(count, start, stepSeconds, base, slope) {
  const rows = [];
  for (let i = 0; i < count; i += 1) {
    const close = base + i * (slope || 12) + Math.sin(i / 7) * 20;
    rows.push({
      time: start + i * stepSeconds,
      open: close - 5,
      high: close + 5,
      low: close - 20,
      close,
      volume: 1000 + i * 3,
    });
  }
  return rows;
}

const sample = candles(140, 1700000000, 3600, 1000);
const ema = indicators.ema([1, 2, 3, 4, 5], 3);
assert.strictEqual(ema.length, 5);
assert(Math.abs(ema[4] - 4.0625) < 1e-8);

const atr = indicators.atr(sample.slice(0, 20), 14);
assert.strictEqual(atr.length, 20);
assert(atr[19] > 0);

const adx = indicators.adx(sample, 14);
assert.strictEqual(adx.adx.length, sample.length);
assert(adx.adx[80] >= 0);

const dc = indicators.donchian(sample, 20);
assert.strictEqual(dc.high[18], null);
assert(dc.high[19] >= sample[19].high);
assert(dc.low[19] <= sample[0].low);

const dc55 = indicators.donchian(sample, 55);
const entryIndex = 55;
const previousEntryHigh55 = dc55.high[entryIndex - 1];
const expectedPreviousEntryHigh55 = Math.max(...sample.slice(entryIndex - 55, entryIndex).map((c) => c.high));
assert.strictEqual(previousEntryHigh55, expectedPreviousEntryHigh55);
const currentIncludedHigh55 = dc55.high[entryIndex];
assert(currentIncludedHigh55 >= previousEntryHigh55);
assert(!sample.slice(entryIndex - 55, entryIndex).includes(sample[entryIndex]));

const volSma = indicators.sma(sample.map((c) => c.volume), 20);
assert.strictEqual(volSma[18], null);
assert(volSma[19] > 0);

const regimeCandles = candles(260, 1699900000, 14400, 20000);
const mapped = regime.mapRegimeToCandles(sample, regimeCandles);
assert(mapped.every((row) => row.btcRegimeTime === null || row.btcRegimeTime <= row.time));

const tradingSample = candles(500, 1700000000, 3600, 25000, 60);
const result = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "1h",
  strategy: "RegimeFilteredTrendStrategy",
  candles: tradingSample,
  regimeCandles,
  params: {
    donchianEntry: 20,
    donchianExit: 10,
    adxThreshold: 10,
    atrMultiplier: 2,
    emaTrendLength: 100,
    volumeFilter: false,
    maxNotional: 1000000,
  },
});
assert(result.trades > 0, "RegimeFilteredTrendStrategy should produce synthetic trades");
assert(result.equityCurve.length > 0);
assert(result.diagnostics.primaryBlocker);
assert.strictEqual(result.diagnostics.breakoutSummary.usesPreviousChannel, true);
assert(tradeAudit.auditTrades(result, tradingSample).ok);

["RegimeDonchian20", "RegimeDonchianCloseConfirm", "RegimePullbackTrend"].forEach((strategy) => {
  const variant = backtest.runBacktestOnCandles({
    symbol: "BTCUSDT",
    interval: "1h",
    strategy,
    candles: tradingSample,
    regimeCandles,
    params: {
      adxThreshold: 10,
      atrMultiplier: 2,
      emaTrendLength: 100,
      volumeFilter: false,
      maxNotional: 1000000,
    },
  });
  assert(variant.equityCurve.length > 0, `${strategy} should return an equity curve`);
  assert(variant.diagnostics.breakoutSummary, `${strategy} should return breakout diagnostics`);
});

optimizer.optimizeRegimeStaged({
  symbol: "BTCUSDT",
  interval: "1h",
  strategy: "RegimeFilteredTrendStrategy",
  candles: candles(420, 1700000000, 3600, 25000, 60),
  regimeCandles,
  maxCombos: 20,
  progressEvery: 9999,
  outputDir: "reports-smoke",
}).then((summary) => {
  assert(summary.combinationsTested.stage1 > 0);
  assert(summary.combinationsTested.stage2 <= 20);
  console.log("regime strategy smoke tests passed");
}).catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
