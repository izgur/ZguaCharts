"""Account permissions, audited administration and optional authenticator MFA."""
import hmac
import binascii
import json
from pathlib import Path
import secrets
import time
from collections import deque
from threading import Lock
from urllib.parse import urlsplit

from flask import current_app, g, jsonify, render_template, request
from services.chart_accounts import (blueprint, database, digest, limited, password_matches,
    COOKIE, account_origin, begin_account_write, csrf_matches,
    public_deployment, request_boundary)
from services.storage import postgres_enabled, setting

ROLES = ('member', 'tactics', 'admin')
_demo_requests = {}
_demo_lock = Lock()

def protect_public_demo():
    try:
        origin = account_origin()
    except RuntimeError:
        return jsonify(error='Public account configuration is unavailable.'),503
    if request.headers.get('Origin') not in (None, origin) or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return jsonify(error='Cross-origin demo request blocked.'),403
    blocked = request_boundary(32768)
    if blocked: return blocked
    body = request.get_json(silent=True)
    if not isinstance(body,dict) or body.get('strategy') != 'RelativeStrengthV2':
        return jsonify(error='Guest demo is limited to RelativeStrengthV2.'),403
    if body.get('params') not in (None,{}):
        return jsonify(error='Guest demo uses fixed RelativeStrengthV2 rules.'),403
    with _demo_lock:
        now = time.time()
        for address in list(_demo_requests):
            queue = _demo_requests[address]
            while queue and queue[0] < now-60: queue.popleft()
            if not queue: del _demo_requests[address]
        if len(_demo_requests) >= 2048: return jsonify(error='Demo capacity reached. Try later.'),429
        queue = _demo_requests.setdefault(request.remote_addr,deque())
        if len(queue)>=24: return jsonify(error='Too many demo backtests. Try again in a minute.'),429
        queue.append(now)

