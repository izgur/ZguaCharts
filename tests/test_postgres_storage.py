import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from psycopg2 import InterfaceError, OperationalError
from psycopg2.pool import PoolError, ThreadedConnectionPool

from services import storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.pool = MagicMock()
        self.pool.closed = False
        self.connection = self.pool.getconn.return_value
        self.connection.closed = 0
        self.original_pool = storage._pool
        self.pool_patch = patch.object(storage, "_pool", return_value=self.pool)
        self.pool_patch.start()
        self.addCleanup(self.pool_patch.stop)

    def test_bind_parameters_and_serialized_account_lock(self):
        connection = MagicMock()
        db = storage.PostgresConnection(connection)
        db.execute("SELECT * FROM users WHERE email=?", ("a@example.org",))
        connection.cursor.return_value.execute.assert_called_with("SELECT * FROM users WHERE email=%s", ("a@example.org",))
        db.execute("BEGIN IMMEDIATE")
        connection.cursor.return_value.execute.assert_called_with("SELECT pg_advisory_xact_lock(927401)")

    def test_connection_returned_on_failure_without_fallback(self):
        pool = MagicMock()
        connection = pool.getconn.return_value
        connection.closed = 0
        with patch.object(storage, "_pool", return_value=pool):
            with self.assertRaises(ValueError):
                with storage.postgres_database("test"):
                    raise ValueError("failure")
        connection.commit.assert_not_called()
        connection.rollback.assert_called()
        pool.putconn.assert_called_once_with(connection, close=False)

    def test_readiness_and_timeout_share_the_transaction(self):
        with storage.postgres_database("test") as db:
            self.assertIs(db.connection, self.connection)
            self.connection.commit.assert_not_called()
        cursor = self.connection.cursor.return_value.__enter__.return_value
        self.assertEqual([call[0][0] for call in cursor.execute.call_args_list],
                         ["SET LOCAL statement_timeout = 30000", "SELECT 1"])
        self.connection.cursor.return_value.__exit__.assert_called_once()
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()
        self.pool.putconn.assert_called_once_with(self.connection, close=False)

    def test_dead_connection_discarded_before_retry(self):
        dead = MagicMock(closed=1)
        self.pool.getconn.side_effect = [dead, self.connection]
        with storage.postgres_database("test"):
            pass
        dead.cursor.assert_not_called()
        dead.rollback.assert_not_called()
        self.assertEqual(self.pool.getconn.call_count, 2)
        self.pool.putconn.assert_any_call(dead, close=True)
        self.pool.putconn.assert_any_call(self.connection, close=False)

    def test_failed_ping_discarded_before_retry(self):
        dead = MagicMock(closed=0)
        dead.cursor.return_value.__enter__.return_value.execute.side_effect = [None, OperationalError("dead")]
        self.pool.getconn.side_effect = [dead, self.connection]
        with storage.postgres_database("test"):
            pass
        dead.rollback.assert_not_called()
        dead.commit.assert_not_called()
        dead.cursor.return_value.__exit__.assert_called_once()
        self.pool.putconn.assert_any_call(dead, close=True)
        self.assertEqual(self.pool.getconn.call_count, 2)

    def test_readiness_failures_are_bounded_without_yield(self):
        self.connection.cursor.return_value.__enter__.return_value.execute.side_effect = InterfaceError("dead")
        with self.assertRaises(storage.StorageUnavailable):
            with storage.postgres_database("test"):
                self.fail("Checkout must not yield an unready connection")
        self.assertEqual(self.pool.getconn.call_count, storage.READINESS_ATTEMPTS)
        self.assertEqual(self.pool.putconn.call_count, storage.READINESS_ATTEMPTS)
        self.connection.commit.assert_not_called()

    def test_pool_creation_failure_is_bounded(self):
        with patch.object(storage, "_pool", side_effect=OperationalError("offline")) as create:
            with self.assertRaises(storage.StorageUnavailable):
                with storage.postgres_database("test"):
                    self.fail("Unavailable pool must not yield")
        self.assertEqual(create.call_count, storage.READINESS_ATTEMPTS)

    def test_exhausted_pool_waits_then_succeeds(self):
        self.pool.getconn.side_effect = [PoolError("connection pool exhausted"), self.connection]
        with patch.object(storage.time, "monotonic", side_effect=[0, 0.1]), \
                patch.object(storage.time, "sleep") as sleep:
            with storage.postgres_database("test"):
                pass
        sleep.assert_called_once_with(storage.POOL_POLL_INTERVAL)
        self.assertEqual(self.pool.getconn.call_count, 2)

    def test_exhausted_pool_has_bounded_wait(self):
        self.pool.getconn.side_effect = PoolError("connection pool exhausted")
        with patch.object(storage.time, "monotonic", side_effect=[0, 0, storage.POOL_WAIT_TIMEOUT]), \
                patch.object(storage.time, "sleep") as sleep:
            with self.assertRaisesRegex(storage.StorageUnavailable, "timed out"):
                with storage.postgres_database("test"):
                    self.fail("Exhausted pool must not yield")
        sleep.assert_called_once_with(storage.POOL_POLL_INTERVAL)
        self.assertEqual(self.pool.getconn.call_count, 2)
        self.pool.putconn.assert_not_called()

    def test_closed_pool_fails_without_wait(self):
        self.pool.closed = True
        self.pool.getconn.side_effect = PoolError("connection pool is closed")
        with patch.object(storage.time, "sleep") as sleep:
            with self.assertRaises(storage.StorageUnavailable):
                with storage.postgres_database("test"):
                    self.fail("Closed pool must not yield")
        sleep.assert_not_called()
        self.pool.getconn.assert_called_once_with()

    def test_rollback_failure_does_not_mask_transaction_error(self):
        original = ValueError("write failed")
        self.connection.rollback.side_effect = OperationalError("rollback failed")
        with self.assertRaises(ValueError) as raised:
            with storage.postgres_database("test"):
                raise original
        self.assertIs(raised.exception, original)
        self.connection.rollback.assert_called_once_with()
        self.pool.putconn.assert_called_once_with(self.connection, close=True)
        self.pool.getconn.assert_called_once_with()

    def test_write_connection_failure_is_not_retried(self):
        original = OperationalError("write disconnected")
        self.connection.cursor.return_value.execute.side_effect = original
        with self.assertRaises(OperationalError) as raised:
            with storage.postgres_database("test") as db:
                db.execute("INSERT INTO users(name) VALUES (?)", ("alice",))
        self.assertIs(raised.exception, original)
        self.pool.getconn.assert_called_once_with()
        self.connection.cursor.return_value.execute.assert_called_once()
        self.connection.cursor.return_value.close.assert_called_once_with()
        self.connection.commit.assert_not_called()

    def test_commit_failure_is_not_retried_or_masked(self):
        original = OperationalError("commit outcome unknown")
        self.connection.commit.side_effect = original
        self.connection.rollback.side_effect = InterfaceError("closed")
        self.pool.putconn.side_effect = PoolError("closed pool")
        with self.assertRaises(OperationalError) as raised:
            with storage.postgres_database("test"):
                pass
        self.assertIs(raised.exception, original)
        self.connection.commit.assert_called_once_with()
        self.pool.getconn.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_putconn_failure_retries_discard_to_free_pool_slot(self):
        self.pool.putconn.side_effect = [OperationalError("reset failed"), None]
        with self.assertRaises(ValueError):
            with storage.postgres_database("test"):
                raise ValueError("original")
        self.assertEqual(self.pool.putconn.call_args_list[1][1], {"close": True})
        self.connection.close.assert_called_once_with()

    def test_closed_pool_during_return_reports_unavailable(self):
        self.pool.putconn.side_effect = PoolError("connection pool is closed")
        with self.assertRaises(storage.StorageUnavailable):
            with storage.postgres_database("test"):
                pass
        self.connection.commit.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_cleanup_failures_do_not_mask_body_error(self):
        original = ValueError("original")
        self.connection.rollback.side_effect = OperationalError("dead")
        self.pool.putconn.side_effect = PoolError("closed pool")
        self.connection.close.side_effect = InterfaceError("close failed")
        with self.assertRaises(ValueError) as raised:
            with storage.postgres_database("test"):
                raise original
        self.assertIs(raised.exception, original)
        self.pool.getconn.assert_called_once_with()

    def test_non_connection_setup_error_is_not_retried(self):
        original = ValueError("unexpected setup failure")
        self.connection.cursor.return_value.__enter__.return_value.execute.side_effect = original
        with self.assertRaises(ValueError) as raised:
            with storage.postgres_database("test"):
                self.fail("Setup failure must not yield")
        self.assertIs(raised.exception, original)
        self.pool.getconn.assert_called_once_with()
        self.pool.putconn.assert_called_once_with(self.connection, close=True)

    def test_cursors_closed_on_success_including_unfetched_results(self):
        cursors = [MagicMock(), MagicMock(), MagicMock()]
        self.connection.cursor.side_effect = [MagicMock()] + cursors
        with storage.postgres_database("test") as db:
            result = db.execute("SELECT 1")
            result.fetchone()
            result = db.execute("UPDATE users SET name=?", ("alice",))
            self.assertIs(result.rowcount, cursors[1].rowcount)
            db.execute("SELECT 2")
            for cursor in cursors:
                cursor.close.assert_not_called()
        for cursor in cursors:
            cursor.close.assert_called_once_with()

    def test_cursor_cleanup_failure_preserves_error_and_closes_all(self):
        cursors = [MagicMock(), MagicMock()]
        cursors[0].close.side_effect = InterfaceError("closed")
        self.connection.cursor.side_effect = [MagicMock()] + cursors
        with self.assertRaisesRegex(ValueError, "original"):
            with storage.postgres_database("test") as db:
                db.execute("SELECT 1")
                db.execute("SELECT 2")
                raise ValueError("original")
        for cursor in cursors:
            cursor.close.assert_called_once_with()
        self.pool.putconn.assert_called_once_with(self.connection, close=True)

    def test_process_control_exception_also_rolls_back_and_releases(self):
        with self.assertRaises(KeyboardInterrupt):
            with storage.postgres_database("test"):
                raise KeyboardInterrupt()
        self.connection.rollback.assert_called_once_with()
        self.pool.putconn.assert_called_once_with(self.connection, close=False)

    def test_real_pool_bookkeeping_recovers_after_dead_connection(self):
        # Exercise psycopg2's bookkeeping with fake sockets, never a database.
        dead = MagicMock(closed=0)
        dead.cursor.return_value.__enter__.return_value.execute.side_effect = OperationalError("dead")
        self.connection.info.transaction_status = 0
        with patch("psycopg2.connect", side_effect=[dead, self.connection]) as connect:
            pool = ThreadedConnectionPool(1, 1, "test")
            with patch.object(storage, "_pool", return_value=pool):
                with storage.postgres_database("test"):
                    pass
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(pool._used, {})
            self.assertEqual(pool._rused, {})
            self.assertEqual(pool._pool, [self.connection])
        dead.close.assert_called_once_with()

    def test_real_pool_rollback_failure_does_not_leak_slot(self):
        self.connection.info.transaction_status = 2
        self.connection.rollback.side_effect = OperationalError("rollback failed")
        with patch("psycopg2.connect", return_value=self.connection):
            pool = ThreadedConnectionPool(1, 1, "test")
            connection = pool.getconn()
            self.assertFalse(storage._release(pool, connection))
            self.assertEqual(pool._used, {})
            self.assertEqual(pool._rused, {})
            self.assertEqual(pool._pool, [])

    def test_real_exhausted_pool_returns_slot_while_waiting(self):
        self.connection.info.transaction_status = 0
        with patch("psycopg2.connect", return_value=self.connection):
            pool = ThreadedConnectionPool(1, 1, "test")
            borrowed = pool.getconn()
            with patch.object(storage, "_pool", return_value=pool), \
                    patch.object(storage.time, "sleep", side_effect=lambda delay: pool.putconn(borrowed)) as sleep:
                with storage.postgres_database("test"):
                    pass
            sleep.assert_called_once_with(storage.POOL_POLL_INTERVAL)
            self.assertEqual(pool._used, {})
            self.assertEqual(pool._rused, {})

    def test_pool_is_lazy_and_process_scoped(self):
        with patch.object(storage, "_pool", self.original_pool), \
                patch.object(storage, "_pools", {}), \
                patch("psycopg2.pool.ThreadedConnectionPool") as create, \
                patch.object(storage.os, "getpid", return_value=100) as pid:
            create.assert_not_called()
            first = storage._pool("test")
            self.assertIs(storage._pool("test"), first)
            self.assertEqual(create.call_count, 1)
            pid.return_value = 101
            storage._pool("test")
            self.assertEqual(create.call_count, 2)
            create.assert_called_with(1, 4, "test", connect_timeout=15,
                                      application_name="zguacharts")

    def test_import_does_not_create_pool_or_connect(self):
        with patch("psycopg2.pool.ThreadedConnectionPool") as create, \
                patch("psycopg2.connect") as connect:
            imported = runpy.run_path(storage.__file__)
        self.assertEqual(imported["_pools"], {})
        create.assert_not_called()
        connect.assert_not_called()

    def test_account_database_override_isolates_sqlite_from_environment(self):
        from flask import Flask
        app = Flask(__name__)
        with tempfile.TemporaryDirectory() as directory:
            app.config["ACCOUNT_DATABASE"] = str(Path(directory) / "accounts.sqlite")
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://unused.invalid/test"}), app.app_context():
                self.assertFalse(storage.postgres_enabled())
                with storage.database() as db:
                    self.assertEqual(db.execute("SELECT 1").fetchone()[0], 1)
                self.pool.getconn.assert_not_called()
                app.config["DATABASE_URL"] = "explicit-test-override"
                self.assertTrue(storage.postgres_enabled())

    def test_public_accounts_require_explicit_secure_configuration(self):
        from cryptography.fernet import Fernet
        from flask import Flask
        from services.chart_accounts import public_accounts_enabled
        app = Flask(__name__)
        app.config.update(DATABASE_URL="test", ZGUA_PUBLIC_ACCOUNTS="1", PUBLIC_APP_URL="https://example.org")
        with patch.dict(os.environ, {}, clear=True), app.app_context():
            with self.assertRaises(RuntimeError):
                public_accounts_enabled()
            app.config["ACCOUNT_MFA_KEY"] = Fernet.generate_key().decode("ascii")
            self.assertTrue(public_accounts_enabled())
            app.config["PUBLIC_APP_URL"] = "http://example.org"
            with self.assertRaises(RuntimeError):
                public_accounts_enabled()


if __name__ == '__main__':
    unittest.main()
