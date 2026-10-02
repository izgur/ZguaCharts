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
  "RegimeFilteredTrendStrategy",
  "RobustTrendFollowerV1",
  "MtfPullback15mV1",
  "VwapReversion15mV1",
  "VwapReversion15mFastV1"
];

const SELECT_OPTIONS = {
  regimeMode: ["symbolFastTrend", "symbolTrend", "noRegime", "looseBtcBull"],
  indicatorFilterPreset: ["none", "trendQuality", "pullbackTiming", "breakoutQuality"],
  fillModel: ["next-open", "close", "conservative"],
  mtfEntryMode: ["pullback", "breakout", "either"],
  mtfDirection: ["long", "short", "both"],
  reversionDirection: ["long", "short", "both"]
};

const PARAM_LIBRARY = {
  accountEquity: 10000,
  riskPct: 0.005,
  maxOpenTrades: 1,
  maxNotional: 100000,
  maxNotionalPerTrade: 100000,
  makerFeePct: 0,
  takerFeePct: 0,
  feePct: 0,
  slippagePct: 0,
  slippageBps: 0,
  fillModel: "next-open",
  regimeMode: "looseBtcBull",
  btcTrendFilter: false,
  emaFast: 20,
  emaMid: 100,
  emaSlow: 50,
  emaTrend: 200,
  emaTrendLength: 200,
  emaBounceAtr: 0.8,
  emaMomentumSlow: 21,
  emaTrendFast: 50,
  rsiMin: 45,
  rsiMax: 70,
  rsiLimit: 32,
  rsiOversold: 35,
  rsiPullbackLevel: 45,
  rsiReclaimLevel: 50,
  useRsiFilter: true,
  atrMultiplier: 2.5,
  stopAtr: 2,
  takeProfitAtr: 3,
  trailingActivationAtr: 1.5,
  trailingAtr: 2,
  useDonchianTrail: true,
  cooldownBars: 0,
  minHoldBars: 1,
  volumeFilter: false,
  requireVolume: false,
  volumeMultiplier: 1,
  volumeMa: 20,
  useFundingFilter: false,
  maxFundingRate: 0.0005,
  useOpenInterestFilter: false,
  minOpenInterestChangePct: 0,
  requireBreakout: false,
  breakoutLookback: 20,
  donchianEntry: 20,
  donchianConfirmAlt: 55,
  donchianExit: 10,
  retestBars: 8,
  retestLookback: 8,
  retestAtr: 0.8,
  squeezeLookback: 80,
  squeezePercentile: 0.35,
  rangeLookback: 20,
  rangeSma: 20,
  closeHighPct: 0.7,
  indicatorFilterPreset: "none",
  useChoppinessFilter: false,
  maxChoppiness: 61.8,
  useVolumeFlowFilter: false,
  minCmf: 0,
  requireObvAboveMa: true,
  useAtrPercentileFilter: false,
  minAtrPctRank: 15,
  maxAtrPctRank: 85,
  useStochRsiFilter: false,
  stochRsiMin: 20,
  stochRsiMax: 85,
  useKeltnerSqueezeFilter: false,
  maxSqueezeRatio: 1,
  keltnerSqueezeLookback: 20,
  adxMin: 14,
  adxThreshold: 14,
  useAdx: false,
  relativeStrengthLookback: 24,
  rsLookback: 24,
  rsThreshold: 0,
  scoreThreshold: 55,
  minBodyPct: 0.35,
  shortMode: false,
  swingLeft: 3,
  swingRight: 2,
  goldenPocketTolerancePct: 0.35,
  requireAnchoredVwap: true,
  mtfRequire1h: true,
  mtfRequire4h: true,
  mtf1hTrendEma: 50,
  mtf4hTrendEma: 100,
  mtfMinRsi: 45,
  mtfDirection: "long",
  mtfEntryMode: "pullback",
  reversionDirection: "both",
  minVwapDistanceAtr: 0.8,
  maxAdx: 28,
  minChoppiness: 45,
  requireBbTouch: true,
  requireCmfConfirm: false,
  rsiOverbought: 68,
  stochRsiOversold: 20,
  stochRsiOverbought: 80
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
    emaMid: "EMA Mid",
    emaSlow: "EMA Slow",
    emaTrend: "EMA Trend",
    emaTrendLength: "EMA Trend Length",
    rsiMin: "RSI Min",
    rsiMax: "RSI Max",
    useRsiFilter: "Use RSI Filter",
    regimeMode: "Regime Mode",
    volumeFilter: "Volume Filter",
    volumeMultiplier: "Volume Multiplier",
    volumeMa: "Volume MA",
    indicatorFilterPreset: "Indicator Filter Preset",
    useChoppinessFilter: "Use Choppiness Filter",
    maxChoppiness: "Max Choppiness",
    useVolumeFlowFilter: "Use Volume Flow Filter",
    minCmf: "Min CMF",
    requireObvAboveMa: "Require OBV Above MA",
    useAtrPercentileFilter: "Use ATR Percentile Filter",
    minAtrPctRank: "Min ATR Rank",
    maxAtrPctRank: "Max ATR Rank",
    useStochRsiFilter: "Use Stoch RSI Filter",
    stochRsiMin: "Stoch RSI Min",
    stochRsiMax: "Stoch RSI Max",
    useKeltnerSqueezeFilter: "Use Keltner Squeeze Filter",
    maxSqueezeRatio: "Max Squeeze Ratio",
    keltnerSqueezeLookback: "Keltner Squeeze Lookback",
    useFundingFilter: "Use Funding Filter",
    maxFundingRate: "Max Funding Rate",
    useOpenInterestFilter: "Use OI Filter",
    minOpenInterestChangePct: "Min OI Change %",
    useDonchianTrail: "Use Donchian Trail",
    cooldownBars: "Cooldown Bars",
    minHoldBars: "Min Hold Bars",
    riskPct: "Risk %",
    maxOpenTrades: "Max Open Trades",
    maxNotional: "Max Notional",
    maxNotionalPerTrade: "Max Notional Per Trade",
    makerFeePct: "Maker Fee %",
    takerFeePct: "Taker Fee %",
    slippageBps: "Slippage Bps",
    fillModel: "Fill Model",
    mtfRequire1h: "Require 1h Trend",
    mtfRequire4h: "Require 4h Trend",
    mtf1hTrendEma: "1h Trend EMA",
    mtf4hTrendEma: "4h Trend EMA",
    mtfMinRsi: "MTF Min RSI",
    mtfDirection: "MTF Direction",
    mtfEntryMode: "MTF Entry Mode",
    reversionDirection: "Reversion Direction",
    minVwapDistanceAtr: "Min VWAP Distance ATR",
    maxAdx: "Max ADX",
    minChoppiness: "Min Choppiness",
    requireBbTouch: "Require BB Touch",
    requireCmfConfirm: "Require CMF Confirm",
    rsiOverbought: "RSI Overbought",
    stochRsiOversold: "Stoch RSI Oversold",
    stochRsiOverbought: "Stoch RSI Overbought"
  };
  return labels[key] || titleize(key);
}

