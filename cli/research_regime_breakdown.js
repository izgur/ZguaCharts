const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function percentile(sortedValues, ratio) {
  const values = sortedValues.filter((value) => Number.isFinite(Number(value))).map(Number).sort((a, b) => a - b);
  if (!values.length) return 0;
  const index = Math.max(0, Math.min(values.length - 1, Math.floor((values.length - 1) * ratio)));
  return values[index];
}

function nearestFrameRow(frame, time) {
  let chosen = null;
  for (let i = 0; i < frame.length; i += 1) {
    if (Number(frame[i].time) > Number(time)) break;
    chosen = frame[i];
  }
  return chosen || frame[0] || null;
}

function trendLabel(frame, index) {
  const row = frame[index] || {};
  const prior = frame[Math.max(0, index - 24)] || {};
  const slopePct = prior.ema50 ? (row.ema50 - prior.ema50) / prior.ema50 * 100 : 0;
  if (row.ema50 && row.ema200 && row.ema50 > row.ema200 && slopePct > 0.15) return "uptrend";
  if (row.ema50 && row.ema200 && row.ema50 < row.ema200 && slopePct < -0.15) return "downtrend";
  return "sideways";
}

function volatilityLabel(row, thresholds) {
  const value = Number(row && row.atrPct);
  if (!Number.isFinite(value)) return "unknownVol";
  if (value <= thresholds.low) return "lowVol";
  if (value >= thresholds.high) return "highVol";
  return "mediumVol";
}

function momentumLabel(frame, index) {
  const row = frame[index] || {};
  const prior = frame[Math.max(0, index - 12)] || {};
  const returnPct = prior.close ? (row.close - prior.close) / prior.close * 100 : 0;
  if ((row.rsi14 || 0) >= 55 || returnPct >= 1) return "bullish";
  if ((row.rsi14 || 0) <= 45 || returnPct <= -1) return "bearish";
  return "neutral";
}

function classifyRegime(frame, thresholds, time) {
  const row = nearestFrameRow(frame, time);
  if (!row) {
    return {
      regime: "unknown_unknownVol_neutral",
      trend: "unknown",
      volatility: "unknownVol",
      momentum: "neutral",
      row: null
    };
  }
  const index = Number(row.__index || 0);
  const trend = trendLabel(frame, index);
  const volatility = volatilityLabel(row, thresholds);
  const momentum = momentumLabel(frame, index);
  return {
    regime: [trend, volatility, momentum].join("_"),
    trend,
    volatility,
    momentum,
    row
  };
}

function equityMetrics(trades, fullReturnPct) {
  const returns = trades.map((trade) => Number(trade.returnPct || 0));
  const wins = returns.filter((value) => value > 0);
  const losses = returns.filter((value) => value < 0);
  let equity = 1;
  let peak = 1;
  let maxDrawdown = 0;
  returns.forEach((value) => {
    equity *= 1 + value / 100;
    peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, peak ? (peak - equity) / peak * 100 : 0);
  });
  const totalReturnPct = round((equity - 1) * 100, 4);
  const grossWins = wins.reduce((sum, value) => sum + value, 0);
  const grossLosses = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  return {
    trades: trades.length,
    totalReturnPct,
    profitFactor: round(grossLosses ? grossWins / grossLosses : wins.length ? 999 : 0, 4),
    winRate: round(trades.length ? wins.length / trades.length * 100 : 0, 4),
    maxDrawdownPct: round(maxDrawdown, 4),
    avgBarsHeld: round(trades.length ? trades.reduce((sum, trade) => sum + Number(trade.barsHeld || 0), 0) / trades.length : 0, 4),
    contributionPct: round(fullReturnPct ? totalReturnPct / fullReturnPct * 100 : 0, 4)
  };
}

function failureReason(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 5) return "TOO_FEW_TRADES";
  if (row.totalReturnPct <= 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "OK";
}

function statusFor(row) {
  const reason = failureReason(row);
  if (reason === "OK" && row.trades >= 10 && row.profitFactor >= 1.05) return "PASS";
  if (reason === "OK" || (row.totalReturnPct >= 0 && row.profitFactor >= 1)) return "WARN";
  return "FAIL";
}

