from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app, build_research_confirmed_paper_tick_once, build_research_enable_paper_candidate, build_research_init_active_paper_candidate, build_research_paper_candle_alignment, build_research_paper_freshness, build_research_paper_status, build_research_paper_tick_due, build_research_plan_paper_enable_candidate, build_research_preview_paper_tick, build_research_publish_review_candidate, build_research_refresh_active_paper_data  # noqa: E402


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def queue_counts_text(counts: dict) -> str:
    return ", ".join(f"{key}={counts.get(key, 0)}" for key in ["QUEUED", "RUNNING", "DONE", "FAILED", "SKIPPED"])


def job_label(job: dict | None) -> str:
    if not job:
        return "-"
    strategies = job.get("strategies") or ([job.get("strategy")] if job.get("strategy") else [])
    symbols = job.get("symbols") or ([job.get("symbol")] if job.get("symbol") else [])
    timeframes = job.get("timeframes") or ([job.get("timeframe")] if job.get("timeframe") else [])
    return f"{','.join(str(item) for item in strategies)} {','.join(str(item) for item in symbols)} {','.join(str(item) for item in timeframes)} {job.get('period') or ''}".strip()


def branch_label(row: dict | None) -> str:
    if not row:
        return "-"
    return f"{row.get('strategy') or '-'} {row.get('symbol') or '-'} {row.get('timeframe') or '-'} {row.get('period') or ''}".strip()


def print_status(payload: dict) -> None:
    queue = payload.get("queue") or {}
    memory = payload.get("memory") or {}
    safety = payload.get("safety") or {}
    next_job = (queue.get("nextJobs") or [None])[0]
    print(f"Queue: {queue_counts_text(queue.get('counts') or {})} length={queue.get('length', 0)}")
    print(f"Next job: {job_label(next_job)}")
    print(f"Branches: tested={memory.get('branchesTested', 0)} candidates={memory.get('candidates', 0)} reports={memory.get('sourceReports', 0)}")
    lead = (memory.get("topLeads") or [None])[0]
    rare = (memory.get("promisingButRare") or [None])[0]
    rejected = (memory.get("rejectedBranches") or [])[:3]
    print(f"Best current candidate: {branch_label(lead)}")
    print(f"Best challenger: {branch_label(rare or lead)}")
    print("Rejected branches: " + ("; ".join(branch_label(row) for row in rejected) if rejected else "-"))
    families = memory.get("strategyFamilies") or []
    if families:
        print("Strategy families:")
        for family in families[:8]:
            print(f"- {family.get('strategy')}: {family.get('familyStatus')} ({family.get('reason')})")
    skips = queue.get("lastPlanSkippedJobs") or []
    if skips:
        print("Last plan skips:")
        for item in skips[:8]:
            print(f"- {item.get('skipReason')}: {item.get('branchKey') or job_label(item)} ({item.get('detail') or '-'})")
    elif queue.get("lastPlanWarnings"):
        print("Last plan warnings: " + "; ".join(str(item) for item in queue.get("lastPlanWarnings", [])[:5]))
    print(f"Safety: researchOnly={safety.get('researchOnly')} paperEnabled={safety.get('paperEnabled')} realTradingEnabled={safety.get('realTradingEnabled')} configWritten={safety.get('configWritten')} paperStateChanged={safety.get('paperStateChanged')} liveOrdersTouched={safety.get('liveOrdersTouched')}")


