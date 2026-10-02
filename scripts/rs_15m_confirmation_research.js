const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");

function arg(name, fallback) {
  const flag = "--" + name;
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] !== undefined ? process.argv[index + 1] : fallback;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function timeframeSeconds(timeframe) {
  const raw = String(timeframe || "1h").toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 15) * 60;
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 60 * 60;
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24 * 60 * 60;
  return 60 * 60;
}

function limitFor(days, timeframe) {
  return Math.ceil(Number(days || 365) * 24 * 60 * 60 / timeframeSeconds(timeframe));
}

function alignBasketCandles(seriesBySymbol) {
  const symbols = Object.keys(seriesBySymbol || {});
  if (!symbols.length) return [];
  const maps = {};
  symbols.forEach((symbol) => {
    maps[symbol] = new Map((seriesBySymbol[symbol] || []).map((row) => [Number(row.time), row]));
  });
  const times = Array.from(maps[symbols[0]].keys())
    .filter((time) => symbols.every((symbol) => maps[symbol].has(time)))
    .sort((a, b) => a - b);
  if (!times.length) return [];
  const first = {};
  symbols.forEach((symbol) => {
    first[symbol] = Number(maps[symbol].get(times[0]).close) || 1;
  });
  return times.map((time) => {
    const close = symbols
      .map((symbol) => Number(maps[symbol].get(time).close) / first[symbol])
      .reduce((sum, value) => sum + value, 0) / symbols.length;
    return { time, open: close, high: close, low: close, close, volume: 0 };
  });
}

async function loadBasket(source, timeframe, limit) {
  const series = {};
  for (const symbol of ["ETHUSDT", "SOLUSDT", "BNBUSDT"]) {
    series[symbol] = await data.fetchCandles({ source, symbol, interval: timeframe, limit });
  }
  return alignBasketCandles(series);
}

