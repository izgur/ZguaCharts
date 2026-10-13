"""User-owned database alerts or legacy local JSON. Notifications only.

PostgreSQL commits the trigger, pending email event and immutable emailAlert
snapshot before delivery. Each cycle recovers pending events under the original
chart-alert/<event-id> ledger identity. Accepted claims restore receipts without
provider I/O; sending/failed claims require review and are never resent. Legacy
pending events without snapshots require review rather than rendering edited
alerts. Storage failures leave pending events available for a later cycle.
"""
from __future__ import annotations

import json
import hmac
import logging
import math
import re
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, g, jsonify, request

from data_source import DATA_SOURCE_CONFIG, custom_chart_base, fetch_candles, interval_to_ms
from indicators import available_indicators, build_indicator_payload
from services import account_mail, mail_templates
from services.storage import database, postgres_enabled

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "chart-alerts.json"
LOCK = threading.RLock()
OPERATORS = {"crossing", "crossing-up", "crossing-down", "above", "below"}
_started = False
blueprint = Blueprint("chart_alerts", __name__)
logger = logging.getLogger(__name__)


def log_monitor_failure(message, exc):
    # Driver messages may contain DSNs, credentials or provider responses.
    # Keep the stack for diagnosis but replace the exception value and type.
    logger.exception(message, exc_info=(RuntimeError, RuntimeError("Alert monitor operation failed"), exc.__traceback__))


@blueprint.before_request
def authorize():
    if not postgres_enabled():
        return
    from services.chart_accounts import COOKIE, account_origin, digest
    with database() as db:
        user = db.execute(
            "SELECT users.id, users.email, users.email_verified, sessions.csrf "
            "FROM users JOIN sessions ON sessions.user_id=users.id "
            "WHERE sessions.token=? AND sessions.expires>? AND users.disabled=0",
            (digest(request.cookies.get(COOKIE, "")), time.time())).fetchone()
    if not user:
        return jsonify(error="Sign in to manage alerts."), 401
    g.alert_user = dict(user)
    if request.method != "GET":
        if request.headers.get("Origin") not in (None, account_origin()) or request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="Cross-origin request blocked."), 403
        if not hmac.compare_digest(user["csrf"], request.headers.get("X-CSRF-Token", "")):
            return jsonify(error="Invalid CSRF token."), 403


def user_store(user_id):
    with database() as db:
        records = db.execute("SELECT id, body, revision FROM alert_records WHERE user_id=?", (user_id,)).fetchall()
        events = db.execute("SELECT body FROM alert_events WHERE user_id=? ORDER BY created DESC LIMIT 200", (user_id,)).fetchall()
    return {"alerts": [record_alert(row) for row in records],
            "events": [json.loads(row["body"]) for row in reversed(events)]}


def record_alert(row):
    alert = json.loads(row["body"])
    alert.update(id=row["id"], revision=row["revision"])
    return alert


def commit_alert(alert, user_id, revision, event=None):
    # The database context commits both the claim and event, or rolls both back.
    with database() as db:
        changed = db.execute(
            "UPDATE alert_records SET body=?, revision=?, updated=? WHERE id=? AND user_id=? AND revision=? "
            "AND EXISTS (SELECT 1 FROM users WHERE users.id=alert_records.user_id AND disabled=0)",
            (json.dumps(alert), alert["revision"], time.time(), alert["id"], user_id, revision)).rowcount
        if changed != 1:
            return False
        if event:
            db.execute("INSERT INTO alert_events(id,user_id,alert_id,body,created) VALUES (?,?,?,?,?)",
                       (event["id"], user_id, alert["id"], json.dumps(event), event["time"]))
    return True


def load_store():
    if not STORE.exists():
        return {"alerts": [], "events": []}
    return json.loads(STORE.read_text(encoding="utf-8"))


def save_store(store):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STORE.with_suffix(".tmp")
    temporary.write_text(json.dumps(store, indent=2), encoding="utf-8")
    temporary.replace(STORE)


def mail_config():
    return account_mail.configuration()


def email_ready():
    config = mail_config()
    if postgres_enabled():
        user = g.alert_user
        return bool(account_mail.configured(config) and user["email"] and user["email_verified"])
    return bool(account_mail.configured(config) and config.get("to"))


def send_email(message, alert=None, event=None, recipient=None):
    if postgres_enabled() and recipient is None:
        raise account_mail.MailDeliveryError("An authenticated alert owner's email is required")
    config = mail_config()
    subject, text, html = mail_templates.chart_alert(alert, event) if alert and event else ("ZguaCharts alert", message, None)
    return account_mail.send(recipient if recipient is not None else config.get("to", ""), subject, text, html=html,
                             idempotency_key="chart-alert/" + event["id"] if event else None, config=config)


