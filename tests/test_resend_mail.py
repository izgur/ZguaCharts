import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import requests
from services import account_mail, mail_templates, chart_alerts


class ResendMailTests(unittest.TestCase):
    def setUp(self):
        self.config = {"provider": "resend", "api_key": "test-key-not-real", "from": "ZguaCharts <onboarding@resend.dev>",
                       "to": "owner@example.test", "test_recipient": "owner@example.test"}
        self.temp = tempfile.TemporaryDirectory()
        self.usage = Path(self.temp.name) / "usage.json"
        self.patches = [patch.object(account_mail, "USAGE", self.usage), patch.object(account_mail.time, "sleep"),
                        patch.object(account_mail.requests, "post")]
        for item in self.patches: item.start()
        self.post = account_mail.requests.post
        self.post.return_value.status_code = 200
        self.post.return_value.json.return_value = {"id": "email-test-id"}

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def send(self, **kwargs):
        return account_mail.send("owner@example.test", "subject", "text", config=self.config, **kwargs)

    def test_payload_and_accepted_not_delivered(self):
        result = self.send(html="<p>hello</p>", idempotency_key="event/test")
        self.assertEqual(result, {"provider": "resend", "id": "email-test-id", "status": "accepted"})
        args, options = self.post.call_args
        self.assertEqual(args[0], "https://api.resend.com/emails")
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(options["timeout"], (5, 15))
        self.assertEqual(options["headers"]["Idempotency-Key"], "event/test")
        self.assertEqual(options["json"]["to"], ["owner@example.test"])
        self.assertEqual(options["json"]["text"], "text")
        self.assertEqual(options["json"]["html"], "<p>hello</p>")
        self.assertNotIn("api_key", options["json"])

    def test_test_sender_and_recipient_restricted_before_network(self):
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "account owner"):
            account_mail.send("other@example.test", "s", "t", config=self.config)
        self.config["from"] = "alerts@resend.dev"
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "onboarding"):
            self.send()
        self.post.assert_not_called()
        self.assertFalse(self.usage.exists())

    def test_verified_domain_has_no_owner_only_restriction(self):
        self.config["from"] = "ZguaCharts <alerts@example.test>"
        account_mail.send("other@example.test", "s", "t", config=self.config)
        self.assertEqual(self.post.call_args[1]["json"]["to"], ["other@example.test"])

    def test_transient_retry_uses_identical_idempotency_key(self):
        success = MagicMock(status_code=200); success.json.return_value = {"id": "retried"}
        self.post.side_effect = [requests.Timeout("sensitive details"), MagicMock(status_code=503), success]
        result = self.send(idempotency_key="event/stable")
        self.assertEqual(result["id"], "retried")
        self.assertEqual(self.post.call_count, 3)
        self.assertEqual({call[1]["headers"]["Idempotency-Key"] for call in self.post.call_args_list}, {"event/stable"})
        self.assertEqual(json.loads(self.usage.read_text())["daily"], 1)

    def test_errors_do_not_echo_provider_response_or_credentials(self):
        self.post.return_value.status_code = 403
        self.post.return_value.text = self.config["api_key"] + " private data"
        with self.assertRaises(account_mail.MailDeliveryError) as caught: self.send()
        self.assertNotIn("private data", str(caught.exception))
        self.assertNotIn(self.config["api_key"], str(caught.exception))
        self.assertEqual(self.post.call_count, 1)

    def test_daily_and_monthly_limits_are_persisted(self):
        self.config["daily_limit"] = 1
        self.send()
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "safety limit"): self.send()
        self.assertEqual(self.post.call_count, 1)
        self.config["daily_limit"] = 50; self.config["monthly_limit"] = 1
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "safety limit"): self.send()

    def test_readiness_and_header_injection(self):
        self.assertTrue(account_mail.configured(self.config))
        for field in ("api_key", "test_recipient"):
            config = dict(self.config); del config[field]
            self.assertFalse(account_mail.configured(config))
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "Invalid"):
            account_mail.send("owner@example.test", "subject\r\nBcc: attacker", "t", config=self.config)
        self.post.assert_not_called()

    def test_invalid_acceptance_response_does_not_claim_success(self):
        self.post.return_value.json.return_value = {}
        with self.assertRaisesRegex(account_mail.MailDeliveryError, "invalid acceptance"): self.send()

    def test_templates_escape_untrusted_message_and_include_plain_text(self):
        alert = {"symbol": "BTCUSDT", "source": "bybit", "timeframe": "1h", "message": '<script>alert("bad")</script>',
                 "match": "all", "conditions": [{"field": "price", "operator": "crossing-up", "value": 100}]}
        subject, text, html = mail_templates.chart_alert(alert, {"time": 10000})
        self.assertIn("BTCUSDT", subject)
        self.assertIn("crossing up", text)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("does not place any orders", text)
        subject, text, html = mail_templates.verification("http://127.0.0.1:5000/charts#verify-email=test")
        self.assertIn("verify-email=test", text)
        self.assertIn("Confirm email address", html)

    def test_alerts_use_shared_transport_and_event_identity(self):
        alert = {"symbol": "BTCUSDT", "source": "bybit", "timeframe": "1h", "message": "Price crossed", "conditions": []}
        with patch.object(chart_alerts, "mail_config", return_value=self.config):
            self.assertTrue(chart_alerts.email_ready())
            chart_alerts.send_email(alert["message"], alert, {"time": 10000, "id": "event-id"})
        self.assertEqual(self.post.call_args[1]["headers"]["Idempotency-Key"], "chart-alert/event-id")


if __name__ == "__main__": unittest.main()
