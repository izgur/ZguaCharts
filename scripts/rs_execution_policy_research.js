const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");

function arg(name, fallback) {
  const index = process.argv.indexOf("--" + name);
  return index >= 0 && process.argv[index + 1] !== undefined ? process.argv[index + 1] : fallback;
}

function seconds(timeframe) {
  const value = Number(String(timeframe).slice(0, -1));
  if (String(timeframe).endsWith("m")) return value * 60;
  if (String(timeframe).endsWith("h")) return value * 3600;
  return value * 86400;
}

function limit(days, timeframe) {
  return Math.min(50000, Math.ceil(days * 86400 / seconds(timeframe)) + 300);
}

function round(value, digits = 4) {
  const factor = Math.pow(10, digits);
  return Math.round(Number(value || 0) * factor) / factor;
}

function alignBasket(series) {
  const symbols = Object.keys(series);
  const maps = Object.fromEntries(symbols.map((symbol) => [symbol, new Map(series[symbol].map((row) => [Number(row.time), row]))]));
  const times = Array.from(maps[symbols[0]].keys()).filter((time) => symbols.every((symbol) => maps[symbol].has(time))).sort((a, b) => a - b);
  if (!times.length) return [];
  const first = Object.fromEntries(symbols.map((symbol) => [symbol, Number(maps[symbol].get(times[0]).close) || 1]));
  return times.map((time) => {
    const close = symbols.reduce((sum, symbol) => sum + Number(maps[symbol].get(time).close) / first[symbol], 0) / symbols.length;
    return { time, open: close, high: close, low: close, close, volume: 0 };
  });
}

async function basket(source, timeframe, candleLimit) {
  const series = {};
  for (const symbol of ["ETHUSDT", "SOLUSDT", "BNBUSDT"]) {
    series[symbol] = await data.fetchCandles({ source, symbol, interval: timeframe, limit: candleLimit });
  }
  return alignBasket(series);
}

function tradeMetrics(trades, accountEquity = 10000) {
  let equity = accountEquity;
  let peak = accountEquity;
  let maxDrawdown = 0;
  let grossWin = 0;
  let grossLoss = 0;
  trades.forEach((trade) => {
    const pnl = Number(trade.netPnl ?? trade.pnl ?? 0);
    const value = pnl / accountEquity * 100;
    equity += pnl;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak ? (peak - equity) / peak * 100 : 0);
    if (value > 0) grossWin += value;
    if (value < 0) grossLoss += Math.abs(value);
  });
  return {
    trades: trades.length,
    returnPct: round((equity / accountEquity - 1) * 100),
    winRate: round(trades.length ? trades.filter((trade) => Number(trade.returnPct) > 0).length / trades.length * 100 : 0),
    profitFactor: round(grossLoss ? grossWin / grossLoss : (grossWin ? 999 : 0)),
    maxDrawdownPct: round(maxDrawdown),
    averageTradePct: round(trades.length ? trades.reduce((sum, trade) => sum + Number(trade.returnPct || 0), 0) / trades.length : 0)
  };
}

function regretMetrics(trades, baselineTrades) {
  const baselineByEntry = new Map(baselineTrades.map((trade) => [Number(trade.entrySignalTime), trade]));
  const comparable = trades.filter((trade) => baselineByEntry.has(Number(trade.entrySignalTime)));
  const deltas = comparable.map((trade) => Number(trade.returnPct || 0) - Number(baselineByEntry.get(Number(trade.entrySignalTime)).returnPct || 0));
  return {
    comparableTrades: comparable.length,
    improvedExits: deltas.filter((value) => value > 0).length,
    earlyExitRegrets: deltas.filter((value) => value < 0).length,
    averageReturnDeltaPct: round(deltas.length ? deltas.reduce((sum, value) => sum + value, 0) / deltas.length : 0)
  };
}

