const assert = require("assert");
const indicators = require("../core/indicators");
const regime = require("../core/regime");
const backtest = require("../core/backtest");
const optimizer = require("../core/optimizer");
const tradeAudit = require("../core/backtest/tradeAudit");
const regimeTrend = require("../core/backtest/regimeTrend");

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

const chop = indicators.choppiness(sample, 14);
assert.strictEqual(chop[12], null);
assert(chop[30] >= 0 && chop[30] <= 100);

const kc = indicators.keltner(sample, 20, 1.5);
assert(kc.upper[40] > kc.middle[40]);
assert(kc.lower[40] < kc.middle[40]);

const obv = indicators.obv(sample);
assert.strictEqual(obv.length, sample.length);
assert(obv[30] > obv[0]);

const cmf = indicators.cmf(sample, 20);
assert.strictEqual(cmf[18], null);
assert(cmf[40] >= -1 && cmf[40] <= 1);

const volRoc = indicators.volumeRoc(sample, 20);
assert.strictEqual(volRoc[19], null);
assert(volRoc[40] > 0);

const stoch = indicators.stochRsi(sample.map((c) => c.close), 14, 14, 3, 3);
assert.strictEqual(stoch.raw.length, sample.length);
assert(stoch.raw[40] >= 0 && stoch.raw[40] <= 100);

const indicatorFrame = indicators.buildIndicatorFrame(sample, {});
assert(indicatorFrame[120].choppiness14 >= 0 && indicatorFrame[120].choppiness14 <= 100);
assert(indicatorFrame[120].keltnerUpper > indicatorFrame[120].keltnerMiddle);
assert(indicatorFrame[120].squeezeRatio === null || indicatorFrame[120].squeezeRatio >= 0);
assert(Number.isFinite(indicatorFrame[120].obv));
assert(indicatorFrame[120].cmf20 >= -1 && indicatorFrame[120].cmf20 <= 1);
assert(indicatorFrame[120].atrPctRank100 >= 0 && indicatorFrame[120].atrPctRank100 <= 100);

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
result.tradeList.forEach((trade) => {
  assert(Math.abs(Number(trade.netPnl) - (Number(trade.grossPnl) - Number(trade.feePaid) - Number(trade.slippagePaid))) < 0.02, "net PnL should include entry/exit fees and slippage");
});

const capped = backtest.runBacktestOnCandles({
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
    riskPct: 0.5,
    maxPositionPct: 10,
    maxNotional: 1000000,
  },
});
assert(capped.tradeList.every((trade) => Number(trade.notional) <= 1000.01), "maxPositionPct should cap notional exposure");
assert.strictEqual(
  regimeTrend.testHooks.exitBasePrice({ open: 90 }, 100, "long", { fillModel: "next-open" }, "soft-execution-exit"),
  100,
  "soft execution exits must fill at the known execution close, not the earlier candle open"
);
assert.strictEqual(regimeTrend.testHooks.shouldDeferEntry({ fillModel: "next-open" }, 9, 10), true, "final-bar entries must wait for a real next open");
assert.strictEqual(regimeTrend.testHooks.shouldDeferExit({ kind: "close-signal" }, { fillModel: "next-open" }, 9, 10), true, "final-bar rule exits must wait for a real next open");

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

const robust = backtest.runBacktestOnCandles({
  symbol: "ETHUSDT",
  interval: "1h",
  strategy: "RobustTrendFollowerV1",
  candles: tradingSample.map((row, index) => ({ ...row, volume: 1000 + index * 25 })),
  regimeCandles,
  params: {
    donchianEntry: 20,
    donchianExit: 20,
    adxThreshold: 5,
    atrMultiplier: 2,
    volumeMultiplier: 1,
    volumeFilter: true,
    maxNotional: 1000000,
  },
});
assert(robust.equityCurve.length > 0, "RobustTrendFollowerV1 should return an equity curve");
assert(robust.trades > 0, "RobustTrendFollowerV1 should produce synthetic long-only trades");
assert.strictEqual(robust.params.shortMode, false);
assert(robust.diagnostics.breakoutSummary, "RobustTrendFollowerV1 should return breakout diagnostics");

const mtfSample = candles(520, 1700000000, 900, 25000, 12);
const mtfResult = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "15m",
  strategy: "MtfPullback15mV1",
  candles: mtfSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    mtfRequire1h: true,
    mtfRequire4h: false,
    mtf1hTrendEma: 50,
    mtfMinRsi: 40,
    mtfEntryMode: "breakout",
    donchianEntry: 10,
    donchianExit: 10,
    atrMultiplier: 2,
    takeProfitAtr: 3,
    indicatorFilterPreset: "none",
    maxNotional: 1000000,
  },
});
assert(mtfResult.equityCurve.length > 0, "MtfPullback15mV1 should return an equity curve");
assert(mtfResult.trades > 0, "MtfPullback15mV1 should produce synthetic trades when 1h trend confirms");

