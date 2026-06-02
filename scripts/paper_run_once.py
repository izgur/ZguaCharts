from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402


def run_once(client) -> dict:
    response = client.post("/api/paper/run-once")
    payload = response.get_json() or {"ok": False, "error": response.get_data(as_text=True)}
    payload["httpStatus"] = response.status_code
    return payload


def compact(payload: dict) -> dict:
    return payload.get("summary") or {
        "ok": payload.get("ok"),
        "httpStatus": payload.get("httpStatus"),
        "error": payload.get("error"),
        "action": (payload.get("nextAction") or {}).get("action"),
        "reason": (payload.get("nextAction") or {}).get("reason"),
        "paperEnabled": payload.get("paperEnabled"),
        "realTradingEnabled": payload.get("realTradingEnabled"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one safe local paper refresh/tick cycle.")
    parser.add_argument("--loop", action="store_true", help="Repeat run-once until max iterations is reached.")
    parser.add_argument("--interval-minutes", type=float, default=5.0, help="Loop interval in minutes.")
    parser.add_argument("--max-iterations", type=int, default=12, help="Maximum loop iterations.")
    parser.add_argument("--full", action="store_true", help="Print full endpoint payload instead of compact summary.")
    args = parser.parse_args()

    results = []
    with app.test_client() as client:
        iterations = args.max_iterations if args.loop else 1
        for index in range(iterations):
            payload = run_once(client)
            item = payload if args.full else compact(payload)
            item["iteration"] = index + 1
            results.append(item)
            if not args.loop or index >= iterations - 1:
                break
            time.sleep(max(0, args.interval_minutes) * 60)

    output = {
        "ok": all(item.get("ok") for item in results),
        "loop": args.loop,
        "iterations": len(results),
        "results": results,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if output["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
