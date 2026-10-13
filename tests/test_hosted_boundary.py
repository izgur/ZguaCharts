import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app


class HostedBoundaryTests(unittest.TestCase):
    def test_operator_routes_are_not_public(self):
        with tempfile.TemporaryDirectory() as folder:
            original = dict(app.config)
            try:
                app.config.update(TESTING=True,ACCOUNT_DATABASE=str(Path(folder)/'test.sqlite'),DATABASE_URL='')
                with patch.dict('os.environ',{'RENDER':'true'}):
                    client = app.test_client()
                    for route in ('/api/paper/run-once','/api/learning/tick','/api/research/autopilot/run-next','/api/config/bootstrap-local'):
                        self.assertEqual(client.post(route,json={}).status_code,403,route)
                    self.assertEqual(client.get('/api/chart-alerts').status_code,503)
                    self.assertEqual(client.get('/healthz').status_code,200)
            finally:
                app.config.clear()
                app.config.update(original)


if __name__ == '__main__':
    unittest.main()
