"""Shared outbound mail via Resend or encrypted SMTP; secrets stay local."""
import json
import os
from pathlib import Path
import smtplib
import ssl
import threading
import time
import uuid
from datetime import datetime
from email.utils import parseaddr
from email.message import EmailMessage
import requests
from services import mail_store

ROOT = Path(__file__).resolve().parents[1]
USAGE = ROOT / "config" / "local" / "mail-usage.json"
SEND_LOCK = threading.Lock()
_last_send = 0.0


class MailDeliveryError(ValueError):
    """Safe to display; never includes credentials or provider response bodies."""


def configuration():
    path = ROOT / "config" / "local" / "alert-email.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for field in ("host", "port", "username", "password", "from", "to", "security"):
        value = os.getenv("ZGUA_SMTP_" + field.upper())
        if value:
            config[field] = value
    for field, variable in (("provider", "ZGUA_MAIL_PROVIDER"), ("api_key", "RESEND_API_KEY"),
                            ("from", "ZGUA_MAIL_FROM"), ("to", "ZGUA_MAIL_TO"),
                            ("test_recipient", "ZGUA_MAIL_TEST_RECIPIENT")):
        if os.getenv(variable):
            config[field] = os.environ[variable]
    if config.get("provider") == "resend":
        config.setdefault("from", "ZguaCharts <onboarding@resend.dev>")
    return config


def configured(config):
    if config.get("provider", "smtp") == "resend":
        sender = parseaddr(config.get("from", ""))[1].lower()
        return bool(config.get("api_key") and sender and (not sender.endswith("@resend.dev") or config.get("test_recipient")))
    if config.get("provider", "smtp") != "smtp":
        return False
    return bool(config.get("host") and config.get("from") and config.get("security", "starttls") in ("starttls", "ssl"))


def ready():
    return configured(configuration())


def _reserve_send(config):
    """Persist conservative local caps, counting attempts even on ambiguous failures."""
    now = datetime.utcnow()
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    usage = json.loads(USAGE.read_text(encoding="utf-8")) if USAGE.exists() else {}
    daily = usage.get("daily", 0) if usage.get("day") == day else 0
    monthly = usage.get("monthly", 0) if usage.get("month") == month else 0
    if daily >= int(config.get("daily_limit", 50)) or monthly >= int(config.get("monthly_limit", 1000)):
        raise MailDeliveryError("Local email safety limit reached; try later or review mail settings")
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    temporary = USAGE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"day": day, "month": month, "daily": daily + 1, "monthly": monthly + 1}), encoding="utf-8")
    temporary.replace(USAGE)


def _send_resend(config, recipient, subject, message, html, idempotency_key, persisted=False):
    global _last_send
    sender = parseaddr(config["from"])[1].lower()
    if sender.endswith("@resend.dev"):
        if sender != "onboarding@resend.dev":
            raise MailDeliveryError("Use onboarding@resend.dev until a sender domain is verified")
        if recipient.lower() != config.get("test_recipient", "").lower():
            raise MailDeliveryError("Resend test mode can only email the configured account owner; verify a domain to send to other users")
    payload = {"from": config["from"], "to": [recipient], "subject": subject, "text": message}
    if html:
        payload["html"] = html
    headers = {"Authorization": "Bearer " + config["api_key"], "Idempotency-Key": idempotency_key,
               "Content-Type": "application/json"}
    with SEND_LOCK:
        if not persisted:
            _reserve_send(config)
        for attempt in range(3):
            time.sleep(max(0, 1.0 - (time.monotonic() - _last_send)))
            _last_send = time.monotonic()
            if persisted:
                mail_store.record_attempt(idempotency_key)
            try:
                response = requests.post("https://api.resend.com/emails", headers=headers, json=payload,
                                         timeout=(5, 15), allow_redirects=False)
            except requests.RequestException:
                if attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                raise MailDeliveryError("Resend connection failed; check network or provider status") from None
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    time.sleep(attempt + 1)
                    continue
            if response.status_code not in (200, 201):
                reason = {401: "API key rejected", 403: "sender or recipient not permitted; check test recipient and verified domain",
                          429: "rate or sending quota exceeded"}.get(response.status_code, "request rejected")
                raise MailDeliveryError("Resend: " + reason)
            try:
                email_id = response.json()["id"]
                if not isinstance(email_id, str) or not email_id:
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise MailDeliveryError("Resend returned an invalid acceptance response") from None
            return {"provider": "resend", "id": email_id, "status": "accepted"}


def _send(recipient, subject, message, html=None, idempotency_key=None, config=None, persisted=False):
    config = configuration() if config is None else config
    if not configured(config):
        raise MailDeliveryError("Email delivery is not configured")
    if any(char in recipient + subject for char in "\r\n") or parseaddr(recipient)[1] != recipient or "@" not in recipient:
        raise MailDeliveryError("Invalid email recipient or subject")
    if config.get("provider") == "resend":
        return _send_resend(config, recipient, subject, message, html, idempotency_key or "zgua/" + uuid.uuid4().hex, persisted)
    mail = EmailMessage()
    mail["From"], mail["To"], mail["Subject"] = config["from"], recipient, subject
    mail.set_content(message)
    if html:
        mail.add_alternative(html, subtype="html")
    security = config.get("security", "starttls")
    context = ssl.create_default_context()
    port = int(config.get("port", 465 if security == "ssl" else 587))
    if persisted:
        mail_store.record_attempt(idempotency_key)
    if security == "ssl":
        connection = smtplib.SMTP_SSL(config["host"], port, timeout=15, context=context)
    else:
        connection = smtplib.SMTP(config["host"], port, timeout=15)
    with connection as smtp:
        if security == "starttls":
            smtp.starttls(context=context)
        if config.get("username"):
            smtp.login(config["username"], config.get("password", ""))
        smtp.send_message(mail)
    return {"provider": "smtp", "status": "accepted"}


def send(recipient, subject, message, html=None, idempotency_key=None, config=None):
    """Deliver synchronously; PostgreSQL claims are durable before provider I/O."""
    config = configuration() if config is None else config
    if not configured(config):
        raise MailDeliveryError("Email delivery is not configured")
    if any(char in recipient + subject for char in "\r\n") or parseaddr(recipient)[1] != recipient or "@" not in recipient:
        raise MailDeliveryError("Invalid email recipient or subject")
    try:
        persisted = mail_store.postgres_enabled()
    except Exception:
        raise MailDeliveryError("Email persistence is unavailable") from None
    if not persisted:
        return _send(recipient, subject, message, html, idempotency_key, config)
    delivery_id = idempotency_key or "zgua/" + uuid.uuid4().hex
    try:
        prior = mail_store.claim(delivery_id, recipient, subject, config)
    except mail_store.MailStoreError as exc:
        raise MailDeliveryError(str(exc)) from None
    except Exception:
        raise MailDeliveryError("Email persistence is unavailable") from None
    if prior:
        return prior
    try:
        result = _send(recipient, subject, message, html, delivery_id, config, persisted=True)
    except Exception as exc:
        # Never persist arbitrary exception text, SMTP replies or message data.
        safe_error = str(exc) if isinstance(exc, MailDeliveryError) else "Email delivery failed; reconcile its outcome before resending"
        try:
            mail_store.finish(delivery_id, error=safe_error)
        except Exception:
            pass  # The committed claim still prevents an ambiguous resend.
        raise MailDeliveryError(safe_error) from None
    try:
        mail_store.finish(delivery_id, result=result)
    except Exception:
        raise MailDeliveryError("Email was accepted but its receipt could not be saved; reconcile before resending") from None
    return result
