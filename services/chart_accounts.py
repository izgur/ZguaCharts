"""Local chart accounts. Research workers and trading configuration remain shared."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import hmac
import ipaddress
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, has_request_context, jsonify, request
from services import account_mail
from services import mail_templates
from services.account_avatar import clean_avatar
from services.deployment import production_mode
from services.storage import database, postgres_enabled, setting

try:
    from psycopg2 import IntegrityError as PostgresIntegrityError
except ImportError:
    PostgresIntegrityError = sqlite3.IntegrityError
INTEGRITY_ERRORS = (sqlite3.IntegrityError, PostgresIntegrityError)

blueprint = Blueprint("chart_accounts", __name__, url_prefix="/api/account")
COOKIE = "zgua_session"
ITERATIONS = 600000
MAX_PROFILE = 2000000


def public_deployment():
    return (bool(setting("RENDER")) or setting("ZGUA_PUBLIC_ACCOUNTS", "0") == "1"
            or production_mode({"ZGUA_ENV": setting("ZGUA_ENV", "")}))


def registration_enabled():
    return (not public_deployment() or setting("ZGUA_REGISTRATION_ENABLED", "0") == "1") and account_mail.ready()


def validate_public_account_configuration():
    """Startup preflight for main; never connects to or changes a database."""
    if not public_deployment():
        return
    from cryptography.fernet import Fernet
    try:
        url = setting("PUBLIC_APP_URL", "")
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment or "\\" in url or parsed.port == 0):
            raise ValueError
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if len(parsed.hostname) > 253 or not all(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label) for label in parsed.hostname.split(".")):
                raise ValueError
        key = setting("ACCOUNT_MFA_KEY")
        if not isinstance(key, (str, bytes)) or not re.fullmatch(rb"[A-Za-z0-9_-]{43}=", key.encode("ascii") if isinstance(key, str) else key):
            raise ValueError
        Fernet(key.encode("ascii") if isinstance(key, str) else key)
        if not postgres_enabled() or setting("ZGUA_PUBLIC_ACCOUNTS", "0") != "1":
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise RuntimeError("Public accounts require PostgreSQL, ZGUA_PUBLIC_ACCOUNTS=1, a valid ACCOUNT_MFA_KEY and an HTTPS PUBLIC_APP_URL origin.") from None


def public_accounts_enabled():
    if not public_deployment():
        return False
    validate_public_account_configuration()
    return True


def account_origin():
    if public_accounts_enabled():
        parsed = urlsplit(setting("PUBLIC_APP_URL"))
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        return "https://" + host + (":" + str(parsed.port) if parsed.port not in (None, 443) else "")
    return "http://127.0.0.1:5000" if not has_request_context() else request.host_url.rstrip("/")


def request_boundary(limit=MAX_PROFILE + 4096):
    # Validate the raw Host before Werkzeug normalizes away malformed userinfo
    # or ports. Do not let normalization turn an untrusted header into our host.
    raw_host = request.environ.get('HTTP_HOST','')
    if raw_host:
        try:
            parsed_host = urlsplit('//'+raw_host)
            if (not parsed_host.hostname or parsed_host.username is not None or parsed_host.password is not None
                    or parsed_host.path or parsed_host.query or parsed_host.fragment
                    or any(c.isspace() or ord(c) < 32 for c in raw_host) or '\\' in raw_host
                    or parsed_host.port == 0):
                raise ValueError
        except ValueError:
            return jsonify(error='Untrusted account host.'),403
    try:
        public = public_accounts_enabled()
        host = urlsplit(request.host_url).hostname
    except RuntimeError:
        return jsonify(error="Public account configuration is unavailable."), 503
    except ValueError:
        return jsonify(error="Untrusted account host."), 403
    try:
        local = ipaddress.ip_address(request.remote_addr or "").is_loopback
    except ValueError:
        local = False
    if public:
        actual, expected = urlsplit(request.host_url), urlsplit(account_origin())
        try:
            trusted = (actual.username is None and actual.password is None
                and ((actual.hostname or "").lower(), actual.port or 443) == (expected.hostname, expected.port or 443))
        except ValueError:
            trusted = False
        if not trusted:
            return jsonify(error="Untrusted account host."), 403
    elif not local or host not in ("localhost", "127.0.0.1", "::1"):
        return jsonify(error="Accounts are available only on this computer."), 403
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin is not None and origin != account_origin():
            return jsonify(error="Cross-origin request blocked."), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(error="Cross-site request blocked."), 403
    return bounded_body(limit)


def bounded_body(limit):
    # Flask 2.x has no per-request max_content_length setter. Bound the actual
    # stream as well as Content-Length, including WSGI-terminated chunked input.
    if (request.content_length or 0) > limit:
        return jsonify(error="Request body too large."), 413
    data = request.__dict__.get("_cached_data")
    if data is None:
        data = request.stream.read(limit + 1)
    if len(data) > limit:
        return jsonify(error="Request body too large."), 413
    request._cached_data = data
    if request.is_json:
        try:
            request.get_json(silent=True)
        except (RecursionError, UnicodeError):
            return jsonify(error="Invalid JSON body."), 400


def csrf_matches(expected, supplied):
    try:
        return (isinstance(expected, str) and isinstance(supplied, str)
                and hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")))
    except UnicodeError:
        return False


class AccountSessionExpired(Exception):
    pass


@blueprint.errorhandler(AccountSessionExpired)
def expired_session(error):
    return jsonify(error="Session expired. Reload and sign in again."), 401


def begin_account_write(db):
    db.execute("BEGIN IMMEDIATE")
    session = db.execute("SELECT * FROM sessions WHERE token=? AND expires>?", (g.account_session["token"], time.time())).fetchone()
    if not session or session["user_id"] != g.account_session["user_id"] or not csrf_matches(session["csrf"], request.headers.get("X-CSRF-Token", "")):
        raise AccountSessionExpired
    if session["user_id"] is not None:
        row = db.execute("SELECT role,disabled FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not row or row["disabled"] or not g.account_user or row["role"] != g.account_user["role"]:
            raise AccountSessionExpired


@contextmanager
def sqlite_database():
    path = Path(current_app.config.get("ACCOUNT_DATABASE", Path(__file__).resolve().parents[1] / "data" / "accounts.sqlite"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER, csrf TEXT NOT NULL, expires REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS profiles (user_id INTEGER PRIMARY KEY, body TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS user_documents (user_id INTEGER NOT NULL, kind TEXT NOT NULL, document_key TEXT NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, updated REAL, PRIMARY KEY(user_id,kind,document_key));
      CREATE TABLE IF NOT EXISTS user_document_versions (user_id INTEGER NOT NULL, kind TEXT NOT NULL, document_key TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL, created REAL, PRIMARY KEY(user_id,kind,document_key,revision));
      CREATE TABLE IF NOT EXISTS attempts (ip TEXT NOT NULL, created REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS email_tokens (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS account_tokens (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, purpose TEXT NOT NULL, address TEXT, expires REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS account_sequence (singleton INTEGER PRIMARY KEY, next_id INTEGER NOT NULL);
      INSERT OR IGNORE INTO account_sequence VALUES (1, (SELECT COALESCE(MAX(id),0)+1 FROM users));
      UPDATE account_sequence SET next_id=MAX(next_id,(SELECT COALESCE(MAX(id),0)+1 FROM users)) WHERE singleton=1;
    """)
    if "revision" not in {row["name"] for row in db.execute("PRAGMA table_info(profiles)")}:
        db.execute("ALTER TABLE profiles ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
    for name, definition in (("first_name", "TEXT"), ("last_name", "TEXT"), ("email", "TEXT"), ("email_verified", "INTEGER NOT NULL DEFAULT 1"), ("avatar", "TEXT"), ("role", "TEXT NOT NULL DEFAULT 'member'"), ("disabled", "INTEGER NOT NULL DEFAULT 0"), ("totp_secret", "TEXT"), ("totp_pending", "TEXT"), ("totp_pending_until", "REAL"), ("totp_last", "INTEGER NOT NULL DEFAULT -1"), ("recovery", "TEXT")):
        if name not in columns:
            db.execute("ALTER TABLE users ADD COLUMN " + name + " " + definition)
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email COLLATE NOCASE) WHERE email IS NOT NULL")
    try:
        with db:
            yield db
    finally:
        db.close()


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def valid_new_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        return False
    try:
        password.encode("utf-8")
        return True
    except UnicodeError:
        return False


