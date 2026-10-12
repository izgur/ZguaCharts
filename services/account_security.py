"""Account permissions, audited administration and optional authenticator MFA."""
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from flask import current_app, g, jsonify, render_template, request
from services.chart_accounts import blueprint, database, digest, limited, password_hash, COOKIE

ROLES = ('member', 'tactics', 'admin')

def request_role():
    with database() as db:
        row = db.execute('SELECT role FROM users JOIN sessions ON sessions.user_id=users.id WHERE sessions.token=? AND sessions.expires>? AND users.disabled=0', (digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
    return row['role'] if row else None

def protect_tactic_request():
    if request_role() not in ('tactics','admin'):
        return jsonify(error='Tactic access must be granted by an administrator.'), 403
    if request.method != 'GET':
        if request.headers.get('Origin') not in (None,request.host_url.rstrip('/')) or request.headers.get('Sec-Fetch-Site')=='cross-site':
            return jsonify(error='Cross-origin request blocked.'),403
        with database() as db:
            session=db.execute('SELECT csrf FROM sessions WHERE token=? AND expires>?',(digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
        if not session or not hmac.compare_digest(session['csrf'],request.headers.get('X-CSRF-Token','')):
            return jsonify(error='Invalid CSRF token.'),403

def cipher():
    from cryptography.fernet import Fernet
    key = current_app.config.get('ACCOUNT_MFA_KEY') or os.environ.get('ACCOUNT_MFA_KEY')
    if not key:
        path = Path(current_app.config.get('ACCOUNT_DATABASE', Path(__file__).resolve().parents[1] / 'data/accounts.sqlite')).with_suffix('.mfa-key')
        if not path.exists():
            try:
                with path.open('xb') as stream:
                    stream.write(Fernet.generate_key())
            except FileExistsError:
                pass
        key = path.read_bytes()
    return Fernet(key.encode() if isinstance(key, str) else key)

def verify_factor(db, row, code):
    import pyotp
    if not isinstance(code, str) or len(code) > 80:
        return False
    if not row['totp_secret']:
        return True
    secret = cipher().decrypt(row['totp_secret'].encode()).decode()
    counter = int(time.time() // 30)
    totp = pyotp.TOTP(secret)
    for step in (counter - 1, counter, counter + 1):
        if step > row['totp_last'] and hmac.compare_digest(totp.at(step * 30), code.strip()):
            changed = db.execute('UPDATE users SET totp_last=? WHERE id=? AND totp_last<?', (step, row['id'], step))
            return changed.rowcount == 1
    recovery = json.loads(row['recovery'] or '[]')
    hashed = digest(code.strip())
    found = next((item for item in recovery if hmac.compare_digest(item, hashed)), None)
    if found:
        recovery.remove(found)
        db.execute('UPDATE users SET recovery=? WHERE id=?', (json.dumps(recovery), row['id']))
        return True
    return False

def authorized(role='member'):
    user = getattr(g, 'account_user', None)
    return bool(user and (role == 'member' or user['role'] == 'admin' or role == 'tactics' and user['role'] == 'tactics'))

def reauthenticate(db, body):
    if not limited(db):
        return False
    row = db.execute('SELECT * FROM users WHERE id=?', (g.account_user['id'],)).fetchone()
    password = body.get('currentPassword')
    return bool(isinstance(password, str) and len(password) <= 128 and hmac.compare_digest(password_hash(password, row['salt']), row['hash']) and verify_factor(db, row, body.get('twoFactorCode', '')))

def schema(db):
    db.executescript('CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY, actor INTEGER, action TEXT NOT NULL, target INTEGER, created REAL NOT NULL); CREATE TABLE IF NOT EXISTS site_settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);')

@blueprint.get('/admin')
def admin_page():
    if not authorized('admin'): return jsonify(error='Administrator access required.'), 403
    return render_template('admin.html')

@blueprint.get('/admin/users')
def list_users():
    if not authorized('admin'): return jsonify(error='Administrator access required.'), 403
    with database() as db:
        rows = db.execute('SELECT id,name,first_name,last_name,email,email_verified,role,disabled,totp_secret IS NOT NULL AS twoFactorEnabled FROM users ORDER BY id LIMIT 1000').fetchall()
        schema(db)
        audit = [dict(row) for row in db.execute('SELECT * FROM admin_audit ORDER BY id DESC LIMIT 50')]
    return jsonify(users=[dict(row) for row in rows], audit=audit)

@blueprint.route('/admin/users/<int:user_id>', methods=['PUT','DELETE'])
def manage_user(user_id):
    if not authorized('admin'): return jsonify(error='Administrator access required.'), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict): return jsonify(error='Invalid request.'), 400
    with database() as db:
        schema(db)
        db.execute('BEGIN IMMEDIATE')
        if not reauthenticate(db, body): return jsonify(error='Confirm your password and, if enabled, authenticator code.'), 403
        row = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not row: return jsonify(error='User not found.'), 404
        role, disabled = body.get('role', row['role']), body.get('disabled', bool(row['disabled']))
        if role not in ROLES or not isinstance(disabled, bool): return jsonify(error='Invalid role or account status.'), 400
        removing = request.method == 'DELETE'
        if user_id == g.account_user['id'] and (removing or disabled or role != 'admin'):
            return jsonify(error='You cannot delete, suspend or demote your own administrator account.'), 409
        if row['role'] == 'admin' and not row['disabled'] and (removing or disabled or role != 'admin') and db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0").fetchone()[0] <= 1:
            return jsonify(error='Keep at least one active administrator.'), 409
        if removing:
            for table in ('sessions','profiles','email_tokens','account_tokens'):
                db.execute('DELETE FROM ' + table + ' WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM users WHERE id=?', (user_id,))
        else:
            db.execute('UPDATE users SET role=?,disabled=? WHERE id=?', (role, int(disabled), user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
        db.execute('INSERT INTO admin_audit(actor,action,target,created) VALUES (?,?,?,?)', (g.account_user['id'], 'delete' if removing else 'role:' + role + ';disabled:' + str(disabled), user_id, time.time()))
    return jsonify(saved=True)

@blueprint.route('/donation', methods=['GET','PUT'])
def donation():
    with database() as db:
        schema(db)
        if request.method == 'PUT':
            if not authorized('admin'): return jsonify(error='Administrator access required.'), 403
            body = request.get_json(silent=True)
            if not isinstance(body, dict): return jsonify(error='Invalid settings.'), 400
            db.execute('BEGIN IMMEDIATE')
            if not reauthenticate(db, body): return jsonify(error='Confirm administrator credentials.'), 403
            settings = body.get('settings')
            if not isinstance(settings,dict): return jsonify(error='Invalid donation settings.'), 400
            allowed = {'kofi': ('ko-fi.com',), 'stripe': ('buy.stripe.com',), 'paypal': ('paypal.me','www.paypal.com'), 'revolut': ('revolut.me','revolut.com','www.revolut.com')}
            cleaned = {}
            for field, hosts in allowed.items():
                value = settings.get(field,'')
                if not isinstance(value,str) or len(value)>1000: return jsonify(error='Invalid donation link.'),400
                parsed = urlsplit(value)
                if value and (parsed.scheme!='https' or parsed.hostname not in hosts or parsed.username or parsed.password): return jsonify(error='Use the official HTTPS payment link for '+field+'.'),400
                cleaned[field] = value
            for field in ('beneficiary','iban','bic','bankName','reference'):
                value = settings.get(field,'')
                if not isinstance(value,str) or len(value)>120 or any(ord(c)<32 for c in value): return jsonify(error='Invalid bank-transfer details.'),400
                cleaned[field] = value.strip()
            iban = cleaned['iban'].replace(' ','').upper()
            if iban:
                import re
                if not re.fullmatch('[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}',iban): return jsonify(error='Invalid IBAN.'),400
                rearranged = iban[4:]+iban[:4]
                digits = ''.join(str(ord(c)-55) if c.isalpha() else c for c in rearranged)
                if int(digits)%97!=1 or not cleaned['beneficiary']: return jsonify(error='Invalid IBAN checksum or missing beneficiary.'),400
            cleaned['iban'] = iban
            db.execute('INSERT OR REPLACE INTO site_settings VALUES (?,?)', ('donations', json.dumps(cleaned)))
            db.execute('INSERT INTO admin_audit(actor,action,created) VALUES (?,?,?)', (g.account_user['id'],'donation:update',time.time()))
        row = db.execute("SELECT value FROM site_settings WHERE name='donations'").fetchone()
    return jsonify(settings=json.loads(row['value']) if row else {})

@blueprint.route('/mfa/setup', methods=['POST'])
def mfa_setup():
    import pyotp
    if not authorized(): return jsonify(error='Sign in first.'), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict): return jsonify(error='Invalid request.'), 400
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not reauthenticate(db, body): return jsonify(error='Confirm your current password and authenticator code if enabled.'), 403
        row = db.execute('SELECT * FROM users WHERE id=?', (g.account_user['id'],)).fetchone()
        if row['totp_secret']: return jsonify(error='Two-factor authentication is already enabled.'), 409
        secret = pyotp.random_base32()
        db.execute('UPDATE users SET totp_pending=?,totp_pending_until=? WHERE id=?', (cipher().encrypt(secret.encode()).decode(),time.time()+600,row['id']))
    return jsonify(secret=secret, uri=pyotp.TOTP(secret).provisioning_uri(name=g.account_user['name'],issuer_name='ZguaCharts'))

@blueprint.route('/mfa/confirm', methods=['POST'])
def mfa_confirm():
    import pyotp
    if not authorized(): return jsonify(error='Sign in first.'), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict): return jsonify(error='Invalid request.'), 400
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not limited(db): return jsonify(error='Too many attempts.'), 429
        row = db.execute('SELECT * FROM users WHERE id=?', (g.account_user['id'],)).fetchone()
        code = body.get('twoFactorCode')
        if not row['totp_pending'] or (row['totp_pending_until'] or 0) < time.time() or not isinstance(code,str): return jsonify(error='Start setup again.'), 400
        secret = cipher().decrypt(row['totp_pending'].encode()).decode()
        if not pyotp.TOTP(secret).verify(code): return jsonify(error='Incorrect authenticator code.'), 400
        codes = [secrets.token_hex(8) for _ in range(10)]
        db.execute('UPDATE users SET totp_secret=totp_pending,totp_pending=NULL,totp_pending_until=NULL,totp_last=?,recovery=? WHERE id=?', (int(time.time()//30),json.dumps([digest(code) for code in codes]),row['id']))
        db.execute('DELETE FROM sessions WHERE user_id=? AND token<>?', (row['id'],g.account_session['token']))
    return jsonify(recoveryCodes=codes)

@blueprint.route('/mfa/disable', methods=['POST'])
def mfa_disable():
    if not authorized(): return jsonify(error='Sign in first.'), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict): return jsonify(error='Invalid request.'), 400
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if not reauthenticate(db, body): return jsonify(error='Confirm your password and authenticator or recovery code.'), 403
        db.execute('UPDATE users SET totp_secret=NULL,totp_pending=NULL,totp_pending_until=NULL,totp_last=-1,recovery=NULL WHERE id=?', (g.account_user['id'],))
        db.execute('DELETE FROM sessions WHERE user_id=? AND token<>?', (g.account_user['id'],g.account_session['token']))
    return jsonify(message='Two-factor authentication disabled.')
