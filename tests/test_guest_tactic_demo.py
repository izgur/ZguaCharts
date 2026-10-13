from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import app
from services import account_security

class GuestDemoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=app.config.get('ACCOUNT_DATABASE')
        app.config['ACCOUNT_DATABASE']=str(Path(self.temp.name)/'accounts.sqlite');app.config['TESTING']=True
        self.client=app.test_client();account_security._demo_requests.clear()
    def tearDown(self):
        if self.old is None: app.config.pop('ACCOUNT_DATABASE',None)
        else: app.config['ACCOUNT_DATABASE']=self.old
        self.temp.cleanup()
    def test_demo_is_only_public_tactic(self):
        response=self.client.get('/api/config');self.assertEqual(response.status_code,200)
        config=response.get_json();self.assertEqual([p['strategy'] for p in config['chart_signal_strategies']],['RelativeStrengthV2'])
        for route in ('/api/backtest-history','/api/backtest/strategy-metadata','/api/account/admin/users'):
            self.assertEqual(self.client.get(route).status_code,403)
        for strategy in ('ZGUA','RelativeStrengthFreshV3','SimpleAtrTrendV2'):
            self.assertEqual(self.client.post('/api/backtest/run-custom',json={'strategy':strategy}).status_code,403)
        self.assertEqual(self.client.post('/api/backtest/run-custom',json={'strategy':'RelativeStrengthV2','params':{'researchExitMode':'staged'}}).status_code,403)
    def test_demo_does_not_write_history_or_accept_private_settings(self):
        metrics={'trades':2,'totalReturnPct':1,'winRate':50}
        with patch('app.run_shared_backtest_engine',return_value={'presets':['private'],'numberOfTrades':2}) as run,patch('app.manual_backtest_result_summary',return_value=metrics),patch('app.record_backtest_history') as save:
            response=self.client.post('/api/backtest/run-custom',json={'strategy':'RelativeStrengthV2','source':'bybit','symbol':'BTCUSDT','timeframe':'1h','limit':50000,'saveHistory':True,'allowShorts':True,'feePct':0})
        self.assertEqual(response.status_code,200,response.get_json());payload=response.get_json();self.assertTrue(payload['guestDemo']);self.assertFalse(payload['historySaved']);self.assertNotIn('presets',payload);save.assert_not_called()
        self.assertEqual(run.call_args[0][6],.02);self.assertEqual(run.call_args[0][7],2500)
        self.assertTrue(run.call_args.kwargs['allow_shorts'])
    def test_guest_parameters_are_bounded_and_forwarded(self):
        params={'rsLookback':12,'rsThreshold':-.01,'rsiMin':40,'rsiMax':80,'atrMultiplier':2,'regimeMode':'symbolTrend','cooldownBars':3}
        with patch('app.run_shared_backtest_engine',return_value={}) as run,patch('app.manual_backtest_result_summary',return_value={'trades':0,'totalReturnPct':0,'winRate':0}):
            response=self.client.post('/api/backtest/run-custom',json={'strategy':'RelativeStrengthV2','params':params,'allowShorts':True})
        self.assertEqual(response.status_code,200,response.get_json())
        for key,value in params.items(): self.assertEqual(run.call_args.kwargs['strategy_params'][key],value)
        self.assertEqual(run.call_args.kwargs['strategy_params']['maxPositionPct'],100)
        for invalid in ({'rsLookback':1},{'rsLookback':2.5},{'rsThreshold':1},{'rsiMin':80,'rsiMax':40},{'regimeMode':'anything'},{'accountEquity':1000000},{'rsLookback':True}):
            response=self.client.post('/api/backtest/run-custom',json={'strategy':'RelativeStrengthV2','params':invalid})
            self.assertIn(response.status_code,(400,403))
        self.assertEqual(self.client.post('/api/backtest/run-custom',json={'strategy':'RelativeStrengthV2','allowShorts':'true'}).status_code,400)
    def test_cross_site_demo_blocked(self):
        self.assertEqual(self.client.post('/api/backtest/run-custom',json={'strategy':'RelativeStrengthV2'},headers={'Origin':'https://evil.example'}).status_code,403)

if __name__=='__main__':unittest.main()