def password_matches(password, row):
    try:
        return bool(row and isinstance(password, str) and len(password) <= 128
                    and isinstance(row["salt"], str) and re.fullmatch(r"[0-9a-fA-F]{64}", row["salt"])
                    and isinstance(row["hash"], str) and re.fullmatch(r"[0-9a-fA-F]{64}", row["hash"])
                    and csrf_matches(password_hash(password, row["salt"]), row["hash"]))
    except (ValueError, TypeError, UnicodeError):
        return False


def new_session(db, user_id=None, remember=False):
    raw = secrets.token_urlsafe(32)
    token, csrf = digest(raw), secrets.token_urlsafe(32)
    duration = 30 * 86400 if remember else 12 * 3600
    db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (token, user_id, csrf, time.time() + duration))
    g.new_cookie = (raw, duration if remember else None)
    return db.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()


def reserve_anonymous_session(db):
    now = time.time()
    key = 'anonymous:' + (request.remote_addr or 'unknown')
    db.execute('DELETE FROM attempts WHERE created<?',(now-900,))
    if db.execute('SELECT COUNT(*) FROM attempts WHERE ip=?',(key,)).fetchone()[0] >= 500:
        return False
    db.execute('INSERT INTO attempts VALUES (?,?)',(key,now))
    return True


