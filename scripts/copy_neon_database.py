"""Transfer current user data to an empty production DB, never session tokens."""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import dotenv_values
from services.storage import postgres_database

TABLES = {
    'users': 'id,name,salt,hash,first_name,last_name,email,email_verified,avatar,role,disabled,totp_secret,totp_pending,totp_pending_until,totp_last,recovery',
    'account_sequence': 'singleton,next_id',
    'profiles': 'user_id,body,revision',
    'user_documents': 'user_id,kind,document_key,body,revision,updated',
    'user_document_versions': 'user_id,kind,document_key,revision,body,created',
    'site_settings': 'name,value',
    'admin_audit': 'id,actor,action,target,created',
    'alert_records': 'id,user_id,body,revision,updated',
    'alert_events': 'id,user_id,alert_id,body,created',
    'backtest_runs': 'id,user_id,body,created',
    'mail_usage': 'period,attempts',
    'mail_deliveries': 'id,recipient,subject,status,provider_id,error,attempts,created,updated',
}


def identity(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('postgresql','postgres') or not parsed.hostname:
        raise ValueError('A PostgreSQL direct URL is required')
    return (parsed.hostname.replace('-pooler',''),parsed.path)


def transfer(source_url,target_url,mfa_key,enable_alerts=False):
    if identity(source_url) == identity(target_url):
        raise ValueError('Source and destination must be different databases')
    counts = {}
    with postgres_database(source_url) as source, postgres_database(target_url) as target:
        source.rollback()
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        source.execute('SET LOCAL statement_timeout = 30000')
        target.execute('SELECT pg_advisory_xact_lock(927401)')
        for table in TABLES:
            if table != 'account_sequence' and target.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:
                raise ValueError('Target is not empty; refusing to merge or overwrite data')
        source_versions = [row[0] for row in source.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()]
        target_versions = [row[0] for row in target.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()]
        if source_versions != target_versions:
            raise ValueError('Source and target schema versions must match')
        for table, columns in TABLES.items():
            names = columns.split(',')
            from psycopg2.extras import DictCursor
            rows = source.connection.cursor(name='copy_'+table,cursor_factory=DictCursor)
            rows.execute('SELECT '+columns+' FROM '+table)
            if table == 'account_sequence':
                target.execute('DELETE FROM account_sequence')
            count = 0
            while True:
                batch = rows.fetchmany(10)
                if not batch:
                    break
                for row in batch:
                    if table == 'site_settings' and row['name'] == 'deployment_environment':
                        continue
                    if table == 'users':
                        from cryptography.fernet import Fernet
                        for field in ('totp_secret','totp_pending'):
                            if row[field]:
                                if not mfa_key:
                                    raise ValueError('Preserve the MFA encryption key before copying accounts')
                                Fernet(mfa_key.encode()).decrypt(row[field].encode())
                    if not enable_alerts and table in ('alert_records','alert_events'):
                        payload = json.loads(row['body'])
                        if table == 'alert_records':
                            payload['migrationSourceEnabled'] = payload.get('enabled',False)
                            payload['enabled'] = False
                        elif payload.get('emailStatus') == 'pending':
                            payload['emailStatus'] = 'needs-review'
                        row['body'] = json.dumps(payload)
                    inserted = target.execute('INSERT INTO '+table+'('+columns+') VALUES ('+','.join('?' for _ in names)+')',tuple(row[name] for name in names))
                    target.close_cursor(inserted)
                    count += 1
            counts[table] = count
            rows.close()
        target.execute("SELECT setval(pg_get_serial_sequence('admin_audit','id'),COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM admin_audit")
        target.execute('INSERT INTO site_settings(name,value) VALUES (?,?)',('deployment_environment',json.dumps({'environment':'production','projectId':'cool-tree-27722416'})))
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-env',type=Path,default=ROOT/'.env')
    parser.add_argument('--target-env',type=Path,default=ROOT/'.env.production')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--enable-alerts',action='store_true',help='Explicitly activate copied alerts only after retiring the development notifications')
    parser.add_argument('--confirm-project',help='Must equal cool-tree-27722416 for the planned production transfer')
    args = parser.parse_args()
    if not args.apply:
        print('Dry run: transfers current account data, not SQLite snapshots. No data changed. Sessions/reset tokens are excluded. Target must be empty and schema-migrated.')
    else:
        if args.confirm_project != 'cool-tree-27722416':
            parser.error('Explicit production project confirmation is required')
        src,dst = dotenv_values(args.source_env),dotenv_values(args.target_env)
        if dst.get('ZGUA_NEON_PROJECT_ID') != args.confirm_project:
            parser.error('Target project metadata does not match the confirmed project')
        if not src.get('DATABASE_URL_UNPOOLED') or not dst.get('DATABASE_URL_UNPOOLED'):
            parser.error('Configure direct URLs privately in both environment files')
        try:
            counts = transfer(src['DATABASE_URL_UNPOOLED'],dst['DATABASE_URL_UNPOOLED'],dst.get('ACCOUNT_MFA_KEY'),args.enable_alerts)
        except Exception:
            print('Transfer did not finish cleanly. Check destination state before retrying; no merge or overwrite is attempted. Review schema, connectivity and MFA key.',file=sys.stderr)
            raise SystemExit(1)
        print('Transfer completed. Account count: '+str(counts['users'])+'. Sign in again. No sessions or reset tokens were copied.')
        if not args.enable_alerts:
            print('Copied alerts are paused; pending email events require review before production activation.')
