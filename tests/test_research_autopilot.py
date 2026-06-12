import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as zgua_app


def patch_autopilot_paths(testcase):
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    testcase.addCleanup(tmp.cleanup)
    patches = [
        patch.object(zgua_app, "RESEARCH_AUTOPILOT_DIR", root),
        patch.object(zgua_app, "RESEARCH_AUTOPILOT_QUEUE_PATH", root / "research-queue.json"),
        patch.object(zgua_app, "RESEARCH_AUTOPILOT_MEMORY_PATH", root / "research-memory.json"),
    ]
    for item in patches:
        item.start()
        testcase.addCleanup(item.stop)
    return root


def candidate_row(strategy="MeanReversion", symbol="BTCUSDT", timeframe="4h", params=None, trades=47, pf=1.4, ret=4.0, tier="STABILITY_WATCH", eligibility="RESEARCH_MORE"):
    params = params or {"emaSlow": 200, "rsiLimit": 35}
    identity = zgua_app.candidate_identity_from_parts(strategy, symbol, timeframe, params, "next-open", 0.02, 0.055, 2)
    return {
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
        "params": params,
        **identity,
        "tier": tier,
        "eligibilityStatus": eligibility,
        "trades": trades,
        "profitFactor": pf,
        "totalReturnPct": ret,
        "maxDrawdownPct": 5,
        "foldPassCount": 3,
        "negativeFoldCount": 0,
        "stressStatus": "RESILIENT",
        "recentWindowStatus": "MIXED",
        "failedGates": [],
    }


def campaign_payload(row):
    return {
        "ok": True,
        "schemaVersion": "research-campaign-v2",
        "candidateIdentityVersion": zgua_app.CANONICAL_CANDIDATE_IDENTITY_VERSION,
        "generatedAt": "2026-06-12T00:00:00+00:00",
        "savedPath": "reports/research-snapshots/autopilot-test.json",
        "search": {"period": "730d"},
        "modules": {
            "stabilityFirstSearch": {
                "summary": {
                    "topCandidates": [row],
                    "bestResearchedCandidate": row,
                    "bestStableCandidate": row,
                    "bestEligibleChallenger": row if row.get("eligibilityStatus") == "CHALLENGER_ELIGIBLE" else {},
                }
            }
        },
        "recommendation": {"action": "TEST"},
        "safety": {
            "promoted": False,
            "configWritten": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "realTradingTouched": False,
        },
    }


class ResearchAutopilotTests(unittest.TestCase):
    def setUp(self):
        patch_autopilot_paths(self)

    def test_queue_deduplicates_exact_jobs(self):
        queue = zgua_app.load_autopilot_queue()
        job = zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "test")
        added, skipped = zgua_app.autopilot_enqueue(queue, [job, {**job, "jobId": "dupe"}])
        self.assertEqual(len(added), 1)
        self.assertEqual(len(skipped), 1)

    def test_rejected_branch_deprioritized(self):
        memory = {
            "branches": [{
                "strategy": "MeanReversion",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "period": "1095d",
                "reasonCategory": "NEGATIVE_RETURN",
                "profitFactor": 0.8,
                "totalReturnPct": -3,
            }],
            "candidates": [],
        }
        jobs, warnings = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=5)
        self.assertTrue(any("Deprioritized rejected branch" in warning for warning in warnings))
        self.assertFalse(any(job.get("strategies") == ["MeanReversion"] and job.get("symbols") == ["BTCUSDT"] and job.get("timeframes") == ["1h"] and job.get("period") == "1095d" for job in jobs))

    def test_promising_but_rare_creates_followups(self):
        memory = {"branches": [{
            "strategy": "MeanReversion",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "period": "730d",
            "reasonCategory": "PROMISING_BUT_RARE",
            "profitFactor": 1.4,
            "totalReturnPct": 4,
            "fullTrades": 47,
        }], "candidates": []}
        jobs, _warnings = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=4)
        self.assertTrue(any(job.get("timeframes") == ["1h"] for job in jobs))
        self.assertTrue(any("ETHUSDT" in job.get("symbols", []) or "SOLUSDT" in job.get("symbols", []) for job in jobs))

    def test_eligible_branch_creates_confirmation(self):
        memory = {"branches": [{
            "strategy": "MeanReversion",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "period": "730d",
            "reasonCategory": "PROMISING_STABLE",
            "profitFactor": 1.5,
        }], "candidates": []}
        jobs, _warnings = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=3)
        self.assertTrue(any(job.get("period") == "1095d" and job.get("includeReproAudit") for job in jobs))

    def test_run_next_updates_queue_and_memory_without_side_effects(self):
        queue = zgua_app.load_autopilot_queue()
        zgua_app.autopilot_enqueue(queue, [zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "test")])
        zgua_app.save_autopilot_queue(queue)
        row = candidate_row()
        with patch.object(zgua_app, "build_research_campaign_runner", return_value=(campaign_payload(row), 200)):
            payload, status = zgua_app.build_research_autopilot_run_next()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["job"]["status"], "DONE")
        self.assertFalse(payload["safety"]["configWritten"])
        memory = zgua_app.load_autopilot_memory()
        self.assertEqual(len(memory["candidates"]), 1)
        self.assertTrue(memory["candidates"][0].get("candidateKey"))

    def test_run_batch_respects_max_jobs(self):
        queue = zgua_app.load_autopilot_queue()
        jobs = [
            zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "a"),
            zgua_app.make_autopilot_job(["PullbackTrend"], ["ETHUSDT"], ["1h"], "365d", "b"),
        ]
        zgua_app.autopilot_enqueue(queue, jobs)
        zgua_app.save_autopilot_queue(queue)
        with patch.object(zgua_app, "build_research_campaign_runner", return_value=(campaign_payload(candidate_row()), 200)):
            payload, status = zgua_app.build_research_autopilot_run_batch({"maxJobs": 1})
        self.assertEqual(status, 200)
        self.assertEqual(payload["jobsAttempted"], 1)
        self.assertLessEqual(payload["jobsAttempted"], 1)

    def test_malformed_result_fails_safely(self):
        queue = zgua_app.load_autopilot_queue()
        zgua_app.autopilot_enqueue(queue, [zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "test")])
        zgua_app.save_autopilot_queue(queue)
        with patch.object(zgua_app, "build_research_campaign_runner", return_value=({"ok": False, "error": "bad"}, 502)):
            payload, status = zgua_app.build_research_autopilot_run_next()
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["job"]["status"], "FAILED")
        self.assertFalse(payload["safety"]["paperStateChanged"])

    def test_status_and_summary_safety_flags(self):
        status = zgua_app.build_research_autopilot_status()
        summary = zgua_app.build_research_autopilot_summary()
        self.assertTrue(status["safety"]["researchOnly"])
        self.assertFalse(status["safety"]["promotionAttempted"])
        self.assertIn("Safety:", summary["summaryText"])


if __name__ == "__main__":
    unittest.main()
