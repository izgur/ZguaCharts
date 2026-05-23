const fs = require("fs");
const path = require("path");

const backtest = require("../core/backtest");
const data = require("../core/data");
const tradeAudit = require("../core/backtest/tradeAudit");
const argsUtil = require("./args");
const runtime = require("./runtime");

const SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"];
const INTERVALS = ["15m", "1h", "4h"];
const STRATEGY = "RegimePullbackTrend";

const args = argsUtil.parseArgs(process.argv.slice(2));
const options = {
  source: args.source || "bybit",
  days: Number(args.days || 365),
  from: args.from || argsUtil.daysToFrom(args.days || 365),
  to: args.to || new Date().toISOString(),
  outputDir: args.output || "reports"
};

function limitForInterval(interval) {
  if (interval === "15m") return 20000;
  if (interval === "1h") return 9000;
  if (interval === "4h") return 3000;
  return 5000;
}

function ensureDir(dir) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir);
}

function runResult(symbol, interval, candles, regimeCandles) {
  const result = backtest.runBacktestOnCandles({
    symbol,
    interval,
    strategy: STRATEGY,
    candles,
    regimeCandles,
    params: {}
  });
  const audit = tradeAudit.auditTrades(result, candles);
  const row = {
    symbol,
    interval,
    candles: candles.length,
    trades: result.trades,
    totalReturn: result.totalReturn,
    profitFactor: result.profitFactor,
    maxDrawdown: result.maxDrawdown,
    winRate: result.winRate,
    avgTrade: result.averageTrade,
    exposurePct: result.exposurePct,
    auditOk: audit.ok,
    primaryBlocker: result.diagnostics ? result.diagnostics.primaryBlocker : null
  };
  row.viable = isViable(row);
  return row;
}

function isViable(row) {
  const minTrades = row.interval === "4h" ? 20 : 50;
  return row.trades >= minTrades &&
    row.profitFactor > 1.15 &&
    row.totalReturn > 0 &&
    row.maxDrawdown < 15 &&
    row.auditOk === true;
}

function loadRegime(limit) {
  return data.fetchCandles({
    source: options.source,
    symbol: "BTCUSDT",
    interval: "4h",
    from: options.from,
    to: options.to,
    limit
  });
}

function fetchMatrix() {
  return loadRegime(3000).then(function (regimeCandles) {
    const jobs = [];
    SYMBOLS.forEach(function (symbol) {
      INTERVALS.forEach(function (interval) {
        jobs.push(function () {
          return data.fetchCandles({
            source: options.source,
            symbol,
            interval,
            from: options.from,
            to: options.to,
            limit: limitForInterval(interval)
          }).then(function (candles) {
            return runResult(symbol, interval, candles, regimeCandles);
          });
        });
      });
    });
    return runSequential(jobs).then(function (rows) {
      return { rows, regimeCandles };
    });
  });
}

function runSequential(jobs) {
  const rows = [];
  return jobs.reduce(function (promise, job) {
    return promise.then(function () {
      return job().then(function (row) {
        rows.push(row);
      });
    });
  }, Promise.resolve()).then(function () { return rows; });
}

function walkForwardBtc1h(regimeCandles) {
  return data.fetchCandles({
    source: options.source,
    symbol: "BTCUSDT",
    interval: "1h",
    from: options.from,
    to: options.to,
    limit: limitForInterval("1h")
  }).then(function (candles) {
    const folds = 4;
    const foldSize = Math.floor(candles.length / (folds + 1));
    const out = [];
    for (let fold = 1; fold <= folds; fold += 1) {
      const train = candles.slice(0, foldSize * fold);
      const test = candles.slice(foldSize * fold, foldSize * (fold + 1));
      const trainResult = backtest.runBacktestOnCandles({
        symbol: "BTCUSDT",
        interval: "1h",
        strategy: STRATEGY,
        candles: train,
        regimeCandles,
        params: {}
      });
      const testResult = backtest.runBacktestOnCandles({
        symbol: "BTCUSDT",
        interval: "1h",
        strategy: STRATEGY,
        candles: test,
        regimeCandles,
        params: {}
      });
      out.push({
        fold,
        trainReturn: trainResult.totalReturn,
        testReturn: testResult.totalReturn,
        trainPF: trainResult.profitFactor,
        testPF: testResult.profitFactor,
        trainTrades: trainResult.trades,
        testTrades: testResult.trades
      });
    }
    return out;
  });
}

function verdict(rows, walkForward) {
  const viableRows = rows.filter(function (row) { return row.viable; });
  const positiveWalkTests = walkForward.filter(function (fold) {
    return fold.testReturn > 0 && fold.testPF > 1;
  }).length;
  const best = rows.slice().sort(function (a, b) { return b.profitFactor - a.profitFactor; }).slice(0, 5);
  const worst = rows.slice().sort(function (a, b) { return a.totalReturn - b.totalReturn; }).slice(0, 5);
  const robust = viableRows.length >= 3 && positiveWalkTests >= 3;
  return {
    robust_candidate: robust,
    reason: robust
      ? "Multiple symbol/timeframe rows passed viability and most BTCUSDT 1h walk-forward folds were positive."
      : "Too few rows passed viability and/or BTCUSDT 1h walk-forward was not consistently positive.",
    best_markets_timeframes: best.map(function (row) {
      return { symbol: row.symbol, interval: row.interval, totalReturn: row.totalReturn, profitFactor: row.profitFactor, trades: row.trades };
    }),
    worst_markets_timeframes: worst.map(function (row) {
      return { symbol: row.symbol, interval: row.interval, totalReturn: row.totalReturn, profitFactor: row.profitFactor, trades: row.trades };
    }),
    optimization_justified: robust || viableRows.length > 0,
    viableResults: viableRows.length,
    totalTests: rows.length
  };
}

function toCsv(rows) {
  const columns = ["symbol", "interval", "candles", "trades", "totalReturn", "profitFactor", "maxDrawdown", "winRate", "avgTrade", "exposurePct", "auditOk", "primaryBlocker", "viable"];
  return [columns.join(",")].concat(rows.map(function (row) {
    return columns.map(function (column) {
      return csv(row[column]);
    }).join(",");
  })).join("\n");
}

function csv(value) {
  const text = String(value === undefined || value === null ? "" : value);
  return /[",\n]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
}

fetchMatrix().then(function (loaded) {
  return walkForwardBtc1h(loaded.regimeCandles).then(function (walkForward) {
    const payload = {
      strategy: STRATEGY,
      source: options.source,
      days: options.days,
      rows: loaded.rows,
      walkForward: {
        symbol: "BTCUSDT",
        interval: "1h",
        folds: walkForward
      },
      verdict: verdict(loaded.rows, walkForward)
    };
    ensureDir(options.outputDir);
    fs.writeFileSync(path.join(options.outputDir, "pullback-validation.json"), JSON.stringify(payload, null, 2));
    fs.writeFileSync(path.join(options.outputDir, "pullback-validation.csv"), toCsv(loaded.rows));
    process.stdout.write(JSON.stringify(payload, null, 2));
    runtime.finishCli({
      debugHandles: args["debug-handles"] === true,
      forceExit: args["force-exit"] === true,
      exitCode: 0
    });
  });
}).catch(function (error) {
  process.stderr.write(error.stack || error.message);
  runtime.finishCli({
    debugHandles: args["debug-handles"] === true,
    forceExit: args["force-exit"] === true,
    exitCode: 1
  });
});
