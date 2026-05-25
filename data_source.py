from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import threading
import time
from typing import Callable

import requests
import yfinance as yf


BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
BYBIT_REQUEST_LIMIT = 1000
BYBIT_MAX_REQUESTS_IN_FLIGHT = 2
BYBIT_MIN_REQUEST_DELAY_SECONDS = 0.18
BYBIT_MAX_CACHE_CANDLES = 50000
BYBIT_HISTORICAL_DEFAULT_MAX_CANDLES = 50000
BYBIT_CACHE_STALE_MULTIPLIER = 3
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
BYBIT_DISK_CACHE_DIR = Path(__file__).resolve().parent / ".research-cache"
BYBIT_DISK_CACHE_DIR.mkdir(exist_ok=True)
YFINANCE_CACHE_DIR = Path(__file__).resolve().parent / ".yfinance-cache"
YFINANCE_CACHE_DIR.mkdir(exist_ok=True)
yf.cache.set_cache_location(str(YFINANCE_CACHE_DIR))
LOGGER = logging.getLogger(__name__)

VISIBLE_CHART_LIMITS = {
    1: 20000,
    2: 12000,
    4: 8000,
    6: 5000,
    8: 3000,
}

_bybit_cache = {}
_bybit_cache_lock = threading.Lock()
_bybit_instruments_cache = {}
_bybit_instruments_cache_time = 0.0
BYBIT_SYMBOL_ALIASES = {
    "PEPEUSDT": "1000PEPEUSDT",
}


class BybitRateLimiter:
    def __init__(self) -> None:
        self._semaphore = threading.BoundedSemaphore(BYBIT_MAX_REQUESTS_IN_FLIGHT)
        self._lock = threading.Lock()
        self._next_request_time = 0.0
        self.total_wait_seconds = 0.0

    def acquire(self) -> None:
        self._semaphore.acquire()
        with self._lock:
            now = time.time()
            wait = max(0.0, self._next_request_time - now)
            if wait:
                self.total_wait_seconds += wait
                time.sleep(wait)
            self._next_request_time = time.time() + BYBIT_MIN_REQUEST_DELAY_SECONDS

    def release(self, response=None) -> None:
        try:
            self._apply_headers(response)
        finally:
            self._semaphore.release()

    def backoff(self, seconds: float) -> None:
        with self._lock:
            self.total_wait_seconds += seconds
            self._next_request_time = max(self._next_request_time, time.time() + seconds)

    def _apply_headers(self, response) -> None:
        if response is None:
            return
        headers = response.headers
        remaining = safe_int(headers.get("X-Bapi-Limit-Status"))
        limit = safe_int(headers.get("X-Bapi-Limit"))
        reset_ms = safe_int(headers.get("X-Bapi-Limit-Reset-Timestamp"))
        if remaining is None or limit is None:
            return
        if remaining <= max(2, int(limit * 0.1)):
            wait = BYBIT_MIN_REQUEST_DELAY_SECONDS * 2
            if reset_ms:
                wait = max(wait, min(3.0, (reset_ms / 1000) - time.time()))
            self.backoff(max(wait, 0.25))


_bybit_rate_limiter = BybitRateLimiter()


def safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_symbol_list(symbols: list[str] | str | None, fallback: list[str]) -> list[str]:
    if isinstance(symbols, str):
        items = [item.strip().upper() for item in symbols.split(",") if item.strip()]
    elif isinstance(symbols, list):
        items = [str(item).strip().upper() for item in symbols if str(item).strip()]
    else:
        items = []
    return items or fallback