@blueprint.before_request
def guard():
    # Do not accept forwarded headers as proof of TLS or localhost identity.
    blocked = request_boundary()
    if blocked:
        return blocked
    with database() as db:
        token = digest(request.cookies.get(COOKIE, ""))
        session = db.execute("SELECT * FROM sessions WHERE token=? AND expires>?", (token, time.time())).fetchone()
        if session is None:
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                return jsonify(error="Session expired. Reload and sign in again."), 401
            if public_deployment():
                db.execute('BEGIN IMMEDIATE')
                if not reserve_anonymous_session(db):
                    response = jsonify(error='New session capacity reached. Please retry shortly.')
                    response.headers['Retry-After'] = '60'
                    return response,429
            db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            session = new_session(db)
        g.account_session = dict(session)
        g.account_user = None
        if session["user_id"]:
            user = db.execute('SELECT id, name, first_name, last_name, email, email_verified, avatar, role, disabled, totp_secret IS NOT NULL AS "twoFactorEnabled" FROM users WHERE id=?', (session["user_id"],)).fetchone()
            if user and not user['disabled']:
                g.account_user = dict(user)
            else:
                db.execute("DELETE FROM sessions WHERE token=?", (token,))
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    return jsonify(error="Session expired. Reload and sign in again."), 401
                g.account_session = dict(new_session(db))
        if request.method not in ("GET", "HEAD", "OPTIONS") and not csrf_matches(session["csrf"], request.headers.get("X-CSRF-Token", "")):
            return jsonify(error="Invalid CSRF token."), 403


@blueprint.after_request
def headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if hasattr(g, "new_cookie"):
        raw, max_age = g.new_cookie
        response.set_cookie(COOKIE, raw, max_age=max_age, httponly=True, secure=public_deployment() or request.is_secure, samesite="Strict", path="/")
    return response


