from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATCH_DIR = ROOT / "reports" / "research-batches"
LEAD_DIR = ROOT / "reports" / "research-leads"
OUT_TXT = ROOT / "reports" / "champion-board-latest.txt"
OUT_JSON = ROOT / "reports" / "champion-board-latest.json"


def as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def metric(row: dict, key: str, default=0.0):
    return row.get(key, row.get({
        "return": "totalReturnPct",
        "pf": "profitFactor",
        "dd": "maxDrawdownPct",
    }.get(key, key), default))


def candidate_key(strategy: str, symbol: str, timeframe: str) -> str:
    return f"{strategy}|{symbol}|{timeframe}".upper()


def row_key(row: dict) -> str:
    return candidate_key(str(row.get("strategy", "")), str(row.get("symbol", "")), str(row.get("timeframe", "")))


def lead_key(lead: dict) -> str:
    return candidate_key(str(lead.get("strategy", "")), str(lead.get("symbol", "")), str(lead.get("timeframe", "")))


def compact_row(row: dict, source: str) -> dict:
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    return {
        "source": source,
        "rank": row.get("rank"),
        "tier": row.get("finalCandidateTier") or row.get("tier") or row.get("evidenceTier"),
        "status": row.get("status"),
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "trades": metric(row, "trades"),
        "returnPct": metric(row, "totalReturnPct"),
        "profitFactor": metric(row, "profitFactor"),
        "maxDrawdownPct": metric(row, "maxDrawdownPct"),
        "replacementEligible": bool(row.get("replacementEligible")),
        "reason": row.get("mainFailureReason") or "; ".join(row.get("replacementRejectionReasons") or row.get("qualityGateReasons") or []),
        "score": row.get("practicalScore", row.get("score")),
        "params": params,
        "paramsSummary": summarize_params(params),
    }


def summarize_params(params: dict, limit: int = 8) -> list[dict]:
    priority = [
        "regimeMode",
        "donchianEntry",
        "donchianExit",
        "squeezeLookback",
        "squeezePercentile",
        "rangeLookback",
        "rangeSma",
        "closeHighPct",
        "emaMid",
        "emaSlow",
        "adxThreshold",
        "atrMultiplier",
        "stopAtr",
        "takeProfitAtr",
        "trailingAtr",
        "indicatorFilterPreset",
        "useDonchianTrail",
        "volumeFilter",
    ]
    items = []
    for key in priority:
        if key in params:
            items.append({"key": key, "value": params.get(key)})
    for key in sorted(params):
        if len(items) >= limit:
            break
        if key not in priority:
            items.append({"key": key, "value": params.get(key)})
    return items[:limit]


def compact_lead(path: Path, payload: dict) -> dict:
    lead = payload.get("lead") or {}
    comparison = payload.get("comparison") or {}
    eligibility = payload.get("replacementEligibility") or {}
    lead_summary = comparison.get("leadSummary") or payload.get("evidence", {}).get("leadActivity") or {}
    checks = payload.get("evidence", {}).get("checks") or payload.get("checks") or {}
    verdict = payload.get("verdict")
    if isinstance(verdict, dict):
        verdict = verdict.get("action") or verdict.get("status") or verdict.get("verdict")
    return {
        "source": str(path.relative_to(ROOT)),
        "strategy": lead.get("strategy"),
        "symbol": lead.get("symbol"),
        "timeframe": lead.get("timeframe"),
        "verdict": verdict,
        "eligible": eligibility.get("eligible"),
        "reasons": eligibility.get("reasons") or [],
        "winner": comparison.get("winner"),
        "trades": lead_summary.get("trades"),
        "returnPct": lead_summary.get("totalReturnPct"),
        "profitFactor": lead_summary.get("profitFactor"),
        "maxDrawdownPct": lead_summary.get("maxDrawdownPct"),
        "checks": checks,
    }


def score_candidate(row: dict) -> tuple[float, float, float, float]:
    eligible_bonus = 1000 if row.get("replacementEligible") else 0
    status = str(row.get("status", "")).upper()
    status_bonus = 100 if status == "PASS" else -500
    if as_float(row.get("trades")) <= 0:
        status_bonus -= 250
    return (
        eligible_bonus + status_bonus + as_float(row.get("returnPct")),
        as_float(row.get("profitFactor")),
        -as_float(row.get("maxDrawdownPct")),
        as_float(row.get("trades")),
    )


