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
  let resolve, calls = 0, range, rendered = 0;
  const context = vm.createContext({
    loadCandles: async () => { calls++; return new Promise((done) => { resolve = done; }); },
    renderIndicators: async () => { rendered++; }, syncIndicatorTimeRanges: () => {},
  });
  load("loadOlderChartHistory", context);
  const pane = { requestId: 1, historyLoaded: true, candles: [{ time: 100 }, { time: 200 }], status: {},
    chart: { timeScale: () => ({ getVisibleLogicalRange: () => ({ from: -10, to: 20 }), setVisibleLogicalRange: (value) => { range = value; } }) },
    series: { setData: () => {} },
  };
  const first = context.loadOlderChartHistory(pane);
  await context.loadOlderChartHistory(pane);
  assert.equal(calls, 1, "Only one older-history request per pane");
  resolve({ candles: [{ time: 50 }, { time: 100 }] }); await first;
  assert.equal(pane.candles.length, 3);
  assert.equal(range.from, -9, "Prepending must preserve the viewed candles");
  assert.equal(rendered, 1, "Studies must cover the expanded history");
  const stale = context.loadOlderChartHistory(pane);
  pane.requestId++; pane.olderHistoryLoading = false;
  resolve({ candles: [{ time: 1 }] }); await stale;
  assert.equal(pane.candles.length, 3, "Old market requests cannot modify a new market");
  const empty = context.loadOlderChartHistory(pane);
  resolve({ candles: [] }); await empty;
  assert.equal(pane.olderHistoryExhausted, true);
  await context.loadOlderChartHistory(pane);
  assert.equal(calls, 3, "Exhausted history stops requesting");
  console.log("Chart history passed: one request, viewport preservation, study expansion, stale-market guards and exhaustion.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
