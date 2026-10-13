"""Bounded PostgreSQL connections; SQLite remains an explicit local fallback."""
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, has_app_context

ROOT = Path(__file__).resolve().parents[1]
_pools = {}
_lock = threading.Lock()
POOL_WAIT_TIMEOUT = 5.0
POOL_POLL_INTERVAL = 0.05
READINESS_ATTEMPTS = 2


class StorageUnavailable(RuntimeError):
    """Storage checkout failed; HTTP callers should return a generic 503.

    Transaction errors retain their original type and must never be retried.
    """


def setting(name, default=None):
    if has_app_context() and name in current_app.config:
        return current_app.config[name]
    if name == "DATABASE_URL" and has_app_context() and current_app.config.get("ACCOUNT_DATABASE"):
        # Isolated SQLite test fixtures must never inherit a developer's Neon URL.
        return None
    return os.environ.get(name, default)


def postgres_enabled():
    return bool(setting("DATABASE_URL"))


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection
        self._cursors = []

    def execute(self, sql, params=()):
        from psycopg2.extras import DictCursor
        cursor = self.connection.cursor(cursor_factory=DictCursor)
        self._cursors.append(cursor)
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            # Existing account operations rely on SQLite's serialized writer.
            cursor.execute("SELECT pg_advisory_xact_lock(927401)")
        else:
            cursor.execute(sql.replace("?", "%s"), params)
        return cursor

    def rollback(self):
        self.connection.rollback()

    def close_cursor(self, cursor):
        cursor.close()
        self._cursors = [existing for existing in self._cursors if existing is not cursor]

    def close(self):
        failed = False
        for cursor in self._cursors:
            try:
                cursor.close()
            except Exception:
                failed = True
        self._cursors.clear()
        return not failed


def _pool(url):
    from psycopg2.pool import ThreadedConnectionPool
    # Construct only on checkout in the worker, never during module import.
    key = (os.getpid(), url)
    with _lock:
        if key not in _pools:
            _pools[key] = ThreadedConnectionPool(1, 4, url, connect_timeout=15,
                application_name="zguacharts")
        return _pools[key]


def _release(pool, connection, discard=False):
    """Return or discard without allowing cleanup to replace a primary error."""
    try:
        pool.putconn(connection, close=discard or bool(connection.closed))
        return True
    except Exception:
        # A rollback inside psycopg2.putconn can fail before bookkeeping is freed.
        try:
            pool.putconn(connection, close=True)
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass
        return False


def _checkout(url):
    from psycopg2 import InterfaceError, OperationalError
    from psycopg2.pool import PoolError
    deadline = time.monotonic() + POOL_WAIT_TIMEOUT
    failures = 0
    while True:
        pool = None
        connection = None
        try:
            pool = _pool(url)
            connection = pool.getconn()
            if connection.closed:
                raise InterfaceError("Closed storage connection")
            with connection.cursor() as cursor:
                # Neon transaction pooling requires a transaction-local timeout.
                cursor.execute("SET LOCAL statement_timeout = 30000")
                cursor.execute("SELECT 1")
            return pool, connection
        except PoolError as error:
            if connection is not None:
                _release(pool, connection, discard=True)
                raise StorageUnavailable("Storage connection pool is unavailable") from error
            if pool is None or pool.closed:
                raise StorageUnavailable("Storage connection pool is unavailable") from error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StorageUnavailable("Storage connection checkout timed out") from error
            time.sleep(min(POOL_POLL_INTERVAL, remaining))
        except (InterfaceError, OperationalError) as error:
            if connection is not None:
                _release(pool, connection, discard=True)
            failures += 1
            if failures >= READINESS_ATTEMPTS or time.monotonic() >= deadline:
                raise StorageUnavailable("Storage connection is unavailable") from error
        except BaseException:
            if connection is not None:
                _release(pool, connection, discard=True)
            raise


@contextmanager
def postgres_database(url=None):
    pool, connection = _checkout(url or setting("DATABASE_URL"))
    db = PostgresConnection(connection)
    discard = False
    failed = False
    try:
        yield db
        connection.commit()
    except BaseException:
        failed = True
        try:
            connection.rollback()
        except Exception:
            discard = True
        raise
    finally:
        discard = not db.close() or discard
        released = _release(pool, connection, discard=discard)
        if not failed and (not released or discard):
            raise StorageUnavailable("Storage connection cleanup failed")


@contextmanager
def database():
    if postgres_enabled():
        with postgres_database() as db:
            yield db
    else:
        from services.chart_accounts import sqlite_database
        with sqlite_database() as db:
            yield db


def migrate(url=None):
    """Run explicit, transactional versioned migrations, never on each request."""
    with postgres_database(url) as db:
        db.execute("SELECT pg_advisory_xact_lock(927402)")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            if db.execute("SELECT version FROM schema_migrations WHERE version=?", (path.name,)).fetchone():
                continue
            db.execute(path.read_text(encoding="utf-8"))
            db.execute("INSERT INTO schema_migrations(version) VALUES (?)", (path.name,))
