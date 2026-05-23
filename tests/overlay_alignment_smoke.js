const assert = require("assert");
const data = require("../core/data");
const backtest = require("../core/backtest");

Promise.all([
  data.fetchCandles({
    source: "bybit",
    symbol: "BTCUSDT",
    interval: "1h",
    from: new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString(),
    to: new Date().toISOString(),
    limit: 9000,
  }),
  data.fetchCandles({
    source: "bybit",
    symbol: "BTCUSDT",
    interval: "4h",
    from: new Date(Date.now() - 365 * 24 * 60 * 60 * 1000).toISOString(),
    to: new Date().toISOString(),
    limit: 2500,
  }),
]).then(([candles, regimeCandles]) => {
  const result = backtest.runBacktestOnCandles({
    symbol: "BTCUSDT",
    interval: "1h",
    strategy: "RegimeFilteredTrendStrategy",
    candles,
    regimeCandles,
    params: {},
  });
  assert(candles.length > 8000, `expected >8000 candles, got ${candles.length}`);
  const ema200 = result.overlays.find((item) => item.name === "EMA 200");
  assert(ema200, "EMA 200 overlay missing");
  assert.strictEqual(ema200.data.length, candles.length);
  const firstNonNullIndex = ema200.data.findIndex((point) => point.value !== null && point.value !== undefined);
  assert(firstNonNullIndex >= 198 && firstNonNullIndex <= 205, `EMA200 starts at unexpected index ${firstNonNullIndex}`);
  assert.strictEqual(ema200.data[ema200.data.length - 1].time, candles[candles.length - 1].time);
  assert.strictEqual(result.overlayDiagnostics.lastOverlayTime, candles[candles.length - 1].time);
  console.log("overlay alignment smoke passed", JSON.stringify({
    candles: candles.length,
    firstNonNullEma200Index: firstNonNullIndex,
    lastTime: candles[candles.length - 1].time,
  }));
}).catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
