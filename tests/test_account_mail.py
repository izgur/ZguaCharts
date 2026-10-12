import unittest
from unittest.mock import patch, MagicMock
from services import account_mail


class AccountMailTests(unittest.TestCase):
    def test_starttls_uses_verified_context_and_registered_recipient(self):
        config = {"host": "smtp.example.test", "from": "charts@example.test", "to": "fixed-alert@example.test", "username": "sender", "password": "test-only"}
        smtp = MagicMock()
        with patch.object(account_mail, "configuration", return_value=config), patch.object(account_mail.smtplib, "SMTP", return_value=smtp) as connection:
            account_mail.send("new-user@example.test", "Confirm account", "test message")
        connection.assert_called_once_with("smtp.example.test", 587, timeout=15)
        client = smtp.__enter__.return_value
        context = client.starttls.call_args[1]["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(client.send_message.call_args[0][0]["To"], "new-user@example.test")

    def test_insecure_or_missing_configuration_never_sends(self):
        with patch.object(account_mail, "configuration", return_value={"host": "smtp.example.test", "from": "charts@example.test", "security": "none"}), patch.object(account_mail.smtplib, "SMTP") as connection:
            self.assertFalse(account_mail.ready())
            with self.assertRaises(ValueError): account_mail.send("new-user@example.test", "subject", "test")
            connection.assert_not_called()
