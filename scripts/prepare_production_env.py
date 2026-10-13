"""Prepare an ignored production config without printing credentials."""
import argparse
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key
from services.deployment import validate_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials-file', type=Path, required=True)
    args = parser.parse_args()
    target = ROOT / '.env.production'
    try:
        credentials = dotenv_values(args.credentials_file)
        development = dotenv_values(ROOT / '.env')
        values = dict(dotenv_values(ROOT / 'deploy/production.env.example'))
        if target.exists():
            values.update(dotenv_values(target))
        for name in ('DATABASE_URL', 'DATABASE_URL_UNPOOLED'):
            values[name] = credentials[name]
        pooled = urlsplit(values['DATABASE_URL'])
        direct = urlsplit(values['DATABASE_URL_UNPOOLED'])
        dev = urlsplit(development['DATABASE_URL'])
        expected = 'ep-odd-thunder-b9jogu3z.c-7.eu-central-1.aws.neon.tech'
        if (direct.hostname != expected or pooled.hostname != expected.replace('.c-7', '-pooler.c-7')
                or direct.hostname == dev.hostname.replace('-pooler', '')
                or pooled.path != direct.path or pooled.username != direct.username
                or pooled.password != direct.password):
            raise ValueError()
        values['ZGUA_ENV'] = 'production'
        values['ZGUA_NEON_PROJECT_ID'] = 'cool-tree-27722416'
        if not values.get('ACCOUNT_MFA_KEY'):
            key = development.get('ACCOUNT_MFA_KEY')
            key_file = ROOT / 'data/accounts.mfa-key'
            if not key and key_file.is_file():
                key = key_file.read_text(encoding='ascii').strip()
            if not key:
                import psycopg2
                with psycopg2.connect(development['DATABASE_URL_UNPOOLED'], connect_timeout=15) as db:
                    with db.cursor() as cursor:
                        cursor.execute("SELECT count(*) FROM users WHERE COALESCE(totp_secret,'') <> '' OR COALESCE(totp_pending,'') <> ''")
                        if cursor.fetchone()[0]:
                            raise ValueError()
                key = Fernet.generate_key().decode('ascii')
            values['ACCOUNT_MFA_KEY'] = key
        if validate_environment(values):
            raise ValueError()
        if not target.exists():
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        for name, value in values.items():
            if value is not None:
                set_key(str(target), name, str(value), quote_mode='always')
        print('PASS production config prepared; credentials were not displayed.')
        return 0
    except Exception:
        print('FAIL production config preparation; check endpoint, credentials and MFA key privately.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
