from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_DIR = ROOT / "reports" / "research-campaigns"
STOP_FILE = CAMPAIGN_DIR / "STOP_AFTER_CURRENT"

stop_requested = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def job_id(strategy: str, symbol: str, timeframe: str) -> str:
    return f"{strategy}|{symbol}|{timeframe}".replace(" ", "")


def state_path(campaign: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", campaign.strip() or "default")
    return CAMPAIGN_DIR / f"{safe}.json"


def log_path(campaign: str, job: dict) -> Path:
    safe_campaign = re.sub(r"[^a-zA-Z0-9_.-]+", "_", campaign.strip() or "default")
    safe_job = re.sub(r"[^a-zA-Z0-9_.-]+", "_", job["id"])
    return CAMPAIGN_DIR / safe_campaign / f"{safe_job}.log"


def install_signal_handlers() -> None:
    def request_stop(_signum, _frame) -> None:
        global stop_requested
        stop_requested = True
        print("\nSTOP REQUESTED: current optimizer job will be allowed to finish; no new job will start.", flush=True)

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, request_stop)


def build_jobs(args) -> list[dict]:
    symbols = split_csv(args.symbols)
    timeframes = split_csv(args.timeframes)
    strategies = split_csv(args.strategies)
    jobs = []
    for timeframe in timeframes:
        for symbol in symbols:
            for strategy in strategies:
                jobs.append(
                    {
                        "id": job_id(strategy, symbol, timeframe),
                        "strategy": strategy,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "status": "PENDING",
                        "attempts": 0,
                        "startedAt": None,
                        "finishedAt": None,
                        "returnCode": None,
                        "savedReport": None,
                        "logPath": None,
                        "error": None,
                    }
                )
    return jobs


def load_or_create_state(args) -> dict:
    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(args.campaign)
    desired_jobs = build_jobs(args)
    desired_by_id = {job["id"]: job for job in desired_jobs}
    if path.exists() and not args.reset:
        state = json.loads(path.read_text(encoding="utf-8"))
        existing_by_id = {job.get("id"): job for job in state.get("jobs", []) if job.get("id")}
        merged = []
        for job in desired_jobs:
            if job["id"] in existing_by_id:
                old = existing_by_id[job["id"]]
                old.update({key: job[key] for key in ("strategy", "symbol", "timeframe")})
                merged.append(old)
            else:
                merged.append(job)
        for old_id, old in existing_by_id.items():
            if old_id not in desired_by_id:
                old["status"] = "SKIPPED_OUT_OF_SCOPE"
                merged.append(old)
        state["jobs"] = merged
        state["updatedAt"] = utc_now()
        state["settings"] = settings_from_args(args)
        return state
    return {
        "schemaVersion": "research-campaign-resume-v1",
        "campaign": args.campaign,
        "createdAt": utc_now(),
        "updatedAt": utc_now(),
        "settings": settings_from_args(args),
        "jobs": desired_jobs,
        "stopRequested": False,
        "lastCommand": None,
        "warnings": [],
    }


def settings_from_args(args) -> dict:
    return {
        "symbols": split_csv(args.symbols),
        "timeframes": split_csv(args.timeframes),
        "strategies": split_csv(args.strategies),
        "period": args.period,
        "limit": args.limit,
        "maxCandidates": args.max_candidates,
        "maxCombosPerStrategy": args.max_combos_per_strategy,
        "topN": args.top_n,
        "timeoutSeconds": args.timeout_seconds,
        "runtimeBudgetSeconds": args.runtime_budget_seconds,
        "extraArgs": args.extra_args or "",
    }