def print_summary(payload: dict) -> None:
    safety = payload.get("safety") or {}
    print(payload.get("summaryText") or "No summary available.")
    if payload.get("learningEvents"):
        print("\nWhat Autopilot learned:")
        for event in payload.get("learningEvents", [])[:5]:
            print(f"- {event.get('text')}")
    print(f"\nBest current candidate: {branch_label(payload.get('bestCurrentCandidate'))}")
    print(f"Best challenger: {branch_label(payload.get('bestChallenger'))}")
    print("Rejected branches: " + ("; ".join(branch_label(row) for row in (payload.get("branchesRejected") or [])[:3]) if payload.get("branchesRejected") else "-"))
    print("Next jobs: " + ("; ".join(job_label(job) for job in (payload.get("nextRecommendedJobs") or [])[:3]) if payload.get("nextRecommendedJobs") else "-"))
    print(f"Safety: researchOnly={safety.get('researchOnly')} paperEnabled={safety.get('paperEnabled')} realTradingEnabled={safety.get('realTradingEnabled')} configWritten={safety.get('configWritten')} paperStateChanged={safety.get('paperStateChanged')} liveOrdersTouched={safety.get('liveOrdersTouched')}")


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
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--json", action="store_true")
    plan = sub.add_parser("plan")
    plan.add_argument("--max-jobs", type=int, default=5)
    plan.add_argument("--mode", choices=["conservative", "balanced", "exploratory"], default="balanced")
    plan.add_argument("--include-cooled", action="store_true")
    plan.add_argument("--force-strategy")
    plan.add_argument("--force-branch")
    sub.add_parser("run-next")
    batch = sub.add_parser("run-batch")
    batch.add_argument("--max-jobs", type=int, default=3, help="Requested batch size. The API applies a safety cap of 3 jobs per batch.")
    summarize = sub.add_parser("summarize")
    summarize.add_argument("--json", action="store_true")
    reset = sub.add_parser("reset-queue")
    reset.add_argument("--confirm", action="store_true")
    backfill = sub.add_parser("backfill-memory")
    backfill.add_argument("--file-limit", type=int, default=250)
    dossier = sub.add_parser("candidate-dossier")
    dossier.add_argument("--strategy", required=True)
    dossier.add_argument("--symbol", required=True)
    dossier.add_argument("--timeframe", required=True)
    prepare = sub.add_parser("prepare-paper-candidate")
    prepare.add_argument("--strategy", required=True)
    prepare.add_argument("--symbol", required=True)
    prepare.add_argument("--timeframe", required=True)
    publish = sub.add_parser("publish-review-candidate")
    publish.add_argument("--strategy", required=True)
    publish.add_argument("--symbol", required=True)
    publish.add_argument("--timeframe", required=True)
    plan_paper = sub.add_parser("plan-paper-enable-candidate")
    plan_paper.add_argument("--strategy", required=True)
    plan_paper.add_argument("--symbol", required=True)
    plan_paper.add_argument("--timeframe", required=True)
    enable_paper = sub.add_parser("enable-paper-candidate")
    enable_paper.add_argument("--strategy", required=True)
    enable_paper.add_argument("--symbol", required=True)
    enable_paper.add_argument("--timeframe", required=True)
    enable_paper.add_argument("--confirm", required=True)
    sub.add_parser("preview-paper-tick")
    sub.add_parser("paper:freshness")
    sub.add_parser("paper:candle-alignment")
    sub.add_parser("paper:refresh-active-data")
    init_active = sub.add_parser("paper:init-active-candidate")
    init_active.add_argument("--confirm", required=True)
    tick_once = sub.add_parser("paper:tick-once")
    tick_once.add_argument("--confirm", required=True)
    sub.add_parser("paper:status")
    sub.add_parser("paper:tick-due")
    args = parser.parse_args()

    with app.test_client() as client:
        if args.command == "status":
            payload, status = get_json(client, "/api/research/autopilot/status")
        elif args.command == "plan":
            payload, status = post_json(client, "/api/research/autopilot/plan", {
                "maxJobs": args.max_jobs,
                "planningMode": args.mode,
                "includeCooled": args.include_cooled,
                "forceStrategy": args.force_strategy,
                "forceBranch": args.force_branch,
            })
        elif args.command == "run-next":
            payload, status = post_json(client, "/api/research/autopilot/run-next")
        elif args.command == "run-batch":
            payload, status = post_json(client, "/api/research/autopilot/run-batch", {"maxJobs": args.max_jobs})
        elif args.command == "summarize":
            payload, status = get_json(client, "/api/research/autopilot/summary")
        elif args.command == "reset-queue":
            payload, status = post_json(client, "/api/research/autopilot/reset-queue", {"confirm": args.confirm})
        elif args.command == "backfill-memory":
            payload, status = post_json(client, "/api/research/autopilot/backfill-memory", {"fileLimit": args.file_limit})
        elif args.command == "candidate-dossier":
            payload, status = post_json(client, "/api/research/autopilot/candidate-dossier", {
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "prepare-paper-candidate":
            payload, status = post_json(client, "/api/research/autopilot/prepare-paper-candidate", {
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "publish-review-candidate":
            payload, status = build_research_publish_review_candidate({
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "plan-paper-enable-candidate":
            payload, status = build_research_plan_paper_enable_candidate({
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "enable-paper-candidate":
            payload, status = build_research_enable_paper_candidate({
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "confirm": args.confirm,
            })
        elif args.command == "paper:status":
            payload, status = build_research_paper_status({})
        elif args.command == "paper:tick-due":
            payload, status = build_research_paper_tick_due({})
        elif args.command == "preview-paper-tick":
            payload, status = build_research_preview_paper_tick({})
        elif args.command == "paper:freshness":
            payload, status = build_research_paper_freshness({})
        elif args.command == "paper:candle-alignment":
            payload, status = build_research_paper_candle_alignment({})
        elif args.command == "paper:refresh-active-data":
            payload, status = build_research_refresh_active_paper_data({})
        elif args.command == "paper:init-active-candidate":
            payload, status = build_research_init_active_paper_candidate({"confirm": args.confirm})
        elif args.command == "paper:tick-once":
            payload, status = build_research_confirmed_paper_tick_once({"confirm": args.confirm})
        else:
            payload, status = {"ok": False, "error": "Unknown command."}, 2
    if args.command == "status" and not args.json:
        print_status(payload)
    elif args.command == "summarize" and not args.json:
        print_summary(payload)
    else:
        print_json(payload)
    return 0 if status < 400 and payload.get("ok", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
