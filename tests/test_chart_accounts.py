import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import re
from contextlib import closing

from flask import Flask
from services.chart_accounts import blueprint, COOKIE


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "accounts.sqlite"
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, ACCOUNT_DATABASE=str(self.path))
        self.app.register_blueprint(blueprint)
        self.client = self.app.test_client()
        self.mail_ready = patch("services.chart_accounts.account_mail.ready", return_value=True)
        self.mail_send = patch("services.chart_accounts.account_mail.send")
        self.mail_ready.start()
        self.sender = self.mail_send.start()
        self.csrf = self.client.get("/api/account/session").get_json()["csrf"]

    def tearDown(self):
        self.mail_ready.stop()
        self.mail_send.stop()
        self.temp.cleanup()

    def post(self, path, body=None, client=None, csrf=None, method="POST"):
        return (client or self.client).open("/api/account/" + path, method=method, json=body or {}, headers={"X-CSRF-Token": csrf or self.csrf})

    def register(self, name="tester"):
        response = self.post("register", self.registration_body(name))
        self.assertEqual(response.status_code, 201)
        token = re.search(r"verify-email=([A-Za-z0-9_-]+)", self.sender.call_args[0][2]).group(1)
        self.assertEqual(self.post("verify", {"token": token}).status_code, 200)
        response = self.post("login", {"username": name, "password": "test-only-long-password", "remember": True})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.get_json()["csrf"]
        return response

    def registration_body(self, name="tester"):
        return {"username": name, "firstName": "Test", "lastName": "User", "email": name + "@example.test", "password": "test-only-long-password", "confirmPassword": "test-only-long-password"}

    def test_registration_hash_rotation_remember_and_logout(self):
        old_csrf = self.csrf
        response = self.register()
        cookie = response.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Max-Age=2592000", cookie)
        self.assertNotEqual(old_csrf, self.csrf)
        with closing(sqlite3.connect(str(self.path))) as db:
            salt, hashed = db.execute("SELECT salt, hash FROM users").fetchone()
            self.assertEqual(len(salt), 64)
            self.assertNotIn("password", hashed)
        self.assertEqual(self.client.get("/api/account/session").get_json()["user"]["name"], "tester")
        self.assertEqual(self.post("profile", {"data": "private"}, csrf=old_csrf, method="PUT").status_code, 403)
        self.assertEqual(self.post("logout").status_code, 200)
        self.assertIsNone(self.client.get("/api/account/session").get_json()["user"])

    def test_profile_isolation_and_persistence(self):
        self.register()
        self.assertEqual(self.post("profile", {"tvk-dashboard-state": json.dumps({"count": 4})}, method="PUT").status_code, 200)
        other = self.app.test_client()
        token = other.get("/api/account/session").get_json()["csrf"]
        response = self.post("register", self.registration_body("other"), client=other, csrf=token)
        self.assertEqual(response.status_code, 201)
        verification = re.search(r"verify-email=([A-Za-z0-9_-]+)", self.sender.call_args[0][2]).group(1)
        self.post("verify", {"token": verification}, client=other, csrf=token)
        self.post("login", {"username": "other", "password": "test-only-long-password"}, client=other, csrf=token)
        self.assertIsNone(other.get("/api/account/session").get_json()["profile"])
        self.assertIn("tvk-dashboard-state", self.client.get("/api/account/session").get_json()["profile"])

    def test_cross_origin_remote_and_missing_csrf_rejected(self):
        self.assertEqual(self.post("register", ["invalid"] ).status_code, 400)
        self.assertEqual(self.client.post("/api/account/register", json={}).status_code, 403)
        response = self.client.post("/api/account/register", json={}, headers={"Origin": "https://attacker.invalid", "X-CSRF-Token": self.csrf})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/account/session", base_url="http://attacker.invalid").status_code, 403)
        self.assertEqual(self.client.get("/api/account/session", environ_base={"REMOTE_ADDR": "192.0.2.1"}).status_code, 403)

    def test_password_validation_login_and_expiry(self):
        self.assertEqual(self.post("register", {"username": "tester", "password": "short"}).status_code, 400)
        self.register()
        self.post("logout")
        self.csrf = self.client.get("/api/account/session").get_json()["csrf"]
        self.assertEqual(self.post("login", {"username": "tester", "password": "wrong-but-long-password"}).status_code, 401)
        self.assertEqual(self.post("login", {"username": "tester", "password": "test-only-long-password"}).status_code, 200)
        with closing(sqlite3.connect(str(self.path))) as db:
            db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
            db.commit()
        self.assertIsNone(self.client.get("/api/account/session").get_json()["user"])

    def test_rate_limit_and_profile_limit(self):
        for unused in range(10):
            self.post("login", {"username": "tester", "password": "wrong-but-long-password"})
        self.assertEqual(self.post("login", {"username": "tester", "password": "wrong-but-long-password"}).status_code, 429)
        self.assertEqual(self.post("profile", {"data": "x" * 2100000}, method="PUT").status_code, 413)

    def test_confirmation_required_single_use_and_email_login(self):
        self.assertEqual(self.post("register", self.registration_body()).status_code, 201)
        token = re.search(r"verify-email=([A-Za-z0-9_-]+)", self.sender.call_args[0][2]).group(1)
        credentials = {"username": "tester@example.test", "password": "test-only-long-password"}
        self.assertEqual(self.post("login", credentials).status_code, 403)
        self.assertIsNone(self.client.get("/api/account/session").get_json()["user"])
        with closing(sqlite3.connect(str(self.path))) as db:
            stored = db.execute("SELECT token FROM email_tokens").fetchone()[0]
            self.assertNotEqual(token, stored)
        self.assertEqual(self.post("verify", {"token": token}).status_code, 200)
        self.assertEqual(self.post("verify", {"token": token}).status_code, 400)
        self.assertEqual(self.post("login", credentials).status_code, 200)

    def test_mail_disabled_failure_and_field_validation(self):
        with patch("services.chart_accounts.account_mail.ready", return_value=False):
            self.assertFalse(self.client.get("/api/account/session").get_json()["registrationEnabled"])
            self.assertEqual(self.post("register", self.registration_body()).status_code, 503)
        for field, value in (("firstName", ""), ("lastName", []), ("email", "bad\r\nmail@example.test"), ("confirmPassword", "mismatch")):
            body = self.registration_body(); body[field] = value
            self.assertEqual(self.post("register", body).status_code, 400)
        self.sender.side_effect = OSError("SMTP unavailable")
        self.assertEqual(self.post("register", self.registration_body()).status_code, 502)
        with closing(sqlite3.connect(str(self.path))) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_expired_token_and_resend_cooldown(self):
        self.post("register", self.registration_body())
        token = re.search(r"verify-email=([A-Za-z0-9_-]+)", self.sender.call_args[0][2]).group(1)
        self.post("resend-verification", {"email": "tester@example.test"})
        self.assertEqual(self.sender.call_count, 1)
        with closing(sqlite3.connect(str(self.path))) as db:
            db.execute("UPDATE email_tokens SET expires=?", (time.time() - 1,))
            db.commit()
        self.assertEqual(self.post("verify", {"token": token}).status_code, 400)
        self.post("resend-verification", {"email": "tester@example.test"})
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(self.post("verify", {"token": token}).status_code, 400)

    def test_legacy_user_migration_preserves_login(self):
        from services.chart_accounts import password_hash
        salt = "ab" * 32
        legacy = Path(self.temp.name) / "legacy.sqlite"
        with closing(sqlite3.connect(str(legacy))) as db:
            db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL)")
            db.execute("INSERT INTO users VALUES (1,?,?,?)", ("legacy", salt, password_hash("test-only-long-password", salt)))
            db.commit()
        self.app.config["ACCOUNT_DATABASE"] = str(legacy)
        client = self.app.test_client(); csrf = client.get("/api/account/session").get_json()["csrf"]
        self.assertEqual(self.post("login", {"username": "legacy", "password": "test-only-long-password"}, client=client, csrf=csrf).status_code, 200)


if __name__ == "__main__":
    unittest.main()
