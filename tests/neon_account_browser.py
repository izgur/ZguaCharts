"""Live browser login/persistence against a disposable development Neon schema."""
import os
import logging
from pathlib import Path
import secrets
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    url = os.environ.get('ZGUA_TEST_POSTGRES_URL')
    if not url or os.environ.get('ZGUA_ENV') == 'production':
        raise RuntimeError('Explicit development test URL required; no production mode')
    import psycopg2
    from app import app
    from services import storage, chart_accounts, hidden_subprocess
    from werkzeug.serving import make_server
    schema = 'zgua_browser_' + uuid.uuid4().hex
    setup = psycopg2.connect(url,connect_timeout=15)
    setup.autocommit = True
    setup.cursor().execute('CREATE SCHEMA '+schema)
    password = secrets.token_urlsafe(24)

    class ScopedPool:
        def getconn(self):
            connection = psycopg2.connect(url,connect_timeout=15)
            connection.cursor().execute('SET LOCAL search_path TO '+schema)
            return connection

        def putconn(self,connection,close=False):
            connection.close()

    original = storage._pool
    server = None
    try:
        storage._pool = lambda url: ScopedPool()
        with storage.postgres_database(url) as db:
            for path in sorted((ROOT/'migrations').glob('*.sql')):
                db.execute(path.read_text(encoding='utf-8'))
            salt = secrets.token_hex(32)
            db.execute('INSERT INTO users(id,name,salt,hash,email,email_verified,role) VALUES (1,?,?,?,?,1,?)',
                       ('browsercheck',salt,chart_accounts.password_hash(password,salt),'browser@example.test','admin'))
        app.config['DATABASE_URL'] = url
        logging.getLogger('werkzeug').setLevel(logging.ERROR)
        server = make_server('127.0.0.1',5011,app,threaded=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        env = os.environ.copy()
        env['ZGUA_BROWSER_TEST_PASSWORD'] = password
        result = hidden_subprocess.run(['node',str(ROOT/'tests/neon_account_browser.js')],env=env,cwd=str(ROOT),timeout=180)
        if result.returncode:
            raise RuntimeError('Browser account checks failed')
        with storage.postgres_database(url) as db:
            rows = db.execute("SELECT body FROM user_documents WHERE user_id=1 AND kind='preferences'").fetchall()
            if not rows or not any('headerFontSize' in row['body'] and '14' in row['body'] for row in rows):
                raise RuntimeError('Browser preference was not persisted in PostgreSQL')
        print('Real browser login, admin menu, cross-context preferences and sign-out passed; original accounts untouched.')
    finally:
        if server:
            server.shutdown()
        storage._pool = original
        setup.cursor().execute('DROP SCHEMA '+schema+' CASCADE')
        setup.close()


if __name__ == '__main__':
    main()
