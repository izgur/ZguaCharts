const data = require("../core/data");
const argsUtil = require("./args");
const runtime = require("./runtime");

const args = argsUtil.parseArgs(process.argv.slice(2));

function parseCsv(value, fallback) {
  if (!value || value === "auto") return fallback;
  return String(value).split(",").map((item) => item.trim()).filter(Boolean);
}

function periodDays(raw) {
  return Number(String(raw || "365d").replace(/d$/i, "")) || 365;
}

function round(value, digits) {
  const factor = Math.pow(10, digits || 4);
  return Math.round(Number(value || 0) * factor) / factor;
}

function timeframeMinutes(timeframe) {
  const raw = String(timeframe || "1h").trim().toLowerCase();
  if (raw.endsWith("m")) return Math.max(1, Number(raw.slice(0, -1)) || 60);
  if (raw.endsWith("h")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 60;
  if (raw.endsWith("d")) return Math.max(1, Number(raw.slice(0, -1)) || 1) * 24 * 60;
  if (/^\d+$/.test(raw)) return Math.max(1, Number(raw));
  return 60;
}

function expectedCandlesFor(timeframe, days) {
  return Math.ceil(Number(days || 365) * 24 * 60 / timeframeMinutes(timeframe));
}

function autoLimitFor(timeframe, days, cap) {
  const bars = expectedCandlesFor(timeframe, days);
  return Math.max(100, Math.min(Number(cap || 1000), bars));
}

function candleIso(candle) {
  if (!candle || candle.time === undefined) return null;
  return new Date(Number(candle.time) * 1000).toISOString();
}

function coverageDays(candles) {
  if (!candles || candles.length < 2) return 0;
  const first = Number(candles[0].time || 0);
  const last = Number(candles[candles.length - 1].time || 0);
  return round((last - first) / 86400, 2);
}

function limitFor(timeframe, days) {
  if (!args.limit || args.limit === "auto") return autoLimitFor(timeframe, days, 1000);
  if (String(args.limit).toLowerCase() === "period") return expectedCandlesFor(timeframe, days);
  return Math.max(1, Number(args.limit) || 1000);
}

function rowWarnings(row) {
  const warnings = [];
  if (row.requestedCandles < row.expectedPeriodCandles) {
    warnings.push(`limit_below_period_need_${row.expectedPeriodCandles}`);
  }
  if (row.returnedCandles < Math.min(row.requestedCandles, row.expectedPeriodCandles)) {
    warnings.push("returned_less_than_requested");
  }
  if (row.approxCoverageDays < row.requestedDays * 0.8) {
    warnings.push("coverage_much_shorter_than_period");
  }
  if (row.optimizerAutoLimitCandles < row.expectedPeriodCandles) {
    warnings.push(`optimizer_auto_limit_${row.optimizerAutoLimitCandles}_candles`);
  }
  return warnings;
}

async function main() {
  const days = periodDays(args.period);
  const symbols = parseCsv(args.symbols, ["SOLUSDT"]);
  const timeframes = parseCsv(args.timeframes, ["15m"]);
  const source = args.source || "bybit";
  const from = args.from || argsUtil.daysToFrom(days);
  const to = args.to || new Date().toISOString();
  const forceRefresh = String(args.refresh || "false").toLowerCase() === "true";
  const rows = [];
  const warnings = [];

  for (const symbol of symbols) {
    for (const timeframe of timeframes) {
      const expected = expectedCandlesFor(timeframe, days);
      const requested = limitFor(timeframe, days);
      const optimizerAutoLimit = autoLimitFor(timeframe, days, 1000);
      try {
        const payload = await data.fetchCandles({
          source,
          symbol,
          interval: timeframe,
          from,
          to,
          limit: requested,
          forceRefresh,
          withMetadata: true
        });
        const candles = data.normalizeCandles(payload.candles || []);
        const row = {
          symbol,
          timeframe,
          requestedPeriod: args.period || "365d",
          requestedDays: days,
          expectedPeriodCandles: expected,
          requestedCandles: requested,
          optimizerAutoLimitCandles: optimizerAutoLimit,
          returnedCandles: candles.length,
          approxCoverageDays: coverageDays(candles),
          firstCandleAt: candleIso(candles[0]),
          lastCandleAt: candleIso(candles[candles.length - 1]),
          cacheHit: !!(payload.metadata && payload.metadata.cacheHit),
          fetchedFromBybit: !!(payload.metadata && payload.metadata.fetchedFromBybit),
          bybitRequests: payload.metadata ? Number(payload.metadata.bybitRequests || 0) : 0,
          warnings: []
        };
        row.warnings = rowWarnings(row);
        rows.push(row);
      } catch (error) {
        rows.push({
          symbol,
          timeframe,
          requestedPeriod: args.period || "365d",
          requestedDays: days,
          expectedPeriodCandles: expected,
          requestedCandles: requested,
          optimizerAutoLimitCandles: optimizerAutoLimit,
          returnedCandles: 0,
          approxCoverageDays: 0,
          firstCandleAt: null,
          lastCandleAt: null,
          cacheHit: false,
          fetchedFromBybit: false,
          bybitRequests: 0,
          warnings: ["fetch_error"],
          error: error.message
        });
        warnings.push(`${symbol} ${timeframe}: ${error.message}`);
      }
    }
  }

  process.stdout.write(JSON.stringify({
    ok: rows.every((row) => !row.error),
    search: {
      source,
      symbols,
      timeframes,
      period: args.period || "365d",
      from,
      to,
      limit: args.limit || "auto",
      refresh: forceRefresh
    },
    rows,
    warnings
  }, null, 2));
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: rows.some((row) => row.error) ? 1 : 0 });
}

main().catch((error) => {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({ debugHandles: args["debug-handles"] === true, forceExit: args["force-exit"] === true, exitCode: 1 });
});
