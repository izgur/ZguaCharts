import contextlib
import io
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from services import deployment, storage
from scripts import check_production, start_web


def production_env(**overrides):
    values = {
        "ZGUA_ENV": "production", "DATABASE_URL": "postgresql://user:private@db.example.org/app?sslmode=require",
        "PUBLIC_APP_URL": "https://charts.example.org", "ACCOUNT_MFA_KEY": Fernet.generate_key().decode(),
        "ZGUA_PUBLIC_ACCOUNTS": "1",
    }
    values.update(overrides)
    return values


class EnvironmentTests(unittest.TestCase):
    def test_pure_validation_and_defaults(self):
        with patch.object(storage, "database", side_effect=AssertionError("unexpected I/O")):
            env = production_env()
            before = dict(env)
            self.assertEqual(deployment.validate_environment(env), [])
            self.assertEqual(env, before)
            self.assertTrue(deployment.production_mode(env))
            self.assertFalse(deployment.public_registration_enabled(env))
            self.assertTrue(deployment.public_registration_enabled({}))
            self.assertEqual(deployment.validate_environment({}), [])

    def test_database_tls_and_malformed_urls(self):
        for url in ("sqlite:///data/accounts.sqlite", "postgresql://db/app", "postgresql://db/app?sslmode=disable",
                    "postgresql://db/app?sslmode=require&sslmode=disable", "postgresql://db:bad/app?sslmode=require",
                    "postgresql://db/app?sslmode=prefer", "postgresql:///app?sslmode=require"):
            with self.subTest(url=url):
                self.assertIn("DATABASE_URL", deployment.validate_environment(production_env(DATABASE_URL=url)))
        for mode in ("require", "verify-ca", "verify-full"):
            self.assertEqual(deployment.validate_environment(production_env(
                DATABASE_URL="postgres://db/app?sslmode=" + mode)), [])

    def test_strict_https_origin(self):
        for origin in ("http://charts.example.org", "https://user:private@charts.example.org",
                       "https://charts.example.org/path", "https://charts.example.org/",
                       "https://charts.example.org?", "https://charts.example.org#", "https://charts.example.org:0",
                       "https://charts.example.org:bad", "https://charts.example.org:", "https://charts.example.org\n", "https://charts%2f.example.org",
                       "https://charts.example.org\\evil", "https:///charts.example.org"):
            with self.subTest(origin=origin):
                self.assertIn("PUBLIC_APP_URL", deployment.validate_environment(production_env(PUBLIC_APP_URL=origin)))
        for origin in ("https://charts.example.org:8443", "https://[2001:db8::1]"):
            self.assertEqual(deployment.validate_environment(production_env(PUBLIC_APP_URL=origin)), [])

    def test_secrets_and_public_accounts_are_required(self):
        for key in ("", "private", "A" * 44, "A" * 43 + "=\n"):
            self.assertIn("ACCOUNT_MFA_KEY", deployment.validate_environment(production_env(ACCOUNT_MFA_KEY=key)))
        self.assertIn("ZGUA_PUBLIC_ACCOUNTS", deployment.validate_environment(production_env(ZGUA_PUBLIC_ACCOUNTS="true")))
        self.assertIn("ZGUA_REGISTRATION_ENABLED", deployment.validate_environment(production_env(ZGUA_REGISTRATION_ENABLED="true")))

    def test_resend_registration_requires_attested_real_domain(self):
        env = production_env(ZGUA_REGISTRATION_ENABLED="1", ZGUA_MAIL_PROVIDER="resend",
                             ZGUA_MAIL_FROM="Charts <signup@example.org>", RESEND_API_KEY="private",
                             ZGUA_MAIL_DOMAIN_VERIFIED="1")
        self.assertEqual(deployment.validate_environment(env), [])
        for sender in ("Charts <onboarding@resend.dev>", "signup@sub.resend.dev", "not-an-email", "a@example.org\nBcc: b@example.org"):
            self.assertIn("mail.sender_domain", deployment.validate_environment(dict(env, ZGUA_MAIL_FROM=sender)))
        self.assertIn("ZGUA_MAIL_DOMAIN_VERIFIED", deployment.validate_environment(dict(env, ZGUA_MAIL_DOMAIN_VERIFIED="0")))
        self.assertIn("RESEND_API_KEY", deployment.validate_environment(dict(env, RESEND_API_KEY="")))
        self.assertEqual(deployment.validate_environment(dict(env, ZGUA_REGISTRATION_ENABLED="0",
                         ZGUA_MAIL_FROM="onboarding@resend.dev", ZGUA_MAIL_DOMAIN_VERIFIED="0")), [])

    def test_smtp_registration_requires_explicit_secure_configuration(self):
        env = production_env(ZGUA_REGISTRATION_ENABLED="1", ZGUA_MAIL_PROVIDER="smtp", ZGUA_SMTP_FROM="signup@example.org",
                             ZGUA_MAIL_DOMAIN_VERIFIED="1", ZGUA_SMTP_HOST="mail.example.org", ZGUA_SMTP_PORT="587",
                             ZGUA_SMTP_SECURITY="starttls", ZGUA_SMTP_USERNAME="user", ZGUA_SMTP_PASSWORD="private")
        self.assertEqual(deployment.validate_environment(env), [])
        for name in ("ZGUA_SMTP_SECURITY", "ZGUA_SMTP_PORT", "ZGUA_SMTP_HOST", "ZGUA_SMTP_PASSWORD"):
            self.assertTrue(deployment.validate_environment(dict(env, **{name: ""})))
        self.assertIn("mail.sender_configuration", deployment.validate_environment(dict(env, ZGUA_MAIL_FROM="onboarding@resend.dev")))

    def test_optional_development_endpoint_guard_handles_neon_pooling(self):
        env = production_env(DATABASE_URL="postgresql://u:p@ep-branch-pooler.example.org/app?sslmode=require",
                             ZGUA_DEVELOPMENT_DATABASE_URL="postgresql://other:secret@ep-branch.example.org/app?sslmode=require")
        self.assertIn("database.environment_separation", deployment.validate_environment(env))
        self.assertEqual(deployment.validate_environment(dict(env,
            ZGUA_DEVELOPMENT_DATABASE_URL="postgresql://db.other.org/app?sslmode=require")), [])


