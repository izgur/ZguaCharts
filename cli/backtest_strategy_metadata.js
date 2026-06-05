const strategies = require("../core/strategies");
const optimizer = require("../core/optimizer");
const regimeTrend = require("../core/backtest/regimeTrend");
const runtime = require("./runtime");

const MANUAL_STRATEGIES = [
  "SimpleAtrTrendV2",
  "ConservativeTrend",
  "ConservativeTrendLoose",
  "EmaBounceV2",
  "RelativeStrengthV2",
  "PullbackReclaimV2",
  "RegimePullbackTrend",
  "MeanReversion",
  "MeanReversionInBullRegime",
  "PullbackTrend",
  "BreakoutRetestV2",
  "RangeExpansionV2",
  "RegimeFilteredTrendStrategy"
];

const SELECT_OPTIONS = {
  regimeMode: ["symbolFastTrend", "symbolTrend", "noRegime", "looseBtcBull"],
  fillModel: ["next-open", "close", "conservative"]
};

function titleize(name) {
  return String(name || "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/\b\w/g, function (char) { return char.toUpperCase(); });
}

function labelForParam(key) {
  const labels = {
    atrMultiplier: "ATR Multiplier",
    emaFast: "EMA Fast",
    emaSlow: "EMA Slow",
    emaTrend: "EMA Trend",
    emaTrendLength: "EMA Trend Length",
    rsiMin: "RSI Min",
    rsiMax: "RSI Max",
    useRsiFilter: "Use RSI Filter",
    regimeMode: "Regime Mode",
    volumeFilter: "Volume Filter",
    cooldownBars: "Cooldown Bars",
    minHoldBars: "Min Hold Bars",
    riskPct: "Risk %",
    maxOpenTrades: "Max Open Trades",
    maxNotional: "Max Notional",
    maxNotionalPerTrade: "Max Notional Per Trade",
    makerFeePct: "Maker Fee %",
    takerFeePct: "Taker Fee %",
    slippageBps: "Slippage Bps",
    fillModel: "Fill Model"
  };
  return labels[key] || titleize(key);
}

function isRegimeRunner(name) {
  return [
    "RegimeFilteredTrendStrategy",
    "RegimeDonchian20",
    "RegimeDonchianCloseConfirm",
    "RegimePullbackTrend",
    "EmaPullbackContinuation",
    "TrendBreakoutRetest",
    "VolatilitySqueezeBreakout",
    "MeanReversionInBullRegime",
    "MomentumContinuation",
    "PullbackReclaimV2",
    "EmaBounceV2",
    "BreakoutRetestV2",
    "RangeExpansionV2",
    "RelativeStrengthV2",
    "SimpleAtrTrendV2"
  ].includes(name);
}

function defaultParamsFor(name) {
  try {
    const strategy = strategies.getStrategy(name);
    const params = Object.assign({}, strategy.params || {});
    if (Object.keys(params).length) return params;
  } catch (error) {
    // Fall through to regime defaults for strategy names that are served by
    // the shared regime runner.
  }
  if (isRegimeRunner(name)) return regimeTrend.defaultParams(name);
  return {};
}

function firstGridParams(grid) {
  const params = {};
  Object.entries((grid && grid.params) || {}).forEach(function ([key, values]) {
    if (Array.isArray(values) && values.length) params[key] = values[0];
  });
  return params;
}

function schemaEntry(key, value, gridValues) {
  if (SELECT_OPTIONS[key]) {
    return { type: "select", options: SELECT_OPTIONS[key], label: labelForParam(key) };
  }
  if (Array.isArray(gridValues) && gridValues.some(function (item) { return typeof item === "string"; })) {
    return { type: "select", options: gridValues, label: labelForParam(key) };
  }
  if (typeof value === "boolean" || (Array.isArray(gridValues) && gridValues.every(function (item) { return typeof item === "boolean"; }))) {
    return { type: "boolean", label: labelForParam(key) };
  }
  if (typeof value === "number" || (Array.isArray(gridValues) && gridValues.every(function (item) { return typeof item === "number"; }))) {
    const numeric = (Array.isArray(gridValues) ? gridValues : [value]).filter(function (item) { return typeof item === "number" && Number.isFinite(item); });
    const min = numeric.length ? Math.min.apply(null, numeric) : undefined;
    const max = numeric.length ? Math.max.apply(null, numeric) : undefined;
    const step = numeric.some(function (item) { return Math.abs(item - Math.round(item)) > 0.000001; }) ? 0.01 : 1;
    const entry = { type: "number", step: step, label: labelForParam(key) };
    if (min !== undefined) entry.min = Math.min(0, min);
    if (max !== undefined) entry.max = Math.max(max, Number(value || 0), 1);
    return entry;
  }
  return { type: "text", label: labelForParam(key) };
}

function buildParamSchema(defaultParams, gridParams) {
  const schema = {};
  const keys = Array.from(new Set(Object.keys(defaultParams).concat(Object.keys(gridParams || {})))).sort();
  keys.forEach(function (key) {
    schema[key] = schemaEntry(key, defaultParams[key], gridParams ? gridParams[key] : undefined);
  });
  return schema;
}

function strategyInfo(name, activeCandidate) {
  const catalog = optimizer.optimizerGridCatalog();
  const grid = catalog[name];
  const defaultParams = Object.assign({}, defaultParamsFor(name));
  const gridSeedParams = Object.assign({}, defaultParams, firstGridParams(grid));
  const activeParams = activeCandidate && activeCandidate.strategy === name
    ? Object.assign({}, activeCandidate.params || {})
    : null;
  if (activeParams) {
    ["accountEquity", "riskPct", "maxOpenTrades", "maxNotionalPerTrade", "makerFeePct", "takerFeePct", "slippageBps", "fillModel"].forEach(function (key) {
      if (activeCandidate[key] !== undefined && activeParams[key] === undefined) activeParams[key] = activeCandidate[key];
    });
  }
  const presets = [
    { name: "default", label: "Default", params: defaultParams }
  ];
  if (activeParams) presets.push({ name: "activeCandidate", label: "Active Candidate", params: activeParams });
  if (grid) presets.push({ name: "gridSeed", label: grid.humanName || grid.gridName || "Grid Seed", params: gridSeedParams });
  return {
    name: name,
    label: titleize(name),
    description: grid ? (grid.notes || []).join(" ") : "Registered manual backtest strategy.",
    supported: true,
    defaultParams: defaultParams,
    presets: presets,
    paramSchema: buildParamSchema(activeParams ? Object.assign({}, gridSeedParams, activeParams) : gridSeedParams, grid ? grid.params : {}),
    warnings: grid ? [] : ["No optimizer grid metadata is available; showing engine defaults only."]
  };
}

runtime.readStdinIfPresent({ waitForEnd: true }).then(function (raw) {
  const input = raw.trim() ? JSON.parse(raw) : {};
  const activeCandidate = input.activeCandidate || null;
  const registryNames = strategies.listStrategies().map(function (strategy) { return strategy.name; });
  const catalogNames = Object.keys(optimizer.optimizerGridCatalog()).filter(function (name) { return name !== "default_fallback"; });
  const names = Array.from(new Set(MANUAL_STRATEGIES.concat(registryNames).concat(catalogNames)))
    .filter(function (name) { return name && name !== "AlwaysLongTest"; })
    .sort();
  const strategiesPayload = names.map(function (name) {
    try {
      return strategyInfo(name, activeCandidate);
    } catch (error) {
      return {
        name: name,
        label: titleize(name),
        description: "Strategy is registered but metadata could not be loaded.",
        supported: false,
        defaultParams: {},
        presets: [],
        paramSchema: {},
        warnings: [error.message]
      };
    }
  });
  process.stdout.write(JSON.stringify({ ok: true, strategies: strategiesPayload, warnings: [] }, null, 2));
  runtime.finishCli({ forceExit: true, exitCode: 0 });
}).catch(function (error) {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ forceExit: true, exitCode: 1 });
});
