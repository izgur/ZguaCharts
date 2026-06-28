from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def parse_timestamp(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def generated_file_info(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "sizeBytes": 0, "sizeKb": 0, "updatedAt": None}
    stat = path.stat()
    return {
        "exists": True,
        "sizeBytes": stat.st_size,
        "sizeKb": round(stat.st_size / 1024, 2),
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def latest_record_age(records: list[dict], time_key: str = "createdAt") -> dict:
    if not records:
        return {"latestAt": None, "ageHours": None}
    latest = records[-1].get(time_key)
    parsed = parse_timestamp(latest)
    age = None
    if parsed:
        age = round((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600, 2)
    return {"latestAt": latest, "ageHours": age}


def generated_file_age(path: Path) -> dict:
    info = generated_file_info(path)
    updated_at = parse_timestamp(info.get("updatedAt"))
    age_hours = None
    if updated_at:
        age_hours = round((datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)).total_seconds() / 3600, 2)
    return {**info, "ageHours": age_hours}


def freshness_verdict(age_hours, warn_after_hours: float, stale_after_hours: float) -> str:
    if age_hours is None:
        return "MISSING"
    if age_hours >= stale_after_hours:
        return "STALE"
    if age_hours >= warn_after_hours:
        return "AGING"
    return "CURRENT"


def generated_file_freshness(path: Path, warn_after_hours: float, stale_after_hours: float) -> dict:
    age = generated_file_age(path)
    verdict = freshness_verdict(age.get("ageHours"), warn_after_hours, stale_after_hours)
    return {
        **age,
        "status": verdict,
        "warnAfterHours": warn_after_hours,
        "staleAfterHours": stale_after_hours,
    }


def record_age_freshness(records: list[dict], warn_after_hours: float, stale_after_hours: float, time_key: str = "createdAt") -> dict:
    age = latest_record_age(records, time_key=time_key)
    verdict = freshness_verdict(age.get("ageHours"), warn_after_hours, stale_after_hours)
    return {
        **age,
        "status": verdict,
        "warnAfterHours": warn_after_hours,
        "staleAfterHours": stale_after_hours,
    }


def freshness_check_status(freshness: dict) -> str:
    status = freshness.get("status")
    if status in {"AGING", "STALE", "MISSING"}:
        return "WARN"
    return "PASS"


def freshness_check_message(label: str, freshness: dict) -> str:
    status = freshness.get("status")
    age = freshness.get("ageHours")
    if status == "CURRENT":
        return f"{label} is current ({age}h old)."
    if status == "AGING":
        return f"{label} is aging ({age}h old); refresh soon."
    if status == "STALE":
        return f"{label} is stale ({age}h old); refresh before trusting decisions."
    return f"{label} is missing or has no timestamp."
