from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    app,
    build_research_confirmed_paper_tick_once,
    build_research_enable_paper_candidate,
    build_research_history_coverage,
    build_research_init_active_paper_candidate,
    build_research_lead_review,
    build_research_multi_strategy_matrix,
    build_research_multi_strategy_optimizer_batch,
    build_research_paper_candle_alignment,
    build_research_paper_catch_up_batch,
    build_research_paper_catch_up_next,
    build_research_paper_catch_up_plan,
    build_research_paper_freshness,
    build_research_paper_observation_summary,
    build_research_paper_operator_check,
    build_research_paper_status,
    build_research_paper_tick_due,
    build_research_paper_watch_diagnostics,
    build_research_plan_paper_enable_candidate,
    build_research_preview_paper_catch_up_batch,
    build_research_preview_paper_catch_up_next,
    build_research_preview_paper_tick,
    build_research_publish_review_candidate,
    build_research_refresh_active_paper_data,
)


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def queue_counts_text(counts: dict) -> str:
    return ", ".join(f"{key}={counts.get(key, 0)}" for key in ["QUEUED", "RUNNING", "DONE", "FAILED", "SKIPPED"])


def job_label(job: dict | None) -> str:
    if not job:
        return "-"
    strategies = job.get("strategies") or ([job.get("strategy")] if job.get("strategy") else [])
    symbols = job.get("symbols") or ([job.get("symbol")] if job.get("symbol") else [])
    timeframes = job.get("timeframes") or ([job.get("timeframe")] if job.get("timeframe") else [])
    return f"{','.join(str(item) for item in strategies)} {','.join(str(item) for item in symbols)} {','.join(str(item) for item in timeframes)} {job.get('period') or ''}".strip()


def branch_label(row: dict | None) -> str:
    if not row:
        return "-"
    return f"{row.get('strategy') or '-'} {row.get('symbol') or '-'} {row.get('timeframe') or '-'} {row.get('period') or ''}".strip()


def print_status(payload: dict) -> None:
    queue = payload.get("queue") or {}
    memory = payload.get("memory") or {}
    safety = payload.get("safety") or {}
    next_job = (queue.get("nextJobs") or [None])[0]
    print(f"Queue: {queue_counts_text(queue.get('counts') or {})} length={queue.get('length', 0)}")
    print(f"Next job: {job_label(next_job)}")
    print(f"Branches: tested={memory.get('branchesTested', 0)} candidates={memory.get('candidates', 0)} reports={memory.get('sourceReports', 0)}")
    lead = (memory.get("topLeads") or [None])[0]
    rare = (memory.get("promisingButRare") or [None])[0]
    rejected = (memory.get("rejectedBranches") or [])[:3]
    print(f"Best current candidate: {branch_label(lead)}")
    print(f"Best challenger: {branch_label(rare or lead)}")
    print("Rejected branches: " + ("; ".join(branch_label(row) for row in rejected) if rejected else "-"))
    families = memory.get("strategyFamilies") or []
    if families:
        print("Strategy families:")
        for family in families[:8]:
            print(f"- {family.get('strategy')}: {family.get('familyStatus')} ({family.get('reason')})")
    skips = queue.get("lastPlanSkippedJobs") or []
    if skips:
        print("Last plan skips:")
        for item in skips[:8]:
            print(f"- {item.get('skipReason')}: {item.get('branchKey') or job_label(item)} ({item.get('detail') or '-'})")
    elif queue.get("lastPlanWarnings"):
        print("Last plan warnings: " + "; ".join(str(item) for item in queue.get("lastPlanWarnings", [])[:5]))
    print(f"Safety: researchOnly={safety.get('researchOnly')} paperEnabled={safety.get('paperEnabled')} realTradingEnabled={safety.get('realTradingEnabled')} configWritten={safety.get('configWritten')} paperStateChanged={safety.get('paperStateChanged')} liveOrdersTouched={safety.get('liveOrdersTouched')}")


def print_summary(payload: dict) -> None:
    safety = payload.get("safety") or {}
    print(payload.get("summaryText") or "No summary available.")
    if payload.get("learningEvents"):
        print("\nWhat Autopilot learned:")
        for event in payload.get("learningEvents", [])[:5]:
            print(f"- {event.get('text')}")
    print(f"\nBest current candidate: {branch_label(payload.get('bestCurrentCandidate'))}")
    print(f"Best challenger: {branch_label(payload.get('bestChallenger'))}")
    print("Rejected branches: " + ("; ".join(branch_label(row) for row in (payload.get("branchesRejected") or [])[:3]) if payload.get("branchesRejected") else "-"))
    print("Next jobs: " + ("; ".join(job_label(job) for job in (payload.get("nextRecommendedJobs") or [])[:3]) if payload.get("nextRecommendedJobs") else "-"))
    print(f"Safety: researchOnly={safety.get('researchOnly')} paperEnabled={safety.get('paperEnabled')} realTradingEnabled={safety.get('realTradingEnabled')} configWritten={safety.get('configWritten')} paperStateChanged={safety.get('paperStateChanged')} liveOrdersTouched={safety.get('liveOrdersTouched')}")


