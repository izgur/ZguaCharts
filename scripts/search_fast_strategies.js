const fs = require("fs");
const path = require("path");
const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");
const { fetchCompleteRange, hourTrendFilter, summary } = require("./research_short_timeframe");

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i < 0 ? fallback : process.argv[i + 1];
}

function grid(strategy, dimensions, extra = {}, useHourTrend = true) {
  let rows = [{ strategy, params: extra, useHourTrend }];
  for (const [name, values] of Object.entries(dimensions)) {
    rows = rows.flatMap((row) => values.map((value) => ({
      strategy, useHourTrend, params: Object.assign({}, row.params, { [name]: value })
    })));
  }
  return rows;
}

const candidates = [
  ...grid("BreakoutRetestV2", { retestLookback: [5, 10, 15], retestAtr: [0.5, 0.8, 1.1] }),
  ...grid("RangeExpansionV2", { rangeLookback: [12, 20], squeezePercentile: [0.35, 0.5], closeHighPct: [0.6, 0.7] }, { squeezeLookback: 70, rangeSma: 20 }),
  ...grid("PullbackReclaimV2", { rsiPullbackLevel: [40, 45, 50], rsiReclaimLevel: [50, 54, 58] }),
  ...grid("SimpleAtrTrendV2", { rsiMin: [45, 52], rsiMax: [68, 75], atrMultiplier: [2, 2.8] }),
  ...grid("VwapReversion15mFastV1", { minVwapDistanceAtr: [0.3, 0.5, 0.7], maxAdx: [30, 45] },
    { reversionDirection: "both", requireBbTouch: false, riskPct: 0.01 }, false)
];

function qualityScore(row) {
  return row.returnPct - row.drawdownPct * 1.5 + Math.min(row.profitFactor, 5) * 4;
}

