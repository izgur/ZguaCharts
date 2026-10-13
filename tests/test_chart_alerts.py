import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app as web
from services import chart_alerts as alerts
from data_source import aggregate_chart_candles


class ChartAlertTests(unittest.TestCase):
    def setUp(self):
        for target in (alerts, alerts.account_mail.mail_store):
            mode = patch.object(target, "postgres_enabled", return_value=False)
            mode.start()
            self.addCleanup(mode.stop)

    def alert(self, operator="crossing", once=True):
        return {"enabled": True, "expiresAt": time.time() + 1000, "once": once, "match": "all",
                "conditions": [{"field": "price", "operator": operator, "value": 100}]}

    def test_crossings_and_once_only(self):
        row = self.alert()
        now = time.time()
        self.assertFalse(alerts.evaluate_alert(row, {"price": {"value": 90}}, now))
        self.assertTrue(alerts.evaluate_alert(row, {"price": {"value": 110}}, now))
        self.assertFalse(row["enabled"])
        self.assertFalse(alerts.evaluate_alert(row, {"price": {"value": 90}}, now))

    def test_repeated_crossings_and_direction(self):
        row = self.alert("crossing-up", False)
        now = time.time()
        for value, expected in [(100, False), (101, True), (105, False), (99, False), (101, True)]:
            self.assertEqual(alerts.evaluate_alert(row, {"price": {"value": value}}, now), expected)

    def test_levels_rearm_and_no_creation_backfill(self):
        row = self.alert("above", False)
        now = time.time()
        for value, expected in [(110, False), (111, False), (90, False), (110, True)]:
            self.assertEqual(alerts.evaluate_alert(row, {"price": {"value": value}}, now), expected)

    def test_reference_conditions_expiry_and_unavailable(self):
        row = self.alert("crossing-up")
        row["conditions"][0]["reference"] = "overlay:EMA 9"
        now = time.time()
        self.assertFalse(alerts.evaluate_alert(row, {"price": {"value": 90}}, now))
        self.assertIn("unavailable", row["error"])
        self.assertFalse(alerts.evaluate_alert(row, {"price": {"value": 90}, "overlay:EMA 9": {"value": 100}}, now))
        self.assertTrue(alerts.evaluate_alert(row, {"price": {"value": 110}, "overlay:EMA 9": {"value": 100}}, now))
        row = self.alert(); row["expiresAt"] = now - 1
        self.assertFalse(alerts.evaluate_alert(row, {}, now)); self.assertFalse(row["enabled"])

    def test_api_persistence_and_unconfigured_email(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(alerts, "STORE", Path(directory) / "alerts.json"), patch.object(alerts, "start_monitor"), patch.object(alerts, "email_ready", return_value=False), patch.object(alerts, "market_values", return_value={"price": {"value": 100}}):
            client = web.app.test_client()
            body = {"source": "bybit", "symbol": "BTCUSDT", "timeframe": "1h", "conditions": self.alert()["conditions"], "notifications": {"email": True}}
            self.assertEqual(client.post("/api/chart-alerts", json=body).status_code, 400)
            body["notifications"] = {"toast": True}
            created = client.post("/api/chart-alerts", json=body)
            self.assertEqual(created.status_code, 201)
            self.assertEqual(len(client.get("/api/chart-alerts").get_json()["alerts"]), 1)
            client.delete("/api/chart-alerts/" + created.get_json()["id"])
            self.assertEqual(client.get("/api/chart-alerts").get_json()["alerts"], [])

    def test_all_and_any_multi_conditions(self):
        row = self.alert("above", False)
        row["conditions"].append({"field": "rsi:RSI", "operator": "above", "value": 70})
        now = time.time()
        alerts.evaluate_alert(row, {"price": {"value": 90}, "rsi:RSI": {"value": 65}}, now)
        self.assertFalse(alerts.evaluate_alert(row, {"price": {"value": 110}, "rsi:RSI": {"value": 65}}, now))
        self.assertTrue(alerts.evaluate_alert(row, {"price": {"value": 110}, "rsi:RSI": {"value": 75}}, now))
        row["match"] = "any"
        alerts.evaluate_alert(row, {"price": {"value": 90}, "rsi:RSI": {"value": 65}}, now)
        self.assertTrue(alerts.evaluate_alert(row, {"price": {"value": 90}, "rsi:RSI": {"value": 75}}, now))

    def test_email_uses_tls_and_never_exposes_credentials(self):
        config = {"host": "smtp.example.test", "from": "alerts@example.test", "to": "user@example.test", "username": "user", "password": "secret"}
        with patch.object(alerts, "mail_config", return_value=config), patch.object(alerts.account_mail.smtplib, "SMTP") as smtp:
            alerts.send_email("Price crossed")
            client = smtp.return_value.__enter__.return_value
            client.starttls.assert_called_once()
            client.login.assert_called_once_with("user", "secret")
            client.send_message.assert_called_once()

    def test_stale_market_cannot_trigger(self):
        with patch.object(alerts, "fetch_candles", return_value={"candles": [{"time": 1}]}):
            with self.assertRaisesRegex(ValueError, "stale"):
                alerts.market_values("bybit", "BTCUSDT", "1h")

    def test_edit_replaces_in_place_and_restarts_observation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(alerts, "STORE", Path(directory) / "alerts.json"), patch.object(alerts, "start_monitor"), patch.object(alerts, "market_values", return_value={"price": {"value": 100}}):
            client = web.app.test_client()
            body = {"source": "bybit", "symbol": "BTCUSDT", "timeframe": "1h", "conditions": self.alert()["conditions"], "notifications": {"toast": True}}
            created = client.post("/api/chart-alerts", json=body).get_json()
            body["conditions"][0]["value"] = 200; body["enabled"] = False; body["message"] = "Edited price condition"
            response = client.put("/api/chart-alerts/" + created["id"], json=body)
            self.assertEqual(response.status_code, 200)
            row = response.get_json(); self.assertEqual(row["id"], created["id"]); self.assertEqual(row["createdAt"], created["createdAt"])
            self.assertFalse(row["enabled"]); self.assertEqual(row["revision"], 1); self.assertNotIn("lastTriggeredAt", row)
            store = client.get("/api/chart-alerts").get_json(); self.assertEqual(len(store["alerts"]), 1); self.assertEqual(store["events"], [])
            self.assertEqual(client.put("/api/chart-alerts/missing", json=body).status_code, 404)
            body["enabled"] = True; row = client.put("/api/chart-alerts/" + created["id"], json=body).get_json()
            self.assertTrue(row["enabled"]); self.assertEqual(row["previous"], [-100.0]); self.assertNotIn("lastTriggeredAt", row)

    def test_six_and_twelve_minute_ohlcv_no_gap_fabrication(self):
        rows = [{"time": 3600 + index * 180, "open": index + 1, "high": index + 3, "low": index + .5, "close": index + 2, "volume": 10} for index in range(8)]
        output = aggregate_chart_candles(rows, "6m", "3m")
        self.assertEqual(len(output), 4)
        self.assertEqual(output[0], {"time": 3600, "open": 1, "high": 4, "low": .5, "close": 3, "volume": 20})
        self.assertEqual(len(aggregate_chart_candles(rows, "12m", "3m")), 2)
        self.assertEqual(len(aggregate_chart_candles(rows[1:], "6m", "3m")), 3)


if __name__ == "__main__":
    unittest.main()
