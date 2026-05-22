const fs = require("fs");
const path = require("path");

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir);
}

function writeOptimizationReport(outputDir, results, summary, prefix) {
  ensureDir(outputDir);
  prefix = prefix ? prefix + "-" : "";
  fs.writeFileSync(path.join(outputDir, prefix + "optimization-results.json"), JSON.stringify({ summary: summary, results: results }, null, 2));
  fs.writeFileSync(path.join(outputDir, prefix + "optimization-results.csv"), toCsv(results));
  fs.writeFileSync(path.join(outputDir, prefix + "ranked-summary.json"), JSON.stringify(summary, null, 2));
}

function writeDebugReport(result, outputDir) {
  outputDir = outputDir || "reports";
  ensureDir(outputDir);
  fs.writeFileSync(path.join(outputDir, "debug-last-backtest.json"), JSON.stringify(result, null, 2));
}

function writeTradeAuditReport(audit, outputDir) {
  outputDir = outputDir || "reports";
  ensureDir(outputDir);
  fs.writeFileSync(path.join(outputDir, "trade-audit-last.json"), JSON.stringify(audit, null, 2));
}

function toCsv(results) {
  var columns = [
    "symbol",
    "interval",
    "strategy",
    "params",
    "totalReturn",
    "maxDrawdown",
    "profitFactor",
    "winRate",
    "trades",
    "sharpeRatio",
    "score",
    "valid",
    "trainTotalReturn",
    "trainMaxDrawdown",
    "trainProfitFactor",
    "trainWinRate",
    "trainTrades",
    "trainSharpeRatio",
    "testTotalReturn",
    "testMaxDrawdown",
    "testProfitFactor",
    "testWinRate",
    "testTrades",
    "testSharpeRatio"
  ];
  var lines = [columns.join(",")];
  results.forEach(function (row) {
    lines.push(columns.map(function (column) {
      var value = csvValue(row, column);
      return csvEscape(value);
    }).join(","));
  });
  return lines.join("\n");
}

function csvValue(row, column) {
  if (column === "params") return JSON.stringify(row.params);
  if (column.indexOf("train") === 0) return nestedMetric(row.train, column.slice(5));
  if (column.indexOf("test") === 0) return nestedMetric(row.test, column.slice(4));
  return row[column];
}

function nestedMetric(metrics, key) {
  if (!metrics) return "";
  return metrics[key.charAt(0).toLowerCase() + key.slice(1)];
}

function csvEscape(value) {
  var text = String(value === undefined ? "" : value);
  if (/[",\n]/.test(text)) return "\"" + text.replace(/"/g, "\"\"") + "\"";
  return text;
}

module.exports = {
  writeOptimizationReport: writeOptimizationReport,
  writeDebugReport: writeDebugReport,
  writeTradeAuditReport: writeTradeAuditReport,
  toCsv: toCsv
};