def parse_utc_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_time_label(value: str | None) -> str:
    parsed = parse_utc_iso(value)
    if parsed is None:
        return "-"
    local = parsed.astimezone()
    zone = local.tzname()
    suffix = f" {zone}" if zone else ""
    return f"{local.isoformat()}{suffix}"


def timeframe_delta(timeframe: str | None) -> timedelta | None:
    raw = str(timeframe or "").strip().lower()
    try:
        if raw.endswith("m"):
            return timedelta(minutes=int(raw[:-1]))
        if raw.endswith("h"):
            return timedelta(hours=int(raw[:-1]))
        if raw.endswith("d"):
            return timedelta(days=int(raw[:-1]))
    except ValueError:
        return None
    return None


def iso_after_interval(value: str | None, timeframe: str | None) -> str | None:
    start = parse_utc_iso(value)
    delta = timeframe_delta(timeframe)
    if start is None or delta is None:
        return None
    return (start + delta).isoformat()


def print_wait_for_next_closed_candle(status: dict, due: dict, freshness: dict) -> None:
    next_expected = due.get("nextExpectedCandleAt")
    timeframe = str(status.get("timeframe") or "").strip().lower()
    expected_close = iso_after_interval(next_expected, timeframe)
    print("\nWaiting for next closed candle:")
    print(f"Next expected candle start (UTC): {next_expected or '-'}")
    print(f"Expected candle close (UTC): {expected_close or '-'}")
    print(f"Approx local close time: {local_time_label(expected_close)}")
    print(f"Check again after candle close: {expected_close or '-'}")
    if timeframe == "4h":
        print("Normal 4h UTC close schedule: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC")
    if freshness.get("latestCachedCandleIsOpen"):
        cached = freshness.get("latestCachedCandleAt") or freshness.get("latestOpenCandleAt") or "-"
        cached_close = iso_after_interval(cached, timeframe)
        close_suffix = f" and should close at {cached_close}" if cached_close else ""
        print(f"Latest cached candle is open ({cached}){close_suffix}; paper tick ignores it until it closes.")


def print_paper_next_action(payload: dict) -> None:
    status = payload.get("statusSummary") or {}
    due = payload.get("dueSummary") or {}
    freshness = payload.get("freshnessSummary") or {}
    alignment = payload.get("alignmentSummary") or {}
    preview = payload.get("previewSummary") or {}
    safety = payload.get("safety") or {}
    action = payload.get("nextHumanAction") or "UNKNOWN"
    print(f"Next action: {action}")
    print(f"Market: {status.get('strategy') or '-'} {status.get('symbol') or '-'} {status.get('timeframe') or '-'}")
    print(f"Last processed candle: {status.get('lastProcessedCandleAt') or due.get('lastProcessedCandleAt') or '-'}")
    print(f"Latest closed candle: {freshness.get('latestClosedCandleAt') or alignment.get('expectedLatestClosedCandleAt') or '-'}")
    print(f"Next expected candle: {due.get('nextExpectedCandleAt') or '-'}")
    print(f"Missed candles: {due.get('missedClosedCandleCount', 0)}")
    print(f"Alignment: {alignment.get('candleAlignmentStatus') or '-'}")
    print(f"Freshness: {freshness.get('freshnessStatus') or '-'}")
    print(f"Reason: {due.get('reason') or '-'}")
    print(f"Open positions: {status.get('openPositions', 0)}")
    print(f"Last signal/action: {status.get('lastProcessedTickSignal') or '-'} / {status.get('lastProcessedTickAction') or '-'}")
    if preview:
        print(f"Preview: {preview.get('signal') or '-'} / {preview.get('proposedAction') or '-'}")
    if action == "WAIT_FOR_NEXT_CLOSED_CANDLE":
        print_wait_for_next_closed_candle(status, due, freshness)
    if due.get("nextSafeCommand"):
        print("\nRun next:")
        print(due.get("nextSafeCommand"))
    elif due.get("catchUpNextSafeCommand"):
        print("\nCatch-up required:")
        print(due.get("catchUpNextSafeCommand"))
    else:
        print("\nRun next: -")
    if str(freshness.get("freshnessStatus") or "").upper() == "STALE":
        print("\nRefresh stale data:")
        print("python scripts\\research_autopilot.py paper:refresh-active-data")
        print("python scripts\\research_autopilot.py paper:freshness")
    blockers = (preview.get("blockers") or []) + (payload.get("blockers") or [])
    warnings = (preview.get("warnings") or []) + (payload.get("warnings") or [])
    print("Blockers: " + ("; ".join(str(item) for item in blockers) if blockers else "-"))
    print("Warnings: " + ("; ".join(str(item) for item in warnings) if warnings else "-"))
    print(f"Safety: realTradingEnabled={safety.get('realTradingEnabled')} liveOrdersTouched={safety.get('liveOrdersTouched')} schedulerEnabled={safety.get('schedulerEnabled')} autoTickEnabled={safety.get('autoTickEnabled')}")


