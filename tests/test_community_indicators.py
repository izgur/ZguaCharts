import unittest
import numpy as np
import pandas as pd
from community_indicators import CATALOG, calculate, rma
from indicators import available_indicators, build_indicator_payload, candles_to_frame, normalize_indicator_settings


class CommunityIndicatorTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"time": 100000 + i * 3600, "open": 100 + 10 * np.sin(i / 8),
                      "high": 104 + 10 * np.sin(i / 8), "low": 96 + 10 * np.sin(i / 8),
                      "close": 101 + 10 * np.sin(i / 8), "volume": 100 + i} for i in range(320)]
        self.df = candles_to_frame(self.rows)

    def test_reference_vix_fix(self):
        p = dict(CATALOG["vixfix"]["defaults"], length=5)
        lines = calculate(self.df, "vixfix", p)
        for i in (4, 20, 80):
            high_close = max(row["close"] for row in self.rows[i - 4:i + 1])
            self.assertAlmostEqual(lines[0]["values"].iloc[i], 100 * (high_close - self.rows[i]["low"]) / high_close)

    def test_source_urls_and_schemas(self):
        registered = {row["id"]: row for row in available_indicators()}
        for key in CATALOG:
            self.assertIn(key, registered)
            self.assertTrue(CATALOG[key]["sourceUrl"].startswith("https://www.tradingview.com/script/"))
        for values in ({"utbot": {"heikin_ashi": 2}}, {"utbot": {"sensitivity": 0}}, {"vixfix": {"high_factor": 0}}):
            with self.assertRaises(ValueError): normalize_indicator_settings(values)

    def test_markers_are_crossovers_and_do_not_use_future_bars(self):
        for key in ("utbot", "supertrend_kivanc"):
            params = dict(CATALOG[key]["defaults"])
            if key == "supertrend_kivanc": params["multiplier"] = 1.0
            full = calculate(self.df, key, params)
            prefix = calculate(self.df.iloc[:200].copy(), key, params)
            markers = full[0]["markers"]
            self.assertTrue(markers)
            self.assertTrue(any(marker["text"] == "Buy" for marker in markers))
            self.assertTrue(any(marker["text"] == "Sell" for marker in markers))
            self.assertEqual(prefix[0]["markers"], [marker for marker in markers if marker["time"] <= self.rows[199]["time"]])
            payload = build_indicator_payload(self.rows, [key], settings={key: params})
            self.assertEqual(payload["overlays"][0]["markers"], markers)

    def test_ut_stop_reference_recurrence(self):
        p = CATALOG["utbot"]["defaults"]
        lines = calculate(self.df, "utbot", p)
        stop = lines[0]["values"]
        tr = pd.concat([self.df.high - self.df.low, (self.df.high - self.df.close.shift()).abs(),
                        (self.df.low - self.df.close.shift()).abs()], axis=1).max(axis=1)
        losses = rma(tr, p["length"])
        self.assertTrue(stop.iloc[:9].isna().all())
        self.assertAlmostEqual(stop.iloc[9], self.df.close.iloc[9] - losses.iloc[9])
        for i in range(10, 100):
            prior = stop.iloc[i - 1]; price = self.df.close.iloc[i]; last = self.df.close.iloc[i - 1]
            if price > prior and last > prior: expected = max(prior, price - losses.iloc[i])
            elif price < prior and last < prior: expected = min(prior, price + losses.iloc[i])
            else: expected = price - losses.iloc[i] if price > prior else price + losses.iloc[i]
            self.assertAlmostEqual(stop.iloc[i], expected)

    def test_corrected_squeeze_separate_from_legacy_and_native(self):
        default = build_indicator_payload(self.rows, ["squeeze_lb"])
        legacy = build_indicator_payload(self.rows, ["squeeze_lb"], settings={"squeeze_lb": {"legacy_bb": 1}})
        self.assertNotEqual(default["panes"][0]["series"][1], legacy["panes"][0]["series"][1])
        self.assertEqual(default["panes"][0]["series"][0], legacy["panes"][0]["series"][0])
        native = build_indicator_payload(self.rows, ["squeeze"])
        self.assertNotEqual(default["panes"][0]["series"][1], native["panes"][0]["series"][1])


if __name__ == "__main__": unittest.main()
