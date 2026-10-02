const fs = require("fs");
const path = require("path");
const https = require("https");
const backtest = require("../core/backtest");
const data = require("../core/data");
const indicators = require("../core/indicators");

function arg(name, fallback) {
  const index = process.argv.indexOf("--" + name);
  return index < 0 ? fallback : process.argv[index + 1];
}

function summary(result) {
  return {
    trades: result.trades,
    returnPct: result.totalReturn,
    profitFactor: result.profitFactor,
    drawdownPct: result.maxDrawdown,
    winRatePct: result.winRate
  };
}

function getJson(url, attempt = 0) {
  return new Promise((resolve, reject) => {
    https.get(url, (response) => {
      let body = "";
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => {
        try {
          const payload = JSON.parse(body);
          if (response.statusCode === 429 || response.statusCode >= 500 || payload.retCode === 10006) {
            if (attempt >= 5) throw new Error(payload.retMsg || `HTTP ${response.statusCode}`);
            return setTimeout(() => getJson(url, attempt + 1).then(resolve, reject), 1000 * (attempt + 1));
          }
          if (response.statusCode !== 200 || payload.retCode !== 0) throw new Error(payload.retMsg || `HTTP ${response.statusCode}`);
          resolve(payload);
        } catch (error) { reject(error); }
      });
    }).on("error", reject);
  });
}

async function fetchCompleteRange(symbol, timeframe, fromSeconds, nowSeconds) {
  const intervalSeconds = /^\d+m$/.test(timeframe) ? Number(timeframe.slice(0, -1)) * 60 : NaN;
  if (![180, 300, 900].includes(intervalSeconds)) throw new Error(`Unsupported research timeframe: ${timeframe}`);
  let endMs = nowSeconds * 1000;
  const candles = [];
  let reachedStart = false;
  for (let page = 0; page < 150; page++) {
    const url = new URL("https://api.bybit.com/v5/market/kline");
    url.searchParams.set("category", "linear");
    url.searchParams.set("symbol", symbol);
    url.searchParams.set("interval", String(intervalSeconds / 60));
    url.searchParams.set("limit", "1000");
    url.searchParams.set("end", String(endMs));
    const payload = await getJson(url);
    const batch = (payload.result && payload.result.list) || [];
    if (!batch.length) break;
    for (const row of batch) {
      const time = Number(row[0]) / 1000;
      if (time < fromSeconds || time + intervalSeconds > nowSeconds) continue;
      candles.push({ time, open: Number(row[1]), high: Number(row[2]), low: Number(row[3]),
        close: Number(row[4]), volume: Number(row[5]) });
    }
    const oldest = Math.min(...batch.map((row) => Number(row[0]) / 1000));
    if (oldest <= fromSeconds + intervalSeconds) { reachedStart = true; break; }
    endMs = oldest * 1000 - 1;
    await new Promise((resolve) => setTimeout(resolve, 180));
  }
  const normalized = data.normalizeCandles(candles);
  if (!reachedStart || !normalized.length || normalized[normalized.length - 1].time < nowSeconds - intervalSeconds * 3) {
    throw new Error(`Incomplete direct ${symbol} ${timeframe} download`);
  }
  const expected = Math.floor((normalized[normalized.length - 1].time - normalized[0].time) / intervalSeconds) + 1;
  if (normalized.length / expected < 0.995) throw new Error(`Gapped direct ${symbol} ${timeframe} download: ${normalized.length}/${expected}`);
  return normalized;
}

function lastClosedHourIndex(hours, signalTime, intervalSeconds) {
  const cutoff = signalTime + intervalSeconds;
  let low = 0;
  let high = hours.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >>> 1;
    if (hours[mid].time + 3600 <= cutoff) {
      found = mid;
      low = mid + 1;
    } else high = mid - 1;
  }
  return found;
}

function hourTrendFilter(hours, intervalSeconds) {
  return ({ row }) => {
    const index = lastClosedHourIndex(hours, row.time, intervalSeconds);
    if (index < 200) return { allow: false, reason: "hour_trend_warmup" };
    const hour = hours[index];
    return hour.close > hour.ema50 && hour.ema50 > hour.ema200
      ? { allow: true }
      : { allow: false, reason: "hour_trend_down" };
  };
}

const variants = [
  { strategy: "RangeExpansionV2", params: { rangeLookback: 12, squeezeLookback: 60, squeezePercentile: 0.35, rangeSma: 20, closeHighPct: 0.65 } },
  { strategy: "RangeExpansionV2", params: { rangeLookback: 20, squeezeLookback: 80, squeezePercentile: 0.5, rangeSma: 20, closeHighPct: 0.7 } },
  { strategy: "BreakoutRetestV2", params: { retestLookback: 6, retestAtr: 0.6 } },
  { strategy: "BreakoutRetestV2", params: { retestLookback: 10, retestAtr: 1.0 } },
  { strategy: "PullbackReclaimV2", params: { rsiPullbackLevel: 42, rsiReclaimLevel: 50 } },
  { strategy: "PullbackReclaimV2", params: { rsiPullbackLevel: 46, rsiReclaimLevel: 54 } }
];