function descriptionForParam(key) {
  const descriptions = {
    accountEquity: "Starting paper account size used by the strategy engine for position sizing.",
    atrMultiplier: "Multiplier applied to ATR for trend distance, stop distance, or breakout filtering depending on the strategy.",
    emaFast: "Fast exponential moving average length. Lower values react faster to price.",
    emaMid: "Mid-length EMA used by strategies that require a coin trend filter, commonly EMA100 on 1h.",
    emaSlow: "Slow exponential moving average length. Higher values make the trend filter slower.",
    emaTrend: "Trend EMA length used to decide whether price is in a larger trend.",
    emaTrendLength: "Trend EMA lookback length. Higher values require a longer trend to agree.",
    emaBounceAtr: "Allowed distance from EMA measured in ATR for bounce-style entries.",
    rsiMin: "Minimum RSI accepted for entry. Higher values require stronger momentum.",
    rsiMax: "Maximum RSI accepted for entry. Lower values avoid more overbought entries.",
    rsiPullbackLevel: "RSI level used to detect the pullback part of a bounce or reclaim setup.",
    rsiReclaimLevel: "RSI level that must be reclaimed before entry is allowed.",
    useRsiFilter: "Whether RSI rules are active for this strategy.",
    regimeMode: "Market regime filter mode. It controls whether BTC or symbol trend context is required.",
    volumeFilter: "Whether volume must pass the strategy filter before entry.",
    requireVolume: "Whether volume confirmation must pass before entry.",
    volumeMultiplier: "Multiplier above volume moving average required for volume confirmation.",
    volumeMa: "Volume moving-average length used by volume filters where supported.",
    indicatorFilterPreset: "Compact preset for free indicator filters: none, trend quality, pullback timing, or breakout quality.",
    useChoppinessFilter: "Whether entries should be blocked when the market is too sideways/choppy.",
    maxChoppiness: "Maximum Choppiness Index allowed. Lower values require cleaner trend conditions.",
    useVolumeFlowFilter: "Whether entries require Chaikin Money Flow and optional OBV confirmation.",
    minCmf: "Minimum Chaikin Money Flow value required when volume-flow filtering is enabled.",
    requireObvAboveMa: "Whether OBV must be above its moving average when volume-flow filtering is enabled.",
    useAtrPercentileFilter: "Whether entries require ATR percent rank to stay within configured volatility bounds.",
    minAtrPctRank: "Minimum ATR percent-rank allowed. Higher values avoid very quiet markets.",
    maxAtrPctRank: "Maximum ATR percent-rank allowed. Lower values avoid very hot volatility spikes.",
    useStochRsiFilter: "Whether entries require Stoch RSI timing to be inside the configured range.",
    stochRsiMin: "Minimum Stoch RSI K value accepted for entry timing.",
    stochRsiMax: "Maximum Stoch RSI K value accepted for entry timing.",
    useKeltnerSqueezeFilter: "Whether breakout entries require a recent Bollinger/Keltner squeeze.",
    maxSqueezeRatio: "Maximum Bollinger-width to Keltner-width ratio counted as a squeeze.",
    keltnerSqueezeLookback: "How many candles back to look for a valid Keltner squeeze.",
    useFundingFilter: "Whether entries are blocked when the latest known Bybit funding rate is above the configured maximum.",
    maxFundingRate: "Maximum allowed funding rate as a decimal. Example: 0.0005 means 0.05%.",
    useOpenInterestFilter: "Whether entries require open interest to be rising by at least the configured percentage.",
    minOpenInterestChangePct: "Minimum open-interest change percentage between Bybit open-interest points. Zero means flat or rising.",
    cooldownBars: "Minimum candles to wait after a trade before another entry can trigger.",
    minHoldBars: "Minimum candles a trade should remain open before normal exit checks.",
    riskPct: "Percent of account equity risked per trade in the sizing model.",
    maxOpenTrades: "Maximum simultaneous open trades allowed by the strategy runner.",
    maxNotional: "Maximum notional exposure allowed.",
    maxNotionalPerTrade: "Maximum position notional allowed for one trade.",
    makerFeePct: "Maker fee percentage used in cost modeling.",
    takerFeePct: "Taker fee percentage used in cost modeling.",
    slippageBps: "Slippage cost in basis points used in cost modeling.",
    stopAtr: "Stop-loss distance measured in ATR. Higher values give trades more room.",
    takeProfitAtr: "Take-profit distance measured in ATR. Higher values target larger moves.",
    trailingAtr: "Trailing stop distance measured in ATR after a trade moves favorably.",
    useDonchianTrail: "Whether the trailing stop should also respect the opposite-side Donchian channel.",
    fillModel: "Price fill assumption used by the backtest engine.",
    breakoutLookback: "Lookback window used to detect breakouts.",
    requireBreakout: "Whether a breakout confirmation is required before entry.",
    donchianEntry: "Donchian channel length used for breakout entries.",
    donchianConfirmAlt: "Alternate Donchian length used by close-confirm breakout logic.",
    donchianExit: "Donchian channel length used for exit signals.",
    retestBars: "Number of candles allowed for a breakout retest.",
    retestLookback: "How far back to search for the breakout before a retest.",
    retestAtr: "How close price must retest the breakout level, measured in ATR.",
    squeezeLookback: "Lookback window used to measure volatility squeeze.",
    squeezePercentile: "Percentile threshold for squeeze detection. Lower is stricter.",
    rangeLookback: "Lookback window used for range breakout checks.",
    rangeSma: "Average range length used by range-expansion checks.",
    closeHighPct: "How high in the candle range the close must be for expansion entries.",
    rangeLookback: "Lookback window used to measure range expansion.",
    adxMin: "Minimum ADX trend strength required for entry.",
    adxThreshold: "ADX threshold used by trend and breakout filters.",
    useAdx: "Whether ADX filtering is active where the strategy supports it.",
    relativeStrengthLookback: "Lookback window used to compare relative strength.",
    rsLookback: "Relative-strength lookback window.",
    rsThreshold: "Required return edge versus BTC over the relative-strength lookback.",
    scoreThreshold: "Composite indicator score required by momentum strategies.",
    minBodyPct: "Minimum candle body as a fraction of full candle range.",
    rsiLimit: "RSI threshold used by mean-reversion entries.",
    rsiOversold: "RSI oversold threshold used before reclaim entries.",
    shortMode: "Whether the strategy may consider short-side entries where supported.",
    swingLeft: "Left-side candle count for swing point detection.",
    swingRight: "Right-side candle count for swing point confirmation.",
    goldenPocketTolerancePct: "Allowed tolerance around Fibonacci golden-pocket pullbacks.",
    requireAnchoredVwap: "Whether anchored VWAP confirmation is required.",
    btcTrendFilter: "Whether BTC trend context must agree before entries are accepted.",
    mtfRequire1h: "Whether a closed 1h symbol trend must confirm before a 15m entry.",
    mtfRequire4h: "Whether a closed 4h symbol trend must confirm before a 15m entry.",
    mtf1hTrendEma: "Higher-timeframe EMA length used for the 1h confirmation.",
    mtf4hTrendEma: "Higher-timeframe EMA length used for the 4h confirmation.",
    mtfMinRsi: "Minimum higher-timeframe RSI required on the confirming frames.",
    mtfDirection: "Research direction for the MTF 15m setup: long, short, or both.",
    mtfEntryMode: "15m trigger mode after higher-timeframe confirmation: pullback, breakout, or either.",
    reversionDirection: "Research direction for VWAP reversion: long, short, or both.",
    minVwapDistanceAtr: "Minimum distance from VWAP, measured in ATR, before a reversion entry is allowed.",
    maxAdx: "Maximum ADX allowed for range mean-reversion entries.",
    minChoppiness: "Minimum Choppiness Index required; zero disables this range filter.",
    requireBbTouch: "Whether price must touch the outer Bollinger Band before fading back toward VWAP.",
    requireCmfConfirm: "Whether Chaikin Money Flow must confirm the reversion direction.",
    rsiOverbought: "RSI threshold used for short-side reversion timing.",
    stochRsiOversold: "Stoch RSI threshold used for long-side reversion timing.",
    stochRsiOverbought: "Stoch RSI threshold used for short-side reversion timing."
  };
  return descriptions[key] || `${labelForParam(key)} strategy parameter. Adjust only when you want this strategy rule to change.`;
}

