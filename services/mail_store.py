"""Synchronous PostgreSQL mail accounting, using the shared storage adapter.

The delivery id is the provider idempotency key. A committed claim precedes
network I/O; quota counts logical sends, including failed/ambiguous attempts.
Transport attempts are recorded separately. Locks always follow delivery,
month, day order. No transaction stays open during provider I/O.

This is not an outbox: no bodies, credentials or tokens are stored, and nothing
is queued. Accepted means provider acceptance, not inbox delivery. A crash or
failure after claiming is deliberately not retried under the same id, since
SMTP has no idempotency and provider deduplication windows can expire. Such
records require reconciliation before an operator uses a new id. Provider
acceptance and account/token transactions cannot be committed atomically.
There is no lease or automatic same-id recovery, even within Resend's retry
window: persisted metadata cannot verify an identical body, and a failed
transport can still mean acceptance. Pending alert events with a claimed id
will therefore remain blocked after a crash until explicitly reconciled.
"""
from datetime import datetime, timezone
import time


class MailStoreError(ValueError):
    """Only fixed, safe messages may cross the mail API boundary."""


def postgres_enabled():
    from services.storage import postgres_enabled as enabled
    return enabled()


def database():
    from services.storage import database as connect
    return connect()


def claim(delivery_id, recipient, subject, config):
    """Return a prior acceptance, or atomically claim and reserve both caps."""
    now = time.time()
    utc = datetime.fromtimestamp(now, timezone.utc)
    month, day = utc.strftime("%Y-%m"), utc.strftime("%Y-%m-%d")
    with database() as db:
        inserted = db.execute("""INSERT INTO mail_deliveries
            (id, recipient, subject, status, provider_id, error, attempts, created, updated)
            VALUES (?, ?, ?, 'sending', NULL, NULL, 0, ?, ?)
            ON CONFLICT (id) DO NOTHING RETURNING id""", (delivery_id, recipient, subject, now, now)).fetchone()
        row = db.execute("SELECT * FROM mail_deliveries WHERE id=? FOR UPDATE", (delivery_id,)).fetchone()
        if row["recipient"] != recipient or row["subject"] != subject:
            raise MailStoreError("Email delivery identity was already used for another recipient or subject")
        if row["status"] == "accepted":
            result = {"provider": "resend" if row["provider_id"] else "smtp", "status": "accepted"}
            if row["provider_id"]:
                result["id"] = row["provider_id"]
            return result
        if not inserted:
            raise MailStoreError("Email delivery is already claimed; reconcile its outcome before resending")
        # Even row creation takes locks. Both insert and SELECT must use this
        # order across all workers, including the first send of a new day.
        for period, limit in ((month, int(config.get("monthly_limit", 1000))),
                              (day, int(config.get("daily_limit", 50)))):
            db.execute("INSERT INTO mail_usage (period, attempts) VALUES (?, 0) ON CONFLICT (period) DO NOTHING",
                       (period,))
            usage = db.execute("SELECT attempts FROM mail_usage WHERE period=? FOR UPDATE", (period,)).fetchone()
            if usage["attempts"] >= limit:
                raise MailStoreError("Local email safety limit reached; try later or review mail settings")
            db.execute("UPDATE mail_usage SET attempts=attempts+1 WHERE period=?", (period,))
    return None


def record_attempt(delivery_id):
    with database() as db:
        db.execute("UPDATE mail_deliveries SET attempts=attempts+1, updated=? WHERE id=? AND status='sending'",
                   (time.time(), delivery_id))


def finish(delivery_id, result=None, error=None):
    with database() as db:
        db.execute("""UPDATE mail_deliveries SET status=?, provider_id=?, error=?, updated=?
            WHERE id=? AND status='sending'""",
                   ("accepted" if result else "failed", result.get("id") if result else None,
                    error, time.time(), delivery_id))
