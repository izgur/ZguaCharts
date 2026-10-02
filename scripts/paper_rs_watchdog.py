from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "scripts/research_autopilot.py"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_cli(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


def parse_json(stdout: str) -> dict:
    text = stdout.strip()
    if not text:
        return {}
    return json.loads(text)


def write_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def command_args(command: str) -> list[str]:
    parts = shlex.split(command.replace("\\", "/"), posix=False)
    normalized = []
    for part in parts:
        item = part.strip('"')
        if item == "scripts/research_autopilot.py":
            item = "scripts/research_autopilot.py"
        normalized.append(item)
    if len(normalized) < 3 or normalized[0] != "python" or normalized[1] != "scripts/research_autopilot.py":
        raise ValueError(f"Refusing unexpected command: {command}")
    return [sys.executable, normalized[1], *normalized[2:]]


def safety_error(payload: dict) -> str | None:
    safety = payload.get("safety") if isinstance(payload.get("safety"), dict) else {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    if payload.get("realTradingEnabled") or safety.get("realTradingEnabled") or status.get("realTradingEnabled"):
        return "realTradingEnabled=true"
    if payload.get("liveOrdersTouched") or safety.get("liveOrdersTouched") or status.get("liveOrdersTouched"):
        return "liveOrdersTouched=true"
    if payload.get("schedulerEnabled") or safety.get("schedulerEnabled"):
        return "schedulerEnabled=true"
    if payload.get("autoTickEnabled") or safety.get("autoTickEnabled"):
        return "autoTickEnabled=true"
    return None


def pick_safe_command(payload: dict) -> str | None:
    due = payload.get("dueSummary") if isinstance(payload.get("dueSummary"), dict) else {}
    tick_due = payload.get("tickDue") if isinstance(payload.get("tickDue"), dict) else {}
    catch_up = payload.get("catchUpPlan") if isinstance(payload.get("catchUpPlan"), dict) else {}
    return (
        due.get("nextSafeCommand")
        or due.get("catchUpNextSafeCommand")
        or tick_due.get("nextSafeCommand")
        or catch_up.get("nextSafeCommand")
    )


def compact_next_action(payload: dict) -> dict:
    status = payload.get("statusSummary") or {}
    due = payload.get("dueSummary") or {}
    preview = payload.get("previewSummary") or {}
    return {
        "type": "watchdog-check",
        "timestamp": utc_now(),
        "ok": payload.get("ok"),
        "nextHumanAction": payload.get("nextHumanAction"),
        "paperEnabled": status.get("paperEnabled"),
        "strategy": status.get("strategy") or payload.get("strategy"),
        "symbol": status.get("symbol") or payload.get("symbol"),
        "timeframe": status.get("timeframe") or payload.get("timeframe"),
        "lastProcessedCandleAt": status.get("lastProcessedCandleAt") or due.get("lastProcessedCandleAt"),
        "latestClosedCandleAt": due.get("latestMissedCandleAt") or due.get("nextDueCandleAt"),
        "nextExpectedCandleAt": due.get("nextExpectedCandleAt"),
        "tickDue": bool(due.get("tickDue")),
        "catchUpRequired": bool(due.get("catchUpRequired")),
        "previewSignal": preview.get("signal"),
        "previewAction": preview.get("proposedAction"),
        "safeCommandAvailable": bool(pick_safe_command(payload)),
        "safetyError": safety_error(payload),
    }


def compact_tick_result(payload: dict, rc: int) -> dict:
    return {
        "type": "watchdog-tick",
        "timestamp": utc_now(),
        "returnCode": rc,
        "ok": payload.get("ok"),
        "command": payload.get("command"),
        "paperTickRan": payload.get("paperTickRan"),
        "paperStateChanged": payload.get("paperStateChanged"),
        "openedTrade": payload.get("openedTrade"),
        "closedTrade": payload.get("closedTrade"),
        "processedCandleAt": payload.get("processedCandleAt") or payload.get("targetCandleAt"),
        "previewSignal": payload.get("previewSignal") or payload.get("signal"),
        "previewAction": payload.get("previewProposedAction") or payload.get("proposedAction"),
        "equityBefore": payload.get("equityBefore"),
        "equityAfter": payload.get("equityAfter"),
        "safetyError": safety_error(payload),
        "error": payload.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded paper-only watchdog for the active RS candidate.")
    parser.add_argument("--interval-minutes", type=float, default=5.0)
    parser.add_argument("--max-iterations", type=int, default=288)
    parser.add_argument("--log-file", default="reports/paper-rs-watchdog.jsonl")
    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.is_absolute():
        log_path = ROOT / log_path

    for index in range(1, max(1, args.max_iterations) + 1):
        try:
            rc, out, err = run_cli(CLI + ["paper:next-action", "--refresh", "--json"])
            payload = parse_json(out) if out.strip() else {"ok": False, "error": err.strip() or "empty output"}
            check = compact_next_action(payload)
            check["iteration"] = index
            check["returnCode"] = rc
            if err.strip():
                check["stderr"] = err.strip()[:1000]
            write_jsonl(log_path, check)

            stop = safety_error(payload)
            if rc != 0 or not payload.get("ok") or stop:
                write_jsonl(log_path, {"type": "watchdog-stop", "timestamp": utc_now(), "reason": stop or payload.get("error") or f"returnCode={rc}"})
                return 1

            command = pick_safe_command(payload)
            if command:
                tick_rc, tick_out, tick_err = run_cli(command_args(command), timeout=240)
                tick_payload = parse_json(tick_out) if tick_out.strip() else {"ok": False, "error": tick_err.strip() or "empty tick output"}
                tick = compact_tick_result(tick_payload, tick_rc)
                if tick_err.strip():
                    tick["stderr"] = tick_err.strip()[:1000]
                write_jsonl(log_path, tick)
                tick_stop = safety_error(tick_payload)
                if tick_rc != 0 or not tick_payload.get("ok") or tick_stop:
                    write_jsonl(log_path, {"type": "watchdog-stop", "timestamp": utc_now(), "reason": tick_stop or tick_payload.get("error") or f"tickReturnCode={tick_rc}"})
                    return 1

            if index < args.max_iterations:
                time.sleep(max(0.1, args.interval_minutes) * 60)
        except KeyboardInterrupt:
            write_jsonl(log_path, {"type": "watchdog-stop", "timestamp": utc_now(), "reason": "keyboard interrupt"})
            return 130
        except Exception as exc:
            write_jsonl(log_path, {"type": "watchdog-stop", "timestamp": utc_now(), "reason": str(exc)})
            return 1

    write_jsonl(log_path, {"type": "watchdog-finished", "timestamp": utc_now(), "iterations": args.max_iterations})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