@blueprint.route("/session")
@blueprint.route("/profile", methods=["GET"])
def session_info():
    from services.user_profiles import load_profile
    profile = None
    revision = 0
    if g.account_user:
        with database() as db:
            profile, revision = load_profile(db, g.account_user["id"])
    return jsonify(user=g.account_user, csrf=g.account_session["csrf"], profile=profile, revision=revision, registrationEnabled=registration_enabled(), mailEnabled=account_mail.ready())


@blueprint.route("/register", methods=["POST"])
@blueprint.route("/login", methods=["POST"])
def authenticate():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error="Invalid credentials."), 400
    name, password = body.get("username", ""), body.get("password", "")
    if not isinstance(name, str) or not isinstance(password, str):
        return jsonify(error="Invalid credentials."), 400
    name = name.strip().lower()
    register = request.path.endswith("/register")
    if register and public_deployment() and setting("ZGUA_REGISTRATION_ENABLED", "0") != "1":
        return jsonify(error="Registration is unavailable."), 503
    if (register and not re.fullmatch(r"[a-z0-9_.-]{3,40}", name)) or not name or len(name) > 254 or not valid_new_password(password):
        return jsonify(error="Use a 3-40 character username and a 12-128 character password."), 400
    if register:
        first, last, email = body.get("firstName"), body.get("lastName"), body.get("email")
        if not all(isinstance(value, str) and 1 <= len(value.strip()) <= 80 and not any(ord(char) < 32 for char in value) for value in (first, last)):
            return jsonify(error="Enter your first name and surname."), 400
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@<>\x00-\x1f]+@[^\s@<>\x00-\x1f]+\.[^\s@<>\x00-\x1f]+", email):
            return jsonify(error="Enter a valid email address."), 400
        email = email.strip().lower()
        if body.get("confirmPassword") != password:
            return jsonify(error="Passwords do not match."), 400
        if not account_mail.ready():
            return jsonify(error="Registration is waiting for email delivery setup. Existing accounts can still sign in."), 503
    with database() as db:
        now = time.time()
        begin_account_write(db)
        if not limited(db):
            return jsonify(error="Too many attempts. Try again in 15 minutes."), 429
    with database() as db:
        begin_account_write(db)
        user = db.execute("SELECT * FROM users WHERE name=? OR lower(email)=lower(?)", (name, name)).fetchone()
        if register:
            salt = secrets.token_hex(32)
            hashed = password_hash(password, salt)
            if db.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone():
                return jsonify(error="That username is already used. Sign in or choose another username."), 409
            if db.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email,)).fetchone():
                return jsonify(error="That email address is already used. Sign in or resend its verification email."), 409
            try:
                next_id = db.execute('SELECT next_id FROM account_sequence WHERE singleton=1').fetchone()[0]
                db.execute('UPDATE account_sequence SET next_id=next_id+1 WHERE singleton=1')
                cursor = db.execute("INSERT INTO users(id,name,salt,hash,first_name,last_name,email,email_verified) VALUES (?,?,?,?,?,?,?,0)", (next_id, name, salt, hashed, first.strip(), last.strip(), email))
                user_id = next_id
            except INTEGRITY_ERRORS:
                return jsonify(error="That username or email is already registered. Sign in or resend verification."), 409
            try:
                issue_verification(db, user_id, email)
            except account_mail.MailDeliveryError as exc:
                db.execute("DELETE FROM email_tokens WHERE user_id=?", (user_id,))
                db.execute("DELETE FROM users WHERE id=?", (user_id,))
                return jsonify(error=str(exc)), 502
            except Exception:
                db.execute("DELETE FROM email_tokens WHERE user_id=?", (user_id,))
                db.execute("DELETE FROM users WHERE id=?", (user_id,))
                return jsonify(error="Confirmation email could not be sent. Please try again later."), 502
            return jsonify(verificationRequired=True, message="Check your email to confirm your account before signing in."), 201
        else:
            if user is None:
                password_hash(password, secrets.token_hex(32))
            if not password_matches(password, user):
                return jsonify(error="Invalid username or password."), 401
            if not user["email_verified"]:
                return jsonify(error="Confirm your email before signing in. You can resend the verification email."), 403
            if user['disabled']:
                return jsonify(error="This account is suspended."), 403
            if user['totp_secret']:
                from services.account_security import verify_factor
                if not verify_factor(db, user, body.get('twoFactorCode', '')):
                    return jsonify(error="Enter a valid authenticator or recovery code.", twoFactorRequired=True), 401
            user_id = user["id"]
        db.execute("DELETE FROM sessions WHERE token=?", (g.account_session["token"],))
        session = new_session(db, user_id, body.get("remember") is True)
    return jsonify(user={"id": user_id, "name": user["name"], "role": user['role']}, csrf=session["csrf"])


