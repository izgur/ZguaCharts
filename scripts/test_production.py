"""Production-library unit regressions, isolated from development credentials."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.update(DATABASE_URL='',DATABASE_URL_UNPOOLED='',RENDER='',ZGUA_ENV='test',
                  ZGUA_PUBLIC_ACCOUNTS='0',ZGUA_LOAD_DOTENV='0',PUBLIC_APP_URL='http://127.0.0.1:5000')
PATTERNS = ('test_account*.py','test_indicator*.py','test_community_indicators.py',
            'test_extended_indicators.py','test_user_profiles.py','test_chart_alerts*.py',
            'test_resend_mail.py','test_mail_store.py','test_deployment.py',
            'test_postgres_storage.py','test_hosted_boundary.py','test_guest_tactic_demo.py',
            'test_zgua*.py','test_bybit_loading.py','test_calendar_timeframes.py','test_production_limits.py')

if __name__ == '__main__':
    suite = unittest.TestSuite(unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=pattern) for pattern in PATTERNS)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(not result.wasSuccessful())
