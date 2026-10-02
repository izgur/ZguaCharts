const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");
const argsUtil = require("../cli/args");

const args = argsUtil.parseArgs(process.argv.slice(2));

function csv(raw, fallback) {
  if (!raw) return fallback;
  return String(raw).split(",").map((item) => item.trim()).filter(Boolean);
}

function nums(raw, fallback) {
  return csv(raw, fallback.map(String)).map(Number).filter((value) => Number.isFinite(value));
}

function daysToFrom(days) {
  return argsUtil.daysToFrom(Number(days || 730));
}

function expandGrid(grid) {
  const keys = Object.keys(grid);
  const out = [];
  function step(index, current) {
    if (index >= keys.length) {
      out.push(Object.assign({}, current));
      return;
    }
    const key = keys[index];
    grid[key].forEach((value) => {
      current[key] = value;
      step(index + 1, current);
    });
  }
  step(0, {});
  return out;
}

function round(value, digits = 4) {
  const factor = Math.pow(10, digits);
  return Math.round(Number(value || 0) * factor) / factor;
}

function timeframeHours(timeframe) {
  const raw = String(timeframe || "1h").trim().toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 60) / 60;
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1);
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24;
  return 1;
}

function executionLimitFor(signalTimeframe, executionTimeframe, days) {
  const signalBars = Math.ceil(Number(days || 730) * 24 / timeframeHours(signalTimeframe));
  const multiplier = Math.max(1, Math.ceil(timeframeHours(signalTimeframe) / timeframeHours(executionTimeframe)));
  return signalBars * multiplier;
}

function classify(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 20) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "PASS";
}

function score(row) {
  if (row.trades <= 0) return -999;
  const tradeTargetBonus = row.trades >= 40 ? 35 : -Math.max(0, 40 - row.trades) * 1.2;
  return round(
    tradeTargetBonus
      + row.totalReturnPct * 2.2
      + row.profitFactor * 24
      - row.maxDrawdownPct * 1.5
      + Math.min(row.trades, 80) * 0.25,
    5
  );
}

async function main() {
  const symbol = args.symbol || "BTCUSDT";
  const timeframe = args.timeframe || "15m";
  const days = Number(args.days || 730);
  const limit = Number(args.limit || Math.ceil(days * 24 * 4));
  const from = args.from || daysToFrom(days);
  const to = args.to || new Date().toISOString();
  const output = args.output || "reports/research-campaigns/btc15m-range-refine.json";
  const feePct = Number(args.feePct || 0.055);
  const slippagePct = Number(args.slippagePct || 0.02);
  const executionTimeframe = args.executionTimeframe || args.executionInterval || "none";
  const useExecutionCandles = executionTimeframe && executionTimeframe !== "none" && executionTimeframe !== timeframe;
  const executionLimit = Number(args.executionLimit || executionLimitFor(timeframe, executionTimeframe, days));
  const checkpointEvery = Math.max(1, Number(args.checkpointEvery || 5));
  const runtimeBudgetSeconds = Math.max(0, Number(args.runtimeBudgetSeconds || 0));
  const startedAt = Date.now();

  const grid = {
    regimeMode: csv(args.regimeMode, ["looseBtcBull"]),
    squeezeLookback: nums(args.squeezeLookback, [50, 60, 70, 80]),
    squeezePercentile: nums(args.squeezePercentile, [0.35, 0.45, 0.5, 0.55, 0.65]),
    rangeLookback: nums(args.rangeLookback, [14, 18, 20, 24, 30]),
    rangeSma: nums(args.rangeSma, [20, 25, 30, 35, 40]),
    closeHighPct: nums(args.closeHighPct, [0.5, 0.55, 0.6, 0.65, 0.7, 0.75]),
    atrMultiplier: nums(args.atrMultiplier, [1.8, 2, 2.2, 2.5]),
    riskPct: nums(args.riskPct, [0.05]),
    cooldownBars: nums(args.cooldownBars, [0]),
    volumeFilter: [false],
    indicatorFilterPreset: ["none"],
  };

  const combos = expandGrid(grid);
  const rows = [];
  let runtimeBudgetHit = false;
  const candles = data.normalizeCandles(await data.fetchCandles({
    source: args.source || "bybit",
    symbol,
    interval: timeframe,
    from,
    to,
    limit,
  }));
  const regimeCandles = data.normalizeCandles(await data.fetchCandles({
    source: args.source || "bybit",
    symbol: "BTCUSDT",
    interval: "4h",
    from,
    to,
    limit: Math.ceil(days * 6) + 250,
  }));
  const executionCandles = useExecutionCandles ? data.normalizeCandles(await data.fetchCandles({
    source: args.source || "bybit",
    symbol,
    interval: executionTimeframe,
    from,
    to,
    limit: executionLimit,
  })) : [];

  function writePayload(partial) {
    const sorted = rows.slice().sort((a, b) => b.score - a.score);
    sorted.forEach((row, index) => { row.rank = index + 1; });
    const payload = {
      ok: true,
      partial: !!partial,
      generatedAt: new Date().toISOString(),
      search: {
        symbol,
        timeframe,
        days,
        limit,
        combos: combos.length,
        evaluated: rows.length,
        feePct,
        slippagePct,
        executionTimeframe: useExecutionCandles ? executionTimeframe : null,
        executionLimit: useExecutionCandles ? executionLimit : null,
        executionCandles: executionCandles.length,
        runtimeBudgetSeconds,
        runtimeBudgetHit,
      },
      topRows: sorted.slice(0, 50),
      rows: sorted,
    };
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(payload, null, 2));
    return payload;
  }

  for (let index = 0; index < combos.length; index += 1) {
    if (runtimeBudgetSeconds > 0 && (Date.now() - startedAt) / 1000 >= runtimeBudgetSeconds) {
      runtimeBudgetHit = true;
      break;
    }
    const params = combos[index];
    const result = backtest.runBacktestOnCandles({
      source: args.source || "bybit",
      symbol,
      interval: timeframe,
      executionInterval: useExecutionCandles ? executionTimeframe : timeframe,
      strategy: "RangeExpansionV2",
      candles,
      regimeCandles,
      executionCandles,
      params: Object.assign({}, params, { feePct, slippagePct }),
      feePct,
      slippagePct,
    });
    const row = {
      rank: null,
      index,
      strategy: "RangeExpansionV2",
      symbol,
      timeframe,
      params,
      trades: Number(result.trades || 0),
      totalReturnPct: round(result.totalReturn || 0, 4),
      profitFactor: round(result.profitFactor || 0, 4),
      maxDrawdownPct: round(result.maxDrawdown || 0, 4),
      winRate: round(result.winRate || 0, 4),
      avgBarsHeld: round(result.avgBarsHeld || 0, 4),
    };
    row.status = classify(row);
    row.score = score(row);
    rows.push(row);
    if (rows.length % checkpointEvery === 0) {
      writePayload(true);
      process.stderr.write(`evaluated ${rows.length}/${combos.length}\n`);
    }
  }

  const payload = writePayload(false);
  console.log(JSON.stringify({
    ok: true,
    output,
    combos: combos.length,
    evaluated: rows.length,
    runtimeBudgetHit,
    topRows: payload.topRows.slice(0, 10).map((row) => ({
      rank: row.rank,
      status: row.status,
      trades: row.trades,
      returnPct: row.totalReturnPct,
      profitFactor: row.profitFactor,
      maxDrawdownPct: row.maxDrawdownPct,
      score: row.score,
      params: row.params,
    })),
  }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
