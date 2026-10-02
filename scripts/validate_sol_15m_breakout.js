const fs = require("fs");
const path = require("path");
const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");
const { fetchCompleteRange, hourTrendFilter, summary } = require("./research_short_timeframe");

async function main() {
  const days = Number(process.argv[2] || 365);
  const output = process.argv[3] || "reports/research-campaigns/sol-15m-breakout-validation.json";
  const to = new Date();
  const from = new Date(to.getTime() - days * 86400000);
  const candles = await fetchCompleteRange("SOLUSDT", "15m", Math.floor(from.getTime() / 1000), Math.floor(to.getTime() / 1000));
  const hourly = data.normalizeCandles(await data.fetchCandles({ source: "bybit", symbol: "SOLUSDT",
    interval: "1h", from: from.toISOString(), to: to.toISOString(), limit: days * 24 + 300 }));
  const regimeCandles = data.normalizeCandles(await data.fetchCandles({ source: "bybit", symbol: "BTCUSDT",
    interval: "4h", from: from.toISOString(), to: to.toISOString(), limit: days * 6 + 300 }));
  const hourFrame = indicators.buildIndicatorFrame(hourly, {});
  const params = { regimeMode: "symbolFastTrend", indicatorFilterPreset: "none", riskPct: 0.01,
    atrMultiplier: 2.4, volumeFilter: false, feePct: 0.055, slippagePct: 0.02,
    retestLookback: 10, retestAtr: 1.0 };
  const entryFilter = hourTrendFilter(hourFrame, 900);
  const folds = [];
  for (let fold = 0; fold < 4; fold++) {
    const start = Math.floor(candles.length * fold / 4);
    const end = Math.floor(candles.length * (fold + 1) / 4);
    const period = candles.slice(Math.max(0, start - 300), end);
    const base = { source: "bybit", symbol: "SOLUSDT", interval: "15m", strategy: "BreakoutRetestV2",
      candles: period, regimeCandles, entryFilter };
    const normal = backtest.runBacktestOnCandles(Object.assign({}, base, { params }));
    const stress = backtest.runBacktestOnCandles(Object.assign({}, base, {
      params: Object.assign({}, params, { feePct: 0.11, slippagePct: 0.04 })
    }));
    folds.push({ fold: fold + 1, start: new Date(candles[start].time * 1000).toISOString(),
      end: new Date(candles[end - 1].time * 1000).toISOString(),
      normal: summary(normal), doubleCosts: summary(stress) });
    process.stderr.write(`fold ${fold + 1}/4 complete\n`);
  }
  const report = { generatedAt: new Date().toISOString(), purpose: "Fixed-parameter historical consistency check; the recent 180d overlaps prior discovery.",
    source: "bybit", symbol: "SOLUSDT", timeframe: "15m", days, candles: candles.length,
    first: new Date(candles[0].time * 1000).toISOString(),
    last: new Date(candles[candles.length - 1].time * 1000).toISOString(), params, folds };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, JSON.stringify(report, null, 2));
  process.stdout.write(JSON.stringify({ output, folds }, null, 2) + "\n");
}

main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
