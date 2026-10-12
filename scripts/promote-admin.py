"""Local, explicit one-account bootstrap. Never promotes the first registrant."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask
from services.chart_accounts import database

parser=argparse.ArgumentParser()
parser.add_argument('account',help='Exact username or confirmed email')
args=parser.parse_args()
app=Flask(__name__)
with app.app_context(), database() as db:
    rows=db.execute('SELECT id,name,email,email_verified FROM users WHERE name=? COLLATE NOCASE OR email=? COLLATE NOCASE',(args.account,args.account)).fetchall()
    if len(rows)!=1 or not rows[0]['email_verified']:
        raise SystemExit('Exactly one verified existing account is required; nothing changed.')
    row=rows[0]
    db.execute("UPDATE users SET role='admin',disabled=0 WHERE id=?",(row['id'],))
    print('Administrator assigned: '+row['name']+' ('+(row['email'] or '')+')')
