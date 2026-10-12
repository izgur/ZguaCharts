"""Explicit, read-only smoke test; never invoked by the trading runtime."""
import sys
import argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from free_exchange_data import FREE_MARKETS, catalog_rows, fetch_free_candles

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeframe", default="1h")
    args = parser.parse_args()
    for source in args.sources or list(FREE_MARKETS):
        try:
            records = catalog_rows(source)
            symbol = FREE_MARKETS[source]["seed"] or (records[0]["symbol"] if records else "")
            candles = fetch_free_candles(source, symbol, args.timeframe, args.limit) if symbol else []
            print(source, "instruments=" + str(len(records)), "symbol=" + symbol,
                  "candles=" + str(len(candles)), "latest=" + str(candles[-1] if candles else None), flush=True)
        except Exception as exc:
            print(source, "FAILED", type(exc).__name__, str(exc), flush=True)
