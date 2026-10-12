import unittest
import numpy as np
import pandas as pd
from indicator_catalog import CATALOG, calculate, confirmed_pivots
from indicators import build_indicator_payload, candles_to_frame, normalize_indicator_settings
from services.indicator_sources import source_payload


class ExtendedIndicatorTests(unittest.TestCase):
    def rows(self, count=100):
        return [{"time": 1700000000+i*3600, "open": 100+i, "high": 102+i,
                 "low": 98+i, "close": 100+i, "volume": 10+i} for i in range(count)]

    def test_ichimoku_midpoints_and_display_offsets(self):
        rows = self.rows()
        payload = build_indicator_payload(rows, ["ichimoku"])
        conversion, base, a, b, lagging = payload["overlays"]
        self.assertAlmostEqual(conversion["data"][51]["value"], 147)
        self.assertAlmostEqual(base["data"][51]["value"], 138.5)
        self.assertAlmostEqual(a["data"][51]["value"], (147+138.5)/2)
        self.assertAlmostEqual(b["data"][51]["value"], 125.5)
        self.assertEqual([item["offset"] for item in payload["overlays"]], [0,0,25,25,-25])
        self.assertEqual(a["cloud"], "a")
        self.assertEqual(b["cloud"], "b")
        self.assertEqual(lagging["data"][-1]["value"], 199)
        custom = build_indicator_payload(rows, ["ichimoku"], settings={"ichimoku": {"displacement": 10}})
        self.assertEqual(custom["overlays"][2]["offset"], 9)

    def test_ultimate_reference_and_flat_rsi(self):
        frame = candles_to_frame(self.rows())
        series = calculate(frame, "ultimate", CATALOG["ultimate"]["defaults"])[0]["values"]
        self.assertAlmostEqual(series.iloc[-1], 50)
        frame["close"] = 100
        connors = calculate(frame, "connors", {"length":3,"streak":2,"rank":10})[0]["values"]
        self.assertAlmostEqual(connors.iloc[-1], 100/3)

    def test_pivots_are_known_only_at_confirmation(self):
        hi, lo = confirmed_pivots(pd.Series([1.,2.,5.,3.,1.,2.]), pd.Series([0.,1.,4.,2.,0.,1.]), 2, 2)
        self.assertTrue(np.isnan(hi.iloc[2]))
        self.assertEqual(hi.iloc[4], 5)
        self.assertTrue(np.isnan(hi.iloc[3]))

    def test_zigzag_requires_observed_reversal(self):
        rows = self.rows(10)
        for row, price in zip(rows, [100,110,120,119,118,115,110,108,114,120]):
            row.update(open=price,close=price,high=price+1,low=price-1)
        output = calculate(candles_to_frame(rows), "zigzag", {"percent":5})[0]
        self.assertTrue(np.isnan(output["values"].iloc[4]))
        self.assertEqual(output["values"].iloc[6], 120)
        self.assertEqual(output["markers"][0]["time"], rows[6]["time"])

    def test_projection_metadata_survives_single_source_export(self):
        for key in ["ichimoku", "alligator", "psar"]:
            namespace = {}
            exec(source_payload(key)["files"][0]["code"], namespace)
            output = namespace["indicator"](self.rows())
            expected = build_indicator_payload(self.rows(), [key])
            for actual, native in zip(output["overlays"], expected["overlays"]):
                for field in ["offset", "cloud", "pointOnly"]:
                    self.assertEqual(actual.get(field), native.get(field))

    def test_sar_acceleration_validation(self):
        self.assertEqual(normalize_indicator_settings({"psar":{"start":0.04}})["psar"]["start"], 0.04)
        with self.assertRaises(ValueError): normalize_indicator_settings({"psar":{"start":0.5}})


if __name__ == '__main__': unittest.main()