def print_paper_watch_diagnostics(payload: dict) -> None:
    search = payload.get("search") or {}
    safety = payload.get("safety") or {}
    print("Watch diagnostics: " + ("OK" if payload.get("ok") else "CHECK"))
    print(f"Period: {search.get('period') or '-'} limit={search.get('limit') or '-'} refresh={search.get('refresh')}")
    for row in payload.get("rows") or []:
        rec = row.get("recommendation") or {}
        stale = "STALE" if row.get("freshnessStale") else "FRESH"
        print(f"- {row.get('symbol')} {row.get('timeframe')}: {stale}, trades={row.get('tradeCount')}, signalRate={row.get('signalRatePct')}%, mainBlocker={row.get('mainBlocker') or '-'}")
        if rec.get("action") or rec.get("reason"):
            print(f"  Recommendation: {rec.get('action') or '-'} - {rec.get('reason') or '-'}")
        if row.get("warnings"):
            print("  Warnings: " + "; ".join(str(item) for item in row.get("warnings") or []))
    if not payload.get("rows"):
        print("- No diagnostic watch markets configured.")
    print(f"Safety: paperTickRan={safety.get('paperTickRan')} paperStateChanged={safety.get('paperStateChanged')} liveOrdersTouched={safety.get('liveOrdersTouched')} realTradingEnabled={safety.get('realTradingEnabled')}")


def print_focused_optimizer(payload: dict) -> None:
    search = payload.get("search") or {}
    summary = payload.get("summary") or {}
    rec = summary.get("recommendation") or {}
    print("Focused optimizer: " + ("OK" if payload.get("ok") else "CHECK"))
    print(f"Markets: {','.join(str(item) for item in (search.get('symbols') or [])) or search.get('symbols') or '-'}")
    print(f"Timeframes: {','.join(str(item) for item in (search.get('timeframes') or [])) or search.get('timeframes') or '-'}")
    print(f"Strategies: {','.join(str(item) for item in (search.get('strategies') or [])) or search.get('strategies') or '-'}")
    limit_note = search.get("limit") or "auto"
    cap_note = search.get("optimizerAutoLimitCap")
    cap_suffix = f" autoCap={cap_note}" if cap_note else ""
    print(f"Period: {search.get('period') or '-'} limit={limit_note}{cap_suffix} maxCombosPerStrategy={search.get('maxCombosPerStrategy') or '-'}")
    print(f"Recommendation: {rec.get('action') or '-'} - {rec.get('reason') or '-'}")
    print(f"Replacement eligible: {summary.get('replacementEligibleCount', 0)}")
    print(f"Reproducible candidates: {summary.get('reproducibleCandidateCount', 0)}")
    print(f"Quality rejected: {summary.get('rejectedByQualityGateCount', 0)}")
    timings = payload.get("timings") or {}
    if timings:
        elapsed = timings.get("elapsedMs")
        elapsed_text = f"{round(float(elapsed) / 1000, 2)}s" if elapsed is not None else "-"
        print(f"Runtime: elapsed={elapsed_text} evaluatedBacktests={timings.get('evaluatedCandidateBacktests', '-')} plannedBacktests={timings.get('plannedCandidateBacktests', '-')} budgetHit={timings.get('runtimeBudgetHit')}")
        strategy_timings = timings.get("strategyTimings") or []
        if strategy_timings:
            print("Strategy timing:")
            for item in strategy_timings[:8]:
                print(f"- {item.get('strategy')} {item.get('symbol')} {item.get('timeframe')}: {item.get('evaluatedCombinations')}/{item.get('plannedCombinations')} combos in {round(float(item.get('elapsedMs') or 0) / 1000, 2)}s")
    if payload.get("savedPath"):
        print(f"Saved report: {payload.get('savedPath')}")
    print("Top rows:")
    rows = payload.get("rows") or []
    for row in rows[:8]:
        tier = row.get("finalCandidateTier") or row.get("qualityGateStatus") or row.get("status") or "-"
        reasons = row.get("replacementRejectionReasons") or row.get("qualityGateReasons") or []
        reason = "; ".join(str(item) for item in reasons[:3]) if reasons else row.get("mainFailureReason") or "-"
        print(
            f"- #{row.get('rank') or '-'} {row.get('strategy') or '-'} {row.get('symbol') or '-'} {row.get('timeframe') or row.get('interval') or '-'} "
            f"{row.get('status') or '-'} tier={tier} trades={row.get('trades')} return={row.get('totalReturnPct')}% "
            f"PF={row.get('profitFactor')} DD={row.get('maxDrawdownPct')} reason={reason}"
        )
    if not rows:
        print("- No rows returned.")
    if payload.get("warnings"):
        print("Warnings: " + "; ".join(str(item) for item in payload.get("warnings") or []))
    print("Safety: readOnly=True paperTickRan=False paperStateChanged=False liveOrdersTouched=False realTradingEnabled=False")


def compact_reasons(row: dict, limit: int = 3) -> str:
    reasons = row.get("replacementRejectionReasons") or []
    if reasons:
        return "; ".join(str(item) for item in reasons[:limit])
    if row.get("mainFailureReason"):
        return str(row.get("mainFailureReason"))
    warnings = row.get("warnings") or []
    if warnings:
        return "; ".join(str(item) for item in warnings[:limit])
    if row.get("replacementEligible"):
        return "replacement_eligible"
    return "-"