const candidates = [
  {
    id: "rs-btc4h-lb42",
    timeframe: "4h",
    params: { atrMultiplier: 2.7, causalRegimeFilter: "bullishOnly", indicatorFilterPreset: "trendQuality", regimeMode: "looseBtcBull", riskPct: 0.03, rsBenchmarkSymbol: "CRYPTO_BASKET", rsLookback: 42, rsThreshold: 0, rsiMax: 72, rsiMin: 61, volumeFilter: false }
  },
  {
    id: "rs-btc1h-lb156",
    timeframe: "1h",
    params: { atrMultiplier: 2.9, causalRegimeFilter: "bullishOnly", indicatorFilterPreset: "trendQuality", regimeMode: "looseBtcBull", riskPct: 0.02, rsBenchmarkSymbol: "CRYPTO_BASKET", rsLookback: 156, rsThreshold: -0.02, rsiMax: 72, rsiMin: 57, volumeFilter: false }
  }
];

function policiesFor(candidate) {
  const hard = [
    { id: "confirmed-htf", executionTimeframe: candidate.timeframe },
    { id: "5m-hard", executionTimeframe: "5m" },
    { id: "15m-hard", executionTimeframe: "15m" }
  ];
  if (candidate.timeframe === "4h") hard.push({ id: "30m-hard", executionTimeframe: "30m" });
  return hard.concat([
    { id: "5m-fixed-stop", executionTimeframe: "5m", stop: { trailingActivationR: 999 } },
    { id: "5m-trail-1r", executionTimeframe: "5m", stop: { trailingActivationR: 1 } },
    { id: "5m-trail-1r-be1", executionTimeframe: "5m", stop: { trailingActivationR: 1, breakEvenAtR: 1 } },
    { id: "5m-trail-1.5r-be1", executionTimeframe: "5m", stop: { trailingActivationR: 1.5, breakEvenAtR: 1 } },
    { id: "5m-wide-trail-1r", executionTimeframe: "5m", stop: { trailingActivationR: 1, trailingAtrMultiplier: candidate.params.atrMultiplier + 0.8 } },
    { id: "5m-ema2", executionTimeframe: "5m", soft: "ema50-two-close" },
    { id: "5m-donchian2", executionTimeframe: "5m", soft: "donchian-two-close" },
    { id: "5m-either2", executionTimeframe: "5m", soft: "ema-or-donchian-two-close" }
  ]);
}

