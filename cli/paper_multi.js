const fs = require("fs");
const path = require("path");

const paper = require("../core/paper");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

const ROOT = process.cwd();
const DEFAULT_MULTI_CONFIG = path.join(ROOT, "config", "local", "paper-candidates.json");
const DEFAULT_CONFIG_DIR = path.join(ROOT, "config", "local", "paper-candidates");
const DEFAULT_STATE_DIR = path.join(ROOT, "data", "paper-multi");
const DEFAULT_REPORT_DIR = path.join(ROOT, "reports", "paper-multi");
const frameCache = new Map();

function readJson(file, fallback) {
  try {
    if (!fs.existsSync(file)) return fallback;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function safeId(value) {
  return String(value || "candidate").replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "") || "candidate";
}

function stableHash(value) {
  return require("crypto").createHash("sha1").update(JSON.stringify(value)).digest("hex").slice(0, 12);
}

function defaultExecutionTimeframe(timeframe) {
  const interval = String(timeframe || "1h").toLowerCase();
  return require("../core/data").intervalToMs(interval) > require("../core/data").intervalToMs("5m") ? "5m" : interval;
}

function candidateToConfig(multi, candidate) {
  const symbol = String(candidate.symbol || multi.symbol || "BTCUSDT").toUpperCase();
  const timeframe = String(candidate.timeframe || candidate.interval || multi.timeframe || "4h").toLowerCase();
  const strategy = candidate.strategy || candidate.preset;
  const params = Object.assign({}, candidate.params || {});
  if (!params.regimeMode && (candidate.regimeMode || multi.regimeMode)) {
    params.regimeMode = candidate.regimeMode || multi.regimeMode;
  }
  const riskPct = Number(candidate.riskPct || params.riskPct || multi.riskPct || 0.005);
  params.riskPct = riskPct;
  const id = safeId(candidate.id || [strategy, symbol, timeframe, stableHash(params)].join("-"));
  const paramsHash = stableHash({ strategy, regimeMode: params.regimeMode, params });
  const candidateKey = candidate.candidateKey || ["multi-paper-v1", id, strategy, symbol, timeframe, paramsHash].join("|");
  return {
    enabled: candidate.enabled !== false,
    _replaceParams: true,
    paperEnabled: candidate.enabled !== false,
    mode: "PAPER_ONLY",
    source: candidate.source || multi.source || "bybit",
    strategy,
    preset: candidate.preset || strategy,
    symbol,
    timeframe,
    executionTimeframe: candidate.executionTimeframe || multi.executionTimeframe || defaultExecutionTimeframe(timeframe),
    label: candidate.label || id,
    paramsHash,
    candidateKey,
    candidateIdentity: {
      candidateKey,
      label: candidate.label || id,
      paramsHash,
      strategy,
      symbol,
      timeframe
    },
    params,
    symbols: [{ symbol, interval: timeframe, mode: "active" }],
    fillModel: candidate.fillModel || multi.fillModel || "next-open",
    makerFeePct: Number(candidate.makerFeePct ?? multi.makerFeePct ?? 0.02),
    takerFeePct: Number(candidate.takerFeePct ?? multi.takerFeePct ?? 0.055),
    slippageBps: Number(candidate.slippageBps ?? multi.slippageBps ?? 2),
    accountEquity: Number(candidate.accountEquity ?? multi.accountEquity ?? 10000),
    riskPct,
    maxPositionPct: Number(candidate.maxPositionPct ?? multi.maxPositionPct ?? 10),
    maxDailyLossPct: Number(candidate.maxDailyLossPct ?? multi.maxDailyLossPct ?? 2),
    maxOpenTrades: Number(candidate.maxOpenTrades ?? multi.maxOpenTrades ?? 1),
    maxNotionalPerTrade: Number(candidate.maxNotionalPerTrade ?? multi.maxNotionalPerTrade ?? 100000),
    realTradingEnabled: false,
    exchangeOrders: false,
    liveOrdersTouched: false,
    requireManualConfirmation: true,
    maxOnePosition: true,
    noLiveOrderFunctions: true,
    regimeMode: params.regimeMode
  };
}

function validatePaperOnly(config) {
  const blockers = [];
  if (!config.enabled) blockers.push("candidate disabled");
  if (config.mode !== "PAPER_ONLY") blockers.push("mode must be PAPER_ONLY");
  if (config.realTradingEnabled) blockers.push("realTradingEnabled must be false");
  if (config.exchangeOrders) blockers.push("exchangeOrders must be false");
  if (config.liveOrdersTouched) blockers.push("liveOrdersTouched must be false");
  if (!config.strategy) blockers.push("strategy is required");
  if (!config.symbol || !config.timeframe) blockers.push("symbol/timeframe are required");
  return blockers;
}

function pathsFor(id) {
  const clean = safeId(id);
  return {
    configPath: path.join(DEFAULT_CONFIG_DIR, `${clean}.json`),
    statePath: path.join(DEFAULT_STATE_DIR, `${clean}-state.json`),
    reportDir: path.join(DEFAULT_REPORT_DIR, clean)
  };
}

function writeRuntimeConfig(config, configPath) {
  ensureDir(path.dirname(configPath));
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");
}

function compactStatus(id, config, status) {
  return {
    id,
    label: config.label,
    enabled: status.enabled,
    strategy: config.strategy,
    symbol: config.symbol,
    timeframe: config.timeframe,
    paramsHash: config.paramsHash,
    riskPct: config.riskPct,
    openPositions: (status.openPositions || []).length,
    closedTradesCount: status.closedTradesCount,
    equity: status.equity,
    realizedPnL: status.realizedPnL,
    latestEvents: (status.latestEvents || []).length,
    lastProcessedCandle: status.lastProcessedCandle,
    warnings: status.warnings || []
  };
}

async function runForCandidate(command, multi, candidate, frameCache) {
  const config = candidateToConfig(multi, candidate);
  const id = safeId(candidate.id || config.candidateKey);
  const blockers = validatePaperOnly(config);
  const paths = pathsFor(id);
  if (blockers.length) {
    return { id, ok: false, blockers, config: { strategy: config.strategy, symbol: config.symbol, timeframe: config.timeframe } };
  }
  writeRuntimeConfig(config, paths.configPath);
  ensureDir(path.dirname(paths.statePath));
  ensureDir(paths.reportDir);
  if (command === "init") {
    const result = await paper.initializePaper(paths);
    return { id, ok: true, command, paths, result };
  }
  if (command === "tick" || command === "dry-run") {
    const result = await paper.runPaperTick({
      ...paths,
      frameCache,
      refreshFirst: args["no-refresh"] !== true && (args["refresh-first"] === true || args.refresh === true || args.command === "watch"),
      dryRun: command === "dry-run",
      allowStale: args["allow-stale"] === true
    });
    return { id, ok: true, command, paths, result };
  }
  const status = paper.getPaperStatus(paths);
  return { id, ok: true, command: "status", paths, status: compactStatus(id, config, status) };
}

function loadMultiConfig() {
  const file = args.config || DEFAULT_MULTI_CONFIG;
  const multi = readJson(file, {});
  const candidates = Array.isArray(multi.candidates) ? multi.candidates : [];
  return { file, multi, candidates: candidates.filter((candidate) => candidate && candidate.enabled !== false) };
}

async function runCommand(command) {
  const loaded = loadMultiConfig();
  const rows = await Promise.all(loaded.candidates.map((candidate) => runForCandidate(command, loaded.multi, candidate, frameCache)));
  return {
    ok: rows.every((row) => row.ok),
    command,
    configPath: path.relative(ROOT, loaded.file),
    candidateCount: rows.length,
    paperOnly: true,
    realTradingEnabled: false,
    liveOrdersTouched: false,
    rows
  };
}

async function runLoop() {
  const iterations = Math.max(1, Number(args.iterations || args["max-iterations"] || 288));
  const intervalMs = Math.max(0.1, Number(args["interval-minutes"] || 5)) * 60 * 1000;
  const logFile = path.resolve(ROOT, args.log || "reports/paper-multi/watchdog.jsonl");
  ensureDir(path.dirname(logFile));
  let last = null;
  for (let i = 1; i <= iterations; i += 1) {
    const payload = await runCommand("tick");
    const event = {
      type: "multi-paper-iteration",
      iteration: i,
      timestamp: new Date().toISOString(),
      ok: payload.ok,
      candidateCount: payload.candidateCount,
      rows: payload.rows.map((row) => ({
        id: row.id,
        ok: row.ok,
        status: row.result && row.result.status,
        events: row.result && row.result.events,
        processedCandles: row.result && row.result.processedCandles,
        warnings: row.result && row.result.warnings
      }))
    };
    fs.appendFileSync(logFile, JSON.stringify(event) + "\n");
    last = event;
    if (!payload.ok) break;
    if (i < iterations) await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return { ok: last ? last.ok : false, command: "watch", logFile: path.relative(ROOT, logFile), last };
}

const command = args._ || args.command || process.argv.slice(2).find((token) => token && token.indexOf("--") !== 0) || "status";
args.command = command;

(command === "watch" ? runLoop() : runCommand(command))
  .then((result) => {
    process.stdout.write(JSON.stringify(result, null, 2));
    runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: result.ok ? 0 : 1 });
  })
  .catch((error) => {
    process.stderr.write(error.stack || error.message);
    runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
  });