DATA_SOURCE_CONFIG = {
    "sources": {
        "bybit": {
            "label": "Bybit Crypto",
            "live": "websocket",
            "symbols": [
                "BTCUSDT",
                "ETHUSDT",
                "SOLUSDT",
                "XRPUSDT",
                "DOGEUSDT",
                "BNBUSDT",
                "ADAUSDT",
                "AVAXUSDT",
                "LINKUSDT",
                "TONUSDT",
                "SUIUSDT",
                "LTCUSDT",
                "TRXUSDT",
                "DOTUSDT",
                "BCHUSDT",
                "NEARUSDT",
                "APTUSDT",
                "ARBUSDT",
                "OPUSDT",
                "WIFUSDT",
                "PEPEUSDT",
                "HYPEUSDT",
            ],
            "timeframes": ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"],
        },
        "hyperliquid": {
            "label": "Hyperliquid Crypto",
            "live": "websocket",
            "symbols": ["BTC", "ETH", "SOL", "HYPE", "DOGE", "XRP", "BNB", "AVAX", "LINK", "SUI"],
            "timeframes": ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"],
        },
        "yfinance": {
            "label": "Indian Stocks",
            "live": "polling",
            "symbols": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "ITC.NS", "TATAMOTORS.NS"],
            "timeframes": ["1m", "2m", "5m", "15m", "30m", "60m", "1d"],
        },
    }
}


def fetch_candles(source: str, symbol: str, timeframe: str, limit: int = 240, visible_charts: int | None = None) -> dict:
    """Single public data entry point used by Flask.

    To add a broker later, implement a fetch function with this signature and
    register it in PROVIDERS plus DATA_SOURCE_CONFIG.
    """
    provider = PROVIDERS.get(source)
    if provider is None:
        raise ValueError(f"Unknown data source: {source}")

    normalized_limit = max(50, int(limit))
    diagnostics = {}
    if source == "bybit":
        normalized_limit = adaptive_bybit_limit(normalized_limit, visible_charts)
        candles, diagnostics = fetch_bybit_candles_with_diagnostics(symbol, timeframe, normalized_limit, visible_charts)
    else:
        normalized_limit = min(normalized_limit, 5000)
        candles = provider(symbol, timeframe, normalized_limit)
    return {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_limit": normalized_limit,
        "diagnostics": diagnostics,
        "candles": candles,
    }


def fetch_historical_candles(source: str, symbol: str, timeframe: str, period: str = "60d", limit: int | None = 5000) -> dict:
    """Historical candle entry point for analytics such as backtests.

    Broker adapters should still return plain OHLCV dictionaries. yfinance can
    honor period directly; exchange websocket-style sources use the latest limit.
    """
    if source == "yfinance":
        effective_period = yfinance_backtest_period(timeframe)
        candles = fetch_yfinance_candles_for_period(symbol, timeframe, effective_period)
        diagnostics = historical_diagnostics(period, timeframe, limit, len(candles), len(candles), len(candles), candles)
    elif source == "bybit":
        effective_period = period
        effective_limit = historical_limit_for_period(period, timeframe, limit)
        candles, bybit_diagnostics = fetch_bybit_candles_with_diagnostics(symbol, timeframe, effective_limit)
        diagnostics = historical_diagnostics(
            period,
            timeframe,
            limit,
            candles_needed_for_period(period, timeframe),
            effective_limit,
            BYBIT_MAX_CACHE_CANDLES,
            candles,
        )
        diagnostics.update({"bybit": bybit_diagnostics})
    else:
        effective_period = period
        effective_limit = int(limit or 5000)
        candles = fetch_candles(source, symbol, timeframe, limit=effective_limit, visible_charts=1)["candles"]
        diagnostics = historical_diagnostics(period, timeframe, limit, effective_limit, effective_limit, effective_limit, candles)

    return {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "effective_period": effective_period,
        "limit": diagnostics["effective_limit"],
        "diagnostics": diagnostics,
        "candles": candles,
    }


