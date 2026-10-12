from __future__ import annotations

import json
from pathlib import Path
import threading
import time
import requests

BASE_URL = "https://data-api.binance.vision/api/v3"
CATALOG_PATH = Path(__file__).resolve().parent / ".research-cache" / "binance-symbols.json"
_lock = threading.RLock()
_catalog = None
_catalog_time = 0
_candles = {}


def request_public(path, params=None):
    for attempt in range(3):
        response = requests.get(BASE_URL + path, params=params, timeout=15)
        if response.status_code in (429, 503) and attempt < 2:
            time.sleep(min(10, max(1, float(response.headers.get("Retry-After", 2)))))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("Binance market data unavailable")


def symbol_catalog():
    global _catalog, _catalog_time
    with _lock:
        if _catalog and time.time() - _catalog_time < 600:
            return {"symbols": list(_catalog), "stale": False}
        try:
            payload = request_public("/exchangeInfo")
            symbols = sorted({row["symbol"] for row in payload["symbols"] if row.get("status") == "TRADING" and row.get("isSpotTradingAllowed", True)})
            if not symbols:
                raise RuntimeError("Binance returned an empty symbol catalog")
            _catalog = symbols
            _catalog_time = time.time()
            CATALOG_PATH.parent.mkdir(exist_ok=True)
            temporary = CATALOG_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(symbols), encoding="utf-8")
            temporary.replace(CATALOG_PATH)
            return {"symbols": list(symbols), "stale": False}
        except (requests.RequestException, ValueError, KeyError, OSError, RuntimeError) as exc:
            if not _catalog and CATALOG_PATH.exists():
                _catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            if _catalog:
                return {"symbols": list(_catalog), "stale": True, "warning": str(exc)}
            raise RuntimeError("Could not load Binance symbol catalog: " + str(exc))


def fetch_binance_candles(symbol, timeframe, limit, end_ms=None):
    symbol = str(symbol).upper()
    if not symbol.isalnum() or timeframe not in ["1s", "1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]:
        raise ValueError("Invalid Binance symbol or timeframe")
    limit = max(1, min(50000, int(limit)))
    key = (symbol, timeframe, limit, end_ms)
    with _lock:
        cached = _candles.get(key)
        if cached and time.time() - cached[0] < (600 if end_ms else 1 if timeframe=='1s' else 10):
            return [dict(row) for row in cached[1]]
        rows = {}
        end = end_ms
        while len(rows) < limit:
            params = {"symbol": symbol, "interval": timeframe, "limit": min(1000, limit - len(rows))}
            if end is not None:
                params["endTime"] = end
            batch = request_public("/klines", params)
            if not batch:
                break
            for row in batch:
                rows[int(row[0])] = {"time": int(row[0]) // 1000, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5])}
            earliest = min(int(row[0]) for row in batch)
            if end is not None and earliest >= end:
                break
            end = earliest - 1
            if len(batch) < params["limit"]:
                break
        result = [rows[t] for t in sorted(rows)][-limit:]
        if len(_candles) >= 128:
            _candles.pop(next(iter(_candles)))
        _candles[key] = (time.time(), result)
        return [dict(row) for row in result]
