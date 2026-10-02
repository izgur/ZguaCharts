from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    RESEARCH_BRAIN_LOOP_STATE_PATH,
    RESEARCH_BRAIN_LOOP_STOP_PATH,
    build_research_brain_plan,
    campaign_args_from_payload,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_state() -> dict:
    try:
        if RESEARCH_BRAIN_LOOP_STATE_PATH.exists():
            return json.loads(RESEARCH_BRAIN_LOOP_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "schemaVersion": "research-brain-loop-v1",
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "rounds": [],
        "running": False,
        "stopRequested": False,
    }


def write_state(state: dict) -> None:
    state["updatedAt"] = utc_now()
    RESEARCH_BRAIN_LOOP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESEARCH_BRAIN_LOOP_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def csv_values(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def run_discovery(args, state: dict, round_index: int) -> list[dict]:
    if args.disable_discovery:
        return []
    cadence = max(1, int(args.discovery_every_rounds or 1))
    if (round_index - 1) % cadence != 0:
        return []
    symbols = csv_values(args.symbols)[: max(1, args.discovery_max_markets)]
    timeframes = csv_values(args.timeframes)[: max(1, args.discovery_max_markets)]
    if not symbols:
        symbols = ["BTCUSDT"]
    if not timeframes:
        timeframes = ["4h"]
    runs = []
    for symbol in symbols:
        for timeframe in timeframes:
            command = [
                "node",
                "scripts/research_candle_pattern_discovery.js",
                "--symbol", symbol,
                "--timeframe", timeframe,
                "--days", str(args.discovery_days),
                "--top", str(args.discovery_top),
                "--holdBars", args.discovery_hold_bars,
                "--sides", args.discovery_sides,
            ]
            record = {
                "round": round_index,
                "startedAt": utc_now(),
                "finishedAt": None,
                "symbol": symbol,
                "timeframe": timeframe,
                "command": " ".join(command),
                "returnCode": None,
                "status": "RUNNING",
                "savedPath": None,
                "top": [],
                "error": None,
            }
            state["currentDiscovery"] = record
            write_state(state)
            print(f"=== DISCOVERY ROUND {round_index}: {symbol} {timeframe} ===", flush=True)
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            record["finishedAt"] = utc_now()
            record["returnCode"] = completed.returncode
            record["status"] = "DONE" if completed.returncode == 0 else "FAILED"
            if completed.stdout:
                try:
                    payload = json.loads(completed.stdout)
                    record["savedPath"] = payload.get("savedPath")
                    record["top"] = payload.get("top", [])[:5]
                    record["viableRows"] = payload.get("viableRows")
                    print(f"Discovery saved: {record['savedPath']} viable={record.get('viableRows')}", flush=True)
                except json.JSONDecodeError:
                    record["error"] = completed.stdout[-1000:]
            if completed.returncode != 0:
                record["error"] = (completed.stderr or record.get("error") or "Discovery command failed.")[-1500:]
                print(f"Discovery failed: {record['error']}", flush=True)
            runs.append(record)
            state.setdefault("discoveryRuns", []).append(record)
            state["discoveryRuns"] = state["discoveryRuns"][-50:]
            state["currentDiscovery"] = None
            write_state(state)
    return runs


def run_round(args, state: dict, round_index: int) -> int:
    discovery_runs = run_discovery(args, state, round_index)
    plan = build_research_brain_plan({
        "mode": args.mode,
        "depth": args.depth,
        "symbols": args.symbols,
        "timeframes": args.timeframes,
        "maxJobs": args.jobs_per_campaign,
        "fileLimit": args.file_limit,
        "rowLimit": args.row_limit,
    })
    payload = plan.get("campaignPayload") or {}
    payload["clearStop"] = True
    command = [sys.executable] + campaign_args_from_payload(payload)
    round_record = {
        "round": round_index,
        "startedAt": utc_now(),
        "finishedAt": None,
        "campaign": payload.get("campaign"),
        "campaignPayload": payload,
        "command": " ".join(command),
        "returnCode": None,
        "status": "RUNNING",
        "plan": {
            "mode": plan.get("mode"),
            "depth": plan.get("depth"),
            "candidateCount": plan.get("candidateCount"),
            "selectedJobs": plan.get("selectedJobs"),
            "warnings": plan.get("warnings"),
            "discoveryRuns": discovery_runs,
        },
    }
    state["running"] = True
    state["currentRound"] = round_record
    state.setdefault("rounds", []).append(round_record)
    write_state(state)
    print("")
    print(f"=== BRAIN LOOP ROUND {round_index}: {payload.get('campaign')} ===", flush=True)
    print(" ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, text=True)
    round_record["finishedAt"] = utc_now()
    round_record["returnCode"] = completed.returncode
    round_record["status"] = "DONE" if completed.returncode == 0 else "FAILED"
    state["currentRound"] = None
    write_state(state)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous safe research brain loop.")
    parser.add_argument("--mode", default="balanced", choices=["balanced", "swing", "scalping"])
    parser.add_argument("--depth", default="focused", choices=["quick", "focused", "deep"])
    parser.add_argument("--symbols", default="BTCUSDT")
    parser.add_argument("--timeframes", default="4h")
    parser.add_argument("--jobs-per-campaign", type=int, default=12)
    parser.add_argument("--max-rounds", type=int, default=0, help="0 means run until stopped.")
    parser.add_argument("--file-limit", type=int, default=180)
    parser.add_argument("--row-limit", type=int, default=900)
    parser.add_argument("--sleep-seconds", type=int, default=5)
    parser.add_argument("--disable-discovery", action="store_true")
    parser.add_argument("--discovery-every-rounds", type=int, default=1)
    parser.add_argument("--discovery-days", type=int, default=1095)
    parser.add_argument("--discovery-top", type=int, default=40)
    parser.add_argument("--discovery-hold-bars", default="6,12,18,24,36")
    parser.add_argument("--discovery-sides", default="long,short")
    parser.add_argument("--discovery-max-markets", type=int, default=1)
    args = parser.parse_args()

    if RESEARCH_BRAIN_LOOP_STOP_PATH.exists():
        RESEARCH_BRAIN_LOOP_STOP_PATH.unlink()
    state = read_state()
    state.update({
        "mode": args.mode,
        "depth": args.depth,
        "symbols": args.symbols,
        "timeframes": args.timeframes,
        "jobsPerCampaign": args.jobs_per_campaign,
        "maxRounds": args.max_rounds,
        "discovery": {
            "enabled": not args.disable_discovery,
            "everyRounds": args.discovery_every_rounds,
            "days": args.discovery_days,
            "top": args.discovery_top,
            "holdBars": args.discovery_hold_bars,
            "sides": args.discovery_sides,
            "maxMarkets": args.discovery_max_markets,
        },
        "running": True,
        "stopRequested": False,
        "startedAt": state.get("startedAt") or utc_now(),
    })
    write_state(state)
    round_index = len(state.get("rounds") or []) + 1
    exit_code = 0
    while True:
        if RESEARCH_BRAIN_LOOP_STOP_PATH.exists():
            state["stopRequested"] = True
            state["running"] = False
            write_state(state)
            print("STOP: brain loop will not start another campaign.", flush=True)
            break
        if args.max_rounds and round_index > args.max_rounds:
            state["running"] = False
            write_state(state)
            break
        exit_code = run_round(args, state, round_index)
        if exit_code != 0:
            state["running"] = False
            state["stopRequested"] = True
            state["error"] = f"Round {round_index} failed with exit code {exit_code}."
            write_state(state)
            break
        round_index += 1
        time.sleep(max(1, args.sleep_seconds))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
