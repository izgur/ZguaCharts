"""One-shot, transactional local account import into an empty Neon database."""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)
from services.storage import postgres_database


def import_accounts(source, url, owner_email=None):
    local = sqlite3.connect("file:" + str(source.resolve()) + "?mode=ro", uri=True)
    local.row_factory = sqlite3.Row
    try:
        users = [dict(row) for row in local.execute("SELECT * FROM users")]
        if any(row.get("totp_secret") for row in users):
            from cryptography.fernet import Fernet
            key = os.environ.get("ACCOUNT_MFA_KEY")
            if not key:
                raise ValueError("Set ACCOUNT_MFA_KEY to the existing local MFA key before importing 2FA accounts")
            for row in users:
                if row.get("totp_secret"):
                    Fernet(key.encode()).decrypt(row["totp_secret"].encode())
        tables = {row[0] for row in local.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with postgres_database(url) as db:
            db.execute("SELECT pg_advisory_xact_lock(927401)")
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise ValueError("Target already contains accounts; refusing to merge or overwrite")
            columns = [row["column_name"] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='users' ORDER BY ordinal_position").fetchall()]
            for user in users:
                names = [name for name in columns if name in user]
                db.execute("INSERT INTO users(" + ",".join(names) + ") VALUES(" + ",".join("?" for _ in names) + ")", tuple(user[name] for name in names))
            db.execute("UPDATE account_sequence SET next_id=? WHERE singleton=1", (max((user["id"] for user in users),default=0)+1,))
            if 'account_sequence' in tables:
                old_next = local.execute("SELECT next_id FROM account_sequence WHERE singleton=1").fetchone()
                if old_next:
                    db.execute("UPDATE account_sequence SET next_id=GREATEST(next_id,?) WHERE singleton=1", (old_next[0],))
            if 'profiles' in tables:
                from services.user_profiles import save_snapshot
                for row in local.execute("SELECT user_id,body FROM profiles"):
                    save_snapshot(db, row["user_id"], json.loads(row["body"]), 0)
            for table, names in (('site_settings',['name','value']),('admin_audit',['actor','action','target','created'])):
                if table in tables:
                    for row in local.execute("SELECT * FROM " + table):
                        db.execute("INSERT INTO " + table + "(" + ",".join(names) + ") VALUES(" + ",".join('?' for _ in names) + ")", tuple(row[name] for name in names))
            if owner_email:
                owner = db.execute("SELECT id FROM users WHERE lower(email)=lower(?) AND email_verified=1", (owner_email,)).fetchone()
                if not owner:
                    raise ValueError("Explicit legacy data owner must be an imported verified account")
                alert_path = ROOT / 'data/chart-alerts.json'
                if alert_path.exists():
                    store = json.loads(alert_path.read_text(encoding='utf-8'))
                    for alert in store.get('alerts',[]):
                        alert['userId'] = owner['id']
                        db.execute("INSERT INTO alert_records(id,user_id,body,revision,updated) VALUES (?,?,?,?,?)", (alert['id'],owner['id'],json.dumps(alert),alert.get('revision',0),time.time()))
                    for event in store.get('events',[]):
                        db.execute("INSERT INTO alert_events(id,user_id,alert_id,body,created) VALUES (?,?,?,?,?)", (event['id'],owner['id'],event['alertId'],json.dumps(event),event['time']))
                history_path = ROOT / 'data/backtest-history.json'
                if history_path.exists():
                    for run in json.loads(history_path.read_text(encoding='utf-8'))[-500:]:
                        db.execute("INSERT INTO backtest_runs(id,user_id,body,created) VALUES (?,?,?,?)", (run['id'],owner['id'],json.dumps(run),time.time()))
        return len(users)
    finally:
        local.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT/'data/accounts.sqlite')
    parser.add_argument('--legacy-owner-email', help='Explicitly assign shared local alerts/backtests to this verified account')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print('Dry run: no data changed. Import excludes sessions and reset/verification tokens. Run --apply only after backing up local data and applying schema migrations.')
    else:
        url = os.environ.get('DATABASE_URL_UNPOOLED')
        if not url: parser.error('DATABASE_URL_UNPOOLED is required')
        count = import_accounts(args.source,url,args.legacy_owner_email)
        print('Imported {} accounts. Existing passwords preserved; sign in again.'.format(count))
