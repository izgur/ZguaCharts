import unittest
from indicators import build_indicator_payload, normalize_indicator_settings, available_indicators


class IndicatorParameterTests(unittest.TestCase):
    def setUp(self):
        self.candles = [{"time": 100000 + i * 3600, "open": 100 + i % 7, "close": 101 + i % 9, "high": 113, "low": 99, "volume": 100 + i} for i in range(220)]

    def test_lengths_change_calculation_and_warmup(self):
        default = build_indicator_payload(self.candles, ["rsi", "volume", "ema"])
        custom = build_indicator_payload(self.candles, ["rsi", "volume", "ema"], settings={"rsi": {"length": 21}, "volume": {"length": 30}, "ema": {"length1": 18}})
        self.assertEqual(custom["panes"][0]["title"], "RSI 21")
        self.assertNotEqual(default["panes"][0]["series"][-1]["data"][-1], custom["panes"][0]["series"][-1]["data"][-1])
        self.assertEqual(custom["panes"][1]["series"][-1]["warmup"], 30)
        self.assertEqual(custom["overlays"][0]["name"], "EMA 18")

    def test_invalid_settings(self):
        for value in ([], {"rsi": {"length": 0}}, {"rsi": {"length": float("nan")}}, {"rsi": {"length": 2.1}}, {"rsi": {"length": True}}, {"rsi": {"wrong": 10}}, {"rsi": {"upper": 20}}, {"macd": {"fast": 30}}, {"fake": {}}):
            with self.assertRaises(ValueError): normalize_indicator_settings(value)

    def test_all_schemas_and_default_compatibility(self):
        names = [item["id"] for item in available_indicators()]
        self.assertEqual(build_indicator_payload(self.candles, names), build_indicator_payload(self.candles, names, settings={}))
        self.assertTrue(all("schema" in item for item in available_indicators()))


if __name__ == "__main__": unittest.main()
