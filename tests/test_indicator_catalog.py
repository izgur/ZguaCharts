import unittest
import numpy as np
from indicator_catalog import CATALOG, calculate, rma, wma, regression
from indicators import available_indicators, build_indicator_payload, candles_to_frame


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"time": 100000 + i * 3600, "open": 100 + i / 5, "high": 106 + i / 5 + 4*np.sin(i/5), "low": 94 + i / 5 + 3*np.sin(i/5),
                      "close": 101 + i / 5 + np.sin(i), "volume": 100 + i % 17} for i in range(900)]
        self.frame = candles_to_frame(self.rows)

    def test_all_catalog_studies_are_registered_and_render(self):
        available = {item["id"]: item for item in available_indicators()}
        for key, entry in CATALOG.items():
            with self.subTest(indicator=key):
                self.assertIn(key, available)
                self.assertEqual(available[key]["placement"], entry["placement"])
                payload = build_indicator_payload(self.rows, [key])
                series = payload["overlays"] if entry["placement"] == "overlay" else payload["panes"][0]["series"]
                self.assertTrue(series)
                for item in series:
                    self.assertEqual(len(item["data"]), len(self.rows))
                    # Divergence classes may legitimately have no matching event.
                    if key not in ("supertrend_kivanc", "zigzag", "zgua"):
                        self.assertTrue(any(row["value"] is not None for row in item["data"]))
                    self.assertTrue(all(row["value"] is None or np.isfinite(row["value"]) for row in item["data"]))
                    self.assertEqual(item["indicatorId"], key)

    def test_no_future_data_changes_prior_values(self):
        prefix = self.frame.iloc[:200].copy()
        for key, entry in CATALOG.items():
            with self.subTest(indicator=key):
                full = calculate(self.frame, key, entry["defaults"])
                past = calculate(prefix, key, entry["defaults"])
                for a, b in zip(full, past):
                    np.testing.assert_allclose(a["values"].iloc[:200], b["values"], equal_nan=True, rtol=1e-10, atol=1e-10)

    def test_reference_math(self):
        import pandas as pd
        values = pd.Series([1., 2., 3., 4., 5.])
        self.assertAlmostEqual(wma(values, 3).iloc[2], 14 / 6)
        self.assertAlmostEqual(rma(values, 3).iloc[2], 2)
        self.assertAlmostEqual(rma(values, 3).iloc[3], 8 / 3)
        self.assertAlmostEqual(regression(values, 3).iloc[4], 5)
        payload = build_indicator_payload(self.rows, ["donchian"], settings={"donchian": {"length": 5}})
        self.assertAlmostEqual(payload["overlays"][0]["data"][10]["value"], max(row["high"] for row in self.rows[6:11]))

    def test_settings_change_formulas(self):
        for key, entry in CATALOG.items():
            defaults = entry["defaults"]
            if not defaults: continue
            if key == "zigzag": continue  # No 5% reversals in the rising fixture; tested separately.
            name = next(iter(defaults)); value = defaults[name]
            custom = {name: value + 5 if name != "offset" else 0.25}
            if name == "fast": custom = {name: value + 1}
            if key == "psar": custom = {"start": 0.04}
            if key == "anchoredvwap": custom = {"length": 8}
            if key == "zgua": custom = {"max_events": 1}
            with self.subTest(indicator=key):
                old = build_indicator_payload(self.rows, [key])
                new = build_indicator_payload(self.rows, [key], settings={key: custom})
                self.assertNotEqual(old, new)

    def test_flat_and_zero_volume_do_not_emit_infinity(self):
        rows = [{"time": i + 1, "open": 10, "high": 10, "low": 10, "close": 10, "volume": 0} for i in range(250)]
        for key in CATALOG:
            with self.subTest(indicator=key):
                payload = build_indicator_payload(rows, [key])
                series = payload["overlays"] or payload["panes"][0]["series"]
                self.assertTrue(all(p["value"] is None or np.isfinite(p["value"]) for s in series for p in s["data"]))


if __name__ == "__main__": unittest.main()
