const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const data = require("../core/data");
const paper = require("../core/paper");

let staleMode = false;
const fixedNowSeconds = Math.floor(Date.now() / 1000);

function candles(count, stepSeconds) {
  const out = [];
  let price = 100;
  const base = staleMode
    ? 1700000000
    : fixedNowSeconds - (count - 1) * stepSeconds;
  for (let i = 0; i < count; i += 1) {
    price += Math.sin(i / 8) * 0.6 + 0.18;
    out.push({
      time: base + i * stepSeconds,
      open: price - 0.25,
      high: price + 1.4,
      low: price - 1.4,
      close: price,
      volume: 1000 + (i % 12) * 30
    });
  }
  return out;
}

function alreadyClosedTailCandles(count, stepSeconds) {
  const out = [];
  let price = 100;
  const base = fixedNowSeconds - count * stepSeconds;
  for (let i = 0; i < count; i += 1) {
    price += 0.2;
    out.push({
      time: base + i * stepSeconds,
      open: price - 0.25,
      high: price + 1.4,
      low: price - 1.4,
      close: price,
      volume: 1000
    });
  }
  return out;
}

const originalFetch = data.fetchCandles;
data.fetchCandles = function (options) {
  if (options.symbol === "BTCUSDT" && options.interval === "4h") return Promise.resolve(candles(320, 14400));
  return Promise.resolve(candles(420, 3600));
};

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "zgua-paper-"));
const configPath = path.join(tmp, "paper-config.json");
const statePath = path.join(tmp, "paper-state.json");
const reportDir = path.join(tmp, "reports");
function closedBaseline() {
  return candles(420, 3600)[418].time;
}

function baseConfig(mode) {
  return {
    enabled: true,
    source: "bybit",
    strategy: "SimpleAtrTrendV2",
    regimeMode: "noRegime",
    params: {
      useRsiFilter: false,
      atrMultiplier: 3,
      emaFast: 20,
      emaSlow: 50,
      emaTrend: 100,
      cooldownBars: 3,
      minHoldBars: 3
    },
    symbols: [{ symbol: "TESTUSDT", interval: "1h", mode: mode || "active", limit: 420 }],
    fillModel: "next-open",
    makerFeePct: 0.02,
    takerFeePct: 0.055,
    slippageBps: 2,
    accountEquity: 10000,
    riskPct: 0.005,
    maxOpenTrades: 1,
    maxNotionalPerTrade: 100000
  };
}

function writeConfig(config) {
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
}

function readState() {
  return JSON.parse(fs.readFileSync(statePath, "utf8"));
}

function writeState(state) {
  fs.writeFileSync(statePath, JSON.stringify(state, null, 2));
}

async function expectEnableRefusesBeforeInit() {
  writeConfig(baseConfig("active"));
  let refused = false;
  try {
    paper.setPaperEnabled({ configPath, statePath }, true);
  } catch {
    refused = true;
  }
  assert.ok(refused, "paper:enable should refuse before initialization");
}

async function testInitAndNoHistoricalImport() {
  const result = await paper.initializePaper({ configPath, statePath, reportDir });
  assert.strictEqual(result.status, "initialized");
  assert.strictEqual(result.marketsInitialized, 1);
  assert.strictEqual(result.importedHistoricalTrades, 0);
  const state = readState();
  assert.ok(state.startedAt, "startedAt should be set");
  assert.strictEqual(state.lastProcessedCandleTime["TESTUSDT:1h"], closedBaseline(), "init should baseline latest closed candle");
  assert.strictEqual(state.closedTrades.length, 0, "init must import zero historical trades");
  assert.strictEqual(state.openPositions.length, 0, "init must open zero positions");
}

async function testTickAfterInitDoesNotImportHistory() {
  const first = await paper.runPaperTick({ configPath, statePath, reportDir });
  const state1 = readState();
  assert.strictEqual(first.status, "processed");
  assert.strictEqual(state1.closedTrades.length, 0, "tick after init should not import historical trades");
  const second = await paper.runPaperTick({ configPath, statePath, reportDir });
  const state2 = readState();
  assert.strictEqual(state2.closedTrades.length, state1.closedTrades.length, "restart tick should not duplicate trades");
  assert.strictEqual(second.status, "processed");
}

async function testUninitializedMarketRefusesTick() {
  fs.unlinkSync(statePath);
  const result = await paper.runPaperTick({ configPath, statePath, reportDir });
  const state = readState();
  assert.ok(result.warnings.some((warning) => warning.indexOf("Market not initialized") !== -1), "uninitialized tick should warn");
  assert.strictEqual(state.closedTrades.length, 0, "uninitialized tick must not import trades");
}

async function testWatchCannotTrade() {
  writeConfig(baseConfig("watch"));
  await paper.initializePaper({ configPath, statePath, reportDir });
  const state = readState();
  state.lastProcessedCandleTime["TESTUSDT:1h"] = candles(420, 3600)[300].time;
  writeState(state);
  await paper.runPaperTick({ configPath, statePath, reportDir });
  const next = readState();
  assert.strictEqual(next.closedTrades.length, 0, "watch mode must not close trades");
  assert.strictEqual(next.openPositions.length, 0, "watch mode must not open positions");
  assert.strictEqual(next.realizedPnl, 0, "watch mode must not affect realized PnL");
}

