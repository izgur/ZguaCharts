import unittest
from unittest.mock import patch
import free_exchange_data as data
import market_symbols
import data_source
import app as web


class PublicMarketsTests(unittest.TestCase):
    def setUp(self):
        data._cache.clear()
        market_symbols.catalog.cache_clear()

    def test_perpetual_display_does_not_change_native_identifier(self):
        row = market_symbols.instrument("okx_perpetual", "BTC-USDT-SWAP", "BTC / USDT", "OKX", "Crypto", "Perpetual", "Futures")
        self.assertEqual(row["displaySymbol"], "BTCUSDT.P")
        self.assertEqual(row["symbol"], "BTC-USDT-SWAP")

    def test_gate_spot_ohlcv_order_and_no_conflicting_limit(self):
        with patch.object(data, "public_json", return_value=[[3600, "500", "12", "14", "9", "10", "40"]]) as request:
            rows = data.request_page("gate_spot", "BTC_USDT", "1h", 7200000, 1000)
            self.assertEqual(rows[0], dict(time=3600, open=10, high=14, low=9, close=12, volume=40))
            self.assertNotIn("limit", request.call_args[0][2])
            self.assertLessEqual(request.call_args[0][2]["to"] - request.call_args[0][2]["from"], 999 * 3600)

    def test_kucoin_unified_ohlc_order(self):
        with patch.object(data, "public_json", return_value={"data": {"list": [[3600, 10, 14, 9, 12, 40]]}}) as request:
            self.assertEqual(data.request_page("kucoin_futures", "XBTUSDTM", "1h", 7200000, 200)[0]["close"], 12)
            self.assertEqual(request.call_args[0][2]["tradeType"], "FUTURES")

    def test_coinbase_ohlcv_order(self):
        with patch.object(data, "public_json", return_value=[[3600, 9, 14, 10, 12, 40]]):
            self.assertEqual(data.request_page("coinbase_spot", "BTC-USD", "1h", 7200000, 300)[0]["close"], 12)

    def test_pages_move_backwards_even_when_page_is_short(self):
        batches = [[data.candle(7200, 10, 12, 9, 11, 3)], [data.candle(3600, 10, 12, 9, 11, 3)], []]
        with patch.object(data, "request_page", side_effect=batches) as request:
            rows = data.fetch_free_candles("okx_spot", "BTC-USDT", "1h", 3, end_ms=10800000)
            self.assertEqual([row["time"] for row in rows], [3600, 7200])
            self.assertEqual(request.call_args_list[1][0][3], 7199999)
            rows[0]["close"] = 999
            self.assertEqual(data.fetch_free_candles("okx_spot", "BTC-USDT", "1h", 3, end_ms=10800000)[0]["close"], 11)

    def test_kraken_recent_window_is_not_repeated_forever(self):
        with patch.object(data, "request_page", return_value=[data.candle(3600, 10, 12, 9, 11, 3)]) as request:
            data.fetch_free_candles("kraken_spot", "XBTUSD", "1h", 5000)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args[0][4], 720)

    def test_coinbase_aggregate_4h(self):
        rows = [data.candle(i * 3600, 10 + i, 20 + i, 5 + i, 11 + i, 2) for i in range(4)]
        result = data.resample_candles(rows, "4h")[0]
        self.assertEqual((result["open"], result["high"], result["low"], result["close"], result["volume"]), (10, 23, 5, 14, 8))

    def test_coinapi_is_not_registered(self):
        self.assertFalse(any("coinapi" in source for source in data_source.PROVIDERS))

    def test_history_boundary_is_exclusive(self):
        with patch.object(data_source, "fetch_free_candles", return_value=[{"time": 3600}, {"time": 7200}]):
            result = data_source.fetch_older_candles("okx_spot", "BTC-USDT", "1h", 7200)
            self.assertEqual(result["candles"], [{"time": 3600}])

    def test_historical_signals_exclude_partial_new_provider_bars(self):
        with patch.object(data_source, "fetch_free_candles", return_value=[{"time": i * 3600} for i in range(8)]):
            self.assertEqual(len(data_source.fetch_closed_candles("okx_spot", "BTC-USDT", "1h", 18000)), 5)

    def test_indicator_post_uses_displayed_candles_without_refetch(self):
        rows = [data.candle(3600 + i * 3600, 10, 12, 9, 11, 3) for i in range(40)]
        with patch.object(web, "fetch_candles", side_effect=AssertionError("Must not refetch a different history")):
            result = web.app.test_client().post("/api/indicators?indicators=rsi", json={"candles": rows})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.get_json()["diagnostics"]["input"], "displayed-chart-candles")

    def test_indicator_post_rejects_unsorted_candles(self):
        rows = [data.candle(7200, 10, 12, 9, 11, 3), data.candle(3600, 10, 12, 9, 11, 3)]
        self.assertEqual(web.app.test_client().post("/api/indicators", json={"candles": rows}).status_code, 400)

    def test_rsi_parameter_update_after_live_candle(self):
        rows = [data.candle(3600 + i * 3600, 10 + i % 4, 15, 9, 11 + i % 3, 3) for i in range(60)]
        rows[-1].update(close=12, volume=42.5)
        response = web.app.test_client().post("/api/indicators?indicators=rsi,cipherb", json={"candles": rows, "settings": {"rsi": {"length": 12}}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["panes"][0]["title"], "RSI 12")
        del rows[-1]["volume"]
        response = web.app.test_client().post("/api/indicators?indicators=rsi", json={"candles": rows, "settings": {"rsi": {"length": 12}}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Incomplete chart candle", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
