import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app as zgua_app
from scripts import paper_run_once


PARAMS_A = {
    "atrMultiplier": 2.8,
    "emaFast": 20,
    "emaSlow": 80,
    "emaTrend": 150,
    "cooldownBars": 3,
    "minHoldBars": 3,
    "rsiMin": 45,
    "rsiMax": 76,
    "useRsiFilter": False,
    "regimeMode": "looseBtcBull",
}

PARAMS_B = {
    "atrMultiplier": 1.8,
    "emaFast": 8,
    "emaSlow": 40,
    "emaTrend": 80,
    "cooldownBars": 0,
    "minHoldBars": 0,
    "rsiMin": 35,
    "rsiMax": 68,
    "useRsiFilter": True,
    "regimeMode": "looseBtcBull",
}


def identity(params):
    return zgua_app.candidate_identity_from_parts(
        "SimpleAtrTrendV2",
        "ETHUSDT",
        "1h",
        params,
        "next-open",
        0.02,
        0.055,
        2,
    )


class ResearchOrchestrationTests(unittest.TestCase):
    def test_campaign_deep_validation_uses_selected_candidate_params(self):
        id_a = identity(PARAMS_A)
        id_b = identity(PARAMS_B)
        self.assertNotEqual(id_a["paramsHash"], id_b["paramsHash"])
        calls = []

        def fake_stability(_args):
            def row(params, rank):
                row_identity = identity(params)
                return {
                    "rank": rank,
                    "strategy": "SimpleAtrTrendV2",
                    "symbol": "ETHUSDT",
                    "timeframe": "1h",
                    "params": params,
                    **row_identity,
                    "fullPeriod": {"trades": rank, "profitFactor": rank, "totalReturnPct": rank, "maxDrawdownPct": rank},
                    "walkForward": {"foldPassCount": rank, "negativeFoldCount": 0},
                    "eligibility": {"status": "RESEARCH_MORE"},
                }

            return {
                "ok": True,
                "topCandidates": [row(PARAMS_A, 1), row(PARAMS_B, 2)],
                "search": {},
                "verdict": {"action": "TEST"},
            }, 200

        def fake_validation(args):
            calls.append(args)
            params, error, source = zgua_app.research_params_from_args({}, args)
            self.assertIsNone(error)
            params_meta = zgua_app.validation_params_meta(params, source, identity(params))
            return {
                "ok": True,
                **params_meta,
                "stress": {"status": "PASS"},
                "stability": {"status": "PASS"},
                "summary": {"regimeDependencyStatus": "LOW"},
                "warnings": [],
            }, 200

        with patch.object(zgua_app, "build_research_stability_first_challenger_search", side_effect=fake_stability), \
             patch.object(zgua_app, "build_research_candidate_leaderboard", return_value=({"ok": True, "rows": [], "summary": {}}, 200)), \
             patch.object(zgua_app, "build_research_activity_lab", return_value=({"ok": True, "rows": [], "summary": {}}, 200)), \
             patch.object(zgua_app, "build_research_fee_slippage_stress", side_effect=fake_validation), \
             patch.object(zgua_app, "build_research_walk_forward_review", side_effect=fake_validation), \
             patch.object(zgua_app, "build_research_regime_breakdown", side_effect=fake_validation):
            payload, status = zgua_app.build_research_campaign_runner({"validateTop": "2", "topN": "2", "maxCombosPerStrategy": "1"})

        self.assertEqual(status, 200)
        hashes = [row["paramsHash"] for row in payload["modules"]["deepValidation"]]
        self.assertEqual(hashes, [id_a["paramsHash"], id_b["paramsHash"]])
        self.assertEqual(len(set(hashes)), 2)
        passed_hashes = {
            zgua_app.short_hash(zgua_app.normalized_candidate_params(zgua_app.parse_json_arg(call, "baseParams")[0]))
            for call in calls
        }
        self.assertEqual(passed_hashes, {id_a["paramsHash"], id_b["paramsHash"]})

    def test_ledger_dedupes_sections_but_counts_reports(self):
        row = {
            "strategy": "SimpleAtrTrendV2",
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "params": PARAMS_A,
            **identity(PARAMS_A),
            "tier": "STABILITY_WATCH",
            "eligibilityStatus": "RESEARCH_MORE",
        }
        payload = {
            "generatedAt": "2026-06-11T00:00:00+00:00",
            "modules": {
                "stabilityFirstSearch": {
                    "summary": {
                        "topCandidates": [row],
                        "bestResearchedCandidate": row,
                        "bestStableCandidate": row,
                        "bestEligibleChallenger": row,
                    }
                }
            },
            "bestRawCandidate": row,
        }
        entries_one = zgua_app.candidate_ledger_rows_from_payload(payload, "reports/research-snapshots/a.json")
        rows_one = zgua_app.summarize_candidate_ledger(entries_one, active_key="-")
        self.assertEqual(rows_one[0]["campaignSightings"], 1)
        self.assertGreaterEqual(rows_one[0]["sectionAppearances"], 5)

        entries_two = entries_one + zgua_app.candidate_ledger_rows_from_payload(payload, "reports/research-snapshots/b.json")
        rows_two = zgua_app.summarize_candidate_ledger(entries_two, active_key="-")
        self.assertEqual(rows_two[0]["campaignSightings"], 2)

        row_b = {**row, "params": PARAMS_B, **identity(PARAMS_B)}
        entries_diff = entries_one + zgua_app.candidate_ledger_rows_from_payload({"generatedAt": "2026", "topCandidates": [row_b]}, "reports/research-snapshots/c.json")
        rows_diff = zgua_app.summarize_candidate_ledger(entries_diff, active_key="-")
        self.assertEqual(len(rows_diff), 2)

    def test_result_diff_context_comparability_suppresses_score_deltas(self):
        current = zgua_app.research_context_for_diff({"search": {"period": "365d"}, "executionContext": {"takerFeePct": 0.055}})
        previous = zgua_app.research_context_for_diff({"search": {"period": "180d"}, "executionContext": {"takerFeePct": 0.055}})
        comparability = zgua_app.compare_research_contexts(current, previous)
        self.assertIn(comparability["status"], {"PARTIALLY_COMPARABLE", "NOT_COMPARABLE"})
        row = zgua_app.compact_diff_row(
            "k",
            {"strategy": "S", "symbol": "ETH", "timeframe": "1h", "stabilityScore": 10},
            {"strategy": "S", "symbol": "ETH", "timeframe": "1h", "stabilityScore": 2},
            score_deltas_allowed=comparability["scoreDeltasAllowed"],
        )
        self.assertIsNone(row["stabilityScoreDelta"])

    def test_promotion_checklist_normalizes_actual_stability_shape(self):
        normalized = zgua_app.normalize_stability_status({"validation": {"status": "PASS", "windows": []}})
        self.assertEqual(normalized["status"], "PASS")
        self.assertEqual(normalized["statusSource"], "validation.status")
        unknown = zgua_app.normalize_stability_status({"validation": {}})
        self.assertEqual(unknown["status"], "UNKNOWN")


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def get_json(self):
        return self.payload

    def get_data(self, as_text=False):
        return "" if as_text else b""