def condition_matches(operator, previous, current):
    if operator == "above":
        return current > 0
    if operator == "below":
        return current < 0
    if previous is None:
        return False
    up, down = previous <= 0 < current, previous >= 0 > current
    return up if operator == "crossing-up" else down if operator == "crossing-down" else up or down


def alert_series(candles):
    result = {"price": {"label": "Price", "value": candles[-1]["close"]}}
    closed = [row for row in candles if row["time"] < candles[-1]["time"]]
    payload = build_indicator_payload(closed, [row["id"] for row in available_indicators()])
    series = [("overlay", row) for row in payload["overlays"]]
    series += [(pane["id"], row) for pane in payload["panes"] for row in pane["series"] if not row.get("guide")]
    for group, row in series:
        label = row.get("title") or row.get("name") or group
        values = [p["value"] for p in row["data"] if p.get("value") is not None and math.isfinite(p["value"])]
        result[group + ":" + label] = {"label": label, "value": values[-1] if values else None}
    return result


def market_values(source, symbol, timeframe):
    payload = fetch_candles(source, symbol, timeframe, limit=600, force_network=True)
    candles = payload["candles"]
    if not candles or candles[-1]["time"] + interval_to_ms(timeframe) / 1000 + 120 < time.time():
        raise ValueError("Alert market data is stale; evaluation paused")
    return alert_series(candles)


def evaluate_alert(alert, values, now):
    if not alert.get("enabled") or alert["expiresAt"] <= now:
        alert["enabled"] = False
        return False
    matches, next_values = [], []
    for index, condition in enumerate(alert["conditions"]):
        left = values.get(condition["field"], {}).get("value")
        right = values.get(condition.get("reference"), {}).get("value") if condition.get("reference") else condition["value"]
        if left is None or right is None:
            alert["error"] = "Condition series unavailable or still warming up"
            return False
        delta = left - right
        previous = alert.get("previous", [])
        matches.append(condition_matches(condition["operator"], previous[index] if index < len(previous) else None, delta))
        next_values.append(delta)
    triggered = (any(matches) if alert["match"] == "any" else all(matches))
    # An alert starts observing from creation; it never backfills historical triggers.
    crossing = any(condition["operator"].startswith("crossing") for condition in alert["conditions"])
    fired = bool(alert.get("previous")) and triggered and (crossing or not alert.get("matched", False))
    alert.update(previous=next_values, matched=triggered, checkedAt=now, error=None)
    if fired:
        alert["lastTriggeredAt"] = now
        if alert["once"]:
            alert["enabled"] = False
    return fired


def email_ledger(event_id):
    with database() as db:
        return db.execute("SELECT status, provider_id FROM mail_deliveries WHERE id=?",
                          ("chart-alert/" + event_id,)).fetchone()


def deliver_pending_email(row):
    original = row["body"]
    event = json.loads(original)
    if event.get("emailStatus") != "pending":
        return
    event["id"] = row["id"]
    snapshot = event.get("emailAlert")
    with database() as db:
        owner = db.execute("SELECT email, email_verified FROM users WHERE id=? AND disabled=0",
                           (row["user_id"],)).fetchone()
    if not isinstance(snapshot, dict):
        event["emailStatus"] = "needs-review"
    elif not owner or not owner["email_verified"] or not owner["email"]:
        event["emailStatus"] = "skipped"
    else:
        prior = email_ledger(event["id"])
        if prior and prior["status"] != "accepted":
            event["emailStatus"] = "needs-review"
        else:
            try:
                # The mail ledger arbitrates competing workers, including a claim
                # made after our lookup. Accepted entries do not contact providers.
                delivery = send_email(event["message"], snapshot, event, recipient=owner["email"])
                event.update(emailStatus="accepted", emailProvider=delivery["provider"])
                if delivery.get("id"):
                    event["emailId"] = delivery["id"]
            except Exception:
                prior = email_ledger(event["id"])
                if prior and prior["status"] == "accepted":
                    event.update(emailStatus="accepted", emailProvider="resend" if prior["provider_id"] else "smtp")
                    if prior["provider_id"]:
                        event["emailId"] = prior["provider_id"]
                else:
                    event["emailStatus"] = "needs-review" if prior else "failed"
    with database() as db:
        # A losing worker must not replace an acceptance with its ambiguous
        # outcome. An authoritative acceptance may repair a competing review.
        sql = "UPDATE alert_events SET body=? WHERE id=? AND user_id=?"
        params = (json.dumps(event), row["id"], row["user_id"])
        if event["emailStatus"] != "accepted":
            sql += " AND body=?"
            params += (original,)
        db.execute(sql, params)


