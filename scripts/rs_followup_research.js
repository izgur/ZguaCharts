const fs = require("fs");
const path = require("path");
const assert = require("assert");
const data = require("../core/data");
const backtest = require("../core/backtest");

function arg(name, fallback) {
  const i = process.argv.indexOf("--" + name);
  return i >= 0 ? process.argv[i + 1] : fallback;
}
const days = Math.min(170, Math.max(30, Number(arg("days", "170"))));
const output = arg("output", "reports/research-campaigns/rs-followup-20261002.json");
const offline = process.argv.includes("--offline");
const selectionTime = Date.parse("2026-10-01T19:43:18.476Z") / 1000;
const clockCorrectedAt = "2026-10-02T21:34:48Z";
const forwardStart = Math.max(selectionTime, Date.parse(clockCorrectedAt) / 1000);
const now = Math.floor(Date.now() / 1000);
const frameCache = new Map();

async function fetch(symbol, interval) {
  const step = data.intervalToMs(interval) / 1000;
  const limit = Math.min(50000, Math.ceil(days * 86400 / step) + 300);
  const rows = await data.fetchCandles({ source: "bybit", symbol, interval, limit, forceRefresh: !offline });
  return data.normalizeCandles(rows).filter((row) => row.time + step <= now);
}

function basket(series) {
  const maps = series.map((rows) => new Map(rows.map((row) => [row.time, row.close])));
  const times = [...maps[0].keys()].filter((time) => maps.every((map) => map.has(time))).sort((a, b) => a - b);
  const first = maps.map((map) => map.get(times[0]));
  return times.map((time) => {
    const close = maps.reduce((sum, map, i) => sum + map.get(time) / first[i], 0) / maps.length;
    return { time, open: close, high: close, low: close, close, volume: 0 };
  });
}

function metrics(trades) {
  const net = trades.reduce((sum, trade) => sum + trade.netPnl, 0);
  return { trades: trades.length, netPnl: net, returnPct: net / 100,
    fees: trades.reduce((sum, trade) => sum + trade.feePaid, 0),
    slippage: trades.reduce((sum, trade) => sum + trade.slippagePaid, 0),
    stopExits: trades.filter((trade) => /stop/.test(trade.exitReason)).length,
    costsTurnedWinnerIntoLoss: trades.filter((trade) => trade.grossPnl > 0 && trade.netPnl <= 0).length };
}

async function main() {
  const multi = JSON.parse(fs.readFileSync("config/local/paper-candidates.json", "utf8"));
  const executionCandles = await fetch("BTCUSDT", "5m");
  const regimeCandles = await fetch("BTCUSDT", "4h");
  const rows = [];
  const riskResults = [];
  for (const interval of ["1h", "4h"]) {
    const candidate = multi.candidates.find((item) => item.timeframe === interval && item.enabled !== false);
    assert(candidate, "missing active " + interval + " reference");
    const candles = await fetch("BTCUSDT", interval);
    const benchmarkCandles = basket(await Promise.all(["ETHUSDT", "SOLUSDT", "BNBUSDT"].map((symbol) => fetch(symbol, interval))));
    const startTime = Math.max(executionCandles[0].time, benchmarkCandles[0].time, candles[Math.min(250, candles.length - 1)].time);
    const variants = [
      { name: "current", params: {} },
      { name: "delayed-trail", params: { trailingActivationR: interval === "1h" ? 1.5 : 1 } },
      { name: "double-cost", params: { takerFeePct: 0.11, slippageBps: 4 } }
    ];
    if (interval === "1h") variants.push({ name: "risk0175", params: { riskPct: 0.0175 } });
    for (const variant of variants) {
      const result = backtest.runBacktestOnCandles({
        symbol: "BTCUSDT", interval, executionInterval: "5m", strategy: "RelativeStrengthV2",
        candles, regimeCandles, benchmarkCandles, executionCandles, frameCache,
        entryFilter: ({ row }) => row.time >= startTime,
        params: { ...candidate.params, accountEquity: 10000, maxPositionPct: 10, maxDailyLossPct: 2,
          maxNotional: 100000, fillModel: "next-open", takerFeePct: 0.055, makerFeePct: 0.02,
          slippageBps: 2, ...variant.params }
      });
      const trades = result.tradeList.filter((trade) => trade.exitReason !== "End of data");
      const forward = trades.filter((trade) => trade.entrySignalTime >= forwardStart);
      rows.push({ interval, variant: variant.name, params: result.params, startTime,
        historicalDiagnostics: metrics(trades), newForwardEvidence: metrics(forward),
        forwardVerdict: forward.length >= 30 ? "REVIEW" : "INSUFFICIENT_NEW_TRADES" });
      if (interval === "1h" && ["current", "risk0175"].includes(variant.name)) {
        riskResults.push({ variant: variant.name, trades: result.tradeList.map((trade) => ({
          entry: trade.entryFillTime, exit: trade.exitFillTime, size: trade.size, notional: trade.notional,
          netPnl: trade.netPnl, reason: trade.exitReason
        })) });
      }
    }
  }
  const riskVariantsIdentical = JSON.stringify(riskResults[0].trades) === JSON.stringify(riskResults[1].trades);
  const report = { generatedAt: new Date().toISOString(), days, offline,
    purpose: "Bounded seven-run production-policy diagnostics and capped 1h sizing comparison; no automatic promotion.",
    selectedPolicyAt: new Date(selectionTime * 1000).toISOString(),
    clockCorrectedAt, forwardEvidenceStartsAt: new Date(forwardStart * 1000).toISOString(),
    historicalDataIsPreviouslyExamined: true,
    newForwardEvidenceStartsAfterPolicySelection: true,
    caveat: "Older diagnostics are not a fresh holdout. New forward evidence begins after both policy selection and the engine clock correction.",
    executionCoverage: { first: executionCandles[0].time, last: executionCandles[executionCandles.length - 1].time },
    riskVariantsIdentical, riskComparison: riskResults.map((row) => ({ variant: row.variant, trades: row.trades.length })), rows };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ output, riskVariantsIdentical, rows: rows.map(({ interval, variant, historicalDiagnostics, newForwardEvidence, forwardVerdict }) => ({ interval, variant, historicalDiagnostics, newForwardEvidence, forwardVerdict })) }, null, 2));
}
main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