def print_watch_matrix(payload: dict) -> None:
    search = payload.get("search") or {}
    summary = payload.get("summary") or {}
    rec = summary.get("recommendation") or {}
    rows = payload.get("rows") or []
    print("Watch matrix: " + ("OK" if payload.get("ok") else "CHECK"))
    print(f"Markets: {','.join(str(item) for item in (search.get('symbols') or [])) or search.get('symbols') or '-'}")
    print(f"Timeframes: {','.join(str(item) for item in (search.get('timeframes') or [])) or search.get('timeframes') or '-'}")
    print(f"Strategies: {','.join(str(item) for item in (search.get('strategies') or [])) or search.get('strategies') or '-'}")
    print(f"Period: {search.get('period') or '-'} rows={len(rows)}")
    print(f"Pass/Warn: {summary.get('passCount', 0)}  Fail: {summary.get('failCount', 0)}  Unsupported: {summary.get('unsupportedCount', 0)}")
    print(f"Replacement eligible: {summary.get('replacementEligibleCount', 0)}")
    print(f"Recommendation: {rec.get('action') or '-'} - {rec.get('reason') or '-'}")
    print("")
    print("Rank  Symbol     TF    Strategy                  Params            Status     Trades  Ret%    PF     DD%    Win%   Tier          Why")
    print("----  ---------  ----  ------------------------  ----------------  ---------  ------  ------  -----  ------  -----  ------------  ------------------------------")
    for row in rows:
        tier = row.get("evidenceTier") or row.get("qualityStatus") or "-"
        why = compact_reasons(row)
        print(
            f"{str(row.get('rank') or '-'):>4}  "
            f"{str(row.get('symbol') or '-')[:9]:<9}  "
            f"{str(row.get('timeframe') or '-')[:4]:<4}  "
            f"{str(row.get('strategy') or '-')[:24]:<24}  "
            f"{str(row.get('paramsSource') or '-')[:16]:<16}  "
            f"{str(row.get('status') or '-')[:9]:<9}  "
            f"{str(row.get('trades') if row.get('trades') is not None else '-'):>6}  "
            f"{str(row.get('totalReturnPct') if row.get('totalReturnPct') is not None else '-'):>6}  "
            f"{str(row.get('profitFactor') if row.get('profitFactor') is not None else '-'):>5}  "
            f"{str(row.get('maxDrawdownPct') if row.get('maxDrawdownPct') is not None else '-'):>6}  "
            f"{str(row.get('winRate') if row.get('winRate') is not None else '-'):>5}  "
            f"{str(tier)[:12]:<12}  "
            f"{why[:120]}"
        )
    if not rows:
        print("- No matrix rows returned.")
    if summary.get("rankingExplanation"):
        print(f"\nRanking: {summary.get('rankingExplanation')}")
    if summary.get("recommendationExplanation"):
        print(f"Decision rule: {summary.get('recommendationExplanation')}")
    if payload.get("warnings"):
        print("Warnings: " + "; ".join(str(item) for item in payload.get("warnings") or []))
    print("Safety: readOnly=True paperTickRan=False paperStateChanged=False liveOrdersTouched=False realTradingEnabled=False")


def print_history_coverage(payload: dict) -> None:
    search = payload.get("search") or {}
    rows = payload.get("rows") or []
    print("History coverage: " + ("OK" if payload.get("ok") else "CHECK"))
    print(f"Markets: {','.join(str(item) for item in (search.get('symbols') or [])) or search.get('symbols') or '-'}")
    print(f"Timeframes: {','.join(str(item) for item in (search.get('timeframes') or [])) or search.get('timeframes') or '-'}")
    print(f"Period: {search.get('period') or '-'} limit={search.get('limit') or '-'} refresh={search.get('refresh')}")
    print("")
    print("Symbol     TF    Need candles  Requested  Returned  Coverage days  First candle              Last candle               Source")
    print("---------  ----  ------------  ---------  --------  -------------  ------------------------  ------------------------  ----------------")
    for row in rows:
        source_bits = []
        source_bits.append("cache" if row.get("cacheHit") else "fetch")
        if row.get("fetchedFromBybit"):
            source_bits.append(f"bybitRequests={row.get('bybitRequests')}")
        warnings = row.get("warnings") or []
        source = ",".join(source_bits)
        print(
            f"{str(row.get('symbol') or '-')[:9]:<9}  "
            f"{str(row.get('timeframe') or '-')[:4]:<4}  "
            f"{str(row.get('expectedPeriodCandles') if row.get('expectedPeriodCandles') is not None else '-'):>12}  "
            f"{str(row.get('requestedCandles') if row.get('requestedCandles') is not None else '-'):>9}  "
            f"{str(row.get('returnedCandles') if row.get('returnedCandles') is not None else '-'):>8}  "
            f"{str(row.get('approxCoverageDays') if row.get('approxCoverageDays') is not None else '-'):>13}  "
            f"{str(row.get('firstCandleAt') or '-')[:24]:<24}  "
            f"{str(row.get('lastCandleAt') or '-')[:24]:<24}  "
            f"{source}"
        )
        if warnings:
            print("  Warnings: " + "; ".join(str(item) for item in warnings))
        if row.get("error"):
            print(f"  Error: {row.get('error')}")
        expected = row.get("expectedPeriodCandles")
        requested = row.get("requestedCandles")
        if expected and requested and requested < expected:
            print(f"  To use full requested period in optimizer, rerun with --limit {expected} or --limit period.")
    if not rows:
        print("- No coverage rows returned.")
    if payload.get("warnings"):
        print("Warnings: " + "; ".join(str(item) for item in payload.get("warnings") or []))
    print("Safety: readOnly=True paperTickRan=False paperStateChanged=False liveOrdersTouched=False realTradingEnabled=False")


