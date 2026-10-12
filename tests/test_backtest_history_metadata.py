import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app as web


class BacktestHistoryMetadataTests(unittest.TestCase):
    def test_history_preserves_source_execution_and_closed_trade_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "history.json"
            with patch.object(web, "BACKTEST_HISTORY_PATH", target):
                web.record_backtest_history({"source": "bybit", "symbol": "BTCUSDT", "timeframe": "1h", "executionTimeframe": "5m", "period": "84d", "strategy": "RelativeStrengthV2",
                    "candlesLoaded": 2000, "firstCandleTime": 100, "lastCandleTime": 200, "trades": 2,
                    "params": {"feePct": 0, "slippagePct": 0}, "tradeList": [{"exitReason": "Stop"}, {"exitReason": "End of data"}]})
                record = json.loads(target.read_text())[0]
                self.assertEqual(record["executionTimeframe"], "5m")
                self.assertEqual(record["candlesLoaded"], 2000)
                self.assertEqual(record["closedTrades"], 1)
                self.assertTrue(record["includesEndOfData"])


if __name__ == "__main__":
    unittest.main()
