from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402


def raise_keyboard_interrupt(_signum, _frame) -> None:
    raise KeyboardInterrupt


signal.signal(signal.SIGINT, raise_keyboard_interrupt)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, raise_keyboard_interrupt)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_once(client) -> dict:
    response = client.post("/api/paper/run-once")
    payload = response.get_json() or {"ok": False, "error": response.get_data(as_text=True)}
    payload["httpStatus"] = response.status_code
    return payload


def get_json(client, path: str) -> dict:
    response = client.get(path)
    payload = response.get_json() or {"ok": False, "error": response.get_data(as_text=True)}
    payload["httpStatus"] = response.status_code
    return payload


def guided_preflight(client) -> dict:
    status = get_json(client, "/api/paper/status")
    stop_rules = get_json(client, "/api/paper/stop-rules")
    tick_readiness = get_json(client, "/api/paper/tick-readiness")
    observation_targets = get_json(client, "/api/paper/observation-targets")
    observation_report = get_json(client, "/api/paper/observation-report")
    paper_enabled = bool(status.get("paperEnabled"))
    real_enabled = bool(status.get("realTradingEnabled"))
    stop_status = stop_rules.get("status")
    blockers = []
    if not paper_enabled:
        blockers.append("paper_disabled")
    if real_enabled:
        blockers.append("real_trading_enabled")
    if stop_status in {"STOP_RECOMMENDED", "PAUSE_RECOMMENDED", "BLOCKED"}:
        blockers.append(f"stop_rules_{stop_status.lower()}")
    return {
        "ok": not blockers,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "blockers": blockers,
        "status": status,
        "stopRules": stop_rules,
        "tickReadiness": tick_readiness,
        "observationTargets": observation_targets,
        "observationReport": observation_report,
        "nextAction": observation_report.get("verdict", {}).get("nextAction") or observation_targets.get("nextAction") or tick_readiness.get("nextAction") or {},
    }


def guided_run_once(client) -> dict:
    preflight = guided_preflight(client)
    if not preflight.get("ok"):
        return {
            "ok": True,
            "guided": True,
            "tickRan": False,
            "paperEnabled": preflight.get("paperEnabled"),
            "realTradingEnabled": preflight.get("realTradingEnabled"),
            "preflight": preflight,
            "skipReason": ", ".join(preflight.get("blockers") or ["guided_preflight_blocked"]),
            "nextAction": {
                "action": "SKIP_GUIDED_RUN",
                "reason": "Guided runner skipped before POST /api/paper/run-once because preflight blockers were present.",
            },
            "httpStatus": 200,
        }
    payload = run_once(client)
    payload["guided"] = True
    payload["preflight"] = preflight
    return payload


def compact_iteration(payload: dict, iteration: int) -> dict:
    summary = payload.get("summary") or {}
    refresh = payload.get("refresh") or {}
    tick_result = payload.get("tickResult") or {}
    tick_summary = tick_result.get("summary") or {}
    tick_readiness_after = (payload.get("tickReadinessAfter") or payload.get("tickReadinessAfterRefresh") or payload.get("tickReadinessBefore") or {}).get("tickReadiness") or {}
    observation_targets = payload.get("observationTargets") or {}
    observation_progress = observation_targets.get("progress") or {}
    stop_rules = payload.get("stopRulesAfter") or payload.get("stopRulesBefore") or {}
    preflight = payload.get("preflight") or {}
    if not tick_readiness_after:
        tick_readiness_after = (preflight.get("tickReadiness") or {}).get("tickReadiness") or {}
    if not observation_targets:
        observation_targets = preflight.get("observationTargets") or {}
        observation_progress = observation_targets.get("progress") or {}
    if not stop_rules:
        stop_rules = preflight.get("stopRules") or {}
    next_action = payload.get("nextAction") or {}
    tick_skip_reason = None
    if not payload.get("tickRan"):
        tick_skip_reason = payload.get("skipReason") or next_action.get("reason") or refresh.get("reason") or payload.get("error")
    return {
        "type": "iteration",
        "iteration": iteration,
        "timestamp": utc_now(),
        "guided": bool(payload.get("guided")),
        "paperEnabled": payload.get("paperEnabled"),
        "realTradingEnabled": payload.get("realTradingEnabled"),
        "ok": payload.get("ok"),
        "httpStatus": payload.get("httpStatus"),
        "runStatus": "OK" if payload.get("ok") else "ERROR",
        "refreshStatus": "SKIPPED" if refresh.get("attempted") is False else "OK" if refresh.get("ok") else "FAILED" if refresh.get("ok") is False else None,
        "tickReadinessStatus": tick_readiness_after.get("status") or summary.get("readinessAfter") or summary.get("readinessBefore"),
        "tickRan": bool(payload.get("tickRan")),
        "tickSkipReason": tick_skip_reason,
        "processedCandlesDelta": summary.get("processedCandlesDelta", tick_summary.get("processedCandlesDelta")),
        "newSignals": tick_summary.get("newSignals"),
        "observationTargetStatus": observation_targets.get("status") or summary.get("observationTargetStatus"),
        "ticksObserved": observation_progress.get("ticksObserved"),
        "closedTrades": observation_progress.get("closedTrades"),
        "stopRulesStatus": stop_rules.get("status") or summary.get("stopRulesAfter") or summary.get("stopRulesBefore"),
        "preflightBlockers": preflight.get("blockers") or [],
        "nextAction": next_action,
        "error": payload.get("error"),
    }


