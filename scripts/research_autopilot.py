from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def get_json(client, path: str) -> tuple[dict, int]:
    response = client.get(path)
    payload = response.get_json() or {"ok": False, "error": response.get_data(as_text=True)}
    return payload, response.status_code


def post_json(client, path: str, body: dict | None = None) -> tuple[dict, int]:
    response = client.post(path, json=body or {})
    payload = response.get_json() or {"ok": False, "error": response.get_data(as_text=True)}
    return payload, response.status_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Run safe research-only Autopilot queue commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    plan = sub.add_parser("plan")
    plan.add_argument("--max-jobs", type=int, default=12)
    sub.add_parser("run-next")
    batch = sub.add_parser("run-batch")
    batch.add_argument("--max-jobs", type=int, default=3)
    sub.add_parser("summarize")
    reset = sub.add_parser("reset-queue")
    reset.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    with app.test_client() as client:
        if args.command == "status":
            payload, status = get_json(client, "/api/research/autopilot/status")
        elif args.command == "plan":
            payload, status = post_json(client, "/api/research/autopilot/plan", {"maxJobs": args.max_jobs})
        elif args.command == "run-next":
            payload, status = post_json(client, "/api/research/autopilot/run-next")
        elif args.command == "run-batch":
            payload, status = post_json(client, "/api/research/autopilot/run-batch", {"maxJobs": args.max_jobs})
        elif args.command == "summarize":
            payload, status = get_json(client, "/api/research/autopilot/summary")
        elif args.command == "reset-queue":
            payload, status = post_json(client, "/api/research/autopilot/reset-queue", {"confirm": args.confirm})
        else:
            payload, status = {"ok": False, "error": "Unknown command."}, 2
    print_json(payload)
    return 0 if status < 400 and payload.get("ok", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