def issue_verification(db, user_id, email):
    raw = secrets.token_urlsafe(32)
    db.execute("DELETE FROM email_tokens WHERE user_id=? OR expires<?", (user_id, time.time()))
    db.execute("INSERT INTO email_tokens VALUES (?,?,?)", (digest(raw), user_id, time.time() + 86400))
    # Loopback links intentionally work only on the computer running this local app.
    link = account_origin() + "/charts#verify-email=" + raw
    subject, text, html = mail_templates.verification(link)
    account_mail.send(email, subject, text, html=html, idempotency_key="verify/" + digest(raw))


@blueprint.route("/verify", methods=["POST"])
def verify_email():
    body = request.get_json(silent=True)
    raw = body.get("token") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
        return jsonify(error="Invalid or expired confirmation link."), 400
    with database() as db:
        begin_account_write(db)
        token = db.execute("SELECT * FROM email_tokens WHERE token=? AND expires>?", (digest(raw), time.time())).fetchone()
        if not token:
            changed = db.execute("SELECT * FROM account_tokens WHERE token=? AND purpose='email' AND expires>?", (digest(raw), time.time())).fetchone()
            if not changed:
                return jsonify(error="Invalid or expired confirmation link. Request a new verification email."), 400
            address = changed["address"]
            if not isinstance(address, str) or len(address) > 254 or not re.fullmatch(r"[^\s@<>\x00-\x1f]+@[^\s@<>\x00-\x1f]+\.[^\s@<>\x00-\x1f]+", address):
                return jsonify(error="Invalid or expired confirmation link."), 400
            try:
                updated = db.execute("UPDATE users SET email=?, email_verified=1 WHERE id=?", (address, changed["user_id"]))
                if updated.rowcount != 1:
                    return jsonify(error="Invalid or expired confirmation link."), 400
            except INTEGRITY_ERRORS:
                return jsonify(error="That email address is already used."), 409
            db.execute("DELETE FROM account_tokens WHERE user_id=?", (changed["user_id"],))
            db.execute("DELETE FROM sessions WHERE user_id=?", (changed["user_id"],))
            session = new_session(db)
            return jsonify(message="Email address updated and confirmed. You can now sign in.", csrf=session["csrf"])
        updated = db.execute("UPDATE users SET email_verified=1 WHERE id=?", (token["user_id"],))
        if updated.rowcount != 1:
            return jsonify(error="Invalid or expired confirmation link."), 400
        db.execute("DELETE FROM email_tokens WHERE user_id=?", (token["user_id"],))
    return jsonify(message="Email confirmed. You can now sign in.")


