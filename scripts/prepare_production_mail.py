"""Prepare a private Render mail import from the local Resend configuration."""
import json
import os
from pathlib import Path
import sys
from email.utils import parseaddr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values, set_key
from services.account_mail import configured
from services.deployment import validate_environment


def main():
    try:
        config = json.loads((ROOT / 'config/local/alert-email.json').read_text(encoding='utf-8'))
        if config.get('provider') != 'resend' or not configured(config):
            raise ValueError()
        sender = parseaddr(config.get('from', ''))[1].lower()
        test_mode = sender.endswith('@resend.dev')
        if test_mode and (sender != 'onboarding@resend.dev'
                          or config.get('test_recipient', '').lower() != 'igorzgur@gmail.com'):
            raise ValueError()
        # Domain verification is an operator decision, not inferred from a sender.
        values = {
            'ZGUA_MAIL_PROVIDER': 'resend',
            'RESEND_API_KEY': config['api_key'],
            'ZGUA_MAIL_FROM': config['from'],
            'ZGUA_MAIL_DOMAIN_VERIFIED': '0',
            'ZGUA_REGISTRATION_ENABLED': '0',
        }
        if test_mode:
            values['ZGUA_MAIL_TEST_RECIPIENT'] = config['test_recipient']
        production_path = ROOT / '.env.production'
        production = dict(dotenv_values(production_path))
        production.update(values)
        if validate_environment(production):
            raise ValueError()
        for name, value in values.items():
            set_key(str(production_path), name, value, quote_mode='always')
        import_path = ROOT / '.env.render-mail'
        fd = os.open(str(import_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        for name, value in values.items():
            set_key(str(import_path), name, value, quote_mode='always')
        print('PASS private mail configuration prepared; public registration stays closed.')
        print('Sender mode: owner-only test sender.' if test_mode else 'Sender domain still requires verification.')
        return 0
    except Exception:
        print('FAIL private mail configuration; review local Resend and production settings.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