const mtfBlocked = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "15m",
  strategy: "MtfPullback15mV1",
  candles: mtfSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    mtfRequire1h: true,
    mtfRequire4h: false,
    mtf1hTrendEma: 50,
    mtfMinRsi: 101,
    mtfEntryMode: "breakout",
    donchianEntry: 10,
    donchianExit: 10,
    atrMultiplier: 2,
    indicatorFilterPreset: "none",
    maxNotional: 1000000,
  },
});
assert.strictEqual(mtfBlocked.trades, 0, "impossible MTF RSI should block MtfPullback15mV1 entries");
assert(mtfBlocked.diagnostics.blockerCounts.mtfTrendBlocked > 0, "MTF trend blocker should be diagnosed");

const mtfShortSample = candles(520, 1700000000, 900, 32000, -12).map((row) => ({
  ...row,
  open: row.close + 5,
  high: row.close + 20,
  low: row.close - 5,
}));
const mtfShortResult = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "15m",
  strategy: "MtfPullback15mV1",
  candles: mtfShortSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    mtfDirection: "short",
    mtfRequire1h: true,
    mtfRequire4h: false,
    mtf1hTrendEma: 50,
    mtfMinRsi: 40,
    mtfEntryMode: "breakout",
    donchianEntry: 10,
    donchianExit: 10,
    atrMultiplier: 2,
    takeProfitAtr: 3,
    indicatorFilterPreset: "none",
    maxNotional: 1000000,
  },
});
assert(mtfShortResult.trades > 0, "MtfPullback15mV1 should produce synthetic short trades when 1h trend confirms down");
assert(mtfShortResult.tradeList.some((trade) => trade.side === "short"), "MtfPullback15mV1 should record short-side trades");

const reversionSample = candles(520, 1700000000, 900, 25000, 0).map((row, index) => {
  const close = 25000 + Math.sin(index / 5) * 120 + Math.sin(index / 17) * 40;
  return {
    ...row,
    open: close + Math.sin(index / 3) * 20,
    high: close + 35,
    low: close - 35,
    close,
    volume: 1000 + (index % 30) * 10,
  };
});
const reversionResult = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "15m",
  strategy: "VwapReversion15mV1",
  candles: reversionSample,
  regimeCandles,
  params: {
    reversionDirection: "both",
    minVwapDistanceAtr: 0.2,
    requireBbTouch: false,
    rsiOversold: 55,
    rsiOverbought: 45,
    stochRsiOversold: 55,
    stochRsiOverbought: 45,
    maxAdx: 100,
    minChoppiness: 0,
    atrMultiplier: 1.5,
    takeProfitAtr: 1.5,
    cooldownBars: 0,
    minHoldBars: 0,
    maxNotional: 1000000,
  },
});
assert(reversionResult.trades > 0, "VwapReversion15mV1 should produce synthetic range-reversion trades");
assert(reversionResult.tradeList.some((trade) => trade.exitReason === "VWAP reversion exit" || trade.exitReason === "ATR take profit"), "VwapReversion15mV1 should use reversion/take-profit exits");

const fastReversionResult = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "15m",
  strategy: "VwapReversion15mFastV1",
  candles: reversionSample,
  regimeCandles,
  params: {
    reversionDirection: "both",
    minVwapDistanceAtr: 0.1,
    requireBbTouch: false,
    rsiOversold: 55,
    rsiOverbought: 45,
    stochRsiOversold: 55,
    stochRsiOverbought: 45,
    maxAdx: 100,
    minChoppiness: 0,
    atrMultiplier: 1,
    takeProfitAtr: 1,
    cooldownBars: 0,
    minHoldBars: 0,
    maxNotional: 1000000,
  },
});
assert(fastReversionResult.trades >= reversionResult.trades, "VwapReversion15mFastV1 should be at least as active as the strict reversion variant on synthetic ranges");

const choppinessBlocked = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "1h",
  strategy: "SimpleAtrTrendV2",
  candles: tradingSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    useRsiFilter: false,
    atrMultiplier: 2,
    emaFast: 20,
    emaSlow: 50,
    emaTrend: 100,
    volumeFilter: false,
    useChoppinessFilter: true,
    maxChoppiness: 0,
    maxNotional: 1000000,
  },
});
assert(choppinessBlocked.diagnostics.blockerCounts.marketTooChoppy > 0, "choppiness filter should be diagnosed");

