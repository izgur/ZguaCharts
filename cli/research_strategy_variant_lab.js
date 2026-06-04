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

function parseCsv(value, fallback) {
  if (!value || value === "default") return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function changedParams(base, params) {
  const changed = {};
  Object.keys(params || {}).forEach((key) => {
    if (params[key] !== base[key]) changed[key] = params[key];
  });
  return changed;
}

function variantSpecs(base, requested, maxVariants) {
  const specs = [
    { variantName: "baseline", params: Object.assign({}, base), experimental: false },
    { variantName: "noRsiFilter", params: Object.assign({}, base, { useRsiFilter: false, rsiMin: null, rsiMax: null }), experimental: false },
    { variantName: "looserRsi", params: Object.assign({}, base, { rsiMin: Math.max(1, Number(base.rsiMin || 48) - 5), rsiMax: Math.min(99, Number(base.rsiMax || 76) + 5) }), experimental: false },
    { variantName: "fasterTrend", params: Object.assign({}, base, { emaTrend: Math.max(50, Number(base.emaTrend || 200) - 50) }), experimental: false },
    { variantName: "slowerTrend", params: Object.assign({}, base, { emaTrend: Number(base.emaTrend || 200) + 50 }), experimental: false },
    { variantName: "looserCooldown", params: Object.assign({}, base, { cooldownBars: Math.max(0, Number(base.cooldownBars || 0) - 3) }), experimental: false },
    { variantName: "symbolTrendRegime", params: Object.assign({}, base, { regimeMode: "symbolTrend" }), experimental: false },
    { variantName: "noRegime", params: Object.assign({}, base, { regimeMode: "noRegime" }), experimental: false },
    { variantName: "stricterTrend", params: Object.assign({}, base, { emaTrend: Number(base.emaTrend || 200) + 100, regimeMode: "symbolFastTrend" }), experimental: false },
    { variantName: "looserPullback", skipped: true, warning: "looserPullback is not parameterized yet; skipped to avoid changing SimpleAtrTrendV2 behavior." }
  ];
  const wanted = requested && requested.length ? requested : specs.map((spec) => spec.variantName);
  return specs.filter((spec) => wanted.includes(spec.variantName)).slice(0, Math.max(1, maxVariants));
}

function classify(row) {
  if (row.status === "ERROR") return "ERROR";
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  if (row.expectancyPctPerTrade <= 0) return "NEGATIVE_EXPECTANCY";
  return "OK";
}

function statusFor(row) {
  const reason = classify(row);
  if (reason === "OK") return row.profitFactor >= 1.1 && row.totalReturnPct > 0 ? "PASS" : "WARN";
  if (reason === "NO_TRADES") return "NO_TRADES";
  return "FAIL";
}

function tradeoffScore(row) {
  const viable = Number(row.profitFactor || 0) >= 1.1 && Number(row.totalReturnPct || 0) > 0;
  return round(
    (viable ? 25 : -25) +
    Math.min(Number(row.tradesPerMonth || 0), 20) * 1.5 +
    Number(row.profitFactor || 0) * 12 +
    Number(row.totalReturnPct || 0) * 1.2 -
    Number(row.maxDrawdownPct || 0) * 1.5,
    5
  );
}

function summarizeBlockers(diagnostics) {
  const counts = diagnostics && diagnostics.blockerCounts ? diagnostics.blockerCounts : {};
  return Object.keys(counts).map((name) => ({ name, count: Number(counts[name] || 0) }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 3);
}

function compactResult(result, spec, base, context) {
  const trades = Number(result.trades || 0);
  const row = {
    variantName: spec.variantName,
    strategy: context.strategy,
    experimental: !!spec.experimental,
    changedParams: changedParams(base, spec.params),
    changedLogic: spec.experimental ? spec.variantName : null,
    status: result.status || "FAIL",
    trades,
    tradesPerMonth: round(trades / Math.max(1, context.days) * 30, 2),
    totalReturnPct: round(result.totalReturn || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    winRate: round(result.winRate || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || 0, 4),
    expectancyPctPerTrade: trades ? round((result.totalReturn || 0) / trades, 4) : 0,
    score: 0,
    mainFailureReason: null,
    blockerSummary: summarizeBlockers(result.diagnostics),
    warnings: result.warnings || []
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  row.score = tradeoffScore(row);
  return row;
}

function skippedRow(spec, base, context) {
  return {
    variantName: spec.variantName,
    strategy: context.strategy,
    experimental: true,
    changedParams: {},
    changedLogic: spec.variantName,
    status: "SKIPPED",
    trades: 0,
    tradesPerMonth: 0,
    totalReturnPct: 0,
    profitFactor: 0,
    winRate: 0,
    maxDrawdownPct: 0,
    expectancyPctPerTrade: 0,
    score: -999,
    mainFailureReason: "NOT_PARAMETERIZED",
    blockerSummary: [],
    warnings: [spec.warning || "Variant skipped."]
  };
}

function bestRow(rows, predicate) {
  const filtered = rows.filter((row) => row.status !== "SKIPPED" && (!predicate || predicate(row)));
  if (!filtered.length) return null;
  return filtered.slice().sort((a, b) => b.score - a.score || b.profitFactor - a.profitFactor || b.totalReturnPct - a.totalReturnPct)[0];
}

function buildSummary(rows) {
  const baseline = rows.find((row) => row.variantName === "baseline") || null;
  const passing = rows.filter((row) => row.status === "PASS" || row.status === "WARN");
  const bestOverall = bestRow(rows);
  const mostActivePassing = passing.slice().sort((a, b) => b.tradesPerMonth - a.tradesPerMonth || b.score - a.score)[0] || null;
  const bestTradeoff = bestRow(rows, (row) => row.profitFactor >= 1.1 && row.totalReturnPct > 0);
  const rejectedVariants = rows.filter((row) => !["PASS", "WARN"].includes(row.status)).map((row) => ({
    variantName: row.variantName,
    status: row.status,
    reason: row.mainFailureReason,
    warnings: row.warnings || []
  }));
  const recommendation = bestTradeoff && baseline && bestTradeoff.variantName !== "baseline" && bestTradeoff.tradesPerMonth > baseline.tradesPerMonth
    ? {
      action: "REVIEW_VARIANT_ONLY",
      reason: bestTradeoff.variantName + " improved activity while preserving positive return and PF >= 1.1. This is read-only research; no candidate is changed."
    }
    : {
      action: "KEEP_CURRENT_CANDIDATE",
      reason: "No controlled variant clearly improved the activity/expectancy tradeoff enough to replace the baseline in this read-only lab."
    };
  return { baseline, bestOverall, mostActivePassing, bestTradeoff, rejectedVariants, recommendation };
}

const symbol = args.symbol || "ETHUSDT";
const timeframe = args.timeframe || args.interval || "1h";
const strategy = args.baseStrategy || args.strategy || "SimpleAtrTrendV2";
const days = periodDays(args.period);
const maxVariants = Math.max(1, Math.min(Number(args.maxVariants || args.max_variants || 20), 50));
const baseParams = args.baseParams ? JSON.parse(args.baseParams) : {};
const requestedVariants = parseCsv(args.variants, null);
const feePct = Number(args.feePct || args["fee-pct"] || 0.055);
const slippagePct = Number(args.slippagePct || args["slippage-pct"] || 0.02);
const source = args.source || "bybit";
const specs = variantSpecs(baseParams, requestedVariants, maxVariants);
const warnings = specs.filter((spec) => spec.skipped && spec.warning).map((spec) => spec.warning);

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
  const rows = specs.map((spec) => {
    if (spec.skipped) return skippedRow(spec, baseParams, { strategy });
    try {
      const params = Object.assign({}, spec.params, { feePct, slippagePct });
      const result = backtest.runBacktestOnCandles({
        source,
        symbol,
        interval: timeframe,
        strategy,
        candles,
        regimeCandles,
        params,
        feePct,
        slippagePct
      });
      return compactResult(result, spec, baseParams, { strategy, days });
    } catch (error) {
      return {
        variantName: spec.variantName,
        strategy,
        experimental: !!spec.experimental,
        changedParams: changedParams(baseParams, spec.params),
        changedLogic: spec.experimental ? spec.variantName : null,
        status: "ERROR",
        trades: 0,
        tradesPerMonth: 0,
        totalReturnPct: 0,
        profitFactor: 0,
        winRate: 0,
        maxDrawdownPct: 0,
        expectancyPctPerTrade: 0,
        score: -999,
        mainFailureReason: "ERROR",
        blockerSummary: [],
        warnings: [error.message]
      };
    }
  });
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbol,
      timeframe,
      period: args.period || "365d",
      baseStrategy: strategy,
      variants: specs.map((spec) => spec.variantName),
      maxVariants,
      feePct,
      slippagePct
    },
    rows,
    summary: buildSummary(rows),
    warnings
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