function groupForParam(key) {
  const groups = {
    accountEquity: ["execution", "Execution / Costs"],
    riskPct: ["execution", "Execution / Costs"],
    maxOpenTrades: ["execution", "Execution / Costs"],
    maxNotional: ["execution", "Execution / Costs"],
    maxNotionalPerTrade: ["execution", "Execution / Costs"],
    makerFeePct: ["execution", "Execution / Costs"],
    takerFeePct: ["execution", "Execution / Costs"],
    feePct: ["execution", "Execution / Costs"],
    slippagePct: ["execution", "Execution / Costs"],
    slippageBps: ["execution", "Execution / Costs"],
    fillModel: ["execution", "Execution / Costs"],
    regimeMode: ["regime", "Regime Filter"],
    btcTrendFilter: ["regime", "Regime Filter"],
    emaFast: ["ema", "EMA"],
    emaMid: ["ema", "EMA"],
    emaSlow: ["ema", "EMA"],
    emaTrend: ["ema", "EMA"],
    emaTrendLength: ["ema", "EMA"],
    emaBounceAtr: ["ema", "EMA"],
    emaMomentumSlow: ["ema", "EMA"],
    emaTrendFast: ["ema", "EMA"],
    rsiMin: ["rsi", "RSI"],
    rsiMax: ["rsi", "RSI"],
    rsiLimit: ["rsi", "RSI"],
    rsiOversold: ["rsi", "RSI"],
    rsiPullbackLevel: ["rsi", "RSI"],
    rsiReclaimLevel: ["rsi", "RSI"],
    useRsiFilter: ["rsi", "RSI"],
    atrMultiplier: ["atr", "ATR / Volatility"],
    stopAtr: ["risk", "Stops / Exits"],
    takeProfitAtr: ["risk", "Stops / Exits"],
    trailingAtr: ["risk", "Stops / Exits"],
    useDonchianTrail: ["risk", "Stops / Exits"],
    cooldownBars: ["timing", "Timing"],
    minHoldBars: ["timing", "Timing"],
    volumeFilter: ["volume", "Volume"],
    requireVolume: ["volume", "Volume"],
    volumeMultiplier: ["volume", "Volume"],
    volumeMa: ["volume", "Volume"],
    indicatorFilterPreset: ["quality", "Market Quality"],
    useChoppinessFilter: ["quality", "Market Quality"],
    maxChoppiness: ["quality", "Market Quality"],
    useVolumeFlowFilter: ["quality", "Market Quality"],
    minCmf: ["quality", "Market Quality"],
    requireObvAboveMa: ["quality", "Market Quality"],
    useAtrPercentileFilter: ["volatility", "ATR / Volatility"],
    minAtrPctRank: ["volatility", "ATR / Volatility"],
    maxAtrPctRank: ["volatility", "ATR / Volatility"],
    useStochRsiFilter: ["rsi", "RSI"],
    stochRsiMin: ["rsi", "RSI"],
    stochRsiMax: ["rsi", "RSI"],
    useKeltnerSqueezeFilter: ["squeeze", "Squeeze"],
    maxSqueezeRatio: ["squeeze", "Squeeze"],
    keltnerSqueezeLookback: ["squeeze", "Squeeze"],
    useFundingFilter: ["derivatives", "Funding / OI"],
    maxFundingRate: ["derivatives", "Funding / OI"],
    useOpenInterestFilter: ["derivatives", "Funding / OI"],
    minOpenInterestChangePct: ["derivatives", "Funding / OI"],
    breakoutLookback: ["breakout", "Breakout"],
    requireBreakout: ["breakout", "Breakout"],
    donchianEntry: ["donchian", "Donchian"],
    donchianExit: ["donchian", "Donchian"],
    retestBars: ["retest", "Retest"],
    retestLookback: ["retest", "Retest"],
    retestAtr: ["retest", "Retest"],
    rangeLookback: ["range", "Range Expansion"],
    rangeSma: ["range", "Range Expansion"],
    closeHighPct: ["range", "Range Expansion"],
    squeezeLookback: ["squeeze", "Squeeze"],
    squeezePercentile: ["squeeze", "Squeeze"],
    adxMin: ["adx", "ADX"],
    adxThreshold: ["adx", "ADX"],
    relativeStrengthLookback: ["relativeStrength", "Relative Strength"],
    rsLookback: ["relativeStrength", "Relative Strength"],
    rsThreshold: ["relativeStrength", "Relative Strength"],
    shortMode: ["direction", "Direction"],
    mtfRequire1h: ["regime", "Regime Filter"],
    mtfRequire4h: ["regime", "Regime Filter"],
    mtf1hTrendEma: ["regime", "Regime Filter"],
    mtf4hTrendEma: ["regime", "Regime Filter"],
    mtfMinRsi: ["regime", "Regime Filter"],
    mtfDirection: ["direction", "Direction"],
    mtfEntryMode: ["timing", "Timing"],
    reversionDirection: ["direction", "Direction"],
    minVwapDistanceAtr: ["vwap", "VWAP"],
    maxAdx: ["adx", "ADX"],
    minChoppiness: ["quality", "Market Quality"],
    requireBbTouch: ["squeeze", "Squeeze"],
    requireCmfConfirm: ["quality", "Market Quality"],
    rsiOverbought: ["rsi", "RSI"],
    stochRsiOversold: ["rsi", "RSI"],
    stochRsiOverbought: ["rsi", "RSI"]
  };
  return groups[key] || ["other", "Other Rules"];
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
    "SimpleAtrTrendV2",
    "MtfPullback15mV1",
    "VwapReversion15mV1",
    "VwapReversion15mFastV1",
    "RobustTrendFollowerV1"
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

function schemaEntry(key, value, gridValues, optional) {
  const group = groupForParam(key);
  function withGroup(entry) {
    return Object.assign(entry, { group: group[0], groupLabel: group[1], indicator: group[0], indicatorLabel: group[1], optional: !!optional, defaultValue: PARAM_LIBRARY[key] });
  }
  if (SELECT_OPTIONS[key]) {
    return withGroup({ type: "select", options: SELECT_OPTIONS[key], label: labelForParam(key), description: descriptionForParam(key) });
  }
  if (Array.isArray(gridValues) && gridValues.some(function (item) { return typeof item === "string"; })) {
    return withGroup({ type: "select", options: gridValues, label: labelForParam(key), description: descriptionForParam(key) });
  }
  if (typeof value === "boolean" || (Array.isArray(gridValues) && gridValues.every(function (item) { return typeof item === "boolean"; }))) {
    return withGroup({ type: "boolean", label: labelForParam(key), description: descriptionForParam(key) });
  }
  if (typeof value === "number" || (Array.isArray(gridValues) && gridValues.every(function (item) { return typeof item === "number"; }))) {
    const numeric = (Array.isArray(gridValues) ? gridValues : [value]).filter(function (item) { return typeof item === "number" && Number.isFinite(item); });
    const min = numeric.length ? Math.min.apply(null, numeric) : undefined;
    const max = numeric.length ? Math.max.apply(null, numeric) : undefined;
    const step = numeric.some(function (item) { return Math.abs(item - Math.round(item)) > 0.000001; }) ? 0.01 : 1;
    const entry = withGroup({ type: "number", step: step, label: labelForParam(key), description: descriptionForParam(key) });
    if (min !== undefined) entry.min = Math.min(0, min);
    if (max !== undefined) entry.max = Math.max(max, Number(value || 0), 1);
    return entry;
  }
  return withGroup({ type: "text", label: labelForParam(key), description: descriptionForParam(key) });
}

function buildParamSchema(defaultParams, gridParams) {
  const schema = {};
  const strategyKeys = new Set(Object.keys(defaultParams).concat(Object.keys(gridParams || {})));
  const keys = Array.from(new Set(Object.keys(PARAM_LIBRARY).concat(Object.keys(defaultParams)).concat(Object.keys(gridParams || {})))).sort();
  keys.forEach(function (key) {
    var value = defaultParams[key] !== undefined ? defaultParams[key] : PARAM_LIBRARY[key];
    schema[key] = schemaEntry(key, value, gridParams ? gridParams[key] : undefined, !strategyKeys.has(key));
  });
  return schema;
}

function indicatorGroupsFromSchema(schema) {
  const groups = {};
  Object.entries(schema || {}).forEach(function ([key, entry]) {
    const id = entry.group || entry.indicator || "other";
    groups[id] = groups[id] || { id: id, label: entry.groupLabel || entry.indicatorLabel || titleize(id), params: [] };
    groups[id].params.push(key);
  });
  return Object.values(groups).sort(function (a, b) {
    const order = ["execution", "regime", "ema", "rsi", "atr", "risk", "timing", "volume", "derivatives", "breakout", "donchian", "retest", "range", "squeeze", "adx", "relativeStrength", "direction", "other"];
    const ai = order.indexOf(a.id);
    const bi = order.indexOf(b.id);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });
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
  const paramSchema = buildParamSchema(activeParams ? Object.assign({}, gridSeedParams, activeParams) : gridSeedParams, grid ? grid.params : {});
  return {
    name: name,
    label: titleize(name),
    description: grid ? (grid.notes || []).join(" ") : "Registered manual backtest strategy.",
    supported: true,
    defaultParams: defaultParams,
    presets: presets,
    paramSchema: paramSchema,
    indicatorGroups: indicatorGroupsFromSchema(paramSchema),
    optimizerGrid: grid ? {
      gridName: grid.gridName || grid.humanName || name,
      humanName: grid.humanName || grid.gridName || name,
      params: grid.params || {},
      maxCombinations: grid.maxCombinations || 0,
      notes: grid.notes || [],
      riskLevel: grid.riskLevel || "unknown"
    } : null,
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
