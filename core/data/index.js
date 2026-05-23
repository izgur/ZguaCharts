const fs = require("fs");
const path = require("path");
const https = require("https");
const logger = require("../utils/logger");

const CACHE_DIR = path.join(process.cwd(), ".research-cache");
const BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline";
const BYBIT_REQUEST_LIMIT = 1000;
const memoryCache = {};
const pendingFetches = {};
var activeHttpRequests = 0;
const httpQueue = [];

function ensureCacheDir() {
  if (!fs.existsSync(CACHE_DIR)) fs.mkdirSync(CACHE_DIR);
}

function cacheKey(source, symbol, interval) {
  return [source || "bybit", symbol, interval].join("_").replace(/[^a-zA-Z0-9_.-]/g, "_");
}

function cachePath(source, symbol, interval) {
  ensureCacheDir();
  return path.join(CACHE_DIR, cacheKey(source, symbol, interval) + ".json");
}

function loadCachedCandles(source, symbol, interval) {
  var key = cacheKey(source, symbol, interval);
  if (memoryCache[key]) return memoryCache[key].slice();
  var file = cachePath(source, symbol, interval);
  if (!fs.existsSync(file)) return [];
  try {
    var candles = JSON.parse(fs.readFileSync(file, "utf8"));
    memoryCache[key] = normalizeCandles(candles);
    return memoryCache[key].slice();
  } catch (error) {
    logger.warn("Ignoring unreadable candle cache", { file: file, error: error.message });
    return [];
  }
}

function saveCachedCandles(source, symbol, interval, candles) {
  var normalized = normalizeCandles(candles);
  var key = cacheKey(source, symbol, interval);
  memoryCache[key] = normalized;
  fs.writeFileSync(cachePath(source, symbol, interval), JSON.stringify(normalized));
}

function normalizeCandles(candles) {
  var byTime = {};
  (candles || []).forEach(function (candle) {
    if (!candle || candle.time === undefined) return;
    byTime[Number(candle.time)] = {
      time: Number(candle.time),
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
      volume: Number(candle.volume || 0)
    };
  });
  return Object.keys(byTime).map(Number).sort(function (a, b) { return a - b; }).map(function (time) {
    return byTime[time];
  });
}

function filterCandles(candles, from, to) {
  var fromSec = parseDateToSeconds(from);
  var toSec = parseDateToSeconds(to);
  return normalizeCandles(candles).filter(function (candle) {
    if (fromSec !== null && candle.time < fromSec) return false;
    if (toSec !== null && candle.time > toSec) return false;
    return true;
  });
}

function parseDateToSeconds(value) {
  if (!value) return null;
  if (typeof value === "number") return value;
  var parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
}

function intervalToBybit(interval) {
  if (String(interval) === "240") return "240";
  if (String(interval).toUpperCase() === "D") return "D";
  if (/^\d+m$/.test(interval)) return interval.slice(0, -1);
  if (/^\d+h$/.test(interval)) return String(Number(interval.slice(0, -1)) * 60);
  if (interval === "1d") return "D";
  return interval;
}

function intervalToMs(interval) {
  interval = String(interval);
  if (/^\d+$/.test(interval)) return Number(interval) * 60 * 1000;
  if (interval.toUpperCase() === "D") return 24 * 60 * 60 * 1000;
  var amount = Number(interval.slice(0, -1));
  var unit = interval.slice(-1);
  if (unit === "m") return amount * 60 * 1000;
  if (unit === "h") return amount * 60 * 60 * 1000;
  if (unit === "d") return amount * 24 * 60 * 60 * 1000;
  return 60 * 1000;
}

function fetchCandles(options) {
  options = options || {};
  var source = options.source || "bybit";
  if (options.candles) return Promise.resolve(filterCandles(options.candles, options.from, options.to));
  if (source !== "bybit") {
    throw new Error("Node research data currently supports bybit directly. Flask may pass candles for other sources.");
  }
  return Promise.resolve(fetchBybitCandles(options));
}

function fetchBybitCandles(options) {
  var symbol = options.symbol;
  var interval = options.interval;
  var pendingKey = JSON.stringify({
    symbol: symbol,
    interval: interval,
    from: options.from || null,
    to: options.to || null,
    limit: options.limit || null,
    forceRefresh: !!options.forceRefresh,
    withMetadata: !!options.withMetadata
  });
  if (pendingFetches[pendingKey]) return pendingFetches[pendingKey];
  var requested = Number(options.limit || candlesForRange(options.from, options.to, interval) || 1000);
  var cached = loadCachedCandles("bybit", symbol, interval);
  var filtered = filterCandles(cached, options.from, options.to);
  if (!options.forceRefresh && filtered.length >= requested) {
    logger.info("Candle cache hit", { symbol: symbol, interval: interval, candles: filtered.length });
    return Promise.resolve(withMetadata(filtered.slice(-requested), {
      cacheHit: true,
      cacheMiss: false,
      fetchedFromBybit: false,
      bybitRequests: 0
    }, options));
  }

  logger.info("Candle cache miss", { symbol: symbol, interval: interval, requested: requested, cached: cached.length });
  var rows = cached.map(candleToBybitRow);
  var endTime = options.forceRefresh ? undefined : (rows.length ? Math.min.apply(null, rows.map(function (row) { return Number(row[0]); })) - 1 : undefined);
  var maxRequests = Math.ceil(requested / BYBIT_REQUEST_LIMIT) + 2;
  var requestCount = 0;

  function loop() {
    if ((!options.forceRefresh || requestCount > 0) && (normalizeCandles(rows.map(bybitRowToCandle)).length >= requested || requestCount >= maxRequests)) {
      var candles = normalizeCandles(rows.map(bybitRowToCandle));
      saveCachedCandles("bybit", symbol, interval, candles);
      return withMetadata(filterCandles(candles, options.from, options.to).slice(-requested), {
        cacheHit: false,
        cacheMiss: true,
        fetchedFromBybit: requestCount > 0,
        bybitRequests: requestCount
      }, options);
    }
    requestCount += 1;
    return requestBybitKline(symbol, interval, endTime).then(function (batch) {
      if (!batch.length) return withMetadata(normalizeCandles(rows.map(bybitRowToCandle)).slice(-requested), {
        cacheHit: false,
        cacheMiss: true,
        fetchedFromBybit: requestCount > 0,
        bybitRequests: requestCount
      }, options);
      var oldest = Math.min.apply(null, batch.map(function (row) { return Number(row[0]); }));
      rows = rows.concat(batch);
      if (endTime !== undefined && oldest >= endTime) {
        return withMetadata(normalizeCandles(rows.map(bybitRowToCandle)).slice(-requested), {
          cacheHit: false,
          cacheMiss: true,
          fetchedFromBybit: requestCount > 0,
          bybitRequests: requestCount
        }, options);
      }
      endTime = oldest - 1;
      return delay(180).then(loop);
    });
  }

  pendingFetches[pendingKey] = Promise.resolve(loop()).then(function (candles) {
    delete pendingFetches[pendingKey];
    return candles;
  }).catch(function (error) {
    delete pendingFetches[pendingKey];
    throw error;
  });
  return pendingFetches[pendingKey];
}

