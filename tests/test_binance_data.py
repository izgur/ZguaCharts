import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import binance_data
import data_source
import app as zgua_app


def kline(index):
    return [index * 3600000, "10", "12", "9", "11", "100"]


class BinanceDataTests(unittest.TestCase):
    def setUp(self):
        binance_data._candles.clear()
        zgua_app.historical_signal_snapshot.cache_clear()

    def test_catalog_all_quote_currencies_active_spot_only(self):
        rows = [{"symbol": "ETHBTC", "status": "TRADING", "isSpotTradingAllowed": True},
                {"symbol": "BTCUSDT", "status": "TRADING", "isSpotTradingAllowed": True},
                {"symbol": "OLDUSDT", "status": "BREAK"},
                {"symbol": "FUTURE", "status": "TRADING", "isSpotTradingAllowed": False}]
        with tempfile.TemporaryDirectory() as directory, patch.object(binance_data, "_catalog", None), \
                patch.object(binance_data, "CATALOG_PATH", Path(directory) / "symbols.json"), \
                patch.object(binance_data, "request_public", return_value={"symbols": rows}):
            self.assertEqual(binance_data.symbol_catalog()["symbols"], ["BTCUSDT", "ETHBTC"])

    def test_pagination_and_cache_copy(self):
        with patch.object(binance_data, "request_public", side_effect=[[kline(i) for i in range(200, 1200)], [kline(i) for i in range(200)]]) as request:
            rows = binance_data.fetch_binance_candles("BTCUSDT", "1h", 1200)
            self.assertEqual(len(rows), 1200)
            self.assertEqual(request.call_args_list[1][0][1]["endTime"], 200 * 3600000 - 1)
            rows[0]["close"] = 999
            self.assertEqual(binance_data.fetch_binance_candles("BTCUSDT", "1h", 1200)[0]["close"], 11)

    def test_closed_bar_filter_excludes_future_and_partial_bars(self):
        candles = [{"time": i * 3600, "close": i} for i in range(8)]
        with patch.object(data_source, "fetch_binance_candles", return_value=candles) as request:
            rows = data_source.fetch_closed_candles("binance", "BTCUSDT", "1h", 5 * 3600 + 1800)
            self.assertEqual([row["time"] for row in rows], [i * 3600 for i in range(5)])
            self.assertEqual(request.call_args[1]["end_ms"], (5 * 3600 + 1800) * 1000 - 1)

    def test_bybit_historical_request_uses_selected_cutoff(self):
        with patch.object(data_source, "request_bybit_kline_batch", return_value=[kline(i) for i in range(8)]) as request:
            rows = data_source.fetch_closed_candles("bybit", "BTCUSDT", "1h", 18000)
            self.assertEqual(len(rows), 5)
            self.assertEqual(request.call_args[0][2], 17999999)

    def test_snapshot_has_independent_timeframes_and_cache(self):
        candles = [{"time": i, "close": 1} for i in range(400)]
        signal = {"score": 70, "signalDirection": "LONG", "longSignalPct": 60, "shortSignalPct": 0, "tone": "buy"}
        with patch.object(zgua_app, "fetch_closed_candles", return_value=candles) as fetch, patch.object(zgua_app, "build_signal_payload", return_value=signal):
            result = zgua_app.historical_signal_snapshot("binance", "BTCUSDT", 18000)
            self.assertEqual([row["timeframe"] for row in result["timeframes"]], ["4h", "1h", "15m"])
            self.assertEqual(fetch.call_count, 3)
            zgua_app.historical_signal_snapshot("binance", "BTCUSDT", 18000)
            self.assertEqual(fetch.call_count, 3)

    def test_insufficient_history_not_reported_as_neutral(self):
        with patch.object(zgua_app, "fetch_closed_candles", return_value=[]):
            result = zgua_app.historical_signal_snapshot("binance", "NEWUSDT", 18000)
            self.assertTrue(all("error" in row for row in result["timeframes"]))

    def test_future_timestamp_rejected(self):
        response = zgua_app.app.test_client().get("/api/signals/at?as_of=999999999999")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
