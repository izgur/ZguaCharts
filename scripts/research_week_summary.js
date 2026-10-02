const fs = require("fs");
const path = require("path");

function arg(name, fallback) {
  const flag = "--" + name;
  const index = process.argv.indexOf(flag);
  return index >= 0 && process.argv[index + 1] !== undefined ? process.argv[index + 1] : fallback;
}

function round(value, digits) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "";
  return n.toFixed(digits === undefined ? 2 : digits);
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function findJsonFiles(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => path.join(dir, name));
}

function baselineStress(row) {
  if (!row) return null;
  if (Array.isArray(row.stress)) return row.stress.find((item) => item.label === "baseline-costs") || row.stress[0] || null;
  return null;
}

function highStress(row) {
  if (!row) return null;
  if (Array.isArray(row.stress)) return row.stress.find((item) => item.label === "high-stress") || row.stress[row.stress.length - 1] || null;
  return null;
}

function paramsText(params) {
  if (!params) return "";
  const pieces = [];
  if (params.rsBenchmarkSymbol) pieces.push(params.rsBenchmarkSymbol);
  if (params.rsLookback !== undefined) pieces.push("LB" + params.rsLookback);
  if (params.rsThreshold !== undefined) pieces.push("thr " + params.rsThreshold);
  if (params.rsiMin !== undefined || params.rsiMax !== undefined) pieces.push("rsi " + params.rsiMin + "-" + params.rsiMax);
  if (params.atrMultiplier !== undefined) pieces.push("atr " + params.atrMultiplier);
  if (params.riskPct !== undefined) pieces.push("risk " + round(Number(params.riskPct) * 100, 2) + "%");
  return pieces.join(", ");
}

function summarize(file) {
  const report = readJson(file);
  if (!report || !report.clusterSummary) return null;
  const deep = Array.isArray(report.deepReview) ? report.deepReview[0] : null;
  const base = baselineStress(deep);
  const stress = highStress(deep);
  const best = report.clusterSummary.best || {};
  return {
    file,
    generatedAt: report.generatedAt || "",
    symbol: report.symbol || "",
    timeframe: report.timeframe || "",
    mode: report.mode || "",
    verdict: report.verdict || "",
    tested: report.clusterSummary.tested,
    passRate: report.clusterSummary.passRate,
    medianReturnPct: report.clusterSummary.medianReturnPct,
    medianProfitFactor: report.clusterSummary.medianProfitFactor,
    medianDrawdownPct: report.clusterSummary.medianDrawdownPct,
    bestReturnPct: best.totalReturnPct,
    bestProfitFactor: best.profitFactor,
    bestDrawdownPct: best.maxDrawdownPct,
    bestTrades: best.trades,
    bestParams: best.params,
    deepScore: deep && deep.deepScore,
    deepVerdict: deep && deep.verdict,
    deepReturnPct: base && base.totalReturnPct,
    deepProfitFactor: base && base.profitFactor,
    deepDrawdownPct: base && base.maxDrawdownPct,
    deepTrades: base && base.trades,
    highStressReturnPct: stress && stress.totalReturnPct,
    highStressDrawdownPct: stress && stress.maxDrawdownPct,
    walkForwardPass: deep && deep.walkForward ? deep.walkForward.passCount : null,
    walkForwardWorstReturnPct: deep && deep.walkForward ? deep.walkForward.worstReturnPct : null,
    deepParams: deep && deep.params
  };
}

function rank(row) {
  const robust = row.verdict === "ROBUST_CLUSTER" ? 1000 : row.verdict === "PROMISING_BUT_SENSITIVE" ? 300 : 0;
  const deep = Number(row.deepScore || 0);
  const stress = Number(row.highStressReturnPct || 0);
  const ddPenalty = Math.max(0, Number(row.highStressDrawdownPct || row.deepDrawdownPct || 0) - 20) * 20;
  return robust + deep + stress - ddPenalty;
}

function markdown(rows) {
  const lines = [];
  lines.push("# Week Research Summary");
  lines.push("");
  lines.push("Generated: " + new Date().toISOString());
  lines.push("");
  lines.push("## Ranked Runs");
  lines.push("");
  lines.push("| # | TF | Mode | Verdict | Tested | Pass | Deep Ret | PF | DD | Stress Ret | WF | Params |");
  lines.push("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|");
  rows.forEach((row, index) => {
    lines.push([
      index + 1,
      row.timeframe,
      row.mode,
      row.verdict,
      row.tested,
      round(Number(row.passRate) * 100) + "%",
      round(row.deepReturnPct) + "%",
      round(row.deepProfitFactor),
      round(row.deepDrawdownPct) + "%",
      round(row.highStressReturnPct) + "%",
      (row.walkForwardPass === null || row.walkForwardPass === undefined ? "" : row.walkForwardPass + "/4"),
      paramsText(row.deepParams)
    ].join(" | ").replace(/^/, "| ").replace(/$/, " |"));
  });
  lines.push("");
  lines.push("## Top Screen Rows");
  lines.push("");
  lines.push("| # | TF | Mode | Return | PF | DD | Trades | Params | File |");
  lines.push("|---:|---|---|---:|---:|---:|---:|---|---|");
  rows.slice(0, 20).forEach((row, index) => {
    lines.push([
      index + 1,
      row.timeframe,
      row.mode,
      round(row.bestReturnPct) + "%",
      round(row.bestProfitFactor),
      round(row.bestDrawdownPct) + "%",
      row.bestTrades,
      paramsText(row.bestParams),
      path.basename(row.file)
    ].join(" | ").replace(/^/, "| ").replace(/$/, " |"));
  });
  lines.push("");
  lines.push("## Reading Guide");
  lines.push("");
  lines.push("- Prefer rows with `ROBUST_CLUSTER`, stress return still positive, and `WF 4/4`.");
  lines.push("- Do not promote anything live from this report alone; use it to choose paper candidates and the next focused validation.");
  lines.push("- 15m rows need extra skepticism because trade count rises but noise and execution sensitivity rise too.");
  lines.push("");
  return lines.join("\n");
}

function main() {
  const input = arg("input", "reports/research-campaigns");
  const output = arg("output", path.join("reports", "research-campaigns", "week-research-summary.md"));
  const rows = findJsonFiles(input)
    .map(summarize)
    .filter(Boolean)
    .sort((a, b) => rank(b) - rank(a));
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, markdown(rows));
  process.stdout.write(JSON.stringify({ ok: true, input, output, runs: rows.length, best: rows[0] || null }, null, 2));
}

main();
