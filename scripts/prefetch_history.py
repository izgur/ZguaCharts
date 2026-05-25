from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_source import BYBIT_MAX_CACHE_CANDLES, bybit_symbol_validation_payload, fetch_historical_candles, validate_bybit_symbol  # noqa: E402


def csv_values(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prefetch Bybit historical candles into the local disk cache.")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--timeframes", default="15m,1h,4h")
    parser.add_argument("--period", default="max")
    parser.add_argument("--limit", type=int, default=BYBIT_MAX_CACHE_CANDLES)
    args = parser.parse_args()

    symbols = csv_values(args.symbols)
    timeframes = csv_values(args.timeframes)
    limit = min(max(1, args.limit), BYBIT_MAX_CACHE_CANDLES)
    validation = bybit_symbol_validation_payload(symbols)
    print(f"Validation: invalid={validation['invalidSymbols']} aliases={validation['suggestedAliases']}")
    validation_unavailable = bool(validation.get("warnings")) and not validation.get("validSymbols") and not validation.get("invalidSymbols")

    ok = 0
    errors = 0
    for symbol in symbols:
        if validation_unavailable:
            symbol_check = {"valid": True, "alias": validation.get("suggestedAliases", {}).get(symbol), "message": "Validation unavailable; attempting fetch directly."}
        else:
            try:
                symbol_check = validate_bybit_symbol(symbol)
            except Exception as exc:
                symbol_check = {"valid": False, "alias": validation.get("suggestedAliases", {}).get(symbol), "message": f"Could not validate symbol: {exc}"}
        if not symbol_check.get("valid") and not symbol_check.get("alias"):
            print(f"SKIP {symbol}: {symbol_check.get('message')}")
            errors += len(timeframes)
            continue
        resolved = symbol_check.get("alias") or symbol
        for timeframe in timeframes:
            try:
                payload = fetch_historical_candles("bybit", resolved, timeframe, period=args.period, limit=limit)
                diag = payload.get("diagnostics", {})
                print(f"OK {symbol} {timeframe}: resolved={resolved} candles={len(payload.get('candles', []))} coverage={diag.get('approximate_days_returned')}d")
                for warning in diag.get("warnings", []):
                    print(f"  WARN {warning}")
                ok += 1
            except Exception as exc:
                print(f"ERROR {symbol} {timeframe}: {exc}")
                errors += 1
    print(f"Summary: ok={ok} errors={errors} limit={limit} period={args.period}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
