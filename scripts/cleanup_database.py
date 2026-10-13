"""Explicit retention cleanup for deployment's own database; never paper files."""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
if not os.environ.get('RENDER') and os.environ.get('ZGUA_ENV') != 'production':
    load_dotenv(ROOT / '.env',override=False)
from services.storage import postgres_database, setting

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    if not args.apply:
        print('Dry run: expires sessions/tokens, 15-minute auth attempts, 30-day alert events, 90-day completed mail receipts, and source versions beyond the latest 20 per script. Nothing changed.')
    else:
        if not setting('DATABASE_URL'):
            parser.error('DATABASE_URL required')
        now = time.time()
        with postgres_database() as db:
            for table in ('sessions','email_tokens','account_tokens'):
                db.execute('DELETE FROM '+table+' WHERE expires<?',(now,))
            db.execute('DELETE FROM attempts WHERE created<?',(now-900,))
            db.execute('DELETE FROM alert_events WHERE created<?',(now-30*86400,))
            db.execute("DELETE FROM mail_deliveries WHERE status='accepted' AND created<?",(now-90*86400,))
            db.execute('DELETE FROM user_document_versions WHERE (user_id,kind,document_key,revision) IN '
                       '(SELECT user_id,kind,document_key,revision FROM '
                       '(SELECT user_id,kind,document_key,revision,row_number() OVER '
                       '(PARTITION BY user_id,kind,document_key ORDER BY revision DESC) AS rank FROM user_document_versions) AS old WHERE rank>20)')
        print('Retention cleanup completed. No research or paper files changed.')
