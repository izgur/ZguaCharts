const backtest = require("../core/backtest");
const data = require("../core/data");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function median(values) {
  const nums = values.map(Number).filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (!nums.length) return 0;
  const mid = Math.floor(nums.length / 2);
  return nums.length % 2 ? nums[mid] : (nums[mid - 1] + nums[mid]) / 2;
}

function parseWindows(raw) {
  return String(raw || "90,180,365").split(",").map((item) => Number(String(item).replace(/d$/i, "").trim())).filter((value) => value > 0);
}

function classify(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 5) return "TOO_FEW_TRADES";
  if (row.totalReturnPct <= 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK" && row.trades >= 10 && row.profitFactor >= 1.05) return "PASS";
  if (["OK", "TOO_FEW_TRADES"].includes(reason) && row.totalReturnPct >= 0 && row.profitFactor >= 1) return "WARN";
  return "FAIL";
}

function compactResult(result, context) {
  const trades = Number(result.trades || 0);
  const row = {
    trades,
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    winRate: round(result.winRate || 0, 4),
    status: "FAIL",
    mainFailureReason: null
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  if (context) Object.assign(row, context);
  return row;
}

function runSlice(candles, regimeCandles, params, context) {
  const result = backtest.runBacktestOnCandles({
    source: context.source,
    symbol: context.symbol,
    interval: context.timeframe,
    strategy: context.strategy,
    candles,
    regimeCandles,
    params
  });
  return compactResult(result, context.extra);
}

function foldSlices(candles, folds) {
  const count = Math.max(1, Math.min(Number(folds || 4), 12));
  const size = Math.max(1, Math.floor(candles.length / count));
  const out = [];
  for (let i = 0; i < count; i += 1) {
    const start = i * size;
    const end = i === count - 1 ? candles.length : Math.min(candles.length, (i + 1) * size);
    const slice = candles.slice(start, end);
    if (slice.length) out.push({ fold: i + 1, candles: slice });
  }
  return out;
}

function stability(full, folds) {
  const passFoldCount = folds.filter((row) => row.status === "PASS" || row.status === "WARN").length;
  const failFoldCount = folds.length - passFoldCount;
  const negativeFoldCount = folds.filter((row) => Number(row.totalReturnPct || 0) < 0).length;
  const medianFoldReturnPct = round(median(folds.map((row) => row.totalReturnPct)), 4);
  const medianFoldProfitFactor = round(median(folds.map((row) => row.profitFactor)), 4);
  const worstFold = folds.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct || a.profitFactor - b.profitFactor)[0] || null;
  const bestFold = folds.slice().sort((a, b) => b.totalReturnPct - a.totalReturnPct || b.profitFactor - a.profitFactor)[0] || null;
  let status = "WATCH";
  if (!full || full.status === "FAIL") status = "FAIL";
  else if (failFoldCount > passFoldCount || negativeFoldCount > Math.floor(folds.length / 2)) status = "FRAGILE";
  else if (negativeFoldCount > 0 || failFoldCount > 0 || medianFoldProfitFactor < 1) status = "WATCH";
  else status = "STABLE";
  return {
    status,
    passFoldCount,
    failFoldCount,
    negativeFoldCount,
    medianFoldReturnPct,
    medianFoldProfitFactor,
    worstFold,
    bestFold,
    recommendation: {
      action: status === "STABLE" ? "KEEP_CURRENT_FOR_PAPER_REVIEW" : status === "WATCH" ? "WATCH_REGIME_DEPENDENCE" : status === "FRAGILE" ? "RESEARCH_REGIME_ROBUSTNESS" : "RESEARCH_ALTERNATIVES",
      reason: status === "STABLE"
        ? "Full-period and fold results are broadly consistent in this read-only review."
        : status === "WATCH"
          ? "Full-period backtest passes, but one or more folds/windows are weak. Continue paper-only observation and monitor regime dependence."
          : status === "FRAGILE"
            ? "Full-period result appears dependent on too few periods or too many weak folds."
            : "Full-period backtest fails, so this candidate should be researched further."
    }
  };
}

const symbol = args.symbol || "ETHUSDT";
const timeframe = args.timeframe || args.interval || "1h";
const strategy = args.strategy || "SimpleAtrTrendV2";
const days = periodDays(args.period);
const source = args.source || "bybit";
const baseParams = args.baseParams ? JSON.parse(args.baseParams) : {};
const folds = Math.max(1, Math.min(Number(args.folds || 4), 12));
const windows = parseWindows(args.recentWindows || args["recent-windows"]);
const costs = {
  makerFeePct: Number(args.makerFeePct || args["maker-fee-pct"] || 0),
  takerFeePct: Number(args.takerFeePct || args["taker-fee-pct"] || 0),
  slippageBps: Number(args.slippageBps || args["slippage-bps"] || 0)
};
const params = Object.assign({}, baseParams, costs);

Promise.all([
  data.fetchCandles({
    source,
    symbol,
    interval: timeframe,
    from: args.from || argsUtil.daysToFrom(days),
    to: args.to || new Date().toISOString(),
    limit: args.limit && args.limit !== "auto" ? Number(args.limit) : 5000
  }),
  data.fetchCandles({
    source,
    symbol: "BTCUSDT",
    interval: "4h",
    from: args.from || argsUtil.daysToFrom(days),
    to: args.to || new Date().toISOString(),
    limit: 3000
  })
]).then(([candles, regimeCandles]) => {
  const normalized = data.normalizeCandles(candles || []);
  const full = runSlice(normalized, regimeCandles, params, {
    source,
    symbol,
    timeframe,
    strategy,
    extra: {
      label: "full",
      startTime: normalized.length ? new Date(normalized[0].time * 1000).toISOString() : null,
      endTime: normalized.length ? new Date(normalized[normalized.length - 1].time * 1000).toISOString() : null
    }
  });
  const latestTime = normalized.length ? normalized[normalized.length - 1].time : 0;
  const recentWindows = windows.map((windowDays) => {
    const cutoff = latestTime - windowDays * 86400;
    const slice = normalized.filter((candle) => candle.time >= cutoff);
    return runSlice(slice, regimeCandles, params, {
      source,
      symbol,
      timeframe,
      strategy,
      extra: {
        label: String(windowDays) + "d",
        startTime: slice.length ? new Date(slice[0].time * 1000).toISOString() : null,
        endTime: slice.length ? new Date(slice[slice.length - 1].time * 1000).toISOString() : null
      }
    });
  });
  const foldRows = foldSlices(normalized, folds).map((item) => runSlice(item.candles, regimeCandles, params, {
    source,
    symbol,
    timeframe,
    strategy,
    extra: {
      fold: item.fold,
      startTime: new Date(item.candles[0].time * 1000).toISOString(),
      endTime: new Date(item.candles[item.candles.length - 1].time * 1000).toISOString()
    }
  }));
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbol,
      timeframe,
      strategy,
      period: args.period || "365d",
      folds,
      recentWindows: windows.map((item) => String(item) + "d")
    },
    full,
    recentWindows,
    folds: foldRows,
    stability: stability(full, foldRows),
    warnings: []
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
