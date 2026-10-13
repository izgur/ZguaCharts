"""Security regressions using only disposable SQLite files and mocked mail."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from flask import Flask, g
import pyotp
from werkzeug.test import EnvironBuilder

from services import account_security as security, chart_accounts as accounts


class AccountHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'accounts.sqlite'
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, ACCOUNT_DATABASE=str(self.path),
            DATABASE_URL='', RENDER='', ZGUA_PUBLIC_ACCOUNTS='0', ZGUA_ENV='development',
            ACCOUNT_MFA_KEY=Fernet.generate_key().decode())
        self.app.register_blueprint(accounts.blueprint)
        self.app.add_url_rule('/tactic', 'tactic', lambda: security.protect_tactic_request() or {'ok': True}, methods=['GET', 'POST'])
        self.app.add_url_rule('/demo', 'demo', lambda: security.protect_public_demo() or {'ok': True}, methods=['POST'])
        self.addCleanup(patch.stopall)
        patch.object(accounts, 'ITERATIONS', 1000).start()
        patch.object(accounts.account_mail, 'ready', return_value=True).start()
        self.sender = patch.object(accounts.account_mail, 'send').start()
        self.client = self.app.test_client()
        self.csrf = self.client.get('/api/account/session').get_json()['csrf']
        self.password = 'synthetic-test-password'
        self.secret = pyotp.random_base32()
        self.recovery = '1234567890abcdef'
        with self.app.app_context(), accounts.database() as db:
            salt = 'ab' * 32
            db.execute("INSERT INTO users(id,name,salt,hash,role,email_verified) VALUES (1,'tester',?,?,'admin',1)", (salt, accounts.password_hash(self.password, salt)))

    def call(self, path, body=None, method='POST', headers=None):
        response = self.client.open('/api/account/' + path, method=method, json=body or {},
            headers={'X-CSRF-Token': self.csrf, **(headers or {})})
        data = response.get_json(silent=True) or {}
        self.csrf = data.get('csrf', self.csrf)
        return response

    def login(self):
        self.assertEqual(self.call('login', {'username': 'tester', 'password': self.password}).status_code, 200)

    def enable_mfa(self):
        with self.app.app_context(), accounts.database() as db:
            encrypted = security.cipher().encrypt(self.secret.encode()).decode()
            db.execute('UPDATE users SET totp_secret=?,recovery=? WHERE id=1', (encrypted, json.dumps([accounts.digest(self.recovery)])))

    def concurrent_factor(self, code):
        self.enable_mfa()
        barrier = Barrier(2)
        def consume():
            with self.app.app_context(), closing(sqlite3.connect(self.path, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute('SELECT * FROM users WHERE id=1').fetchone()
                barrier.wait(timeout=5)
                result = security.verify_factor(db, row, code)
                db.commit()
                return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(consume) for _ in range(2)]
            return sorted(f.result(timeout=15) for f in futures)

    def test_recovery_consumption_atomic_across_stale_concurrent_reads(self):
        self.assertEqual(self.concurrent_factor(self.recovery), [False, True])

    def test_totp_consumption_atomic_across_stale_concurrent_reads(self):
        now = int(time.time() // 30) * 30
        with patch.object(security.time, 'time', return_value=now):
            self.assertEqual(self.concurrent_factor(pyotp.TOTP(self.secret).at(now)), [False, True])

    def test_rotated_secret_and_recovery_reject_stale_snapshot(self):
        self.enable_mfa()
        with self.app.app_context(), accounts.database() as db:
            row = db.execute('SELECT * FROM users WHERE id=1').fetchone()
            db.execute('UPDATE users SET totp_secret=?,recovery=? WHERE id=1', (security.cipher().encrypt(pyotp.random_base32().encode()).decode(), '[]'))
            self.assertFalse(security.verify_factor(db, row, self.recovery))
            self.assertFalse(security.verify_factor(db, row, pyotp.TOTP(self.secret).now()))

    def test_corrupt_factor_inputs_fail_closed(self):
        self.enable_mfa()
        with self.app.app_context(), accounts.database() as db:
            original = dict(db.execute('SELECT * FROM users WHERE id=1').fetchone())
            for value in ('{', '{}', '[null]', '[12]', '["\u00e9"]', 'null', '[' * 2000 + ']' * 2000,
                    json.dumps([accounts.digest(self.recovery)] * 2)):
                row = dict(original, recovery=value)
                self.assertFalse(security.verify_factor(db, row, self.recovery), value)
            for value in ('corrupt', '\u00e9', security.cipher().encrypt(b'not base32!').decode()):
                self.assertFalse(security.verify_factor(db, dict(original, totp_secret=value), self.recovery))
            for code in (None, [], {}, '\u00e9', 'x' * 81):
                self.assertFalse(security.verify_factor(db, original, code))

    def test_corrupt_password_storage_denies_login(self):
        for field, value in (('salt', 'bad'), ('hash', '\u00e9')):
            with self.app.app_context(), accounts.database() as db:
                salt = 'ab' * 32
                db.execute('UPDATE users SET salt=?,hash=? WHERE id=1', (salt, accounts.password_hash(self.password, salt)))
                db.execute('UPDATE users SET ' + field + '=? WHERE id=1', (value,))
            self.assertEqual(self.call('login', {'username': 'tester', 'password': self.password}).status_code, 401)

    def test_unicode_csrf_and_origin_are_denied_without_exception(self):
        self.assertEqual(self.call('logout', headers={'X-CSRF-Token': '\u00e9'}).status_code, 403)
        self.assertEqual(self.call('logout', headers={'Origin': ''}).status_code, 403)
        self.assertEqual(self.call('logout', headers={'Origin': 'null'}).status_code, 403)
        self.assertEqual(self.client.get('/api/account/session', base_url='http://evil.test', headers={'X-Forwarded-Host': 'localhost'}).status_code, 403)

    def test_exact_expiry_and_disabled_sessions_cannot_mutate(self):
        self.login()
        now = time.time()
        with self.app.app_context(), accounts.database() as db:
            db.execute('UPDATE sessions SET expires=? WHERE user_id=1', (now,))
        with patch.object(accounts.time, 'time', return_value=now):
            self.assertEqual(self.call('sessions/revoke').status_code, 401)
        self.client.get('/api/account/session')
        self.csrf = self.client.get('/api/account/session').get_json()['csrf']
        self.login()
        with self.app.app_context(), accounts.database() as db:
            db.execute('UPDATE users SET disabled=1 WHERE id=1')
        self.assertEqual(self.call('sessions/revoke').status_code, 401)
        self.assertIsNone(self.client.get('/api/account/session').get_json()['user'])

    def test_revocation_between_guard_and_login_transaction_denies_session(self):
        original = accounts.begin_account_write
        def revoke_before_write(db):
            db.execute('DELETE FROM sessions WHERE token=?', (g.account_session['token'],))
            # Commit the simulated concurrent revocation before starting the write.
            db.commit()
            original(db)
        with patch.object(accounts, 'begin_account_write', side_effect=revoke_before_write):
            self.assertEqual(self.call('login', {'username': 'tester', 'password': self.password}).status_code, 401)
        with self.app.app_context(), accounts.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=1').fetchone()[0], 0)

    def test_chunked_body_and_declared_length_limits(self):
        for path, limit in (('/api/account/login', accounts.MAX_PROFILE + 4096), ('/demo', 32768), ('/tactic', accounts.MAX_PROFILE + 4096)):
            builder = EnvironBuilder(path=path, method='POST', base_url='http://localhost')
            environ = builder.get_environ()
            environ.pop('CONTENT_LENGTH', None)
            environ.update({'wsgi.input': BytesIO(b'x' * (limit + 1)), 'wsgi.input_terminated': True, 'REMOTE_ADDR': '127.0.0.1'})
            self.assertEqual(self.client.open(environ).status_code, 413, path)
        self.assertEqual(self.call('login', {'padding': 'x' * (accounts.MAX_PROFILE + 4096)}).status_code, 413)

    def test_tactic_boundary_and_body_survive_repeated_guards(self):
        self.login()
        response = self.client.post('/tactic', json={'value': 1}, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/tactic', headers={'X-CSRF-Token': '\u00e9'}).status_code, 403)
        self.assertEqual(self.client.get('/tactic', environ_base={'REMOTE_ADDR': '192.0.2.1'}).status_code, 403)

    def public_config(self):
        self.app.config.update(DATABASE_URL='postgresql://unused.invalid/test', ZGUA_PUBLIC_ACCOUNTS='1', PUBLIC_APP_URL='https://charts.example')

    def test_public_preflight_rejects_invalid_configuration_without_database(self):
        self.public_config()
        with self.app.app_context():
            accounts.validate_public_account_configuration()
            for field, value in (('ACCOUNT_MFA_KEY', 'invalid'), ('ACCOUNT_MFA_KEY', ''), ('PUBLIC_APP_URL', 'https://'),
                    ('PUBLIC_APP_URL', 'http://charts.example'), ('PUBLIC_APP_URL', 'https://user@charts.example'),
                    ('PUBLIC_APP_URL', 'https://charts.example/path'), ('PUBLIC_APP_URL', 'https://charts.example?x=1'),
                    ('PUBLIC_APP_URL', 'https://charts.example:bad'), ('PUBLIC_APP_URL', 'https://[broken'),
                    ('PUBLIC_APP_URL', 'https://bad,host'),
                    ('PUBLIC_APP_URL', 'https://charts.example/#fragment'), ('PUBLIC_APP_URL', 'https://charts.example\n'),
                    ('DATABASE_URL', ''), ('ZGUA_PUBLIC_ACCOUNTS', '0')):
                original = self.app.config[field]
                self.app.config[field] = value
                # Keep production intent when testing a disabled opt-in flag.
                self.app.config['RENDER'] = 'true'
                with self.assertRaises(RuntimeError, msg=(field, value)):
                    accounts.validate_public_account_configuration()
                with patch.object(accounts, 'database', side_effect=AssertionError('must not connect')):
                    self.assertEqual(self.client.get('/api/account/session').status_code, 503)
                self.app.config[field] = original

    def test_public_registration_default_off_and_trusted_host(self):
        self.public_config()
        with patch.object(accounts, 'database', accounts.sqlite_database):
            client = self.app.test_client()
            response = client.get('/api/account/session', base_url='https://charts.example')
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.get_json()['registrationEnabled'])
            self.assertIn('Secure', response.headers['Set-Cookie'])
            csrf = response.get_json()['csrf']
            self.assertEqual(client.post('/api/account/register', base_url='https://charts.example', json={}, headers={'X-CSRF-Token': csrf}).status_code, 503)
            self.app.config['ZGUA_REGISTRATION_ENABLED'] = '1'
            self.assertTrue(client.get('/api/account/session', base_url='https://charts.example').get_json()['registrationEnabled'])
            self.assertEqual(client.get('/api/account/session', base_url='https://evil.example').status_code, 403)
        self.sender.assert_not_called()

    def test_explicit_production_mode_never_falls_back_to_local_accounts(self):
        self.app.config['ZGUA_ENV'] = 'production'
        with patch.object(accounts, 'database', side_effect=AssertionError('must not connect')):
            self.assertEqual(self.client.get('/api/account/session').status_code, 503)

    def test_public_origin_normalizes_case_and_default_https_port(self):
        self.public_config()
        self.app.config['PUBLIC_APP_URL'] = 'https://CHARTS.example:443/'
        with self.app.app_context():
            self.assertEqual(accounts.account_origin(), 'https://charts.example')
        with patch.object(accounts, 'database', accounts.sqlite_database):
            client = self.app.test_client()
            csrf = client.get('/api/account/session', base_url='https://charts.example').get_json()['csrf']
            self.assertEqual(client.post('/api/account/logout', base_url='https://charts.example', json={},
                headers={'X-CSRF-Token': csrf, 'Origin': 'https://charts.example'}).status_code, 200)
            for host in ('charts.example:bad', 'user@charts.example', ':443'):
                self.assertEqual(client.get('/api/account/session', base_url='https://charts.example', headers={'Host': host}).status_code, 403)

    def test_invalid_unicode_password_is_safe_bad_request(self):
        password = '\ud800' + 'x' * 15
        self.assertEqual(self.call('login', {'username': 'tester', 'password': password}).status_code, 400)
        self.assertEqual(self.call('register', {'username': 'new', 'password': password}).status_code, 400)
        self.assertEqual(self.call('reset-password', {'token': 'T' * 43, 'password': password, 'confirmPassword': password}).status_code, 400)
        self.login()
        self.assertEqual(self.call('password', {'currentPassword': self.password, 'password': password, 'confirmPassword': password}, 'PUT').status_code, 400)

    def test_deeply_nested_json_is_safe_bad_request(self):
        self.assertEqual(self.client.post('/api/account/login', data='[' * 2000 + ']' * 2000,
            content_type='application/json', headers={'X-CSRF-Token': self.csrf}).status_code, 400)

    def concurrent_token_requests(self, endpoint, body):
        clients = [self.app.test_client() for _ in range(2)]
        csrf = [client.get('/api/account/session').get_json()['csrf'] for client in clients]
        barrier = Barrier(2)
        def consume(index):
            barrier.wait(timeout=5)
            return clients[index].post('/api/account/' + endpoint, json=body, headers={'X-CSRF-Token': csrf[index]}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(consume, index) for index in range(2)]
            return sorted(f.result(timeout=15) for f in futures)

    def test_reset_token_atomic_single_use_and_session_revocation(self):
        self.login()
        raw = 'R' * 43
        with self.app.app_context(), accounts.database() as db:
            db.execute('INSERT INTO account_tokens VALUES (?,?,?,?,?)', (accounts.digest(raw), 1, 'reset', None, time.time() + 600))
        body = {'token': raw, 'password': 'new-synthetic-password', 'confirmPassword': 'new-synthetic-password'}
        self.assertEqual(self.concurrent_token_requests('reset-password', body), [200, 400])
        self.assertEqual(self.call('sessions/revoke').status_code, 401)
        with self.app.app_context(), accounts.database() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM account_tokens WHERE user_id=1').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions WHERE user_id=1').fetchone()[0], 0)
            self.assertTrue(accounts.password_matches(body['password'], db.execute('SELECT * FROM users WHERE id=1').fetchone()))

    def test_verification_token_atomic_single_use(self):
        raw = 'V' * 43
        with self.app.app_context(), accounts.database() as db:
            db.execute('UPDATE users SET email_verified=0 WHERE id=1')
            db.execute('INSERT INTO email_tokens VALUES (?,?,?)', (accounts.digest(raw), 1, time.time() + 600))
        self.assertEqual(self.concurrent_token_requests('verify', {'token': raw}), [200, 400])

    def test_expired_and_orphan_tokens_do_not_claim_success(self):
        raw = 'T' * 43
        body = {'token': raw, 'password': 'new-synthetic-password', 'confirmPassword': 'new-synthetic-password'}
        with self.app.app_context(), accounts.database() as db:
            db.execute('INSERT INTO account_tokens VALUES (?,?,?,?,?)', (accounts.digest(raw), 1, 'reset', None, time.time() - 1))
        self.assertEqual(self.call('reset-password', body).status_code, 400)
        with self.app.app_context(), accounts.database() as db:
            db.execute('UPDATE account_tokens SET user_id=999,expires=?', (time.time() + 600,))
            db.execute('INSERT INTO email_tokens VALUES (?,?,?)', (accounts.digest(raw), 999, time.time() + 600))
        self.assertEqual(self.call('reset-password', body).status_code, 400)
        self.assertEqual(self.call('verify', {'token': raw}).status_code, 400)

    def test_development_postgres_configuration_does_not_require_public_opt_in(self):
        self.app.config['DATABASE_URL'] = 'postgresql://unused.invalid/development'
        with self.app.app_context():
            self.assertFalse(accounts.public_accounts_enabled())
            accounts.validate_public_account_configuration()
            self.assertTrue(accounts.registration_enabled())
            self.assertIsNotNone(security.cipher())

    def test_corrupt_pending_secret_and_exact_expiry_reject_confirmation(self):
        self.login()
        with self.app.app_context(), accounts.database() as db:
            db.execute('UPDATE users SET totp_pending=?,totp_pending_until=? WHERE id=1', ('broken', time.time() + 600))
        self.assertEqual(self.call('mfa/confirm', {'twoFactorCode': '123456'}).status_code, 400)
        now = time.time()
        with self.app.app_context(), accounts.database() as db:
            db.execute('UPDATE users SET totp_pending=?,totp_pending_until=? WHERE id=1', (security.cipher().encrypt(self.secret.encode()).decode(), now))
        with patch.object(accounts.time, 'time', return_value=now):
            self.assertEqual(self.call('mfa/confirm', {'twoFactorCode': pyotp.TOTP(self.secret).at(now)}).status_code, 400)


class HostedThrottleTests(unittest.TestCase):
    def test_new_anonymous_sessions_cannot_grow_without_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Flask(__name__)
            app.config.update(ACCOUNT_DATABASE=str(Path(folder)/'accounts.sqlite'),DATABASE_URL='')
            with app.test_request_context('/api/account/session'), accounts.database() as db:
                db.executemany('INSERT INTO attempts VALUES (?,?)',[('anonymous:unknown',time.time())]*500)
                self.assertFalse(accounts.reserve_anonymous_session(db))

    def test_public_accounts_do_not_share_a_proxy_login_bucket(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Flask(__name__)
            app.config.update(ACCOUNT_DATABASE=str(Path(folder)/'accounts.sqlite'),DATABASE_URL='')
            with app.app_context(), accounts.database() as db:
                db.execute("INSERT INTO users(id,name,salt,hash,email) VALUES(1,'first','test','test','first@example.test')")
                db.execute("INSERT INTO users(id,name,salt,hash,email) VALUES(2,'second','test','test','second@example.test')")
            for identity in ('first','second'):
                with app.test_request_context('/api/account/login',method='POST',json={'username':identity},headers={'X-Forwarded-For':'spoofed'}), patch.object(accounts,'public_deployment',return_value=True):
                    g.account_user = None
                    with accounts.database() as db:
                        for _ in range(10):
                            self.assertTrue(accounts.limited(db))
                        self.assertFalse(accounts.limited(db))
            with app.test_request_context('/api/account/login',method='POST',json={'username':'FIRST@example.test'}), patch.object(accounts,'public_deployment',return_value=True):
                g.account_user = None
                with accounts.database() as db:
                    self.assertFalse(accounts.limited(db))


if __name__ == '__main__':
    unittest.main()
