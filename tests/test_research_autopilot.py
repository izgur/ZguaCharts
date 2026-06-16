import json
import os
import tempfile
import unittest
from contextlib import ExitStack
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
        patch.object(zgua_app, "PAPER_CANDIDATE_LOCAL_PATH", root / "config" / "local" / "paper-candidate.json"),
        patch.object(zgua_app, "PAPER_STATE_PATH", root / "paper-state.json"),
        patch.object(zgua_app, "PAPER_JOURNAL_PATH", root / "reports" / "paper-journal.jsonl"),
        patch.object(zgua_app, "PAPER_CANDIDATE_ENABLE_AUDIT_DIR", root / "paper-candidates" / "enable-audits"),
        patch.object(zgua_app, "PAPER_TICK_AUDIT_DIR", root / "paper-candidates" / "tick-audits"),
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

    def run_with_live_boundaries(self, func, *args, **kwargs):
        patches = []
        for name in FORBIDDEN_AUTOPILOT_CALLS:
            if name in {"write_candidate_config", "write_paper_candidate_config"}:
                continue
            if hasattr(zgua_app, name):
                patches.append(patch.object(zgua_app, name, side_effect=AssertionError(f"Paper enable must not call {name}")))
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
            "params": {
                "emaBounceAtr": 0.8,
                "rsiReclaimLevel": 53,
                "atrMultiplier": 3.3,
            },
            "strengths": [
                "730d and 1095d branches are CHALLENGER_ELIGIBLE.",
                "Confirmed branches survive moderate stress.",
                "Source reports mark the candidate reproducible.",
                "Return concentration passes stored gates.",
            ],
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
        readiness = candidate["readiness"]
        self.assertIn(readiness["verdict"], {"REVIEW_READY_BUT_DISABLED", "WATCH_BEFORE_ENABLE"})
        self.assertNotEqual(readiness["verdict"], "LIVE_READY")
        self.assertTrue(any("Confirmed chain exists" in item for item in readiness["passItems"]))
        self.assertTrue(any("Stress survives" in item for item in readiness["passItems"]))
        self.assertTrue(any("Reproducible" in item for item in readiness["passItems"]))
        self.assertTrue(any("Concentration passes" in item for item in readiness["passItems"]))
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

    def test_research_paper_candidate_readiness_warns_on_known_review_risks(self):
        package = self.disabled_candidate_package()
        package["warnings"] = [
            "730d has 1 non-passing walk-forward fold(s).",
            "1095d has 2 non-passing walk-forward fold(s).",
            "730d recent windows need review: 90d, 180d.",
            "1095d recent windows need review: 90d, 180d.",
            "730d regime dependence is unknown or not fully recorded.",
        ]
        zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
        (zgua_app.DEPLOY_REVIEW_CANDIDATE_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json").write_text(json.dumps(package), encoding="utf-8")
        payload = self.run_with_forbidden_boundaries(zgua_app.build_research_paper_candidates)
        readiness = payload["candidates"][0]["readiness"]
        self.assertEqual(readiness["verdict"], "REVIEW_READY_BUT_DISABLED")
        self.assertNotEqual(readiness["verdict"], "LIVE_READY")
        self.assertEqual(readiness["safetyReminder"].count("Still disabled:"), 0)
        rendered_disabled_text = f"Still disabled: {readiness['safetyReminder']}"
        self.assertEqual(rendered_disabled_text.count("Still disabled:"), 1)
        self.assertTrue(any("730d has 1 non-passing" in item for item in readiness["warnItems"]))
        self.assertTrue(any("1095d has 2 non-passing" in item for item in readiness["warnItems"]))
        self.assertTrue(any("90d and 180d recent windows" in item for item in readiness["warnItems"]))
        self.assertTrue(any("Regime dependence unknown" in item for item in readiness["warnItems"]))
        self.assertTrue(any("Low recent trade count" in item for item in readiness["warnItems"]))
        self.assertTrue(any("Paper/live disabled by design" in item for item in readiness["warnItems"]))
        self.assertFalse(payload["candidates"][0]["safety"]["paperEnabled"])
        self.assertFalse(payload["candidates"][0]["safety"]["realTradingEnabled"])
        self.assert_autopilot_safety(payload)

    def test_plan_paper_enable_candidate_dry_run_returns_full_params(self):
        package = self.disabled_candidate_package()
        package["warnings"] = [
            "730d has 1 non-passing walk-forward fold(s).",
            "1095d has 2 non-passing walk-forward fold(s).",
            "730d recent windows need review: 90d, 180d.",
            "730d regime dependence is unknown or not fully recorded.",
        ]
        self.write_disabled_candidate_package()
        path = zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json"
        path.write_text(json.dumps(package), encoding="utf-8")
        payload, status = self.run_with_forbidden_boundaries(zgua_app.build_research_plan_paper_enable_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
        })
        self.assertEqual(status, 200)
        self.assertTrue(payload["dryRun"])
        self.assertEqual(payload["params"]["emaBounceAtr"], 0.8)
        self.assertEqual(payload["paramsHash"], "f09aabfcd7a47bd2")
        self.assertEqual(payload["proposedPaperMarket"]["mode"], "PAPER_ONLY")
        self.assertFalse(payload["proposedSafetySettings"]["realTradingEnabled"])
        self.assertFalse(payload["proposedSafetySettings"]["exchangeOrders"])
        self.assertTrue(payload["proposedSafetySettings"]["requireManualConfirmation"])
        self.assertEqual(payload["proposedRiskPlaceholders"]["initialEquity"], 10000)
        self.assertEqual(payload["proposedRiskPlaceholders"]["maxPositionPct"], 10)
        self.assertIn("weak walk-forward folds", payload["blockingWarnings"])
        self.assertIn("90d/180d recent windows", payload["blockingWarnings"])
        self.assertIn("regime dependence unknown", payload["blockingWarnings"])
        self.assertIn("low recent trade count", payload["blockingWarnings"])
        self.assertIn("live trading not approved", payload["blockingWarnings"])
        self.assertFalse(payload["safety"]["paperEnabled"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        self.assertFalse(payload["configWritten"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assert_autopilot_safety(payload)

    def test_plan_paper_enable_candidate_refuses_unsafe_package(self):
        self.write_disabled_candidate_package(safety={
            "researchOnly": True,
            "paperEnabled": True,
            "realTradingEnabled": False,
            "configWritten": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "paperTickRan": False,
        })
        payload, status = self.run_with_forbidden_boundaries(zgua_app.build_research_plan_paper_enable_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
        })
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertIn("paperEnabled", payload["error"])
        self.assert_autopilot_safety(payload)

    def test_plan_paper_enable_candidate_refuses_missing_params(self):
        package = self.disabled_candidate_package()
        package.pop("params", None)
        self.write_disabled_candidate_package()
        path = zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json"
        path.write_text(json.dumps(package), encoding="utf-8")
        payload, status = self.run_with_forbidden_boundaries(zgua_app.build_research_plan_paper_enable_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
        })
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["dryRun"])
        self.assertIn("Full params are required", payload["error"])
        self.assert_autopilot_safety(payload)

    def paper_enable_confirmation(self):
        return "ENABLE PAPER ONLY EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2"

    def test_enable_paper_candidate_fails_without_exact_confirmation(self):
        self.write_disabled_candidate_package()
        payload, status = self.run_with_live_boundaries(zgua_app.build_research_enable_paper_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "confirm": "WRONG",
        })
        self.assertEqual(status, 400)
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["configWritten"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.exists())
        self.assertFalse(zgua_app.PAPER_STATE_PATH.exists())

    def test_enable_paper_candidate_fails_with_wrong_candidate_key(self):
        self.write_disabled_candidate_package()
        payload, status = self.run_with_live_boundaries(zgua_app.build_research_enable_paper_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "confirm": "ENABLE PAPER ONLY other-key",
        })
        self.assertEqual(status, 400)
        self.assertFalse(payload["enabled"])
        self.assertFalse(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.exists())

    def test_enable_paper_candidate_fails_if_package_lacks_full_params(self):
        package = self.disabled_candidate_package()
        package.pop("params", None)
        self.write_disabled_candidate_package()
        (zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json").write_text(json.dumps(package), encoding="utf-8")
        payload, status = self.run_with_live_boundaries(zgua_app.build_research_enable_paper_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "confirm": self.paper_enable_confirmation(),
        })
        self.assertEqual(status, 400)
        self.assertFalse(payload["enabled"])
        self.assertFalse(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.exists())

    def test_enable_paper_candidate_fails_if_unsafe_flags_present(self):
        self.write_disabled_candidate_package(safety={
            "researchOnly": True,
            "paperEnabled": False,
            "realTradingEnabled": True,
            "configWritten": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "paperTickRan": False,
        })
        payload, status = self.run_with_forbidden_boundaries(zgua_app.build_research_enable_paper_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "confirm": self.paper_enable_confirmation(),
        })
        self.assertEqual(status, 400)
        self.assertFalse(payload["enabled"])
        self.assertIn("realTradingEnabled", payload["dryRunPlan"]["error"])
        self.assertFalse(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.exists())

    def test_enable_paper_candidate_succeeds_with_exact_confirmation(self):
        package = self.disabled_candidate_package()
        package["warnings"] = [
            "730d has 1 non-passing walk-forward fold(s).",
            "1095d has 2 non-passing walk-forward fold(s).",
            "730d recent windows need review: 90d, 180d.",
            "730d regime dependence is unknown or not fully recorded.",
        ]
        self.write_disabled_candidate_package()
        (zgua_app.PAPER_CANDIDATE_REVIEW_DIR / "EmaBounceV2-BTCUSDT-4h-disabled.json").write_text(json.dumps(package), encoding="utf-8")
        payload, status = self.run_with_live_boundaries(zgua_app.build_research_enable_paper_candidate, {
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "confirm": self.paper_enable_confirmation(),
        })
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["mode"], "PAPER_ONLY")
        self.assertTrue(payload["configWritten"])
        self.assertTrue(payload["paperStateChanged"])
        self.assertTrue(payload["paperEnabled"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertFalse(payload["exchangeOrders"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertIn("live trading", payload["nextForbiddenActions"])
        config = json.loads(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8"))
        self.assertTrue(config["enabled"])
        self.assertEqual(config["mode"], "PAPER_ONLY")
        self.assertEqual(config["strategy"], "EmaBounceV2")
        self.assertEqual(config["symbols"], [{"symbol": "BTCUSDT", "interval": "4h", "mode": "active"}])
        self.assertEqual(config["params"]["emaBounceAtr"], 0.8)
        loaded = zgua_app.load_paper_candidate_config()
        self.assertEqual(loaded["params"]["emaBounceAtr"], 0.8)
        self.assertNotIn("emaFast", loaded["params"])
        self.assertFalse(config["realTradingEnabled"])
        self.assertFalse(config["exchangeOrders"])
        self.assertFalse(config["paperTickRan"])
        self.assertFalse(config["liveOrdersTouched"])
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(state["paperEnabled"])
        self.assertEqual(state["mode"], "PAPER_ONLY")
        self.assertFalse(state["paperTickRan"])
        self.assertFalse(state["liveOrdersTouched"])
        audits = list(zgua_app.PAPER_CANDIDATE_ENABLE_AUDIT_DIR.glob("*.json"))
        self.assertEqual(len(audits), 1)
        audit = json.loads(audits[0].read_text(encoding="utf-8"))
        self.assertTrue(audit["confirmationMatched"])
        self.assertEqual(audit["paramsHash"], "f09aabfcd7a47bd2")
        self.assertFalse(audit["safety"]["apiKeyPathCreated"])
        self.assertIn("weak walk-forward folds", payload["warnings"])

    def write_active_paper_config(self, enabled=True, real=False, exchange=False, initialized=True):
        zgua_app.PAPER_CANDIDATE_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        config = {
            "_replaceParams": True,
            "enabled": enabled,
            "paperEnabled": enabled,
            "mode": "PAPER_ONLY",
            "source": "bybit",
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "candidateKey": "candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca",
            "symbols": [{"symbol": "BTCUSDT", "interval": "4h", "mode": "active"}],
            "paramsHash": "f09aabfcd7a47bd2",
            "params": {"emaBounceAtr": 0.8, "rsiReclaimLevel": 53},
            "accountEquity": 10000,
            "maxPositionPct": 10,
            "takerFeePct": 0.055,
            "makerFeePct": 0.02,
            "slippageBps": 2,
            "maxOpenTrades": 1,
            "realTradingEnabled": real,
            "exchangeOrders": exchange,
            "liveOrdersTouched": False,
        }
        zgua_app.PAPER_CANDIDATE_LOCAL_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
        state = {
            "accountEquity": 10000,
            "openPositions": [],
            "closedTrades": [],
            "lastProcessedCandleTime": {"BTCUSDT:4h": 1000} if initialized else {},
            "paperTickRan": False,
            "liveOrdersTouched": False,
        }
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        return config

    def preview_subprocess_payloads(self, latest_time=None, diagnostic_warnings=None, tick_warnings=None):
        latest_time = latest_time or int(datetime.now(timezone.utc).timestamp()) - 60 * 60
        diagnostics = {
            "ok": True,
            "activeMarket": {"symbol": "BTCUSDT", "timeframe": "4h"},
            "latestCandle": {"time": latest_time, "close": 50000},
            "diagnostics": {
                "strategy": "EmaBounceV2",
                "signal": "BUY",
                "reason": "Entry signal matched.",
                "positionState": {"hasOpenPosition": False},
            },
            "warnings": diagnostic_warnings or [],
        }
        tick = {
            "ok": True,
            "status": "dry-run",
            "events": 1,
            "openPositions": 0,
            "closedTrades": 0,
            "warnings": tick_warnings or [],
            "freshness": {
                "BTCUSDT:4h": {"latestCandleTime": latest_time}
            },
        }
        def fake_run(command, text, capture_output, cwd, timeout):
            payload = diagnostics if "paper_signal_diagnostics.js" in " ".join(command) else tick
            return type("Completed", (), {"stdout": json.dumps(payload), "stderr": "", "returncode": 0})()
        return fake_run

    def command_text_from_call(self, call):
        positional, keyword = tuple(call)
        command = positional[0] if positional else keyword.get("args") or []
        return " ".join(command if isinstance(command, list) else [str(command)])

    def recent_closed_time(self):
        return int(datetime.now(timezone.utc).timestamp()) - 5 * 60 * 60

    def fresh_cache_payload(self, symbol="BTCUSDT", timeframe="4h", candles=600, latest=None):
        latest = latest or self.recent_closed_time()
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "cachedCandles": candles,
            "lastCandleTime": latest,
            "status": "OK",
            "warnings": [],
        }

    def cached_4h_rows(self):
        return [
            {"time": 1781524800, "open": 50000, "high": 50100, "low": 49900, "close": 50050},
            {"time": 1781539200, "open": 50050, "high": 50200, "low": 50000, "close": 50100},
        ]

    def stale_cache_payload(self, symbol="BTCUSDT", timeframe="4h", candles=600):
        latest = int(datetime.now(timezone.utc).timestamp()) - 24 * 60 * 60
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "cachedCandles": candles,
            "lastCandleTime": latest,
            "status": "STALE",
            "warnings": ["Cached latest candle is stale."],
        }

    def missing_cache_payload(self, symbol="BTCUSDT", timeframe="4h"):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "cachedCandles": 0,
            "lastCandleTime": None,
            "status": "MISSING",
            "warnings": ["No cached candles found."],
        }

    def init_confirm_text(self):
        return "INIT PAPER ONLY candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca"

    def tick_confirm_text(self, candle_at="2026-06-14T16:00:00+00:00"):
        return f"RUN ONE PAPER TICK candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca {candle_at}"

    def catch_up_confirm_text(self, candle_at="2026-06-15T20:00:00+00:00"):
        return f"RUN ONE PAPER CATCH-UP TICK candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca {candle_at}"

    def tick_alignment_payload(self, status="ALIGNED", expected_at="2026-06-14T16:00:00+00:00", expected_time=2000):
        blocking = status == "MISMATCH"
        return {
            "ok": True,
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "freshnessLatestCandleTime": expected_time,
            "freshnessLatestCandleAt": expected_at,
            "signalEvaluationCandleTime": expected_time if not blocking else expected_time - 14400,
            "signalEvaluationCandleAt": expected_at if not blocking else "2026-06-14T12:00:00+00:00",
            "tickDryRunCandleTime": expected_time if not blocking else expected_time - 14400,
            "tickDryRunCandleAt": expected_at if not blocking else "2026-06-14T12:00:00+00:00",
            "expectedLatestClosedCandleTime": expected_time,
            "expectedLatestClosedCandleAt": expected_at,
            "candleAlignmentStatus": status,
            "blockingForPaperTick": blocking,
            "paperTickAllowed": not blocking,
            "safety": {"paperStateChanged": False, "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": False},
        }

    def fresh_payload_for_tick(self):
        return {
            "ok": True,
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "latestCandleTime": 2000,
            "latestCandleAt": "2026-06-14T16:00:00+00:00",
            "freshnessStatus": "FRESH",
            "blockingForPaperTick": False,
            "paperTickAllowed": True,
            "safety": {"paperStateChanged": False, "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": False},
        }

    def preview_allowed_payload(self, signal="HOLD", action="no action"):
        return {
            "ok": True,
            "previewOnly": True,
            "paperEnabled": True,
            "realTradingEnabled": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "blockingForPaperTick": False,
            "paperTickAllowed": True,
            "signal": signal,
            "proposedAction": action,
            "warnings": [],
        }

    def fake_confirmed_tick_run(self, expected_time=2000):
        def fake_run(command, text, capture_output, cwd, timeout):
            state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
            state["lastProcessedCandleTime"] = {"BTCUSDT:4h": expected_time}
            state["processedCandles"] = int(state.get("processedCandles") or 0) + 1
            state["updatedAt"] = "2026-06-14T16:01:00+00:00"
            zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
            payload = {"status": "processed", "events": 0, "openPositions": 0, "closedTrades": 0, "warnings": [], "freshness": {"BTCUSDT:4h": {"latestCandleTime": expected_time}}}
            return type("Completed", (), {"stdout": json.dumps(payload), "stderr": "", "returncode": 0})()
        return fake_run

    def init_candles_payload(self):
        now = int(datetime.now(timezone.utc).timestamp())
        return {
            "candles": [
                {"time": now - 8 * 60 * 60, "open": 49000, "high": 50000, "low": 48000, "close": 49500},
                {"time": now - 4 * 60 * 60, "open": 49500, "high": 51000, "low": 49000, "close": 50500},
                {"time": now, "open": 50500, "high": 50600, "low": 50400, "close": 50550},
            ]
        }

    def test_preview_paper_tick_refuses_if_paper_disabled(self):
        self.write_active_paper_config(enabled=False)
        payload, status = zgua_app.build_research_preview_paper_tick({})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("paperEnabled must be true", payload["blockers"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])

    def test_preview_paper_tick_refuses_if_real_trading_enabled(self):
        self.write_active_paper_config(real=True)
        payload, status = zgua_app.build_research_preview_paper_tick({})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertTrue(any("realTradingEnabled" in item for item in payload["blockers"]))

    def test_preview_paper_tick_refuses_if_exchange_orders_enabled(self):
        self.write_active_paper_config(exchange=True)
        payload, status = zgua_app.build_research_preview_paper_tick({})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("exchangeOrders must be false", payload["blockers"])

    def test_preview_paper_tick_is_read_only_and_returns_action(self):
        self.write_active_paper_config()
        journal_path = zgua_app.PAPER_STATE_PATH.parent / "reports" / "paper-journal.jsonl"
        self.assertFalse(journal_path.exists())
        state_before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        config_before = zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8")
        latest = self.recent_closed_time()
        self.set_last_processed_candle(latest - 4 * 60 * 60)
        state_before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        with patch.object(zgua_app, "PAPER_JOURNAL_PATH", journal_path), patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=latest)), patch.object(zgua_app.subprocess, "run", side_effect=self.preview_subprocess_payloads(latest)):
            payload, status = zgua_app.build_research_preview_paper_tick({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["previewOnly"])
        self.assertTrue(payload["paperEnabled"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertTrue(payload["stateUnchanged"])
        self.assertEqual(payload["stateHashBefore"], payload["stateHashAfter"])
        self.assertEqual(payload["configHashBefore"], payload["configHashAfter"])
        self.assertEqual(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"), state_before)
        self.assertEqual(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8"), config_before)
        self.assertEqual(payload["journalHashBefore"], "missing")
        self.assertEqual(payload["journalHashAfter"], "missing")
        self.assertFalse(journal_path.exists())
        self.assertEqual(payload["signal"], "BUY")
        self.assertEqual(payload["proposedAction"], "would open long")
        self.assertEqual(payload["candleAlignment"]["candleAlignmentStatus"], "ALIGNED")
        self.assertEqual(payload["proposedOrder"]["side"], "long")
        self.assertGreater(payload["proposedOrder"]["notional"], 0)

    def alignment_subprocess_payloads(self, signal_time, tick_time=None):
        tick_time = signal_time if tick_time is None else tick_time
        diagnostics = {
            "ok": True,
            "activeMarket": {"symbol": "BTCUSDT", "timeframe": "4h"},
            "latestCandle": {"time": signal_time, "close": 50000},
            "diagnostics": {"signal": "HOLD", "reason": "No entry signal.", "positionState": {"hasOpenPosition": False}},
            "warnings": [],
        }
        tick = {
            "ok": True,
            "status": "dry-run",
            "events": 0,
            "openPositions": 0,
            "closedTrades": 0,
            "warnings": [],
            "freshness": {"BTCUSDT:4h": {"latestCandleTime": tick_time}},
        }
        def fake_run(command, text, capture_output, cwd, timeout):
            payload = diagnostics if "paper_signal_diagnostics.js" in " ".join(command) else tick
            return type("Completed", (), {"stdout": json.dumps(payload), "stderr": "", "returncode": 0})()
        return fake_run

    def test_paper_candle_alignment_returns_all_timestamps(self):
        self.write_active_paper_config()
        latest = self.recent_closed_time()
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=latest)), patch.object(zgua_app.subprocess, "run", side_effect=self.alignment_subprocess_payloads(latest, latest)):
            payload, status = zgua_app.build_research_paper_candle_alignment({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["freshnessLatestCandleTime"], latest)
        self.assertEqual(payload["signalEvaluationCandleTime"], latest)
        self.assertEqual(payload["tickDryRunCandleTime"], latest)
        self.assertEqual(payload["expectedLatestClosedCandleTime"], latest)
        self.assertEqual(payload["candleAlignmentStatus"], "ALIGNED")
        self.assertFalse(payload["blockingForPaperTick"])
        self.assertTrue(payload["paperTickAllowed"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])

    def test_freshness_distinguishes_open_cached_tail_at_1700(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=1781539200)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.cached_4h_rows()):
            payload, status = zgua_app.build_research_paper_freshness({"nowUtc": "2026-06-15T17:00:00+00:00"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["latestCachedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertTrue(payload["latestCachedCandleIsOpen"])
        self.assertEqual(payload["latestOpenCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(payload["latestClosedCandleAt"], "2026-06-15T12:00:00+00:00")
        self.assertEqual(payload["latestCandleAt"], "2026-06-15T12:00:00+00:00")
        self.assertEqual(payload["freshnessStatus"], "FRESH")
        self.assertIn("Latest cached candle is open", payload["explanation"])

    def test_candle_alignment_ignores_open_tail_when_signal_and_tick_use_latest_closed(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=1781539200)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.cached_4h_rows()), patch.object(zgua_app.subprocess, "run", side_effect=self.alignment_subprocess_payloads(1781524800, 1781524800)):
            payload, status = zgua_app.build_research_paper_candle_alignment({"nowUtc": "2026-06-15T17:00:00+00:00"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["latestCachedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertTrue(payload["latestCachedCandleIsOpen"])
        self.assertEqual(payload["latestClosedCandleAt"], "2026-06-15T12:00:00+00:00")
        self.assertEqual(payload["signalEvaluationCandleAt"], "2026-06-15T12:00:00+00:00")
        self.assertEqual(payload["tickDryRunCandleAt"], "2026-06-15T12:00:00+00:00")
        self.assertEqual(payload["expectedLatestClosedCandleAt"], "2026-06-15T12:00:00+00:00")
        self.assertEqual(payload["candleAlignmentStatus"], "ALIGNED")
        self.assertTrue(payload["openTailIgnored"])
        self.assertFalse(payload["blockingForPaperTick"])
        self.assertTrue(payload["paperTickAllowed"])

    def test_candle_alignment_uses_1600_after_it_closes_at_2100(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=1781539200)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.cached_4h_rows()), patch.object(zgua_app.subprocess, "run", side_effect=self.alignment_subprocess_payloads(1781539200, 1781539200)):
            payload, status = zgua_app.build_research_paper_candle_alignment({"nowUtc": "2026-06-15T21:00:00+00:00"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["latestCachedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertFalse(payload["latestCachedCandleIsOpen"])
        self.assertEqual(payload["latestClosedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(payload["signalEvaluationCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(payload["tickDryRunCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(payload["candleAlignmentStatus"], "ALIGNED")
        self.assertFalse(payload["openTailIgnored"])

    def test_candle_mismatch_blocks_preview(self):
        self.write_active_paper_config()
        latest = self.recent_closed_time()
        lagged = latest - 4 * 60 * 60
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=latest)), patch.object(zgua_app.subprocess, "run", side_effect=self.alignment_subprocess_payloads(lagged, lagged)):
            payload, status = zgua_app.build_research_preview_paper_tick({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["proposedAction"], "blocked_by_candle_mismatch")
        self.assertTrue(payload["blockingForPaperTick"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertEqual(payload["candleAlignment"]["candleAlignmentStatus"], "MISMATCH")
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])

    def test_candle_mismatch_blocks_one_shot_paper_tick_wrapper(self):
        self.write_active_paper_config()
        latest = self.recent_closed_time()
        lagged = latest - 4 * 60 * 60
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=latest)), patch.object(zgua_app.subprocess, "run", side_effect=self.alignment_subprocess_payloads(lagged, lagged)):
            payload, status = zgua_app.run_paper_tick_once({})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertEqual(payload["candleAlignment"]["candleAlignmentStatus"], "MISMATCH")

    def test_paper_freshness_returns_stale_structure(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.stale_cache_payload()):
            payload, status = zgua_app.build_research_paper_freshness({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["timeframe"], "4h")
        self.assertEqual(payload["freshnessStatus"], "STALE")
        self.assertTrue(payload["blockingForPaperTick"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])

    def test_paper_freshness_returns_missing_structure(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.missing_cache_payload()):
            payload, status = zgua_app.build_research_paper_freshness({})
        self.assertEqual(status, 200)
        self.assertEqual(payload["freshnessStatus"], "MISSING")
        self.assertTrue(payload["blockingForPaperTick"])

    def test_preview_paper_tick_blocks_stale_data_without_tick_dry_run(self):
        self.write_active_paper_config()
        state_before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        config_before = zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8")
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.stale_cache_payload()), patch.object(zgua_app.subprocess, "run") as run_mock:
            payload, status = zgua_app.build_research_preview_paper_tick({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["previewOnly"])
        self.assertEqual(payload["proposedAction"], "blocked_by_stale_data")
        self.assertTrue(payload["blockingForPaperTick"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertEqual(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"), state_before)
        self.assertEqual(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8"), config_before)
        run_mock.assert_not_called()

    def test_stale_data_blocks_real_paper_tick_wrapper(self):
        self.write_active_paper_config()
        state_before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.stale_cache_payload()), patch.object(zgua_app.subprocess, "run") as run_mock:
            payload, status = zgua_app.run_paper_tick_once({})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertEqual(payload["freshness"]["freshnessStatus"], "STALE")
        self.assertEqual(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"), state_before)
        run_mock.assert_not_called()

    def test_refresh_active_paper_data_does_not_mutate_paper_files(self):
        self.write_active_paper_config()
        journal_path = zgua_app.PAPER_STATE_PATH.parent / "reports" / "paper-journal.jsonl"
        state_before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        config_before = zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8")
        cache_payloads = [
            self.stale_cache_payload(candles=500),
            self.stale_cache_payload(candles=500),
            self.fresh_cache_payload(candles=620),
            self.fresh_cache_payload(candles=620),
            self.fresh_cache_payload(candles=620),
        ]
        def fake_fetch(source, symbol, timeframe, limit=240, visible_charts=None):
            self.assertEqual(source, "bybit")
            self.assertEqual(symbol, "BTCUSDT")
            self.assertEqual(timeframe, "4h")
            return {"candles": [{"time": self.fresh_cache_payload()["lastCandleTime"], "close": 50000}]}
        with patch.object(zgua_app, "PAPER_JOURNAL_PATH", journal_path), patch.object(zgua_app, "inspect_bybit_cache", side_effect=cache_payloads), patch.object(zgua_app, "fetch_candles", side_effect=fake_fetch):
            payload, status = zgua_app.build_research_refresh_active_paper_data({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["refreshed"])
        self.assertEqual(payload["freshnessBefore"]["freshnessStatus"], "STALE")
        self.assertEqual(payload["freshnessAfter"]["freshnessStatus"], "FRESH")
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertTrue(payload["stateUnchanged"])
        self.assertEqual(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"), state_before)
        self.assertEqual(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8"), config_before)
        self.assertFalse(journal_path.exists())

    def test_init_active_paper_candidate_wrong_confirmation_fails_without_writes(self):
        self.write_active_paper_config()
        state_before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        with patch.object(zgua_app, "fetch_candles") as fetch_mock:
            payload, status = zgua_app.build_research_init_active_paper_candidate({"confirm": "wrong"})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"), state_before)
        fetch_mock.assert_not_called()

    def test_init_active_paper_candidate_initializes_only_btc_4h_and_preserves_params(self):
        config = self.write_active_paper_config(initialized=False)
        config_before = zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8")
        with patch.object(zgua_app, "fetch_candles", return_value=self.init_candles_payload()):
            payload, status = zgua_app.build_research_init_active_paper_candidate({"confirm": self.init_confirm_text()})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["initializedMarkets"], ["BTCUSDT:4h"])
        self.assertFalse(payload["alreadyInitialized"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertTrue(payload["paramsUnchanged"])
        self.assertTrue(payload["configUnchanged"])
        self.assertEqual(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8"), config_before)
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(sorted(state["lastProcessedCandleTime"].keys()), ["BTCUSDT:4h"])
        self.assertEqual(state["strategy"], "EmaBounceV2")
        self.assertEqual(state["paramsHash"], config["paramsHash"])
        self.assertEqual(json.loads(zgua_app.PAPER_CANDIDATE_LOCAL_PATH.read_text(encoding="utf-8"))["params"], config["params"])

    def test_init_active_paper_candidate_is_idempotent(self):
        self.write_active_paper_config(initialized=False)
        with patch.object(zgua_app, "fetch_candles", return_value=self.init_candles_payload()) as fetch_mock:
            first, first_status = zgua_app.build_research_init_active_paper_candidate({"confirm": self.init_confirm_text()})
            second, second_status = zgua_app.build_research_init_active_paper_candidate({"confirm": self.init_confirm_text()})
        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertFalse(first["alreadyInitialized"])
        self.assertTrue(second["alreadyInitialized"])
        self.assertEqual(second["initializedMarkets"], ["BTCUSDT:4h"])
        self.assertFalse(second["paperTickRan"])
        self.assertEqual(fetch_mock.call_count, 1)

    def test_preview_warning_removed_after_active_init(self):
        self.write_active_paper_config(initialized=False)
        with patch.object(zgua_app, "fetch_candles", return_value=self.init_candles_payload()):
            payload, status = zgua_app.build_research_init_active_paper_candidate({"confirm": self.init_confirm_text()})
        self.assertEqual(status, 200)
        latest = self.recent_closed_time()
        def fake_run(command, text, capture_output, cwd, timeout):
            state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
            initialized = bool((state.get("lastProcessedCandleTime") or {}).get("BTCUSDT:4h"))
            if "paper_signal_diagnostics.js" in " ".join(command):
                result = {
                    "ok": True,
                    "activeMarket": {"symbol": "BTCUSDT", "timeframe": "4h"},
                    "latestCandle": {"time": latest, "close": 50000},
                    "diagnostics": {"signal": "HOLD", "reason": "No entry signal.", "positionState": {"hasOpenPosition": False}},
                    "warnings": [],
                }
            else:
                result = {
                    "ok": True,
                    "status": "dry-run",
                    "events": 0,
                    "openPositions": 0,
                    "closedTrades": 0,
                    "warnings": [] if initialized else ["BTCUSDT 4h: Market not initialized. Run npm run paper:init first."],
                    "freshness": {"BTCUSDT:4h": {"latestCandleTime": latest}},
                }
            return type("Completed", (), {"stdout": json.dumps(result), "stderr": "", "returncode": 0})()
        with patch.object(zgua_app, "inspect_bybit_cache", return_value=self.fresh_cache_payload(latest=latest)), patch.object(zgua_app.subprocess, "run", side_effect=fake_run):
            preview, preview_status = zgua_app.build_research_preview_paper_tick({})
        self.assertEqual(preview_status, 200)
        self.assertTrue(preview["ok"])
        self.assertFalse(any("Market not initialized" in warning for warning in preview["warnings"]))

    def test_confirmed_tick_once_fails_with_missing_confirmation(self):
        self.write_active_paper_config()
        state_before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)), patch.object(zgua_app.subprocess, "run") as run_mock:
            payload, status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": ""})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["confirmationMatched"])
        self.assertEqual(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"), state_before)
        run_mock.assert_not_called()

    def test_confirmed_tick_once_fails_with_wrong_candidate_or_candle(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)), patch.object(zgua_app.subprocess, "run") as run_mock:
            wrong_candidate, candidate_status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": "RUN ONE PAPER TICK wrong 2026-06-14T16:00:00+00:00"})
            wrong_candle, candle_status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text("2026-06-14T20:00:00+00:00")})
        self.assertEqual(candidate_status, 400)
        self.assertEqual(candle_status, 400)
        self.assertFalse(wrong_candidate["confirmationMatched"])
        self.assertFalse(wrong_candle["confirmationMatched"])
        run_mock.assert_not_called()

    def test_confirmed_tick_once_fails_if_freshness_stale_or_alignment_mismatch(self):
        self.write_active_paper_config()
        stale = {**self.fresh_payload_for_tick(), "freshnessStatus": "STALE", "blockingForPaperTick": True, "paperTickAllowed": False}
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=stale), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)), patch.object(zgua_app.subprocess, "run") as run_mock:
            stale_payload, stale_status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text()})
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload("MISMATCH"), 200)), patch.object(zgua_app.subprocess, "run") as run_mock_2:
            mismatch_payload, mismatch_status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text()})
        self.assertEqual(stale_status, 400)
        self.assertEqual(mismatch_status, 400)
        self.assertFalse(stale_payload["paperTickRan"])
        self.assertFalse(mismatch_payload["paperTickRan"])
        run_mock.assert_not_called()
        run_mock_2.assert_not_called()

    def test_confirmed_tick_once_fails_if_market_uninitialized_or_unsafe_flags(self):
        self.write_active_paper_config(initialized=False)
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)):
            uninit, uninit_status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text()})
        self.write_active_paper_config(real=True)
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)):
            real, real_status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text()})
        self.write_active_paper_config(exchange=True)
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)):
            exchange, exchange_status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text()})
        self.assertEqual(uninit_status, 400)
        self.assertEqual(real_status, 400)
        self.assertEqual(exchange_status, 400)
        self.assertFalse(uninit["paperTickRan"])
        self.assertTrue(real["realTradingEnabled"])
        self.assertFalse(exchange["paperTickRan"])

    def test_confirmed_tick_once_succeeds_once_for_hold_without_trade_or_equity_change(self):
        self.write_active_paper_config()
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        state["lastProcessedCandleTime"] = {"BTCUSDT:4h": 1000}
        state["accountEquity"] = 10000
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)), patch.object(zgua_app, "build_research_preview_paper_tick", return_value=(self.preview_allowed_payload(), 200)), patch.object(zgua_app, "package_node_script_args", return_value=["node", "cli/paper_tick.js"]), patch.object(zgua_app.subprocess, "run", side_effect=self.fake_confirmed_tick_run()) as run_mock:
            payload, status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text()})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["confirmationMatched"])
        self.assertTrue(payload["tickRan"])
        self.assertTrue(payload["paperTickRan"])
        def command_for_call(call):
            positional, keyword = tuple(call)
            command = positional[0] if positional else keyword.get("args") or []
            return command if isinstance(command, list) else [str(command)]
        mutating_ticks = [
            call for call in run_mock.call_args_list
            if "paper_tick.js" in " ".join(command_for_call(call)) and "--dry-run" not in command_for_call(call)
        ]
        self.assertEqual(len(mutating_ticks), 1)
        self.assertFalse(payload["openedTrade"])
        self.assertFalse(payload["closedTrade"])
        self.assertEqual(payload["equityBefore"], payload["equityAfter"])
        self.assertEqual(payload["openPositionsBefore"], payload["openPositionsAfter"])
        self.assertEqual(payload["processedCandleAt"], 2000)
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertTrue(payload["auditPath"])

    def test_confirmed_tick_once_duplicate_same_candle_is_skipped(self):
        self.write_active_paper_config()
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        state["lastProcessedCandleTime"] = {"BTCUSDT:4h": 2000}
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.fresh_payload_for_tick()), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(), 200)), patch.object(zgua_app.subprocess, "run") as run_mock:
            payload, status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": self.tick_confirm_text()})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["alreadyProcessed"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        run_mock.assert_not_called()

    def test_paper_status_shows_processed_tick_history_without_mutation(self):
        self.write_active_paper_config()
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        state["lastProcessedCandleTime"] = {"BTCUSDT:4h": 1781524800}
        state["processedCandles"] = 1
        state["accountEquity"] = 10000
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        audit = {
            "candidateIdentity": "candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca",
            "paperTickRan": True,
            "paperStateChanged": True,
            "confirmationMatched": True,
            "previewSignal": "HOLD",
            "previewProposedAction": "no action",
            "openedTrade": False,
            "closedTrade": False,
            "equityBefore": 10000,
            "equityAfter": 10000,
        }
        zgua_app.PAPER_TICK_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        (zgua_app.PAPER_TICK_AUDIT_DIR / "tick-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
        before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        payload, status = zgua_app.build_research_paper_status({})
        after = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        self.assertEqual(status, 200)
        self.assertTrue(payload["statusSnapshotOnly"])
        self.assertFalse(payload["statusCommandRanTick"])
        self.assertEqual(payload["message"], "Read-only paper status snapshot. This command did not run a paper tick.")
        self.assertNotIn("No paper tick was run", payload["message"])
        self.assertEqual(payload["paperTickHistory"]["lastProcessedCandleAt"], "2026-06-15T12:00:00+00:00")
        self.assertEqual(payload["paperTickHistory"]["lastProcessedCandleTime"], 1781524800)
        self.assertEqual(payload["paperTickHistory"]["processedCandleCount"], 1)
        self.assertEqual(payload["paperTickHistory"]["lastProcessedTickSignal"], "HOLD")
        self.assertEqual(payload["paperTickHistory"]["lastProcessedTickAction"], "no action")
        self.assertEqual(payload["paperTickHistory"]["lastTickSignal"], "HOLD")
        self.assertEqual(payload["paperTickHistory"]["lastTickAction"], "no action")
        self.assertFalse(payload["paperTickHistory"]["lastTickOpenedTrade"])
        self.assertFalse(payload["paperTickHistory"]["lastTickClosedTrade"])
        self.assertEqual(payload["paperTickHistory"]["lastTickEquityBefore"], 10000)
        self.assertEqual(payload["paperTickHistory"]["lastTickEquityAfter"], 10000)
        self.assertTrue(payload["paperTickHistory"]["lastTickAuditPath"])
        self.assertEqual(payload["paperTickHistory"]["lastTickAuditPath"], payload["paperTickHistory"]["lastProcessedTickAuditPath"])
        self.assertFalse(payload["paperTickLastAttempt"]["skipped"])
        self.assertFalse(payload["paperTickLastAttempt"]["alreadyProcessed"])
        self.assertTrue(payload["paperTickLastAttempt"]["paperTickRan"])
        self.assertEqual(payload["duplicateGuard"]["lastProcessedCandidateKey"], audit["candidateIdentity"])
        self.assertEqual(payload["duplicateGuard"]["lastProcessedSymbol"], "BTCUSDT")
        self.assertEqual(payload["duplicateGuard"]["lastProcessedTimeframe"], "4h")
        self.assertTrue(payload["duplicateGuard"]["duplicateGuardActive"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertEqual(before, after)

    def test_paper_status_keeps_processed_tick_separate_from_skipped_duplicate_attempt(self):
        self.write_active_paper_config()
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        state["lastProcessedCandleTime"] = {"BTCUSDT:4h": 1781539200}
        state["processedCandles"] = 2
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        candidate_key = "candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca"
        processed = {
            "candidateIdentity": candidate_key,
            "paperTickRan": True,
            "paperStateChanged": True,
            "confirmationMatched": True,
            "expectedLatestClosedCandleAt": "2026-06-15T16:00:00+00:00",
            "processedCandleAt": 1781539200,
            "previewSignal": "HOLD",
            "previewProposedAction": "no action",
            "openedTrade": False,
            "closedTrade": False,
            "equityBefore": 10000,
            "equityAfter": 10000,
        }
        skipped = {
            "candidateIdentity": candidate_key,
            "alreadyProcessed": True,
            "skipped": True,
            "confirmationMatched": True,
            "paperTickRan": False,
            "paperStateChanged": False,
            "expectedLatestClosedCandleAt": "2026-06-15T16:00:00+00:00",
            "processedCandleAt": 1781539200,
            "equityBefore": 10000,
            "equityAfter": 10000,
        }
        zgua_app.PAPER_TICK_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        processed_path = zgua_app.PAPER_TICK_AUDIT_DIR / "processed.json"
        skipped_path = zgua_app.PAPER_TICK_AUDIT_DIR / "skipped.json"
        processed_path.write_text(json.dumps(processed, indent=2), encoding="utf-8")
        skipped_path.write_text(json.dumps(skipped, indent=2), encoding="utf-8")
        os.utime(processed_path, (1000, 1000))
        os.utime(skipped_path, (2000, 2000))
        before = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        payload, status = zgua_app.build_research_paper_status({})
        after = zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8")
        history = payload["paperTickHistory"]
        attempt = payload["paperTickLastAttempt"]
        self.assertEqual(status, 200)
        self.assertEqual(history["lastProcessedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(history["lastProcessedTickSignal"], "HOLD")
        self.assertEqual(history["lastProcessedTickAction"], "no action")
        self.assertEqual(history["lastTickSignal"], "HOLD")
        self.assertEqual(history["lastTickAction"], "no action")
        self.assertTrue(history["lastProcessedTickAuditPath"].endswith("processed.json"))
        self.assertEqual(history["lastTickAuditPath"], history["lastProcessedTickAuditPath"])
        self.assertTrue(attempt["lastAttemptAuditPath"].endswith("skipped.json"))
        self.assertTrue(attempt["skipped"])
        self.assertTrue(attempt["alreadyProcessed"])
        self.assertTrue(attempt["confirmationMatched"])
        self.assertFalse(attempt["paperTickRan"])
        self.assertFalse(attempt["paperStateChanged"])
        self.assertEqual(attempt["attemptedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(attempt["attemptedCandleTime"], 1781539200)
        self.assertTrue(payload["duplicateGuard"]["duplicateGuardActive"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertFalse(payload["statusCommandRanTick"])
        self.assertEqual(before, after)

    def test_paper_tick_audits_api_lists_active_candidate_audits_read_only(self):
        self.write_active_paper_config()
        candidate_key = "candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca"
        zgua_app.PAPER_TICK_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        processed = {
            "generatedAt": "2026-06-15T20:22:17+00:00",
            "command": "paper:tick-once",
            "candidateIdentity": candidate_key,
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "expectedLatestClosedCandleAt": "2026-06-15T16:00:00+00:00",
            "processedCandleAt": 1781539200,
            "confirmationMatched": True,
            "alreadyProcessed": False,
            "skipped": False,
            "paperTickRan": True,
            "tickRan": True,
            "paperStateChanged": True,
            "previewSignal": "HOLD",
            "previewProposedAction": "no action",
            "openedTrade": False,
            "closedTrade": False,
            "openPositionsBefore": 0,
            "openPositionsAfter": 0,
            "equityBefore": 10000,
            "equityAfter": 10000,
            "realTradingEnabled": False,
            "liveOrdersTouched": False,
            "returnCode": 0,
            "warnings": [],
        }
        skipped = {
            "generatedAt": "2026-06-15T20:24:40+00:00",
            "command": "paper:tick-once",
            "candidateIdentity": candidate_key,
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "expectedLatestClosedCandleAt": "2026-06-15T16:00:00+00:00",
            "processedCandleAt": 1781539200,
            "confirmationMatched": True,
            "alreadyProcessed": True,
            "skipped": True,
            "paperTickRan": False,
            "tickRan": False,
            "paperStateChanged": False,
            "openedTrade": False,
            "closedTrade": False,
            "openPositionsBefore": 0,
            "openPositionsAfter": 0,
            "equityBefore": 10000,
            "equityAfter": 10000,
            "realTradingEnabled": False,
            "liveOrdersTouched": False,
            "returnCode": 0,
            "warnings": ["duplicate candle skipped"],
        }
        other = {"candidateIdentity": "other-candidate", "paperTickRan": True}
        processed_path = zgua_app.PAPER_TICK_AUDIT_DIR / "processed.json"
        skipped_path = zgua_app.PAPER_TICK_AUDIT_DIR / "skipped.json"
        other_path = zgua_app.PAPER_TICK_AUDIT_DIR / "other.json"
        malformed_path = zgua_app.PAPER_TICK_AUDIT_DIR / "malformed.json"
        processed_path.write_text(json.dumps(processed, indent=2), encoding="utf-8")
        skipped_path.write_text(json.dumps(skipped, indent=2), encoding="utf-8")
        other_path.write_text(json.dumps(other, indent=2), encoding="utf-8")
        malformed_path.write_text("{not-json", encoding="utf-8")
        os.utime(processed_path, (1000, 1000))
        os.utime(skipped_path, (2000, 2000))
        before_hashes = zgua_app.preview_file_hashes()
        before_audits = {path.name: path.read_text(encoding="utf-8") for path in zgua_app.PAPER_TICK_AUDIT_DIR.glob("*.json")}
        with zgua_app.app.test_client() as client:
            response = client.get("/api/research/paper-tick-audits?limit=20")
            limited_response = client.get("/api/research/paper-tick-audits?limit=1")
            capped_response = client.get("/api/research/paper-tick-audits?limit=500")
            post_response = client.post("/api/research/paper-tick-audits")
            put_response = client.put("/api/research/paper-tick-audits")
            delete_response = client.delete("/api/research/paper-tick-audits")
        after_hashes = zgua_app.preview_file_hashes()
        after_audits = {path.name: path.read_text(encoding="utf-8") for path in zgua_app.PAPER_TICK_AUDIT_DIR.glob("*.json")}
        payload = response.get_json()
        rows = payload["audits"]
        summary = payload["summary"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["candidateIdentity"], candidate_key)
        self.assertEqual(summary["totalAuditsRead"], 4)
        self.assertEqual(summary["returnedCount"], 2)
        self.assertEqual(summary["processedCount"], 1)
        self.assertEqual(summary["skippedCount"], 1)
        self.assertEqual(summary["malformedAuditCount"], 1)
        self.assertEqual(summary["latestProcessedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(summary["latestProcessedTickAt"], "2026-06-15T20:22:17+00:00")
        self.assertEqual(summary["openedTradeCount"], 0)
        self.assertEqual(summary["closedTradeCount"], 0)
        self.assertEqual(summary["liveOrdersTouchedCount"], 0)
        self.assertEqual(summary["realTradingEnabledCount"], 0)
        self.assertEqual([row["auditPath"].endswith("skipped.json") for row in rows], [True, False])
        self.assertTrue(rows[0]["skipped"])
        self.assertTrue(rows[0]["alreadyProcessed"])
        self.assertFalse(rows[0]["paperTickRan"])
        self.assertEqual(rows[0]["warnings"], ["duplicate candle skipped"])
        self.assertEqual(rows[1]["processedCandleAt"], "2026-06-15T16:00:00+00:00")
        self.assertEqual(rows[1]["previewSignal"], "HOLD")
        self.assertEqual(rows[1]["previewProposedAction"], "no action")
        self.assertFalse(rows[1]["openedTrade"])
        self.assertFalse(rows[1]["closedTrade"])
        self.assertEqual(rows[1]["equityBefore"], 10000)
        self.assertEqual(rows[1]["equityAfter"], 10000)
        self.assertFalse(rows[1]["realTradingEnabled"])
        self.assertFalse(rows[1]["liveOrdersTouched"])
        self.assertEqual(limited_response.get_json()["summary"]["returnedCount"], 1)
        self.assertEqual(capped_response.get_json()["limit"], 100)
        self.assertEqual(post_response.status_code, 405)
        self.assertEqual(put_response.status_code, 405)
        self.assertEqual(delete_response.status_code, 405)
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        self.assertEqual(before_hashes, after_hashes)
        self.assertEqual(before_audits, after_audits)

    def test_paper_tick_audits_surfaces_live_safety_flags(self):
        self.write_active_paper_config()
        candidate_key = "candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca"
        zgua_app.PAPER_TICK_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        audit = {
            "candidateIdentity": candidate_key,
            "paperTickRan": True,
            "tickRan": True,
            "processedCandleAt": 1781539200,
            "realTradingEnabled": True,
            "liveOrdersTouched": True,
        }
        (zgua_app.PAPER_TICK_AUDIT_DIR / "danger.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
        payload, status = zgua_app.build_research_paper_tick_audits({})
        self.assertEqual(status, 200)
        self.assertEqual(payload["summary"]["realTradingEnabledCount"], 1)
        self.assertEqual(payload["summary"]["liveOrdersTouchedCount"], 1)
        self.assertTrue(payload["audits"][0]["realTradingEnabled"])
        self.assertTrue(payload["audits"][0]["liveOrdersTouched"])

    def tick_due_freshness(self, latest_time=2000, status="FRESH", blocking=False):
        return {
            "ok": True,
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "latestClosedCandleTime": latest_time,
            "latestClosedCandleAt": zgua_app.epoch_to_iso(latest_time),
            "latestOpenCandleTime": 2400,
            "latestOpenCandleAt": zgua_app.epoch_to_iso(2400),
            "latestCachedCandleTime": 2400,
            "latestCachedCandleAt": zgua_app.epoch_to_iso(2400),
            "freshnessStatus": status,
            "blockingForPaperTick": blocking,
            "paperTickAllowed": not blocking,
            "safety": {"paperStateChanged": False, "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": False},
        }

    def set_last_processed_candle(self, candle_time):
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        state["lastProcessedCandleTime"] = {"BTCUSDT:4h": candle_time}
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def candle_cache_rows(self, *times):
        return [{"time": time, "open": 1, "high": 1, "low": 1, "close": 1} for time in times]

    def assert_tick_due_safety(self, payload):
        self.assertFalse(payload["schedulerEnabled"])
        self.assertFalse(payload["autoTickEnabled"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])

    def operator_due_payload(self, due=False, reason=None, confirmation=None):
        confirmation = confirmation or "RUN ONE PAPER TICK candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca 1970-01-01T00:33:20+00:00"
        return {
            "ok": True,
            "schedulerEnabled": False,
            "autoTickEnabled": False,
            "tickDue": due,
            "reason": reason or ("New closed candle available." if due else "No new closed candle since last processed candle."),
            "strategy": "EmaBounceV2",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "lastProcessedCandleAt": "2026-06-15T16:00:00+00:00",
            "lastProcessedCandleTime": 1781539200,
            "latestClosedCandleAt": "2026-06-15T16:00:00+00:00",
            "latestClosedCandleTime": 1781539200,
            "latestOpenCandleAt": None,
            "latestCachedCandleAt": "2026-06-15T16:00:00+00:00",
            "freshnessStatus": "FRESH",
            "candleAlignmentStatus": "ALIGNED",
            "paperTickAllowed": due,
            "requiredConfirmation": confirmation if due else None,
            "nextSafeCommand": f'python scripts\\research_autopilot.py paper:tick-once --confirm "{confirmation}"' if due else None,
            "safety": {"paperStateChanged": False, "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": False},
        }

    def operator_preview_payload(self, signal="HOLD", action="no action", order=None, blockers=None):
        return {
            "ok": True,
            "signal": signal,
            "proposedAction": action,
            "proposedOrder": order,
            "currentPaperPosition": None,
            "blockers": blockers or [],
            "warnings": [],
            "paperTickRan": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "realTradingEnabled": False,
        }

    def operator_patch_common(self, due_payload=None, freshness=None, alignment=None, preview=None):
        freshness = freshness or self.tick_due_freshness(1781539200)
        alignment = alignment or self.tick_alignment_payload(expected_at="2026-06-15T16:00:00+00:00", expected_time=1781539200)
        due_payload = due_payload or self.operator_due_payload(False)
        patches = [
            patch.object(zgua_app, "active_paper_market_freshness", return_value=freshness),
            patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(alignment, 200)),
            patch.object(zgua_app, "build_research_paper_tick_due", return_value=(due_payload, 200)),
        ]
        if preview is not None:
            patches.append(patch.object(zgua_app, "build_research_preview_paper_tick", return_value=(preview, 200)))
        return patches

    def test_paper_tick_due_false_when_latest_closed_equals_last_processed(self):
        self.write_active_paper_config()
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        state["lastProcessedCandleTime"] = {"BTCUSDT:4h": 2000}
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(2000)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(expected_time=2000), 200)):
            payload, status = zgua_app.build_research_paper_tick_due({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["tickDue"])
        self.assertEqual(payload["reason"], "No new closed candle since last processed candle.")
        self.assertFalse(payload["paperTickAllowed"])
        self.assertIsNone(payload["requiredConfirmation"])
        self.assertIsNone(payload["nextSafeCommand"])
        self.assertEqual(payload["lastProcessedCandleTime"], 2000)
        self.assertEqual(payload["latestClosedCandleTime"], 2000)
        self.assert_tick_due_safety(payload)
        self.assertEqual(before, after)

    def test_paper_tick_due_true_when_newer_closed_candle_is_safe(self):
        self.write_active_paper_config()
        state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        state["lastProcessedCandleTime"] = {"BTCUSDT:4h": 1000}
        zgua_app.PAPER_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        before = zgua_app.preview_file_hashes()
        self.assertFalse(zgua_app.PAPER_TICK_AUDIT_DIR.exists())
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(2000)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(expected_time=2000), 200)):
            payload, status = zgua_app.build_research_paper_tick_due({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["tickDue"])
        self.assertEqual(payload["reason"], "New closed candle available.")
        self.assertTrue(payload["paperTickAllowed"])
        self.assertEqual(payload["requiredConfirmation"], "RUN ONE PAPER TICK candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca 1970-01-01T00:33:20+00:00")
        self.assertIn('paper:tick-once --confirm "RUN ONE PAPER TICK', payload["nextSafeCommand"])
        self.assertEqual(payload["strategy"], "EmaBounceV2")
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["timeframe"], "4h")
        self.assertEqual(payload["freshnessStatus"], "FRESH")
        self.assertEqual(payload["candleAlignmentStatus"], "ALIGNED")
        self.assert_tick_due_safety(payload)
        self.assertEqual(before, after)
        self.assertFalse(zgua_app.PAPER_TICK_AUDIT_DIR.exists())

    def test_paper_tick_due_allows_one_missed_closed_candle(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        next_candle = last_processed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(next_candle)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(expected_at=zgua_app.epoch_to_iso(next_candle), expected_time=next_candle), 200)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, next_candle)):
            payload, status = zgua_app.build_research_paper_tick_due({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["tickDue"])
        self.assertTrue(payload["paperTickAllowed"])
        self.assertFalse(payload["catchUpRequired"])
        self.assertEqual(payload["missedClosedCandleCount"], 1)
        self.assertEqual(payload["nextDueCandleAt"], "2026-06-15T20:00:00+00:00")
        self.assertEqual(payload["requiredConfirmation"], "RUN ONE PAPER TICK candidate-identity-v1|EmaBounceV2|BTCUSDT|4h|f09aabfcd7a47bd2|ea006941770e9dca 2026-06-15T20:00:00+00:00")
        self.assertIn("2026-06-15T20:00:00+00:00", payload["nextSafeCommand"])
        self.assertEqual(before, after)

    def test_paper_tick_due_blocks_multiple_missed_closed_candles(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(expected_at=zgua_app.epoch_to_iso(latest_missed), expected_time=latest_missed), 200)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed)):
            payload, status = zgua_app.build_research_paper_tick_due({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertFalse(payload["tickDue"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertTrue(payload["catchUpRequired"])
        self.assertFalse(payload["catchUpModeAvailable"])
        self.assertEqual(payload["reason"], "Multiple closed candles are pending; sequential catch-up is required.")
        self.assertEqual(payload["missedClosedCandleCount"], 2)
        self.assertEqual(payload["firstMissedCandleAt"], "2026-06-15T20:00:00+00:00")
        self.assertEqual(payload["latestMissedCandleAt"], "2026-06-16T00:00:00+00:00")
        self.assertEqual(payload["missedClosedCandles"], ["2026-06-15T20:00:00+00:00", "2026-06-16T00:00:00+00:00"])
        self.assertIsNone(payload["requiredConfirmation"])
        self.assertIsNone(payload["nextSafeCommand"])
        self.assert_tick_due_safety(payload)
        self.assertEqual(before, after)

    def test_paper_catch_up_plan_targets_first_missed_candle(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed, latest_missed + 4 * 60 * 60)):
            payload, status = zgua_app.build_research_paper_catch_up_plan({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["catchUpRequired"])
        self.assertTrue(payload["catchUpModeAvailable"])
        self.assertEqual(payload["missedClosedCandleCount"], 2)
        self.assertEqual(payload["targetCandleAt"], "2026-06-15T20:00:00+00:00")
        self.assertEqual(payload["requiredConfirmation"], self.catch_up_confirm_text("2026-06-15T20:00:00+00:00"))
        self.assertIn("paper:catch-up-next", payload["nextSafeCommand"])
        self.assertTrue(payload["targetCacheValidation"]["ok"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        self.assertEqual(before, after)

    def test_paper_catch_up_plan_without_gap_is_read_only(self):
        self.write_active_paper_config()
        self.set_last_processed_candle(2000)
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(2000)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(2000)):
            payload, status = zgua_app.build_research_paper_catch_up_plan({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["catchUpRequired"])
        self.assertFalse(payload["catchUpModeAvailable"])
        self.assertIsNone(payload["targetCandleAt"])
        self.assertIsNone(payload["nextSafeCommand"])
        self.assertEqual(before, after)

    def test_paper_catch_up_plan_refresh_does_not_mutate_paper_files(self):
        self.write_active_paper_config()
        before = zgua_app.preview_file_hashes()
        refresh_payload = {
            "ok": True,
            "refreshed": True,
            "paperStateChanged": False,
            "paperTickRan": False,
            "liveOrdersTouched": False,
            "realTradingEnabled": False,
        }
        with patch.object(zgua_app, "build_research_refresh_active_paper_data", return_value=(refresh_payload, 200)), patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(2000)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(1000, 2000)):
            payload, status = zgua_app.build_research_paper_catch_up_plan({"refresh": True})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["refreshAttempted"])
        self.assertTrue(payload["refreshed"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertEqual(before, after)

    def test_preview_paper_catch_up_next_evaluates_first_missed_only(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed)), patch.object(zgua_app.subprocess, "run", side_effect=self.preview_subprocess_payloads(first_missed)) as run_mock:
            payload, status = zgua_app.build_research_preview_paper_catch_up_next({})
        after = zgua_app.preview_file_hashes()
        commands = [
            self.command_text_from_call(call)
            for call in run_mock.call_args_list
            if "paper_signal_diagnostics.js" in self.command_text_from_call(call) or "paper_tick.js" in self.command_text_from_call(call)
        ]
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["catchUpPreviewOnly"])
        self.assertTrue(payload["targetReplayMode"])
        self.assertEqual(payload["targetCandleAt"], "2026-06-15T20:00:00+00:00")
        self.assertEqual(payload["nextOpenCandleAt"], "2026-06-16T00:00:00+00:00")
        self.assertEqual(payload["signal"], "BUY")
        self.assertEqual(payload["proposedAction"], "would open long")
        self.assertEqual(payload["warnings"], [])
        self.assertTrue(all(str(first_missed) in command for command in commands))
        self.assertTrue(all(str(latest_missed) not in command for command in commands))
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertEqual(before, after)

    def test_preview_paper_catch_up_next_filters_target_replay_stale_warnings(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        stale_warnings = [
            "Active-market candle data is stale; refresh=true can attempt to refresh cache before diagnostics.",
            "BTCUSDT 4h: Market data is stale; skipping paper processing.",
            "Candle cache may be stale.",
        ]
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed)), patch.object(zgua_app.subprocess, "run", side_effect=self.preview_subprocess_payloads(first_missed, diagnostic_warnings=stale_warnings[:1], tick_warnings=stale_warnings[1:])):
            payload, status = zgua_app.build_research_preview_paper_catch_up_next({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["targetReplayMode"])
        self.assertEqual(payload["targetCandleAt"], "2026-06-15T20:00:00+00:00")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["blockers"], [])
        self.assertTrue(payload["paperTickAllowed"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])

    def test_preview_paper_catch_up_next_refuses_when_not_required_or_missing_next_open(self):
        self.write_active_paper_config()
        self.set_last_processed_candle(2000)
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(2000)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(2000)):
            no_gap, no_gap_status = zgua_app.build_research_preview_paper_catch_up_next({})
        self.assertEqual(no_gap_status, 200)
        self.assertFalse(no_gap["ok"])
        self.assertIn("Catch-up is not required", no_gap["blockers"][0])
        plan = {
            "ok": True,
            "catchUpRequired": True,
            "catchUpModeAvailable": True,
            "targetCandleAt": "2026-06-15T20:00:00+00:00",
            "targetCandleTime": 1781553600,
            "requiredConfirmation": self.catch_up_confirm_text("2026-06-15T20:00:00+00:00"),
            "missedClosedCandleCount": 2,
            "targetCacheValidation": {"ok": False, "blockers": ["Next-open fill candle is missing from cache."]},
        }
        with patch.object(zgua_app, "build_research_paper_catch_up_plan", return_value=(plan, 200)):
            missing_next, missing_status = zgua_app.build_research_preview_paper_catch_up_next({})
        self.assertEqual(missing_status, 200)
        self.assertFalse(missing_next["ok"])
        self.assertIn("Next-open fill candle is missing", "; ".join(missing_next["blockers"]))

    def test_preview_paper_tick_blocks_multiple_missed_closed_candles(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(expected_at=zgua_app.epoch_to_iso(latest_missed), expected_time=latest_missed), 200)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed)), patch.object(zgua_app.subprocess, "run") as run_mock:
            payload, status = zgua_app.build_research_preview_paper_tick({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["proposedAction"], "blocked_by_missed_candle_gap")
        self.assertTrue(payload["blockingForPaperTick"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertTrue(payload["catchUpRequired"])
        self.assertEqual(payload["missedClosedCandleCount"], 2)
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])
        run_mock.assert_not_called()
        self.assertEqual(before, after)

    def test_confirmed_tick_once_refuses_multiple_missed_closed_candles(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        confirm = self.tick_confirm_text(zgua_app.epoch_to_iso(latest_missed))
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(expected_at=zgua_app.epoch_to_iso(latest_missed), expected_time=latest_missed), 200)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed)), patch.object(zgua_app.subprocess, "run") as run_mock:
            payload, status = zgua_app.build_research_confirmed_paper_tick_once({"confirm": confirm})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "Multiple closed candles are pending; sequential catch-up is required.")
        self.assertTrue(payload["catchUpRequired"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertFalse(payload["paperTickRan"])
        self.assertFalse(payload["paperStateChanged"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertIsNone(payload["requiredConfirmation"])
        run_mock.assert_not_called()
        self.assertEqual(before, after)

    def test_paper_catch_up_next_refuses_missing_or_wrong_confirmation(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed)), patch.object(zgua_app.subprocess, "run") as run_mock:
            missing, missing_status = zgua_app.build_research_paper_catch_up_next({"confirm": ""})
            wrong_latest, wrong_status = zgua_app.build_research_paper_catch_up_next({"confirm": self.catch_up_confirm_text("2026-06-16T00:00:00+00:00")})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(missing_status, 400)
        self.assertEqual(wrong_status, 400)
        self.assertFalse(missing["paperTickRan"])
        self.assertFalse(wrong_latest["paperTickRan"])
        self.assertFalse(missing["confirmationMatched"])
        self.assertFalse(wrong_latest["confirmationMatched"])
        run_mock.assert_not_called()
        self.assertEqual(before, after)

    def test_paper_catch_up_next_processes_exactly_one_first_missed_candle(self):
        self.write_active_paper_config()
        last_processed = 1781539200
        first_missed = last_processed + 4 * 60 * 60
        latest_missed = first_missed + 4 * 60 * 60
        self.set_last_processed_candle(last_processed)
        before = zgua_app.preview_file_hashes()
        preview = {
            "ok": True,
            "paperTickAllowed": True,
            "signal": "HOLD",
            "proposedAction": "no action",
            "warnings": [],
        }
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest_missed)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(last_processed, first_missed, latest_missed)), patch.object(zgua_app, "build_research_preview_paper_catch_up_next", return_value=(preview, 200)), patch.object(zgua_app, "package_node_script_args", return_value=["node", "cli/paper_tick.js"]), patch.object(zgua_app.subprocess, "run", side_effect=self.fake_confirmed_tick_run(first_missed)) as run_mock:
            payload, status = zgua_app.build_research_paper_catch_up_next({"confirm": self.catch_up_confirm_text("2026-06-15T20:00:00+00:00")})
        after_state = json.loads(zgua_app.PAPER_STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["catchUpTick"])
        self.assertTrue(payload["paperTickRan"])
        self.assertEqual(payload["targetCandleAt"], "2026-06-15T20:00:00+00:00")
        self.assertEqual(payload["processedCandleAt"], first_missed)
        self.assertEqual(after_state["lastProcessedCandleTime"]["BTCUSDT:4h"], first_missed)
        self.assertEqual(payload["missedClosedCandleCountBefore"], 2)
        self.assertEqual(payload["missedClosedCandleCountAfter"], 1)
        self.assertEqual(payload["remainingMissedClosedCandles"], ["2026-06-16T00:00:00+00:00"])
        self.assertFalse(payload["liveOrdersTouched"])
        self.assertFalse(payload["realTradingEnabled"])
        self.assertTrue(payload["auditPath"])
        audit_path = Path(payload["auditPath"])
        if not audit_path.is_absolute():
            audit_path = Path(zgua_app.app.root_path) / audit_path
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertTrue(audit["catchUpTick"])
        self.assertEqual(audit["targetCandleAt"], "2026-06-15T20:00:00+00:00")
        command = self.command_text_from_call(run_mock.call_args)
        self.assertIn("--target-candle-time", command)
        self.assertIn(str(first_missed), command)
        self.assertNotEqual(before["state"], zgua_app.preview_file_hashes()["state"])

    def test_paper_catch_up_next_refuses_already_processed_target(self):
        self.write_active_paper_config()
        target = 1781553600
        latest = target + 4 * 60 * 60
        self.set_last_processed_candle(target)
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(latest)), patch.object(zgua_app, "load_bybit_disk_cache", return_value=self.candle_cache_rows(target, latest)):
            payload, status = zgua_app.build_research_paper_catch_up_next({"confirm": self.catch_up_confirm_text("2026-06-15T20:00:00+00:00")})
        self.assertEqual(status, 400)
        self.assertFalse(payload["paperTickRan"])

    def test_paper_tick_due_false_when_freshness_is_stale(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(2000, status="STALE", blocking=True)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(expected_time=2000), 200)):
            payload, status = zgua_app.build_research_paper_tick_due({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["tickDue"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertEqual(payload["reason"], "Paper tick blocked because freshness is not safe.")
        self.assertIsNone(payload["requiredConfirmation"])
        self.assert_tick_due_safety(payload)

    def test_paper_tick_due_false_when_candle_alignment_mismatch(self):
        self.write_active_paper_config()
        with patch.object(zgua_app, "active_paper_market_freshness", return_value=self.tick_due_freshness(2000)), patch.object(zgua_app, "build_research_paper_candle_alignment", return_value=(self.tick_alignment_payload(status="MISMATCH", expected_time=2000), 200)):
            payload, status = zgua_app.build_research_paper_tick_due({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["tickDue"])
        self.assertFalse(payload["paperTickAllowed"])
        self.assertEqual(payload["reason"], "Paper tick blocked because candle alignment is not safe.")
        self.assertIsNone(payload["requiredConfirmation"])
        self.assert_tick_due_safety(payload)

    def test_paper_operator_check_without_refresh_waits_and_does_not_mutate(self):
        self.write_active_paper_config()
        before = zgua_app.preview_file_hashes()
        with ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=self.operator_due_payload(False)):
                stack.enter_context(item)
            refresh_mock = stack.enter_context(patch.object(zgua_app, "build_research_refresh_active_paper_data"))
            preview_mock = stack.enter_context(patch.object(zgua_app, "build_research_preview_paper_tick"))
            payload, status = zgua_app.build_research_paper_operator_check({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["operatorCheckOnly"])
        self.assertFalse(payload["refreshAttempted"])
        self.assertFalse(payload["refreshed"])
        self.assertEqual(payload["nextHumanAction"], "WAIT_FOR_NEXT_CLOSED_CANDLE")
        self.assertFalse(payload["dueSummary"]["tickDue"])
        self.assertIsNone(payload["previewSummary"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        self.assertFalse(payload["safety"]["autoTickEnabled"])
        self.assertFalse(payload["safety"]["schedulerEnabled"])
        refresh_mock.assert_not_called()
        preview_mock.assert_not_called()
        self.assertEqual(before, after)

    def test_paper_operator_check_refresh_does_not_mutate_paper_files(self):
        self.write_active_paper_config()
        before = zgua_app.preview_file_hashes()
        refresh_payload = {
            "ok": True,
            "refreshed": True,
            "paperStateChanged": False,
            "paperTickRan": False,
            "liveOrdersTouched": False,
            "realTradingEnabled": False,
        }
        with ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=self.operator_due_payload(False)):
                stack.enter_context(item)
            refresh_mock = stack.enter_context(patch.object(zgua_app, "build_research_refresh_active_paper_data", return_value=(refresh_payload, 200)))
            payload, status = zgua_app.build_research_paper_operator_check({"refresh": True})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["refreshAttempted"])
        self.assertTrue(payload["refreshed"])
        self.assertEqual(payload["nextHumanAction"], "WAIT_FOR_NEXT_CLOSED_CANDLE")
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        refresh_mock.assert_called_once()
        self.assertEqual(before, after)

    def test_paper_operator_check_due_hold_is_safe_to_run_single_confirmed_tick(self):
        self.write_active_paper_config()
        due = self.operator_due_payload(True)
        preview = self.operator_preview_payload(signal="HOLD", action="no action")
        with ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=due, preview=preview):
                stack.enter_context(item)
            payload, status = zgua_app.build_research_paper_operator_check({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nextHumanAction"], "SAFE_TO_RUN_SINGLE_CONFIRMED_TICK")
        self.assertTrue(payload["dueSummary"]["tickDue"])
        self.assertTrue(payload["dueSummary"]["nextSafeCommand"])
        self.assertEqual(payload["previewSummary"]["signal"], "HOLD")
        self.assertEqual(payload["previewSummary"]["proposedAction"], "no action")
        self.assertFalse(payload["safety"]["paperTickRan"])

    def test_paper_operator_check_due_order_requires_preview_review(self):
        self.write_active_paper_config()
        due = self.operator_due_payload(True)
        preview = self.operator_preview_payload(signal="BUY", action="would open long", order={"side": "buy", "notional": 100})
        with ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=due, preview=preview):
                stack.enter_context(item)
            payload, status = zgua_app.build_research_paper_operator_check({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nextHumanAction"], "REVIEW_PREVIEW_BEFORE_TICK")
        self.assertEqual(payload["previewSummary"]["proposedOrder"]["side"], "buy")
        self.assertFalse(payload["safety"]["paperTickRan"])

    def test_paper_operator_check_stale_freshness_is_blocked(self):
        self.write_active_paper_config()
        stale = self.tick_due_freshness(1781539200, status="STALE", blocking=True)
        due = self.operator_due_payload(False, reason="Paper tick blocked because freshness is not safe.")
        with ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=due, freshness=stale):
                stack.enter_context(item)
            payload, status = zgua_app.build_research_paper_operator_check({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nextHumanAction"], "BLOCKED")
        self.assertEqual(payload["freshnessSummary"]["freshnessStatus"], "STALE")
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])

    def test_paper_operator_check_candle_mismatch_is_blocked(self):
        self.write_active_paper_config()
        mismatch = self.tick_alignment_payload(status="MISMATCH", expected_time=1781539200)
        due = self.operator_due_payload(False, reason="Paper tick blocked because candle alignment is not safe.")
        with ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=due, alignment=mismatch):
                stack.enter_context(item)
            payload, status = zgua_app.build_research_paper_operator_check({})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nextHumanAction"], "BLOCKED")
        self.assertEqual(payload["alignmentSummary"]["candleAlignmentStatus"], "MISMATCH")
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])

    def test_paper_operator_check_catch_up_required(self):
        self.write_active_paper_config()
        due = {
            **self.operator_due_payload(False, reason="Multiple closed candles are pending; sequential catch-up is required."),
            "paperTickAllowed": False,
            "catchUpRequired": True,
            "catchUpModeAvailable": False,
            "missedClosedCandleCount": 2,
            "firstMissedCandleAt": "2026-06-15T20:00:00+00:00",
            "latestMissedCandleAt": "2026-06-16T00:00:00+00:00",
            "missedClosedCandles": ["2026-06-15T20:00:00+00:00", "2026-06-16T00:00:00+00:00"],
            "requiredConfirmation": None,
            "nextSafeCommand": None,
        }
        before = zgua_app.preview_file_hashes()
        with ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=due):
                stack.enter_context(item)
            preview_mock = stack.enter_context(patch.object(zgua_app, "build_research_preview_paper_tick"))
            payload, status = zgua_app.build_research_paper_operator_check({})
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nextHumanAction"], "CATCH_UP_REQUIRED")
        self.assertTrue(payload["dueSummary"]["catchUpRequired"])
        self.assertEqual(payload["dueSummary"]["missedClosedCandleCount"], 2)
        self.assertIsNone(payload["dueSummary"]["nextSafeCommand"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        preview_mock.assert_not_called()
        self.assertEqual(before, after)

    def test_paper_operator_check_api_is_get_only_and_read_only(self):
        self.write_active_paper_config()
        before = zgua_app.preview_file_hashes()
        with zgua_app.app.test_client() as client, ExitStack() as stack:
            for item in self.operator_patch_common(due_payload=self.operator_due_payload(False)):
                stack.enter_context(item)
            response = client.get("/api/research/paper-operator-check")
            post_response = client.post("/api/research/paper-operator-check")
            put_response = client.put("/api/research/paper-operator-check")
            delete_response = client.delete("/api/research/paper-operator-check")
        after = zgua_app.preview_file_hashes()
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["operatorCheckOnly"])
        self.assertFalse(payload["refreshAttempted"])
        self.assertFalse(payload["refreshed"])
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        self.assertEqual(post_response.status_code, 405)
        self.assertEqual(put_response.status_code, 405)
        self.assertEqual(delete_response.status_code, 405)
        self.assertEqual(before, after)

    def test_paper_observation_summary_is_read_only_and_counts_audits(self):
        config = self.write_active_paper_config()
        self.set_last_processed_candle(1781625600)
        zgua_app.PAPER_TICK_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        processed_audit = {
            "command": "paper:tick-once",
            "candidateIdentity": config["candidateKey"],
            "confirmationMatched": True,
            "paperTickRan": True,
            "tickRan": True,
            "paperStateChanged": True,
            "processedCandleAt": 1781625600,
            "previewSignal": "HOLD",
            "previewProposedAction": "no action",
            "signalReason": "No entry signal because fibPullbackFailed.",
            "openedTrade": False,
            "closedTrade": False,
            "equityBefore": 10000,
            "equityAfter": 10000,
            "realTradingEnabled": False,
            "liveOrdersTouched": False,
        }
        (zgua_app.PAPER_TICK_AUDIT_DIR / "processed.json").write_text(json.dumps(processed_audit), encoding="utf-8")
        before = zgua_app.preview_file_hashes()
        due = {
            **self.operator_due_payload(False, reason="No new closed candle since last processed candle."),
            "nextExpectedCandleAt": "2026-06-16T20:00:00+00:00",
            "nextExpectedCandleTime": 1781640000,
        }
        with patch.object(zgua_app, "build_research_paper_tick_due", return_value=(due, 200)):
            payload, status = zgua_app.build_research_paper_observation_summary({})
            with zgua_app.app.test_client() as client:
                response = client.get("/api/research/paper-observation-summary")
                post_response = client.post("/api/research/paper-observation-summary")
        after = zgua_app.preview_file_hashes()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["observationSummaryOnly"])
        self.assertEqual(payload["totalProcessedPaperTicks"], 1)
        self.assertEqual(payload["lastProcessedCandleAt"], "2026-06-16T16:00:00+00:00")
        self.assertEqual(payload["nextExpectedCandleAt"], "2026-06-16T20:00:00+00:00")
        self.assertEqual(payload["holdStreak"], 1)
        self.assertEqual(payload["signalsPer10Candles"], 0)
        self.assertEqual(payload["noTradeReasonCounts"]["No entry signal because fibPullbackFailed."], 1)
        self.assertFalse(payload["safety"]["paperTickRan"])
        self.assertFalse(payload["safety"]["paperStateChanged"])
        self.assertFalse(payload["safety"]["liveOrdersTouched"])
        self.assertFalse(payload["safety"]["realTradingEnabled"])
        self.assertFalse(payload["safety"]["schedulerEnabled"])
        self.assertFalse(payload["safety"]["autoTickEnabled"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(post_response.status_code, 405)
        self.assertEqual(before, after)

    def test_research_paper_operator_route_renders_main_page(self):
        with zgua_app.app.test_client() as client:
            response = client.get("/research/paper-operator")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Paper Operator", html)
        self.assertIn("research-paper-operator-panel", html)

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
        self.assertIn("Paper Candidate Readiness Checklist", script)
        self.assertIn("Required Before Enabling Paper", script)
        self.assertIn("Still disabled:", script)
        self.assertIn("readiness.verdict", script)
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
        self.assertNotIn("plan-paper-enable-candidate", template)
        self.assertNotIn("plan-paper-enable-candidate", script)
        self.assertNotIn("preview-paper-tick", template)
        self.assertNotIn("preview-paper-tick", script)
        self.assertNotIn("paper:candle-alignment", template)
        self.assertNotIn("paper:candle-alignment", script)
        self.assertNotIn("paper:tick-once", template)
        self.assertNotIn("paper:tick-once", script)
        self.assertNotIn("paper:refresh-active-data", template)
        self.assertNotIn("paper:refresh-active-data", script)
        self.assertNotIn("research-paper-candidates-enable", template)
        self.assertNotIn("research-paper-candidates-run", template)
        self.assertNotIn("research-paper-candidates-paper-tick", template)
        self.assertNotIn("research-paper-candidates-live", template)

    def test_research_paper_operator_ui_is_read_only_and_get_only(self):
        template = (Path(zgua_app.app.root_path) / "templates" / "index.html").read_text(encoding="utf-8")
        script = (Path(zgua_app.app.root_path) / "static" / "app.js").read_text(encoding="utf-8")
        styles = (Path(zgua_app.app.root_path) / "static" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("Paper Operator", template)
        self.assertIn("research-paper-operator-panel", template)
        self.assertIn("/research/paper-operator", template)
        self.assertIn('apiGet("/api/research/paper-operator-check")', script)
        self.assertIn('apiGet("/api/research/paper-tick-audits")', script)
        self.assertIn('apiGet("/api/research/paper-observation-summary")', script)
        self.assertIn("Read-only paper operator view. This page cannot run paper ticks or live orders.", script)
        self.assertIn("Paper Observation Summary", script)
        self.assertIn("Signals / 10 candles", script)
        self.assertIn("No-trade reason", script)
        self.assertIn("nextHumanAction", script)
        self.assertIn("Tick due", script)
        self.assertIn("lastProcessedCandleAt", script)
        self.assertIn("Paper Tick Audit History", script)
        self.assertIn("SKIPPED_DUPLICATE", script)
        self.assertIn("PROCESSED", script)
        self.assertIn("CATCH_UP_REQUIRED", script)
        self.assertIn("Multiple closed candles are pending. Sequential manual catch-up required. Process only the first missed candle.", script)
        self.assertIn("Catch-up confirmation", script)
        self.assertIn("Catch-up CLI command", script)
        self.assertIn("Missed closed candles", script)
        self.assertIn("First missed candle", script)
        self.assertIn("Latest missed candle", script)
        self.assertIn("Audit path", script)
        self.assertIn("paper-audit-skipped", script)
        self.assertIn("Paper tick OFF", script)
        self.assertIn("Live orders OFF", script)
        self.assertIn("Real trading OFF", script)
        self.assertIn("Auto tick OFF", script)
        self.assertIn("Scheduler OFF", script)
        self.assertIn(".paper-operator-panel", styles)
        self.assertIn(".paper-audit-history", styles)
        self.assertIn(".paper-audit-skipped", styles)
        operator_template = template.split('id="research-paper-operator-panel"', 1)[1].split("Candidate Evidence Ledger", 1)[0]
        operator_script = script.split("function renderResearchPaperOperator", 1)[1].split("function renderPaperCandidateList", 1)[0]
        self.assertNotIn("<button", operator_template.lower())
        self.assertNotIn("<button", operator_script.lower())
        self.assertNotIn('apiPost("/api/research/paper-operator-check"', script)
        self.assertNotIn('apiPut("/api/research/paper-operator-check"', script)
        self.assertNotIn('apiDelete("/api/research/paper-operator-check"', script)
        self.assertNotIn('apiPost("/api/research/paper-tick-audits"', script)
        self.assertNotIn('apiPut("/api/research/paper-tick-audits"', script)
        self.assertNotIn('apiDelete("/api/research/paper-tick-audits"', script)
        self.assertNotIn('apiPost("/api/research/paper-observation-summary"', script)
        self.assertNotIn('apiPut("/api/research/paper-observation-summary"', script)
        self.assertNotIn('apiDelete("/api/research/paper-observation-summary"', script)
        self.assertNotIn("paper-operator-refresh", template)
        self.assertNotIn("paper-operator-enable", template)
        self.assertNotIn("paper-operator-run", template)
        self.assertNotIn("paper-operator-schedule", template)
        self.assertNotIn("paper-operator-auto", template)

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