@blueprint.route("/resend-verification", methods=["POST"])
def resend_verification():
    body = request.get_json(silent=True)
    email = body.get("email") if isinstance(body, dict) else None
    if not isinstance(email, str) or len(email) > 254:
        return jsonify(error="Enter your email address."), 400
    if not account_mail.ready():
        return jsonify(error="Email delivery is not configured yet."), 503
    with database() as db:
        now = time.time()
        begin_account_write(db)
        if not limited(db):
            return jsonify(error="Too many requests. Try again in 15 minutes."), 429
    with database() as db:
        begin_account_write(db)
        user = db.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND email_verified=0", (email.strip(),)).fetchone()
        if user:
            recent = db.execute("SELECT expires FROM email_tokens WHERE user_id=?", (user["id"],)).fetchone()
            if not recent or recent["expires"] - 86400 < time.time() - 60:
                try:
                    issue_verification(db, user["id"], user["email"])
                except account_mail.MailDeliveryError as exc:
                    db.rollback()
                    return jsonify(error=str(exc)), 502
                except Exception:
                    db.rollback()
                    return jsonify(error="Confirmation email could not be sent. Please try again later."), 502
    return jsonify(message="If that email has a pending account, a confirmation link has been sent recently. Check your inbox.")


@blueprint.route("/logout", methods=["POST"])
def logout():
    with database() as db:
        begin_account_write(db)
        db.execute("DELETE FROM sessions WHERE token=?", (g.account_session["token"],))
        session = new_session(db)
    return jsonify(user=None, csrf=session["csrf"])


def attempt_key(db):
    if not public_deployment():
        return request.remote_addr or 'unknown'
    user = getattr(g,'account_user',None)
    if user:
        return 'account:' + str(user['id'])
    body = request.get_json(silent=True)
    candidate = body.get('email') or body.get('username') if isinstance(body,dict) else None
    if isinstance(candidate,str) and 0 < len(candidate) <= 254:
        candidate = candidate.strip().lower()
        row = db.execute('SELECT id FROM users WHERE name=? OR lower(email)=lower(?)',(candidate,candidate)).fetchone()
        return 'account:' + str(row['id']) if row else 'subject:' + digest(candidate)
    return request.remote_addr or 'unknown'


def limited(db):
    now = time.time()
    db.execute("DELETE FROM attempts WHERE created<?", (now - 900,))
    key = attempt_key(db)
    keys = [(key,10)]
    if public_deployment():
        # Never trust client-supplied proxy headers. A broad transport ceiling
        # supplements per-account caps without locking all users behind one proxy.
        keys.append(('network:' + (request.remote_addr or 'unknown'),1000))
    if any(db.execute("SELECT COUNT(*) FROM attempts WHERE ip=?", (identity,)).fetchone()[0] >= maximum for identity,maximum in keys):
        return False
    for identity,maximum in keys:
        db.execute("INSERT INTO attempts VALUES (?,?)", (identity,now))
    return True


@blueprint.route("/details", methods=["PUT"])
def update_details():
    if not g.account_user: return jsonify(error="Sign in first."), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict): return jsonify(error="Invalid profile details."), 400
    first, last, name = body.get("firstName"), body.get("lastName"), body.get("username")
    if not all(isinstance(v, str) and 1 <= len(v.strip()) <= 80 and not any(ord(c) < 32 for c in v) for v in (first, last)):
        return jsonify(error="Enter your first name and surname."), 400
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{3,40}", name):
        return jsonify(error="Use a 3-40 character username."), 400
    try:
        avatar = clean_avatar(body.get("avatar"))
        with database() as db:
            begin_account_write(db)
            db.execute("UPDATE users SET name=?,first_name=?,last_name=?,avatar=? WHERE id=?",
                       (name.lower(), first.strip(), last.strip(), avatar, g.account_user["id"]))
    except INTEGRITY_ERRORS: return jsonify(error="That username is already used."), 409
    except ValueError as exc: return jsonify(error=str(exc)), 400
    return jsonify(saved=True)


