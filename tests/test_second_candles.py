import unittest
from unittest.mock import patch
from services.second_candles import aggregate_trades, fetch_recent_seconds
from data_source import DATA_SOURCE_CONFIG, interval_to_ms, fetch_older_candles
from free_exchange_data import seconds

class SecondCandlesTests(unittest.TestCase):
    def test_trade_ohlcv_dedup_and_gaps(self):
        trades=[dict(execId='b',time='1200',price='103',size='2'),dict(execId='a',time='1100',price='100',size='1'),dict(execId='c',time='3100',price='99',size='4')]
        rows=aggregate_trades(trades+[trades[0]])
        self.assertEqual([row['time'] for row in rows],[1,3])
        self.assertEqual(rows[0],dict(time=1,open=100.,high=103.,low=100.,close=103.,volume=3.))

    def test_capabilities_and_units(self):
        for source in ('bybit','bybit_spot','bybit_inverse','binance','okx_spot','okx_perpetual','okx_futures'):
            self.assertIn('1s',DATA_SOURCE_CONFIG['sources'][source]['timeframes'])
        for source in ('binance_futures','okx_options','hyperliquid'):
            self.assertNotIn('1s',DATA_SOURCE_CONFIG['sources'][source]['timeframes'])
        self.assertEqual(interval_to_ms('1s'),1000)
        self.assertEqual(seconds('1s'),1)
        self.assertEqual(fetch_older_candles('bybit','BTCUSDT','1s',1000)['candles'],[])

    @patch('services.second_candles.requests.get')
    def test_seed_discloses_limits(self,get):
        get.return_value.json.return_value={'retCode':0,'result':{'list':[dict(execId='test',time='1100',price='100',size='2')]}}
        result=fetch_recent_seconds('bybit_spot','TESTUSDT',10)
        self.assertTrue(result['diagnostics']['liveOnly'])
        self.assertEqual(get.call_args[1]['params']['limit'],60)
        self.assertEqual(result['diagnostics']['seedTradeIds'],['test'])

if __name__=='__main__':unittest.main()