class FakeClient:
    def __init__(self, readiness_status="READY", useful_now=True, paper_enabled=True, real_enabled=False, stop_status="OK"):
        self.readiness_status = readiness_status
        self.useful_now = useful_now
        self.paper_enabled = paper_enabled
        self.real_enabled = real_enabled
        self.stop_status = stop_status
        self.posts = []

    def get(self, path):
        if path == "/api/paper/status":
            return FakeResponse({"paperEnabled": self.paper_enabled, "realTradingEnabled": self.real_enabled})
        if path == "/api/paper/stop-rules":
            return FakeResponse({"status": self.stop_status})
        if path == "/api/paper/tick-readiness":
            return FakeResponse({"tickReadiness": {"status": self.readiness_status, "usefulNow": self.useful_now}})
        if path == "/api/paper/observation-targets":
            return FakeResponse({"nextAction": {}})
        if path == "/api/paper/observation-report":
            return FakeResponse({"verdict": {"nextAction": {}}})
        return FakeResponse({})

    def post(self, path):
        self.posts.append(path)
        return FakeResponse({"ok": True, "tickRan": True, "paperEnabled": self.paper_enabled, "realTradingEnabled": self.real_enabled})


class GuidedPaperRunnerTests(unittest.TestCase):
    def test_guided_runner_skips_disabled_paper(self):
        client = FakeClient(paper_enabled=False, readiness_status="DISABLED", useful_now=False)
        payload = paper_run_once.guided_run_once(client)
        self.assertFalse(payload["postAttempted"])
        self.assertEqual(client.posts, [])

    def test_guided_runner_skips_no_new_candle(self):
        client = FakeClient(readiness_status="WAIT_FOR_NEXT_CANDLE", useful_now=False)
        payload = paper_run_once.guided_run_once(client)
        self.assertFalse(payload["postAttempted"])
        self.assertEqual(client.posts, [])

    def test_guided_runner_allows_useful_candle(self):
        client = FakeClient(readiness_status="READY", useful_now=True)
        payload = paper_run_once.guided_run_once(client)
        self.assertTrue(payload["postAttempted"])
        self.assertEqual(client.posts, ["/api/paper/run-once"])

    def test_guided_runner_skips_real_trading_and_stop_block(self):
        real = FakeClient(real_enabled=True)
        self.assertFalse(paper_run_once.guided_run_once(real)["postAttempted"])
        blocked = FakeClient(stop_status="BLOCKED")
        self.assertFalse(paper_run_once.guided_run_once(blocked)["postAttempted"])

    def test_guided_runner_skips_malformed_readiness(self):
        client = FakeClient(readiness_status=None, useful_now=False)
        payload = paper_run_once.guided_run_once(client)
        self.assertFalse(payload["postAttempted"])