def print_lead_review(payload: dict) -> None:
    search = payload.get("search") or {}
    lead = payload.get("lead") or {}
    baseline = payload.get("baseline") or {}
    evidence = payload.get("evidence") or {}
    activity = evidence.get("activity") or {}
    comparison = payload.get("comparison") or {}
    eligibility = payload.get("replacementEligibility") or {}
    verdict = payload.get("verdict") or {}
    print("Lead review: " + ("OK" if payload.get("ok") else "CHECK"))
    print(f"Lead: {lead.get('strategy') or search.get('leadStrategy') or '-'} {lead.get('symbol') or search.get('leadSymbol') or '-'} {lead.get('timeframe') or search.get('leadTimeframe') or '-'} params={lead.get('paramsSource') or '-'}")
    print(f"Baseline: {baseline.get('strategy') or search.get('baselineStrategy') or '-'} {baseline.get('symbol') or search.get('baselineSymbol') or '-'} {baseline.get('timeframe') or search.get('baselineTimeframe') or '-'}")
    print(f"Period: {search.get('period') or '-'}")
    lead_activity = activity.get("lead") or {}
    base_activity = activity.get("baseline") or {}
    print(f"Lead activity: {lead_activity.get('status') or '-'} trades={lead_activity.get('trades')} return={lead_activity.get('totalReturnPct')}% PF={lead_activity.get('profitFactor')} DD={lead_activity.get('maxDrawdownPct')}")
    print(f"Baseline activity: {base_activity.get('status') or '-'} trades={base_activity.get('trades')} return={base_activity.get('totalReturnPct')}% PF={base_activity.get('profitFactor')} DD={base_activity.get('maxDrawdownPct')}")
    print(f"Comparison: winner={comparison.get('winner') or '-'} {comparison.get('tradeoffSummary') or ''}".strip())
    print(f"Replacement eligible: {eligibility.get('eligible')} reasons=" + ("; ".join(str(item) for item in (eligibility.get("reasons") or [])) if eligibility.get("reasons") else "-"))
    print(f"Verdict: {verdict.get('action') or '-'} - {verdict.get('reason') or '-'}")
    resolver = payload.get("resolver") or {}
    resolver_source = resolver.get("source") or {}
    if resolver_source:
        requested = resolver_source.get("requested") or {}
        print(
            "Resolver: "
            f"source={lead.get('paramsSource') or '-'} "
            f"batchFile={resolver_source.get('batchFile') or '-'} "
            f"requested={requested.get('strategy') or '-'} {requested.get('symbol') or '-'} {requested.get('timeframe') or '-'} "
            f"rows={resolver_source.get('totalRows', '-')} "
            f"candidates={resolver_source.get('candidateRows', '-')} "
            f"withParams={resolver_source.get('rowsWithParams', '-')} "
            f"matched={resolver_source.get('matchedRows', '-')} "
            f"selectedRank={resolver_source.get('selectedRank') or '-'}"
        )
        if resolver_source.get("selectedParamKeys"):
            print("Resolved param keys: " + ", ".join(str(item) for item in resolver_source.get("selectedParamKeys") or []))
        batch_search = resolver_source.get("batchSearch") or {}
        if batch_search:
            batch_requested = batch_search.get("requested") or {}
            print(
                "Batch search: "
                f"batchFile={batch_search.get('batchFile') or '-'} "
                f"requested={batch_requested.get('strategy') or '-'} {batch_requested.get('symbol') or '-'} {batch_requested.get('timeframe') or '-'} "
                f"rows={batch_search.get('totalRows', '-')} "
                f"candidates={batch_search.get('candidateRows', '-')} "
                f"withParams={batch_search.get('rowsWithParams', '-')} "
                f"matched={batch_search.get('matchedRows', '-')}"
            )
    checks = [
        ("Robustness", (evidence.get("robustness") or {}).get("status")),
        ("Stress", (evidence.get("feeSlippageStress") or {}).get("status")),
        ("Walk-forward", (evidence.get("walkForward") or {}).get("status")),
        ("Regime", (evidence.get("regimeBreakdown") or {}).get("status")),
        ("Deep compare", (evidence.get("deepCompare") or {}).get("status")),
    ]
    print("Checks: " + "; ".join(f"{name}={status or '-'}" for name, status in checks))
    if payload.get("savedPath"):
        print(f"Saved report: {payload.get('savedPath')}")
    if payload.get("error"):
        print(f"Error: {payload.get('error')}")
    if payload.get("warnings"):
        print("Warnings: " + "; ".join(str(item) for item in payload.get("warnings") or []))
    print("Safety: readOnly=True paperTickRan=False paperStateChanged=False liveOrdersTouched=False realTradingEnabled=False")


def get_json(client, path: str) -> tuple[dict, int]:
    response = client.get(path)
    payload = response.get_json() or {"ok": False, "error": response.get_data(as_text=True)}
    return payload, response.status_code


