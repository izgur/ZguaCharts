const backtest = require("../core/backtest");
const data = require("../core/data");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function parseCsv(value, fallback) {
  if (!value) return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function timeframeHours(timeframe) {
  const raw = String(timeframe || "1h").trim().toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 60) / 60;
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1);
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24;
  return 1;
}

function autoLimitFor(timeframe, days, cap) {
  const bars = Math.ceil(Number(days || 365) * 24 / timeframeHours(timeframe));
  return Math.max(100, Math.min(Number(cap || 5000), bars));
}

function clampInt(value, min) {
  return Math.max(min, Math.round(Number(value || min)));
}

function normalizeEmaOrder(params) {
  const out = Object.assign({}, params);
  out.emaFast = clampInt(out.emaFast, 2);
  out.emaSlow = Math.max(clampInt(out.emaSlow, 3), out.emaFast + 1);
  out.emaTrend = Math.max(clampInt(out.emaTrend, 4), out.emaSlow + 1);
  out.cooldownBars = clampInt(out.cooldownBars, 1);
  out.minHoldBars = clampInt(out.minHoldBars, 1);
  return out;
}

function scaledParams(base, timeframe) {
  const factor = 1 / timeframeHours(timeframe);
  return normalizeEmaOrder(Object.assign({}, base, {
    emaFast: Number(base.emaFast || 30) * factor,
    emaSlow: Number(base.emaSlow || 80) * factor,
    emaTrend: Number(base.emaTrend || 200) * factor,
    cooldownBars: Number(base.cooldownBars || 6) * factor,
    minHoldBars: Number(base.minHoldBars || 3) * factor
  }));
}

function changedParams(base, params) {
  const keys = ["atrMultiplier", "emaFast", "emaSlow", "emaTrend", "rsiMin", "rsiMax", "cooldownBars", "minHoldBars", "regimeMode", "useRsiFilter", "volumeFilter"];
  const out = {};
  keys.forEach((key) => {
    if (params[key] !== undefined && params[key] !== base[key]) out[key] = { from: base[key], to: params[key] };
  });
  return out;
}

function timeHorizon(params, timeframe) {
  const hours = timeframeHours(timeframe);
  return {
    emaFastHours: round(Number(params.emaFast || 0) * hours, 2),
    emaSlowHours: round(Number(params.emaSlow || 0) * hours, 2),
    emaTrendHours: round(Number(params.emaTrend || 0) * hours, 2),
    cooldownHours: round(Number(params.cooldownBars || 0) * hours, 2),
    minHoldHours: round(Number(params.minHoldBars || 0) * hours, 2)
  };
}

function classify(row) {
  if (row.status === "ERROR") return "ERROR";
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK") return "PASS";
  if (row.trades >= 20 && row.totalReturnPct >= 0 && row.profitFactor >= 1) return "WARN";
  if (reason === "NO_TRADES") return "NO_TRADES";
  return "FAIL";
}

function scoreRow(row) {
  const statusBonus = row.status === "PASS" ? 30 : row.status === "WARN" ? 12 : -25;
  let score = statusBonus
    + Math.min(Number(row.trades || 0), 150) * 0.14
    + Math.min(Number(row.tradesPerMonth || 0), 12) * 1.5
    + Number(row.profitFactor || 0) * 18
    + Number(row.totalReturnPct || 0) * 2.2
    + Number(row.expectancyPctPerTrade || 0) * 12
    - Number(row.maxDrawdownPct || 0) * 1.25;
  if (row.trades < 20) score -= 15;
  if (row.profitFactor < 1.1) score -= 18;
  if (row.totalReturnPct < 0) score -= 20;
  if (row.maxDrawdownPct > 25) score -= 20;
  return round(score, 5);
}

function compactResult(result, spec, options) {
  const trades = Number(result.trades || 0);
  const row = {
    presetFamily: spec.presetFamily,
    presetName: spec.presetName,
    symbol: options.symbol,
    timeframe: spec.timeframe,
    strategy: options.strategy,
    params: spec.params,
    changedParams: changedParams(options.baseParams, spec.params),
    timeHorizon: timeHorizon(spec.params, spec.timeframe),
    status: "FAIL",
    trades,
    tradesPerMonth: round(trades / Math.max(1, options.days) * 30, 2),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades, 4) : 0,
    score: 0,
    mainFailureReason: null,
    warnings: result.warnings || []
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  row.score = scoreRow(row);
  return row;
}

