"""Personal backtest history; administrator research files stay separate."""
import json
import hashlib
from pathlib import Path
import time

from flask import has_request_context, request
from services.chart_accounts import COOKIE, digest
from services.storage import database


def owner_id(db):
    if not has_request_context():
        return None
    row = db.execute("SELECT users.id FROM users JOIN sessions ON users.id=sessions.user_id WHERE sessions.token=? AND sessions.expires>? AND users.disabled=0",
                     (digest(request.cookies.get(COOKIE, "")), time.time())).fetchone()
    return row["id"] if row else None


def history():
    with database() as db:
        owner = owner_id(db)
        if owner is None:
            return []
        return [json.loads(row["body"]) for row in db.execute(
            "SELECT (body::jsonb - 'tradeList')::text AS body FROM backtest_runs WHERE user_id=? ORDER BY created DESC LIMIT 500", (owner,)).fetchall()][::-1]


def record(summary, payload):
    body = dict(summary)
    body["tradeList"] = payload.get("trade_list") or payload.get("tradeList") or []
    root = Path(__file__).resolve().parents[1]
    fingerprint = hashlib.sha256()
    for name in ('core/backtest/regimeTrend.js','core/backtest/engine.js','core/indicators.js'):
        path = root / name
        if path.exists():
            fingerprint.update(name.encode('ascii'))
            fingerprint.update(path.read_bytes())
    body["tacticSourceHash"] = payload.get("tacticSourceHash") or fingerprint.hexdigest()
    encoded = json.dumps(body, allow_nan=False)
    if len(encoded.encode("utf-8")) > 2000000:
        body.pop("tradeList", None)
        body["tradeListOmitted"] = "Result exceeds saved history size limit"
        encoded = json.dumps(body, allow_nan=False)
    with database() as db:
        owner = owner_id(db)
        if owner is None:
            return
        db.execute("INSERT INTO backtest_runs(id,user_id,body,created) VALUES (?,?,?,?)", (summary["id"],owner,encoded,time.time()))
        db.execute("DELETE FROM backtest_runs WHERE user_id=? AND id NOT IN (SELECT id FROM backtest_runs WHERE user_id=? ORDER BY created DESC LIMIT 500)", (owner,owner))
