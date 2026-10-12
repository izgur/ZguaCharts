const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require.resolve("../static/app.js"), "utf8");
function load(name, context) {
  const match = new RegExp("^(?:async )?function " + name + "\\(", "m").exec(source);
  const rest = source.slice(match.index), next = /\n(?:async )?function /.exec(rest);
  vm.runInContext(next ? rest.slice(0, next.index) : rest, context);
}
const properties = {}, options = [];
const context = vm.createContext({
  normalizeMarkerTime: Number, uiPrefs: { uiFontSize: 15, chartFontSize: 16, detailFontSize: 11 },
  document: { documentElement: { style: { setProperty: (name, value) => { properties[name] = value; } } } },
  panes: [{ chart: { applyOptions: (value) => options.push(value) }, indicatorCharts: [{ chart: { applyOptions: (value) => options.push(value) } }] }], backtestPane: null,
  localStorage: { getItem: () => JSON.stringify({ uiFontSize: 200, chartFontSize: 0, detailFontSize: 1 }) }, UI_PREF_KEY: "test",
  formatDateTime: String,
});
load("isCandleBodyClick", context);
const pane = { chartStyle: "candles", candles: [{ time: 1, open: 100, close: 110, high: 130, low: 80 }],
  chart: { timeScale: () => ({ timeToCoordinate: () => 50, options: () => ({ barSpacing: 10 }) }) }, series: { priceToCoordinate: (value) => 200 - value } };
assert.equal(context.isCandleBodyClick(pane, { time: 1, point: { x: 50, y: 95 } }), true);
assert.equal(context.isCandleBodyClick(pane, { time: 1, point: { x: 50, y: 75 } }), false, "Wick clicks do not open details");
assert.equal(context.isCandleBodyClick(pane, { time: 1, point: { x: 55, y: 95 } }), false, "Candle gaps do not open details");
assert.equal(context.isCandleBodyClick(pane, { time: 2, point: { x: 50, y: 95 } }), false);
assert.equal(context.isCandleBodyClick({ ...pane, chartStyle: "line" }, { time: 1, point: { x: 50, y: 95 } }), false);
load("applyFontSettings", context); context.applyFontSettings();
assert.equal(properties["--ui-font-scale"], 1.25); assert.equal(properties["--detail-font-size"], "11px");
assert.equal(options.length, 2); assert.equal(options[0].layout.fontSize, 16);
load("loadUiPrefs", context);
const settings = context.loadUiPrefs(); assert.equal(settings.uiFontSize, 16); assert.equal(settings.chartFontSize, 12); assert.equal(settings.detailFontSize, 8);
load("renderDataDiagnostics", context);
let inserted;
const status = { textContent: "Market unavailable" };
const diagnostics = { innerHTML: "", querySelector: () => ({ replaceWith: (element) => { inserted = element; } }) };
context.renderDataDiagnostics({ status, dataDiagnosticsEl: diagnostics, candles: [{ time: 1 }] });
assert.equal(inserted, status, "Move the live status node, not a stale text copy");
assert.ok(!diagnostics.innerHTML.includes("Candles 1"));
console.log("Display preferences passed: font bounds, chart updates, candle-body hit testing and unified live footer.");
