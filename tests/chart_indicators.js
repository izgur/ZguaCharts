const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../static/app.js"), "utf8");
function load(name, context) {
  const match = new RegExp("^(?:async )?function " + name + "\\(", "m").exec(source);
  assert.ok(match);
  const rest = source.slice(match.index), next = /\n(?:async )?function /.exec(rest);
  vm.runInContext(next ? rest.slice(0, next.index) : rest, context);
}
let height;
const context = vm.createContext({
  currentVisibleRange: () => ({ from: 100, to: 200 }),
  currentVisibleLogicalRange: () => ({ from: 1800, to: 2000 }),
});
load("currentVisibleSyncRange", context);
assert.equal(context.currentVisibleSyncRange({}).type, "time", "Different study warmup lengths must align by timestamps, not indices");
context.normalizeSeriesData = data => data;
load("indicatorSeriesData", context);
const spine = [{time:10},{time:20},{time:30}];
assert.equal(JSON.stringify(context.indicatorSeriesData({candles:spine},[{time:5,value:9},{time:20,value:2}])),JSON.stringify([{time:10},{time:20,value:2},{time:30}]),'Warmup and absent bars must keep identical timestamp indices; outside overlay times must not expand the axis');
assert.equal(JSON.stringify(context.indicatorSeriesData({candles:[{time:10}],replayActive:true,replayTimeline:spine},[{time:10,value:1},{time:20,value:2}])),JSON.stringify([{time:10,value:1},{time:20},{time:30}]),'Replay must retain its full axis without leaking future values');
assert.equal(JSON.stringify(context.indicatorSeriesData({candles:spine,indicatorTimeline:[...spine,{time:40}]},[{time:20,value:2}],1)),JSON.stringify([{time:10},{time:20},{time:30,value:2},{time:40}]),'Forward displacement must use bar indices on the shared spine');
assert.equal(JSON.stringify(context.indicatorSeriesData({candles:[{time:10}],replayActive:true,replayTimeline:spine},[{time:10,value:1},{time:20,value:99}],1)),JSON.stringify([{time:10},{time:20,value:1},{time:30}]),'Replay may project known values but must never project unseen candles');
assert.equal(JSON.stringify(context.indicatorSeriesData({candles:spine},[{time:20,value:2}],-1)),JSON.stringify([{time:10,value:2},{time:20},{time:30}]),'Lagging display is distinct from when a value was known');
load("applyIndicatorHeights", context);
const pane = {
  indicatorPanesEl: { children: [{ dataset: { indicatorId: "rsi" }, classList: { contains: () => true } }, { dataset: { indicatorId: "cipherb" }, classList: { contains: () => true } }], style: {} },
  indicatorHeights: { rsi: 143 }, node: { style: { setProperty: (_key, value) => { height = value; } }, querySelector: () => ({ classList: { toggle: () => {} } }) },
};
context.applyIndicatorHeights(pane);
assert.equal(pane.indicatorPanesEl.style.gridTemplateRows, "143px 128px");
assert.equal(height, "271px", "The study viewport must include both study heights");
pane.node.querySelector = () => ({ getBoundingClientRect: () => ({ height: 305 }), classList: { toggle: () => {} } });
context.applyIndicatorHeights(pane);
const fittedRows = pane.indicatorPanesEl.style.gridTemplateRows.split(" ").map(parseFloat);
assert.ok(fittedRows.every((value) => value >= 64), "Compact studies must remain visible");
assert.ok(Math.abs(fittedRows.reduce((a, b) => a + b, 0) - 163) < 0.001, "Both studies must fit without a clipped second pane");
const stored = vm.createContext({
  state: { panes: [{ source: "old" }, { source: "binance", symbol: "ETHUSDT" }] },
  panes: [{ sourceSelect: { value: "bybit_spot" }, symbolSelect: { value: "BTCUSDT" }, timeframeSelect: { value: "1h" }, presetSelect: { value: "none" }, signalMarkerToggle: { checked: true }, indicatorHeights: { rsi: 143 } }],
  selectedIndicators: () => ["rsi"], countSelect: { value: "1" }, STORAGE_KEY: "test",
  localStorage: { setItem: () => {} },
});
load("saveState", stored); stored.saveState();
assert.equal(stored.state.panes[0].source, "bybit_spot", "Layout changes must use the latest market selection");
assert.equal(stored.state.panes[0].indicatorHeights.rsi, 143);
assert.equal(stored.state.panes[1].symbol, "ETHUSDT", "Temporarily hidden chart settings must be retained");
let callback, requested;
Object.assign(context, {
  setTimeout: (fn) => { callback = fn; return 1; },
  loadCandles: async (_pane, options) => { requested = options.limit; return { candles: [{ time: 100, close: 11 }] }; },
  historyLimitForPane: () => 2000, updateTicker: () => {},
});
load("startBinancePolling", context);
(async () => {
  const live = { requestId: 1, candles: Array(2000).fill({ close: 10 }), series: { setData: () => {} } };
  context.startBinancePolling(live);
  await callback();
  assert.equal(requested, 2000, "Polling must not shrink chart history to 240 candles");
  console.log("Indicator regression checks passed: timestamp alignment, saved heights and full-history polling.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