const volumeFlowBlocked = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "1h",
  strategy: "SimpleAtrTrendV2",
  candles: tradingSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    useRsiFilter: false,
    atrMultiplier: 2,
    emaFast: 20,
    emaSlow: 50,
    emaTrend: 100,
    volumeFilter: false,
    useVolumeFlowFilter: true,
    minCmf: 2,
    requireObvAboveMa: false,
    maxNotional: 1000000,
  },
});
assert(volumeFlowBlocked.diagnostics.blockerCounts.volumeFlowWeak > 0, "volume-flow filter should be diagnosed");

const volatilityBlocked = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "1h",
  strategy: "SimpleAtrTrendV2",
  candles: tradingSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    useRsiFilter: false,
    atrMultiplier: 2,
    emaFast: 20,
    emaSlow: 50,
    emaTrend: 100,
    volumeFilter: false,
    useAtrPercentileFilter: true,
    minAtrPctRank: 101,
    maxAtrPctRank: 101,
    maxNotional: 1000000,
  },
});
assert(volatilityBlocked.diagnostics.blockerCounts.volatilityRegimeBlocked > 0, "ATR percentile filter should be diagnosed");

const causalBaseline = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "1h",
  strategy: "SimpleAtrTrendV2",
  candles: tradingSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    useRsiFilter: false,
    atrMultiplier: 2,
    emaFast: 20,
    emaSlow: 50,
    emaTrend: 100,
    volumeFilter: false,
    maxNotional: 1000000,
  },
});
const causalBullishOnly = backtest.runBacktestOnCandles({
  symbol: "BTCUSDT",
  interval: "1h",
  strategy: "SimpleAtrTrendV2",
  candles: tradingSample,
  regimeCandles,
  params: {
    regimeMode: "noRegime",
    useRsiFilter: false,
    atrMultiplier: 2,
    emaFast: 20,
    emaSlow: 50,
    emaTrend: 100,
    volumeFilter: false,
    maxNotional: 1000000,
    causalRegimeFilter: "bullishOnly",
  },
});
assert(causalBaseline.trades > 0, "causal regime filter test needs a live baseline entry");
assert(causalBullishOnly.trades <= causalBaseline.trades, "causal bullish-only filter should not add trades");
assert(causalBullishOnly.diagnostics.blockerCounts.causalRegimeFilterBlocked > 0, "causal regime filter blocker should be diagnosed");

const robustFundingBlocked = backtest.runBacktestOnCandles({
  symbol: "ETHUSDT",
  interval: "1h",
  strategy: "RobustTrendFollowerV1",
  candles: tradingSample.map((row, index) => ({
    ...row,
    volume: 1000 + index * 25,
    fundingRate: 0.001,
    openInterest: 100000 + index * 1000,
    openInterestChangePct: 1,
  })),
  regimeCandles,
  params: {
    donchianEntry: 20,
    donchianExit: 20,
    adxThreshold: 5,
    atrMultiplier: 2,
    volumeMultiplier: 1,
    volumeFilter: true,
    useFundingFilter: true,
    maxFundingRate: 0.0001,
    useOpenInterestFilter: false,
    maxNotional: 1000000,
  },
});
assert.strictEqual(robustFundingBlocked.trades, 0, "funding filter should be able to block RobustTrendFollowerV1 entries");
assert(robustFundingBlocked.diagnostics.blockerCounts.fundingTooHigh > 0, "funding blocker should be diagnosed");

const robustOiBlocked = backtest.runBacktestOnCandles({
  symbol: "ETHUSDT",
  interval: "1h",
  strategy: "RobustTrendFollowerV1",
  candles: tradingSample.map((row, index) => ({
    ...row,
    volume: 1000 + index * 25,
    fundingRate: 0,
    openInterest: 100000 - index * 1000,
    openInterestChangePct: -1,
  })),
  regimeCandles,
  params: {
    donchianEntry: 20,
    donchianExit: 20,
    adxThreshold: 5,
    atrMultiplier: 2,
    volumeMultiplier: 1,
    volumeFilter: true,
    useFundingFilter: false,
    useOpenInterestFilter: true,
    minOpenInterestChangePct: 0,
    maxNotional: 1000000,
  },
});
assert.strictEqual(robustOiBlocked.trades, 0, "open-interest filter should be able to block RobustTrendFollowerV1 entries");
assert(robustOiBlocked.diagnostics.blockerCounts.openInterestNotRising > 0, "open-interest blocker should be diagnosed");

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
