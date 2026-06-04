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

function classify(row) {
  if (row.status === "ERROR") return "ERROR";
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor <= 1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  if (row.expectancyPctPerTrade <= 0) return "FEES_TOO_HIGH";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK") return row.profitFactor >= 1.1 ? "PASS" : "WARN";
  if (reason === "NO_TRADES") return "NO_TRADES";
  return "FAIL";
}

function score(row) {
  return round(
    Number(row.totalReturnPct || 0) +
    Number(row.profitFactor || 0) * 8 -
    Number(row.maxDrawdownPct || 0) * 0.75 +
    Math.min(Number(row.trades || 0), 150) * 0.03,
    5
  );
}

function changedParams(base, params) {
  const changed = {};
  Object.keys(params).forEach((key) => {
    if (params[key] !== base[key]) changed[key] = params[key];
  });
  return changed;
}

function compactResult(result, context) {
  const trades = Number(result.trades || 0);
  const row = {
    params: context.params,
    changedParams: context.changedParams,
    status: result.status || "FAIL",
    trades,
    tradesPerMonth: round(trades / Math.max(1, context.days) * 30, 2),
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
  row.score = score(row);
  return row;
}

function validParams(params) {
  if (Number(params.atrMultiplier) <= 0) return false;
  if (Number(params.emaFast) <= 0 || Number(params.emaSlow) <= 0 || Number(params.emaTrend) <= 0) return false;
  if (Number(params.emaFast) >= Number(params.emaSlow)) return false;
  if (Number(params.rsiMin) >= Number(params.rsiMax)) return false;
  if (Number(params.cooldownBars) < 0 || Number(params.minHoldBars) < 0) return false;
  return true;
}

function uniquePush(list, seen, base, params, maxVariants, includeBase) {
  if (!validParams(params)) return;
  const key = JSON.stringify(params);
  if (seen.has(key)) return;
  if (!includeBase && Object.keys(changedParams(base, params)).length === 0) return;
  if (list.length >= maxVariants) return;
  seen.add(key);
  list.push(params);
}

function localRanges(base) {
  return {
    atrMultiplier: [base.atrMultiplier, round(Number(base.atrMultiplier) - 0.4, 2), round(Number(base.atrMultiplier) - 0.2, 2), round(Number(base.atrMultiplier) + 0.2, 2), round(Number(base.atrMultiplier) + 0.4, 2)],
    emaFast: [base.emaFast, Number(base.emaFast) - 10, Number(base.emaFast) + 10],
    emaSlow: [base.emaSlow, Number(base.emaSlow) - 20, Number(base.emaSlow) + 20],
    emaTrend: [base.emaTrend, Number(base.emaTrend) - 50, Number(base.emaTrend) + 50],
    rsiMin: [base.rsiMin, Number(base.rsiMin) - 3, Number(base.rsiMin) + 3],
    rsiMax: [base.rsiMax, Number(base.rsiMax) - 3, Number(base.rsiMax) + 3],
    cooldownBars: [base.cooldownBars, Math.max(0, Number(base.cooldownBars) - 2), Number(base.cooldownBars) + 2],
    minHoldBars: [base.minHoldBars, Math.max(0, Number(base.minHoldBars) - 1), Number(base.minHoldBars) + 1]
  };
}

function generateVariants(base, maxVariants, includeBase) {
  const variants = [];
  const seen = new Set();
  const ranges = localRanges(base);
  if (includeBase) uniquePush(variants, seen, base, Object.assign({}, base), maxVariants, includeBase);

  Object.keys(ranges).forEach((key) => {
    ranges[key].forEach((value) => {
      const next = Object.assign({}, base, { [key]: value });
      uniquePush(variants, seen, base, next, maxVariants, includeBase);
    });
  });

  const pairKeys = ["atrMultiplier", "emaFast", "emaSlow", "rsiMin", "rsiMax", "cooldownBars", "minHoldBars"];
  for (let i = 0; i < pairKeys.length && variants.length < maxVariants; i += 1) {
    for (let j = i + 1; j < pairKeys.length && variants.length < maxVariants; j += 1) {
      const left = ranges[pairKeys[i]].filter((value) => value !== base[pairKeys[i]]);
      const right = ranges[pairKeys[j]].filter((value) => value !== base[pairKeys[j]]);
      left.forEach((leftValue) => {
        right.forEach((rightValue) => {
          const next = Object.assign({}, base, { [pairKeys[i]]: leftValue, [pairKeys[j]]: rightValue });
          uniquePush(variants, seen, base, next, maxVariants, includeBase);
        });
      });
    }
  }
  return variants;
}

function robustnessStatus(baseResult, variants, thresholds) {
  const tested = variants.length;
  const passed = variants.filter((row) => row.status === "PASS" || row.status === "WARN");
  const passRate = tested ? passed.length / tested : 0;
  const medianPf = median(variants.map((row) => row.profitFactor));
  const medianReturn = median(variants.map((row) => row.totalReturnPct));
  if (!baseResult || baseResult.status !== "PASS") return "FAIL";
  if (passRate >= thresholds.robustPassRate && medianPf >= thresholds.minMedianProfitFactor && medianReturn > thresholds.minMedianReturnPct) return "ROBUST";
  if (passRate >= thresholds.watchPassRate && medianPf >= 1 && medianReturn > 0) return "WATCH";
  return "FRAGILE";
}

const symbol = args.symbol || "ETHUSDT";
const timeframe = args.timeframe || args.interval || "1h";
const strategy = args.strategy || "SimpleAtrTrendV2";
const days = periodDays(args.period);
const maxVariants = Math.max(1, Math.min(Number(args.maxVariants || args.max_variants || 100), 250));
const includeBase = String(args.includeBase === undefined ? "true" : args.includeBase).toLowerCase() !== "false";
const baseParams = args.baseParams ? JSON.parse(args.baseParams) : {};
const feePct = Number(args.feePct || args["fee-pct"] || 0.055);
const slippagePct = Number(args.slippagePct || args["slippage-pct"] || 0.02);
const thresholds = {
  robustPassRate: 0.35,
  watchPassRate: 0.15,
  minMedianProfitFactor: 1.1,
  minMedianReturnPct: 0
};

data.fetchCandles({
  source: args.source || "bybit",
  symbol,
  interval: timeframe,
  from: args.from || argsUtil.daysToFrom(days),
  to: args.to || new Date().toISOString(),
  limit: args.limit && args.limit !== "auto" ? Number(args.limit) : 5000
}).then((candles) => {
  const variants = generateVariants(baseParams, maxVariants, includeBase);
  const rows = variants.map((params) => {
    try {
      const withCosts = Object.assign({}, params, { feePct, slippagePct });
      const result = backtest.runBacktestOnCandles({
        source: args.source || "bybit",
        symbol,
        interval: timeframe,
        strategy,
        candles,
        params: withCosts,
        feePct,
        slippagePct
      });
      return compactResult(result, {
        params,
        changedParams: changedParams(baseParams, params),
        days
      });
    } catch (error) {
      return {
        params,
        changedParams: changedParams(baseParams, params),
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
  const sorted = rows.slice().sort((a, b) => b.score - a.score);
  const baseKey = JSON.stringify(baseParams);
  const baseResult = rows.find((row) => JSON.stringify(row.params) === baseKey) || null;
  const status = robustnessStatus(baseResult, rows, thresholds);
  const passCount = rows.filter((row) => row.status === "PASS" || row.status === "WARN").length;
  const passRate = rows.length ? round(passCount / rows.length, 4) : 0;
  const recommendation = {
    action: status === "ROBUST" ? "CONTINUE_OBSERVING_CURRENT_CANDIDATE" : status === "WATCH" ? "REVIEW_PARAMETER_SENSITIVITY" : status === "FRAGILE" ? "TREAT_AS_FRAGILE" : "DO_NOT_RELY_ON_BASE_PARAMS",
    reason: status === "ROBUST"
      ? "Nearby parameter variants remain acceptable often enough for read-only robustness review."
      : status === "WATCH"
        ? "The base passes, but nearby variants are mixed. Continue paper observation and inspect sensitivity before any promotion decision."
        : status === "FRAGILE"
          ? "The base passes, but too few nearby variants remain acceptable."
          : "The base parameter set does not pass this robustness lab."
  };
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbol,
      timeframe,
      strategy,
      period: args.period || "365d",
      mode: args.mode || "local-grid",
      maxVariants,
      includeBase,
      thresholds,
      feePct,
      slippagePct
    },
    baseResult,
    robustness: {
      status,
      passRate,
      passCount,
      testedVariants: rows.length,
      medianProfitFactor: round(median(rows.map((row) => row.profitFactor)), 4),
      medianReturnPct: round(median(rows.map((row) => row.totalReturnPct)), 4),
      medianMaxDrawdownPct: round(median(rows.map((row) => row.maxDrawdownPct)), 4),
      medianTrades: round(median(rows.map((row) => row.trades)), 4),
      bestVariant: sorted[0] || null,
      worstVariant: sorted.slice().reverse()[0] || null,
      recommendation
    },
    variants: sorted,
    warnings: rows.length >= maxVariants ? ["Variant generation was capped by maxVariants."] : []
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
