"""Transactional unit tests plus opt-in real PostgreSQL process contention.

Run the PostgreSQL cases with MAIL_TEST_DATABASE_URL pointing at a test DB.
They create and remove only their own randomly named schema. SQLite exercises
rollback and API behavior; it is not evidence for PostgreSQL row locking.
"""
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing, contextmanager
import json
import multiprocessing
import os
from pathlib import Path
import smtplib
import sqlite3
import tempfile
import unittest
import uuid
from unittest.mock import MagicMock, patch

import requests
from services import account_mail, mail_store


SCHEMA = (
    "CREATE TABLE mail_usage(period TEXT PRIMARY KEY, attempts INTEGER NOT NULL)",
    "CREATE TABLE mail_deliveries(id TEXT PRIMARY KEY, recipient TEXT, subject TEXT, "
    "status TEXT, provider_id TEXT, error TEXT, attempts INTEGER, created REAL, updated REAL)",
)


class SQLiteAdapter:
    def __init__(self, connection, statements):
        self.connection, self.statements = connection, statements

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        normalized = " ".join(statement.split())
        if normalized.endswith("ON CONFLICT (id) DO NOTHING RETURNING id"):
            # SQLite 3.21 lacks UPSERT/RETURNING. Preserve ownership semantics:
            # only a newly inserted claim produces a returned row.
            insert = statement.split("ON CONFLICT", 1)[0].replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
            cursor = self.connection.execute(insert, parameters)
            if cursor.rowcount:
                return self.connection.execute("SELECT id FROM mail_deliveries WHERE id=?", (parameters[0],))
            return self.connection.execute("SELECT id FROM mail_deliveries WHERE 0")
        if normalized.endswith("ON CONFLICT (period) DO NOTHING"):
            insert = statement.split("ON CONFLICT", 1)[0].replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
            return self.connection.execute(insert, parameters)
        return self.connection.execute(statement.replace(" FOR UPDATE", ""), parameters)


class MailPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mail.sqlite"
        self.statements = []
        with closing(sqlite3.connect(str(self.path))) as db:
            for statement in SCHEMA:
                db.execute(statement)
            db.commit()
        self.config = {"provider": "resend", "api_key": "SECRET_API_KEY",
                       "from": "sender@example.test"}
        self.usage = Path(self.temp.name) / "usage.json"
        self.patches = [patch.object(mail_store, "database", self.database),
                        patch.object(mail_store, "postgres_enabled", return_value=True),
                        patch.object(account_mail, "USAGE", self.usage),
                        patch.object(account_mail.time, "sleep"),
                        patch.object(account_mail.requests, "post")]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.post = account_mail.requests.post
        self.post.return_value.status_code = 200
        self.post.return_value.json.return_value = {"id": "provider-123"}

    @contextmanager
    def database(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        try:
            with db:
                yield SQLiteAdapter(db, self.statements)
        finally:
            db.close()

    def send(self, key="event/123", **options):
        return account_mail.send("user@example.test", "Confirm account", "SECRET_TOKEN_BODY",
                                 html="<p>SECRET_TOKEN_BODY</p>", idempotency_key=key,
                                 config=self.config, **options)

    def rows(self, table):
        with self.database() as db:
            return [dict(row) for row in db.execute("SELECT * FROM " + table)]

    def test_success_is_persisted_and_same_id_never_resends(self):
        result = self.send()
        self.assertEqual(self.send(), result)
        self.assertEqual(self.post.call_count, 1)
        row = self.rows("mail_deliveries")[0]
        self.assertEqual(row["id"], "event/123")
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["provider_id"], "provider-123")
        self.assertEqual(row["attempts"], 1)
        self.assertIsNone(row["error"])
        self.assertNotIn("SECRET", json.dumps(row))
        self.assertFalse(self.usage.exists())
        self.assertEqual([r["attempts"] for r in self.rows("mail_usage")], [1, 1])
        locks = [parameters[0] for sql, parameters in self.statements
                 if sql.startswith("SELECT attempts")]
        self.assertEqual([len(period) for period in locks], [7, 10])

    def test_generated_keys_are_unique_and_match_provider_headers(self):
        self.send(None)
        self.send(None)
        keys = {row["id"] for row in self.rows("mail_deliveries")}
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys, {call[1]["headers"]["Idempotency-Key"]
                                for call in self.post.call_args_list})

    def test_daily_rollover_does_not_reset_monthly_limit(self):
        self.config["monthly_limit"] = 1
        with patch.object(mail_store.time, "time", return_value=1767225600):
            self.send("first-day")
        with patch.object(mail_store.time, "time", return_value=1767312000):
            with self.assertRaisesRegex(account_mail.MailDeliveryError, "safety limit"):
                self.send("second-day")
        self.assertEqual({row["period"] for row in self.rows("mail_usage")},
                         {"2026-01", "2026-01-01"})

    def test_identity_cannot_be_reused_for_another_recipient(self):
        self.send()
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "identity"):
            account_mail.send("other@example.test", "Confirm account", "body",
                              idempotency_key="event/123", config=self.config)
        self.assertEqual(self.post.call_count, 1)

    def test_daily_limit_rolls_back_month_and_claim(self):
        self.config["daily_limit"] = 1
        self.send()
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "safety limit"):
            self.send("event/456")
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(len(self.rows("mail_deliveries")), 1)
        self.assertEqual([r["attempts"] for r in self.rows("mail_usage")], [1, 1])

    def test_monthly_limit_and_utc_rollover(self):
        with patch.object(mail_store.time, "time", return_value=1767225599):
            self.send("december")
        self.config["monthly_limit"] = 1
        with patch.object(mail_store.time, "time", return_value=1767225600):
            self.send("january")
            with self.assertRaisesRegex(account_mail.MailDeliveryError, "safety limit"):
                self.send("january-again")
        self.assertEqual({r["period"] for r in self.rows("mail_usage")},
                         {"2025-12", "2025-12-31", "2026-01", "2026-01-01"})

    def test_crashed_claim_blocks_resend_even_with_zero_attempts(self):
        mail_store.claim("event/123", "user@example.test", "Confirm account", self.config)
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "already claimed"):
            self.send()
        self.post.assert_not_called()

    def test_retries_keep_key_and_record_each_network_attempt(self):
        success = MagicMock(status_code=200)
        success.json.return_value = {"id": "provider-123"}
        self.post.side_effect = [requests.Timeout("SECRET_NETWORK"), MagicMock(status_code=503), success]
        self.send()
        self.assertEqual(self.rows("mail_deliveries")[0]["attempts"], 3)
        self.assertEqual([r["attempts"] for r in self.rows("mail_usage")], [1, 1])
        self.assertEqual({call[1]["headers"]["Idempotency-Key"] for call in self.post.call_args_list},
                         {"event/123"})

    def test_provider_failure_is_safe_and_blocks_same_id(self):
        self.post.return_value.status_code = 403
        self.post.return_value.text = "SECRET_PROVIDER_BODY"
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "Resend"):
            self.send()
        row = self.rows("mail_deliveries")[0]
        self.assertEqual(row["status"], "failed")
        self.assertNotIn("SECRET", json.dumps(row))
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "already claimed"):
            self.send()
        self.assertEqual(self.post.call_count, 1)

    def test_claim_commits_before_network_and_receipt_failure_blocks_resend(self):
        def accepted(*args, **kwargs):
            self.assertEqual(self.rows("mail_deliveries")[0]["status"], "sending")
            return MagicMock(status_code=200, json=lambda: {"id": "provider-123"})
        self.post.side_effect = accepted
        with patch.object(mail_store, "finish", side_effect=RuntimeError("SECRET_DB")):
            with self.assertRaisesRegex(account_mail.MailDeliveryError, "receipt could not be saved"):
                self.send()
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "already claimed"):
            self.send()
        self.assertEqual(self.post.call_count, 1)

    def test_database_failure_is_safe_and_never_sends(self):
        with patch.object(mail_store, "database", side_effect=RuntimeError("SECRET_DSN")):
            with self.assertRaisesRegex(account_mail.MailDeliveryError, "persistence is unavailable") as caught:
                self.send()
        self.assertNotIn("SECRET", str(caught.exception))
        self.post.assert_not_called()

    def test_smtp_is_synchronous_persisted_and_deduplicated(self):
        self.config = {"host": "smtp.example.test", "from": "sender@example.test"}
        with patch.object(account_mail.smtplib, "SMTP") as smtp:
            self.assertEqual(self.send(), {"provider": "smtp", "status": "accepted"})
            self.send()
            self.assertEqual(smtp.call_count, 1)
            smtp.return_value.__enter__.return_value.send_message.assert_called_once()
        self.assertEqual(self.rows("mail_deliveries")[0]["attempts"], 1)

    def test_smtp_exception_never_persists_provider_reply(self):
        self.config = {"host": "smtp.example.test", "from": "sender@example.test"}
        with patch.object(account_mail.smtplib, "SMTP", side_effect=smtplib.SMTPException("SECRET_SMTP_REPLY")):
            with self.assertRaises(account_mail.MailDeliveryError) as caught:
                self.send()
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertNotIn("SECRET", json.dumps(self.rows("mail_deliveries")))

    def test_failure_record_write_failure_preserves_claim_and_safe_error(self):
        self.post.return_value.status_code = 401
        with patch.object(mail_store, "finish", side_effect=RuntimeError("SECRET_DB")):
            with self.assertRaisesRegex(account_mail.MailDeliveryError, "API key rejected"):
                self.send()
        self.assertEqual(self.rows("mail_deliveries")[0]["status"], "sending")

    def test_local_mode_does_not_touch_store(self):
        with patch.object(mail_store, "postgres_enabled", return_value=False), \
                patch.object(mail_store, "claim") as claim, \
                patch.object(account_mail, "USAGE", Path(self.temp.name) / "usage.json"):
            self.send()
            self.send()
        claim.assert_not_called()
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(self.rows("mail_deliveries"), [])


