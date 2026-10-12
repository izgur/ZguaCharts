const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../static/app.js"), "utf8");
function load(name, context) {
  const match = new RegExp("^(?:async )?function " + name + "\\(", "m").exec(source);
  assert.ok(match, name);
  const rest = source.slice(match.index), next = /\n(?:async )?function /.exec(rest);
  vm.runInContext(next ? rest.slice(0, next.index) : rest, context);
}
let view = { from: 40, to: 80 }, price = { from: 90, to: 130 }, finish;
const rows = Array.from({ length: 100 }, (_, i) => ({ time: 1000 + i * 60, close: 100 + i }));
const context = vm.createContext({
  window: { ChartPresentation: { seriesData: (data) => data } },
  currentVisibleLogicalRange: () => ({ ...view }),
  restoreVisibleLogicalRange: (_chart, range) => { view = { ...range }; },
  normalizeSeriesData: (data) => data,
  cancelPaneRangeSync() {}, setSeriesMarkers() {}, renderDataDiagnostics() {},
  refreshHeaderSignalStrip() {},
  renderIndicators: () => new Promise((resolve) => { finish = resolve; }),
});
for (const name of ["applyReplaySlice", "indicatorSeriesData", "schedulePaneRangeSync", "syncPaneTimeRangeFromChart"]) load(name, context);
const pane = {
  replayActive: true, replayNeedsReset: false, requestId: 1,
  chart: { priceScale: () => ({ getVisibleRange: () => ({ ...price }), setVisibleRange: (range) => { price = range; } }) },
  series: { setData(data) { this.data = data; view = { from: 0, to: 20 }; price = { from: 0, to: 50 }; } },
  status: {}, candles: rows, overlaySeries: [], indicatorCharts: [],
};
(async () => {
  const first = context.applyReplaySlice(pane, rows.slice(0, 60), rows);
  assert.equal(view.from, 40, "Selecting a replay bar must retain the current viewport");
  assert.equal(price.from, 90, "Selecting a replay bar must retain the price range");
  assert.equal(pane.series.data.length, 100);
  assert.equal(pane.series.data[60].close, undefined, "Future timestamps must not reveal prices");
  view = { from: 25, to: 65 }; finish(); await first;
  assert.equal(view.from, 25, "Async indicator completion must not undo manual panning");
  const next = context.applyReplaySlice(pane, rows.slice(0, 61), rows);
  assert.equal(view.from, 25, "Playback must retain the user's newly panned position");
  finish(); await next;
  const study = context.indicatorSeriesData(pane, rows.slice(0, 61).map(({ time, close }) => ({ time, value: close })));
  assert.equal(study.length, 100, "Studies must retain the same complete time axis");
  assert.equal(study[61].value, undefined);
  pane.syncIndicators = true;
  context.schedulePaneRangeSync(pane, {});
  context.syncPaneTimeRangeFromChart(pane, {});
  assert.equal(pane.pendingRangeSyncSource, undefined, "Study updates cannot recenter the replay chart");
  const template = fs.readFileSync(require.resolve("../templates/index.html"), "utf8");
  assert.ok(template.includes('<body data-page="charts">'));
  assert.match(template, /id="dashboard-page"[^>]* hidden>/, "Dashboard must not flash before chart initialization");
  console.log("Replay viewport passed: frozen axes, manual panning, future whitespace and chart-first startup.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