async function main() {
  const source = arg("source", "bybit");
  const days = Number(arg("days", "170"));
  const outputDir = arg("output", "reports/research-campaigns");
  const tag = arg("tag", new Date().toISOString().slice(0, 10).replace(/-/g, ""));
  const executionCache = {};
  for (const timeframe of ["5m", "15m", "30m"]) {
    executionCache[timeframe] = await data.fetchCandles({ source, symbol: "BTCUSDT", interval: timeframe, limit: limit(days, timeframe) });
  }
  const regimeCandles = await data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", limit: limit(days, "4h") });
  const rows = [];
  for (const candidate of candidates) {
    const candles = await data.fetchCandles({ source, symbol: "BTCUSDT", interval: candidate.timeframe, limit: limit(days, candidate.timeframe) });
    const benchmarkCandles = await basket(source, candidate.timeframe, limit(days, candidate.timeframe));
    const validationTime = candles[Math.floor(candles.length * 0.6)].time;
    const testTime = candles[Math.floor(candles.length * 0.8)].time;
    const results = [];
    for (const policy of policiesFor(candidate)) {
      const result = backtest.runBacktestOnCandles({
        source,
        symbol: "BTCUSDT",
        interval: candidate.timeframe,
        executionInterval: policy.executionTimeframe,
        executionCandles: policy.executionTimeframe === candidate.timeframe ? [] : executionCache[policy.executionTimeframe],
        strategy: "RelativeStrengthV2",
        candles,
        regimeCandles,
        benchmarkCandles,
        params: Object.assign({}, candidate.params, policy.stop || {}, {
          fillModel: "next-open",
          takerFeePct: 0.055,
          makerFeePct: 0.02,
          slippageBps: 2,
          maxPositionPct: 10,
          maxDailyLossPct: 2,
          maxNotional: 100000,
          executionSoftExitMode: policy.soft || "none",
          executionSoftExitConfirmations: 2
        })
      });
      results.push({ policy, result });
    }
    const baseline = results.find((item) => item.policy.id === "5m-hard").result;
    const baselineValidation = baseline.tradeList.filter((trade) => Number(trade.exitFillTime) >= validationTime && Number(trade.exitFillTime) < testTime);
    const baselineTest = baseline.tradeList.filter((trade) => Number(trade.exitFillTime) >= testTime);
    results.forEach(({ policy, result }) => {
      const validationTrades = result.tradeList.filter((trade) => Number(trade.exitFillTime) >= validationTime && Number(trade.exitFillTime) < testTime);
      const testTrades = result.tradeList.filter((trade) => Number(trade.exitFillTime) >= testTime);
      const validation = tradeMetrics(validationTrades);
      const test = tradeMetrics(testTrades);
      const baselineMetrics = tradeMetrics(baselineValidation);
      const baselineTestMetrics = tradeMetrics(baselineTest);
      rows.push({
        candidate: candidate.id,
        signalTimeframe: candidate.timeframe,
        policy: policy.id,
        executionTimeframe: policy.executionTimeframe,
        softExitMode: policy.soft || "none",
        stopPolicy: policy.stop || { trailingActivationR: 0 },
        validationTime,
        testTime,
        full: tradeMetrics(result.tradeList),
        validation,
        validationDelta: {
          returnPct: round(validation.returnPct - baselineMetrics.returnPct),
          maxDrawdownPct: round(validation.maxDrawdownPct - baselineMetrics.maxDrawdownPct),
          trades: validation.trades - baselineMetrics.trades
        },
        test,
        testDelta: {
          returnPct: round(test.returnPct - baselineTestMetrics.returnPct),
          maxDrawdownPct: round(test.maxDrawdownPct - baselineTestMetrics.maxDrawdownPct),
          trades: test.trades - baselineTestMetrics.trades
        },
        regret: regretMetrics(validationTrades, baselineValidation),
        executionDiagnostics: result.diagnostics.execution
      });
    });
  }
  const eligible = rows.filter((row) => row.policy !== "confirmed-htf" && row.validation.trades >= 4);
  const winners = eligible.filter((row) => row.validationDelta.returnPct > 0 && row.validationDelta.maxDrawdownPct <= 0 && row.regret.averageReturnDeltaPct >= 0);
  const report = {
    generatedAt: new Date().toISOString(),
    purpose: "Compare causal lower-timeframe protective, delayed-trailing, break-even, and two-close soft exits against the production 5m hard-check policy.",
    source,
    days,
    assumptions: { trainFraction: 0.6, validationFraction: 0.2, untouchedTestFraction: 0.2, baselinePolicy: "5m-hard", fillModel: "next-open", softExitFillModel: "execution-close", takerFeePct: 0.055, makerFeePct: 0.02, slippageBps: 2, maxPositionPct: 10, maxDailyLossPct: 2, maxNotional: 100000, stopBeforeTargetWhenBothTouched: true, gapStopsFillAtOpen: true },
    rows,
    recommendation: winners.length
      ? "Inspect the untouched test rows for policies that passed validation; paper-test only policies that remain favorable there."
      : "No lower-timeframe policy passed the validation return, drawdown, and regret gates. Keep 5m hard protective checks only and retain confirmed higher-timeframe rule exits."
  };
  fs.mkdirSync(outputDir, { recursive: true });
  const output = path.join(outputDir, "rs-execution-policy-" + tag.replace(/[^a-zA-Z0-9_.-]/g, "-") + ".json");
  fs.writeFileSync(output, JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({ output, recommendation: report.recommendation, rows }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