def recover_pending_email():
    try:
        with database() as db:
            rows = db.execute("SELECT id, user_id, body FROM alert_events WHERE body LIKE ? OR body LIKE ? ORDER BY created",
                              ('%"emailStatus": "pending"%', '%"emailStatus":"pending"%')).fetchall()
    except Exception as exc:
        log_monitor_failure("Could not load pending chart alert emails", exc)
        return
    for row in rows:
        try:
            deliver_pending_email(row)
        except Exception as exc:
            log_monitor_failure("Could not recover pending chart alert email", exc)


def postgres_monitor_cycle():
    try:
        with database() as db:
            rows = db.execute("SELECT alert_records.id, alert_records.user_id, body, revision FROM alert_records "
                              "JOIN users ON users.id=alert_records.user_id WHERE users.disabled=0").fetchall()
    except Exception as exc:
        log_monitor_failure("Could not load PostgreSQL chart alerts", exc)
        raise RuntimeError("Could not load PostgreSQL chart alerts") from None
    recover_pending_email()
    snapshots = {}
    for row in rows:
        try:
            alert = record_alert(row)
            if not alert.get("enabled"):
                continue
            now = time.time()
            event = None
            try:
                key = (alert["source"], alert["symbol"], alert["timeframe"])
                if alert["expiresAt"] > now and key not in snapshots:
                    snapshots[key] = market_values(*key)
                if evaluate_alert(alert, snapshots.get(key, {}), now):
                    event = {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"chart-alert/{alert['id']}/{row['revision']}")),
                             "alertId": alert["id"], "time": now, "message": alert["message"],
                             "symbol": alert["symbol"], "notifications": alert["notifications"]}
                    if alert["notifications"].get("email"):
                        event["emailStatus"] = "pending"
                        event["emailAlert"] = json.loads(json.dumps(alert))
            except Exception as exc:
                alert["error"] = str(exc)[:200]
            alert["revision"] = row["revision"] + 1
            if not commit_alert(alert, row["user_id"], row["revision"], event):
                continue
            if event and alert["notifications"].get("email"):
                deliver_pending_email({"id": event["id"], "user_id": row["user_id"], "body": json.dumps(event)})
        except Exception as exc:
            # One bad record or failed transaction must not block other owners.
            log_monitor_failure("Could not process PostgreSQL chart alert record", exc)
            continue


def monitor_cycle():
    if postgres_enabled():
        return postgres_monitor_cycle()
    with LOCK:
        alerts = [dict(row) for row in load_store()["alerts"] if row.get("enabled")]
    snapshots = {}
    for alert in alerts:
        now = time.time()
        try:
            key = (alert["source"], alert["symbol"], alert["timeframe"])
            if alert["expiresAt"] > now and key not in snapshots:
                snapshots[key] = market_values(*key)
            values = snapshots.get(key, {})
            fired = evaluate_alert(alert, values, now)
            event = None
            if fired:
                event = {"id": str(uuid.uuid4()), "alertId": alert["id"], "time": now,
                         "message": alert["message"], "symbol": alert["symbol"], "notifications": alert["notifications"]}
                if alert["notifications"].get("email"):
                    try:
                        delivery = send_email(alert["message"], alert, event)
                        event["emailStatus"] = "accepted"
                        event["emailProvider"] = delivery["provider"]
                        if delivery.get("id"):
                            event["emailId"] = delivery["id"]
                    except account_mail.MailDeliveryError as exc:
                        event["emailStatus"] = "failed"
                        alert["error"] = str(exc)
                    except Exception:
                        event["emailStatus"] = "failed"
                        alert["error"] = "Email delivery failed; check local mail settings"
        except Exception as exc:
            alert["error"] = str(exc)[:200]
            event = None
        with LOCK:
            store = load_store()
            for index, existing in enumerate(store["alerts"]):
                if existing["id"] == alert["id"]:
                    if existing.get("revision", 0) != alert.get("revision", 0):
                        break
                    store["alerts"][index] = alert
                    if event:
                        store["events"] = (store["events"] + [event])[-200:]
                    save_store(store)
                    break


def start_monitor():
    global _started
    if postgres_enabled():
        return
    with LOCK:
        if _started:
            return
        _started = True
    def watch():
        while True:
            try:
                monitor_cycle()
            except Exception as exc:
                # Store failures are visible on GET; keep the monitor alive for recovery.
                log_monitor_failure("Chart alert monitor cycle failed", exc)
            time.sleep(30)
    threading.Thread(target=watch, name="chart-alerts", daemon=True).start()


