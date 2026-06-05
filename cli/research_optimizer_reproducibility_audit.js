const backtest = require("../core/backtest");
const data = require("../core/data");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

const TOLERANCES = {
  tradeTolerance: 1,
  returnPctTolerance: 0.5,
  profitFactorTolerance: 0.15,
  drawdownPctTolerance: 0.5
};

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
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

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
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

function compactResult(result, candidate) {
  const row = {
    status: "FAIL",
    trades: Number(result.trades || 0),
    totalReturnPct: round(result.totalReturn || result.totalReturnPct || 0, 4),
    profitFactor: round(result.profitFactor || 0, 4),
    maxDrawdownPct: round(result.maxDrawdown || result.maxDrawdownPct || 0, 4),
    winRate: round(result.winRate || 0, 4),
    mainFailureReason: null
  };
  row.mainFailureReason = classify(row);
  row.status = statusFor(row);
  if (candidate && candidate.score !== undefined) row.score = round(candidate.score || 0, 4);
  return row;
}

function compactOriginal(candidate) {
  const original = {
    status: candidate.status || candidate.qualityStatus || "FAIL",
    trades: Number(candidate.trades || 0),
    totalReturnPct: round(candidate.totalReturnPct || candidate.totalReturn || 0, 4),
    profitFactor: round(candidate.profitFactor || 0, 4),
    maxDrawdownPct: round(candidate.maxDrawdownPct || candidate.maxDrawdown || 0, 4),
    winRate: round(candidate.winRate || 0, 4),
    score: round(candidate.score || candidate.practicalScore || 0, 4)
  };
  original.mainFailureReason = candidate.mainFailureReason || classify(original);
  if (!original.status || original.status === "OK") original.status = statusFor(original);
  return original;
}

function maxAbs(values) {
  return values.reduce((max, value) => Math.max(max, Math.abs(Number(value || 0))), 0);
}

function statusBand(status) {
  if (status === "PASS" || status === "WARN") return "PASSING";
  if (status === "NO_TRADES") return "NO_TRADES";
  return "FAILING";
}

function diffSummary(original, reruns) {
  return {
    tradesDiffMax: maxAbs(reruns.map((row) => Number(row.trades || 0) - Number(original.trades || 0))),
    returnDiffMax: round(maxAbs(reruns.map((row) => Number(row.totalReturnPct || 0) - Number(original.totalReturnPct || 0))), 4),
    profitFactorDiffMax: round(maxAbs(reruns.map((row) => Number(row.profitFactor || 0) - Number(original.profitFactor || 0))), 4),
    drawdownDiffMax: round(maxAbs(reruns.map((row) => Number(row.maxDrawdownPct || 0) - Number(original.maxDrawdownPct || 0))), 4),
    statusChanged: reruns.some((row) => row.status !== original.status)
  };
}

function reproducibilityStatus(original, reruns, diffs) {
  if (!reruns.length || reruns.some((row) => row.status === "ERROR")) return "FAIL";
  const materiallyDifferent = diffs.tradesDiffMax > TOLERANCES.tradeTolerance
    || diffs.returnDiffMax > TOLERANCES.returnPctTolerance
    || diffs.profitFactorDiffMax > TOLERANCES.profitFactorTolerance
    || diffs.drawdownDiffMax > TOLERANCES.drawdownPctTolerance;
  const bandChanged = reruns.some((row) => statusBand(row.status) !== statusBand(original.status));
  if (original.status === "PASS" && reruns.some((row) => row.status === "FAIL")) return "UNSTABLE";
  if (diffs.statusChanged || bandChanged || materiallyDifferent) return "UNSTABLE";
  const modest = diffs.tradesDiffMax > 0
    || diffs.returnDiffMax > TOLERANCES.returnPctTolerance / 2
    || diffs.profitFactorDiffMax > TOLERANCES.profitFactorTolerance / 2
    || diffs.drawdownDiffMax > TOLERANCES.drawdownPctTolerance / 2;
  return modest ? "WATCH" : "REPRODUCIBLE";
}

function rejectionReasons(original, status, diffs, reruns) {
  const reasons = [];
  if (original.status === "PASS" && reruns.some((row) => row.status === "FAIL")) reasons.push("original_pass_rerun_fail");
  if (diffs.statusChanged) reasons.push("status_changed");
  if (diffs.tradesDiffMax > TOLERANCES.tradeTolerance) reasons.push("trades_mismatch");
  if (diffs.returnDiffMax > TOLERANCES.returnPctTolerance) reasons.push("return_mismatch");
  if (diffs.profitFactorDiffMax > TOLERANCES.profitFactorTolerance) reasons.push("profit_factor_mismatch");
  if (diffs.drawdownDiffMax > TOLERANCES.drawdownPctTolerance) reasons.push("drawdown_mismatch");
  if (status === "FAIL") reasons.push("rerun_failed");
  return reasons;
}