def write_jsonl(handle, item: dict) -> None:
    line = json.dumps(item, sort_keys=True)
    print(line)
    if handle:
        handle.write(line + "\n")
        handle.flush()


def final_summary(results: list[dict], interrupted: bool = False) -> dict:
    errors = [item for item in results if not item.get("ok")]
    final = results[-1] if results else {}
    exit_code = 130 if interrupted else 0 if not errors else 1
    return {
        "type": "summary",
        "timestamp": utc_now(),
        "ok": not errors and not interrupted,
        "interrupted": interrupted,
        "exitCode": exit_code,
        "iterationsAttempted": len(results),
        "ticksRun": len([item for item in results if item.get("tickRan")]),
        "ticksSkipped": len([item for item in results if not item.get("tickRan")]),
        "errors": len(errors),
        "guided": any(item.get("guided") for item in results),
        "finalPaperEnabled": final.get("paperEnabled"),
        "finalRealTradingEnabled": final.get("realTradingEnabled"),
        "finalObservationTargetStatus": final.get("observationTargetStatus"),
        "finalNextAction": final.get("nextAction"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one safe local paper refresh/tick cycle.")
    parser.add_argument("--loop", action="store_true", help="Repeat run-once until max iterations is reached.")
    parser.add_argument("--interval-minutes", type=float, default=5.0, help="Loop interval in minutes.")
    parser.add_argument("--max-iterations", type=int, default=12, help="Maximum loop iterations.")
    parser.add_argument("--full", action="store_true", help="Print full endpoint payload instead of compact summary.")
    parser.add_argument("--log-file", help="Optional JSONL file for iteration and final summary records.")
    parser.add_argument("--guided", action="store_true", help="Run explicit preflight checks and skip before POST when paper/real/stop-rule blockers exist.")
    args = parser.parse_args()

    results = []
    log_handle = None
    interrupted = False
    output = final_summary(results)
    if args.log_file:
        log_path = Path(args.log_file)
        if not log_path.is_absolute():
            log_path = ROOT / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
    with app.test_client() as client:
        try:
            iterations = max(1, args.max_iterations) if args.loop else 1
            for index in range(iterations):
                payload = guided_run_once(client) if args.guided else run_once(client)
                item = payload if args.full else compact_iteration(payload, index + 1)
                if args.full:
                    item = {**compact_iteration(payload, index + 1), "payload": item}
                results.append(item)
                write_jsonl(log_handle, item)
                if not args.loop or index >= iterations - 1:
                    break
                time.sleep(max(0, args.interval_minutes) * 60)
        except KeyboardInterrupt:
            interrupted = True
        finally:
            output = final_summary(results, interrupted=interrupted)
            write_jsonl(log_handle, output)
            if log_handle:
                log_handle.close()
    return output["exitCode"]


if __name__ == "__main__":
    raise SystemExit(main())