def fetch_bybit_instruments(category: str = "linear") -> list[dict]:
    global _bybit_instruments_cache_time
    category = category or "linear"
    now = time.time()
    cached = _bybit_instruments_cache.get(category)
    if cached is not None and now - _bybit_instruments_cache_time < 3600:
        return cached

    rows = []
    cursor = None
    for _page in range(20):
        params = {"category": category, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        response = None
        _bybit_rate_limiter.acquire()
        try:
            response = requests.get(BYBIT_INSTRUMENTS_URL, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
        finally:
            _bybit_rate_limiter.release(response)
        if payload.get("retCode") != 0:
            raise ValueError(payload.get("retMsg", "Bybit instruments request failed"))
        result = payload.get("result", {})
        rows.extend(result.get("list", []) or [])
        cursor = result.get("nextPageCursor")
        if not cursor:
            break
    _bybit_instruments_cache[category] = rows
    _bybit_instruments_cache_time = now
    return rows


def get_valid_bybit_symbols(category: str = "linear") -> list[str]:
    symbols = []
    for item in fetch_bybit_instruments(category):
        symbol = str(item.get("symbol") or "").upper()
        status = str(item.get("status") or "").lower()
        if symbol and status in {"trading", ""}:
            symbols.append(symbol)
    return sorted(set(symbols))


def validate_bybit_symbol(symbol: str, valid_symbols: set[str] | None = None) -> dict:
    requested = str(symbol or "").strip().upper()
    valid = valid_symbols if valid_symbols is not None else set(get_valid_bybit_symbols())
    alias = BYBIT_SYMBOL_ALIASES.get(requested)
    if requested in valid:
        return {"symbol": requested, "valid": True, "alias": None, "message": "Symbol is valid."}
    if alias and alias in valid:
        return {"symbol": requested, "valid": False, "alias": alias, "message": f"{requested} is not listed; use {alias}."}
    return {"symbol": requested, "valid": False, "alias": alias if alias else None, "message": "Symbol is not listed by Bybit linear instruments."}


def bybit_symbol_validation_payload(symbols: list[str] | None = None) -> dict:
    configured = symbols or DATA_SOURCE_CONFIG["sources"]["bybit"]["symbols"]
    warnings = []
    try:
        valid_symbols = set(get_valid_bybit_symbols())
    except Exception as exc:
        aliases = {symbol: BYBIT_SYMBOL_ALIASES[symbol] for symbol in configured if symbol in BYBIT_SYMBOL_ALIASES}
        return {
            "configuredSymbols": configured,
            "validSymbols": [],
            "invalidSymbols": [],
            "suggestedAliases": aliases,
            "warnings": [f"Could not fetch Bybit instruments: {exc}"],
        }
    validations = [validate_bybit_symbol(symbol, valid_symbols) for symbol in configured]
    valid_configured = [item["symbol"] for item in validations if item["valid"]]
    invalid = [item["symbol"] for item in validations if not item["valid"]]
    aliases = {item["symbol"]: item["alias"] for item in validations if item.get("alias") and not item["valid"]}
    if aliases:
        current = DATA_SOURCE_CONFIG["sources"]["bybit"]["symbols"]
        DATA_SOURCE_CONFIG["sources"]["bybit"]["symbols"] = [aliases.get(symbol, symbol) for symbol in current]
        warnings.append("Configured Bybit symbols were updated in memory where confirmed aliases exist.")
    return {
        "configuredSymbols": configured,
        "validSymbols": valid_configured,
        "invalidSymbols": invalid,
        "suggestedAliases": aliases,
        "warnings": warnings,
    }


def parse_period_to_days(period: str | None) -> float | None:
    if not period:
        return None
    text = str(period).strip().lower()
    if text in {"max", "all"}:
        return None
    try:
        if text.endswith("d"):
            return float(text[:-1])
        if text.endswith("w"):
            return float(text[:-1]) * 7
        if text.endswith("mo"):
            return float(text[:-2]) * 30
        if text.endswith("m"):
            return float(text[:-1]) * 30
        if text.endswith("y"):
            return float(text[:-1]) * 365
        return float(text)
    except (TypeError, ValueError):
        return None


def timeframe_to_minutes(timeframe: str) -> float:
    text = str(timeframe).strip().lower()
    if text.endswith("m"):
        return float(text[:-1])
    if text.endswith("h"):
        return float(text[:-1]) * 60
    if text.endswith("d"):
        return float(text[:-1]) * 1440
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def candles_needed_for_period(period: str | None, timeframe: str) -> int | None:
    days = parse_period_to_days(period)
    if days is None:
        return None
    minutes = timeframe_to_minutes(timeframe)
    return max(1, int(math.ceil((days * 1440) / minutes)))


def approximate_days_for_candles(candle_count: int, timeframe: str) -> float:
    minutes = timeframe_to_minutes(timeframe)
    return round((max(0, int(candle_count)) * minutes) / 1440, 2)


def historical_limit_for_period(period: str | None, timeframe: str, requested_limit: int | str | None = None) -> int:
    required = candles_needed_for_period(period, timeframe)
    requested = safe_int(requested_limit)
    if requested is None:
        requested = BYBIT_HISTORICAL_DEFAULT_MAX_CANDLES if required is None else required
    target = max(requested, required or 0, BYBIT_HISTORICAL_DEFAULT_MAX_CANDLES if required is None else 50)
    return min(target, BYBIT_MAX_CACHE_CANDLES)


def historical_diagnostics(
    period: str,
    timeframe: str,
    requested_limit,
    required_candles: int | None,
    effective_limit: int,
    provider_max: int,
    candles: list[dict],
) -> dict:
    returned = len(candles)
    requested_days = parse_period_to_days(period)
    approx_days = approximate_days_for_candles(returned, timeframe)
    first_time = int(candles[0]["time"]) if candles else None
    last_time = int(candles[-1]["time"]) if candles else None
    warnings = []
    full_period_covered = True
    if required_candles is not None:
        full_period_covered = returned >= min(required_candles, provider_max)
        if required_candles > provider_max:
            full_period_covered = False
            warnings.append(
                f"Requested {period} on {timeframe} requires {required_candles} candles, capped at {provider_max}."
            )
        elif returned < required_candles:
            full_period_covered = False
            warnings.append(
                f"Requested {period} on {timeframe} requires {required_candles} candles, but source returned {returned}."
            )
    if requested_days is not None and approx_days + 0.01 < requested_days and required_candles is not None:
        warnings.append(f"Approximate returned coverage is {approx_days}d versus requested {requested_days:g}d.")
    return {
        "requested_period": period,
        "requested_limit": requested_limit,
        "period_required_candles": required_candles,
        "effective_limit": effective_limit,
        "provider_max_candles": provider_max,
        "returned_candles": returned,
        "approximate_days_returned": approx_days,
        "first_candle_time": first_time,
        "last_candle_time": last_time,
        "full_period_covered": full_period_covered,
        "period_capped": bool(required_candles and required_candles > provider_max),
        "warnings": warnings,
    }


def adaptive_bybit_limit(requested_limit: int, visible_charts: int | None) -> int:
    return max(50, min(int(requested_limit), bybit_visible_chart_cap(visible_charts)))


def bybit_visible_chart_cap(visible_charts: int | None) -> int:
    chart_count = int(visible_charts or 1)
    allowed_counts = sorted(VISIBLE_CHART_LIMITS)
    closest_count = min(allowed_counts, key=lambda count: abs(count - chart_count))
    return VISIBLE_CHART_LIMITS[closest_count]


def clear_bybit_cache() -> None:
    with _bybit_cache_lock:
        _bybit_cache.clear()


def inspect_bybit_cache(symbol: str, timeframe: str, partial_threshold: int = 1000) -> dict:
    symbol = str(symbol or "").upper()
    cached = load_bybit_disk_cache(symbol, timeframe)
    with _bybit_cache_lock:
        memory_cached = list(_bybit_cache.get((symbol, timeframe), []))
    if len(memory_cached) > len(cached):
        cached = memory_cached
    warnings = []
    cache_file = bybit_disk_cache_path(symbol, timeframe)
    stale_details = {}
    stale = bybit_cache_is_stale(cached, timeframe, stale_details)
    count = len(cached)
    if count == 0:
        status = "MISSING"
        warnings.append("No cached candles found.")
    elif stale:
        status = "STALE"
        warnings.append("Cached latest candle is stale.")
    elif count < partial_threshold:
        status = "PARTIAL"
        warnings.append(f"Cached candles below partial threshold {partial_threshold}.")
    else:
        status = "OK"
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "cachedCandles": count,
        "approximateDays": approximate_days_for_candles(count, timeframe),
        "firstCandleTime": int(cached[0]["time"]) if cached else None,
        "lastCandleTime": int(cached[-1]["time"]) if cached else None,
        "cacheFile": cache_file.name,
        "status": status,
        "warnings": warnings,
        "stale": stale,
        "latestCachedCandleAgeSeconds": stale_details.get("latest_cached_candle_age_seconds"),
        "staleThresholdSeconds": stale_details.get("stale_threshold_seconds"),
    }


def inspect_all_bybit_cache(symbols: list[str] | None = None, timeframes: list[str] | None = None) -> dict:
    symbols = safe_symbol_list(symbols, DATA_SOURCE_CONFIG["sources"]["bybit"]["symbols"])
    timeframes = timeframes or ["15m", "1h", "4h"]
    rows = [inspect_bybit_cache(symbol, timeframe) for symbol in symbols for timeframe in timeframes]
    return {
        "source": "bybit",
        "summary": {
            "symbols": len(symbols),
            "timeframes": len(timeframes),
            "totalCachedCandles": sum(row["cachedCandles"] for row in rows),
            "missingPairs": sum(1 for row in rows if row["status"] == "MISSING"),
            "partialPairs": sum(1 for row in rows if row["status"] == "PARTIAL"),
            "stalePairs": sum(1 for row in rows if row["status"] == "STALE"),
        },
        "rows": rows,
    }


def fetch_bybit_candles(symbol: str, timeframe: str, limit: int) -> list[dict]:
    candles, _diagnostics = fetch_bybit_candles_with_diagnostics(symbol, timeframe, limit)
    return candles


def fetch_bybit_candles_with_diagnostics(
    symbol: str,
    timeframe: str,
    limit: int,
    visible_charts: int | None = None,
) -> tuple[list[dict], dict]:
    symbol = symbol.upper()
    interval = bybit_interval(timeframe)
    requested = max(1, int(limit))
    diagnostics = {
        "symbol": symbol,
        "interval": interval,
        "requested_candles": requested,
        "returned_candles": 0,
        "bybit_requests": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_stale": False,
        "latest_cached_candle_time": None,
        "latest_cached_candle_age_seconds": None,
        "stale_threshold_seconds": bybit_stale_threshold_seconds(timeframe),
        "rate_limit_wait_seconds": 0.0,
        "visible_charts": visible_charts or 1,
        "max_candles_per_chart": bybit_visible_chart_cap(visible_charts),
        "disk_cache_hits": 0,
        "degraded_to_stale_cache": False,
        "warnings": [],
    }
    cache_key = (symbol, timeframe)

    with _bybit_cache_lock:
        cached = list(_bybit_cache.get(cache_key, []))
    if not cached:
        cached = load_bybit_disk_cache(symbol, timeframe)
        if cached:
            diagnostics["disk_cache_hits"] = 1
            with _bybit_cache_lock:
                _bybit_cache[cache_key] = cached[-BYBIT_MAX_CACHE_CANDLES:]

    cache_fresh = not bybit_cache_is_stale(cached, timeframe, diagnostics)
    if diagnostics["disk_cache_hits"] and len(cached) >= requested:
        diagnostics["cache_hits"] = 1
        diagnostics["degraded_to_stale_cache"] = not cache_fresh
        if not cache_fresh:
            diagnostics["warnings"].append("Using stale disk cache because this is the first load after restart.")
        candles = cached[-requested:]
        diagnostics["returned_candles"] = len(candles)
        log_bybit_diagnostics(diagnostics)
        return candles, diagnostics

    if len(cached) >= requested and cache_fresh:
        diagnostics["cache_hits"] = 1
        candles = cached[-requested:]
        diagnostics["returned_candles"] = len(candles)
        log_bybit_diagnostics(diagnostics)
        return candles, diagnostics

    diagnostics["cache_misses"] = 1
    rows = [bybit_candle_to_row(candle) for candle in cached]
    waits_before = _bybit_rate_limiter.total_wait_seconds

    # Always refresh the newest page when the cached tail is stale. Otherwise a
    # chart can end yesterday and then websocket updates appear to jump forward.
    if not cache_fresh:
        try:
            latest_batch = request_bybit_kline_batch(symbol, interval)
            diagnostics["bybit_requests"] += 1
            rows.extend(latest_batch)
        except Exception as exc:
            if not rows:
                raise
            return stale_cached_bybit_response(rows, requested, diagnostics, f"Bybit refresh failed: {exc}")

    oldest_row_time = min((safe_int(row[0]) for row in rows if safe_int(row[0]) is not None), default=None)
    end_time = oldest_row_time - 1 if oldest_row_time is not None else None
    seen_oldest = set()

    while len(rows) < requested:
        try:
            batch = request_bybit_kline_batch(symbol, interval, end_time)
            diagnostics["bybit_requests"] += 1
        except Exception as exc:
            if not rows:
                raise
            diagnostics["warnings"].append(f"Bybit pagination stopped early: {exc}")
            break
        if not batch:
            break

        oldest_time = min(int(row[0]) for row in batch)
        if oldest_time in seen_oldest:
            break
        seen_oldest.add(oldest_time)

        rows.extend(batch)
        new_end_time = oldest_time - 1
        if end_time is not None and new_end_time >= end_time:
            break
        end_time = new_end_time

        if len(batch) < BYBIT_REQUEST_LIMIT:
            break

    candles = bybit_rows_to_candles(rows)
    with _bybit_cache_lock:
        _bybit_cache[cache_key] = candles[-BYBIT_MAX_CACHE_CANDLES:]
    save_bybit_disk_cache(symbol, timeframe, candles[-BYBIT_MAX_CACHE_CANDLES:])

    candles = candles[-requested:]
    diagnostics["returned_candles"] = len(candles)
    diagnostics["rate_limit_wait_seconds"] = round(_bybit_rate_limiter.total_wait_seconds - waits_before, 3)
    log_bybit_diagnostics(diagnostics)
    return candles, diagnostics


def stale_cached_bybit_response(rows: list, requested: int, diagnostics: dict, warning: str) -> tuple[list[dict], dict]:
    candles = bybit_rows_to_candles(rows)[-requested:]
    diagnostics["returned_candles"] = len(candles)
    diagnostics["degraded_to_stale_cache"] = True
    diagnostics["warnings"].append(warning)
    log_bybit_diagnostics(diagnostics)
    return candles, diagnostics


def bybit_disk_cache_path(symbol: str, timeframe: str) -> Path:
    safe_symbol = "".join(char for char in symbol.upper() if char.isalnum())
    safe_timeframe = "".join(char for char in timeframe if char.isalnum())
    return BYBIT_DISK_CACHE_DIR / f"bybit_{safe_symbol}_{safe_timeframe}.json"


def load_bybit_disk_cache(symbol: str, timeframe: str) -> list[dict]:
    path = bybit_disk_cache_path(symbol, timeframe)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    candles = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            candles.append({
                "time": int(item["time"]),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("volume", 0)),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(candles, key=lambda candle: candle["time"])


def save_bybit_disk_cache(symbol: str, timeframe: str, candles: list[dict]) -> None:
    path = bybit_disk_cache_path(symbol, timeframe)
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(candles, handle, separators=(",", ":"))
    except OSError:
        LOGGER.warning("Could not write Bybit disk cache %s", path)


def bybit_cache_is_stale(candles: list[dict], timeframe: str, diagnostics: dict | None = None) -> bool:
    if not candles:
        if diagnostics is not None:
            diagnostics["cache_stale"] = True
        return True

    latest_time = int(candles[-1]["time"])
    age_seconds = max(0, int(datetime.now(timezone.utc).timestamp()) - latest_time)
    threshold = bybit_stale_threshold_seconds(timeframe)
    stale = age_seconds > threshold
    if diagnostics is not None:
        diagnostics["cache_stale"] = stale
        diagnostics["latest_cached_candle_time"] = latest_time
        diagnostics["latest_cached_candle_age_seconds"] = age_seconds
    return stale


def bybit_stale_threshold_seconds(timeframe: str) -> int:
    interval_seconds = int(interval_to_ms(timeframe) / 1000)
    if timeframe == "15m":
        return 45 * 60
    if timeframe == "1h":
        return 3 * 60 * 60
    if timeframe == "4h":
        return 10 * 60 * 60
    return max(interval_seconds * BYBIT_CACHE_STALE_MULTIPLIER, interval_seconds + 300)


def request_bybit_kline_batch(symbol: str, interval: str, end_time: int | None = None) -> list:
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": BYBIT_REQUEST_LIMIT,
    }
    if end_time is not None:
        params["end"] = end_time

    for attempt in range(4):
        response = None
        _bybit_rate_limiter.acquire()
        try:
            response = requests.get(BYBIT_KLINE_URL, params=params, timeout=10)
        except requests.RequestException as exc:
            wait = min(5.0, 0.5 * (2 ** attempt))
            LOGGER.warning("Bybit request error %s. Retrying in %.2fs.", exc, wait)
            _bybit_rate_limiter.backoff(wait)
            time.sleep(wait)
            continue
        finally:
            _bybit_rate_limiter.release(response)

        if response.status_code == 429 or 500 <= response.status_code < 600:
            wait = bybit_retry_wait(response, attempt)
            LOGGER.warning("Bybit temporary error %s. Retrying in %.2fs.", response.status_code, wait)
            _bybit_rate_limiter.backoff(wait)
            time.sleep(wait)
            continue

        response.raise_for_status()
        payload = response.json()
        if payload.get("retCode") == 10006:
            wait = bybit_retry_wait(response, attempt)
            LOGGER.warning("Bybit rate limit retCode 10006. Retrying in %.2fs.", wait)
            _bybit_rate_limiter.backoff(wait)
            time.sleep(wait)
            continue
        if payload.get("retCode") != 0:
            raise ValueError(payload.get("retMsg", "Bybit returned an error"))
        return payload.get("result", {}).get("list", [])

    raise RuntimeError("Bybit request failed after retries")


def bybit_retry_wait(response, attempt: int) -> float:
    reset_ms = safe_int(response.headers.get("X-Bapi-Limit-Reset-Timestamp")) if response is not None else None
    if reset_ms:
        wait = (reset_ms / 1000) - time.time()
        if wait > 0:
            return min(5.0, max(0.5, wait))
    return min(5.0, 0.5 * (2 ** attempt))


def bybit_rows_to_candles(rows: list) -> list[dict]:
    deduped = {}
    for row in rows:
        try:
            deduped[int(row[0])] = row
        except (TypeError, ValueError, IndexError):
            continue
    return [
        {
            "time": int(timestamp / 1000),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for timestamp, row in sorted(deduped.items())
    ]


def bybit_candle_to_row(candle: dict) -> list:
    return [
        int(candle["time"]) * 1000,
        candle["open"],
        candle["high"],
        candle["low"],
        candle["close"],
        candle.get("volume", 0),
    ]


def log_bybit_diagnostics(diagnostics: dict) -> None:
    LOGGER.info(
        "Bybit candles symbol=%s interval=%s requested=%s returned=%s requests=%s cache_hits=%s cache_misses=%s waits=%ss",
        diagnostics.get("symbol"),
        diagnostics.get("interval"),
        diagnostics.get("requested_candles"),
        diagnostics.get("returned_candles"),
        diagnostics.get("bybit_requests"),
        diagnostics.get("cache_hits"),
        diagnostics.get("cache_misses"),
        diagnostics.get("rate_limit_wait_seconds"),
    )


def fetch_hyperliquid_candles(symbol: str, timeframe: str, limit: int) -> list[dict]:
    interval_ms = interval_to_ms(timeframe)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - (interval_ms * (limit + 5))

    response = requests.post(
        HYPERLIQUID_INFO_URL,
        json={
            "type": "candleSnapshot",
            "req": {
                "coin": symbol.upper(),
                "interval": timeframe,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    rows = response.json()

    return [
        {
            "time": int(row["t"] / 1000),
            "open": float(row["o"]),
            "high": float(row["h"]),
            "low": float(row["l"]),
            "close": float(row["c"]),
            "volume": float(row.get("v", 0)),
        }
        for row in rows[-limit:]
    ]


def fetch_yfinance_candles(symbol: str, timeframe: str, limit: int) -> list[dict]:
    period = period_for_yfinance(timeframe, limit)
    candles = fetch_yfinance_candles_for_period(symbol, timeframe, period)
    return candles[-limit:]


def fetch_yfinance_candles_for_period(symbol: str, timeframe: str, period: str) -> list[dict]:
    ticker = yf.Ticker(symbol.upper())
    history = ticker.history(period=period, interval=timeframe, auto_adjust=False, prepost=True)

    if history.empty:
        history = yf.download(
            symbol.upper(),
            period=period,
            interval=timeframe,
            auto_adjust=False,
            prepost=True,
            progress=False,
            threads=False,
        )

    if history.empty:
        return []

    candles = []
    for index, row in history.iterrows():
        if row[["Open", "High", "Low", "Close"]].isna().any():
            continue
        timestamp = index.to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        candles.append(
            {
                "time": int(timestamp.timestamp()),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0) or 0),
            }
        )
    return candles


def interval_to_ms(interval: str) -> int:
    amount = int(interval[:-1])
    unit = interval[-1]
    if unit == "m":
        return amount * 60_000
    if unit == "h":
        return amount * 60 * 60_000
    if unit == "d":
        return amount * 24 * 60 * 60_000
    raise ValueError(f"Unsupported timeframe: {interval}")


def bybit_interval(interval: str) -> str:
    if interval.endswith("m"):
        return interval[:-1]
    if interval.endswith("h"):
        return str(int(interval[:-1]) * 60)
    if interval == "1d":
        return "D"
    raise ValueError(f"Unsupported Bybit timeframe: {interval}")


def period_for_yfinance(interval: str, limit: int) -> str:
    if interval.endswith("m") or interval.endswith("h"):
        minutes = interval_to_ms(interval) / 60_000
        days = max(2, min(60, int((minutes * limit / 390) + 3)))
        return f"{days}d"
    return "2y"


def yfinance_backtest_period(interval: str) -> str:
    mapping = {
        "1m": "7d",
        "2m": "60d",
        "5m": "60d",
        "15m": "60d",
        "30m": "60d",
        "90m": "60d",
        "60m": "730d",
        "1h": "730d",
        "1d": "max",
    }
    return mapping.get(interval, "60d")


PROVIDERS: dict[str, Callable[[str, str, int], list[dict]]] = {
    "bybit": fetch_bybit_candles,
    "hyperliquid": fetch_hyperliquid_candles,
    "yfinance": fetch_yfinance_candles,
}
