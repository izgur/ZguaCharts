import unittest

from services import operational_freshness


class OperationalFreshnessServiceTests(unittest.TestCase):
    def test_freshness_verdict_bands(self):
        self.assertEqual(operational_freshness.freshness_verdict(None, 1, 2), "MISSING")
        self.assertEqual(operational_freshness.freshness_verdict(0.5, 1, 2), "CURRENT")
        self.assertEqual(operational_freshness.freshness_verdict(1.5, 1, 2), "AGING")
        self.assertEqual(operational_freshness.freshness_verdict(2, 1, 2), "STALE")

    def test_record_age_freshness_handles_empty_records(self):
        payload = operational_freshness.record_age_freshness([], warn_after_hours=1, stale_after_hours=2)
        self.assertEqual(payload["status"], "MISSING")
        self.assertIsNone(payload["latestAt"])


if __name__ == "__main__":
    unittest.main()