def build_board(batch_limit: int, lead_limit: int, symbol: str | None = None, timeframe: str | None = None) -> dict:
    batches = sorted(BATCH_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:batch_limit]
    leads = sorted(LEAD_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:lead_limit]
    rows: list[dict] = []
    baseline = None
    batch_summaries = []
    for path in batches:
        payload = load_json(path)
        if not payload:
            continue
        batch_summaries.append({
            "path": str(path.relative_to(ROOT)),
            "search": payload.get("search") or {},
            "recommendation": ((payload.get("summary") or {}).get("recommendation") or {}).get("action"),
            "replacementEligibleCount": (payload.get("summary") or {}).get("replacementEligibleCount"),
            "budgetHit": (payload.get("search") or {}).get("budgetHit"),
        })
        for row in payload.get("rows") or []:
            compact = compact_row(row, str(path.relative_to(ROOT)))
            if symbol and str(compact.get("symbol") or "").upper() != symbol.upper():
                continue
            if timeframe and str(compact.get("timeframe") or "") != timeframe:
                continue
            rows.append(compact)
            if row.get("isActiveBaseline") or compact.get("tier") == "BASELINE":
                baseline = compact

    reviewed: dict[str, dict] = {}
    review_rows = []
    for path in leads:
        payload = load_json(path)
        if not payload:
            continue
        item = compact_lead(path, payload)
        if symbol and str(item.get("symbol") or "").upper() != symbol.upper():
            continue
        if timeframe and str(item.get("timeframe") or "") != timeframe:
            continue
        review_rows.append(item)
        key = lead_key(item)
        if key not in reviewed:
            reviewed[key] = item

    by_timeframe: dict[str, list[dict]] = {}
    for row in rows:
        by_timeframe.setdefault(str(row.get("timeframe") or "-"), []).append(row)

    champions = {}
    for timeframe, items in by_timeframe.items():
        unique = {}
        for item in items:
            unique.setdefault(row_key(item), item)
        ranked = sorted(unique.values(), key=score_candidate, reverse=True)
        champions[timeframe] = ranked[:5]

    review_needed = []
    review_needed_keys = set()
    for row in sorted(rows, key=score_candidate, reverse=True):
        key = row_key(row)
        if row.get("replacementEligible") and key not in reviewed and key not in review_needed_keys:
            review_needed.append(row)
            review_needed_keys.add(key)
        if len(review_needed) >= 8:
            break

    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "symbol": symbol,
            "timeframe": timeframe,
            "batchLimit": batch_limit,
            "leadLimit": lead_limit,
        },
        "baseline": baseline,
        "championsByTimeframe": champions,
        "reviewNeeded": review_needed,
        "recentReviews": review_rows[:12],
        "recentBatches": batch_summaries,
        "counts": {
            "batchFilesRead": len(batches),
            "leadFilesRead": len(leads),
            "candidateRows": len(rows),
            "reviewedCandidates": len(reviewed),
        },
        "notes": board_notes(champions, review_needed),
    }


def board_notes(champions: dict, review_needed: list[dict]) -> list[str]:
    notes = []
    all_rows = [row for rows in champions.values() for row in rows]
    if all_rows:
        returns = [as_float(row.get("returnPct")) for row in all_rows if row.get("returnPct") is not None]
        if returns and max(returns) < 25:
            notes.append("Top saved variants are clustered below 25% return; this suggests the current BTC 4h strategy families may be near their ceiling.")
        new_family = [row for row in all_rows if row.get("strategy") in {"RegimeDonchian20", "RegimeDonchianCloseConfirm", "VolatilitySqueezeBreakout", "RangeExpansionV2", "RobustTrendFollowerV1"}]
        if new_family:
            notes.append("New public-rule families are now represented; compare their PF/drawdown/trade count before trusting return alone.")
    if review_needed:
        notes.append("Replacement-eligible candidates still need lead review before any paper/config discussion.")
    return notes


def fmt(value, digits=4) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def line_for(row: dict) -> str:
    reviewed = ""
    return (
        f"{row.get('strategy')} {row.get('symbol')} {row.get('timeframe')} "
        f"tier={row.get('tier')} status={row.get('status')} trades={fmt(row.get('trades'), 0)} "
        f"ret={fmt(row.get('returnPct'))}% PF={fmt(row.get('profitFactor'))} DD={fmt(row.get('maxDrawdownPct'))}%"
        f"{reviewed}"
    )


def render_text(board: dict) -> str:
    lines = ["ZGUA Strategy Champion Board", f"Generated: {board['generatedAt']}", ""]
    baseline = board.get("baseline")
    lines.append("ACTIVE BASELINE")
    lines.append(line_for(baseline) if baseline else "-")
    lines.append("")
    lines.append("CHAMPIONS BY TIMEFRAME")
    for timeframe in sorted((board.get("championsByTimeframe") or {}).keys()):
        lines.append(f"{timeframe}:")
        for index, row in enumerate(board["championsByTimeframe"][timeframe], 1):
            lines.append(f"  {index}. {line_for(row)}")
    lines.append("")
    lines.append("REVIEW NEEDED")
    if board.get("reviewNeeded"):
        for index, row in enumerate(board["reviewNeeded"], 1):
            lines.append(f"  {index}. {line_for(row)}")
            lines.append(f"     source={row.get('source')}")
    else:
        lines.append("  none")
    lines.append("")
    lines.append("RECENT LEAD REVIEWS")
    for item in board.get("recentReviews") or []:
        reasons = ";".join(item.get("reasons") or [])
        lines.append(
            f"  {item.get('strategy')} {item.get('symbol')} {item.get('timeframe')} "
            f"verdict={item.get('verdict')} winner={item.get('winner')} "
            f"ret={fmt(item.get('returnPct'))}% PF={fmt(item.get('profitFactor'))} DD={fmt(item.get('maxDrawdownPct'))}% "
            f"reasons={reasons or '-'}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact strategy champion board from saved research reports.")
    parser.add_argument("--batch-limit", type=int, default=12)
    parser.add_argument("--lead-limit", type=int, default=30)
    parser.add_argument("--symbol")
    parser.add_argument("--timeframe")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()

    board = build_board(max(1, args.batch_limit), max(1, args.lead_limit), args.symbol, args.timeframe)
    text = render_text(board)
    if not args.no_save:
        OUT_TXT.parent.mkdir(parents=True, exist_ok=True)
        OUT_TXT.write_text(text, encoding="utf-8")
        OUT_JSON.write_text(json.dumps(board, indent=2), encoding="utf-8")
        board["savedText"] = str(OUT_TXT.relative_to(ROOT))
        board["savedJson"] = str(OUT_JSON.relative_to(ROOT))

    if args.json:
        print(json.dumps(board, indent=2))
    else:
        print(text, end="")
        if not args.no_save:
            print(f"Saved: {OUT_TXT.relative_to(ROOT)}")
            print(f"Saved: {OUT_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
