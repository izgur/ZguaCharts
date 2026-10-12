import base64
import json
import re
import sqlite3
import struct
import tempfile
import time
import unittest
import zlib
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from services import chart_accounts
from services.account_avatar import clean_avatar


def png(width=1, height=1, extra=b""):
    def chunk(kind, body): return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    if extra: data += chunk(b"tEXt", extra)
    data += chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0\xff" * width) * height)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(data).decode()


class AccountSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / "accounts.sqlite"
        self.app = Flask(__name__); self.app.config.update(TESTING=True, ACCOUNT_DATABASE=str(self.path)); self.app.register_blueprint(chart_accounts.blueprint)
        self.client = self.app.test_client(); self.csrf = self.client.get("/api/account/session").get_json()["csrf"]
        self.ready = patch.object(chart_accounts.account_mail, "ready", return_value=True); self.ready.start()
        self.sending = patch.object(chart_accounts.account_mail, "send"); self.sender = self.sending.start()
        self.password = "test-only-long-password"
        body = {"username": "tester", "firstName": "Test", "lastName": "User", "email": "tester@example.test", "password": self.password, "confirmPassword": self.password}
        self.assertEqual(self.post("register", body).status_code, 201)
        token = self.mail_token("verify-email")
        self.assertEqual(self.post("verify", {"token": token}).status_code, 200)
        self.assertEqual(self.post("login", {"username": "tester", "password": self.password}).status_code, 200)

    def tearDown(self):
        self.ready.stop(); self.sending.stop(); self.temp.cleanup()

    def post(self, endpoint, body=None, method="POST"):
        response = self.client.open("/api/account/" + endpoint, method=method, json=body or {}, headers={"X-CSRF-Token": self.csrf})
        if response.get_json().get("csrf"): self.csrf = response.get_json()["csrf"]
        return response

    def mail_token(self, name): return re.search(name + r"=([A-Za-z0-9_-]+)", self.sender.call_args[0][2]).group(1)

    def test_duplicate_email_has_specific_notice(self):
        body = {"username": "different", "firstName": "Test", "lastName": "User", "email": "TESTER@example.test", "password": self.password, "confirmPassword": self.password}
        result = self.post("register", body)
        self.assertEqual(result.status_code, 409); self.assertIn("email address is already used", result.get_json()["error"])

    def test_profile_and_avatar_saved_without_touching_chart_preferences(self):
        self.post("profile", {"zgua-ui-preferences": json.dumps({"watchFontSize": 15})}, "PUT")
        result = self.post("details", {"firstName": "New", "lastName": "Name", "username": "new.name", "avatar": png(extra=b"private metadata")}, "PUT")
        self.assertEqual(result.status_code, 200)
        account = self.client.get("/api/account/session").get_json()
        self.assertEqual(account["user"]["first_name"], "New"); self.assertEqual(account["user"]["name"], "new.name")
        self.assertNotIn(b"private metadata", base64.b64decode(account["user"]["avatar"].split(",")[1]))
        self.assertEqual(json.loads(account["profile"]["zgua-ui-preferences"])["watchFontSize"], 15)

    def test_avatars_reject_svg_oversize_and_invalid_png(self):
        for avatar in ("data:image/svg+xml,<svg/>", png(129), "data:image/png;base64,AAAA", "data:image/png;base64," + "A" * 120000):
            with self.assertRaises(ValueError): clean_avatar(avatar)
        self.assertIsNone(clean_avatar(""))

    def test_password_requires_current_and_revokes_old_sessions(self):
        other = self.app.test_client(); csrf = other.get("/api/account/session").get_json()["csrf"]
        login = other.post("/api/account/login", json={"username": "tester", "password": self.password}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(login.status_code, 200)
        body = {"currentPassword": "wrong", "password": "new-test-only-password", "confirmPassword": "new-test-only-password"}
        self.assertEqual(self.post("password", body, "PUT").status_code, 403)
        body["currentPassword"] = self.password
        self.assertEqual(self.post("password", body, "PUT").status_code, 200)
        self.assertIsNone(other.get("/api/account/session").get_json()["user"])
        self.assertEqual(self.post("login", {"username": "tester", "password": self.password}).status_code, 401)
        self.assertEqual(self.post("login", {"username": "tester", "password": body["password"]}).status_code, 200)

    def test_reset_is_one_use_hashed_and_does_not_sign_in(self):
        self.post("forgot-password", {"email": "tester@example.test"}); token = self.mail_token("reset-password")
        with closing(sqlite3.connect(str(self.path))) as db:
            self.assertNotEqual(db.execute("SELECT token FROM account_tokens WHERE purpose='reset'").fetchone()[0], token)
        body = {"token": token, "password": "reset-test-only-password", "confirmPassword": "reset-test-only-password"}
        self.assertEqual(self.post("reset-password", body).status_code, 200)
        self.assertIsNone(self.client.get("/api/account/session").get_json()["user"])
        self.assertEqual(self.post("reset-password", body).status_code, 400)
        self.assertEqual(self.post("login", {"username": "tester", "password": body["password"]}).status_code, 200)

    def test_reset_unknown_email_generic_and_known_cooldown(self):
        unknown = self.post("forgot-password", {"email": "unknown@example.test"}).get_json()
        known = self.post("forgot-password", {"email": "tester@example.test"}).get_json()
        self.assertEqual(unknown, known)
        count = self.sender.call_count; self.post("forgot-password", {"email": "tester@example.test"})
        self.assertEqual(count, self.sender.call_count)

    def test_new_email_stays_pending_until_verified(self):
        self.assertEqual(self.post("email", {"email": "new@example.test", "currentPassword": self.password}).status_code, 200)
        token = self.mail_token("verify-email")
        self.assertEqual(self.client.get("/api/account/session").get_json()["user"]["email"], "tester@example.test")
        self.assertEqual(self.post("verify", {"token": token}).status_code, 200)
        self.assertIsNone(self.client.get("/api/account/session").get_json()["user"])
        self.assertEqual(self.post("verify", {"token": token}).status_code, 400)
        self.assertEqual(self.post("login", {"username": "new@example.test", "password": self.password}).status_code, 200)

    def test_unsigned_settings_rejected_and_sessions_revoked(self):
        self.assertEqual(self.post("sessions/revoke").status_code, 200)
        self.post("logout")
        for path, method in (("details", "PUT"), ("password", "PUT"), ("email", "POST"), ("sessions/revoke", "POST")):
            self.assertEqual(self.post(path, {}, method).status_code, 401)


if __name__ == "__main__": unittest.main()
