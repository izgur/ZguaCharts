"""Exercise the PG storage contract with real transactions and competing snapshots."""
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from services import chart_alerts as alerts
from services.chart_accounts import COOKIE, digest


class PostgresChartAlertTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "database.sqlite"
        with self.database() as db:
            db.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT, email_verified INTEGER, disabled INTEGER);
                CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id INTEGER, csrf TEXT, expires REAL);
                CREATE TABLE alert_records(id TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), body TEXT NOT NULL, revision INTEGER, updated REAL);
                CREATE TABLE alert_events(id TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), alert_id TEXT, body TEXT, created REAL);
                CREATE TABLE mail_deliveries(id TEXT PRIMARY KEY, status TEXT, provider_id TEXT);
            """)
            for user_id in (1, 2):
                db.execute("INSERT INTO users VALUES(?,?,1,0)", (user_id, f"owner{user_id}@example.test"))
                db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(f"session{user_id}"), user_id, f"csrf{user_id}", time.time() + 3600))
        patches = [patch.object(alerts, "postgres_enabled", return_value=True),
                   patch.object(alerts, "database", self.database),
                   patch.object(alerts, "market_values", return_value={"price": {"value": 90}}),
                   patch.object(alerts.account_mail, "configured", return_value=True),
                   patch.object(alerts, "mail_config", return_value={"to": "global@example.test"})]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)
        self.app = Flask(__name__)
        self.app.register_blueprint(alerts.blueprint)
        self.clients = {}
        for user_id in (1, 2):
            client = self.app.test_client()
            try:
                client.set_cookie(COOKIE, f"session{user_id}", domain="localhost")
            except TypeError:
                client.set_cookie("localhost", COOKIE, f"session{user_id}")
            self.clients[user_id] = client
        self.body = {"source": "bybit", "symbol": "BTCUSDT", "timeframe": "1h",
                     "conditions": [{"field": "price", "operator": "crossing-up", "value": 100}],
                     "notifications": {"email": True}, "once": False}

    @contextmanager
    def database(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, user_id=1):
        response = self.clients[user_id].post("/api/chart-alerts", json=self.body,
                                               headers={"X-CSRF-Token": f"csrf{user_id}"})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def records(self):
        with self.database() as db:
            return db.execute("SELECT * FROM alert_records").fetchall()

    def events(self):
        with self.database() as db:
            return db.execute("SELECT * FROM alert_events").fetchall()

    def test_authentication_and_mutation_guards(self):
        guest = self.app.test_client()
        for path in ("/api/chart-alerts", "/api/chart-alerts/options"):
            self.assertEqual(guest.get(path).status_code, 401)
        self.assertEqual(self.clients[1].post("/api/chart-alerts", json=self.body).status_code, 403)
        for headers in ({"Origin": "https://attacker.test"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.clients[1].post("/api/chart-alerts", json=self.body,
                headers={"X-CSRF-Token": "csrf1", **headers}).status_code, 403)
        for sql in ("UPDATE sessions SET expires=0 WHERE user_id=1", "UPDATE users SET disabled=1 WHERE id=2"):
            with self.database() as db:
                db.execute(sql)
        for client in self.clients.values():
            self.assertEqual(client.get("/api/chart-alerts").status_code, 401)

    def test_trusted_public_origin(self):
        with patch("services.chart_accounts.account_origin", return_value="https://charts.example.test"):
            response = self.clients[1].post("/api/chart-alerts", json=self.body,
                headers={"Origin": "https://charts.example.test", "X-CSRF-Token": "csrf1"})
        self.assertEqual(response.status_code, 201)

    def test_owner_isolation_and_legacy_store_never_loaded(self):
        with patch.object(alerts, "load_store", side_effect=AssertionError("Legacy import forbidden")):
            first, second = self.create(), self.create(2)
            for user_id, own in ((1, first), (2, second)):
                saved = self.clients[user_id].get("/api/chart-alerts").get_json()
                self.assertEqual([row["id"] for row in saved["alerts"]], [own["id"]])
            path = "/api/chart-alerts/" + first["id"]
            self.assertEqual(self.clients[2].put(path, json=self.body, headers={"X-CSRF-Token": "csrf2"}).status_code, 404)
            self.assertEqual(self.clients[2].delete(path, headers={"X-CSRF-Token": "csrf2"}).status_code, 404)
            self.assertEqual(self.clients[1].delete(path, headers={"X-CSRF-Token": "csrf1"}).status_code, 200)

    def test_edit_conflicts_with_monitor_revision(self):
        alert = self.create()
        def competing_market(*args):
            with self.database() as db:
                db.execute("UPDATE alert_records SET revision=revision+1 WHERE id=?", (alert["id"],))
            return {"price": {"value": 90}}
        with patch.object(alerts, "market_values", side_effect=competing_market):
            response = self.clients[1].put("/api/chart-alerts/" + alert["id"], json=self.body,
                                         headers={"X-CSRF-Token": "csrf1"})
        self.assertEqual(response.status_code, 409)

    def test_owner_edit_restarts_observation(self):
        alert = self.create()
        response = self.clients[1].put("/api/chart-alerts/" + alert["id"],
            json={**self.body, "message": "Updated", "conditions": [
                {"field": "price", "operator": "crossing-up", "value": 200}]},
            headers={"X-CSRF-Token": "csrf1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["revision"], 1)
        self.assertEqual(response.get_json()["previous"], [-110])
        self.assertEqual(response.get_json()["createdAt"], alert["createdAt"])

    def test_pg_email_never_falls_back_to_global_recipient(self):
        with patch.object(alerts.account_mail, "send") as send:
            with self.assertRaises(alerts.account_mail.MailDeliveryError):
                alerts.send_email("Notification")
        send.assert_not_called()

    def test_mail_failure_does_not_release_trigger_claim(self):
        self.create()
        with patch.object(alerts, "market_values", return_value={"price": {"value": 110}}), \
             patch.object(alerts, "send_email", side_effect=RuntimeError("Provider unavailable")) as send:
            alerts.monitor_cycle()
            alerts.monitor_cycle()
        send.assert_called_once()
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(json.loads(self.events()[0]["body"])["emailStatus"], "failed")

    def test_competing_worker_snapshot_claims_once_and_emails_owner(self):
        self.create()
        original = alerts.commit_alert
        competed = []
        def competitor(alert, user_id, revision, event=None):
            if not competed:
                competed.append(True)
                alerts.monitor_cycle()
            return original(alert, user_id, revision, event)
        with patch.object(alerts, "market_values", return_value={"price": {"value": 110}}), \
             patch.object(alerts, "commit_alert", side_effect=competitor), \
             patch.object(alerts.account_mail, "send", return_value={"provider": "test"}) as send:
            alerts.monitor_cycle()
            alerts.monitor_cycle()
        self.assertEqual(len(self.events()), 1)
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], "owner1@example.test")
        self.assertEqual(json.loads(self.events()[0]["body"])["emailStatus"], "accepted")
        self.assertEqual(self.clients[2].get("/api/chart-alerts").get_json()["events"], [])

    def test_event_failure_rolls_back_revision_claim(self):
        alert = self.create()
        event = {"id": "duplicate", "time": time.time()}
        with self.database() as db:
            db.execute("INSERT INTO alert_events VALUES('duplicate',1,?,?,?)", (alert["id"], "{}", event["time"]))
        alert["revision"] = 1
        with self.assertRaises(sqlite3.IntegrityError):
            alerts.commit_alert(alert, 1, 0, event)
        self.assertEqual(self.records()[0]["revision"], 0)

    def test_edited_or_deleted_snapshot_never_sends(self):
        for delete in (False, True):
            alert = self.create()
            def change_during_fetch(*args):
                with self.database() as db:
                    db.execute("DELETE FROM alert_records WHERE id=?" if delete else
                               "UPDATE alert_records SET revision=revision+1 WHERE id=?", (alert["id"],))
                return {"price": {"value": 110}}
            with patch.object(alerts, "market_values", side_effect=change_during_fetch), \
                 patch.object(alerts, "send_email") as send:
                alerts.monitor_cycle()
                send.assert_not_called()
            with self.database() as db:
                db.execute("DELETE FROM alert_records WHERE id=?", (alert["id"],))
        self.assertEqual(self.events(), [])

    def test_unverified_owner_cannot_enable_email_and_worker_skips_delivery(self):
        self.create()
        with self.database() as db:
            db.execute("UPDATE users SET email_verified=0 WHERE id=1")
        self.assertFalse(self.clients[1].get("/api/chart-alerts").get_json()["emailReady"])
        self.assertEqual(self.clients[1].post("/api/chart-alerts", json=self.body,
            headers={"X-CSRF-Token": "csrf1"}).status_code, 400)
        with patch.object(alerts, "market_values", return_value={"price": {"value": 110}}), \
             patch.object(alerts, "send_email") as send:
            alerts.monitor_cycle()
        send.assert_not_called()
        self.assertEqual(json.loads(self.events()[0]["body"])["emailStatus"], "skipped")

    def test_suspended_owner_not_evaluated(self):
        self.create()
        with self.database() as db:
            db.execute("UPDATE users SET disabled=1 WHERE id=1")
        with patch.object(alerts, "market_values") as market:
            alerts.monitor_cycle()
        market.assert_not_called()
        self.assertEqual(self.records()[0]["revision"], 0)

    def test_monitor_continues_after_one_record_failure(self):
        self.create()
        self.create(2)
        original = alerts.commit_alert
        def fail_first(alert, user_id, revision, event=None):
            if user_id == 1:
                raise RuntimeError("Transaction unavailable")
            return original(alert, user_id, revision, event)
        with patch.object(alerts, "commit_alert", side_effect=fail_first), \
             self.assertLogs(alerts.logger, level="ERROR") as logs:
            alerts.monitor_cycle()
        self.assertEqual([row["revision"] for row in self.records()], [0, 1])
        self.assertIn("Could not process PostgreSQL chart alert record", logs.output[0])

    def test_storage_logging_redacts_exception_and_keeps_stack(self):
        self.create()
        secret = "postgresql://private-user:private-password@private-host/database"
        with patch.object(alerts, "commit_alert", side_effect=RuntimeError(secret)), \
             self.assertLogs(alerts.logger, level="ERROR") as logs:
            alerts.monitor_cycle()
        rendered = "\n".join(logs.output)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("private-password", rendered)
        self.assertIn("Traceback", rendered)
        self.assertIn("Alert monitor operation failed", rendered)

    def test_initial_storage_failure_logged_and_reported_safely(self):
        secret = "postgresql://private-user:private-password@private-host/database"
        with patch.object(alerts, "database", side_effect=RuntimeError(secret)), \
             self.assertLogs(alerts.logger, level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "Could not load PostgreSQL chart alerts") as raised:
                alerts.monitor_cycle()
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, "\n".join(logs.output))

    def test_crash_receipt_gap_stays_pending_for_recovery(self):
        self.create()
        secret = "private-database-credentials"
        @contextmanager
        def fail_receipt():
            with self.database() as db:
                class Connection:
                    def execute(self, sql, params=()):
                        if sql.startswith("UPDATE alert_events"):
                            raise RuntimeError(secret)
                        return db.execute(sql, params)
                yield Connection()
        with patch.object(alerts, "database", fail_receipt), \
             patch.object(alerts, "market_values", return_value={"price": {"value": 110}}), \
             patch.object(alerts, "send_email", return_value={"provider": "test"}) as send, \
             self.assertLogs(alerts.logger, level="ERROR") as logs:
            alerts.monitor_cycle()
        send.assert_called_once()
        self.assertEqual(json.loads(self.events()[0]["body"])["emailStatus"], "pending")
        self.assertNotIn(secret, "\n".join(logs.output))

    def pending_event(self):
        self.body["once"] = True
        self.body["message"] = "Original trigger"
        alert = self.create()
        with patch.object(alerts, "market_values", return_value={"price": {"value": 110}}), \
             patch.object(alerts, "deliver_pending_email", side_effect=RuntimeError("Worker interrupted")), \
             self.assertLogs(alerts.logger, level="ERROR"):
            alerts.monitor_cycle()
        event = next(json.loads(row["body"]) for row in self.events() if row["alert_id"] == alert["id"])
        self.assertEqual(event["emailStatus"], "pending")
        return alert, event

    def test_recovery_before_mail_claim_uses_snapshot_after_edit(self):
        alert, pending = self.pending_event()
        self.body["message"] = "Changed after trigger"
        self.body["symbol"] = "ETHUSDT"
        response = self.clients[1].put("/api/chart-alerts/" + alert["id"], json=self.body,
                                     headers={"X-CSRF-Token": "csrf1"})
        self.assertEqual(response.status_code, 200)
        with patch.object(alerts.account_mail, "send", return_value={"provider": "test"}) as send, \
             patch.object(alerts.mail_templates, "chart_alert", wraps=alerts.mail_templates.chart_alert) as render:
            alerts.monitor_cycle()
            alerts.monitor_cycle()
        send.assert_called_once()
        self.assertEqual(send.call_args[1]["idempotency_key"], "chart-alert/" + pending["id"])
        self.assertEqual(render.call_args[0][0], pending["emailAlert"])
        self.assertEqual(render.call_args[0][0]["symbol"], "BTCUSDT")
        self.assertEqual(json.loads(self.events()[0]["body"])["emailStatus"], "accepted")

    def test_recovery_accepted_ledger_restores_receipt_without_network(self):
        _, pending = self.pending_event()
        delivery_id = "chart-alert/" + pending["id"]
        with self.database() as db:
            db.execute("INSERT INTO mail_deliveries VALUES(?,'accepted','provider-receipt')", (delivery_id,))
        prior = {"provider": "resend", "status": "accepted", "id": "provider-receipt"}
        with patch.object(alerts.account_mail.mail_store, "postgres_enabled", return_value=True), \
             patch.object(alerts.account_mail.mail_store, "claim", return_value=prior) as claim, \
             patch.object(alerts.account_mail, "_send") as network:
            alerts.monitor_cycle()
            alerts.monitor_cycle()
        network.assert_not_called()
        claim.assert_called_once()
        self.assertEqual(claim.call_args[0][0], delivery_id)
        event = json.loads(self.events()[0]["body"])
        self.assertEqual(event["emailStatus"], "accepted")
        self.assertEqual(event["emailId"], "provider-receipt")

    def test_recovery_ambiguous_ledger_never_resends(self):
        for status in ("sending", "failed"):
            _, pending = self.pending_event()
            with self.database() as db:
                db.execute("INSERT INTO mail_deliveries VALUES(?,?,NULL)", ("chart-alert/" + pending["id"], status))
            with patch.object(alerts.account_mail, "send") as send:
                alerts.monitor_cycle()
                alerts.monitor_cycle()
            send.assert_not_called()
            event = next(json.loads(row["body"]) for row in self.events() if row["id"] == pending["id"])
            self.assertEqual(event["emailStatus"], "needs-review")

    def test_recovery_requires_current_verified_nonsuspended_owner(self):
        for column in ("email_verified", "disabled"):
            _, pending = self.pending_event()
            with self.database() as db:
                db.execute("UPDATE users SET " + column + "=? WHERE id=1", (0 if column == "email_verified" else 1,))
            with patch.object(alerts.account_mail, "send") as send:
                alerts.monitor_cycle()
            send.assert_not_called()
            event = next(json.loads(row["body"]) for row in self.events() if row["id"] == pending["id"])
            self.assertEqual(event["emailStatus"], "skipped")
            with self.database() as db:
                db.execute("UPDATE users SET email_verified=1, disabled=0 WHERE id=1")

    def test_old_pending_event_without_snapshot_requires_review(self):
        _, pending = self.pending_event()
        del pending["emailAlert"]
        with self.database() as db:
            db.execute("UPDATE alert_events SET body=? WHERE id=?", (json.dumps(pending), pending["id"]))
        with patch.object(alerts.account_mail, "send") as send:
            alerts.monitor_cycle()
        send.assert_not_called()
        self.assertEqual(json.loads(self.events()[0]["body"])["emailStatus"], "needs-review")

    def test_recovery_losing_claim_cannot_overwrite_acceptance(self):
        _, pending = self.pending_event()
        row = self.events()[0]
        def competing_send(*args, **kwargs):
            accepted = {**pending, "emailStatus": "accepted", "emailProvider": "test"}
            with self.database() as db:
                db.execute("UPDATE alert_events SET body=? WHERE id=?", (json.dumps(accepted), pending["id"]))
                db.execute("INSERT INTO mail_deliveries VALUES(?,'sending',NULL)", ("chart-alert/" + pending["id"],))
            raise alerts.account_mail.MailDeliveryError("Already claimed")
        with patch.object(alerts, "send_email", side_effect=competing_send):
            alerts.deliver_pending_email(row)
        self.assertEqual(json.loads(self.events()[0]["body"])["emailStatus"], "accepted")

    def test_no_web_process_daemon_in_postgres_mode(self):
        with patch.object(alerts.threading, "Thread") as thread:
            alerts.start_monitor()
        thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