def save_state(path: Path, state: dict) -> None:
    state["updatedAt"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def pending_jobs(state: dict) -> list[dict]:
    return [job for job in state.get("jobs", []) if job.get("status") not in {"DONE", "SKIPPED_OUT_OF_SCOPE"}]


def summarize(state: dict) -> dict:
    counts = {}
    for job in state.get("jobs", []):
        counts[job.get("status") or "UNKNOWN"] = counts.get(job.get("status") or "UNKNOWN", 0) + 1
    return {
        "campaign": state.get("campaign"),
        "counts": counts,
        "totalJobs": len(state.get("jobs", [])),
        "pendingJobs": len(pending_jobs(state)),
        "statePath": str(state_path(state.get("campaign") or "default").relative_to(ROOT)),
        "stopFile": str(STOP_FILE.relative_to(ROOT)),
    }


def command_for_job(args, job: dict) -> list[str]:
    command = [
        sys.executable,
        "scripts\\research_autopilot.py",
        "research:focused-optimizer",
        "--symbols",
        job["symbol"],
        "--timeframes",
        job["timeframe"],
        "--strategies",
        job["strategy"],
        "--period",
        args.period,
        "--limit",
        args.limit,
        "--max-candidates",
        str(args.max_candidates),
        "--max-combos-per-strategy",
        str(args.max_combos_per_strategy),
        "--top-n",
        str(args.top_n),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--runtime-budget-seconds",
        str(args.runtime_budget_seconds),
    ]
    if args.optimizer_grid_overrides:
        command.extend(["--optimizer-grid-overrides", args.optimizer_grid_overrides])
    if args.no_walk_forward:
        command.append("--no-walk-forward")
    if args.no_stress:
        command.append("--no-stress")
    if args.no_repro:
        command.append("--no-repro")
    if args.extra_args:
        command.extend(args.extra_args.split())
    return command


def run_job(args, state: dict, path: Path, job: dict) -> int:
    job["status"] = "RUNNING"
    job["attempts"] = int(job.get("attempts") or 0) + 1
    job["startedAt"] = utc_now()
    job["finishedAt"] = None
    job["returnCode"] = None
    job["error"] = None
    log_file = log_path(args.campaign, job)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    job["logPath"] = str(log_file.relative_to(ROOT))
    command = command_for_job(args, job)
    state["lastCommand"] = " ".join(command)
    save_state(path, state)

    print("")
    print(f"=== RUN {job['id']} ===")
    print(f"Log: {job['logPath']}")
    print(" ".join(command))

    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    saved_report = None
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"\n=== {utc_now()} RUN {' '.join(command)} ===\n")
        handle.flush()
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            handle.write(line)
            handle.flush()
            match = re.search(r"Saved report:\s*(.+)\s*$", line)
            if match:
                saved_report = match.group(1).strip()
        return_code = process.wait()

    job["finishedAt"] = utc_now()
    job["returnCode"] = return_code
    job["savedReport"] = saved_report
    if return_code == 0:
        job["status"] = "DONE"
    else:
        job["status"] = "FAILED"
        job["error"] = f"Command exited with {return_code}; see {job['logPath']}"
    save_state(path, state)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Resumable research optimizer campaign runner.")
    parser.add_argument("--campaign", default="default-4h-promising")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--timeframes", default="4h")
    parser.add_argument("--strategies", default="EmaBounceV2,BreakoutRetestV2")
    parser.add_argument("--period", default="1460d")
    parser.add_argument("--limit", default="period")
    parser.add_argument("--max-candidates", type=int, default=240)
    parser.add_argument("--max-combos-per-strategy", type=int, default=180)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=int, default=10800)
    parser.add_argument("--runtime-budget-seconds", type=int, default=10500)
    parser.add_argument("--max-jobs", type=int, default=999)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear-stop", action="store_true")
    parser.add_argument("--no-walk-forward", action="store_true")
    parser.add_argument("--no-stress", action="store_true")
    parser.add_argument("--no-repro", action="store_true")
    parser.add_argument("--optimizer-grid-overrides")
    parser.add_argument("--extra-args", default="")
    args = parser.parse_args()

    install_signal_handlers()
    path = state_path(args.campaign)
    if args.clear_stop and STOP_FILE.exists():
        STOP_FILE.unlink()
    state = load_or_create_state(args)
    save_state(path, state)

    summary = summarize(state)
    if args.status or args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        if args.dry_run:
            for job in pending_jobs(state)[: max(1, args.max_jobs)]:
                print(" ".join(command_for_job(args, job)))
        return 0

    completed_this_run = 0
    exit_code = 0
    while completed_this_run < max(1, args.max_jobs):
        if stop_requested or STOP_FILE.exists():
            state["stopRequested"] = True
            save_state(path, state)
            print("STOP: no new job started. Remove stop file or use --clear-stop to continue.")
            break
        jobs = pending_jobs(state)
        if not jobs:
            print("DONE: campaign has no pending jobs.")
            break
        job = jobs[0]
        exit_code = run_job(args, state, path, job)
        completed_this_run += 1
        if exit_code != 0 and not args.extra_args:
            print("STOP: job failed; fix or rerun to retry remaining jobs.")
            break
        time.sleep(0.2)

    print(json.dumps(summarize(state), indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
