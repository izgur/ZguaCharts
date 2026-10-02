from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "scripts/research_autopilot.py"]


def run_cli(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def parse_json_output(stdout: str) -> dict:
    text = stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def extract_catchup_command(next_action_text: str) -> str | None:
    # Extract the exact command printed after "Catch-up required:"
    match = re.search(
        r'python\s+scripts[\\/]+research_autopilot\.py\s+paper:catch-up-next\s+--confirm\s+"([^"]+)"',
        next_action_text,
    )
    if not match:
        return None
    confirmation = match.group(1)
    return confirmation


def safety_stop_reason(payload: dict, return_code: int) -> str | None:
    if return_code != 0:
        return f"CLI returned non-zero exit code: {return_code}"

    if not payload.get("ok"):
        return f"payload ok=false: {payload.get('error') or 'unknown error'}"

    if payload.get("realTradingEnabled"):
        return "STOP: realTradingEnabled=true"

    if payload.get("liveOrdersTouched"):
        return "STOP: liveOrdersTouched=true"

    if payload.get("schedulerEnabled"):
        return "STOP: schedulerEnabled=true"

    if payload.get("autoTickEnabled"):
        return "STOP: autoTickEnabled=true"

    if payload.get("warnings"):
        return f"STOP: warnings returned: {payload.get('warnings')}"

    if payload.get("blockers"):
        return f"STOP: blockers returned: {payload.get('blockers')}"

    if payload.get("openedTrade"):
        return "STOP: openedTrade=true"

    if payload.get("closedTrade"):
        return "STOP: closedTrade=true"

    signal = payload.get("signal") or payload.get("previewSignal")
    if signal and str(signal).upper() != "HOLD":
        return f"STOP: signal is not HOLD: {signal}"

    proposed = payload.get("proposedAction") or payload.get("previewProposedAction")
    safe_hold_actions = {"no action", "would hold position"}
    if proposed and str(proposed).lower() not in safe_hold_actions:
        return f"STOP: proposedAction is not a safe HOLD action: {proposed}"

    if payload.get("paperTickRan") is not True:
        return "STOP: paperTickRan is not true"

    if payload.get("paperStateChanged") is not True:
        return "STOP: paperStateChanged is not true"

    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely run paper catch-up one candle at a time until current or until safety stop."
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum number of catch-up ticks to run in this script execution.",
    )
    parser.add_argument(
        "--allow-non-hold",
        action="store_true",
        help="Do not stop on signal != HOLD or proposedAction != no action. Not recommended.",
    )
    args = parser.parse_args()

    if args.max_steps < 1:
        print("max-steps must be >= 1")
        return 2

    print(f"Safe catch-up started. max_steps={args.max_steps}")
    print("This script stops on trade open/close, non-HOLD signal, warnings, blockers, or safety flags.")
    print("Safe HOLD actions are allowed: no action, would hold position.")
    print()

    processed = 0

    for step in range(1, args.max_steps + 1):
        print(f"=== Step {step}/{args.max_steps}: checking next action ===")

        rc, out, err = run_cli(CLI + ["paper:next-action", "--refresh"])
        if out:
            print(out.strip())
        if err:
            print(err.strip(), file=sys.stderr)
        if rc != 0:
            print(f"STOP: paper:next-action returned non-zero exit code {rc}")
            return rc

        if "WAIT_FOR_NEXT_CLOSED_CANDLE" in out:
            print("DONE: caught up to the latest trusted closed candle.")
            return 0

        if "SAFE_TO_RUN_SINGLE_CONFIRMED_TICK" in out:
            print("STOP: normal single tick is due, not catch-up. Run the printed tick command manually.")
            return 0

        if "CATCH_UP_REQUIRED" not in out:
            print("STOP: next action is not CATCH_UP_REQUIRED. Review output above.")
            return 0

        confirmation = extract_catchup_command(out)
        if not confirmation:
            print("STOP: could not extract exact catch-up confirmation from paper:next-action output.")
            return 1

        print()
        print("Running catch-up tick:")
        print(f'python scripts\\research_autopilot.py paper:catch-up-next --confirm "{confirmation}"')
        print()

        rc, out, err = run_cli(CLI + ["paper:catch-up-next", "--confirm", confirmation])
        if out:
            print(out.strip())
        if err:
            print(err.strip(), file=sys.stderr)

        payload = parse_json_output(out)
        if not payload:
            print("STOP: catch-up output was not valid JSON.")
            return 1

        if args.allow_non_hold:
            # Temporarily ignore these two checks only.
            signal = payload.get("signal") or payload.get("previewSignal")
            proposed = payload.get("proposedAction") or payload.get("previewProposedAction")
            payload_for_checks = dict(payload)
            if signal and str(signal).upper() != "HOLD":
                payload_for_checks["signal"] = "HOLD"
                payload_for_checks["previewSignal"] = "HOLD"
            if proposed and str(proposed).lower() != "no action":
                payload_for_checks["proposedAction"] = "no action"
                payload_for_checks["previewProposedAction"] = "no action"
        else:
            payload_for_checks = payload

        stop_reason = safety_stop_reason(payload_for_checks, rc)
        processed += 1

        target = payload.get("targetCandleAt") or payload.get("processedCandleAt")
        remaining = payload.get("missedClosedCandleCountAfter")
        signal = payload.get("signal") or payload.get("previewSignal")
        proposed = payload.get("proposedAction") or payload.get("previewProposedAction")

        print()
        print(
            f"Processed step {processed}: target={target}, "
            f"remaining={remaining}, signal={signal}, action={proposed}"
        )
        print()

        if stop_reason:
            print(stop_reason)
            print("Stopped safely. Review the last output before continuing.")
            return 0

    print(f"STOP: reached max_steps={args.max_steps}. Run paper:next-action --refresh before continuing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
