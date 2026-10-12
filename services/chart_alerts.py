"""Local persisted alerts. Notifications only; never submits trading orders."""
from __future__ import annotations

import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from data_source import DATA_SOURCE_CONFIG, custom_chart_base, fetch_candles, interval_to_ms
from indicators import available_indicators, build_indicator_payload
from services import account_mail, mail_templates

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "data" / "chart-alerts.json"
LOCK = threading.RLock()
OPERATORS = {"crossing", "crossing-up", "crossing-down", "above", "below"}
_started = False
blueprint = Blueprint("chart_alerts", __name__)


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
    return bool(account_mail.configured(config) and config.get("to"))


def send_email(message, alert=None, event=None):
    config = mail_config()
    subject, text, html = mail_templates.chart_alert(alert, event) if alert and event else ("ZguaCharts alert", message, None)
    return account_mail.send(config.get("to", ""), subject, text, html=html,
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


def monitor_cycle():
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
    with LOCK:
        if _started:
            return
        _started = True
    def watch():
        while True:
            try:
                monitor_cycle()
            except Exception:
                # Store failures are visible on GET; keep the monitor alive for recovery.
                pass
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
            store = load_store()
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
                raise ValueError("Configure config/local/alert-email.json before enabling email")
            if not existing and len(store["alerts"]) >= 100:
                raise ValueError("Maximum 100 saved alerts")
            alert = {"id": alert_id or str(uuid.uuid4()), "source": source, "symbol": symbol, "timeframe": timeframe,
                     "conditions": conditions, "once": bool(body.get("once", True)), "expiresAt": expires,
                     "message": str(body.get("message") or symbol + " alert")[:1000], "match": "any" if body.get("match") == "any" else "all",
                     "notifications": notifications, "enabled": body.get("enabled", True) is True, "createdAt": existing["createdAt"] if existing else time.time(),
                     "revision": existing.get("revision", 0) + 1 if existing else 0}
            evaluate_alert(alert, values, time.time())
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
    with LOCK:
        store = load_store()
        store["alerts"] = [row for row in store["alerts"] if row["id"] != alert_id]
        save_store(store)
    return jsonify({"ok": True})
