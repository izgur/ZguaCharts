from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import run_due_learning_cycle  # noqa: E402


def main() -> int:
    force = "--force" in sys.argv
    result = run_due_learning_cycle(force=force)
    summary = {
        "ran": result.get("ran", False),
        "reason": result.get("reason"),
        "lastRunAt": result.get("lastRunAt"),
        "nextRunAt": result.get("nextRunAt"),
        "reportId": (result.get("report") or {}).get("id"),
        "recommendation": ((result.get("report") or {}).get("recommendation") or {}).get("action"),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
