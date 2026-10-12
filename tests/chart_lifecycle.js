const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(require.resolve("../static/app.js"), "utf8");

function loadFunctions(context, names) {
  vm.createContext(context);
  for (const name of names) {
    const marker = new RegExp("^(?:async )?function " + name + "\\(", "m");
    const match = marker.exec(source);
    assert.ok(match, name);
    const remainder = source.slice(match.index);
    const next = /\n(?:async )?function /.exec(remainder);
    vm.runInContext(next ? remainder.slice(0, next.index) : remainder, context);
  }
  return context;
}

class Socket {
  static instances = [];
  constructor() { this.listeners = {}; this.sent = []; this.closed = false; Socket.instances.push(this); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  send(message) { this.sent.push(message); }
  close() { this.closed = true; }
  emit(name, value) { this.listeners[name]?.(value); }
}
const candle = (time) => ({ time, open: 10, high: 12, low: 9, close: 11, volume: 5 });
function pane() {
  return {
    disposed: false, requestId: 1, candles: [], historyLoaded: true,
    performanceTimings: {},
    sourceSelect: { value: "bybit" }, symbolSelect: { value: "BTCUSDT" }, timeframeSelect: { value: "1h" },
    status: {}, series: { update() {}, setData() {} }, tickerSymbol: {}, tickerPrice: {},
  };
}

async function main() {
  const context = loadFunctions({
    WebSocket: Socket, BYBIT_WS: "bybit", HYPERLIQUID_WS: "hyperliquid", updateTicker() {},
  }, ["connectBybit", "connectHyperliquid", "mergeLiveCandle", "bybitInterval"]);
  for (const [connect, message] of [
    ["connectBybit", (time) => ({ topic: "kline.60.BTCUSDT", data: [{ start: time * 1000, open: 10, high: 12, low: 9, close: 11, volume: "42.5" }] })],
    ["connectHyperliquid", (time) => ({ channel: "candle", data: { t: time * 1000, o: 10, h: 12, l: 9, c: 11, v: "42.5" } })],
  ]) {
    const p = pane(); let updates = 0;
    p.series.update = () => updates++;
    context[connect](p, 1);
    const socket = p.ws;
    socket.emit("message", { data: JSON.stringify(message(100)) });
    assert.equal(updates, 1, connect + " accepts current candles");
    assert.equal(p.candles[0].volume, 42.5, connect + " preserves volume for indicator parameter updates");
    socket.emit("message", { data: JSON.stringify(message(90)) });
    assert.equal(updates, 1, connect + " rejects older candles");
    socket.emit("message", { data: "not JSON" });
    socket.emit("message", { data: "null" });
    p.requestId = 2;
    socket.emit("message", { data: JSON.stringify(message(110)) });
    assert.equal(updates, 1, connect + " rejects old requests");
    p.requestId = 1; p.ws = {};
    socket.emit("message", { data: JSON.stringify(message(110)) });
    assert.equal(updates, 1, connect + " rejects replaced sockets");
    p.ws = socket; p.disposed = true;
    socket.emit("message", { data: JSON.stringify(message(110)) });
    socket.emit("open");
    assert.equal(updates, 1, connect + " rejects removed charts");
    assert.ok(socket.closed);
    const count = Socket.instances.length;
    context[connect](p, 1);
    assert.equal(Socket.instances.length, count, connect + " cannot reconnect removed charts");
  }
  const p = pane();
  assert.equal(context.mergeLiveCandle(p, { ...candle(100), close: NaN }), false);
  assert.equal(context.mergeLiveCandle(p, candle(100)), true);
  assert.equal(context.mergeLiveCandle(p, { ...candle(100), close: 12 }), true);
  assert.equal(p.candles.length, 1);
  assert.equal(context.mergeLiveCandle(p, { time: 100, open: 10, high: 12, low: 9, close: 10 }), true);
  assert.equal(p.candles[0].volume, 5, "Price-only updates cannot erase known volume");
  assert.equal(context.mergeLiveCandle(p, { ...candle(110), volume: NaN }), false);
  assert.equal(context.mergeLiveCandle(p, { ...candle(110), volume: -1 }), false);
  assert.equal(context.mergeLiveCandle(p, { ...candle(110), volume: 0 }), true, "Zero volume is valid");

  let completeIndicators;
  let connections = 0;
  const noop = () => {};
  const loading = loadFunctions({
    performance, console, clearInterval: noop, clearIndicatorSeries: noop, clearSignalMarkers: noop, updateSymbolPickerButton: noop,
    clearBacktestMarkers: noop, historyLimitForPane: () => 2000, jumpPaneToNow: noop, updateTicker: noop,
    loadCandles: async () => ({ candles: [candle(100)] }),
    renderIndicators: () => new Promise((resolve) => { completeIndicators = resolve; }),
    renderSelectedStrategy: async () => {}, renderSignals: async () => {}, renderDataDiagnostics: noop,
    candleStatusText: () => "Loaded", connectBybit: () => connections++, connectHyperliquid: () => connections++,
  }, ["startPane"]);
  for (const disposed of [false, true]) {
    const p = pane(); p.presetSelect = { value: "none" };
    const pending = loading.startPane(p);
    await new Promise(setImmediate);
    p.requestId++; p.disposed = disposed;
    completeIndicators();
    await pending;
    assert.equal(connections, 0, "completed stale requests cannot reconnect");
  }

  const panes = [pane(), pane()];
  let drawingStops = 0, removals = 0;
  for (const p of panes) {
    p.drawingTools = { destroy: () => drawingStops++ };
    p.chart = { remove: () => { assert.equal(drawingStops, panes.length); removals++; } };
  }
  const cleanup = loadFunctions({
    panes, clearInterval: noop, clearTimeout: noop, cancelPaneRangeSync: noop, clearIndicatorSeries: noop,
  }, ["cleanupPanes"]);
  cleanup.cleanupPanes();
  assert.equal(removals, 2);
  assert.ok(panes.every((p) => p.disposed));

  const timerCallbacks = [];
  let finishStrategy;
  const watcher = loadFunctions({
    clearTimeout: noop,
    setTimeout: (callback) => { timerCallbacks.push(callback); return timerCallbacks.length; },
    renderSelectedStrategy: () => new Promise((resolve) => { finishStrategy = resolve; }),
  }, ["startStrategyWatcher"]);
  const watched = pane(); watched.presetSelect = { value: "EmaBounceV2" };
  watcher.startStrategyWatcher(watched);
  const running = timerCallbacks[0]();
  watched.disposed = true;
  finishStrategy();
  await running;
  assert.equal(timerCallbacks.length, 1, "strategy polling cannot restart after disposal");

  const responses = [], rows = [];
  const sidebar = loadFunctions({
    sidebarPaperRequestId: 0,
    activeSignalsList: {
      querySelectorAll: () => rows.map((html) => ({ remove: () => rows.splice(rows.indexOf(html), 1) })),
      insertAdjacentHTML: (_, html) => rows.push(html),
    },
    fetch: () => new Promise((resolve) => responses.push(resolve)),
    escapeHtml: (value) => String(value),
  }, ["refreshSidebarPaperStatus"]);
  const first = sidebar.refreshSidebarPaperStatus(), second = sidebar.refreshSidebarPaperStatus();
  responses[1]({ ok: true, json: async () => ({ openPositions: [] }) });
  await second;
  responses[0]({ ok: true, json: async () => ({ openPositions: [] }) });
  await first;
  assert.equal(rows.length, 1, "stale paper responses cannot duplicate sidebar rows");
  console.log("Chart lifecycle passed: stale feeds, invalid candles, async load completion and coordinated disposal.");
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
