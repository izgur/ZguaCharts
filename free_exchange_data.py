"""Public, read-only exchange catalogs and backwards-paginated OHLCV."""
from datetime import datetime, timezone
from functools import partial
import math
import re
import threading
import time
import requests

TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]


def market(exchange, kind, batch, seed, category="", history="Paginated public history", timeframes=None, cap=None):
    return {"exchange": exchange, "marketType": kind, "batchSize": batch, "seed": seed, "category": category,
            "label": exchange + " " + kind + (" " + category.upper() if category in ("btc", "usd1") else " " + category.replace("-FUTURES", "") if exchange == "Bitget" and kind == "Perpetual" else ""),
            "historyNote": history, "timeframes": timeframes or TIMEFRAMES, "historyCap": cap}


FREE_MARKETS = {
    "bitget_spot": market("Bitget", "Spot", 100, "BTCUSDT", "SPOT", timeframes=[t for t in TIMEFRAMES if t != "1w"] + ["6h", "12h"]),
    "bitget_perpetual": market("Bitget", "Perpetual", 100, "BTCUSDT", "USDT-FUTURES", timeframes=[t for t in TIMEFRAMES if t != "1w"] + ["6h", "12h"]),
    "bitget_coinm": market("Bitget", "Perpetual", 100, "BTCUSD_CM", "COIN-FUTURES", timeframes=[t for t in TIMEFRAMES if t != "1w"] + ["6h", "12h"]),
    "bitget_usdc": market("Bitget", "Perpetual", 100, "BTCPERP", "USDC-FUTURES", timeframes=[t for t in TIMEFRAMES if t != "1w"] + ["6h", "12h"]),
    "mexc_spot": market("MEXC", "Spot", 500, "BTCUSDT", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "okx_spot": market("OKX", "Spot", 300, "BTC-USDT", "SPOT"),
    "okx_perpetual": market("OKX", "Perpetual", 300, "BTC-USDT-SWAP", "SWAP"),
    "okx_futures": market("OKX", "Futures", 300, "", "FUTURES"),
    "okx_options": market("OKX", "Option", 300, "", "OPTION"),
    "gate_spot": market("Gate.io", "Spot", 1000, "BTC_USDT", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "gate_perpetual": market("Gate.io", "Perpetual", 2000, "BTC_USDT", "usdt", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "gate_inverse": market("Gate.io", "Perpetual", 2000, "BTC_USD", "btc", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "gate_usd1": market("Gate.io", "Perpetual", 2000, "", "usd1", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "gate_delivery": market("Gate.io", "Futures", 2000, "", "usdt", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "gate_delivery_inverse": market("Gate.io", "Futures", 2000, "", "btc", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "kucoin_spot": market("KuCoin", "Spot", 1500, "BTC-USDT"),
    "kucoin_futures": market("KuCoin", "Perpetual", 200, "XBTUSDTM", history="Below 8h: history starts 2025-01-01 on the unified API"),
    "kraken_spot": market("Kraken", "Spot", 720, "XBTUSD", history="REST: latest 720 native bars only; older history requires free OHLCVT archives", timeframes=[t for t in TIMEFRAMES if t != "3m"], cap=720),
    "kraken_futures": market("Kraken", "Perpetual", 1000, "PF_XBTUSD", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "coinbase_spot": market("Coinbase", "Spot", 300, "BTC-USD", timeframes=[t for t in TIMEFRAMES if t != "3m"]),
    "hyperliquid_spot": market("Hyperliquid", "Spot", 5000, "PURR/USDC", history="Latest 5,000 candles only", cap=5000),
    "binance_options": market("Binance", "Option", 1500, "", "eapi"),
}
_cache = {}
_cache_lock = threading.RLock()
_provider_locks = {}
_next_requests = {}


def public_json(exchange, url, params=None, body=None):
    with _cache_lock:
        lock = _provider_locks.setdefault(exchange, threading.Lock())
    with lock:
        wait = max(0, _next_requests.get(exchange, 0) - time.monotonic())
        if wait:
            time.sleep(wait)
        for attempt in range(3):
            response = requests.post(url, json=body, timeout=15) if body is not None else requests.get(url, params=params, headers={"User-Agent": "ZguaCharts/0.2"}, timeout=15)
            _next_requests[exchange] = time.monotonic() + (1.0 if exchange == "Kraken" else 0.15)
            if response.status_code in (429, 503) and attempt < 2:
                try:
                    delay = float(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                except ValueError:
                    delay = 2 ** (attempt + 1)
                time.sleep(min(30, max(1, delay)))
                continue
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                code = payload.get("code")
                if code is not None and str(code) not in ("0", "00000", "200000"):
                    raise RuntimeError(payload.get("msg", "Public market request failed") + " (" + str(code) + ")")
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                if payload.get("result") == "error":
                    raise RuntimeError(str(payload.get("error", "Exchange error")))
            return payload
    raise RuntimeError("Public market data unavailable")


def catalog_rows(source):
    info = FREE_MARKETS[source]
    exchange, kind, category = info["exchange"], info["marketType"], info["category"]
    rows = []
    def add(symbol, base="", quote="", record_kind=None):
        rows.append({"symbol": symbol, "base": base.replace("XBT", "BTC"), "quote": quote, "type": record_kind or kind})
    if exchange == "Bitget":
        payload = public_json(exchange, "https://api.bitget.com/api/v3/market/instruments", {"category": category})
        for row in payload["data"]:
            if row.get("status") == "online" and row.get("symbolType", "crypto") == "crypto" and row.get("isReality", "no") == "no" and row.get("isRwa", "NO") == "NO":
                add(row["symbol"], row["baseCoin"], row["quoteCoin"])
    elif exchange == "MEXC":
        payload = public_json(exchange, "https://api.mexc.com/api/v3/exchangeInfo")
        for row in payload["symbols"]:
            if row.get("status") in ("1", "ENABLED", "TRADING") and row.get("isSpotTradingAllowed", True):
                add(row["symbol"], row["baseAsset"], row["quoteAsset"])
    elif exchange == "OKX":
        families = [None]
        if category == "OPTION":
            payload = public_json(exchange, "https://www.okx.com/api/v5/public/underlying", {"instType": category})
            families = [name for group in payload["data"] for name in (group if isinstance(group, list) else [group])]
        for family in families:
            params = {"instType": category}
            if family:
                params["uly"] = family
            payload = public_json(exchange, "https://www.okx.com/api/v5/public/instruments", params)
            for row in payload["data"]:
                if row.get("state") != "live":
                    continue
                base, quote = (row.get("instFamily") or row["instId"]).split("-")[:2]
                add(row["instId"], row.get("baseCcy") or base, row.get("quoteCcy") or quote)
    elif exchange == "Gate.io":
        branch = "spot/currency_pairs" if kind == "Spot" else ("delivery/" if kind == "Futures" else "futures/") + category + "/contracts"
        payload = public_json(exchange, "https://api.gateio.ws/api/v4/" + branch)
        for row in payload:
            if kind == "Spot" and row.get("trade_status") != "tradable" or row.get("in_delisting"):
                continue
            symbol = row.get("id") if kind == "Spot" else row["name"]
            parts = symbol.split("_")
            add(symbol, row.get("base") or parts[0], row.get("quote") or (parts[1] if len(parts) > 1 else ""))
    elif exchange == "KuCoin":
        url = "https://api.kucoin.com/api/v2/symbols" if kind == "Spot" else "https://api-futures.kucoin.com/api/v1/contracts/active"
        for row in public_json(exchange, url)["data"]:
            if kind == "Spot" and not row.get("enableTrading"):
                continue
            if kind != "Spot" and row.get("status") not in (None, "Open"):
                continue
            add(row["symbol"], row["baseCurrency"], row["quoteCurrency"], "Futures" if row.get("expireDate") else kind)
    elif exchange == "Kraken":
        if kind == "Spot":
            payload = public_json(exchange, "https://api.kraken.com/0/public/AssetPairs")
            for symbol, row in payload["result"].items():
                if row.get("status", "online") != "online" or not row.get("wsname"):
                    continue
                base, quote = row["wsname"].split("/")
                add(row.get("altname") or symbol, base, quote)
        else:
            for row in public_json(exchange, "https://futures.kraken.com/derivatives/api/v3/instruments")["instruments"]:
                if not row.get("tradeable", True) or row.get("isExpired"):
                    continue
                pair = row.get("pair", "").replace(":", "/").split("/")
                add(row["symbol"], pair[0], pair[1] if len(pair) > 1 else "", "Futures" if row.get("lastTradingTime") else kind)
    elif exchange == "Coinbase":
        for row in public_json(exchange, "https://api.exchange.coinbase.com/products"):
            if row.get("status") == "online" and not row.get("trading_disabled"):
                add(row["id"], row["base_currency"], row["quote_currency"])
    elif source == "hyperliquid_spot":
        payload = public_json(exchange, "https://api.hyperliquid.xyz/info", body={"type": "spotMeta"})
        tokens = {row["index"]: row["name"] for row in payload["tokens"]}
        for row in payload["universe"]:
            if not row.get("isDelisted"):
                add(row["name"], tokens[row["tokens"][0]], tokens[row["tokens"][1]])
    elif source == "binance_options":
        payload = public_json(exchange, "https://eapi.binance.com/eapi/v1/exchangeInfo")
        for row in payload.get("optionSymbols", []):
            if int(row.get("expiryDate", 0)) > int(time.time() * 1000):
                add(row["symbol"], row.get("underlying", ""), "")
    return rows


def seconds(timeframe):
    return int(timeframe[:-1]) * {"s":1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[timeframe[-1]]


def candle(timestamp, open_, high, low, close, volume):
    values = [float(value) for value in (open_, high, low, close, volume)]
    if not all(math.isfinite(value) for value in values) or values[0] <= 0 or values[3] <= 0:
        raise ValueError("Exchange returned invalid candle values")
    return dict(zip(("time", "open", "high", "low", "close", "volume"), [int(timestamp)] + values))


def iso_time(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def request_page(source, symbol, timeframe, end_ms, count):
    info = FREE_MARKETS[source]
    exchange = info["exchange"]
    duration = seconds(timeframe)
    end = int(end_ms) // 1000
    start = max(0, end - count * duration + 1)
    if exchange == "Bitget":
        interval = timeframe[:-1]+timeframe[-1].upper() if timeframe.endswith(("h", "d")) else timeframe
        count = min(count, 100, max(1, 90*86400 // duration))
        params = {"category": info["category"], "symbol": symbol, "interval": interval, "startTime": max(0, end_ms-count*duration*1000+1), "endTime": end_ms, "limit": min(100,count), "type": "market"}
        endpoint = "candles" if params["startTime"] >= int(time.time()*1000)-90*86400000 else "history-candles"
        raw = public_json(exchange, "https://api.bitget.com/api/v3/market/" + endpoint, params)["data"]
        return [candle(int(row[0]) // 1000, *row[1:6]) for row in raw]
    if exchange == "MEXC":
        interval = {"1h": "60m", "1w": "1W"}.get(timeframe,timeframe)
        count = min(500,count)
        start = max(0,end-count*duration+1)
        raw = public_json(exchange, "https://api.mexc.com/api/v3/klines", {"symbol": symbol, "interval": interval, "startTime": start*1000, "endTime": end_ms, "limit": count})
        return [candle(int(row[0]) // 1000, *row[1:6]) for row in raw]
    if exchange == "OKX":
        bar = {"1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}.get(timeframe, timeframe)
        raw = public_json(exchange, "https://www.okx.com/api/v5/market/history-candles", {"instId": symbol, "bar": bar, "after": end_ms + 1, "limit": count})["data"]
        return [candle(int(row[0]) // 1000, *row[1:6]) for row in raw]
    if exchange == "Gate.io":
        branch = "spot" if info["marketType"] == "Spot" else ("delivery/" if info["marketType"] == "Futures" else "futures/") + info["category"]
        # Gate counts both endpoint buckets toward the maximum page size.
        aligned_end = end // duration * duration
        params = {"currency_pair" if branch == "spot" else "contract": symbol, "interval": "7d" if timeframe == "1w" else timeframe,
                  "from": max(0, aligned_end - (count - 1) * duration), "to": aligned_end}
        raw = public_json(exchange, "https://api.gateio.ws/api/v4/" + branch + "/candlesticks", params)
        if branch == "spot":
            return [candle(row[0], row[5], row[3], row[4], row[2], row[6] if len(row) > 6 else row[1]) for row in raw]
        return [candle(row["t"], row["o"], row["h"], row["l"], row["c"], row.get("v", 0)) for row in raw]
    if exchange == "KuCoin":
        interval = timeframe[:-1] + {"m": "min", "h": "hour", "d": "day", "w": "week"}[timeframe[-1]]
        params = {"symbol": symbol, "tradeType": "SPOT" if info["marketType"] == "Spot" else "FUTURES", "klineType": "TRADE", "interval": interval, "startAt": start, "endAt": end}
        raw = public_json(exchange, "https://api.kucoin.com/api/ua/v2/market/kline", params)["data"]["list"]
        return [candle(row[0], *row[1:6]) for row in raw]
    if source == "kraken_spot":
        payload = public_json(exchange, "https://api.kraken.com/0/public/OHLC", {"pair": symbol, "interval": duration // 60})
        raw = next(rows for key, rows in payload["result"].items() if key != "last")
        return [candle(row[0], *row[1:5], row[6]) for row in raw if int(row[0]) <= end]
    if source == "kraken_futures":
        payload = public_json(exchange, "https://futures.kraken.com/api/charts/v1/trade/" + symbol + "/" + timeframe, {"from": start, "to": end, "count": count})
        return [candle(int(row["time"]) // 1000, row["open"], row["high"], row["low"], row["close"], row["volume"]) for row in payload["candles"]]
    if exchange == "Coinbase":
        raw = public_json(exchange, "https://api.exchange.coinbase.com/products/" + symbol + "/candles", {"granularity": duration, "start": iso_time(start), "end": iso_time(end)})
        return [candle(row[0], row[3], row[2], row[1], row[4], row[5]) for row in raw]
    if source == "hyperliquid_spot":
        raw = public_json(exchange, "https://api.hyperliquid.xyz/info", body={"type": "candleSnapshot", "req": {"coin": symbol, "interval": timeframe, "startTime": start * 1000, "endTime": end_ms}})
        return [candle(int(row["t"]) // 1000, row["o"], row["h"], row["l"], row["c"], row["v"]) for row in raw]
    if source == "binance_options":
        raw = public_json(exchange, "https://eapi.binance.com/eapi/v1/klines", {"symbol": symbol, "interval": timeframe, "limit": count, "endTime": end_ms})
        return [candle(int(row[0]) // 1000, *row[1:6]) for row in raw]
    raise ValueError("Unknown public market")


def resample_candles(rows, timeframe):
    import pandas as pd
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    frame.index = pd.to_datetime(frame.pop("time"), unit="s", utc=True)
    rule = "W-MON" if timeframe == "1w" else timeframe[:-1] + {"m": "min", "h": "h", "d": "D"}[timeframe[-1]]
    grouped = frame.resample(rule, label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return [candle(int(index.timestamp()), row.open, row.high, row.low, row.close, row.volume) for index, row in grouped.iterrows()]


def fetch_free_candles(source, symbol, timeframe, limit, end_ms=None):
    info = FREE_MARKETS[source]
    if timeframe not in info["timeframes"] or not re.fullmatch(r"[A-Za-z0-9_:@./=-]{1,96}", symbol):
        raise ValueError("Invalid symbol or unsupported timeframe")
    limit = max(1, min(50000, int(limit), info.get("historyCap") or 50000))
    key = (source, symbol, timeframe, limit, end_ms)
    with _cache_lock:
        saved = _cache.get(key)
        if saved and time.time() - saved[0] < (600 if end_ms else 1 if timeframe=='1s' else 10):
            return [dict(row) for row in saved[1]]
    # Coinbase exposes only these native granularities; aggregate with pandas.
    base = {"30m": "15m", "4h": "1h", "1w": "1d"}.get(timeframe, timeframe) if source == "coinbase_spot" else timeframe
    multiplier = seconds(timeframe) // seconds(base)
    target = limit * multiplier + (multiplier if base != timeframe else 0)
    end = int(end_ms if end_ms is not None else time.time() * 1000)
    latest_end = end
    rows = {}
    while len(rows) < target:
        count = min(info["batchSize"], target - len(rows))
        batch = request_page(source, symbol, base, end, count)
        valid = [row for row in batch if row["time"] * 1000 <= end]
        before = len(rows)
        for row in valid:
            rows[row["time"]] = row
        if not valid or len(rows) == before:
            break
        earliest = min(row["time"] for row in valid) * 1000
        if earliest > end:
            break
        end = earliest - 1
        if source in ("kraken_spot", "hyperliquid_spot"):
            break
    result = [rows[t] for t in sorted(rows)]
    if base != timeframe:
        result = resample_candles(result, timeframe)
    result = [row for row in result if row["time"] * 1000 <= latest_end][-limit:]
    with _cache_lock:
        if len(_cache) >= 128:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.time(), result)
    return [dict(row) for row in result]


PROVIDERS = {source: partial(fetch_free_candles, source) for source in FREE_MARKETS}
