import unittest
from unittest.mock import patch

import app as zgua_app


class PaperHandbrakeTests(unittest.TestCase):
    def test_sidebar_summary_skips_expensive_reports(self):
        state = {"openPositions": [{"symbol": "BTCUSDT"}], "closedTrades": [], "accountEquity": 10000}
        with patch.object(zgua_app, "read_json_file", return_value=state), \
                patch.object(zgua_app, "load_paper_candidate_config", return_value={"enabled": True}), \
                patch.object(zgua_app, "paper_real_trading_enabled", return_value=(False, "Disabled")), \
                patch.object(zgua_app, "build_candidate_health") as health, \
                patch.object(zgua_app, "build_paper_observation_report") as observation:
            response = zgua_app.app.test_client().get("/api/paper/status?summaryOnly=true")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertEqual(payload["openPositions"], state["openPositions"])
        self.assertEqual(payload["handbrake"]["metrics"]["closedTrades"], 0)
        health.assert_not_called()
        observation.assert_not_called()

    def test_status_reports_are_cached_only_within_one_request(self):
        calls = []

        @zgua_app.paper_status_report
        def report(args):
            calls.append(dict(args))
            return {"nested": {"value": len(calls)}}

        with zgua_app.app.test_request_context("/api/paper/status"):
            first = report({"limit": "10"})
            first["nested"]["value"] = -1
            self.assertEqual(report({"limit": "10"})["nested"]["value"], 1)
            self.assertEqual(report({"limit": "20"})["nested"]["value"], 2)
        with zgua_app.app.test_request_context("/api/paper/status"):
            self.assertEqual(report({"limit": "10"})["nested"]["value"], 3)
        with zgua_app.app.test_request_context("/api/paper/runtime-status"):
            report({})
            report({})
        report({})
        report({})
        self.assertEqual(len(calls), 7)

    def test_recovery_profit_factor_uses_trade_pnl(self):
        trades = [{"netPnl": 20}, {"pnl": -5}, {"pnl": 0}]
        self.assertEqual(zgua_app.paper_recovery_profit_factor(trades), 4)

    def test_recovery_profit_factor_without_losses(self):
        self.assertEqual(zgua_app.paper_recovery_profit_factor([]), 0)
        self.assertEqual(zgua_app.paper_recovery_profit_factor([{"pnl": 5}]), 99)

    def test_metrics_profit_factor_keeps_its_existing_contract(self):
        self.assertEqual(zgua_app.paper_profit_factor([20], [-5], []), 4)
        self.assertEqual(zgua_app.paper_profit_factor([], [], [{"netPnl": 20}, {"netPnl": -5}]), 4)

    def test_handbrake_with_no_trades_is_read_only(self):
        state = {"closedTrades": [], "equityCurve": []}
        snapshot = zgua_app.paper_handbrake_snapshot(state, {}, False, True)
        self.assertEqual(snapshot["metrics"]["recoveryProfitFactor"], 0)
        self.assertFalse(snapshot["metrics"]["realTradingEnabled"])
        self.assertEqual(state, {"closedTrades": [], "equityCurve": []})

    def test_handbrake_handles_a_recovery_window(self):
        trades = [{"pnl": 20}, {"pnl": -5}] * 5
        snapshot = zgua_app.paper_handbrake_snapshot(
            {"closedTrades": trades, "handbrake": {"state": "PAPER_RECOVERY"}},
            {}, False, True,
        )
        self.assertEqual(snapshot["metrics"]["recoveryProfitFactor"], 4)
        self.assertEqual(snapshot["metrics"]["recoveryTrades"], 10)


if __name__ == "__main__":
    unittest.main()
