"""Secret-safe production validation. Importing this module performs no I/O."""
import ipaddress
import json
import os
import re
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SCHEMA = {
    "schema_migrations": "version applied_at",
    "users": "id name salt hash first_name last_name email email_verified avatar role disabled totp_secret totp_pending totp_pending_until totp_last recovery",
    "account_sequence": "singleton next_id",
    "sessions": "token user_id csrf expires",
    "profiles": "user_id body revision",
    "attempts": "ip created",
    "email_tokens": "token user_id expires",
    "account_tokens": "token user_id purpose address expires",
    "admin_audit": "id actor action target created",
    "site_settings": "name value",
    "user_documents": "user_id kind document_key body revision updated",
    "user_document_versions": "user_id kind document_key revision body created",
    "alert_records": "id user_id body revision updated",
    "alert_events": "id user_id alert_id body created",
    "mail_usage": "period attempts",
    "mail_deliveries": "id recipient subject status provider_id error attempts created updated",
    "backtest_runs": "id user_id body created",
}


def production_mode(env=None):
    env = os.environ if env is None else env
    return str(env.get("ZGUA_ENV", "")).strip().lower() == "production"


def public_registration_enabled(env=None):
    env = os.environ if env is None else env
    default = "0" if production_mode(env) else "1"
    return env.get("ZGUA_REGISTRATION_ENABLED", default) == "1"


def _host_valid(host):
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return len(host) <= 253 and all(
            re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", part)
            for part in host.split(".")
        )


def _url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError()
    parsed = urlsplit(value)
    if not _host_valid(parsed.hostname) or "\\" in value or parsed.netloc.endswith(":"):
        raise ValueError()
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError()
    return parsed


def _postgres_url_valid(value):
    try:
        parsed = _url(value)
        query = parse_qs(parsed.query, keep_blank_values=True)
        return (parsed.scheme in ("postgres", "postgresql")
                and bool(parsed.path.strip("/")) and not parsed.fragment
                and query.get("sslmode") in (["require"], ["verify-ca"], ["verify-full"]))
    except (ValueError, TypeError):
        return False


def _origin_valid(value):
    try:
        parsed = _url(value)
        return (parsed.scheme == "https" and parsed.username is None
                and parsed.password is None and not parsed.path
                and "?" not in value and "#" not in value)
    except (ValueError, TypeError):
        return False


def _same_database(first, second):
    try:
        a, b = _url(first), _url(second)
        return (a.hostname.replace("-pooler", ""), a.path) == (
            b.hostname.replace("-pooler", ""), b.path)
    except (ValueError, TypeError):
        return False


