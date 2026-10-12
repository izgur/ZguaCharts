import unittest
from unittest.mock import patch
import app as chart_app


class LibraryRefreshTests(unittest.TestCase):
    def test_library_routes(self):
        client = chart_app.app.test_client()
        for section in ('indicators', 'patterns'):
            result = client.get('/community/library/' + section)
            self.assertEqual(result.status_code, 200)
            self.assertIn(b'library-toc', result.data)
        self.assertEqual(client.get('/community/library/unknown').status_code, 404)

    def test_live_tail_is_limited_and_forces_fresh_base_data(self):
        for timeframe in ('2m', '6m', '12m'):
            with patch.object(chart_app, 'fetch_candles', return_value={'candles': []}) as fetch:
                response = chart_app.app.test_client().get('/api/candles?source=bybit&timeframe='+timeframe+'&limit=2000&live_tail=true')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(fetch.call_args[1]['limit'], 50)
                self.assertTrue(fetch.call_args[1]['force_network'])

    def test_live_tail_never_overrides_history_pagination(self):
        with patch.object(chart_app, 'fetch_older_candles', return_value={'candles': []}) as older:
            result = chart_app.app.test_client().get('/api/candles?before=123&limit=1000&live_tail=true')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(older.call_args[0][-1], 1000)


if __name__ == '__main__':
    unittest.main()
