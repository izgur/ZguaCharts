from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests
import yfinance as yf


BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
YFINANCE_CACHE_DIR = Path(__file__).resolve().parent / ".yfinance-cache"
YFINANCE_CACHE_DIR.mkdir(exist_ok=True)
yf.cache.set_cache_location(str(YFINANCE_CACHE_DIR))

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


def fetch_candles(source: str, symbol: str, timeframe: str, limit: int = 240) -> dict:
    """Single public data entry point used by Flask.

    To add a broker later, implement a fetch function with this signature and
    register it in PROVIDERS plus DATA_SOURCE_CONFIG.
    """
    provider = PROVIDERS.get(source)
    if provider is None:
        raise ValueError(f"Unknown data source: {source}")

    normalized_limit = max(50, min(int(limit), 500))
    candles = provider(symbol, timeframe, normalized_limit)
    return {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles,
    }


def fetch_historical_candles(source: str, symbol: str, timeframe: str, period: str = "60d", limit: int = 500) -> dict:
    """Historical candle entry point for analytics such as backtests.

    Broker adapters should still return plain OHLCV dictionaries. yfinance can
    honor period directly; exchange websocket-style sources use the latest limit.
    """
    if source == "yfinance":
        candles = fetch_yfinance_candles_for_period(symbol, timeframe, period)
    else:
        candles = fetch_candles(source, symbol, timeframe, limit=limit)["candles"]

    return {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "candles": candles,
    }


def fetch_bybit_candles(symbol: str, timeframe: str, limit: int) -> list[dict]:
    response = requests.get(
        BYBIT_KLINE_URL,
        params={
            "category": "linear",
            "symbol": symbol.upper(),
            "interval": bybit_interval(timeframe),
            "limit": limit,
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("retCode") != 0:
        raise ValueError(payload.get("retMsg", "Bybit returned an error"))

    rows = payload.get("result", {}).get("list", [])
    rows.reverse()

    return [
        {
            "time": int(int(row[0]) / 1000),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


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


PROVIDERS: dict[str, Callable[[str, str, int], list[dict]]] = {
    "bybit": fetch_bybit_candles,
    "hyperliquid": fetch_hyperliquid_candles,
    "yfinance": fetch_yfinance_candles,
}
