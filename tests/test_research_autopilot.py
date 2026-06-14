import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
        patch.object(zgua_app, "RESEARCH_DOSSIER_DIR", root / "research-dossiers"),
        patch.object(zgua_app, "PAPER_CANDIDATE_REVIEW_DIR", root / "paper-candidates"),
        patch.object(zgua_app, "DEPLOY_REVIEW_CANDIDATE_DIR", root / "review-candidates"),
        patch.object(zgua_app, "candidate_ledger_source_files", return_value=[]),
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


def no_research_lead_payload(strategy="MomentumContinuation", symbols=None, timeframes=None, period="365d"):
    symbols = symbols or ["BTCUSDT"]
    timeframes = timeframes or ["1h"]
    return {
        "ok": True,
        "schemaVersion": "research-campaign-v2",
        "candidateIdentityVersion": zgua_app.CANONICAL_CANDIDATE_IDENTITY_VERSION,
        "generatedAt": "2026-06-12T00:00:00+00:00",
        "savedPath": "reports/research-snapshots/no-lead-test.json",
        "search": {
            "period": period,
            "strategies": strategy,
            "symbols": symbols,
            "timeframes": timeframes,
        },
        "modules": {
            "stabilityFirstSearch": {
                "summary": {
                    "topCandidates": [],
                    "bestResearchedCandidate": {},
                    "bestStableCandidate": {},
                    "bestEligibleChallenger": {},
                }
            }
        },
        "topCandidates": [],
        "recommendation": {
            "action": "NO_RESEARCH_LEAD",
            "reason": "No campaign candidate produced enough evidence for deeper review.",
        },
        "safety": {
            "promoted": False,
            "configWritten": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "realTradingTouched": False,
        },
    }


def branch(strategy, symbol, timeframe, category, period="365d", pf=0.8, ret=-2, trades=40, stress=None, recent=None, seen="2026-06-12T00:00:00+00:00"):
    return {
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "reasonCategory": category,
        "profitFactor": pf,
        "totalReturnPct": ret,
        "fullTrades": trades,
        "stressStatus": stress,
        "recentWindowStatus": recent,
        "lastSeenAt": seen,
    }


def stable_branch(strategy="EmaBounceV2", symbol="BTCUSDT", timeframe="4h", period="730d", pf=2.5385, ret=8.1011, trades=49, seen="2026-06-12T00:00:00+00:00"):
    row = branch(strategy, symbol, timeframe, "PROMISING_STABLE", period=period, pf=pf, ret=ret, trades=trades, stress="SURVIVES_MODERATE_STRESS", recent="RECENTLY_CONSISTENT", seen=seen)
    row.update({
        "candidateKey": f"candidate-identity-v1|{strategy}|{symbol}|{timeframe}|params-{period}|exec",
        "paramsHash": f"params-{period}",
        "eligibilityStatus": "CHALLENGER_ELIGIBLE",
        "bestTier": "CHALLENGER_ELIGIBLE",
        "foldPassCount": 4,
        "negativeFolds": 0,
        "maxDrawdownPct": 2.667,
        "failedGates": [],
    })
    return row


def detailed_source_report(candidate):
    return {
        "ok": True,
        "generatedAt": "2026-06-12T00:00:00+00:00",
        "search": {"period": candidate["period"], "strategies": candidate["strategy"], "symbols": [candidate["symbol"]], "timeframes": [candidate["timeframe"]]},
        "modules": {
            "stabilityFirstSearch": {
                "summary": {
                    "search": {"reproducibilityAudited": 3},
                    "benchmark": {
                        "strategy": "SimpleAtrTrendV2",
                        "symbol": "ETHUSDT",
                        "timeframe": "1h",
                        "stabilityScore": -10,
                        "negativeFoldCount": 3,
                    },
                    "bestEligibleChallenger": {
                        "strategy": candidate["strategy"],
                        "symbol": candidate["symbol"],
                        "timeframe": candidate["timeframe"],
                        "candidateKey": candidate["candidateKey"],
                        "paramsHash": candidate["paramsHash"],
                        "params": {"emaBounceAtr": 0.8, "rsiReclaimLevel": 53},
                        "stabilityScore": 44,
                        "negativeFoldCount": 0,
                        "returnConcentrationPct": 42.5,
                        "stressStatus": "SURVIVES_MODERATE_STRESS",
                        "recentWindowStatus": "RECENTLY_CONSISTENT",
                        "reproducibilityStatus": "REPRODUCIBLE",
                        "failedGates": [],
                    },
                    "topCandidates": [],
                }
            },
            "deepValidation": [{
                "candidateKey": candidate["candidateKey"],
                "paramsHash": candidate["paramsHash"],
                "feeSlippageStress": {
                    "status": "RESILIENT",
                    "baseline": {"totalReturnPct": 8.1, "profitFactor": 2.5, "trades": 49},
                    "worstPassingScenario": {"scenario": "highStress", "totalReturnPct": 6.4, "profitFactor": 2.1, "trades": 49, "degradationVsBaseline": {"returnDiffPct": -1.7}},
                    "firstFailureScenario": None,
                    "recommendation": {"action": "KEEP_CURRENT_COST_MODEL"},
                },
                "walkForward": {
                    "folds": [{"fold": 1, "startTime": "2025-01-01", "endTime": "2025-06-01", "status": "PASS", "trades": 12, "totalReturnPct": 1.2, "profitFactor": 1.8, "maxDrawdownPct": 0.9}],
                    "recentWindows": [{"label": "90d", "status": "PASS", "trades": 8, "totalReturnPct": 0.7, "profitFactor": 1.6, "maxDrawdownPct": 0.4}],
                    "stability": {"status": "PASS"},
                },
                "warnings": [],
            }],
        },
        "recommendation": {"action": "REVIEW_STABLE_CHALLENGER"},
    }


FORBIDDEN_AUTOPILOT_CALLS = [
    "write_candidate_config",
    "write_paper_candidate_config",
    "enable_paper_simulation_controlled",
    "disable_paper_simulation_controlled",
    "run_paper_tick_once",
    "run_paper_once_controlled",
    "auto_promote_candidate_if_allowed",
    "build_candidate_promotion_preview",
]


