"""Read-only market adapters for the unified instrument picker."""
from functools import partial
import threading
import time
import requests

MARKETS = {
    "bybit_spot": ("Bybit", "Spot", "spot"),
    "bybit_inverse": ("Bybit", "Futures", "inverse"),
    "binance_futures": ("Binance", "Futures", "fapi"),
    "binance_coinm": ("Binance", "Futures", "dapi"),
}
_candles = {}
_lock = threading.Lock()


def public_request(url, params=None):
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("retCode", 0) != 0:
        raise RuntimeError(payload.get("retMsg", "Exchange request failed"))
    return payload


def binance_url(category):
    host = "fapi" if category == "fapi" else "dapi"
    return "https://" + host + ".binance.com/" + host + "/v1"


def fetch_exchange_candles(source, symbol, timeframe, limit, end_ms=None):
    from data_source import bybit_interval, bybit_rows_to_candles, interval_to_ms
    symbol = str(symbol).upper()
    if not symbol.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Invalid exchange symbol")
    limit = max(1, min(50000, int(limit)))
    key = (source, symbol, timeframe, limit, end_ms)
    with _lock:
        saved = _candles.get(key)
        if saved and time.time() - saved[0] < (600 if end_ms else 10):
            return [dict(row) for row in saved[1]]
    exchange, _kind, category = MARKETS[source]
    rows = {}
    end = end_ms
    while len(rows) < limit:
        batch_limit = min(1000 if exchange == "Bybit" else 1500, limit - len(rows))
        if category == "dapi":
            batch_limit = min(batch_limit, max(1, 200 * 86400000 // interval_to_ms(timeframe)))
        if exchange == "Bybit":
            from data_source import _bybit_rate_limiter
            params = {"category": category, "symbol": symbol, "interval": bybit_interval(timeframe), "limit": batch_limit}
            if end is not None:
                params["end"] = end
            _bybit_rate_limiter.acquire()
            try:
                payload = public_request("https://api.bybit.com/v5/market/kline", params)
            finally:
                _bybit_rate_limiter.release()
            batch = bybit_rows_to_candles(payload["result"]["list"])
        else:
            params = {"symbol": symbol, "interval": timeframe, "limit": batch_limit}
            if category == "dapi":
                end = end if end is not None else int(time.time() * 1000)
                params["startTime"] = max(0, end - 200 * 86400000 + 1)
            if end is not None:
                params["endTime"] = end
            raw = public_request(binance_url(category) + "/klines", params)
            batch = [{"time": int(row[0]) // 1000, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])} for row in raw]
        if not batch:
            break
        for row in batch:
            rows[row["time"]] = row
        earliest = min(row["time"] for row in batch) * 1000
        if end is not None and earliest >= end:
            break
        end = earliest - 1
        if len(batch) < batch_limit:
            break
    result = [rows[key] for key in sorted(rows)][-limit:]
    with _lock:
        if len(_candles) >= 128:
            _candles.pop(next(iter(_candles)))
        _candles[key] = (time.time(), result)
    return [dict(row) for row in result]


PROVIDERS = {source: partial(fetch_exchange_candles, source) for source in MARKETS}
