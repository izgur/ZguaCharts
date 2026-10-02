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

function bools(raw, fallback) {
  if (!raw) return fallback;
  return csv(raw, fallback.map(String)).map((value) => String(value).toLowerCase() === "true");
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

function classify(row) {
  if (row.trades <= 0) return "NO_TRADES";
  if (row.trades < 40) return "TOO_FEW_TRADES";
  if (row.totalReturnPct < 0) return "NEGATIVE_RETURN";
  if (row.profitFactor < 1.1) return "WEAK_PROFIT_FACTOR";
  if (row.maxDrawdownPct > 25) return "HIGH_DRAWDOWN";
  return "PASS";
}

function score(row) {
  if (row.trades <= 0) return -999;
  return round(
    (row.status === "PASS" ? 40 : -20)
      + Math.min(row.trades, 120) * 0.25
      + row.totalReturnPct * 2
      + row.profitFactor * 26
      - row.maxDrawdownPct * 1.4
      + row.winRate * 0.25,
    5
  );
}

async function main() {
  const symbol = args.symbol || "XRPUSDT";
  const timeframe = args.timeframe || "15m";
  const days = Number(args.days || 730);
  const limit = Number(args.limit || Math.ceil(days * 24 * 4));
  const from = args.from || daysToFrom(days);
  const to = args.to || new Date().toISOString();
  const output = args.output || "reports/research-campaigns/mean-reversion-refine.json";
  const feePct = Number(args.feePct || 0.055);
  const slippagePct = Number(args.slippagePct || 0.02);
  const checkpointEvery = Math.max(1, Number(args.checkpointEvery || 25));
  const runtimeBudgetSeconds = Math.max(0, Number(args.runtimeBudgetSeconds || 0));
  const startedAt = Date.now();

  const grid = {
    emaSlow: nums(args.emaSlow, [80, 100, 120, 150, 200]),
    rsiLimit: nums(args.rsiLimit, [20, 25, 30]),
    rsiOversold: nums(args.rsiOversold, [34, 36, 38, 40]),
    stopAtr: nums(args.stopAtr, [1.2, 1.5, 1.8]),
    takeProfitAtr: nums(args.takeProfitAtr, [1.6, 2, 2.4]),
    minHoldBars: nums(args.minHoldBars, [1, 2, 3]),
    cooldownBars: nums(args.cooldownBars, [3, 6, 9]),
    volumeFilter: bools(args.volumeFilter, [false]),
    riskPct: nums(args.riskPct, [0.03, 0.05]),
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

  function writePayload(partial) {
    const sorted = rows.slice().sort((a, b) => b.score - a.score);
    sorted.forEach((row, index) => { row.rank = index + 1; });
    const payload = {
      ok: true,
      partial: !!partial,
      generatedAt: new Date().toISOString(),
      search: { symbol, timeframe, days, limit, combos: combos.length, evaluated: rows.length, feePct, slippagePct, runtimeBudgetSeconds, runtimeBudgetHit },
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
      strategy: "MeanReversion",
      candles,
      params: Object.assign({}, params, { feePct, slippagePct }),
      feePct,
      slippagePct,
    });
    const row = {
      rank: null,
      index,
      strategy: "MeanReversion",
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