def post_json(client, path: str, body: dict | None = None) -> tuple[dict, int]:
    response = client.post(path, json=body or {})
    payload = response.get_json() or {"ok": False, "error": response.get_data(as_text=True)}
    return payload, response.status_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Run safe research-only Autopilot queue commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--json", action="store_true")
    plan = sub.add_parser("plan")
    plan.add_argument("--max-jobs", type=int, default=5)
    plan.add_argument("--mode", choices=["conservative", "balanced", "exploratory"], default="balanced")
    plan.add_argument("--include-cooled", action="store_true")
    plan.add_argument("--force-strategy")
    plan.add_argument("--force-branch")
    sub.add_parser("run-next")
    batch = sub.add_parser("run-batch")
    batch.add_argument("--max-jobs", type=int, default=3, help="Requested batch size. The API applies a safety cap of 3 jobs per batch.")
    summarize = sub.add_parser("summarize")
    summarize.add_argument("--json", action="store_true")
    reset = sub.add_parser("reset-queue")
    reset.add_argument("--confirm", action="store_true")
    backfill = sub.add_parser("backfill-memory")
    backfill.add_argument("--file-limit", type=int, default=250)
    dossier = sub.add_parser("candidate-dossier")
    dossier.add_argument("--strategy", required=True)
    dossier.add_argument("--symbol", required=True)
    dossier.add_argument("--timeframe", required=True)
    prepare = sub.add_parser("prepare-paper-candidate")
    prepare.add_argument("--strategy", required=True)
    prepare.add_argument("--symbol", required=True)
    prepare.add_argument("--timeframe", required=True)
    publish = sub.add_parser("publish-review-candidate")
    publish.add_argument("--strategy", required=True)
    publish.add_argument("--symbol", required=True)
    publish.add_argument("--timeframe", required=True)
    plan_paper = sub.add_parser("plan-paper-enable-candidate")
    plan_paper.add_argument("--strategy", required=True)
    plan_paper.add_argument("--symbol", required=True)
    plan_paper.add_argument("--timeframe", required=True)
    enable_paper = sub.add_parser("enable-paper-candidate")
    enable_paper.add_argument("--strategy", required=True)
    enable_paper.add_argument("--symbol", required=True)
    enable_paper.add_argument("--timeframe", required=True)
    enable_paper.add_argument("--confirm", required=True)
    sub.add_parser("preview-paper-tick")
    sub.add_parser("preview-paper-catch-up-next")
    preview_catch_up_batch = sub.add_parser("preview-paper-catch-up-batch")
    preview_catch_up_batch.add_argument("--limit", type=int, default=5)
    sub.add_parser("paper:freshness")
    sub.add_parser("paper:candle-alignment")
    sub.add_parser("paper:refresh-active-data")
    init_active = sub.add_parser("paper:init-active-candidate")
    init_active.add_argument("--confirm", required=True)
    tick_once = sub.add_parser("paper:tick-once")
    tick_once.add_argument("--confirm", required=True)
    sub.add_parser("paper:status")
    sub.add_parser("paper:tick-due")
    watch_diagnostics = sub.add_parser("paper:watch-diagnostics")
    watch_diagnostics.add_argument("--period", default="90d")
    watch_diagnostics.add_argument("--limit", default="auto")
    watch_diagnostics.add_argument("--refresh", action="store_true")
    watch_diagnostics.add_argument("--json", action="store_true")
    focused_optimizer = sub.add_parser("research:focused-optimizer")
    focused_optimizer.add_argument("--symbols", default="BTCUSDT,SOLUSDT,BNBUSDT,ETHUSDT,XRPUSDT")
    focused_optimizer.add_argument("--timeframes", default="15m,1h")
    focused_optimizer.add_argument("--strategies", default="SimpleAtrTrendV2,EmaBounceV2,PullbackReclaimV2,BreakoutRetestV2,RangeExpansionV2,RelativeStrengthV2")
    focused_optimizer.add_argument("--period", default="365d")
    focused_optimizer.add_argument("--max-candidates", default="40")
    focused_optimizer.add_argument("--max-combos-per-strategy", default="80")
    focused_optimizer.add_argument("--top-n", default="20")
    focused_optimizer.add_argument("--repro-top-n", default="6")
    focused_optimizer.add_argument("--repro-reruns", default="2")
    focused_optimizer.add_argument("--timeout-seconds", default="900")
    focused_optimizer.add_argument("--runtime-budget-seconds", default="auto")
    focused_optimizer.add_argument("--limit", default="auto")
    focused_optimizer.add_argument("--optimizer-params", default="")
    focused_optimizer.add_argument("--optimizer-base-params", default="")
    focused_optimizer.add_argument("--optimizer-grid-overrides", default="")
    focused_optimizer.add_argument("--no-walk-forward", action="store_true")
    focused_optimizer.add_argument("--no-stress", action="store_true")
    focused_optimizer.add_argument("--no-repro", action="store_true")
    focused_optimizer.add_argument("--no-save", action="store_true")
    focused_optimizer.add_argument("--json", action="store_true")
    watch_matrix = sub.add_parser("research:watch-matrix")
    watch_matrix.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT")
    watch_matrix.add_argument("--timeframes", default="15m,1h,4h")
    watch_matrix.add_argument("--strategies", default="SimpleAtrTrendV2,EmaBounceV2,PullbackReclaimV2,BreakoutRetestV2,RangeExpansionV2,RelativeStrengthV2")
    watch_matrix.add_argument("--period", default="365d")
    watch_matrix.add_argument("--limit", default="auto")
    watch_matrix.add_argument("--max-rows", default="100")
    watch_matrix.add_argument("--timeout-seconds", default="600")
    watch_matrix.add_argument("--include-stress", action="store_true")
    watch_matrix.add_argument("--include-walk-forward", action="store_true")
    watch_matrix.add_argument("--json", action="store_true")
    history_coverage = sub.add_parser("research:history-coverage")
    history_coverage.add_argument("--symbols", default="SOLUSDT")
    history_coverage.add_argument("--timeframes", default="15m")
    history_coverage.add_argument("--period", default="730d")
    history_coverage.add_argument("--limit", default="auto")
    history_coverage.add_argument("--timeout-seconds", default="420")
    history_coverage.add_argument("--refresh", action="store_true")
    history_coverage.add_argument("--json", action="store_true")
    lead_review = sub.add_parser("research:lead-review")
    lead_review.add_argument("--strategy", default="SimpleAtrTrendV2")
    lead_review.add_argument("--symbol", default="ETHUSDT")
    lead_review.add_argument("--timeframe", default="1h")
    lead_review.add_argument("--period", default="365d")
    lead_review.add_argument("--limit", default="period")
    lead_review.add_argument("--batch-file")
    lead_review.add_argument("--max-combos-per-strategy", default="80")
    lead_review.add_argument("--top-n", default="10")
    lead_review.add_argument("--timeout-seconds", default="900")
    lead_review.add_argument("--review-timeout-seconds", default="900")
    lead_review.add_argument("--no-robustness", action="store_true")
    lead_review.add_argument("--no-stress", action="store_true")
    lead_review.add_argument("--no-walk-forward", action="store_true")
    lead_review.add_argument("--no-regime", action="store_true")
    lead_review.add_argument("--no-deep-compare", action="store_true")
    lead_review.add_argument("--save", action="store_true")
    lead_review.add_argument("--json", action="store_true")
    observation_summary = sub.add_parser("paper:observation-summary")
    observation_summary.add_argument("--limit", type=int, default=20)
    catch_up_plan = sub.add_parser("paper:catch-up-plan")
    catch_up_plan.add_argument("--refresh", action="store_true")
    catch_up_next = sub.add_parser("paper:catch-up-next")
    catch_up_next.add_argument("--confirm", required=True)
    catch_up_batch = sub.add_parser("paper:catch-up-batch")
    catch_up_batch.add_argument("--limit", type=int, default=5)
    catch_up_batch.add_argument("--confirm", required=True)
    operator_check = sub.add_parser("paper:operator-check")
    operator_check.add_argument("--refresh", action="store_true")
    next_action = sub.add_parser("paper:next-action")
    next_action.add_argument("--refresh", action="store_true")
    next_action.add_argument("--json", action="store_true")
    args = parser.parse_args()

    with app.test_client() as client:
        if args.command == "status":
            payload, status = get_json(client, "/api/research/autopilot/status")
        elif args.command == "plan":
            payload, status = post_json(client, "/api/research/autopilot/plan", {
                "maxJobs": args.max_jobs,
                "planningMode": args.mode,
                "includeCooled": args.include_cooled,
                "forceStrategy": args.force_strategy,
                "forceBranch": args.force_branch,
            })
        elif args.command == "run-next":
            payload, status = post_json(client, "/api/research/autopilot/run-next")
        elif args.command == "run-batch":
            payload, status = post_json(client, "/api/research/autopilot/run-batch", {"maxJobs": args.max_jobs})
        elif args.command == "summarize":
            payload, status = get_json(client, "/api/research/autopilot/summary")
        elif args.command == "reset-queue":
            payload, status = post_json(client, "/api/research/autopilot/reset-queue", {"confirm": args.confirm})
        elif args.command == "backfill-memory":
            payload, status = post_json(client, "/api/research/autopilot/backfill-memory", {"fileLimit": args.file_limit})
        elif args.command == "candidate-dossier":
            payload, status = post_json(client, "/api/research/autopilot/candidate-dossier", {
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "prepare-paper-candidate":
            payload, status = post_json(client, "/api/research/autopilot/prepare-paper-candidate", {
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "publish-review-candidate":
            payload, status = build_research_publish_review_candidate({
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "plan-paper-enable-candidate":
            payload, status = build_research_plan_paper_enable_candidate({
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
            })
        elif args.command == "enable-paper-candidate":
            payload, status = build_research_enable_paper_candidate({
                "strategy": args.strategy,
                "symbol": args.symbol,
                "timeframe": args.timeframe,
                "confirm": args.confirm,
            })
        elif args.command == "paper:status":
            payload, status = build_research_paper_status({})
        elif args.command == "paper:tick-due":
            payload, status = build_research_paper_tick_due({})
        elif args.command == "paper:watch-diagnostics":
            payload, status = build_research_paper_watch_diagnostics({
                "period": args.period,
                "limit": args.limit,
                "refresh": args.refresh,
            })
        elif args.command == "research:focused-optimizer":
            payload, status = build_research_multi_strategy_optimizer_batch({
                "symbols": args.symbols,
                "timeframes": args.timeframes,
                "strategies": args.strategies,
                "period": args.period,
                "maxCandidates": args.max_candidates,
                "maxCombosPerStrategy": args.max_combos_per_strategy,
                "topN": args.top_n,
                "includeWalkForward": "false" if args.no_walk_forward else "true",
                "includeStress": "false" if args.no_stress else "true",
                "includeReproAudit": "false" if args.no_repro else "true",
                "reproTopN": args.repro_top_n,
                "reproReruns": args.repro_reruns,
                "runtimeBudgetSeconds": args.runtime_budget_seconds,
                "save": "false" if args.no_save else "true",
                "limit": args.limit,
                "optimizerParams": args.optimizer_params,
                "optimizerBaseParams": args.optimizer_base_params,
                "optimizerGridOverrides": args.optimizer_grid_overrides,
                "timeout_seconds": args.timeout_seconds,
            })
        elif args.command == "research:watch-matrix":
            payload, status = build_research_multi_strategy_matrix({
                "symbols": args.symbols,
                "timeframes": args.timeframes,
                "strategies": args.strategies,
                "period": args.period,
                "limit": args.limit,
                "maxRows": args.max_rows,
                "includeStress": "true" if args.include_stress else "false",
                "includeWalkForward": "true" if args.include_walk_forward else "false",
                "timeout_seconds": args.timeout_seconds,
            })
        elif args.command == "research:history-coverage":
            payload, status = build_research_history_coverage({
                "symbols": args.symbols,
                "timeframes": args.timeframes,
                "period": args.period,
                "limit": args.limit,
                "refresh": args.refresh,
                "timeout_seconds": args.timeout_seconds,
            })
        elif args.command == "research:lead-review":
            request = {
                "leadStrategy": args.strategy,
                "leadSymbol": args.symbol,
                "leadTimeframe": args.timeframe,
                "period": args.period,
                "limit": args.limit,
                "maxCombosPerStrategy": args.max_combos_per_strategy,
                "topN": args.top_n,
                "includeRobustness": "false" if args.no_robustness else "true",
                "includeStress": "false" if args.no_stress else "true",
                "includeWalkForward": "false" if args.no_walk_forward else "true",
                "includeRegime": "false" if args.no_regime else "true",
                "includeDeepCompare": "false" if args.no_deep_compare else "true",
                "save": "true" if args.save else "false",
                "timeout_seconds": args.timeout_seconds,
                "review_timeout_seconds": args.review_timeout_seconds,
            }
            if args.batch_file:
                request["batchFile"] = args.batch_file
            payload, status = build_research_lead_review(request)
        elif args.command == "paper:observation-summary":
            payload, status = build_research_paper_observation_summary({"limit": args.limit})
        elif args.command == "paper:operator-check":
            payload, status = build_research_paper_operator_check({"refresh": args.refresh})
        elif args.command == "paper:next-action":
            payload, status = build_research_paper_operator_check({"refresh": args.refresh})
        elif args.command == "preview-paper-tick":
            payload, status = build_research_preview_paper_tick({})
        elif args.command == "preview-paper-catch-up-next":
            payload, status = build_research_preview_paper_catch_up_next({})
        elif args.command == "preview-paper-catch-up-batch":
            payload, status = build_research_preview_paper_catch_up_batch({"limit": args.limit})
        elif args.command == "paper:freshness":
            payload, status = build_research_paper_freshness({})
        elif args.command == "paper:candle-alignment":
            payload, status = build_research_paper_candle_alignment({})
        elif args.command == "paper:refresh-active-data":
            payload, status = build_research_refresh_active_paper_data({})
        elif args.command == "paper:init-active-candidate":
            payload, status = build_research_init_active_paper_candidate({"confirm": args.confirm})
        elif args.command == "paper:tick-once":
            payload, status = build_research_confirmed_paper_tick_once({"confirm": args.confirm})
        elif args.command == "paper:catch-up-plan":
            payload, status = build_research_paper_catch_up_plan({"refresh": args.refresh})
        elif args.command == "paper:catch-up-next":
            payload, status = build_research_paper_catch_up_next({"confirm": args.confirm})
        elif args.command == "paper:catch-up-batch":
            payload, status = build_research_paper_catch_up_batch({"limit": args.limit, "confirm": args.confirm})
        else:
            payload, status = {"ok": False, "error": "Unknown command."}, 2
    if args.command == "status" and not args.json:
        print_status(payload)
    elif args.command == "summarize" and not args.json:
        print_summary(payload)
    elif args.command == "paper:next-action" and not args.json:
        print_paper_next_action(payload)
    elif args.command == "paper:watch-diagnostics" and not args.json:
        print_paper_watch_diagnostics(payload)
    elif args.command == "research:focused-optimizer" and not args.json:
        print_focused_optimizer(payload)
    elif args.command == "research:watch-matrix" and not args.json:
        print_watch_matrix(payload)
    elif args.command == "research:history-coverage" and not args.json:
        print_history_coverage(payload)
    elif args.command == "research:lead-review" and not args.json:
        print_lead_review(payload)
    else:
        print_json(payload)
    return 0 if status < 400 and payload.get("ok", True) is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
