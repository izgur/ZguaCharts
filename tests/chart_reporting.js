const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../static/app.js"), "utf8");
function load(name, context) {
  const match = new RegExp("^(?:async )?function " + name + "\\(", "m").exec(source);
  const rest = source.slice(match.index), next = /\n(?:async )?function /.exec(rest);
  vm.runInContext(next ? rest.slice(0, next.index) : rest, context);
}
async function main() {
  const context = vm.createContext({
    escapeHtml: (v) => String(v), formatNumber: (v, d) => Number(v).toFixed(d), formatSigned: (v) => Number(v).toFixed(2),
    formatDateTime: (v) => String(v),
  });
  load("watchlistColumns", context);
  assert.equal(context.watchlistColumns().find(([id]) => id === "percent24h")[1], "%1d");
  load("renderBacktestHistory", context);
  const html = context.renderBacktestHistory({ runs: [{ strategy: "RS", source: "bybit", symbol: "BTCUSDT", timeframe: "1h", period: "13d", createdAt: "2026-10-04T00:00:00Z", params: { feePct: 0, slippagePct: 0 }, trades: 5, winRate: 40, totalReturnPct: 2, profitFactor: 2, maxDrawdown: 4 }] });
  assert.ok(html.includes("Not saved"), "Do not invent execution metadata for old runs");
  assert.ok(html.includes("bybit · BTCUSDT 1h"));
  assert.ok(!html.includes("Sum return"), "Repeated windows must not imply cumulative account performance");
  assert.ok(source.includes("saveHistory: false"), "Chart refreshes must not pollute manual history");
  load("renderSelectedStrategy", context);
  await context.renderSelectedStrategy({ timeframeSelect:{value:'1h'}, historyLoaded: false, candles: [] }, 1);
  console.log("Chart reporting passed: compact labels, legacy provenance, no misleading return sums and no partial-history previews.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
