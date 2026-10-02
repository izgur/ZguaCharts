const fs = require("fs");
const path = require("path");
const https = require("https");
const logger = require("../utils/logger");

const CACHE_DIR = path.join(process.cwd(), ".research-cache");
const BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline";
const BYBIT_FUNDING_URL = "https://api.bybit.com/v5/market/funding/history";
const BYBIT_OPEN_INTEREST_URL = "https://api.bybit.com/v5/market/open-interest";
const BYBIT_REQUEST_LIMIT = 1000;
const BYBIT_FUNDING_LIMIT = 200;
const BYBIT_OPEN_INTEREST_LIMIT = 200;
const memoryCache = {};
const seriesMemoryCache = {};
const pendingFetches = {};
const pendingSeriesFetches = {};
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

function seriesCacheKey(kind, source, symbol, interval) {
  return [kind, source || "bybit", symbol, interval || "all"].join("_").replace(/[^a-zA-Z0-9_.-]/g, "_");
}

function seriesCachePath(kind, source, symbol, interval) {
  ensureCacheDir();
  return path.join(CACHE_DIR, seriesCacheKey(kind, source, symbol, interval) + ".json");
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

function loadCachedSeries(kind, source, symbol, interval) {
  var key = seriesCacheKey(kind, source, symbol, interval);
  if (seriesMemoryCache[key]) return seriesMemoryCache[key].slice();
  var file = seriesCachePath(kind, source, symbol, interval);
  if (!fs.existsSync(file)) return [];
  try {
    var rows = normalizeSeries(JSON.parse(fs.readFileSync(file, "utf8")));
    seriesMemoryCache[key] = rows;
    return rows.slice();
  } catch (error) {
    logger.warn("Ignoring unreadable derivative cache", { file: file, error: error.message });
    return [];
  }
}

function saveCachedSeries(kind, source, symbol, interval, rows) {
  var normalized = normalizeSeries(rows);
  var key = seriesCacheKey(kind, source, symbol, interval);
  seriesMemoryCache[key] = normalized;
  fs.writeFileSync(seriesCachePath(kind, source, symbol, interval), JSON.stringify(normalized));
}

function normalizeCandles(candles) {
  var byTime = {};
  (candles || []).forEach(function (candle) {
    if (!candle || candle.time === undefined) return;
    var row = {
      time: Number(candle.time),
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
      volume: Number(candle.volume || 0)
    };
    ["fundingRate", "fundingRateTime", "openInterest", "openInterestTime", "openInterestChangePct"].forEach(function (key) {
      if (candle[key] === null || candle[key] === undefined || candle[key] === "") return;
      var numeric = Number(candle[key]);
      row[key] = Number.isFinite(numeric) ? numeric : candle[key];
    });
    byTime[Number(candle.time)] = row;
  });
  return Object.keys(byTime).map(Number).sort(function (a, b) { return a - b; }).map(function (time) {
    return byTime[time];
  });
}

function normalizeSeries(rows) {
  var byTime = {};
  (rows || []).forEach(function (row) {
    if (!row || row.time === undefined) return;
    var time = Number(row.time);
    if (!Number.isFinite(time)) return;
    var normalized = Object.assign({}, row, { time: time });
    ["fundingRate", "openInterest", "openInterestChangePct"].forEach(function (key) {
      if (normalized[key] === null || normalized[key] === undefined || normalized[key] === "") return;
      var numeric = Number(normalized[key]);
      normalized[key] = Number.isFinite(numeric) ? numeric : normalized[key];
    });
    byTime[time] = normalized;
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

function minNumber(values, accessor) {
  var min = Infinity;
  for (var i = 0; i < values.length; i += 1) {
    var value = Number(accessor ? accessor(values[i]) : values[i]);
    if (Number.isFinite(value) && value < min) min = value;
  }
  return min;
}

function intervalToBybit(interval) {
  if (String(interval) === "240") return "240";
  if (String(interval).toUpperCase() === "D") return "D";
  if (String(interval).toUpperCase() === "W") return "W";
  if (/^\d+m$/.test(interval)) return interval.slice(0, -1);
  if (/^\d+h$/.test(interval)) return String(Number(interval.slice(0, -1)) * 60);
  if (interval === "1d") return "D";
  if (interval === "1w") return "W";
  return interval;
}

function intervalToMs(interval) {
  interval = String(interval);
  if (/^\d+$/.test(interval)) return Number(interval) * 60 * 1000;
  if (interval.toUpperCase() === "D") return 24 * 60 * 60 * 1000;
  if (interval.toUpperCase() === "W") return 7 * 24 * 60 * 60 * 1000;
  var amount = Number(interval.slice(0, -1));
  var unit = interval.slice(-1);
  if (unit === "m") return amount * 60 * 1000;
  if (unit === "h") return amount * 60 * 60 * 1000;
  if (unit === "d") return amount * 24 * 60 * 60 * 1000;
  if (unit === "w") return amount * 7 * 24 * 60 * 60 * 1000;
  return 60 * 1000;
}

function intervalToOpenInterestInterval(interval) {
  var value = String(interval || "").toLowerCase();
  if (/^\d+m$/.test(value)) return value.replace("m", "min");
  if (/^\d+h$/.test(value)) return value;
  if (/^\d+d$/.test(value)) return value;
  if (/^\d+w$/.test(value)) return "1d";
  if (/^\d+$/.test(value)) return value + "min";
  if (value === "240") return "4h";
  if (value.toUpperCase && value.toUpperCase() === "D") return "1d";
  return value || "1h";
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

function enrichCandlesWithDerivatives(options, candles) {
  options = options || {};
  var normalized = normalizeCandles(candles || []);
  if (!normalized.length || (options.source || "bybit") !== "bybit" || !options.symbol) {
    return Promise.resolve(normalized);
  }
  return Promise.all([
    fetchBybitFundingHistory(options, normalized).catch(function (error) {
      logger.warn("Bybit funding enrichment failed", { symbol: options.symbol, error: error.message });
      return [];
    }),
    fetchBybitOpenInterest(options, normalized).catch(function (error) {
      logger.warn("Bybit open-interest enrichment failed", { symbol: options.symbol, interval: options.interval, error: error.message });
      return [];
    })
  ]).then(function (series) {
    return mergeDerivativeSeries(normalized, series[0], series[1]);
  });
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
  var endTime = options.forceRefresh ? undefined : (rows.length ? minNumber(rows, function (row) { return row[0]; }) - 1 : undefined);
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
      var oldest = minNumber(batch, function (row) { return row[0]; });
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

function candleTimeRangeMs(candles, options) {
  var normalized = normalizeCandles(candles || []);
  var from = parseDateToSeconds(options.from);
  var to = parseDateToSeconds(options.to);
  var first = normalized.length ? normalized[0].time : null;
  var last = normalized.length ? normalized[normalized.length - 1].time : null;
  return {
    start: (from !== null ? from : first || Math.floor(Date.now() / 1000) - 86400 * 365) * 1000,
    end: (to !== null ? to : last || Math.floor(Date.now() / 1000)) * 1000
  };
}

function fetchBybitFundingHistory(options, candles) {
  var symbol = options.symbol;
  var range = candleTimeRangeMs(candles, options);
  var interval = "funding";
  var pendingKey = JSON.stringify({ kind: "funding", symbol: symbol, start: range.start, end: range.end });
  if (pendingSeriesFetches[pendingKey]) return pendingSeriesFetches[pendingKey];
  var cached = loadCachedSeries("funding", "bybit", symbol, interval);
  var filtered = filterSeries(cached, range.start / 1000, range.end / 1000);
  if (!options.forceRefresh && seriesCoversRange(filtered, range.start / 1000, range.end / 1000, 12 * 60 * 60)) {
    return Promise.resolve(filtered);
  }
  var rows = cached.slice();
  var endTime = range.end;
  var maxRequests = Math.max(1, Math.ceil((range.end - range.start) / (8 * 60 * 60 * 1000 * BYBIT_FUNDING_LIMIT)) + 3);
  var requestCount = 0;

  function loop() {
    if (requestCount >= maxRequests || endTime < range.start) {
      var normalized = normalizeSeries(rows);
      saveCachedSeries("funding", "bybit", symbol, interval, normalized);
      return filterSeries(normalized, range.start / 1000, range.end / 1000);
    }
    requestCount += 1;
    return requestBybitFunding(symbol, range.start, endTime).then(function (batch) {
      if (!batch.length) {
        var normalized = normalizeSeries(rows);
        saveCachedSeries("funding", "bybit", symbol, interval, normalized);
        return filterSeries(normalized, range.start / 1000, range.end / 1000);
      }
      rows = rows.concat(batch.map(bybitFundingRowToSeries));
      var oldest = Math.min.apply(null, batch.map(function (row) { return Number(row.fundingRateTimestamp || row.time || 0); }));
      if (!Number.isFinite(oldest) || oldest <= 0) return filterSeries(normalizeSeries(rows), range.start / 1000, range.end / 1000);
      endTime = oldest - 1;
      return delay(180).then(loop);
    });
  }

  pendingSeriesFetches[pendingKey] = Promise.resolve(loop()).then(function (result) {
    delete pendingSeriesFetches[pendingKey];
    return result;
  }).catch(function (error) {
    delete pendingSeriesFetches[pendingKey];
    throw error;
  });
  return pendingSeriesFetches[pendingKey];
}

function fetchBybitOpenInterest(options, candles) {
  var symbol = options.symbol;
  var interval = intervalToOpenInterestInterval(options.interval);
  var range = candleTimeRangeMs(candles, options);
  var pendingKey = JSON.stringify({ kind: "openInterest", symbol: symbol, interval: interval, start: range.start, end: range.end });
  if (pendingSeriesFetches[pendingKey]) return pendingSeriesFetches[pendingKey];
  var cached = loadCachedSeries("openInterest", "bybit", symbol, interval);
  var filtered = filterSeries(cached, range.start / 1000, range.end / 1000);
  if (!options.forceRefresh && seriesCoversRange(filtered, range.start / 1000, range.end / 1000, intervalToMs(options.interval) / 1000 * 3)) {
    return Promise.resolve(filtered);
  }
  var rows = cached.slice();
  var cursor = null;
  var requestCount = 0;
  var maxRequests = Math.max(1, Math.ceil((range.end - range.start) / (intervalToMs(options.interval) * BYBIT_OPEN_INTEREST_LIMIT)) + 3);

  function loop() {
    if (requestCount >= maxRequests) {
      var normalized = normalizeOpenInterestSeries(rows);
      saveCachedSeries("openInterest", "bybit", symbol, interval, normalized);
      return filterSeries(normalized, range.start / 1000, range.end / 1000);
    }
    requestCount += 1;
    return requestBybitOpenInterest(symbol, interval, range.start, range.end, cursor).then(function (payload) {
      var batch = payload.list || [];
      rows = rows.concat(batch.map(bybitOpenInterestRowToSeries));
      cursor = payload.nextPageCursor || null;
      if (!cursor || !batch.length) {
        var normalized = normalizeOpenInterestSeries(rows);
        saveCachedSeries("openInterest", "bybit", symbol, interval, normalized);
        return filterSeries(normalized, range.start / 1000, range.end / 1000);
      }
      return delay(180).then(loop);
    });
  }

  pendingSeriesFetches[pendingKey] = Promise.resolve(loop()).then(function (result) {
    delete pendingSeriesFetches[pendingKey];
    return result;
  }).catch(function (error) {
    delete pendingSeriesFetches[pendingKey];
    throw error;
  });
  return pendingSeriesFetches[pendingKey];
}

function filterSeries(rows, fromSec, toSec) {
  return normalizeSeries(rows).filter(function (row) {
    if (fromSec !== null && row.time < fromSec) return false;
    if (toSec !== null && row.time > toSec) return false;
    return true;
  });
}

function seriesCoversRange(rows, fromSec, toSec, toleranceSeconds) {
  if (!rows.length) return false;
  var first = rows[0].time;
  var last = rows[rows.length - 1].time;
  return first <= fromSec + toleranceSeconds && last >= toSec - toleranceSeconds;
}

function mergeDerivativeSeries(candles, fundingRows, openInterestRows) {
  var funding = normalizeSeries(fundingRows || []);
  var oi = normalizeOpenInterestSeries(openInterestRows || []);
  var fundingIndex = -1;
  var oiIndex = -1;
  return normalizeCandles(candles).map(function (candle) {
    while (fundingIndex + 1 < funding.length && funding[fundingIndex + 1].time <= candle.time) fundingIndex += 1;
    while (oiIndex + 1 < oi.length && oi[oiIndex + 1].time <= candle.time) oiIndex += 1;
    var fundingRow = fundingIndex >= 0 ? funding[fundingIndex] : null;
    var oiRow = oiIndex >= 0 ? oi[oiIndex] : null;
    return Object.assign({}, candle, {
      fundingRate: fundingRow ? fundingRow.fundingRate : null,
      fundingRateTime: fundingRow ? fundingRow.time : null,
      openInterest: oiRow ? oiRow.openInterest : null,
      openInterestTime: oiRow ? oiRow.time : null,
      openInterestChangePct: oiRow && oiRow.openInterestChangePct !== undefined ? oiRow.openInterestChangePct : null
    });
  });
}

function normalizeOpenInterestSeries(rows) {
  var normalized = normalizeSeries(rows);
  var previous = null;
  return normalized.map(function (row) {
    var current = Number(row.openInterest);
    var change = previous && Number.isFinite(current) && Number.isFinite(previous) && previous !== 0
      ? (current - previous) / previous * 100
      : null;
    previous = Number.isFinite(current) ? current : previous;
    return Object.assign({}, row, { openInterestChangePct: change });
  });
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

function requestBybitFunding(symbol, startTime, endTime, attempt) {
  attempt = attempt || 0;
  var params = {
    category: "linear",
    symbol: symbol.toUpperCase(),
    startTime: Math.floor(Number(startTime)),
    endTime: Math.floor(Number(endTime)),
    limit: BYBIT_FUNDING_LIMIT
  };

  return httpGetJson(BYBIT_FUNDING_URL, params).then(function (response) {
    if (response.statusCode === 429 || response.statusCode >= 500) {
      if (attempt >= 4) throw new Error("Bybit funding request failed after retries");
      return retryDelay(response.headers, attempt).then(function () {
        return requestBybitFunding(symbol, startTime, endTime, attempt + 1);
      });
    }
    if (response.statusCode >= 400) throw new Error("Bybit funding HTTP " + response.statusCode);
    if (response.body.retCode === 10006) {
      if (attempt >= 4) throw new Error("Bybit funding rate limit after retries");
      return retryDelay(response.headers, attempt).then(function () {
        return requestBybitFunding(symbol, startTime, endTime, attempt + 1);
      });
    }
    if (response.body.retCode !== 0) throw new Error(response.body.retMsg || "Bybit funding error");
    return response.body.result && response.body.result.list ? response.body.result.list : [];
  });
}

function requestBybitOpenInterest(symbol, intervalTime, startTime, endTime, cursor, attempt) {
  attempt = attempt || 0;
  var params = {
    category: "linear",
    symbol: symbol.toUpperCase(),
    intervalTime: intervalTime,
    startTime: Math.floor(Number(startTime)),
    endTime: Math.floor(Number(endTime)),
    limit: BYBIT_OPEN_INTEREST_LIMIT
  };
  if (cursor) params.cursor = cursor;

  return httpGetJson(BYBIT_OPEN_INTEREST_URL, params).then(function (response) {
    if (response.statusCode === 429 || response.statusCode >= 500) {
      if (attempt >= 4) throw new Error("Bybit open-interest request failed after retries");
      return retryDelay(response.headers, attempt).then(function () {
        return requestBybitOpenInterest(symbol, intervalTime, startTime, endTime, cursor, attempt + 1);
      });
    }
    if (response.statusCode >= 400) throw new Error("Bybit open-interest HTTP " + response.statusCode);
    if (response.body.retCode === 10006) {
      if (attempt >= 4) throw new Error("Bybit open-interest rate limit after retries");
      return retryDelay(response.headers, attempt).then(function () {
        return requestBybitOpenInterest(symbol, intervalTime, startTime, endTime, cursor, attempt + 1);
      });
    }
    if (response.body.retCode !== 0) throw new Error(response.body.retMsg || "Bybit open-interest error");
    var result = response.body.result || {};
    return {
      list: result.list || [],
      nextPageCursor: result.nextPageCursor || ""
    };
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

function bybitFundingRowToSeries(row) {
  return {
    time: Math.floor(Number(row.fundingRateTimestamp || row.time || 0) / 1000),
    fundingRate: Number(row.fundingRate)
  };
}

function bybitOpenInterestRowToSeries(row) {
  return {
    time: Math.floor(Number(row.timestamp || row.time || 0) / 1000),
    openInterest: Number(row.openInterest)
  };
}

module.exports = {
  BYBIT_REQUEST_LIMIT: BYBIT_REQUEST_LIMIT,
  fetchCandles: fetchCandles,
  enrichCandlesWithDerivatives: enrichCandlesWithDerivatives,
  normalizeCandles: normalizeCandles,
  filterCandles: filterCandles,
  loadCachedCandles: loadCachedCandles,
  saveCachedCandles: saveCachedCandles,
  intervalToMs: intervalToMs,
  intervalToOpenInterestInterval: intervalToOpenInterestInterval
};