async function main() {
  const source = arg("source", "bybit");
  const days = Number(arg("days", "365"));
  const symbols = arg("symbols", "BTCUSDT,ETHUSDT,SOLUSDT").split(",");
  const timeframes = arg("timeframes", "5m,15m").split(",");
  const output = arg("output", "reports/research-campaigns/short-timeframe-initial.json");
  const feePct = 0.055;
  const slippagePct = 0.02;
  const from = new Date(Date.now() - days * 86400000).toISOString();
  const to = new Date().toISOString();
  const report = { generatedAt: to, source, days, symbols, timeframes, feePct, slippagePct, holdoutFraction: 0.3, variants, coverage: [], rows: [], errors: [] };
  function save() {
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(report, null, 2));
  }
  for (const symbol of symbols) {
    const hourly = data.normalizeCandles(await data.fetchCandles({ source, symbol, interval: "1h", from, to, limit: days * 24 + 300 }));
    const hourFrame = indicators.buildIndicatorFrame(hourly, {});
    for (const timeframe of timeframes) {
      const intervalSeconds = timeframe === "5m" ? 300 : 900;
      const limit = Math.ceil(days * 86400 / intervalSeconds) + 300;
      const candles = await fetchCompleteRange(symbol, timeframe, Math.floor(Date.parse(from) / 1000), Math.floor(Date.parse(to) / 1000));
      const regimeCandles = data.normalizeCandles(await data.fetchCandles({ source, symbol: "BTCUSDT", interval: "4h", from, to, limit: days * 6 + 300 }));
      if (candles.length) {
        const expected = Math.floor((candles[candles.length - 1].time - candles[0].time) / intervalSeconds) + 1;
        report.coverage.push({ symbol, timeframe, candles: candles.length,
          first: new Date(candles[0].time * 1000).toISOString(),
          last: new Date(candles[candles.length - 1].time * 1000).toISOString(),
          completenessPct: Math.round(candles.length / expected * 10000) / 100,
          daysSinceLastCandle: Math.round((Date.now() / 1000 - candles[candles.length - 1].time) / 864) / 100 });
      }
      const split = Math.floor(candles.length * 0.7);
      if (candles.length < 3000 || hourly.length < 300 || split < 500) {
        report.errors.push({ symbol, timeframe, reason: "insufficient_history", candles: candles.length, hourly: hourly.length });
        save();
        continue;
      }
      const train = candles.slice(0, split);
      const validation = candles.slice(Math.max(0, split - 300));
      const validationStart = candles[split].time;
      const entryFilter = hourTrendFilter(hourFrame, intervalSeconds);
      const runs = variants.map((variant, id) => ({ id, variant }));
      for (const item of runs) {
        const params = Object.assign({
          regimeMode: "symbolFastTrend", indicatorFilterPreset: "none",
          riskPct: 0.01, atrMultiplier: 2.4, volumeFilter: false,
          feePct, slippagePct
        }, item.variant.params);
        const options = { source, symbol, interval: timeframe, strategy: item.variant.strategy,
          regimeCandles, entryFilter, params, feePct, slippagePct };
        const trained = backtest.runBacktestOnCandles(Object.assign({}, options, { candles: train }));
        report.rows.push({ symbol, timeframe, id: item.id, strategy: item.variant.strategy,
          params, train: summary(trained), validation: null, stress: null,
          trainEnd: new Date(train[train.length - 1].time * 1000).toISOString(),
          validationStart: new Date(validationStart * 1000).toISOString() });
      }
      const eligible = report.rows.filter((row) => row.symbol === symbol && row.timeframe === timeframe)
        .filter((row) => row.train.trades >= 20 && row.train.profitFactor > 1 && row.train.returnPct > 0)
        .sort((a, b) => b.train.returnPct - a.train.returnPct).slice(0, 3);
      for (const row of eligible) {
        const options = { source, symbol, interval: timeframe, strategy: row.strategy,
          candles: validation, regimeCandles, entryFilter, params: row.params };
        const normal = backtest.runBacktestOnCandles(options);
        const stressed = backtest.runBacktestOnCandles(Object.assign({}, options, {
          params: Object.assign({}, row.params, { feePct: feePct * 2, slippagePct: slippagePct * 2 })
        }));
        row.validation = summary(normal);
        row.stress = summary(stressed);
      }
      save();
      process.stderr.write(`${symbol} ${timeframe}: ${candles.length} candles, ${eligible.length} holdout candidates\n`);
    }
  }
  save();
  process.stdout.write(JSON.stringify({ output, rows: report.rows.length, errors: report.errors,
    holdout: report.rows.filter((row) => row.validation).map((row) => ({ symbol: row.symbol,
      timeframe: row.timeframe, strategy: row.strategy, id: row.id, validation: row.validation, stress: row.stress })) }, null, 2) + "\n");
}

module.exports = { fetchCompleteRange, hourTrendFilter, summary };
if (require.main === module) main().catch((error) => { console.error(error.stack || error.message); process.exitCode = 1; });