function recommendationFor(status, reasons) {
  if (status === "REPRODUCIBLE") return { action: "TRUST_REPRODUCIBLE_ONLY", reason: "Rerun metrics matched the optimizer row within tight tolerances." };
  if (status === "WATCH") return { action: "RESEARCH_MORE", reason: "Rerun metrics drifted modestly; collect more reproducibility evidence before trusting the candidate." };
  if (reasons.indexOf("original_pass_rerun_fail") !== -1) return { action: "RESEARCH_MORE", reason: "Original PASS became FAIL on rerun; do not trust this optimizer row without resolving the mismatch." };
  return { action: "RESEARCH_MORE", reason: "Candidate reproducibility failed conservative audit checks." };
}

function auditCandidate(candidate, candles, regimeCandles, options) {
  const original = compactOriginal(candidate);
  const reruns = [];
  for (let i = 0; i < options.reruns; i += 1) {
    try {
      const result = backtest.runBacktestOnCandles({
        source: options.source,
        symbol: candidate.symbol,
        interval: candidate.timeframe,
        strategy: candidate.strategy,
        candles,
        regimeCandles,
        params: Object.assign({}, candidate.params || {}, { feePct: options.feePct, slippagePct: options.slippagePct }),
        feePct: options.feePct,
        slippagePct: options.slippagePct
      });
      reruns.push(compactResult(result, candidate));
    } catch (error) {
      reruns.push({
        status: "ERROR",
        trades: 0,
        totalReturnPct: 0,
        profitFactor: 0,
        maxDrawdownPct: 0,
        winRate: 0,
        mainFailureReason: "ERROR",
        error: error.message
      });
    }
  }
  const diffs = diffSummary(original, reruns);
  const status = reproducibilityStatus(original, reruns, diffs);
  const reasons = rejectionReasons(original, status, diffs, reruns);
  return {
    strategy: candidate.strategy,
    symbol: candidate.symbol,
    timeframe: candidate.timeframe,
    params: candidate.params || {},
    original,
    reruns,
    diffs,
    reproducibilityStatus: status,
    rejectionReasons: reasons,
    recommendation: recommendationFor(status, reasons)
  };
}

function mismatchScore(row) {
  return Number(row.diffs.returnDiffMax || 0)
    + Number(row.diffs.profitFactorDiffMax || 0) * 5
    + Number(row.diffs.drawdownDiffMax || 0)
    + Number(row.diffs.tradesDiffMax || 0) * 0.25
    + (row.diffs.statusChanged ? 10 : 0);
}

function buildSummary(rows) {
  const counts = {
    reproducibleCount: rows.filter((row) => row.reproducibilityStatus === "REPRODUCIBLE").length,
    watchCount: rows.filter((row) => row.reproducibilityStatus === "WATCH").length,
    unstableCount: rows.filter((row) => row.reproducibilityStatus === "UNSTABLE").length,
    failCount: rows.filter((row) => row.reproducibilityStatus === "FAIL").length
  };
  const worstMismatch = rows.slice().sort((a, b) => mismatchScore(b) - mismatchScore(a))[0] || null;
  const recommendation = counts.unstableCount || counts.failCount
    ? { action: "RESEARCH_MORE", reason: "One or more optimizer rows did not reproduce; trust only rows that pass reproducibility checks." }
    : counts.watchCount
      ? { action: "TRUST_REPRODUCIBLE_ONLY", reason: "Some rows drifted modestly; treat WATCH rows as research-only until repeated." }
      : { action: "TRUST_REPRODUCIBLE_ONLY", reason: "Audited optimizer rows reproduced within tight tolerances." };
  return Object.assign(counts, { worstMismatch, recommendation });
}

async function main() {
  const candidates = args.candidates ? JSON.parse(args.candidates) : [];
  const days = periodDays(args.period);
  const source = args.source || "bybit";
  const reruns = Math.max(1, Math.min(Number(args.reruns || 2), 5));
  const limit = args.limit && args.limit !== "auto" ? Number(args.limit) : null;
  const from = args.from || argsUtil.daysToFrom(days);
  const to = args.to || new Date().toISOString();
  const options = {
    source,
    reruns,
    feePct: Number(args.feePct || 0.055),
    slippagePct: Number(args.slippagePct || 0.02)
  };
  const candleJobs = {};
  candidates.forEach((candidate) => {
    const key = candidate.symbol + ":" + candidate.timeframe;
    if (!candleJobs[key]) {
      candleJobs[key] = data.fetchCandles({
        source,
        symbol: candidate.symbol,
        interval: candidate.timeframe,
        from,
        to,
        limit: limit || autoLimitFor(candidate.timeframe, days, 5000)
      }).then((candles) => data.normalizeCandles(candles || []));
    }
  });
  const [entries, regimeCandles] = await Promise.all([
    Promise.all(Object.keys(candleJobs).map((key) => candleJobs[key].then((candles) => [key, candles]))),
    data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: limit || autoLimitFor("4h", days, 3000) }).then((candles) => data.normalizeCandles(candles || []))
  ]);
  const candlesByKey = {};
  entries.forEach(([key, candles]) => { candlesByKey[key] = candles; });
  const rows = candidates.map((candidate) => auditCandidate(candidate, candlesByKey[candidate.symbol + ":" + candidate.timeframe] || [], regimeCandles, options));
  process.stdout.write(JSON.stringify({
    ok: true,
    tolerances: TOLERANCES,
    rows,
    summary: buildSummary(rows),
    warnings: ["No promotion, paper tick, config write, or real trading action was performed."]
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}

main().catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