def request_role():
    if request_boundary():
        return None
    with database() as db:
        row = db.execute('SELECT role FROM users JOIN sessions ON sessions.user_id=users.id WHERE sessions.token=? AND sessions.expires>? AND users.disabled=0', (digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
    return row['role'] if row else None

def protect_tactic_request():
    blocked = request_boundary()
    if blocked: return blocked
    if request_role() not in ('tactics','admin'):
        return jsonify(error='Tactic access must be granted by an administrator.'), 403
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        with database() as db:
            session=db.execute('SELECT csrf FROM sessions WHERE token=? AND expires>?',(digest(request.cookies.get(COOKIE,'')),time.time())).fetchone()
        if not session or not csrf_matches(session['csrf'],request.headers.get('X-CSRF-Token','')):
            return jsonify(error='Invalid CSRF token.'),403

def cipher():
    from cryptography.fernet import Fernet
    key = setting('ACCOUNT_MFA_KEY')
    if not key:
        if public_deployment():
            raise RuntimeError('ACCOUNT_MFA_KEY must be configured before using PostgreSQL accounts')
        path = Path(current_app.config.get('ACCOUNT_DATABASE', Path(__file__).resolve().parents[1] / 'data/accounts.sqlite')).with_suffix('.mfa-key')
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            try:
                with path.open('xb') as stream:
                    stream.write(Fernet.generate_key())
            except FileExistsError:
                pass
        key = path.read_bytes()
    return Fernet(key.encode() if isinstance(key, str) else key)

def decrypt_secret(value):
    from cryptography.fernet import InvalidToken
    try:
        if not isinstance(value, str) or len(value) > 4096: return None
        secret = cipher().decrypt(value.encode('ascii')).decode('ascii')
        return secret if 16 <= len(secret) <= 128 else None
    except (InvalidToken, ValueError, TypeError, UnicodeError, RuntimeError, OSError):
        return None


def verify_factor(db, row, code):
    import pyotp
    if not row or row['disabled'] or not isinstance(code, str) or len(code) > 80 or not code.isascii():
        return False
    if not row['totp_secret']:
        return True
    secret = decrypt_secret(row['totp_secret'])
    if not secret or type(row['totp_last']) is not int:
        return False
    counter = int(time.time() // 30)
    totp = pyotp.TOTP(secret)
    for step in (counter - 1, counter, counter + 1):
        try:
            matches = hmac.compare_digest(totp.at(step * 30), code.strip())
        except (ValueError, TypeError, binascii.Error, OverflowError):
            return False
        if step > row['totp_last'] and matches:
            changed = db.execute('UPDATE users SET totp_last=? WHERE id=? AND totp_last<? AND totp_secret=? AND disabled=0', (step, row['id'], step, row['totp_secret']))
            return changed.rowcount == 1
    if not isinstance(row['recovery'], (str, type(None))) or len(row['recovery'] or '') > 1024:
        return False
    try:
        recovery = json.loads(row['recovery'] or '[]')
    except (ValueError, TypeError, RecursionError):
        return False
    if not isinstance(recovery, list) or len(recovery) > 10 or not all(isinstance(item, str) and len(item) == 64 and all(c in '0123456789abcdef' for c in item) for item in recovery):
        return False
    if len(set(recovery)) != len(recovery):
        return False
    hashed = digest(code.strip())
    found = next((item for item in recovery if hmac.compare_digest(item, hashed)), None)
    if found:
        recovery.remove(found)
        changed = db.execute('UPDATE users SET recovery=? WHERE id=? AND recovery=? AND totp_secret=? AND disabled=0', (json.dumps(recovery), row['id'], row['recovery'], row['totp_secret']))
        return changed.rowcount == 1
    return False

def authorized(role='member'):
    user = getattr(g, 'account_user', None)
    return bool(user and (role == 'member' or user['role'] == 'admin' or role == 'tactics' and user['role'] == 'tactics'))

def reauthenticate(db, body):
    if not limited(db):
        return False
    row = db.execute('SELECT * FROM users WHERE id=?', (g.account_user['id'],)).fetchone()
    password = body.get('currentPassword')
    return bool(row and not row['disabled'] and password_matches(password, row) and verify_factor(db, row, body.get('twoFactorCode', '')))

def schema(db):
    if postgres_enabled():
        return
    db.executescript('CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY, actor INTEGER, action TEXT NOT NULL, target INTEGER, created REAL NOT NULL); CREATE TABLE IF NOT EXISTS site_settings(name TEXT PRIMARY KEY, value TEXT NOT NULL);')

@blueprint.get('/admin')
def admin_page():
    if not authorized('admin'): return jsonify(error='Administrator access required.'), 403
    return render_template('admin.html')

@blueprint.get('/admin/users')
def list_users():
    if not authorized('admin'): return jsonify(error='Administrator access required.'), 403
    with database() as db:
        rows = db.execute('SELECT id,name,first_name,last_name,email,email_verified,role,disabled,totp_secret IS NOT NULL AS "twoFactorEnabled" FROM users ORDER BY id LIMIT 1000').fetchall()
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
        begin_account_write(db)
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
            for table in ('sessions','profiles','email_tokens','account_tokens','user_documents','user_document_versions'):
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
            begin_account_write(db)
            if not reauthenticate(db, body): return jsonify(error='Confirm administrator credentials.'), 403
            settings = body.get('settings')
            if not isinstance(settings,dict): return jsonify(error='Invalid donation settings.'), 400
            allowed = {'kofi': ('ko-fi.com',), 'stripe': ('buy.stripe.com',), 'paypal': ('paypal.me','www.paypal.com'), 'revolut': ('revolut.me','revolut.com','www.revolut.com')}
            cleaned = {}
            for field, hosts in allowed.items():
                value = settings.get(field,'')
                if not isinstance(value,str) or len(value)>1000: return jsonify(error='Invalid donation link.'),400
                try: parsed = urlsplit(value)
                except ValueError: return jsonify(error='Invalid donation link.'),400
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
            updated = db.execute('UPDATE site_settings SET value=? WHERE name=?', (json.dumps(cleaned), 'donations'))
            if updated.rowcount == 0:
                db.execute('INSERT INTO site_settings(name,value) VALUES (?,?)', ('donations', json.dumps(cleaned)))
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
        begin_account_write(db)
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
        begin_account_write(db)
        if not limited(db): return jsonify(error='Too many attempts.'), 429
        row = db.execute('SELECT * FROM users WHERE id=?', (g.account_user['id'],)).fetchone()
        code = body.get('twoFactorCode')
        until = row['totp_pending_until']
        if (row['totp_secret'] or not row['totp_pending'] or not isinstance(until, (int, float))
                or not until > time.time() or not isinstance(code,str) or len(code) != 6 or not code.isascii() or not code.isdigit()):
            return jsonify(error='Start setup again.'), 400
        secret = decrypt_secret(row['totp_pending'])
        try: valid = bool(secret and pyotp.TOTP(secret).verify(code))
        except (ValueError, TypeError, binascii.Error, OverflowError): valid = False
        if not valid: return jsonify(error='Incorrect authenticator code.'), 400
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
        begin_account_write(db)
        if not reauthenticate(db, body): return jsonify(error='Confirm your password and authenticator or recovery code.'), 403
        db.execute('UPDATE users SET totp_secret=NULL,totp_pending=NULL,totp_pending_until=NULL,totp_last=-1,recovery=NULL WHERE id=?', (g.account_user['id'],))
        db.execute('DELETE FROM sessions WHERE user_id=? AND token<>?', (g.account_user['id'],g.account_session['token']))
    return jsonify(message='Two-factor authentication disabled.')
