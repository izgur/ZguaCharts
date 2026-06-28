import threading
import time
import tempfile
import unittest
from unittest.mock import patch

import data_source


class FakeResponse:
    def __init__(self, rows, status_code=200, headers=None, ret_code=0):
        self._rows = rows
        self.status_code = status_code
        self.headers = headers or {
            "X-Bapi-Limit": "120",
            "X-Bapi-Limit-Status": "100",
            "X-Bapi-Limit-Reset-Timestamp": str(int(time.time() * 1000)),
        }
        self._ret_code = ret_code

    def json(self):
        return {"retCode": self._ret_code, "retMsg": "OK", "result": {"list": self._rows}}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def row(timestamp_ms, close):
    return [str(timestamp_ms), str(close), str(close + 1), str(close - 1), str(close), "10"]


class BybitLoadingTests(unittest.TestCase):
    def setUp(self):
        self._tmp_cache = tempfile.TemporaryDirectory()
        self._cache_patch = patch.object(data_source, "BYBIT_DISK_CACHE_DIR", data_source.Path(self._tmp_cache.name))
        self._cache_patch.start()
        data_source.clear_bybit_cache()
        data_source._bybit_rate_limiter._next_request_time = 0
        data_source._bybit_rate_limiter.total_wait_seconds = 0

    def tearDown(self):
        self._cache_patch.stop()
        self._tmp_cache.cleanup()

    def test_bybit_requests_always_use_limit_1000(self):
        calls = []

        def fake_get(_url, params, timeout):
            calls.append(dict(params))
            return FakeResponse([row(1_000_000, 100)])

        with patch.object(data_source.requests, "get", side_effect=fake_get):
            candles = data_source.fetch_bybit_candles("BTCUSDT", "1m", 50)

        self.assertEqual(len(candles), 1)
        self.assertTrue(calls)
        self.assertTrue(all(call["limit"] == 1000 for call in calls))

    def test_pagination_collects_more_than_1000_candles(self):
        first = [row((2_000 + i) * 60_000, i) for i in range(1000)]
        second = [row((1_000 + i) * 60_000, i) for i in range(1000)]
        calls = []

        def fake_get(_url, params, timeout):
            calls.append(dict(params))
            return FakeResponse(first if len(calls) == 1 else second)

        with patch.object(data_source.requests, "get", side_effect=fake_get):
            candles = data_source.fetch_bybit_candles("BTCUSDT", "1m", 1500)

        self.assertEqual(len(candles), 1500)
        self.assertEqual(len(calls), 2)
        self.assertLess(calls[1]["end"], min(int(item[0]) for item in first))

    def test_duplicates_are_removed_and_candles_are_sorted(self):
        rows = [
            row(3000, 3),
            row(1000, 1),
            row(2000, 2),
            row(2000, 22),
        ]

        with patch.object(data_source.requests, "get", return_value=FakeResponse(rows)):
            candles = data_source.fetch_bybit_candles("BTCUSDT", "1m", 50)

        self.assertEqual([item["time"] for item in candles], [1, 2, 3])
        self.assertEqual(candles[1]["close"], 22)

    def test_visible_chart_limit_caps_eight_chart_requests(self):
        self.assertEqual(data_source.adaptive_bybit_limit(20_000, 8), 3_000)

    def test_cache_reload_does_not_refetch_historical_candles(self):
        calls = []
        now_minute = int(time.time() // 60) * 60_000

        def fake_get(_url, params, timeout):
            calls.append(dict(params))
            return FakeResponse([row(now_minute - (999 - i) * 60_000, i) for i in range(1000)])

        with patch.object(data_source.requests, "get", side_effect=fake_get):
            first = data_source.fetch_bybit_candles("BTCUSDT", "1m", 500)
            second = data_source.fetch_bybit_candles("BTCUSDT", "1m", 500)

        self.assertEqual(len(first), 500)
        self.assertEqual(len(second), 500)
        self.assertEqual(len(calls), 1)

    def test_force_network_bypasses_stale_disk_cache_shortcut(self):
        old_time = int(time.time()) - 24 * 60 * 60
        stale_candles = [
            {"time": old_time - index * 4 * 60 * 60, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10}
            for index in range(60, 0, -1)
        ]
        data_source.save_bybit_disk_cache("BTCUSDT", "4h", stale_candles)
        data_source.clear_bybit_cache()
        calls = []
        fresh_time_ms = int(time.time() // (4 * 60 * 60)) * 4 * 60 * 60 * 1000

        def fake_get(_url, params, timeout):
            calls.append(dict(params))
            return FakeResponse([row(fresh_time_ms, 200)])

        with patch.object(data_source.requests, "get", side_effect=fake_get):
            candles, diagnostics = data_source.fetch_bybit_candles_with_diagnostics("BTCUSDT", "4h", 50, force_network=True)

        self.assertTrue(calls)
        self.assertTrue(diagnostics["network_fetch_attempted"])
        self.assertEqual(diagnostics["bybit_requests"], 1)
        self.assertEqual(candles[-1]["time"], fresh_time_ms // 1000)

    def test_concurrency_limit_holds_multiple_chart_loads_to_two_requests(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_get(_url, params, timeout):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return FakeResponse([row(1_000_000, 100)])

        def load(index):
            data_source.fetch_bybit_candles(f"BTC{index}USDT", "1m", 50)

        threads = [threading.Thread(target=load, args=(index,)) for index in range(8)]
        with patch.object(data_source.requests, "get", side_effect=fake_get):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertLessEqual(max_active, data_source.BYBIT_MAX_REQUESTS_IN_FLIGHT)


if __name__ == "__main__":
    unittest.main()
