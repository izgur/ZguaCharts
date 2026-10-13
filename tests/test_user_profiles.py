import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from flask import Flask

from services.chart_accounts import blueprint
from services.storage import database
from services.user_profiles import ProfileConflict, load_profile, save_snapshot


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DATABASE_URL="", ACCOUNT_DATABASE=str(Path(self.temp.name) / "accounts.sqlite"))
        self.app.register_blueprint(blueprint)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def save(self, body, revision, user_id=1):
        with database() as db:
            return save_snapshot(db, user_id, body, revision)

    def load(self, user_id=1):
        with database() as db:
            return load_profile(db, user_id)

    def test_projection_normalization_and_source_history(self):
        indicator = {"id": "custom_test", "name": "Test", "code": "print(1)", "schema": []}
        body = {
            "zgua-ui-preferences": json.dumps({"watchlist": ["BTCUSDT"], "watchLists": [{"id": "yellow", "members": []}], "theme": "dark"}),
            "zgua-layouts-v1": json.dumps({"active": "one", "items": [{"id": "one", "name": "One", "values": {}}]}),
            "zgua-custom-indicators-v1": json.dumps([indicator]),
            "zgua-custom-tactics-v1": json.dumps([{"id": "tactic_test", "code": "return 1"}]),
            "zgua-drawings-v1:BTCUSDT": '[{"id":"line"}]',
            "zgua-indicator-favourites-v1": '["rsi"]',
            "zgua-default-chart-layout-v2": "done",
        }
        self.assertEqual(self.save(body, 0), 1)
        projected, revision = self.load()
        self.assertEqual(revision, 1)
        for key, raw in body.items():
            self.assertEqual(projected[key] if raw == "done" else json.loads(projected[key]), raw if raw == "done" else json.loads(raw))
        with database() as db:
            kinds = {row["kind"] for row in db.execute("SELECT kind FROM user_documents")}
            self.assertTrue({"watchlist", "layout", "indicator", "tactic", "drawing", "drawings", "favourites", "preferences"} <= kinds)
            self.assertEqual(json.loads(db.execute("SELECT body FROM profiles").fetchone()["body"]), {"format": "user-documents-v1", "normalized": True})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM user_document_versions").fetchone()[0], 2)
        # Unchanged source records do not create new versions.
        self.assertEqual(self.save(projected, 1), 2)
        indicator["code"] = "print(2)"
        body["zgua-custom-indicators-v1"] = json.dumps([indicator])
        self.assertEqual(self.save(body, 2), 3)
        with database() as db:
            history = db.execute("SELECT revision,body FROM user_document_versions WHERE kind='indicator' ORDER BY revision").fetchall()
            self.assertEqual([row["revision"] for row in history], [1, 2])
            self.assertEqual(json.loads(history[0]["body"])["source_version"], 1)
            self.assertEqual(json.loads(history[1]["body"])["value"]["code"], "print(2)")

    def test_conflicts_isolation_deletion_and_recreation(self):
        body = {"zgua-custom-indicators-v1": '[{"id":"custom_a","code":"old"}]'}
        self.save(body, 0)
        with self.assertRaises(ProfileConflict) as conflict:
            self.save({}, 0)
        self.assertEqual(conflict.exception.revision, 1)
        self.assertEqual(json.loads(self.load()[0]["zgua-custom-indicators-v1"])[0]["code"], "old")
        self.assertEqual(self.load(2), (None, 0))
        self.save({"private": "other"}, 0, 2)
        self.save({}, 1)
        self.assertEqual(self.load(), ({}, 2))
        self.save(body, 2)
        with database() as db:
            self.assertEqual(db.execute("SELECT revision FROM user_documents WHERE kind='indicator'").fetchone()[0], 2)
        self.assertEqual(self.load(2), ({"private": "other"}, 1))

    def test_legacy_schema_and_snapshot_import(self):
        path = self.app.config["ACCOUNT_DATABASE"]
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE profiles(user_id INTEGER PRIMARY KEY,body TEXT NOT NULL)")
        body = {"zgua-ui-preferences": '{"watchlist":["ETHUSDT"]}', "old-key": "unparsed"}
        db.execute("INSERT INTO profiles(user_id,body) VALUES (?,?)", (1, json.dumps(body)))
        db.commit()
        db.close()
        self.assertEqual(self.load(), (body, 0))
        projected, revision = self.load()
        self.assertEqual(revision, 0)
        self.assertEqual(json.loads(projected["zgua-ui-preferences"]), {"watchlist": ["ETHUSDT"]})
        self.assertEqual(projected["old-key"], "unparsed")
        # Importer may already own a transaction.
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            self.assertEqual(save_snapshot(db, 2, body, 0), 1)

    def test_concurrent_writers_cannot_share_a_revision(self):
        self.save({}, 0)
        barrier, results = threading.Barrier(2), []

        def writer(value):
            with self.app.app_context():
                barrier.wait(timeout=10)
                try:
                    results.append(("saved", self.save({"winner": value}, 1)))
                except ProfileConflict as exc:
                    results.append(("conflict", exc.revision))

        threads = [threading.Thread(target=writer, args=(value,)) for value in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(results), [("conflict", 2), ("saved", 2)])
        self.assertEqual(self.load()[1], 2)

    def test_endpoint_revisions_legacy_import_and_stale_rejection(self):
        client = self.app.test_client()
        csrf = client.get("/api/account/session").get_json()["csrf"]
        with database() as db:
            db.execute("INSERT INTO users(id,name,salt,hash) VALUES (1,'profile-test','unused','unused')")
            db.execute("UPDATE sessions SET user_id=1 WHERE csrf=?", (csrf,))
        headers = {"X-CSRF-Token": csrf}
        self.assertEqual(client.get("/api/account/profile").get_json()["revision"], 0)
        legacy = client.put("/api/account/profile", json={"tvk-dashboard-state": "{}"}, headers=headers)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.get_json()["revision"], 1)
        for payload in ({"revision": 0, "profile": {}}, {"tvk-dashboard-state": "stale"}):
            response = client.put("/api/account/profile", json=payload, headers=headers)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.get_json()["revision"], 1)
        for revision in (True, -1, "1", None):
            self.assertEqual(client.put("/api/account/profile", json={"revision": revision, "profile": {}}, headers=headers).status_code, 400)
        response = client.put("/api/account/profile", json={"revision": 1, "profile": {"tvk-dashboard-state": "updated"}}, headers=headers)
        self.assertEqual(response.get_json()["revision"], 2)
        self.assertEqual(client.get("/api/account/session").get_json()["profile"], {"tvk-dashboard-state": "updated"})


if __name__ == "__main__":
    unittest.main()