class DatabaseTests(unittest.TestCase):
    def database(self, missing_version=False, missing_column=False):
        db = MagicMock()
        versions = [] if missing_version else [(p.name,) for p in (deployment.ROOT / "migrations").glob("*.sql")]
        columns = [(table, column) for table, names in deployment.REQUIRED_SCHEMA.items() for column in names.split()
                   if not (missing_column and table == "users" and column == "totp_secret")]
        def execute(sql, *args):
            cursor = MagicMock()
            cursor.fetchall.return_value = columns if "information_schema" in sql else versions
            cursor.fetchone.return_value = ('{"environment":"production"}',) if 'deployment_environment' in sql else (True,)
            return cursor
        db.execute.side_effect = execute
        return db

    def test_development_database_and_missing_admin_are_not_release_ready(self):
        db = self.database()
        original = db.execute.side_effect
        def execute(sql,*args):
            cursor = original(sql,*args)
            if 'deployment_environment' in sql:
                cursor.fetchone.return_value = ('{"environment":"development"}',)
            elif 'SELECT EXISTS' in sql:
                cursor.fetchone.return_value = (False,)
            return cursor
        db.execute.side_effect = execute
        with patch.dict(os.environ,production_env(),clear=True),patch.object(storage,'database',return_value=contextlib.nullcontext(db)):
            self.assertEqual(deployment.database_errors(),['database.environment','database.administrator'])

    def test_readiness_checks_versions_columns_and_read_only_transaction(self):
        for version, column, expected in ((False, False, []), (True, False, ["database.migrations"]),
                                         (False, True, ["database.schema"]),
                                         (True, True, ["database.migrations", "database.schema"])):
            db = self.database(version, column)
            with self.subTest(version=version, column=column), patch.dict(os.environ, production_env(), clear=True), \
                    patch.object(storage, "database", return_value=contextlib.nullcontext(db)):
                self.assertEqual(deployment.database_errors(), expected)
                self.assertEqual(db.execute.call_args_list[0].args[0], "SET TRANSACTION READ ONLY")
                for call in db.execute.call_args_list:
                    self.assertFalse(call.args[0].startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER")))

    def test_connection_failure_is_redacted_and_never_falls_back(self):
        with patch.dict(os.environ, production_env(), clear=True), \
                patch.object(storage, "database", side_effect=RuntimeError("postgresql://user:private@secret")):
            self.assertEqual(deployment.database_errors(), ["database.readiness"])
        with patch.dict(os.environ, {}, clear=True), patch.object(storage, "database") as database:
            self.assertEqual(deployment.database_errors(), ["DATABASE_URL"])
            database.assert_not_called()

    def test_active_flask_configuration_is_honored(self):
        from flask import Flask
        app = Flask(__name__)
        app.config["DATABASE_URL"] = "sqlite:///bad"
        with patch.dict(os.environ, production_env(), clear=True), app.app_context(), patch.object(storage, "database") as database:
            self.assertEqual(deployment.database_errors(), ["DATABASE_URL"])
            database.assert_not_called()


class CliAndStartupTests(unittest.TestCase):
    def capture_check(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = check_production.main(args)
        return code, output.getvalue()

    def test_cli_offline_never_connects_and_database_is_opt_in(self):
        with patch.dict(os.environ, production_env(), clear=True), patch.object(check_production, "database_errors", return_value=[]) as check:
            self.assertEqual(self.capture_check(["--offline"]), (0, "PASS environment\n"))
            check.assert_not_called()
            self.assertEqual(self.capture_check([]), (0, "PASS environment\n"))
            self.assertEqual(self.capture_check(["--database"]), (0, "PASS environment\nPASS database.readiness\n"))
            check.assert_called_once_with()

    def test_cli_bad_environment_never_connects_or_prints_secrets(self):
        with patch.dict(os.environ, production_env(DATABASE_URL="private-password-url", PUBLIC_APP_URL="private-url"), clear=True), \
                patch.object(check_production, "database_errors") as check:
            self.assertEqual(self.capture_check(["--database"]), (1, "FAIL DATABASE_URL\nFAIL PUBLIC_APP_URL\n"))
            check.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.capture_check(["--offline"]), (1, "FAIL ZGUA_ENV\n"))

    def test_cli_real_process_does_not_load_local_dotenv_or_app(self):
        child_env = os.environ.copy()
        for name in list(child_env):
            if name.startswith("ZGUA_") or name in ("DATABASE_URL", "ACCOUNT_MFA_KEY", "PUBLIC_APP_URL"):
                child_env.pop(name)
        child_env["ZGUA_ENV"] = "production"
        result = subprocess.run([sys.executable, str(deployment.ROOT / "scripts/check_production.py"), "--offline"],
                                cwd=str(deployment.ROOT / "scripts"), env=child_env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "FAIL DATABASE_URL\nFAIL PUBLIC_APP_URL\nFAIL ACCOUNT_MFA_KEY\nFAIL ZGUA_PUBLIC_ACCOUNTS\n")

    def test_invalid_cli_arguments_do_not_echo_values(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as caught:
            check_production.main(["--private-password-url"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(output.getvalue(), "FAIL cli.arguments\n")

    def test_bounded_command_and_invalid_ports(self):
        command = start_web.gunicorn_command({"PORT": "12345"})
        self.assertEqual(command, [sys.executable, "-m", "gunicorn", "app:app", "--bind", "0.0.0.0:12345",
                         "--workers", "1", "--threads", "2", "--timeout", "180", "--max-requests", "500",
                         "--max-requests-jitter", "50"])
        for port in ("0", "65536", "x", "80 --workers 20", "-1", " 80", "\u0661", ""):
            with self.subTest(port=port), self.assertRaises(ValueError):
                start_web.gunicorn_command({"PORT": port})

    def test_startup_blocks_bad_configuration_or_schema_before_gunicorn(self):
        for overrides, db_errors in (({"ACCOUNT_MFA_KEY": "bad"}, []), ({}, ["database.schema"]), ({"PORT": "bad"}, [])):
            with patch.dict(os.environ, production_env(**overrides), clear=True), \
                    patch.object(start_web, "database_errors", return_value=db_errors), \
                    patch.object(start_web, "runtime_assets_errors", return_value=[]), \
                    patch.object(start_web.subprocess, "Popen") as spawn, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(start_web.main(), 1)
                spawn.assert_not_called()

    def test_startup_order_exit_status_and_signal_forwarding(self):
        calls = []
        child = MagicMock()
        child.poll.return_value = None
        handlers = {}
        def register(signum, handler):
            handlers[signum] = handler
            return "old-handler"
        def wait():
            handlers[start_web.signal.SIGTERM](start_web.signal.SIGTERM, None)
            return 7
        child.wait.side_effect = wait
        def spawn(*args, **kwargs):
            calls.append("spawn")
            self.assertNotIn("GUNICORN_CMD_ARGS", kwargs["env"])
            self.assertEqual(kwargs["cwd"], str(deployment.ROOT))
            return child
        def check():
            calls.append("database")
            return []
        with patch.dict(os.environ, production_env(GUNICORN_CMD_ARGS="--workers 99"), clear=True), \
                patch.object(start_web, "database_errors", side_effect=check), \
                patch.object(start_web, "runtime_assets_errors", return_value=[]), \
                patch.object(start_web.subprocess, "Popen", side_effect=spawn), \
                patch.object(start_web.signal, "signal", side_effect=register):
            self.assertEqual(start_web.main(), 7)
        self.assertEqual(calls, ["database", "spawn"])
        child.send_signal.assert_called_once_with(start_web.signal.SIGTERM)
        self.assertEqual(set(handlers.values()), {"old-handler"})


if __name__ == "__main__":
    unittest.main()