class OperationalFreshnessTests(unittest.TestCase):
    def test_runtime_status_recomputes_active_data_freshness_from_candle_time(self):
        now = datetime.now(timezone.utc)
        old_candle = int(now.timestamp()) - 12 * 3600
        candidate = {
            "enabled": True,
            "paperEnabled": True,
            "source": "bybit",
            "strategy": "EmaBounceV2",
            "symbols": [{"symbol": "BTCUSDT", "interval": "4h", "mode": "active"}],
        }
        state = {
            "updatedAt": now.isoformat(),
            "lastProcessedCandleTime": {"BTCUSDT:4h": old_candle},
            "freshness": {
                "BTCUSDT:4h": {
                    "latestCandleTime": old_candle,
                    "latestClosedCandleAgeSeconds": 60,
                    "staleThresholdSeconds": 36000,
                    "isStale": False,
                }
            },
        }

        def fake_read_json(path, fallback):
            if str(path).endswith("paper-state.json"):
                return state
            return fallback

        with patch.object(zgua_app, "load_paper_candidate_config", return_value=candidate), \
             patch.object(zgua_app, "read_json_file", side_effect=fake_read_json), \
             patch.object(zgua_app, "read_jsonl_tail", return_value=[]), \
             patch.object(zgua_app, "paper_real_trading_enabled", return_value=(False, "")):
            payload = zgua_app.build_paper_runtime_status({})

        freshness = payload["operationalFreshness"]["activeMarketData"]
        self.assertEqual(freshness["status"], "STALE")
        self.assertGreater(freshness["latestClosedCandleAgeSeconds"], freshness["staleThresholdSeconds"])
        self.assertEqual(payload["nextAction"]["action"], "REFRESH_ACTIVE_MARKET_DATA")

    def test_system_health_exposes_operational_freshness_checks(self):
        payload = zgua_app.build_system_health(quick=True)
        self.assertIn("freshness", payload)
        check_ids = {check["id"] for check in payload["checks"]}
        self.assertIn("paper_state_freshness", check_ids)
        self.assertIn("research_memory_freshness", check_ids)

    def test_candidate_evidence_status_reports_missing_baseline_package_and_dossier(self):
        candidate = {
            "enabled": True,
            "source": "bybit",
            "strategy": "EmaBounceV2",
            "symbols": [{"symbol": "BTCUSDT", "interval": "4h", "mode": "active"}],
            "paramsHash": "abc123",
        }
        evidence = zgua_app.candidate_evidence_status(candidate)
        self.assertEqual(evidence["status"], "INCOMPLETE")
        self.assertIn("expected baseline metrics", evidence["missing"])
        self.assertIn("disabled review package", evidence["missing"])
        self.assertIn("candidate dossier", evidence["missing"])

    def test_candidate_evidence_status_links_matching_package_and_dossier(self):
        candidate = {
            "enabled": True,
            "source": "bybit",
            "strategy": "EmaBounceV2",
            "symbols": [{"symbol": "BTCUSDT", "interval": "4h", "mode": "active"}],
            "paramsHash": "abc123",
            "candidateKey": "candidate-key",
            "promotedFromOptimization": {
                "test": {"trades": 42, "profitFactor": 1.4, "totalReturn": 3.2, "maxDrawdown": 2.1, "winRate": 40}
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "review"
            deploy_dir = root / "deploy"
            dossier = root / "dossier.md"
            review_dir.mkdir()
            deploy_dir.mkdir()
            dossier.write_text("# dossier\n", encoding="utf-8")
            package_path = review_dir / zgua_app.autopilot_review_candidate_filename("EmaBounceV2", "BTCUSDT", "4h")
            package_path.write_text(json.dumps({
                "status": "DISABLED_REVIEW_ONLY",
                "candidateIdentity": {
                    "strategy": "EmaBounceV2",
                    "symbol": "BTCUSDT",
                    "timeframe": "4h",
                    "paramsHash": "abc123",
                    "candidateKey": "candidate-key",
                },
                "dossierPath": str(dossier),
            }), encoding="utf-8")
            with patch.object(zgua_app, "PAPER_CANDIDATE_REVIEW_DIR", review_dir), \
                 patch.object(zgua_app, "DEPLOY_REVIEW_CANDIDATE_DIR", deploy_dir):
                evidence = zgua_app.candidate_evidence_status(candidate)
        self.assertEqual(evidence["status"], "READY")
        self.assertTrue(evidence["baselineReady"])
        self.assertTrue(evidence["reviewPackageReady"])
        self.assertTrue(evidence["dossierReady"])


if __name__ == "__main__":
    unittest.main()
