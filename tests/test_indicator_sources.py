import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from flask import Flask
from indicators import available_indicators, build_indicator_payload
from services.indicator_sources import blueprint, source_payload, ROOT


class IndicatorSourceTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(blueprint)
        self.client = app.test_client()

    def test_every_registered_indicator_exports_actual_source(self):
        for entry in available_indicators():
            with self.subTest(indicator=entry["id"]):
                response = self.client.get("/api/indicator-source/" + entry["id"])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                payload = response.get_json()
                self.assertEqual(payload["label"], entry["label"])
                self.assertEqual(len(payload["files"]), 1)
                for file in payload["files"]:
                    self.assertEqual(file["name"], entry["id"] + ".py")
                    self.assertEqual(file["sha256"], hashlib.sha256(file["code"].encode()).hexdigest())
                    compile(file["code"], file["name"], "exec")
                    self.assertGreater(file["focusLine"], 0)

    def test_selected_formula_is_focused(self):
        for key in ("rsi", "wavetrend", "utbot", "vixfix"):
            file = source_payload(key)["files"][0]
            self.assertIn("def indicator(candles, params=None)", file["code"])
            self.assertNotIn('"macd": "MACD', file["code"])

    def test_all_exported_formulas_match_native_series(self):
        import math
        rows = [{"time": 1700000000 + i * 3600, "open": 100 + math.sin(i / 8),
                 "high": 103 + math.sin(i / 8), "low": 97 + math.sin(i / 8),
                 "close": 100 + math.sin(i / 8) + math.cos(i / 3), "volume": 100 + i % 17}
                for i in range(250)]
        def values(payload):
            return [series["data"] for series in payload["overlays"]] + [
                series["data"] for pane in payload["panes"] for series in pane["series"]]
        for entry in available_indicators():
            with self.subTest(indicator=entry["id"]):
                namespace = {}
                exec(source_payload(entry["id"])["files"][0]["code"], namespace)
                actual = namespace["indicator"](rows)
                expected = build_indicator_payload(rows, [entry["id"]])
                self.assertEqual(values(actual), values(expected))

    def test_worker_csp_restricts_network_to_runtime_assets(self):
        response = self.client.get("/api/indicator-source/worker.js")
        self.assertEqual(response.status_code, 200)
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("connect-src http://localhost/static/pyodide/;", policy)
        self.assertIn("worker-src 'none'", policy)

    def test_unknown_ids_and_paths_do_not_expose_files(self):
        for name in ("fake", "app.py", "alert-email.json", "..%2Fapp.py"):
            for suffix in ("", "/download"):
                self.assertEqual(self.client.get("/api/indicator-source/" + name + suffix).status_code, 404)

    def test_download_is_self_contained_and_runs_without_project_imports(self):
        rows = [{"time": i + 1, "open": 100 + i, "high": 102 + i, "low": 99 + i,
                 "close": 101 + i, "volume": 100} for i in range(100)]
        for key in ("rsi", "utbot", "squeeze_lb"):
            response = self.client.get("/api/indicator-source/" + key + "/download")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/zip")
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive, tempfile.TemporaryDirectory() as tmp:
                self.assertEqual(set(archive.namelist()), {key + ".py",
                                 "run_indicator.py", "requirements.txt", "README.txt", "manifest.json", "MPL-2.0.txt", "COMMUNITY-NOTICES.md"})
                archive.extractall(tmp)
                path = Path(tmp) / "candles.json"
                path.write_text(json.dumps(rows), encoding="utf-8")
                completed = subprocess.run([sys.executable, "run_indicator.py", "candles.json"], cwd=tmp,
                                           capture_output=True, text=True, timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertTrue(payload["panes"] or payload["overlays"])


if __name__ == "__main__":
    unittest.main()
