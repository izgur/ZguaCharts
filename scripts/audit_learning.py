"""Print the backend-owned learning audit summary without starting Flask."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import build_learning_audit_summary  # noqa: E402


def compact(payload: dict) -> dict:
    return {
        "ok": payload.get("ok"),
        "generatedAt": payload.get("generatedAt"),
        "nextAction": payload.get("nextAction"),
        "optimizerQuality": payload.get("optimizerQuality"),
        "zeroTrade": payload.get("zeroTrade"),
        "bestSavedCandidate": payload.get("bestSavedCandidate"),
        "readiness": payload.get("readiness"),
        "warnings": payload.get("warnings", []),
    }


if __name__ == "__main__":
    print(json.dumps(compact(build_learning_audit_summary()), indent=2))