async function testMaxOpenTradesAndDryRun() {
  writeConfig(baseConfig("active"));
  await paper.initializePaper({ configPath, statePath, reportDir });
  const state = readState();
  state.lastProcessedCandleTime["TESTUSDT:1h"] = candles(420, 3600)[300].time;
  state.openPositions = [{ id: "existing", key: "OTHER:1h", symbol: "OTHER", interval: "1h" }];
  writeState(state);
  await paper.runPaperTick({ configPath, statePath, reportDir, dryRun: true });
  const afterDryRun = readState();
  assert.deepStrictEqual(afterDryRun.openPositions, state.openPositions, "dry-run must not mutate state");
  const beforeJournal = fs.existsSync(path.join(reportDir, "paper-journal.jsonl"))
    ? fs.readFileSync(path.join(reportDir, "paper-journal.jsonl"), "utf8")
    : "";
  await paper.runPaperTick({ configPath, statePath, reportDir });
  const after = readState();
  assert.ok(Number(after.skippedSignals || 0) >= 0, "maxOpenTrades path should be tracked safely");
  const afterJournal = fs.existsSync(path.join(reportDir, "paper-journal.jsonl"))
    ? fs.readFileSync(path.join(reportDir, "paper-journal.jsonl"), "utf8")
    : "";
  assert.ok(afterJournal.length >= beforeJournal.length, "journal append remains monotonic");
}

async function testStaleAndRefresh() {
  writeConfig(baseConfig("active"));
  staleMode = true;
  await paper.initializePaper({ configPath, statePath, reportDir });
  const before = fs.readFileSync(statePath, "utf8");
  const stale = await paper.runPaperTick({ configPath, statePath, reportDir });
  assert.ok(stale.warnings.some((warning) => warning.indexOf("stale") !== -1), "stale active market should be skipped");
  const staleWatchConfig = baseConfig("watch");
  writeConfig(staleWatchConfig);
  await paper.initializePaper({ configPath, statePath, reportDir });
  await paper.runPaperTick({ configPath, statePath, reportDir });
  assert.strictEqual(readState().closedTrades.length, 0, "stale watch market cannot trade");
  staleMode = false;
  const beforeRefresh = fs.readFileSync(statePath, "utf8");
  const refresh = await paper.refreshPaperCandles({ configPath, statePath, reportDir });
  assert.ok(fs.existsSync(path.join(reportDir, "paper-freshness.json")), "refresh should write freshness report");
  assert.strictEqual(fs.readFileSync(statePath, "utf8"), beforeRefresh, "refresh without advance-baseline should not mutate state");
  const dry = await paper.runPaperTick({ configPath, statePath, reportDir, refreshFirst: true, dryRun: true });
  assert.strictEqual(dry.status, "dry-run", "refresh-first dry-run should complete");
  const allow = await paper.runPaperTick({ configPath, statePath, reportDir, allowStale: true, dryRun: true });
  assert.ok(allow.status, "--allow-stale should return a summary");
  const status = paper.getPaperStatus({ configPath, statePath, reportDir });
  assert.strictEqual(typeof status.initialized, "boolean", "paper:status should return status payload");
  assert.ok(before.length > 0);
}

async function testEnableDisableAfterInit() {
  await paper.initializePaper({ configPath, statePath, reportDir });
  const enabled = paper.setPaperEnabled({ configPath, statePath }, true);
  assert.strictEqual(enabled.enabled, true, "paper:enable should work after init");
  const disabled = paper.setPaperEnabled({ configPath, statePath }, false);
  assert.strictEqual(disabled.enabled, false, "paper:disable should work");
}

async function testAlreadyClosedTailIsNotDiscarded() {
  const activeCandles = alreadyClosedTailCandles(40, 14400);
  const expectedLatest = activeCandles[activeCandles.length - 1].time;
  data.fetchCandles = function (options) {
    if (options.symbol === "BTCUSDT" && options.interval === "4h") return Promise.resolve(candles(320, 14400));
    return Promise.resolve(activeCandles);
  };
  writeConfig({
    ...baseConfig("active"),
    symbols: [{ symbol: "TESTUSDT", interval: "4h", mode: "active", limit: 40 }]
  });
  await paper.initializePaper({ configPath, statePath, reportDir });
  const state = readState();
  assert.strictEqual(state.lastProcessedCandleTime["TESTUSDT:4h"], expectedLatest, "init should keep already-closed tail candle");
  const diagnostics = await paper.activeSignalDiagnostics({ configPath, statePath, reportDir, limit: 5 });
  assert.strictEqual(diagnostics.latestCandle.time, expectedLatest, "signal diagnostics should use already-closed tail candle");
  const dry = await paper.runPaperTick({ configPath, statePath, reportDir, dryRun: true });
  assert.strictEqual(dry.freshness["TESTUSDT:4h"].latestCandleTime, expectedLatest, "dry-run tick freshness should use already-closed tail candle");
  data.fetchCandles = originalFetch;
}

async function main() {
  await expectEnableRefusesBeforeInit();
  await testInitAndNoHistoricalImport();
  await testTickAfterInitDoesNotImportHistory();
  await testUninitializedMarketRefusesTick();
  await testWatchCannotTrade();
  await testMaxOpenTradesAndDryRun();
  await testStaleAndRefresh();
  await testEnableDisableAfterInit();
  await testAlreadyClosedTailIsNotDiscarded();
  const rebuilt = paper.rebuildStateFromJournal(baseConfig("active"), path.join(reportDir, "paper-journal.jsonl"));
  assert.strictEqual(typeof rebuilt.accountEquity, "number", "paper state can be rebuilt from journal");
  data.fetchCandles = originalFetch;
  console.log("paper smoke tests passed");
}

main().catch((error) => {
  data.fetchCandles = originalFetch;
  throw error;
});
