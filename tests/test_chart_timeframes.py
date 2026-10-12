import unittest
from unittest.mock import patch

import app as chart_app
import data_source


class ChartTimeframeTests(unittest.TestCase):
    def test_two_minute_availability_matches_base_feed(self):
        payload = chart_app.app.test_client().get('/api/config').get_json()
        for source, info in data_source.DATA_SOURCE_CONFIG['sources'].items():
            if '1m' in info['timeframes']:
                self.assertIn('2m', payload['sources'][source]['timeframes'])
                if '2m' not in info['timeframes']:
                    self.assertEqual(data_source.custom_chart_base(source, '2m'), '1m')

    def test_complete_two_minute_candles_preserve_ohlcv(self):
        rows = [{'time': i * 60, 'open': i, 'high': i + 3, 'low': i - 1,
                 'close': i + 1, 'volume': 10} for i in range(8)]
        with patch.object(data_source.time, 'time', return_value=600):
            candles = data_source.aggregate_chart_candles(rows, '2m', '1m')
            self.assertEqual([row['time'] for row in candles], [0, 120, 240, 360])
            self.assertEqual(candles[0], {'time': 0, 'open': 0, 'high': 4, 'low': -1, 'close': 2, 'volume': 20})
            missing = data_source.aggregate_chart_candles(rows[:1] + rows[2:], '2m', '1m')
            self.assertEqual([row['time'] for row in missing], [120, 240, 360])


if __name__ == '__main__':
    unittest.main()
