"""Provider-neutral, cached instrument records. No account or order APIs."""
from functools import lru_cache
import time
import requests
from binance_data import symbol_catalog
from exchange_market_data import MARKETS, binance_url, public_request
from free_exchange_data import FREE_MARKETS, catalog_rows, public_json


def instrument(source, symbol, description, exchange, category, kind, exchange_type):
    display = symbol
    if kind == "Perpetual":
        pair = description.split(" / ")
        display = "".join(pair) if len(pair) == 2 else symbol.replace("_PERP", "")
        display += ".P"
    elif kind == "Spot" and " / " in description:
        display = description.replace(" / ", "")
    return {"id": source + ":" + symbol, "source": source, "symbol": symbol,
            "displaySymbol": display,
            "description": description, "exchange": exchange, "category": category,
            "type": kind, "exchangeType": exchange_type}


@lru_cache(maxsize=32)
def catalog(source, bucket):
    from data_source import DATA_SOURCE_CONFIG, fetch_bybit_instruments
    if source in FREE_MARKETS:
        info = FREE_MARKETS[source]
        return {"instruments": [instrument(source, row["symbol"], row["base"] + " / " + row["quote"] if row["quote"] else row["base"] or row["symbol"],
                info["exchange"], "Crypto", row["type"], "Spot" if row["type"] == "Spot" else "Options" if row["type"] == "Option" else "Futures") for row in catalog_rows(source)]}
    if source == "binance":
        payload = symbol_catalog()
        return {"instruments": [instrument(source, symbol, symbol, "Binance", "Crypto", "Spot", "Spot") for symbol in payload["symbols"]],
                "warning": payload.get("warning")}
    if source in ("bybit", "bybit_spot", "bybit_inverse"):
        category = "linear" if source == "bybit" else MARKETS[source][2]
        records = []
        for row in fetch_bybit_instruments(category):
            if row.get("status") != "Trading":
                continue
            kind = "Spot" if category == "spot" else "Perpetual" if "Perpetual" in row.get("contractType", "") else "Futures"
            description = row.get("baseCoin", "") + " / " + row.get("quoteCoin", "")
            records.append(instrument(source, row["symbol"], description, "Bybit", "Crypto", kind, "Spot" if category == "spot" else "Futures"))
        return {"instruments": records}
    if source in ("binance_futures", "binance_coinm"):
        payload = public_request(binance_url(MARKETS[source][2]) + "/exchangeInfo")
        records = []
        for row in payload["symbols"]:
            if row.get("status", row.get("contractStatus")) != "TRADING":
                continue
            kind = "Perpetual" if row.get("contractType") == "PERPETUAL" else "Futures"
            records.append(instrument(source, row["symbol"], row.get("baseAsset", "") + " / " + row.get("quoteAsset", ""), "Binance", "Crypto", kind, "Futures"))
        return {"instruments": records}
    if source == "hyperliquid":
        payload = public_json("Hyperliquid", "https://api.hyperliquid.xyz/info", body={"type": "allPerpMetas"})
        spot = public_json("Hyperliquid", "https://api.hyperliquid.xyz/info", body={"type": "spotMeta"})
        tokens = {row["index"]: row["name"] for row in spot["tokens"]}
        records = []
        for entry in payload:
            meta = entry[0] if isinstance(entry, list) else entry
            quote = tokens.get(meta.get("collateralToken", 0), "USDC")
            records.extend(instrument(source, row["name"], row["name"] + " / " + quote, "Hyperliquid", "Crypto", "Perpetual", "Futures")
                           for row in meta["universe"] if not row.get("isDelisted"))
        return {"instruments": records}
    return {"instruments": [instrument(source, symbol, symbol, "Yahoo Finance", "Stocks", "Stock", "Cash")
                            for symbol in DATA_SOURCE_CONFIG["sources"][source]["symbols"]]}


def search_catalog(source):
    from data_source import DATA_SOURCE_CONFIG
    if source not in DATA_SOURCE_CONFIG["sources"]:
        raise ValueError("Unknown source")
    return catalog(source, int(time.time() // 600))


@lru_cache(maxsize=256)
def search_finance(query, bucket):
    response = requests.get("https://query1.finance.yahoo.com/v1/finance/search",
                            params={"q": query, "quotesCount": 40, "newsCount": 0},
                            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    response.raise_for_status()
    categories = {"EQUITY": ("Stocks", "Stock"), "ETF": ("Funds", "ETF"), "MUTUALFUND": ("Funds", "Fund"),
                  "FUTURE": ("Futures", "Futures"), "CURRENCY": ("Forex", "Currency"), "INDEX": ("Indices", "Index"),
                  "CRYPTOCURRENCY": ("Crypto", "Spot"), "OPTION": ("Options", "Option"), "BOND": ("Bonds", "Bond")}
    records = []
    for row in response.json().get("quotes", []):
        if not row.get("symbol") or row.get("quoteType") not in categories:
            continue
        category, kind = categories[row["quoteType"]]
        exchange_type = "Futures" if kind == "Futures" else "Options" if kind == "Option" else "Spot" if kind == "Spot" else "Cash"
        records.append(instrument("yfinance", row["symbol"], row.get("shortname") or row.get("longname") or row["symbol"],
                                  row.get("exchDisp") or row.get("exchange") or "Yahoo Finance", category, kind, exchange_type))
    return records