@blueprint.route("/password", methods=["PUT"])
def change_password():
    if not g.account_user: return jsonify(error="Sign in first."), 401
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict): return jsonify(error="Invalid password request."), 400
    old, new = body.get("currentPassword"), body.get("password")
    if not isinstance(old, str) or len(old) > 128 or not valid_new_password(new) or new != body.get("confirmPassword"):
        return jsonify(error="Use a 12-128 character password and matching confirmation."), 400
    with database() as db:
        begin_account_write(db)
        if not limited(db): return jsonify(error="Too many attempts. Try again in 15 minutes."), 429
        row = db.execute("SELECT * FROM users WHERE id=?", (g.account_user["id"],)).fetchone()
        if not password_matches(old, row):
            return jsonify(error="Current password is incorrect."), 403
        if old == new: return jsonify(error="Choose a different password."), 400
        if row['totp_secret']:
            from services.account_security import verify_factor
            if not verify_factor(db, row, body.get('twoFactorCode','')):
                return jsonify(error='Enter your authenticator or recovery code.'), 403
        salt = secrets.token_hex(32)
        db.execute("UPDATE users SET salt=?,hash=? WHERE id=?", (salt, password_hash(new, salt), row["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        db.execute("DELETE FROM account_tokens WHERE user_id=?", (row["id"],))
        session = new_session(db, row["id"])
    return jsonify(message="Password changed. Other sessions have been signed out.", csrf=session["csrf"])


@blueprint.route("/sessions/revoke", methods=["POST"])
def revoke_sessions():
    if not g.account_user: return jsonify(error="Sign in first."), 401
    with database() as db:
        begin_account_write(db)
        db.execute("DELETE FROM sessions WHERE user_id=? AND token<>?", (g.account_user["id"], g.account_session["token"]))
    return jsonify(message="Other sessions signed out.")


@blueprint.route("/email", methods=["POST"])
def change_email():
    if not g.account_user: return jsonify(error="Sign in first."), 401
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict): return jsonify(error="Invalid email request."), 400
    email, password = body.get("email"), body.get("currentPassword")
    if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@<>\x00-\x1f]+@[^\s@<>\x00-\x1f]+\.[^\s@<>\x00-\x1f]+", email) or not isinstance(password, str) or len(password) > 128:
        return jsonify(error="Enter a valid new email address and your current password."), 400
    with database() as db:
        begin_account_write(db)
        if not limited(db): return jsonify(error="Too many attempts. Try again in 15 minutes."), 429
        row = db.execute("SELECT * FROM users WHERE id=?", (g.account_user["id"],)).fetchone()
        if not password_matches(password, row): return jsonify(error="Current password is incorrect."), 403
        if row['totp_secret']:
            from services.account_security import verify_factor
            if not verify_factor(db, row, body.get('twoFactorCode','')):
                return jsonify(error='Enter your authenticator or recovery code.'), 403
        if db.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email.strip(),)).fetchone(): return jsonify(error="That email address is already used."), 409
        raw = secrets.token_urlsafe(32)
        db.execute("DELETE FROM account_tokens WHERE user_id=? AND purpose='email'", (row["id"],))
        db.execute("INSERT INTO account_tokens VALUES (?,?,?,?,?)", (digest(raw), row["id"], "email", email.strip().lower(), time.time() + 86400))
        subject, text, html = mail_templates.verification(account_origin() + "/charts#verify-email=" + raw)
        try: account_mail.send(email.strip().lower(), "Confirm your new ZguaCharts email", text, html=html, idempotency_key="change-email/" + digest(raw))
        except Exception:
            db.rollback()
            return jsonify(error="Could not send confirmation. Your current email has not changed. Resend test mode requires a verified domain to email another address."), 502
    return jsonify(message="Check your new email address. Your existing address stays active until confirmation.")


