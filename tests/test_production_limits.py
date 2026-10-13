import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import app, _hosted_backtest_slot, run_shared_backtest_engine
from services import tactic_sources


class ProductionLimitsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.old = dict(app.config)
        app.config.update(TESTING=True,ACCOUNT_DATABASE=str(Path(self.directory.name)/'test.sqlite'),DATABASE_URL='')
        self.client = app.test_client()

    def tearDown(self):
        app.config.clear()
        app.config.update(self.old)
        self.directory.cleanup()

    def test_chart_and_calculation_caps(self):
        with patch('app.hosted_mode',return_value=True),patch('app.fetch_candles',return_value={'candles':[]}) as fetch:
            self.assertEqual(self.client.get('/api/candles?limit=999999').status_code,200)
            self.assertEqual(fetch.call_args.kwargs['limit'],5000)
            self.assertEqual(self.client.get('/api/indicators?indicators='+','.join('study'+str(i) for i in range(13))).status_code,400)
            self.assertEqual(self.client.post('/api/zgua/events',json={'candles':[{}]*5001}).status_code,400)

    def test_busy_backtest_is_retryable_and_does_not_release_another_slot(self):
        self.assertTrue(_hosted_backtest_slot.acquire(blocking=False))
        try:
            with patch('app.hosted_mode',return_value=True),patch('services.account_security.request_role',return_value=None):
                response = self.client.post('/api/backtest/run-custom',json={'strategy':'RelativeStrengthV2'})
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.headers['Retry-After'],'3')
            self.assertFalse(_hosted_backtest_slot.acquire(blocking=False))
        finally:
            _hosted_backtest_slot.release()

    def test_bounded_history_is_reported_not_claimed_as_full_period(self):
        rows = [{'time':1700000000,'open':100,'high':101,'low':99,'close':100,'volume':1}]
        data = {'candles':rows,'limit':1,'diagnostics':{}}
        with app.test_request_context(),patch('app.hosted_mode',return_value=True),patch('app.fetch_historical_candles',return_value=data) as fetch,patch('app.node_executable',return_value='node'),patch('app.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}')):
            result = run_shared_backtest_engine('bybit','BTCUSDT','4h','max','RelativeStrengthV2',.055,.02,50000)
        self.assertEqual(fetch.call_args_list[0].kwargs['limit'],2500)
        self.assertEqual(fetch.call_args_list[1].kwargs['limit'],30000)
        self.assertLessEqual(float(fetch.call_args_list[1].kwargs['period'][:-1]),105)
        self.assertEqual(result['diagnostics']['hostedHistoryLimits']['requestedPeriod'],'max')
        self.assertTrue(result['diagnostics']['warnings'])

    def test_source_generation_is_bounded_and_reused(self):
        tactic_sources.cached_generated.cache_clear()
        try:
            with patch.object(tactic_sources.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='test-runtime')) as run:
                self.assertEqual(tactic_sources.generated('--runtime'),'test-runtime')
                self.assertEqual(tactic_sources.generated('--runtime'),'test-runtime')
                self.assertEqual(run.call_count,1)
        finally:
            tactic_sources.cached_generated.cache_clear()


if __name__ == '__main__':
    unittest.main()
