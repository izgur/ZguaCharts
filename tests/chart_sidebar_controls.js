const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { snapPoint, zoomRange, measurementStats, removableDrawings } = require("../static/chart-drawings.js");

const near = { x: 10, y: 12, price: 101 }, far = { x: 80, y: 90, price: 110 };
assert.equal(snapPoint({ x: 10, y: 10 }, [near, far], "off"), null);
assert.equal(snapPoint({ x: 10, y: 10 }, [far, near], "weak"), near);
assert.equal(snapPoint({ x: 0, y: 0 }, [near, far], "weak"), null);
assert.equal(snapPoint({ x: 0, y: 0 }, [far, near], "strong"), near);
assert.equal(snapPoint({ x: 0, y: 0 }, [{ x: NaN, y: 2 }], "strong"), null);
assert.equal(snapPoint({ x: 0, y: 0 }, [], "weak"), null);
assert.deepEqual(zoomRange({ from: 0, to: 100 }, 0.7), { from: 15, to: 85 });
const original = { from: -120, to: 400 };
const zoomed = zoomRange(original, 0.7), restored = zoomRange(zoomed, 1 / 0.7);
assert.ok(Math.abs(restored.from - original.from) < 1e-9);
assert.ok(Math.abs(restored.to - original.to) < 1e-9);
assert.equal(zoomRange({ from: 0, to: 1 }, 0.7).to - zoomRange({ from: 0, to: 1 }, 0.7).from, 5);
assert.equal(zoomRange(null, 1), null);
assert.equal(zoomRange(original, NaN), null);
assert.equal(zoomRange(original, -1), null);
assert.deepEqual(measurementStats({ time: 0, price: 100 }, { time: 90000, price: 110 }, 25), { delta: 10, percent: 10, bars: 25, duration: "1d 1h" });
assert.deepEqual(measurementStats({ time: 60, price: 200 }, { time: 0, price: 180 }, -4.7), { delta: -20, percent: -10, bars: 5, duration: "1m" });
assert.equal(measurementStats({ time: 0, price: 0 }, { time: 59.9, price: 10 }, 1).duration, "1m");
assert.equal(measurementStats({ time: 0, price: 0 }, { time: 0, price: 10 }, 0).percent, 0);
const items = [{ id: "a", locked: true }, { id: "b", locked: false }, { id: "c" }];
assert.deepEqual(removableDrawings(items, false).map((i) => i.id), ["b", "c"]);
assert.deepEqual(removableDrawings(items, true), items);
assert.equal(items.length, 3);
const source = fs.readFileSync(require.resolve("../static/app.js"), "utf8");
const start = source.indexOf("function updateChartMarkers(pane) {");
const end = source.indexOf("\nfunction ", start + 1);
const context = { buildMarkerDetailsMap: (markers) => markers, LightweightCharts: {} };
vm.createContext(context); vm.runInContext(source.slice(start, end), context);
let displayed;
const pane = { signalMarkerToggle: { checked: true }, signalMarkers: [{ time: 10 }], backtestMarkers: [{ time: 20 }], series: { setMarkers: (markers) => { displayed = markers; } } };
context.updateChartMarkers(pane); assert.equal(displayed.length, 2);
pane.drawingPositionsHidden = true; context.updateChartMarkers(pane); assert.equal(displayed.length, 0);
assert.equal(pane.signalMarkers.length, 1); assert.equal(pane.backtestMarkers.length, 1);
pane.drawingPositionsHidden = false; context.updateChartMarkers(pane); assert.equal(displayed.length, 2);
const tickerStart = source.indexOf("function updateTicker(pane, price) {");
const tickerEnd = source.indexOf("\nfunction ", tickerStart + 1);
let watchlistRenders = 0;
const tickerContext = {
  panes: [{ sourceSelect: { value: "bybit" } }],
  sourceForWatchSymbol: (_symbol, preferred) => preferred,
  watchlistQuotes: new Map([["bybit:BTCUSDT", { price: 100 }]]),
  renderWatchlist: () => { watchlistRenders += 1; },
  formatPrice: String,
  window: { clearTimeout: () => {}, setTimeout: () => 1 },
};
vm.createContext(tickerContext);
vm.runInContext(source.slice(tickerStart, tickerEnd), tickerContext);
const tickerPane = {
  sourceSelect: { value: "binance" }, symbolSelect: { value: "BTCUSDT" }, lastPrice: null,
  tickerSymbol: {}, tickerPrice: {}, ticker: { classList: { remove: () => {}, add: () => {} } },
};
tickerContext.updateTicker(tickerPane, 110);
assert.equal(tickerPane.tickerPrice.textContent, "110");
assert.equal(tickerContext.watchlistQuotes.get("bybit:BTCUSDT").price, 100);
assert.equal(watchlistRenders, 0);
tickerPane.sourceSelect.value = "bybit";
tickerContext.updateTicker(tickerPane, 105);
assert.equal(tickerContext.watchlistQuotes.get("bybit:BTCUSDT").price, 105);
assert.equal(watchlistRenders, 1);
console.log("Sidebar controls passed: snapping, zoom bounds, bars/time/percent, locked removal and exchange isolation.");
