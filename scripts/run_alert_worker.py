"""Dedicated notification worker, separate from Gunicorn and paper trading."""
import sys
import os
import time
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
if not os.environ.get('RENDER') and os.environ.get('ZGUA_ENV') != 'production':
    load_dotenv(ROOT / '.env',override=False)
from services.chart_alerts import monitor_cycle

if __name__ == '__main__':
    from services.deployment import production_mode, validate_environment, database_errors
    if os.environ.get('RENDER') and not production_mode():
        raise SystemExit('FAIL ZGUA_ENV')
    if production_mode():
        errors = validate_environment()
        if not errors:
            errors = database_errors()
        if errors:
            raise SystemExit('Production alert worker checks failed: '+', '.join(errors))
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            monitor_cycle()
        except Exception:
            logging.exception('Alert worker cycle failed')
        time.sleep(30)