function withMetadata(candles, metadata, options) {
  if (options && options.withMetadata) {
    return {
      candles: normalizeCandles(candles),
      metadata: metadata || {}
    };
  }
  return normalizeCandles(candles);
}

function candlesForRange(from, to, interval) {
  var fromMs = parseDateToSeconds(from);
  var toMs = parseDateToSeconds(to);
  if (fromMs === null || toMs === null) return null;
  return Math.ceil(((toMs - fromMs) * 1000) / intervalToMs(interval));
}

function requestBybitKline(symbol, interval, endTime, attempt) {
  attempt = attempt || 0;
  var params = {
    category: "linear",
    symbol: symbol.toUpperCase(),
    interval: intervalToBybit(interval),
    limit: BYBIT_REQUEST_LIMIT
  };
  if (endTime !== undefined) params.end = endTime;

  return httpGetJson(BYBIT_KLINE_URL, params).then(function (response) {
    if (response.statusCode === 429 || response.statusCode >= 500) {
      if (attempt >= 4) throw new Error("Bybit request failed after retries");
      return retryDelay(response.headers, attempt).then(function () {
        return requestBybitKline(symbol, interval, endTime, attempt + 1);
      });
    }
    if (response.statusCode >= 400) throw new Error("Bybit HTTP " + response.statusCode);
    if (response.body.retCode === 10006) {
      if (attempt >= 4) throw new Error("Bybit rate limit after retries");
      return retryDelay(response.headers, attempt).then(function () {
        return requestBybitKline(symbol, interval, endTime, attempt + 1);
      });
    }
    if (response.body.retCode !== 0) throw new Error(response.body.retMsg || "Bybit error");
    return response.body.result && response.body.result.list ? response.body.result.list : [];
  });
}

function retryDelay(headers, attempt) {
  var resetMs = Number(headers["x-bapi-limit-reset-timestamp"]);
  var wait = Number.isFinite(resetMs) ? Math.max(500, resetMs - Date.now()) : Math.min(5000, 500 * Math.pow(2, attempt));
  logger.warn("Bybit backoff", {
    waitMs: wait,
    limit: headers["x-bapi-limit"],
    remaining: headers["x-bapi-limit-status"]
  });
  return delay(wait);
}

function httpGetJson(url, params) {
  var query = Object.keys(params).map(function (key) {
    return encodeURIComponent(key) + "=" + encodeURIComponent(params[key]);
  }).join("&");
  return enqueueHttp(function () {
    return rawHttpGetJson(url + "?" + query);
  });
}

function enqueueHttp(task) {
  return new Promise(function (resolve, reject) {
    httpQueue.push({ task: task, resolve: resolve, reject: reject });
    pumpHttpQueue();
  });
}

function pumpHttpQueue() {
  while (activeHttpRequests < 2 && httpQueue.length) {
    var item = httpQueue.shift();
    activeHttpRequests += 1;
    Promise.resolve(item.task()).then(item.resolve, item.reject).then(function () {
      activeHttpRequests -= 1;
      setTimeout(pumpHttpQueue, 150);
    });
  }
}

function rawHttpGetJson(url) {
  return new Promise(function (resolve, reject) {
    https.get(url, function (res) {
      var chunks = "";
      res.on("data", function (chunk) { chunks += chunk; });
      res.on("end", function () {
        try {
          resolve({ statusCode: res.statusCode, headers: res.headers, body: JSON.parse(chunks) });
        } catch (error) {
          reject(error);
        }
      });
    }).on("error", reject);
  });
}

function delay(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

function bybitRowToCandle(row) {
  return {
    time: Math.floor(Number(row[0]) / 1000),
    open: Number(row[1]),
    high: Number(row[2]),
    low: Number(row[3]),
    close: Number(row[4]),
    volume: Number(row[5] || 0)
  };
}

function candleToBybitRow(candle) {
  return [Number(candle.time) * 1000, candle.open, candle.high, candle.low, candle.close, candle.volume || 0];
}

module.exports = {
  BYBIT_REQUEST_LIMIT: BYBIT_REQUEST_LIMIT,
  fetchCandles: fetchCandles,
  normalizeCandles: normalizeCandles,
  filterCandles: filterCandles,
  loadCachedCandles: loadCachedCandles,
  saveCachedCandles: saveCachedCandles,
  intervalToMs: intervalToMs
};