function presetSpec(family, name, timeframe, params) {
  return {
    presetFamily: family,
    presetName: name,
    timeframe,
    params: normalizeEmaOrder(params)
  };
}

function generatePresets(base, timeframes, maxRows) {
  const rows = [];
  if (timeframes.includes("1h")) rows.push(presetSpec("baseline_1h", "active_1h_params", "1h", base));
  timeframes.forEach((timeframe) => {
    rows.push(presetSpec("same_candle_params", "same_candles_" + timeframe, timeframe, base));
    rows.push(presetSpec("time_normalized_from_1h", "normalized_" + timeframe, timeframe, scaledParams(base, timeframe)));
  });
  ["15m", "1h"].filter((timeframe) => timeframes.includes(timeframe)).forEach((timeframe) => {
    [
      { emaFast: 12, emaSlow: 50, emaTrend: 100, cooldownBars: 2, minHoldBars: 1, atrMultiplier: 1.8 },
      { emaFast: 20, emaSlow: 50, emaTrend: 150, cooldownBars: 3, minHoldBars: 2, atrMultiplier: 1.8 },
      { emaFast: 20, emaSlow: 80, emaTrend: 200, cooldownBars: 4, minHoldBars: 2, atrMultiplier: 2.2 },
      { emaFast: 12, emaSlow: 80, emaTrend: 200, cooldownBars: 3, minHoldBars: 1, atrMultiplier: 2.2 }
    ].forEach((patch, index) => {
      rows.push(presetSpec("fast_native", "fast_native_" + timeframe + "_" + (index + 1), timeframe, Object.assign({}, base, patch)));
    });
  });
  ["1h", "4h"].filter((timeframe) => timeframes.includes(timeframe)).forEach((timeframe) => {
    [
      { emaFast: 20, emaSlow: 80, emaTrend: 150, atrMultiplier: 1.8, cooldownBars: 4, minHoldBars: 2 },
      { emaFast: 30, emaSlow: 80, emaTrend: 200, atrMultiplier: 2.2, cooldownBars: 6, minHoldBars: 3 },
      { emaFast: 50, emaSlow: 100, emaTrend: 200, atrMultiplier: 2.6, cooldownBars: 8, minHoldBars: 4 },
      { emaFast: 30, emaSlow: 100, emaTrend: 200, atrMultiplier: 1.8, cooldownBars: 6, minHoldBars: 3 }
    ].forEach((patch, index) => {
      rows.push(presetSpec("swing_native", "swing_native_" + timeframe + "_" + (index + 1), timeframe, Object.assign({}, base, patch)));
    });
  });
  const seen = new Set();
  return rows.filter((row) => {
    const key = [row.presetFamily, row.presetName, row.timeframe, JSON.stringify(row.params)].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, maxRows);
}

function best(rows, predicate) {
  return rows.filter(predicate || (() => true)).sort((a, b) => {
    const ap = a.status === "PASS" ? 2 : a.status === "WARN" ? 1 : 0;
    const bp = b.status === "PASS" ? 2 : b.status === "WARN" ? 1 : 0;
    return bp - ap || b.score - a.score || b.profitFactor - a.profitFactor || b.totalReturnPct - a.totalReturnPct;
  })[0] || null;
}

function buildSummary(rows) {
  const baseline = rows.find((row) => row.presetFamily === "baseline_1h") || rows.find((row) => row.timeframe === "1h" && row.presetFamily === "same_candle_params") || null;
  const bestOverall = best(rows);
  const best15m = best(rows, (row) => row.timeframe === "15m");
  const best1h = best(rows, (row) => row.timeframe === "1h");
  const best4h = best(rows, (row) => row.timeframe === "4h");
  const bestTimeNormalized = best(rows, (row) => row.presetFamily === "time_normalized_from_1h");
  const bestFastNative = best(rows, (row) => row.presetFamily === "fast_native");
  const bestSwingNative = best(rows, (row) => row.presetFamily === "swing_native");
  let recommendation = { action: "RESEARCH_MORE", reason: "No preset row clearly passed the research gate." };
  if (bestOverall && ["PASS", "WARN"].includes(bestOverall.status)) {
    if (baseline && bestOverall.presetFamily === baseline.presetFamily && bestOverall.presetName === baseline.presetName) {
      recommendation = { action: "KEEP_CURRENT", reason: "The active 1h baseline remains the best row in this read-only preset search." };
    } else if (!baseline || bestOverall.score > Number(baseline.score || 0) + 8) {
      recommendation = {
        action: "REVIEW_TIMEFRAME_PRESET",
        reason: bestOverall.presetFamily + " " + bestOverall.timeframe + " ranks above the active baseline for research review only."
      };
    } else {
      recommendation = { action: "KEEP_CURRENT", reason: "Alternative presets exist, but none beats the active baseline by enough to review yet." };
    }
  } else if (baseline && ["PASS", "WARN"].includes(baseline.status)) {
    recommendation = { action: "KEEP_CURRENT", reason: "No viable alternative preset beat the active baseline." };
  } else {
    recommendation = { action: "NO_VIABLE_ALTERNATIVE", reason: "No preset row passed or warned in this bounded read-only search." };
  }
  return {
    baseline,
    bestOverall,
    best15m,
    best1h,
    best4h,
    bestTimeNormalized,
    bestFastNative,
    bestSwingNative,
    recommendation
  };
}

const symbol = args.symbol || "ETHUSDT";
const timeframes = parseCsv(args.timeframes, ["15m", "1h", "4h"]);
const strategy = args.strategy || "SimpleAtrTrendV2";
const days = periodDays(args.period);
const source = args.source || "bybit";
const maxRows = Math.max(1, Math.min(Number(args.maxRows || args.max_rows || 100), 100));
const baseParams = args.baseParams ? JSON.parse(args.baseParams) : {};
const feePct = Number(args.feePct || args["fee-pct"] || 0.055);
const slippagePct = Number(args.slippagePct || args["slippage-pct"] || 0.02);
const from = args.from || argsUtil.daysToFrom(days);
const to = args.to || new Date().toISOString();
const explicitLimit = args.limit && args.limit !== "auto" ? Number(args.limit) : null;
const presets = generatePresets(baseParams, timeframes, maxRows);
const candleJobs = {};
timeframes.forEach((timeframe) => {
  const limit = explicitLimit || autoLimitFor(timeframe, days, 5000);
  candleJobs[timeframe] = data.fetchCandles({ source, symbol, interval: timeframe, from, to, limit }).then((candles) => data.normalizeCandles(candles || []));
});

Promise.all([
  Promise.all(Object.keys(candleJobs).map((timeframe) => candleJobs[timeframe].then((candles) => [timeframe, candles]))),
  data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: explicitLimit || autoLimitFor("4h", days, 3000) }).then((candles) => data.normalizeCandles(candles || []))
]).then(([entries, regimeCandles]) => {
  const candlesByTimeframe = {};
  entries.forEach(([timeframe, candles]) => { candlesByTimeframe[timeframe] = candles; });
  const rows = presets.map((spec) => {
    try {
      const result = backtest.runBacktestOnCandles({
        source,
        symbol,
        interval: spec.timeframe,
        strategy,
        candles: candlesByTimeframe[spec.timeframe] || [],
        regimeCandles,
        params: Object.assign({}, spec.params, { feePct, slippagePct }),
        feePct,
        slippagePct
      });
      return compactResult(result, spec, { symbol, strategy, days, baseParams });
    } catch (error) {
      return {
        presetFamily: spec.presetFamily,
        presetName: spec.presetName,
        symbol,
        timeframe: spec.timeframe,
        strategy,
        params: spec.params,
        changedParams: changedParams(baseParams, spec.params),
        timeHorizon: timeHorizon(spec.params, spec.timeframe),
        status: "ERROR",
        trades: 0,
        tradesPerMonth: 0,
        totalReturnPct: 0,
        profitFactor: 0,
        maxDrawdownPct: 0,
        winRate: 0,
        expectancyPctPerTrade: 0,
        score: -999,
        mainFailureReason: "ERROR",
        warnings: [error.message]
      };
    }
  });
  const sortedRows = rows.sort((a, b) => b.score - a.score);
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbol,
      timeframes,
      strategy,
      period: args.period || "365d",
      presets: args.presets || "default",
      maxRows,
      feePct,
      slippagePct
    },
    rows: sortedRows,
    summary: buildSummary(sortedRows),
    warnings: presets.length >= maxRows ? ["Preset search reached maxRows cap."] : []
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
