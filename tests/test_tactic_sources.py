import unittest
from unittest.mock import patch
from flask import Flask
from services.tactic_sources import blueprint, NAMES

class TacticSourceTests(unittest.TestCase):
    def setUp(self):
        role = patch('services.account_security.request_role', return_value='tactics')
        role.start()
        self.addCleanup(role.stop)
        app=Flask(__name__)
        app.register_blueprint(blueprint)
        self.client=app.test_client()

    def test_guests_and_members_cannot_export_tactics(self):
        for role in (None,'member'):
            with patch('services.account_security.request_role',return_value=role):
                self.assertEqual(self.client.get('/api/tactic-source/ZGUA').status_code,403)

    def test_all_sources_are_selected_entry_rules(self):
        for name in NAMES:
            response=self.client.get('/api/tactic-source/'+name)
            self.assertEqual(response.status_code,200)
            source=response.get_json()
            self.assertEqual(source['strategy'],name)
            self.assertRegex(source['code'],r'module\.exports\s*=\s*\{\s*baseStrategy:')
            self.assertNotIn('function run(',source['code'])
            self.assertNotIn('function entrySignal(',source['code'])

    def test_no_arbitrary_source_paths(self):
        for name in ('app.py','fake','..%2Fapp.py'):
            self.assertEqual(self.client.get('/api/tactic-source/'+name).status_code,404)

    def test_worker_blocks_network_and_child_workers(self):
        response=self.client.get('/api/tactic-source/worker.js')
        policy=response.headers['Content-Security-Policy']
        self.assertIn("connect-src 'none'",policy)
        self.assertIn("worker-src 'none'",policy)
        self.assertIn('/api/tactic-source/runtime.js',policy)

    def test_runtime_has_no_host_dependencies(self):
        response=self.client.get('/api/tactic-source/runtime.js')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('require("fs")',response.get_data(as_text=True))

if __name__=='__main__': unittest.main()