class StoragePackageTests(unittest.TestCase):
    def test_real_package_enabled_wrapper_uses_environment_without_flask(self):
        from flask import has_app_context
        self.assertFalse(has_app_context())
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test.invalid/mail"}):
            self.assertTrue(mail_store.postgres_enabled())
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            self.assertFalse(mail_store.postgres_enabled())

    def test_real_package_database_context_commits_and_returns_connection(self):
        from services import storage
        pool = MagicMock()
        connection = pool.getconn.return_value
        connection.closed = 0
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test.invalid/mail"}), \
                patch.object(storage, "_pool", return_value=pool):
            with mail_store.database() as db:
                self.assertIsInstance(db, storage.PostgresConnection)
                self.assertIs(db.connection, connection)
            connection.commit.assert_called_once()
            pool.putconn.assert_called_once_with(connection, close=False)

    def test_real_package_database_rolls_back_on_quota_error(self):
        from services import storage
        pool = MagicMock()
        connection = pool.getconn.return_value
        connection.closed = 0
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://test.invalid/mail"}), \
                patch.object(storage, "_pool", return_value=pool):
            with self.assertRaises(mail_store.MailStoreError):
                with mail_store.database():
                    raise mail_store.MailStoreError("quota")
        connection.commit.assert_not_called()
        self.assertTrue(connection.rollback.called)
        pool.putconn.assert_called_once_with(connection, close=False)


@contextmanager
def postgres_database(url, schema):
    from psycopg2 import sql
    from services import storage
    with storage.postgres_database(url) as db:
        statement = sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
        db.execute(statement.as_string(db.connection))
        db.execute("SET LOCAL lock_timeout='10s'")
        yield db


def postgres_claim_worker(arguments):
    url, schema, key, limits = arguments
    with patch.object(mail_store, "database", lambda: postgres_database(url, schema)):
        try:
            mail_store.claim(key, "user@example.test", "subject", limits)
            return "reserved"
        except mail_store.MailStoreError:
            return "blocked"


@unittest.skipUnless(os.getenv("MAIL_TEST_DATABASE_URL"), "MAIL_TEST_DATABASE_URL not set")
class PostgresProcessTests(unittest.TestCase):
    def setUp(self):
        import psycopg2
        from psycopg2 import sql
        self.url = os.environ["MAIL_TEST_DATABASE_URL"]
        self.schema = "mail_test_" + uuid.uuid4().hex
        connection = psycopg2.connect(self.url)
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
                    cursor.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
                    for statement in SCHEMA:
                        cursor.execute(statement)
        finally:
            connection.close()

    def tearDown(self):
        import psycopg2
        from psycopg2 import sql
        connection = psycopg2.connect(self.url)
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        finally:
            connection.close()

    def contend(self, keys, limits):
        arguments = [(self.url, self.schema, key, limits) for key in keys]
        with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as executor:
            results = list(executor.map(postgres_claim_worker, arguments))
        with postgres_database(self.url, self.schema) as db:
            usage = [row[0] for row in db.execute("SELECT attempts FROM mail_usage")]
            count = db.execute("SELECT COUNT(*) FROM mail_deliveries").fetchone()[0]
        return results, usage, count

    def test_cross_process_daily_quota_and_rollback(self):
        results, usage, count = self.contend([str(i) for i in range(16)], {"daily_limit": 3})
        self.assertEqual(results.count("reserved"), 3)
        self.assertEqual(usage, [3, 3])
        self.assertEqual(count, 3)

    def test_cross_process_monthly_quota(self):
        results, usage, count = self.contend([str(i) for i in range(16)], {"monthly_limit": 2})
        self.assertEqual(results.count("reserved"), 2)
        self.assertEqual(usage, [2, 2])
        self.assertEqual(count, 2)

    def test_cross_process_same_id_has_single_owner(self):
        results, usage, count = self.contend(["same-id"] * 16, {})
        self.assertEqual(results.count("reserved"), 1)
        self.assertEqual(usage, [1, 1])
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