def validate_environment(env=None):
    """Return fixed failing check names; never return supplied values or exceptions.

    Pure configuration check: no dotenv, Flask app, database, files or mail I/O.
    Development configurations are intentionally outside this production contract.
    """
    env = os.environ if env is None else env
    if not production_mode(env):
        return []
    errors = []
    if not _postgres_url_valid(env.get("DATABASE_URL")):
        errors.append("DATABASE_URL")
    if env.get("DATABASE_URL_UNPOOLED") and not _postgres_url_valid(env["DATABASE_URL_UNPOOLED"]):
        errors.append("DATABASE_URL_UNPOOLED")
    if env.get("ZGUA_DEVELOPMENT_DATABASE_URL") and _same_database(
            env.get("DATABASE_URL"), env["ZGUA_DEVELOPMENT_DATABASE_URL"]):
        errors.append("database.environment_separation")
    if not _origin_valid(env.get("PUBLIC_APP_URL")):
        errors.append("PUBLIC_APP_URL")
    try:
        from cryptography.fernet import Fernet
        key = env.get("ACCOUNT_MFA_KEY", "")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}=", key):
            raise ValueError()
        Fernet(key.encode("ascii"))
    except (ImportError, ValueError, TypeError):
        errors.append("ACCOUNT_MFA_KEY")
    if env.get("ZGUA_PUBLIC_ACCOUNTS") != "1":
        errors.append("ZGUA_PUBLIC_ACCOUNTS")
    if env.get("ZGUA_REGISTRATION_ENABLED", "0") not in ("0", "1"):
        errors.append("ZGUA_REGISTRATION_ENABLED")
    if public_registration_enabled(env):
        provider = env.get("ZGUA_MAIL_PROVIDER")
        if provider not in ("resend", "smtp"):
            errors.append("ZGUA_MAIL_PROVIDER")
        sender = (env.get("ZGUA_MAIL_FROM") if provider == "resend"
                  else env.get("ZGUA_MAIL_FROM") or env.get("ZGUA_SMTP_FROM"))
        sender = sender or ""
        address = parseaddr(sender)[1]
        domain = address.rsplit("@", 1)[-1].lower()
        if (any(c in sender for c in "\r\n") or address.count("@") != 1
                or not _host_valid(domain) or "." not in domain
                or domain == "resend.dev" or domain.endswith(".resend.dev")):
            errors.append("mail.sender_domain")
        if env.get("ZGUA_MAIL_DOMAIN_VERIFIED") != "1":
            errors.append("ZGUA_MAIL_DOMAIN_VERIFIED")
        if provider == "resend" and not str(env.get("RESEND_API_KEY", "")).strip():
            errors.append("RESEND_API_KEY")
        if provider == "smtp":
            if env.get("ZGUA_MAIL_FROM") and env.get("ZGUA_MAIL_FROM") != env.get("ZGUA_SMTP_FROM"):
                errors.append("mail.sender_configuration")
            if not env.get("ZGUA_SMTP_HOST"):
                errors.append("ZGUA_SMTP_HOST")
            if env.get("ZGUA_SMTP_SECURITY") not in ("starttls", "ssl"):
                errors.append("ZGUA_SMTP_SECURITY")
            try:
                port = int(env.get("ZGUA_SMTP_PORT", ""))
                if not 1 <= port <= 65535:
                    raise ValueError()
            except (TypeError, ValueError):
                errors.append("ZGUA_SMTP_PORT")
            if not env.get("ZGUA_SMTP_USERNAME") or not env.get("ZGUA_SMTP_PASSWORD"):
                errors.append("mail.smtp_credentials")
    return errors


def runtime_assets_errors():
    """Check presence of the pinned browser Python runtime without loading it."""
    root = ROOT / 'static' / 'pyodide'
    names = ('pyodide.js','pyodide.asm.js','pyodide.asm.wasm','python_stdlib.zip','pyodide-lock.json','asset-hashes.json')
    try:
        return [] if all((root/name).is_file() and (root/name).stat().st_size > 0 for name in names) else ['runtime.pyodide']
    except OSError:
        return ['runtime.pyodide']


def database_errors():
    """Read-only readiness via services.storage.database using the active config.

    No app import, migration, import of local data, or SQLite fallback is allowed.
    The optional Flask configuration context is honored by the storage layer.
    """
    try:
        from services import storage
        if not _postgres_url_valid(storage.setting("DATABASE_URL")):
            return ["DATABASE_URL"]
        versions = {path.name for path in (ROOT / "migrations").glob("*.sql")}
        if not versions:
            return ["database.migrations"]
        with storage.database() as db:
            db.execute("SET TRANSACTION READ ONLY")
            applied = {row[0] for row in db.execute("SELECT version FROM public.schema_migrations").fetchall()}
            columns = {}
            for table, column in db.execute(
                    "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public'").fetchall():
                columns.setdefault(table, set()).add(column)
            errors = []
            if not versions.issubset(applied):
                errors.append("database.migrations")
            if any(not set(required.split()).issubset(columns.get(table, set()))
                   for table, required in REQUIRED_SCHEMA.items()):
                errors.append("database.schema")
            # Verify privileges and effective search_path without reading user data.
            if not errors:
                for table, required in REQUIRED_SCHEMA.items():
                    db.execute("SELECT " + ",".join(required.split()) + " FROM " + table + " LIMIT 0")
                if production_mode():
                    row = db.execute("SELECT value FROM site_settings WHERE name='deployment_environment'").fetchone()
                    realm = json.loads(row[0]) if row else {}
                    project = os.environ.get('ZGUA_NEON_PROJECT_ID')
                    if realm.get('environment') != 'production' or project and realm.get('projectId') != project:
                        errors.append('database.environment')
                    if not db.execute("SELECT EXISTS(SELECT 1 FROM users WHERE role='admin' AND disabled=0 AND email_verified=1 AND email IS NOT NULL)").fetchone()[0]:
                        errors.append('database.administrator')
            return errors
    except Exception:
        return ["database.readiness"]