@blueprint.route("/forgot-password", methods=["POST"])
def forgot_password():
    body = request.get_json(silent=True) or {}
    email = body.get("email") if isinstance(body, dict) else None
    if not isinstance(email, str) or len(email) > 254: return jsonify(error="Enter an email address."), 400
    if not account_mail.ready(): return jsonify(error="Email delivery is unavailable."), 503
    with database() as db:
        begin_account_write(db)
        if not limited(db): return jsonify(error="Too many requests. Try again in 15 minutes."), 429
        row = db.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND email_verified=1", (email.strip(),)).fetchone()
        if row:
            recent = db.execute("SELECT expires FROM account_tokens WHERE user_id=? AND purpose='reset'", (row["id"],)).fetchone()
            if not recent or recent["expires"] - 3600 < time.time() - 60:
                raw = secrets.token_urlsafe(32)
                db.execute("DELETE FROM account_tokens WHERE user_id=? AND purpose='reset'", (row["id"],))
                db.execute("INSERT INTO account_tokens VALUES (?,?,?,?,?)", (digest(raw), row["id"], "reset", None, time.time() + 3600))
                subject, text, html = mail_templates.password_reset(account_origin() + "/charts#reset-password=" + raw)
                try: account_mail.send(row["email"], subject, text, html=html, idempotency_key="reset/" + digest(raw))
                except Exception: db.rollback()
    return jsonify(message="If that email belongs to a confirmed account, a reset link has been sent recently. Check your inbox.")


@blueprint.route("/reset-password", methods=["POST"])
def reset_password():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict): return jsonify(error="Invalid reset request."), 400
    raw, password = body.get("token"), body.get("password")
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw) or not valid_new_password(password) or password != body.get("confirmPassword"):
        return jsonify(error="Enter a valid reset token and matching 12-128 character password."), 400
    with database() as db:
        begin_account_write(db)
        if not limited(db): return jsonify(error="Too many attempts. Try again in 15 minutes."), 429
        token = db.execute("SELECT * FROM account_tokens WHERE token=? AND purpose='reset' AND expires>?", (digest(raw), time.time())).fetchone()
        if not token: return jsonify(error="Invalid or expired reset link. Request a new one."), 400
        salt = secrets.token_hex(32)
        updated = db.execute("UPDATE users SET salt=?,hash=? WHERE id=?", (salt, password_hash(password, salt), token["user_id"]))
        if updated.rowcount != 1:
            return jsonify(error="Invalid or expired reset link. Request a new one."), 400
        db.execute("DELETE FROM account_tokens WHERE user_id=?", (token["user_id"],))
        db.execute("DELETE FROM sessions WHERE user_id=?", (token["user_id"],))
        session = new_session(db)
    return jsonify(message="Password reset. You can now sign in.", csrf=session["csrf"])


@blueprint.route("/profile", methods=["PUT"])
def save_profile():
    from services.user_profiles import ProfileConflict, save_snapshot
    if not g.account_user:
        return jsonify(error="Sign in to save your profile."), 401
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Invalid profile."), 400
    # Raw snapshots remain importable once at revision zero. New clients send
    # an envelope so revision metadata never becomes a localStorage entry.
    if isinstance(body.get("profile"), dict):
        snapshot, revision = body["profile"], body.get("revision", 0)
    else:
        snapshot = {key: value for key, value in body.items() if key != "revision"}
        revision = body.get("revision", 0)
    if type(revision) is not int or revision < 0:
        return jsonify(error="Invalid profile revision."), 400
    encoded = json.dumps(snapshot, ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_PROFILE:
        return jsonify(error="Profile too large."), 413
    try:
        with database() as db:
            begin_account_write(db)
            revision = save_snapshot(db, g.account_user["id"], snapshot, revision)
    except ProfileConflict as exc:
        return jsonify(error=str(exc), revision=exc.revision), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(saved=True, revision=revision)


@blueprint.get("/backtests/<run_id>")
def personal_backtest(run_id):
    if not g.account_user:
        return jsonify(error="Sign in first."), 401
    if not postgres_enabled():
        return jsonify(error="Personal backtest storage requires PostgreSQL."), 503
    with database() as db:
        row = db.execute("SELECT body FROM backtest_runs WHERE id=? AND user_id=?", (run_id,g.account_user['id'])).fetchone()
    return jsonify(json.loads(row['body'])) if row else (jsonify(error="Backtest not found."),404)