function rowAtOrBefore(frame, time) {
  let lo = 0;
  let hi = frame.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (Number(frame[mid].time) <= time) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

function confirmationDecision(mode, lowerFrame, endIndex, minBars) {
  if (mode === "none") return { allow: true };
  if (endIndex < Math.max(60, Number(minBars || 8))) return { allow: false, reason: "lower_timeframe_warmup" };
  const row = lowerFrame[endIndex];
  const prev = lowerFrame[endIndex - 1] || row;
  const prev2 = lowerFrame[endIndex - 2] || prev;
  const reasons = [];
  const close = Number(row.close);
  const ema20 = Number(row.ema20);
  const ema50 = Number(row.ema50);
  const rsi = Number(row.rsi14);
  const prevRsi = Number(prev.rsi14);
  const prevDonchian = Number(prev.donchianHigh20);
  const volumeOk = !row.volumeMa20 || Number(row.volume) >= Number(row.volumeMa20) * 0.8;

  if (mode === "trend") {
    if (!(close > ema50 && Number(row.ema20) > ema50 && rsi >= 50)) reasons.push("15m_trend_not_confirmed");
  } else if (mode === "strongTrend") {
    if (!(close > ema20 && ema20 > ema50 && rsi >= 55 && volumeOk)) reasons.push("15m_strong_trend_not_confirmed");
  } else if (mode === "breakout") {
    if (!(Number.isFinite(prevDonchian) && close > prevDonchian && rsi >= 52)) reasons.push("15m_breakout_not_confirmed");
  } else if (mode === "pullbackReclaim") {
    if (!(close > ema20 && prevRsi < 50 && rsi >= 50)) reasons.push("15m_pullback_reclaim_not_confirmed");
  } else if (mode === "twoBarMomentum") {
    if (!(close > ema20 && Number(prev.close) > Number(prev.ema20) && close > Number(prev2.close) && rsi >= 52)) reasons.push("15m_two_bar_momentum_not_confirmed");
  } else {
    reasons.push("unknown_confirmation_mode");
  }
  return reasons.length ? { allow: false, reason: reasons[0] } : { allow: true };
}

function makeEntryFilter(mode, lowerFrame, baseTimeframe) {
  const baseSeconds = timeframeSeconds(baseTimeframe);
  const lowerSeconds = timeframeSeconds("15m");
  return ({ row }) => {
    const signalEnd = Number(row.time) + baseSeconds - lowerSeconds;
    const endIndex = rowAtOrBefore(lowerFrame, signalEnd);
    return confirmationDecision(mode, lowerFrame, endIndex, Math.max(4, Math.floor(baseSeconds / lowerSeconds / 2)));
  };
}

function resultRow(result, candidate, mode) {
  return {
    candidate: candidate.id,
    timeframe: candidate.timeframe,
    mode,
    trades: result.trades,
    totalReturnPct: round(result.totalReturn),
    profitFactor: round(result.profitFactor),
    maxDrawdownPct: round(result.maxDrawdown),
    winRate: round(result.winRate),
    avgBarsHeld: round(result.avgBarsHeld),
    score: round(Number(result.totalReturn || 0) + Number(result.profitFactor || 0) * 25 - Number(result.maxDrawdown || 0) * 4 + Math.min(Number(result.trades || 0), 200) * 0.05)
  };
}

async function runCandidate(candidate, context, modes) {
  const candles = await data.fetchCandles({
    source: context.source,
    symbol: candidate.symbol,
    interval: candidate.timeframe,
    limit: limitFor(context.days, candidate.timeframe)
  });
  const regimeCandles = await data.fetchCandles({
    source: context.source,
    symbol: "BTCUSDT",
    interval: "4h",
    limit: limitFor(context.days, "4h")
  });
  const benchmarkCandles = await loadBasket(context.source, candidate.timeframe, limitFor(context.days, candidate.timeframe));
  const rows = [];
  for (const mode of modes) {
    const result = backtest.runBacktestOnCandles({
      source: context.source,
      symbol: candidate.symbol,
      interval: candidate.timeframe,
      strategy: "RelativeStrengthV2",
      candles,
      regimeCandles,
      benchmarkCandles,
      params: Object.assign({}, candidate.params, { feePct: 0.055, slippagePct: 0.02 }),
      entryFilter: mode === "none" ? null : makeEntryFilter(mode, context.lowerFrame, candidate.timeframe)
    });
    rows.push(resultRow(result, candidate, mode));
  }
  const baseline = rows.find((row) => row.mode === "none") || rows[0];
  rows.forEach((row) => {
    row.returnDeltaPct = round(row.totalReturnPct - baseline.totalReturnPct);
    row.drawdownDeltaPct = round(row.maxDrawdownPct - baseline.maxDrawdownPct);
    row.tradeRetentionPct = baseline.trades ? round(row.trades / baseline.trades * 100, 2) : 0;
  });
  return rows;
}

async function main() {
  const source = arg("source", "bybit");
  const days = Number(arg("days", "520"));
  const tag = arg("tag", new Date().toISOString().slice(0, 10).replace(/-/g, ""));
  const outputDir = arg("output", "reports/research-campaigns");
  const modes = String(arg("modes", "none,trend,strongTrend,twoBarMomentum,breakout,pullbackReclaim")).split(",").map((item) => item.trim()).filter(Boolean);
  const lowerCandles = await data.fetchCandles({ source, symbol: "BTCUSDT", interval: "15m", limit: limitFor(days, "15m") });
  const lowerFrame = indicators.buildIndicatorFrame(data.normalizeCandles(lowerCandles), {});
  const context = { source, days, lowerFrame };
  const candidates = [
    {
      id: "rs-4h-lb42-thr0-rsi61-atr27-risk03",
      symbol: "BTCUSDT",
      timeframe: "4h",
      params: {
        atrMultiplier: 2.7,
        causalRegimeFilter: "bullishOnly",
        indicatorFilterPreset: "trendQuality",
        regimeMode: "looseBtcBull",
        riskPct: 0.03,
        rsBenchmarkSymbol: "CRYPTO_BASKET",
        rsLookback: 42,
        rsThreshold: 0,
        rsiMax: 72,
        rsiMin: 61,
        volumeFilter: false
      }
    },
    {
      id: "rs-1h-lb156-thr002-rsi57-atr29-risk02",
      symbol: "BTCUSDT",
      timeframe: "1h",
      params: {
        atrMultiplier: 2.9,
        causalRegimeFilter: "bullishOnly",
        indicatorFilterPreset: "trendQuality",
        regimeMode: "looseBtcBull",
        riskPct: 0.02,
        rsBenchmarkSymbol: "CRYPTO_BASKET",
        rsLookback: 156,
        rsThreshold: -0.02,
        rsiMax: 72,
        rsiMin: 57,
        volumeFilter: false
      }
    }
  ];
  const rows = [];
  for (const candidate of candidates) {
    rows.push(...await runCandidate(candidate, context, modes));
  }
  rows.sort((a, b) =>
    a.candidate.localeCompare(b.candidate) ||
    (b.score - a.score)
  );
  const report = {
    generatedAt: new Date().toISOString(),
    purpose: "Test 15m confirmation filters on top of winning RelativeStrengthV2 4h/1h entries.",
    source,
    days,
    lowerTimeframe: "15m",
    modes,
    rows,
    recommendation: rows.some((row) => row.mode !== "none" && row.returnDeltaPct > 0 && row.tradeRetentionPct >= 50 && row.maxDrawdownPct <= rows.find((base) => base.candidate === row.candidate && base.mode === "none").maxDrawdownPct)
      ? "A 15m confirmation improved at least one RS candidate; inspect positive-delta rows for paper-candidate variants."
      : "No 15m confirmation clearly improved the RS candidates; keep 15m as diagnostics unless a later filter shows positive delta."
  };
  fs.mkdirSync(outputDir, { recursive: true });
  const outPath = path.join(outputDir, "rs-15m-confirmation-" + String(tag).replace(/[^a-zA-Z0-9_.-]/g, "-") + ".json");
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({ output: outPath, recommendation: report.recommendation, rows: rows.slice(0, 20) }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
