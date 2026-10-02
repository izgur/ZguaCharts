const assert = require("assert");
const backtest = require("../core/backtest");
const regime = require("../core/regime");

const start = 1700000000;
const candles = Array.from({ length: 650 }, (_, i) => {
  const close = 1000 + i * 3 + Math.sin(i / 8) * 12 - (i >= 350 && i < 420 ? 180 : 0);
  return { time: start + i * 3600, open: close - 2, high: close + 5, low: close - 5, close, volume: 1000 + i };
});
const executionCandles = candles.flatMap((row) => Array.from({ length: 12 }, (_, i) => ({
  ...row, time: row.time + i * 300
})));
const options = {
  symbol: "TESTUSDT", interval: "1h", executionInterval: "5m", strategy: "SimpleAtrTrendV2",
  candles, executionCandles, incremental: true,
  params: { regimeMode: "noRegime", useRsiFilter: false, volumeFilter: false,
    atrMultiplier: 3, emaFast: 20, emaSlow: 50, emaTrend: 100,
    fillModel: "next-open", riskPct: 0.02, maxPositionPct: 10, maxDailyLossPct: 2,
    takerFeePct: 0.055, slippageBps: 2, trailingActivationR: 1, trailingAtrMultiplier: 3.7 },
  entryFilter: ({ row }) => [250, 450, 649].some((i) => row.time === candles[i].time)
};
const full = backtest.runBacktestOnCandles(options);
assert(full.tradeList.length > 0, "fixture must exercise filled positions");
const pending = backtest.runBacktestOnCandles({ ...options, entryFilter: ({ row }) => row.time === candles[649].time });
assert(pending.checkpoint.state.pendingEntry, "final-bar signal must remain pending");
let resumeState;
const trades = [];
for (const count of [251, 290, 351, 420, 500, 650]) {
  const end = candles[count - 1].time + 3600 - 300;
  const result = backtest.runBacktestOnCandles({ ...options,
    candles: candles.slice(0, count), executionCandles: executionCandles.filter((row) => row.time <= end), resumeState });
  assert.strictEqual(result.processedSignalCandles, count - (resumeState ? resumeState.lastSignalIndex + 1 : 0));
  trades.push(...result.tradeList.filter((trade) => trade.exitReason !== "End of data"));
  resumeState = JSON.parse(JSON.stringify(result.checkpoint));
}
// Apply the remaining execution tail without reprocessing a signal candle.
const tail = backtest.runBacktestOnCandles({ ...options, resumeState });
trades.push(...tail.tradeList.filter((trade) => trade.exitReason !== "End of data"));
assert.strictEqual(tail.processedSignalCandles, 0);
assert.deepStrictEqual(tail.checkpoint.state, full.checkpoint.state, "restart must preserve all engine accounting and pending orders");
assert.deepStrictEqual(trades, full.tradeList.filter((trade) => trade.exitReason !== "End of data"));
const repeated = backtest.runBacktestOnCandles({ ...options, resumeState: tail.checkpoint });
assert.strictEqual(repeated.processedSignalCandles, 0);
assert.deepStrictEqual(repeated.checkpoint.state, tail.checkpoint.state, "same candles must not change cash or state");
assert.throws(() => backtest.runBacktestOnCandles({ ...options, resumeState: tail.checkpoint,
  params: { ...options.params, slippageBps: 5 } }), /configuration changed/);
assert.throws(() => backtest.runBacktestOnCandles({ ...options, resumeState: tail.checkpoint,
  candles: candles.slice(0, 500) }), /checkpoint candle is missing/);
const frameCache = new Map();
backtest.runBacktestOnCandles({ ...options, frameCache });
backtest.runBacktestOnCandles({ ...options, frameCache, params: { ...options.params, riskPct: 0.0175 } });
assert.strictEqual(frameCache.size, 1, "risk variants should share their indicator frame");

const firstBarRows = executionCandles.map((row) => row.time === candles[251].time ? { ...row, low: 0 } : row);
const firstBar = backtest.runBacktestOnCandles({ ...options,
  executionCandles: firstBarRows, entryFilter: ({ row }) => row.time === candles[250].time });
assert.strictEqual(firstBar.tradeList[0].exitFillTime, candles[251].time,
  "hard stops must include the first execution candle after entry");

const spikeCandles = candles.map((row, i) => i === 251 ? { ...row, close: row.close + 250, high: row.high + 250 } : row);
const spikeExecution = executionCandles.map((row) => row.time === candles[251].time + 3300
  ? { ...row, open: row.open + 250, high: row.high + 250, low: row.low + 250, close: row.close + 250 } : row);
const spike = backtest.runBacktestOnCandles({ ...options, candles: spikeCandles, executionCandles: spikeExecution,
  entryFilter: ({ row }) => row.time === candles[250].time });
assert.strictEqual(spike.tradeList[0].exitFillTime, candles[252].time,
  "a stop tightened at signal close cannot retroactively stop an earlier execution candle");

const mapped = regime.mapRegimeToCandles(candles.slice(0, 5), [candles[0]], [], { interval: "1h" });
assert.strictEqual(mapped[0].btcRegimeTime, null, "1h candle cannot read the unfinished 4h close");
assert.strictEqual(mapped[2].btcRegimeTime, null);
assert.strictEqual(mapped[3].btcRegimeTime, candles[0].time, "4h context becomes available at its close");
console.log("incremental paper parity tests passed");
