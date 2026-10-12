import unittest
from unittest.mock import patch
import data_source
import exchange_market_data as exchange
import market_symbols
import app as zgua_app


class MarketSymbolTests(unittest.TestCase):
    def setUp(self):
        exchange._candles.clear()
        market_symbols.catalog.cache_clear()

    def test_bybit_spot_and_perpetual_are_distinct_instruments(self):
        with patch.object(data_source, "fetch_bybit_instruments", return_value=[{
                "symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT", "status": "Trading", "contractType": "LinearPerpetual"}]):
            spot = market_symbols.search_catalog("bybit_spot")["instruments"][0]
            perp = market_symbols.search_catalog("bybit")["instruments"][0]
            self.assertNotEqual(spot["id"], perp["id"])
            self.assertEqual(spot["exchangeType"], "Spot")
            self.assertEqual(perp["type"], "Perpetual")

    def test_coin_m_catalog_keeps_contract_symbol(self):
        payload = {"symbols": [{"symbol": "BTCUSD_PERP", "contractStatus": "TRADING", "contractType": "PERPETUAL", "baseAsset": "BTC", "quoteAsset": "USD"}]}
        with patch.object(market_symbols, "public_request", return_value=payload):
            row = market_symbols.search_catalog("binance_coinm")["instruments"][0]
            self.assertEqual(row["symbol"], "BTCUSD_PERP")
            self.assertEqual(row["source"], "binance_coinm")

    def test_futures_candles_are_isolated_from_spot_cache(self):
        raw = [[3600000, "10", "12", "9", "11", "50"]]
        with patch.object(exchange, "public_request", return_value=raw) as request:
            rows = exchange.fetch_exchange_candles("binance_coinm", "BTCUSD_PERP", "1h", 50)
            self.assertEqual(rows[0]["close"], 11)
            self.assertIn("dapi.binance.com/dapi/v1/klines", request.call_args[0][0])
            self.assertEqual(request.call_args[0][1]["symbol"], "BTCUSD_PERP")
            rows[0]["close"] = 999
            self.assertEqual(exchange.fetch_exchange_candles("binance_coinm", "BTCUSD_PERP", "1h", 50)[0]["close"], 11)

    def test_new_market_historical_filter_does_not_include_partial_bars(self):
        with patch.object(data_source, "fetch_exchange_candles", return_value=[{"time": i * 3600} for i in range(8)]) as request:
            rows = data_source.fetch_closed_candles("bybit_spot", "BTCUSDT", "1h", 18000)
            self.assertEqual([row["time"] for row in rows], [i * 3600 for i in range(5)])
            self.assertEqual(request.call_args[1]["end_ms"], 17999999)

    def test_unknown_source_is_rejected(self):
        response = zgua_app.app.test_client().get("/api/market/instruments?source=madeup")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
