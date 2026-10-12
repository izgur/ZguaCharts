import unittest
from unittest.mock import patch
import free_exchange_data as data
from data_source import DATA_SOURCE_CONFIG
from market_symbols import catalog

class TopSourcesTests(unittest.TestCase):
    def test_ten_crypto_exchanges_are_registered(self):
        exchanges = {row['exchange'] for key,row in DATA_SOURCE_CONFIG['sources'].items() if key != 'yfinance'}
        self.assertEqual(exchanges, {'Binance','Bybit','OKX','Gate.io','KuCoin','Kraken','Coinbase','Hyperliquid','Bitget','MEXC'})

    def test_catalog_ignores_noncrypto_and_inactive(self):
        row = dict(symbol='BTCUSDT',baseCoin='BTC',quoteCoin='USDT',status='online')
        with patch.object(data,'public_json',return_value={'data':[row,dict(row,status='offline'),dict(row,isReality='yes')]}):
            self.assertEqual(len(data.catalog_rows('bitget_spot')),1)
        with patch.object(data,'public_json',return_value={'symbols':[dict(symbol='BTCUSDT',baseAsset='BTC',quoteAsset='USDT',status='1'),dict(symbol='OLD',baseAsset='OLD',quoteAsset='USDT',status='2')]}):
            self.assertEqual(len(data.catalog_rows('mexc_spot')),1)

    def test_native_candle_intervals_and_volume(self):
        raw = [[3600000,'10','14','9','12','40','500']]
        with patch.object(data,'public_json',return_value={'data':raw}) as request:
            self.assertEqual(data.request_page('bitget_coinm','BTCUSD_CM','1h',7200000,5)[0]['volume'],40)
            self.assertEqual(request.call_args[0][2]['interval'],'1H')
            self.assertEqual(request.call_args[0][2]['category'],'COIN-FUTURES')
        with patch.object(data,'public_json',return_value=raw) as request:
            self.assertEqual(data.request_page('mexc_spot','BTCUSDT','1h',7200000,5)[0]['time'],3600)
            self.assertEqual(request.call_args[0][2]['interval'],'60m')

    def test_pagination_stops_and_deduplicates(self):
        data._cache.clear()
        row=data.candle(3600,10,14,9,12,40)
        with patch.object(data,'request_page',side_effect=[[row],[]]) as request:
            self.assertEqual(len(data.fetch_free_candles('bitget_spot','BTCUSDT','1h',5,end_ms=7200000)),1)
            self.assertEqual(request.call_args_list[1][0][3],3599999)

    def test_bitget_daily_range_respects_ninety_day_limit(self):
        with patch.object(data,'public_json',return_value={'data':[]}) as request:
            data.request_page('bitget_spot','BTCUSDT','1d',1790000000000,100)
            params=request.call_args[0][2]
            self.assertEqual(params['limit'],90)
            self.assertLessEqual(params['endTime']-params['startTime'],90*86400000)

    def test_mexc_request_range_uses_observed_page_cap(self):
        with patch.object(data,'public_json',return_value=[]) as request:
            data.request_page('mexc_spot','BTCUSDT','1h',1790000000000,1000)
            params=request.call_args[0][2]
            self.assertEqual(params['limit'],500)
            self.assertLessEqual(params['endTime']-params['startTime'],500*3600000)

if __name__=='__main__':
    unittest.main()