function summarize(regimes) {
  const enough = regimes.filter((row) => row.trades >= 5);
  const positive = enough.filter((row) => row.totalReturnPct > 0 && row.profitFactor >= 1);
  const negative = enough.filter((row) => row.totalReturnPct < 0 || row.profitFactor < 1);
  const bestRegime = regimes.slice().sort((a, b) => b.totalReturnPct - a.totalReturnPct || b.profitFactor - a.profitFactor)[0] || null;
  const worstRegime = regimes.slice().sort((a, b) => a.totalReturnPct - b.totalReturnPct || a.profitFactor - b.profitFactor)[0] || null;
  const highestTradeCountRegime = regimes.slice().sort((a, b) => b.trades - a.trades)[0] || null;
  let status = "UNKNOWN";
  if (enough.length < 2) status = "UNKNOWN";
  else if (positive.length >= Math.ceil(enough.length * 0.75) && negative.length <= 1) status = "LOW";
  else if (bestRegime && Math.abs(bestRegime.contributionPct || 0) >= 80 || negative.filter((row) => row.totalReturnPct < -1).length >= Math.ceil(enough.length / 2)) status = "HIGH";
  else status = "MEDIUM";
  const recommendation = {
    action: status === "LOW" ? "CONTINUE_PAPER_OBSERVATION" : status === "MEDIUM" ? "WATCH_REGIME_MIX" : status === "HIGH" ? "RESEARCH_REGIME_FILTERS" : "COLLECT_MORE_EVIDENCE",
    reason: status === "LOW"
      ? "Most regimes with enough trades are positive in this read-only breakdown."
      : status === "MEDIUM"
        ? "Some regimes work and some fail. Compare future paper events with the active regime mix before judging."
        : status === "HIGH"
          ? "Returns appear concentrated or multiple regimes are strongly negative. Treat the active candidate as regime dependent."
          : "Too few trades per regime to classify regime dependence confidently."
  };
  return {
    bestRegime,
    worstRegime,
    highestTradeCountRegime,
    regimeDependencyStatus: status,
    recommendation
  };
}

const symbol = args.symbol || "ETHUSDT";
const timeframe = args.timeframe || args.interval || "1h";
const strategy = args.strategy || "SimpleAtrTrendV2";
const days = periodDays(args.period);
const source = args.source || "bybit";
const regimeBasis = args.regimeBasis || args["regime-basis"] || "symbol1h";
const includeTrades = String(args.includeTrades === undefined ? "true" : args.includeTrades).toLowerCase() !== "false";
const baseParams = args.baseParams ? JSON.parse(args.baseParams) : {};
const params = Object.assign({}, baseParams, {
  makerFeePct: Number(args.makerFeePct || args["maker-fee-pct"] || 0),
  takerFeePct: Number(args.takerFeePct || args["taker-fee-pct"] || 0),
  slippageBps: Number(args.slippageBps || args["slippage-bps"] || 0)
});
const from = args.from || argsUtil.daysToFrom(days);
const to = args.to || new Date().toISOString();
const limit = args.limit && args.limit !== "auto" ? Number(args.limit) : 5000;
const basisSpec = regimeBasis === "btc4h"
  ? { source, symbol: "BTCUSDT", interval: "4h", from, to, limit: 3000 }
  : { source, symbol, interval: "1h", from, to, limit: 5000 };

Promise.all([
  data.fetchCandles({ source, symbol, interval: timeframe, from, to, limit }),
  data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: 3000 }),
  data.fetchCandles(basisSpec)
]).then(([candles, regimeCandles, basisCandles]) => {
  const normalized = data.normalizeCandles(candles || []);
  const basis = data.normalizeCandles(basisCandles || []);
  const basisFrame = indicators.buildIndicatorFrame(basis, params);
  const atrValues = basisFrame.map((row) => row.atrPct).filter((value) => Number.isFinite(Number(value)));
  const thresholds = {
    low: percentile(atrValues, 0.33),
    high: percentile(atrValues, 0.66)
  };
  const result = backtest.runBacktestOnCandles({
    source,
    symbol,
    interval: timeframe,
    strategy,
    candles: normalized,
    regimeCandles,
    params
  });
  const buckets = {};
  const samples = [];
  (result.tradeList || []).forEach((trade) => {
    const classified = classifyRegime(basisFrame, thresholds, trade.entryTime);
    const bucket = buckets[classified.regime] || {
      regime: classified.regime,
      trend: classified.trend,
      volatility: classified.volatility,
      momentum: classified.momentum,
      trades: []
    };
    bucket.trades.push(trade);
    buckets[classified.regime] = bucket;
    if (includeTrades && samples.length < 25) {
      samples.push({
        regime: classified.regime,
        entryTime: trade.entryTime ? new Date(trade.entryTime * 1000).toISOString() : null,
        exitTime: trade.exitTime ? new Date(trade.exitTime * 1000).toISOString() : null,
        returnPct: round(trade.returnPct, 4),
        barsHeld: trade.barsHeld,
        exitReason: trade.exitReason
      });
    }
  });
  const regimes = Object.keys(buckets).map((key) => {
    const bucket = buckets[key];
    const row = Object.assign({
      regime: bucket.regime,
      trend: bucket.trend,
      volatility: bucket.volatility,
      momentum: bucket.momentum
    }, equityMetrics(bucket.trades, result.totalReturn || 0));
    row.mainFailureReason = failureReason(row);
    row.status = statusFor(row);
    return row;
  }).sort((a, b) => b.totalReturnPct - a.totalReturnPct || b.trades - a.trades);
  process.stdout.write(JSON.stringify({
    ok: true,
    search: {
      symbol,
      timeframe,
      strategy,
      period: args.period || "365d",
      regimeBasis,
      includeTrades
    },
    full: {
      trades: Number(result.trades || 0),
      totalReturnPct: round(result.totalReturn || 0, 4),
      profitFactor: round(result.profitFactor || 0, 4),
      maxDrawdownPct: round(result.maxDrawdown || 0, 4),
      winRate: round(result.winRate || 0, 4)
    },
    summary: summarize(regimes),
    regimes,
    tradeSamples: includeTrades ? samples : [],
    warnings: []
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 0 });
}).catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