@blueprint.get("/api/chart-alerts/options")
def options():
    try:
        key = [request.args.get(field, default) for field, default in (("source", "bybit"), ("symbol", "BTCUSDT"), ("timeframe", "1h"))]
        values = market_values(*key)
        return jsonify({"series": [{"id": name, **value} for name, value in values.items()], "emailReady": email_ready()})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.route("/api/chart-alerts", methods=["GET", "POST"])
@blueprint.route("/api/chart-alerts/<alert_id>", methods=["PUT"])
def collection(alert_id=None):
    start_monitor()
    try:
        with LOCK:
            pg = postgres_enabled()
            store = user_store(g.alert_user["id"]) if pg else load_store()
            if request.method == "GET":
                return jsonify({**store, "emailReady": email_ready(), "monitorIntervalSeconds": 30})
            body = request.get_json() or {}
            if not isinstance(body, dict): raise ValueError("Invalid alert")
            existing = next((row for row in store["alerts"] if row["id"] == alert_id), None) if alert_id else None
            if alert_id and existing is None:
                return jsonify(error="Alert not found"), 404
            source, symbol, timeframe = (str(body.get(field, "")) for field in ("source", "symbol", "timeframe"))
            if source not in DATA_SOURCE_CONFIG["sources"] or not re.fullmatch(r"[A-Za-z0-9_:@./^=-]{1,96}", symbol):
                raise ValueError("Invalid market")
            if timeframe not in DATA_SOURCE_CONFIG["sources"][source]["timeframes"] and not custom_chart_base(source, timeframe):
                raise ValueError("Timeframe unavailable for this source")
            conditions = body.get("conditions") or []
            if not 1 <= len(conditions) <= 8:
                raise ValueError("Provide 1 to 8 conditions")
            values = market_values(source, symbol, timeframe)
            for condition in conditions:
                if condition.get("field") not in values or condition.get("operator") not in OPERATORS:
                    raise ValueError("Invalid condition series or operator")
                if condition.get("reference"):
                    if condition["reference"] not in values:
                        raise ValueError("Invalid reference series")
                else:
                    condition["value"] = float(condition["value"])
                    if not math.isfinite(condition["value"]):
                        raise ValueError("Value must be finite")
            expires = float(body.get("expiresAt", time.time() + 30 * 86400))
            if not math.isfinite(expires) or expires <= time.time():
                raise ValueError("Expiration must be in the future")
            notifications = {field: bool((body.get("notifications") or {}).get(field)) for field in ("toast", "sound", "email")}
            if notifications["email"] and not email_ready():
                raise ValueError("Verify your email and configure email delivery before enabling email" if pg else "Configure config/local/alert-email.json before enabling email")
            if not existing and len(store["alerts"]) >= 100:
                raise ValueError("Maximum 100 saved alerts")
            alert = {"id": alert_id or str(uuid.uuid4()), "source": source, "symbol": symbol, "timeframe": timeframe,
                     "conditions": conditions, "once": bool(body.get("once", True)), "expiresAt": expires,
                     "message": str(body.get("message") or symbol + " alert")[:1000], "match": "any" if body.get("match") == "any" else "all",
                     "notifications": notifications, "enabled": body.get("enabled", True) is True, "createdAt": existing["createdAt"] if existing else time.time(),
                     "revision": existing.get("revision", 0) + 1 if existing else 0}
            evaluate_alert(alert, values, time.time())
            if pg:
                if existing:
                    if not commit_alert(alert, g.alert_user["id"], existing["revision"]):
                        return jsonify(error="Alert changed during update. Reload and try again."), 409
                else:
                    with database() as db:
                        db.execute("INSERT INTO alert_records(id,user_id,body,revision,updated) VALUES (?,?,?,?,?)",
                                   (alert["id"], g.alert_user["id"], json.dumps(alert), alert["revision"], time.time()))
                return jsonify(alert), 200 if existing else 201
            if existing:
                store["alerts"][store["alerts"].index(existing)] = alert
            else:
                store["alerts"].append(alert)
            save_store(store)
            return jsonify(alert), 200 if existing else 201
    except (ValueError, KeyError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@blueprint.delete("/api/chart-alerts/<alert_id>")
def remove(alert_id):
    if postgres_enabled():
        with database() as db:
            changed = db.execute("DELETE FROM alert_records WHERE id=? AND user_id=?", (alert_id, g.alert_user["id"])).rowcount
        if not changed:
            return jsonify(error="Alert not found"), 404
        return jsonify(ok=True)
    with LOCK:
        store = load_store()
        store["alerts"] = [row for row in store["alerts"] if row["id"] != alert_id]
        save_store(store)
    return jsonify({"ok": True})