class ResearchAutopilotTests(unittest.TestCase):
    def setUp(self):
        patch_autopilot_paths(self)
        self.paper_candidate_before = dict(zgua_app.load_paper_candidate_config())
        self.paper_enabled_before = zgua_app.canonical_paper_enabled(self.paper_candidate_before)
        self.real_enabled_before = zgua_app.paper_real_trading_enabled()[0]

    def assert_autopilot_safety(self, payload):
        safety = payload.get("safety") or {}
        self.assertTrue(safety.get("researchOnly"))
        self.assertEqual(safety.get("paperEnabled"), self.paper_enabled_before)
        self.assertEqual(safety.get("realTradingEnabled"), self.real_enabled_before)
        self.assertFalse(safety.get("promotionAttempted"))
        self.assertFalse(safety.get("configWritten"))
        self.assertFalse(safety.get("paperStateChanged"))
        self.assertFalse(safety.get("paperTickRan"))
        self.assertFalse(safety.get("liveOrdersTouched"))
        self.assertFalse(safety.get("realOrderFunctionsCalled"))
        self.assertFalse(safety.get("activePaperCandidateMutated"))
        self.assertFalse(safety.get("riskSettingsChanged"))
        self.assertFalse(safety.get("apiKeyPathCreated"))
        after = zgua_app.load_paper_candidate_config()
        self.assertEqual(zgua_app.canonical_paper_enabled(after), self.paper_enabled_before)
        for field in ("strategy", "riskPct", "maxOpenTrades", "maxNotional", "maxNotionalPerTrade"):
            self.assertEqual(after.get(field), self.paper_candidate_before.get(field))

    def forbidden_boundary_patches(self):
        patches = []
        for name in FORBIDDEN_AUTOPILOT_CALLS:
            if hasattr(zgua_app, name):
                patches.append(patch.object(zgua_app, name, side_effect=AssertionError(f"Autopilot must not call {name}")))
        return patches

    def run_with_forbidden_boundaries(self, func, *args, **kwargs):
        patches = self.forbidden_boundary_patches()
        started = [item.start() for item in patches]
        try:
            return func(*args, **kwargs)
        finally:
            for mocked in started:
                self.assertFalse(mocked.called)
            for item in reversed(patches):
                item.stop()

    def test_queue_deduplicates_exact_jobs(self):
        queue = zgua_app.load_autopilot_queue()
        job = zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "test")
        added, skipped = zgua_app.autopilot_enqueue(queue, [job, {**job, "jobId": "dupe"}])
        self.assertEqual(len(added), 1)
        self.assertEqual(len(skipped), 1)

    def test_rejected_branch_deprioritized(self):
        memory = {
            "branches": [{
                "strategy": "SimpleAtrTrendV2",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "period": "365d",
                "reasonCategory": "NEGATIVE_RETURN",
                "profitFactor": 0.8,
                "totalReturnPct": -3,
            }],
            "candidates": [],
        }
        jobs, warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=5)
        self.assertTrue(any("Deprioritized rejected branch" in warning for warning in warnings))
        self.assertFalse(any(job.get("strategies") == ["SimpleAtrTrendV2"] and job.get("symbols") == ["BTCUSDT"] and job.get("timeframes") == ["1h"] and job.get("period") == "365d" for job in jobs))
        self.assertTrue(any(item.get("skipReason") in {"rejected_branch", "recently_tested_rejected_branch"} for item in skipped))

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
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=4)
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
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=3)
        self.assertTrue(any(job.get("period") == "1095d" and job.get("includeReproAudit") for job in jobs))

    def test_broad_search_does_not_requeue_rejected_exact_branches(self):
        memory = {"branches": [
            {"strategy": "SimpleAtrTrendV2", "symbol": "ETHUSDT", "timeframe": "1h", "period": "365d", "reasonCategory": "REJECTED", "profitFactor": 1.2, "lastSeenAt": "2026-06-12T00:00:00+00:00"},
            {"strategy": "SimpleAtrTrendV2", "symbol": "BTCUSDT", "timeframe": "1h", "period": "365d", "reasonCategory": "LOW_PROFIT_FACTOR", "profitFactor": 0.9, "lastSeenAt": "2026-06-12T00:00:00+00:00"},
            {"strategy": "BreakoutRetestV2", "symbol": "ETHUSDT", "timeframe": "4h", "period": "365d", "reasonCategory": "PROMISING_BUT_RARE", "profitFactor": 2.2, "totalReturnPct": 4, "fullTrades": 22, "lastSeenAt": "2026-06-12T00:00:00+00:00"},
            {"strategy": "BreakoutRetestV2", "symbol": "ETHUSDT", "timeframe": "1h", "period": "365d", "reasonCategory": "NEGATIVE_RETURN", "profitFactor": 0.8, "lastSeenAt": "2026-06-12T00:00:00+00:00"},
        ], "candidates": []}
        jobs, warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=12)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in jobs)) if jobs else set()
        self.assertNotIn("SimpleAtrTrendV2|ETHUSDT|1h|365d", planned_keys)
        self.assertNotIn("SimpleAtrTrendV2|BTCUSDT|1h|365d", planned_keys)
        self.assertNotIn("BreakoutRetestV2|ETHUSDT|1h|365d", planned_keys)
        self.assertIn("SimpleAtrTrendV2|BTCUSDT|4h|365d", planned_keys)
        self.assertTrue(any("SimpleAtrTrendV2 ETHUSDT 1h 365d" in warning for warning in warnings))
        skipped_keys = {item.get("branchKey") for item in skipped}
        self.assertIn("SimpleAtrTrendV2|ETHUSDT|1h|365d", skipped_keys)
        self.assertIn("SimpleAtrTrendV2|BTCUSDT|1h|365d", skipped_keys)
        self.assertIn("BreakoutRetestV2|ETHUSDT|1h|365d", skipped_keys)

    def test_existing_queued_branch_is_not_duplicated_by_planner(self):
        memory = {"branches": [{"strategy": "SimpleAtrTrendV2", "symbol": "ETHUSDT", "timeframe": "4h", "period": "365d", "reasonCategory": "TOO_FEW_TRADES"}], "candidates": []}
        queue = {"jobs": [zgua_app.make_autopilot_job(["SimpleAtrTrendV2"], ["BTCUSDT"], ["1h"], "365d", "already queued", generated_by="broad_search")]}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, queue, max_jobs=8)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in jobs)) if jobs else set()
        self.assertNotIn("SimpleAtrTrendV2|BTCUSDT|1h|365d", planned_keys)
        self.assertTrue(any(item.get("skipReason") == "duplicate_queued_job" and item.get("branchKey") == "SimpleAtrTrendV2|BTCUSDT|1h|365d" for item in skipped))

    def test_existing_queued_rejected_branch_is_marked_skipped_before_status(self):
        memory = {"branches": [{
            "strategy": "SimpleAtrTrendV2",
            "symbol": "ETHUSDT",
            "timeframe": "1h",
            "period": "365d",
            "reasonCategory": "REJECTED",
        }], "candidates": [], "sourceReports": []}
        queue = zgua_app.load_autopilot_queue()
        zgua_app.autopilot_enqueue(queue, [zgua_app.make_autopilot_job(["SimpleAtrTrendV2"], ["ETHUSDT"], ["1h"], "365d", "old queued branch", generated_by="broad_search")])
        zgua_app.save_autopilot_queue(queue)
        zgua_app.save_autopilot_memory(memory)
        status = zgua_app.build_research_autopilot_status()
        self.assertEqual(status["queue"]["counts"]["QUEUED"], 0)
        self.assertEqual(status["queue"]["counts"]["SKIPPED"], 1)
        self.assertTrue(any(item.get("branchKey") == "SimpleAtrTrendV2|ETHUSDT|1h|365d" for item in status["queue"]["skippedDeprioritizedJobs"]))

    def test_repeated_negative_family_cools_down_and_blocks_broad_search(self):
        memory = {"branches": [
            branch("PullbackTrend", "BTCUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "BTCUSDT", "4h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "4h", "LOW_PROFIT_FACTOR"),
        ], "candidates": []}
        families = zgua_app.autopilot_family_summary(memory)
        family = next(row for row in families if row["strategy"] == "PullbackTrend")
        self.assertIn(family["familyStatus"], {"COOL_DOWN", "REJECTED_FAMILY"})
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=20)
        self.assertFalse(any(job.get("strategy") == "PullbackTrend" for job in jobs))
        self.assertTrue(any(item.get("skipReason") == "cooled_strategy_family" for item in skipped))

    def test_repeated_stress_collapse_family_cools_down(self):
        memory = {"branches": [
            branch("PullbackTrend", "BTCUSDT", "1h", "STRESS_COLLAPSE", stress="COLLAPSES_UNDER_STRESS"),
            branch("PullbackTrend", "BTCUSDT", "4h", "STRESS_COLLAPSE", stress="COLLAPSES_UNDER_STRESS"),
            branch("PullbackTrend", "ETHUSDT", "1h", "STRESS_COLLAPSE", stress="COLLAPSES_UNDER_STRESS"),
            branch("PullbackTrend", "ETHUSDT", "4h", "TOO_FEW_TRADES", pf=1.3, ret=2, trades=18),
        ], "candidates": []}
        family = next(row for row in zgua_app.autopilot_family_summary(memory) if row["strategy"] == "PullbackTrend")
        self.assertEqual(family["familyStatus"], "COOL_DOWN")

    def test_promising_rare_family_gets_period_confirmation(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN"),
        ], "candidates": []}
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=5)
        self.assertTrue(any(job.get("generatedBy") == "rare_period_confirmation" and job.get("period") == "730d" for job in jobs))
        family = next(row for row in zgua_app.autopilot_family_summary(memory) if row["strategy"] == "BreakoutRetestV2")
        self.assertEqual(family["familyStatus"], "PROMISING_BUT_RARE")

    def test_rejected_lower_timeframe_is_not_requeued_at_wider_period(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": []}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=8)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in jobs)) if jobs else set()
        self.assertNotIn("BreakoutRetestV2|ETHUSDT|1h|730d", planned_keys)
        skip = next(item for item in skipped if item.get("skipReason") == "rejected_lower_timeframe_period_retry")
        self.assertEqual(skip.get("branchKey"), "BreakoutRetestV2|ETHUSDT|1h|365d")
        self.assertIn("ETHUSDT 1h 365d was already rejected as NEGATIVE_RETURN", skip.get("detail", ""))

    def test_force_branch_can_retry_rejected_lower_timeframe_period(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": []}
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=1, force_branch="BreakoutRetestV2:ETHUSDT:1h:730d")
        self.assertEqual(jobs[0].get("generatedBy"), "forced_branch")
        self.assertIn("BreakoutRetestV2|ETHUSDT|1h|730d", zgua_app.autopilot_job_branch_keys(jobs[0]))

    def test_failed_parent_confirmation_blocks_rare_lower_timeframe(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "BNBUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=2.4, ret=6, trades=25),
            branch("BreakoutRetestV2", "BNBUSDT", "4h", "NEGATIVE_RETURN", period="1095d", pf=0.9, ret=-4, trades=25),
        ], "candidates": []}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=8)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in jobs)) if jobs else set()
        self.assertNotIn("BreakoutRetestV2|BNBUSDT|1h|730d", planned_keys)
        skip = next(item for item in skipped if item.get("skipReason") == "failed_parent_period_confirmation")
        self.assertEqual(skip.get("branchKey"), "BreakoutRetestV2|BNBUSDT|4h|1095d")
        self.assertIn("BNBUSDT 4h 1095d failed as NEGATIVE_RETURN", skip.get("detail", ""))

    def test_force_branch_can_override_failed_parent_confirmation(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "BNBUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=2.4, ret=6, trades=25),
            branch("BreakoutRetestV2", "BNBUSDT", "4h", "NEGATIVE_RETURN", period="1095d", pf=0.9, ret=-4, trades=25),
        ], "candidates": []}
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=1, force_branch="BreakoutRetestV2:BNBUSDT:1h:730d")
        self.assertEqual(jobs[0].get("generatedBy"), "forced_branch")
        self.assertIn("BreakoutRetestV2|BNBUSDT|1h|730d", zgua_app.autopilot_job_branch_keys(jobs[0]))

    def test_same_timeframe_wider_period_confirmation_still_allowed_before_failure(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": []}
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=8)
        self.assertTrue(any(job.get("generatedBy") == "rare_period_confirmation" and job.get("period") == "1095d" and job.get("timeframes") == ["4h"] for job in jobs))

    def test_already_tested_exact_branch_is_not_requeued(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="365d", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=1.2, ret=1, trades=34, recent="RECENTLY_WEAK"),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": []}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=8)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in jobs)) if jobs else set()
        self.assertNotIn("BreakoutRetestV2|ETHUSDT|4h|730d", planned_keys)
        skip = next(item for item in skipped if item.get("skipReason") == "already_tested_branch")
        self.assertEqual(skip.get("branchKey"), "BreakoutRetestV2|ETHUSDT|4h|730d")
        self.assertIn("PROMISING_BUT_RARE / RECENTLY_WEAK", skip.get("detail", ""))

    def test_reset_queue_does_not_allow_memory_tested_branch_to_requeue(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="365d", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=1.2, ret=1, trades=34, recent="RECENTLY_WEAK"),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        queue = zgua_app.load_autopilot_queue()
        zgua_app.autopilot_enqueue(queue, [zgua_app.make_autopilot_job(["BreakoutRetestV2"], ["ETHUSDT"], ["4h"], "730d", "old queued", generated_by="rare_period_confirmation")])
        zgua_app.save_autopilot_queue(queue)

        reset, reset_status = zgua_app.build_research_autopilot_reset_queue({"confirm": True})
        self.assertEqual(reset_status, 200)
        self.assertEqual(reset["queue"]["length"], 0)

        payload, status = zgua_app.build_research_autopilot_plan({"maxJobs": 8})
        self.assertEqual(status, 200)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in payload["addedJobs"])) if payload["addedJobs"] else set()
        self.assertNotIn("BreakoutRetestV2|ETHUSDT|4h|730d", planned_keys)
        self.assertTrue(any(item.get("skipReason") == "already_tested_branch" and item.get("branchKey") == "BreakoutRetestV2|ETHUSDT|4h|730d" for item in payload["skippedJobs"]))

    def test_force_branch_can_override_already_tested_branch(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=1.2, ret=1, trades=34, recent="RECENTLY_WEAK"),
        ], "candidates": []}
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=1, force_branch="BreakoutRetestV2:ETHUSDT:4h:730d")
        self.assertEqual(jobs[0].get("generatedBy"), "forced_branch")
        self.assertIn("BreakoutRetestV2|ETHUSDT|4h|730d", zgua_app.autopilot_job_branch_keys(jobs[0]))

    def test_new_conservative_trend_branches_still_queue_after_reset(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="365d", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=1.2, ret=1, trades=34, recent="RECENTLY_WEAK"),
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "RECENTLY_WEAK", period="1095d", pf=1.1, ret=1, trades=35),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        reset, reset_status = zgua_app.build_research_autopilot_reset_queue({"confirm": True})
        self.assertEqual(reset_status, 200)
        payload, status = zgua_app.build_research_autopilot_plan({"maxJobs": 8})
        self.assertEqual(status, 200)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in payload["addedJobs"])) if payload["addedJobs"] else set()
        self.assertTrue(any(key.startswith(("MomentumContinuation|", "RangeExpansionV2|", "EmaBounceV2|", "VolatilitySqueezeBreakout|")) for key in planned_keys))

    def test_failed_1095d_confirmation_prefers_new_uncooled_families(self):
        memory = {"branches": [
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "PROMISING_BUT_RARE", period="730d", pf=2.4, ret=6, trades=22),
            branch("BreakoutRetestV2", "ETHUSDT", "4h", "RECENTLY_WEAK", period="1095d", pf=1.1, ret=1, trades=35),
            branch("BreakoutRetestV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
            branch("RelativeStrengthV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": []}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=6)
        first_broad = next(job for job in jobs if job.get("generatedBy") == "broad_search")
        self.assertIn(first_broad.get("strategy"), {
            "MomentumContinuation",
            "RangeExpansionV2",
            "EmaBounceV2",
            "VolatilitySqueezeBreakout",
        })
        self.assertGreaterEqual(first_broad.get("priority"), max(job.get("priority", 0) for job in jobs if job.get("generatedBy") in {"rare_symbol_expansion", "rare_lower_timeframe"}))
        self.assertTrue(any(item.get("skipReason") == "failed_parent_period_confirmation" for item in skipped))

    def test_relative_strength_rejected_family_blocks_normal_broad_search(self):
        memory = {"branches": [
            branch("RelativeStrengthV2", "ETHUSDT", "1h", "NEGATIVE_RETURN", period="365d"),
        ], "candidates": []}
        family = next(row for row in zgua_app.autopilot_family_summary(memory) if row["strategy"] == "RelativeStrengthV2")
        self.assertEqual(family["familyStatus"], "REJECTED_FAMILY")
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=80)
        self.assertFalse(any(job.get("strategy") == "RelativeStrengthV2" for job in jobs))
        self.assertTrue(any(item.get("skipReason") == "cooled_strategy_family" and item.get("strategy") == "RelativeStrengthV2" for item in skipped))

    def test_summary_does_not_select_rejected_eth_over_confirmed_btc_chain(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d", pf=2.5385, ret=8.1011),
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d", pf=2.2, ret=7.5),
            branch("EmaBounceV2", "ETHUSDT", "4h", "REJECTED", period="730d", pf=3.5, ret=10, trades=44),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        summary = zgua_app.build_research_autopilot_summary()
        self.assertEqual(summary["bestCurrentCandidate"]["symbol"], "BTCUSDT")
        self.assertEqual(summary["bestCurrentCandidate"]["period"], "1095d")
        self.assertFalse(summary["summaryText"].startswith("Best current research candidate: EmaBounceV2 ETHUSDT 4h 730d"))
        self.assertIn("EmaBounceV2 BTCUSDT 4h confirmed chain: 730d + 1095d", summary["summaryText"])
        self.assert_autopilot_safety(summary)

    def test_status_prefers_confirmed_btc_1095d_chain(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d"),
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d", pf=2.1, ret=7.2),
            branch("EmaBounceV2", "ETHUSDT", "4h", "REJECTED", period="730d", pf=4.0, ret=12, trades=50),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        status = zgua_app.build_research_autopilot_status()
        best = status["memory"]["bestCurrentCandidate"]
        self.assertEqual(best["symbol"], "BTCUSDT")
        self.assertEqual(best["period"], "1095d")
        self.assertEqual(status["memory"]["confirmedChain"]["label"], "EmaBounceV2 BTCUSDT 4h confirmed chain: 730d + 1095d")
        self.assertEqual(status["memory"]["topLeads"][0]["period"], "1095d")
        self.assert_autopilot_safety(status)

    def test_exact_already_tested_eligible_branch_is_not_requeued(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d"),
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d"),
        ], "candidates": []}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=6)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in jobs)) if jobs else set()
        self.assertNotIn("EmaBounceV2|BTCUSDT|4h|730d", planned_keys)
        self.assertNotIn("EmaBounceV2|BTCUSDT|4h|1095d", planned_keys)
        self.assertTrue(any(item.get("skipReason") == "already_tested_eligible_branch" and item.get("branchKey") == "EmaBounceV2|BTCUSDT|4h|1095d" for item in skipped))

    def test_confirmed_chain_changes_family_status_to_review_ready(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d"),
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d"),
        ], "candidates": []}
        family = next(row for row in zgua_app.autopilot_family_summary(memory) if row["strategy"] == "EmaBounceV2")
        self.assertEqual(family["familyStatus"], "CONFIRMED_CHALLENGER_REVIEW")
        self.assertEqual(family["confirmedChains"][0]["label"], "EmaBounceV2 BTCUSDT 4h confirmed chain: 730d + 1095d")

    def test_candidate_dossier_finds_confirmed_chain_and_writes_markdown(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d"),
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d", pf=2.0214, ret=7.1006, trades=61),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        payload, status = zgua_app.build_research_autopilot_candidate_dossier({"strategy": "EmaBounceV2", "symbol": "BTCUSDT", "timeframe": "4h"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["confirmedChain"]["periods"], ["730d", "1095d"])
        self.assertEqual([row["period"] for row in payload["metrics"]], ["730d", "1095d"])
        self.assertIn("EmaBounceV2-BTCUSDT-4h-confirmed-chain.md", payload["savedPath"])
        saved = zgua_app.RESEARCH_DOSSIER_DIR / "EmaBounceV2-BTCUSDT-4h-confirmed-chain.md"
        self.assertTrue(saved.exists())
        text = saved.read_text(encoding="utf-8")
        self.assertIn("# EmaBounceV2 BTCUSDT 4h confirmed chain: 730d + 1095d", text)
        self.assertIn("Manual review only", text)
        self.assertIn("No automatic promotion", text)
        self.assert_autopilot_safety(payload)

    def test_candidate_dossier_does_not_include_rejected_other_symbols_as_confirmed(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d"),
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d"),
            branch("EmaBounceV2", "ETHUSDT", "4h", "REJECTED", period="730d", pf=4, ret=12, trades=50),
            branch("EmaBounceV2", "SOLUSDT", "4h", "REJECTED", period="730d", pf=3, ret=9, trades=42),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        payload, status = zgua_app.build_research_autopilot_candidate_dossier({"strategy": "EmaBounceV2", "symbol": "BTCUSDT", "timeframe": "4h"})
        self.assertEqual(status, 200)
        self.assertTrue(all(row["symbol"] == "BTCUSDT" for row in payload["branches"]))
        self.assertNotIn("ETHUSDT", payload["markdown"])
        self.assertNotIn("SOLUSDT", payload["markdown"])

    def test_candidate_dossier_loads_source_reports_and_populates_details(self):
        b730 = stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d")
        b1095 = stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d")
        p730 = zgua_app.RESEARCH_AUTOPILOT_DIR / "source-730.json"
        p1095 = zgua_app.RESEARCH_AUTOPILOT_DIR / "source-1095.json"
        p730.parent.mkdir(parents=True, exist_ok=True)
        p730.write_text(json.dumps(detailed_source_report(b730)), encoding="utf-8")
        p1095.write_text(json.dumps(detailed_source_report(b1095)), encoding="utf-8")
        b730["sourceReport"] = zgua_app.autopilot_display_path(p730)
        b1095["sourceReport"] = zgua_app.autopilot_display_path(p1095)
        zgua_app.save_autopilot_memory({"branches": [b730, b1095], "candidates": [], "sourceReports": []})
        payload, status = zgua_app.build_research_autopilot_candidate_dossier({"strategy": "EmaBounceV2", "symbol": "BTCUSDT", "timeframe": "4h"})
        self.assertEqual(status, 200)
        self.assertTrue(all(detail["sourceReportLoaded"] for detail in payload["details"]))
        markdown = payload["markdown"]
        self.assertIn('"emaBounceAtr": 0.8', markdown)
        self.assertIn("| 730d | 1 | 2025-01-01/2025-06-01 | 1.2 | 1.8 | 12 | 0.9 | PASS |", markdown)
        self.assertIn("| 730d | highStress | RESILIENT | 6.4 | 2.1 | 49 | -1.7 |", markdown)
        self.assertIn("| 730d | 90d | RECENTLY_CONSISTENT | 0.7 | 1.6 | 8 | 0.4 | PASS |", markdown)
        self.assertIn("| 730d | REPRODUCIBLE | 3 | True | No reproducibility diff recorded. |", markdown)
        self.assertIn("| 730d | 49 | 42.5 | No concentration gate failure recorded. | PASS |", markdown)
        self.assertIn("| 730d | SimpleAtrTrendV2 | -10 | 3 | 44 | 0 | 54.0 |", markdown)
        self.assert_autopilot_safety(payload)

    def test_candidate_dossier_marks_unknown_only_when_source_detail_missing(self):
        b730 = stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d")
        b1095 = stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d")
        p730 = zgua_app.RESEARCH_AUTOPILOT_DIR / "source-730.json"
        p730.parent.mkdir(parents=True, exist_ok=True)
        p730.write_text(json.dumps({"ok": True, "modules": {"stabilityFirstSearch": {"summary": {}}, "deepValidation": []}}), encoding="utf-8")
        b730["sourceReport"] = zgua_app.autopilot_display_path(p730)
        b1095["sourceReport"] = "reports/research-snapshots/missing.json"
        zgua_app.save_autopilot_memory({"branches": [b730, b1095], "candidates": [], "sourceReports": []})
        payload, status = zgua_app.build_research_autopilot_candidate_dossier({"strategy": "EmaBounceV2", "symbol": "BTCUSDT", "timeframe": "4h"})
        self.assertEqual(status, 200)
        self.assertIn("UNKNOWN", payload["markdown"])
        self.assertTrue(any(not detail["sourceReportLoaded"] for detail in payload["details"]))

    def test_prepare_paper_candidate_creates_disabled_package_for_confirmed_chain(self):
        b730 = stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d")
        b1095 = stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d")
        p730 = zgua_app.RESEARCH_AUTOPILOT_DIR / "source-730.json"
        p1095 = zgua_app.RESEARCH_AUTOPILOT_DIR / "source-1095.json"
        p730.parent.mkdir(parents=True, exist_ok=True)
        p730.write_text(json.dumps(detailed_source_report(b730)), encoding="utf-8")
        p1095.write_text(json.dumps(detailed_source_report(b1095)), encoding="utf-8")
        b730["sourceReport"] = zgua_app.autopilot_display_path(p730)
        b1095["sourceReport"] = zgua_app.autopilot_display_path(p1095)
        zgua_app.save_autopilot_memory({"branches": [b730, b1095], "candidates": [], "sourceReports": []})
        payload, status = self.run_with_forbidden_boundaries(zgua_app.build_research_autopilot_prepare_paper_candidate, {"strategy": "EmaBounceV2", "symbol": "BTCUSDT", "timeframe": "4h"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "DISABLED_REVIEW_ONLY")
        self.assertEqual(payload["confirmedChainPeriods"], ["730d", "1095d"])
        self.assertFalse(payload["safety"]["paperEnabled"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        self.assertFalse(payload["safety"]["configWritten"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertTrue(payload["dossierPath"].endswith("EmaBounceV2-BTCUSDT-4h-confirmed-chain.md"))
        self.assertTrue(payload["savedPath"].endswith("EmaBounceV2-BTCUSDT-4h-disabled.json"))
        saved = zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json"
        self.assertTrue(saved.exists())
        package = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(package["status"], "DISABLED_REVIEW_ONLY")
        self.assertEqual(package["params"]["emaBounceAtr"], 0.8)
        self.assert_autopilot_safety(payload)

    def test_prepare_paper_candidate_requires_confirmed_chain(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d"),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        payload, status = zgua_app.build_research_autopilot_prepare_paper_candidate({"strategy": "EmaBounceV2", "symbol": "BTCUSDT", "timeframe": "4h"})
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        self.assertFalse((zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json").exists())

    def test_prepare_paper_candidate_rejected_other_symbols_cannot_create_package(self):
        memory = {"branches": [
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "730d"),
            stable_branch("EmaBounceV2", "BTCUSDT", "4h", "1095d"),
            branch("EmaBounceV2", "ETHUSDT", "4h", "REJECTED", period="730d", pf=3, ret=8, trades=45),
            branch("EmaBounceV2", "SOLUSDT", "4h", "REJECTED", period="1095d", pf=2, ret=5, trades=50),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        payload, status = zgua_app.build_research_autopilot_prepare_paper_candidate({"strategy": "EmaBounceV2", "symbol": "ETHUSDT", "timeframe": "4h"})
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        self.assertFalse((zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "EmaBounceV2-ETHUSDT-4h-disabled.json").exists())

    def disabled_candidate_package(self, symbol="BTCUSDT", status="DISABLED_REVIEW_ONLY", safety=None):
        return {
            "ok": True,
            "status": status,
            "reviewBanner": "Confirmed candidate available for manual paper review; disabled by default.",
            "candidateIdentity": {
                "strategy": "EmaBounceV2",
                "symbol": symbol,
                "timeframe": "4h",
                "paramsHash": "f09aabfcd7a47bd2",
                "candidateKey": f"EmaBounceV2|{symbol}|4h|f09aabfcd7a47bd2",
            },
            "confirmedChainPeriods": ["730d", "1095d"],
            "sourceReports": [
                "reports/research-snapshots/research-snapshot-20260613-220214.json",
                "reports/research-snapshots/research-snapshot-20260613-220942.json",
            ],
            "dossierPath": "reports/research-dossiers/EmaBounceV2-BTCUSDT-4h-confirmed-chain.md",
            "strengths": ["730d and 1095d branches are CHALLENGER_ELIGIBLE."],
            "warnings": ["Manual review only; package is disabled by default."],
            "safety": safety or {
                "researchOnly": True,
                "paperEnabled": False,
                "realTradingEnabled": False,
                "configWritten": False,
                "paperStateChanged": False,
                "liveOrdersTouched": False,
                "paperTickRan": False,
                "promotionAttempted": False,
                "realOrderFunctionsCalled": False,
                "activePaperCandidateMutated": False,
                "riskSettingsChanged": False,
                "apiKeyPathCreated": False,
            },
        }

    def write_disabled_candidate_package(self, filename="EmaBounceV2-BTCUSDT-4h-disabled.json", **kwargs):
        zgua_app.PAPER_CANDIDATE_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        path = zgua_app.PAPER_CANDIDATE_REVIEW_DIR / filename
        path.write_text(json.dumps(self.disabled_candidate_package(**kwargs)), encoding="utf-8")
        return path

    def test_research_paper_candidates_api_lists_disabled_packages(self):
        self.write_disabled_candidate_package()
        with zgua_app.app.test_client() as client:
            response = client.get("/api/research/paper-candidates")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["candidateCount"], 1)
        candidate = payload["candidates"][0]
        self.assertEqual(candidate["status"], "DISABLED_REVIEW_ONLY")
        self.assertEqual(candidate["candidateIdentity"]["strategy"], "EmaBounceV2")
        self.assertEqual(candidate["candidateIdentity"]["symbol"], "BTCUSDT")
        self.assertEqual(candidate["confirmedChainPeriods"], ["730d", "1095d"])
        self.assertEqual(candidate["paramsHash"], "f09aabfcd7a47bd2")
        self.assertFalse(candidate["safety"]["paperEnabled"])
        self.assertFalse(candidate["safety"]["realTradingEnabled"])
        self.assertFalse(candidate["safety"]["configWritten"])
        self.assertFalse(candidate["safety"]["paperStateChanged"])
        self.assertFalse(candidate["safety"]["liveOrdersTouched"])
        self.assert_autopilot_safety(payload)

    def test_research_paper_candidates_ignores_malformed_packages_safely(self):
        self.write_disabled_candidate_package()
        zgua_app.PAPER_CANDIDATE_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        (zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "bad.json").write_text("{not json", encoding="utf-8")
        payload = self.run_with_forbidden_boundaries(zgua_app.build_research_paper_candidates)
        self.assertEqual(payload["candidateCount"], 1)
        self.assertTrue(any("malformed package" in item["reason"] for item in payload["ignoredPackages"]))
        self.assert_autopilot_safety(payload)

    def test_research_paper_candidates_excludes_rejected_or_non_disabled_packages(self):
        self.write_disabled_candidate_package("EmaBounceV2-ETHUSDT-4h-disabled.json", symbol="ETHUSDT", status="REJECTED")
        self.write_disabled_candidate_package("EmaBounceV2-SOLUSDT-4h-disabled.json", symbol="SOLUSDT", safety={
            "researchOnly": True,
            "paperEnabled": True,
            "realTradingEnabled": False,
            "configWritten": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
        })
        payload = self.run_with_forbidden_boundaries(zgua_app.build_research_paper_candidates)
        self.assertEqual(payload["candidateCount"], 0)
        self.assertFalse(any((item.get("candidateIdentity") or {}).get("symbol") in {"ETHUSDT", "SOLUSDT"} for item in payload["candidates"]))
        self.assertEqual(len(payload["ignoredPackages"]), 2)
        self.assert_autopilot_safety(payload)

    def test_publish_review_candidate_creates_deploy_safe_package(self):
        self.write_disabled_candidate_package()
        payload, status = self.run_with_forbidden_boundaries(zgua_app.build_research_publish_review_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
        })
        self.assertEqual(status, 200)
        saved = zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json"
        self.assertTrue(saved.exists())
        package = json.loads(saved.read_text(encoding="utf-8"))
        self.assertEqual(package["status"], "DISABLED_REVIEW_ONLY")
        self.assertEqual(package["candidateIdentity"]["symbol"], "BTCUSDT")
        self.assertEqual(package["paramsHash"], "f09aabfcd7a47bd2")
        self.assertEqual(package["confirmedChainPeriods"], ["730d", "1095d"])
        self.assertIn("dossierPath", package)
        self.assertIn("sourceReports", package)
        self.assertNotIn("params", package)
        self.assertFalse(package["safety"]["paperEnabled"])
        self.assertFalse(package["safety"]["realTradingEnabled"])
        self.assertFalse(package["safety"]["configWritten"])
        self.assertFalse(package["safety"]["paperStateChanged"])
        self.assertFalse(package["safety"]["liveOrdersTouched"])
        self.assertFalse(package["safety"]["paperTickRan"])
        self.assert_autopilot_safety(payload)

    def test_publish_review_candidate_rejects_unsafe_package(self):
        self.write_disabled_candidate_package(safety={
            "researchOnly": True,
            "paperEnabled": False,
            "realTradingEnabled": False,
            "configWritten": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "paperTickRan": True,
        })
        payload, status = self.run_with_forbidden_boundaries(zgua_app.build_research_publish_review_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
        })
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("paperTickRan", payload["error"])
        self.assertFalse((zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json").exists())
        self.assert_autopilot_safety(payload)

    def test_research_paper_candidates_reads_deploy_safe_package_without_reports(self):
        zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
        deploy_path = zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json"
        deploy_path.write_text(json.dumps(self.disabled_candidate_package()), encoding="utf-8")
        payload = self.run_with_forbidden_boundaries(zgua_app.build_research_paper_candidates)
        self.assertEqual(payload["candidateCount"], 1)
        self.assertEqual(payload["candidates"][0]["sourceType"], "deploy")
        self.assertEqual(payload["candidates"][0]["candidateIdentity"]["symbol"], "BTCUSDT")
        self.assert_autopilot_safety(payload)

    def test_research_paper_candidates_dedupes_local_and_deploy_packages(self):
        self.write_disabled_candidate_package()
        zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
        deploy_path = zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json"
        deploy_path.write_text(json.dumps(self.disabled_candidate_package()), encoding="utf-8")
        payload = self.run_with_forbidden_boundaries(zgua_app.build_research_paper_candidates)
        self.assertEqual(payload["candidateCount"], 1)
        self.assertEqual(payload["candidates"][0]["candidateIdentity"]["symbol"], "BTCUSDT")
        self.assert_autopilot_safety(payload)

    def test_research_paper_review_route_renders_main_page(self):
        with zgua_app.app.test_client() as client:
            response = client.get("/research/paper-review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Manual Paper Review Candidates", html)
        self.assertIn("Review disabled paper candidates", html)

    def test_research_paper_candidates_ui_is_display_only_and_fetches_read_only_api(self):
        template = (Path(zgua_app.app.root_path) / "templates" / "index.html").read_text(encoding="utf-8")
        script = (Path(zgua_app.app.root_path) / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Manual Paper Review Candidates", template)
        self.assertIn("research-paper-candidates-panel", template)
        self.assertIn('apiGet("/api/research/paper-candidates")', script)
        self.assertIn("/research/paper-review", script)
        self.assertIn("Review disabled paper candidates", template)
        self.assertIn("DISABLED / REVIEW ONLY", script)
        self.assertIn("Paper OFF", script)
        self.assertIn("Live OFF", script)
        self.assertIn("Config write OFF", script)
        self.assertIn("Paper tick OFF", script)
        self.assertIn("No disabled paper-review candidates found.", script)
        self.assertIn("Could not load review candidates.", script)
        self.assertIn('renderPaperCandidateList(candidate.strengths, "positive")', script)
        self.assertIn('renderPaperCandidateList(candidate.warnings, "caution")', script)
        styles = (Path(zgua_app.app.root_path) / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".paper-review-list.positive", styles)
        self.assertIn(".paper-review-list.caution", styles)
        self.assertIn("loadResearchPaperCandidates", script)
        self.assertNotIn('apiPost("/api/research/paper-candidates"', script)
        self.assertNotIn('apiPut("/api/research/paper-candidates"', script)
        self.assertNotIn('apiDelete("/api/research/paper-candidates"', script)
        self.assertNotIn("research-paper-candidates-enable", template)
        self.assertNotIn("research-paper-candidates-run", template)
        self.assertNotIn("research-paper-candidates-paper-tick", template)
        self.assertNotIn("research-paper-candidates-live", template)

    def test_include_cooled_and_force_strategy_can_plan_cooled_family(self):
        memory = {"branches": [
            branch("PullbackTrend", "BTCUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "BTCUSDT", "4h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "4h", "LOW_PROFIT_FACTOR"),
        ], "candidates": []}
        default_jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=30)
        self.assertFalse(any(job.get("strategy") == "PullbackTrend" for job in default_jobs))
        cooled_jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=30, include_cooled=True)
        self.assertTrue(any(job.get("strategy") == "PullbackTrend" and "SOLUSDT" in job.get("symbols", []) for job in cooled_jobs))
        forced_jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=5, force_strategy="PullbackTrend")
        self.assertTrue(any(job.get("strategy") == "PullbackTrend" for job in forced_jobs))

    def test_force_branch_bypasses_family_and_branch_cooldown(self):
        memory = {"branches": [
            branch("PullbackTrend", "BTCUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "BTCUSDT", "4h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "4h", "LOW_PROFIT_FACTOR"),
        ], "candidates": []}
        jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=1, force_branch="PullbackTrend:ETHUSDT:4h:365d")
        self.assertEqual(jobs[0].get("generatedBy"), "forced_branch")
        self.assertEqual(jobs[0].get("strategy"), "PullbackTrend")

    def test_default_api_planning_uses_balanced_mode(self):
        with patch.object(zgua_app, "autopilot_plan_jobs", return_value=([], [], [])) as planner:
            payload, status = zgua_app.build_research_autopilot_plan({})
        self.assertEqual(status, 200)
        self.assertEqual(payload["plannerOptions"]["planningMode"], "balanced")
        self.assertEqual(payload["plannerOptions"]["maxJobs"], 5)
        planner_kwargs = planner.call_args[1]
        self.assertEqual(planner_kwargs["planning_mode"], "balanced")
        self.assertEqual(planner_kwargs["max_jobs"], 5)

    def test_api_planning_options_reach_planner(self):
        with patch.object(zgua_app, "autopilot_plan_jobs", return_value=([], [], [])) as planner:
            payload, status = zgua_app.build_research_autopilot_plan({
                "planningMode": "exploratory",
                "includeCooled": True,
                "forceStrategy": "PullbackTrend",
                "forceBranch": "PullbackTrend:ETHUSDT:4h:365d",
                "maxJobs": 20,
            })
        self.assertEqual(status, 200)
        self.assertEqual(payload["plannerOptions"]["planningMode"], "exploratory")
        self.assertTrue(payload["plannerOptions"]["includeCooled"])
        self.assertEqual(payload["plannerOptions"]["forceStrategy"], "PullbackTrend")
        self.assertEqual(payload["plannerOptions"]["forceBranch"], "PullbackTrend:ETHUSDT:4h:365d")
        self.assertEqual(payload["plannerOptions"]["maxJobs"], 20)
        planner_kwargs = planner.call_args[1]
        self.assertEqual(planner_kwargs["planning_mode"], "exploratory")
        self.assertTrue(planner_kwargs["include_cooled"])
        self.assertEqual(planner_kwargs["force_strategy"], "PullbackTrend")
        self.assertEqual(planner_kwargs["force_branch"], "PullbackTrend:ETHUSDT:4h:365d")

    def test_exploratory_mode_does_not_bypass_exact_rejected_branch_guard(self):
        memory = {"branches": [
            branch("SimpleAtrTrendV2", "ETHUSDT", "1h", "NEGATIVE_RETURN"),
        ], "candidates": []}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=12, planning_mode="exploratory")
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in jobs)) if jobs else set()
        self.assertNotIn("SimpleAtrTrendV2|ETHUSDT|1h|365d", planned_keys)
        self.assertTrue(any(item.get("branchKey") == "SimpleAtrTrendV2|ETHUSDT|1h|365d" and item.get("skipReason") in {"rejected_branch", "recently_tested_rejected_branch"} for item in skipped))

        forced_jobs, _warnings, _skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=1, planning_mode="exploratory", force_branch="SimpleAtrTrendV2:ETHUSDT:1h:365d")
        self.assertEqual(forced_jobs[0].get("generatedBy"), "forced_branch")
        self.assertIn("SimpleAtrTrendV2|ETHUSDT|1h|365d", zgua_app.autopilot_job_branch_keys(forced_jobs[0]))

    def test_family_status_appears_in_journal(self):
        memory = {"branches": [
            branch("PullbackTrend", "BTCUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "BTCUSDT", "4h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "1h", "NEGATIVE_RETURN"),
            branch("PullbackTrend", "ETHUSDT", "4h", "LOW_PROFIT_FACTOR"),
        ], "candidates": [], "sourceReports": []}
        zgua_app.save_autopilot_memory(memory)
        payload = zgua_app.build_research_autopilot_journal()
        family = next(row for row in payload["strategyFamilies"] if row["strategy"] == "PullbackTrend")
        self.assertIn(family["familyStatus"], {"COOL_DOWN", "REJECTED_FAMILY"})

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
        self.assert_autopilot_safety(payload)
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

    def test_run_batch_reports_safety_cap_when_requested_above_three(self):
        queue = zgua_app.load_autopilot_queue()
        jobs = [
            zgua_app.make_autopilot_job(["MeanReversion"], [f"TEST{i}USDT"], ["1h"], "365d", "batch")
            for i in range(5)
        ]
        zgua_app.autopilot_enqueue(queue, jobs)
        zgua_app.save_autopilot_queue(queue)
        with patch.object(zgua_app, "build_research_campaign_runner", return_value=(campaign_payload(candidate_row()), 200)):
            payload, status = zgua_app.build_research_autopilot_run_batch({"maxJobs": 5})
        self.assertEqual(status, 200)
        self.assertEqual(payload["maxJobsRequested"], 5)
        self.assertEqual(payload["maxJobsEffective"], 3)
        self.assertEqual(payload["maxJobs"], 3)
        self.assertEqual(payload["capReason"], "safety cap")
        self.assertEqual(payload["jobsAttempted"], 3)

    def test_no_research_lead_writes_durable_branch_memory(self):
        payload = no_research_lead_payload("MomentumContinuation", ["BTCUSDT"], ["1h"])
        memory = zgua_app.update_autopilot_memory_from_report(payload, payload["savedPath"])
        self.assertEqual(memory["candidates"], [])
        branch_row = next(row for row in memory["branches"] if row["branchKey"] == "MomentumContinuation|BTCUSDT|1h|365d")
        self.assertEqual(branch_row["reasonCategory"], "NO_RESEARCH_LEAD")
        self.assertEqual(branch_row["eligibilityStatus"], "NO_RESEARCH_LEAD")
        self.assertEqual(branch_row["bestTier"], "NO_RESEARCH_LEAD")
        self.assertEqual(branch_row["fullTrades"], 0)
        self.assertEqual(branch_row["profitFactor"], 0)
        self.assertEqual(branch_row["totalReturnPct"], 0)
        self.assertEqual(branch_row["recentWindowStatus"], "NO_RESEARCH_LEAD")
        self.assertEqual(branch_row["stressStatus"], "NO_RESEARCH_LEAD")

    def test_no_research_lead_prevents_exact_requeue_after_reset(self):
        payload = no_research_lead_payload("MomentumContinuation", ["BTCUSDT"], ["1h"])
        zgua_app.update_autopilot_memory_from_report(payload, payload["savedPath"])
        reset, reset_status = zgua_app.build_research_autopilot_reset_queue({"confirm": True})
        self.assertEqual(reset_status, 200)
        self.assertTrue(reset["ok"])
        plan, plan_status = zgua_app.build_research_autopilot_plan({"maxJobs": 20, "forceStrategy": "MomentumContinuation"})
        self.assertEqual(plan_status, 200)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in plan["addedJobs"])) if plan["addedJobs"] else set()
        self.assertNotIn("MomentumContinuation|BTCUSDT|1h|365d", planned_keys)
        self.assertTrue(any(item.get("skipReason") == "already_tested_no_research_lead" and item.get("branchKey") == "MomentumContinuation|BTCUSDT|1h|365d" for item in plan["skippedJobs"]))

    def test_no_research_lead_contributes_to_family_exhaustion(self):
        payload = no_research_lead_payload("MomentumContinuation", ["BTCUSDT", "ETHUSDT", "SOLUSDT"], ["1h", "4h"])
        memory = zgua_app.update_autopilot_memory_from_report(payload, payload["savedPath"])
        family = next(row for row in zgua_app.autopilot_family_summary(memory) if row["strategy"] == "MomentumContinuation")
        self.assertEqual(family["branchesTested"], 6)
        self.assertEqual(family["insufficientEvidenceBranches"], 6)
        self.assertIn(family["familyStatus"], {"EXHAUSTED_IN_CURRENT_SCOPE", "COOL_DOWN"})

    def test_range_expansion_no_research_lead_appears_in_family_summary(self):
        payload = no_research_lead_payload("RangeExpansionV2", ["BTCUSDT"], ["4h"])
        memory = zgua_app.update_autopilot_memory_from_report(payload, payload["savedPath"])
        family = next(row for row in zgua_app.autopilot_family_summary(memory) if row["strategy"] == "RangeExpansionV2")
        self.assertEqual(family["branchesTested"], 1)
        self.assertEqual(family["insufficientEvidenceBranches"], 1)

    def test_force_branch_can_override_no_research_lead_branch(self):
        memory = {"branches": [
            branch("MomentumContinuation", "BTCUSDT", "1h", "NO_RESEARCH_LEAD", period="365d", pf=0, ret=0, trades=0),
        ], "candidates": []}
        jobs, _warnings, skipped = zgua_app.autopilot_plan_jobs(memory, {"jobs": []}, max_jobs=1, force_branch="MomentumContinuation:BTCUSDT:1h:365d")
        self.assertEqual(jobs[0].get("generatedBy"), "forced_branch")
        self.assertIn("MomentumContinuation|BTCUSDT|1h|365d", zgua_app.autopilot_job_branch_keys(jobs[0]))
        self.assertFalse(any(item.get("skipReason") == "already_tested_no_research_lead" and item.get("branchKey") == "MomentumContinuation|BTCUSDT|1h|365d" for item in skipped))

    def test_historical_no_research_lead_report_is_backfilled_into_memory(self):
        report = zgua_app.RESEARCH_AUTOPILOT_DIR / "historical-no-lead.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(no_research_lead_payload("MomentumContinuation", ["BTCUSDT"], ["1h"])), encoding="utf-8")
        with patch.object(zgua_app, "candidate_ledger_source_files", return_value=[report]):
            payload = zgua_app.backfill_autopilot_no_research_leads()
        self.assertEqual(payload["noResearchLeadReports"], 1)
        memory = zgua_app.load_autopilot_memory()
        self.assertTrue(any(row.get("branchKey") == "MomentumContinuation|BTCUSDT|1h|365d" and row.get("reasonCategory") == "NO_RESEARCH_LEAD" for row in memory["branches"]))

    def test_no_research_lead_backfill_is_idempotent(self):
        report = zgua_app.RESEARCH_AUTOPILOT_DIR / "historical-no-lead.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(no_research_lead_payload("MomentumContinuation", ["BTCUSDT"], ["1h"])), encoding="utf-8")
        with patch.object(zgua_app, "candidate_ledger_source_files", return_value=[report]):
            first = zgua_app.backfill_autopilot_no_research_leads()
            second = zgua_app.backfill_autopilot_no_research_leads()
        self.assertEqual(first["backfilledBranches"], 1)
        self.assertEqual(second["backfilledBranches"], 0)
        memory = zgua_app.load_autopilot_memory()
        matches = [row for row in memory["branches"] if row.get("branchKey") == "MomentumContinuation|BTCUSDT|1h|365d"]
        self.assertEqual(len(matches), 1)

    def test_no_research_lead_backfill_does_not_overwrite_better_branch_evidence(self):
        existing = branch("MomentumContinuation", "BTCUSDT", "1h", "PROMISING_BUT_RARE", period="365d", pf=2.1, ret=4, trades=22)
        existing["branchKey"] = zgua_app.autopilot_branch_key(existing, existing["period"])
        zgua_app.save_autopilot_memory({"branches": [existing], "candidates": [], "sourceReports": []})
        report = zgua_app.RESEARCH_AUTOPILOT_DIR / "historical-no-lead.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(no_research_lead_payload("MomentumContinuation", ["BTCUSDT"], ["1h"])), encoding="utf-8")
        with patch.object(zgua_app, "candidate_ledger_source_files", return_value=[report]):
            zgua_app.backfill_autopilot_no_research_leads()
        row = next(item for item in zgua_app.load_autopilot_memory()["branches"] if item.get("branchKey") == "MomentumContinuation|BTCUSDT|1h|365d")
        self.assertEqual(row["reasonCategory"], "PROMISING_BUT_RARE")
        self.assertEqual(row["profitFactor"], 2.1)

    def test_reset_queue_and_plan_skip_backfilled_no_research_lead_branch(self):
        report = zgua_app.RESEARCH_AUTOPILOT_DIR / "historical-no-lead.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(no_research_lead_payload("MomentumContinuation", ["BTCUSDT"], ["1h"])), encoding="utf-8")
        with patch.object(zgua_app, "candidate_ledger_source_files", return_value=[report]):
            reset, reset_status = zgua_app.build_research_autopilot_reset_queue({"confirm": True})
            plan, plan_status = zgua_app.build_research_autopilot_plan({"maxJobs": 20, "forceStrategy": "MomentumContinuation"})
        self.assertEqual(reset_status, 200)
        self.assertTrue(reset["ok"])
        self.assertEqual(plan_status, 200)
        planned_keys = set().union(*(zgua_app.autopilot_job_branch_keys(job) for job in plan["addedJobs"])) if plan["addedJobs"] else set()
        self.assertNotIn("MomentumContinuation|BTCUSDT|1h|365d", planned_keys)
        skip = next(item for item in plan["skippedJobs"] if item.get("skipReason") == "already_tested_no_research_lead")
        self.assertEqual(skip.get("branchKey"), "MomentumContinuation|BTCUSDT|1h|365d")
        self.assertIn("produced NO_RESEARCH_LEAD", skip.get("detail", ""))

    def test_momentum_continuation_historical_no_leads_exhaust_family(self):
        report = zgua_app.RESEARCH_AUTOPILOT_DIR / "historical-no-lead.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(no_research_lead_payload("MomentumContinuation", ["BTCUSDT", "ETHUSDT", "SOLUSDT"], ["1h", "4h"])), encoding="utf-8")
        with patch.object(zgua_app, "candidate_ledger_source_files", return_value=[report]):
            zgua_app.backfill_autopilot_no_research_leads()
        memory = zgua_app.load_autopilot_memory()
        family = next(row for row in zgua_app.autopilot_family_summary(memory) if row["strategy"] == "MomentumContinuation")
        self.assertEqual(family["branchesTested"], 6)
        self.assertEqual(family["insufficientEvidenceBranches"], 6)
        self.assertIn(family["familyStatus"], {"EXHAUSTED_IN_CURRENT_SCOPE", "COOL_DOWN"})

    def test_malformed_result_fails_safely(self):
        queue = zgua_app.load_autopilot_queue()
        zgua_app.autopilot_enqueue(queue, [zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "test")])
        zgua_app.save_autopilot_queue(queue)
        with patch.object(zgua_app, "build_research_campaign_runner", return_value=({"ok": False, "error": "bad"}, 502)):
            payload, status = zgua_app.build_research_autopilot_run_next()
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["job"]["status"], "FAILED")
        self.assert_autopilot_safety(payload)

    def test_status_and_summary_safety_flags(self):
        status = zgua_app.build_research_autopilot_status()
        summary = zgua_app.build_research_autopilot_summary()
        self.assert_autopilot_safety(status)
        self.assert_autopilot_safety(summary)
        self.assertIn("Safety:", summary["summaryText"])

    def test_autopilot_commands_do_not_cross_promotion_boundary(self):
        queue = zgua_app.load_autopilot_queue()
        zgua_app.autopilot_enqueue(queue, [zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "test")])
        zgua_app.save_autopilot_queue(queue)
        row = candidate_row()
        with patch.object(zgua_app, "build_research_campaign_runner", return_value=(campaign_payload(row), 200)):
            plan, plan_status = self.run_with_forbidden_boundaries(zgua_app.build_research_autopilot_plan, {"maxJobs": 2})
            run_next, run_next_status = self.run_with_forbidden_boundaries(zgua_app.build_research_autopilot_run_next)
            batch, batch_status = self.run_with_forbidden_boundaries(zgua_app.build_research_autopilot_run_batch, {"maxJobs": 1})
        summary = self.run_with_forbidden_boundaries(zgua_app.build_research_autopilot_summary)
        reset, reset_status = self.run_with_forbidden_boundaries(zgua_app.build_research_autopilot_reset_queue, {"confirm": True})
        self.assertEqual(plan_status, 200)
        self.assertIn(run_next_status, {200, 502})
        self.assertIn(batch_status, {200, 207})
        self.assertEqual(reset_status, 200)
        for payload in (plan, run_next, batch, summary, reset):
            self.assert_autopilot_safety(payload)

    def test_no_queue_and_malformed_queue_fail_safely(self):
        payload, status = zgua_app.build_research_autopilot_run_next()
        self.assertEqual(status, 404)
        self.assert_autopilot_safety(payload)
        zgua_app.RESEARCH_AUTOPILOT_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        zgua_app.RESEARCH_AUTOPILOT_QUEUE_PATH.write_text("{not-json", encoding="utf-8")
        payload, status = zgua_app.build_research_autopilot_run_next()
        self.assertEqual(status, 404)
        self.assert_autopilot_safety(payload)

    def test_stale_running_job_recovered_without_execution(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        queue = zgua_app.load_autopilot_queue()
        job = zgua_app.make_autopilot_job(["MeanReversion"], ["BTCUSDT"], ["4h"], "730d", "stale")
        job["status"] = "RUNNING"
        job["startedAt"] = old
        queue["jobs"] = [job]
        zgua_app.save_autopilot_queue(queue)
        with patch.object(zgua_app, "build_research_campaign_runner", side_effect=AssertionError("stale recovery must not execute campaign")):
            payload, status = zgua_app.build_research_autopilot_run_next()
        self.assertEqual(status, 404)
        self.assertEqual(payload["queue"]["counts"]["FAILED"], 1)
        self.assertEqual(payload["queue"]["recoveredStaleJobs"][0]["status"], "FAILED")
        self.assert_autopilot_safety(payload)

    def test_failed_gate_display_and_placeholder_candidates_are_sanitized(self):
        placeholder = zgua_app.compact_campaign_candidate({})
        self.assertEqual(placeholder, {})
        formatted = zgua_app.format_failed_gates([
            {"name": "full trades", "detail": "22 >= 40"},
            {"name": "full PF", "detail": "0.7478 >= 1.05"},
            {"name": "negative folds", "detail": "3 <= 1"},
            {"name": "concentration", "detail": "82.81% <= 75%"},
            {"name": "worst fold", "detail": "-1.31% > -1%"},
        ])
        details = [item["detail"] for item in formatted]
        self.assertIn("22 trades < required 40", details)
        self.assertIn("PF 0.7478 < required 1.05", details)
        self.assertIn("3 negative folds > allowed 1", details)
        self.assertIn("concentration 82.81% > allowed 75%", details)
        self.assertIn("worst fold -1.31% < allowed -1%", details)
        payload = campaign_payload({})
        memory = zgua_app.update_autopilot_memory_from_report(payload, "inline-placeholder")
        self.assertEqual(memory["candidates"], [])
        self.assertEqual(memory["branches"], [])


if __name__ == "__main__":
    unittest.main()