async function main() {
  const symbol = arg("symbol", "BTCUSDT");
  const timeframe = arg("timeframe", "15m");
  const days = Number(arg("days", "180"));
  const output = path.resolve(arg("output", `reports/research-campaigns/fast-search-${symbol}-${timeframe}.json`));
  const maxVariants = Number(arg("max-variants", String(candidates.length)));
  const variants = candidates.slice(0, maxVariants);
  const intervalSeconds = Number(timeframe.slice(0, -1)) * 60;
  if (![180, 300, 900].includes(intervalSeconds) || !Number.isFinite(days) || days < 14) throw new Error("Use 3m/5m/15m and at least 14 days");
  const now = new Date();
  const from = new Date(now.getTime() - days * 86400000);
  const fromSeconds = Math.floor(from.getTime() / 1000);
  const toSeconds = Math.floor(now.getTime() / 1000);
  const candles = await fetchCompleteRange(symbol, timeframe, fromSeconds, toSeconds);
  const hourly = data.normalizeCandles(await data.fetchCandles({ source: "bybit", symbol, interval: "1h",
    from: from.toISOString(), to: now.toISOString(), limit: days * 24 + 300 }));
  const regimeCandles = data.normalizeCandles(await data.fetchCandles({ source: "bybit", symbol: "BTCUSDT", interval: "4h",
    from: from.toISOString(), to: now.toISOString(), limit: days * 6 + 300 }));
  if (hourly.length < days * 20 || regimeCandles.length < days * 5
    || hourly[hourly.length - 1].time < toSeconds - 7200
    || regimeCandles[regimeCandles.length - 1].time < toSeconds - 21600) {
    throw new Error(`Stale or incomplete 1h/4h context for ${symbol} ${timeframe}`);
  }
  const hours = indicators.buildIndicatorFrame(hourly, {});
  const split1 = Math.floor(candles.length * 0.6);
  const split2 = Math.floor(candles.length * 0.8);
  const blocks = [candles.slice(0, split1), candles.slice(Math.max(0, split1 - 300), split2), candles.slice(Math.max(0, split2 - 300))];
  const starts = [candles[0].time, candles[split1].time, candles[split2].time];
  const report = { generatedAt: now.toISOString(), complete: false, symbol, timeframe, days,
    candles: candles.length, first: new Date(candles[0].time * 1000).toISOString(),
    last: new Date(candles[candles.length - 1].time * 1000).toISOString(),
    splits: starts.map((time) => new Date(time * 1000).toISOString()),
    costs: { feePctPerSide: 0.055, slippagePctPerSide: 0.02, doubleCostStress: true },
    method: "Fixed grid; 60% train, 20% validation, 20% untouched final. Next-open fills and 300-bar warmup on later blocks.",
    totalVariants: variants.length, evaluated: 0, variants, rows: [], selectedForValidation: [], selectedForFinal: [] };
  function save() {
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(report, null, 2));
  }
  function evaluate(candidate, blockIndex, stress = false) {
    const baseParams = candidate.strategy === "VwapReversion15mFastV1"
      ? { regimeMode: "noRegime", indicatorFilterPreset: "none", riskPct: 0.01, volumeFilter: false }
      : { regimeMode: "symbolFastTrend", indicatorFilterPreset: "none", riskPct: 0.01, volumeFilter: false, atrMultiplier: 2.4 };
    const params = Object.assign(baseParams, candidate.params, {
      feePct: stress ? 0.11 : 0.055, slippagePct: stress ? 0.04 : 0.02
    });
    const hourFilter = candidate.useHourTrend ? hourTrendFilter(hours, intervalSeconds) : null;
    const entryFilter = ({ row }) => {
      if (row.time < starts[blockIndex]) return { allow: false, reason: "pre_block_warmup" };
      return hourFilter ? hourFilter({ row }) : { allow: true };
    };
    const result = backtest.runBacktestOnCandles({ source: "bybit", symbol, interval: timeframe,
      strategy: candidate.strategy, candles: blocks[blockIndex], regimeCandles, entryFilter, params });
    return summary(result);
  }
  for (let i = 0; i < variants.length; i++) {
    const candidate = variants[i];
    const train = evaluate(candidate, 0);
    report.rows.push({ id: i, strategy: candidate.strategy, params: candidate.params, useHourTrend: candidate.useHourTrend,
      train, validation: null, validationStress: null, final: null, finalStress: null });
    report.evaluated++;
    save();
    process.stderr.write(`${symbol} ${timeframe} train ${i + 1}/${variants.length}\n`);
  }
  const trainLeads = report.rows.filter((row) => row.train.trades >= 20 && row.train.returnPct > 0 && row.train.profitFactor > 1 && row.train.drawdownPct < 30);
  const families = [...new Set(variants.map((row) => row.strategy))];
  const selected = families.flatMap((strategy) => trainLeads.filter((row) => row.strategy === strategy)
    .sort((a, b) => qualityScore(b.train) - qualityScore(a.train)).slice(0, 2));
  report.selectedForValidation = selected.map((row) => row.id);
  save();
  for (const row of selected) {
    row.validation = evaluate(variants[row.id], 1);
    row.validationStress = evaluate(variants[row.id], 1, true);
    save();
  }
  const finalLeads = selected.filter((row) => row.validation.trades >= 8 && row.validation.returnPct > 0
    && row.validation.profitFactor > 1.1 && row.validationStress.returnPct > 0)
    .sort((a, b) => qualityScore(b.validationStress) - qualityScore(a.validationStress)).slice(0, 3);
  report.selectedForFinal = finalLeads.map((row) => row.id);
  save();
  for (const row of finalLeads) {
    row.final = evaluate(variants[row.id], 2);
    row.finalStress = evaluate(variants[row.id], 2, true);
    save();
  }
  report.complete = true;
  report.finishedAt = new Date().toISOString();
  save();
  process.stdout.write(JSON.stringify({ output, symbol, timeframe, totalVariants: variants.length,
    selectedForValidation: report.selectedForValidation, selectedForFinal: report.selectedForFinal,
    finalRows: finalLeads.map((row) => ({ id: row.id, strategy: row.strategy, params: row.params,
      train: row.train, validation: row.validation, validationStress: row.validationStress,
      final: row.final, finalStress: row.finalStress })) }, null, 2) + "\n");
}

main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
