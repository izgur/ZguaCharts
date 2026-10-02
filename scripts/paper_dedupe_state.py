from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "paper-state.json"
JOURNAL_JSONL_PATH = ROOT / "reports" / "paper-journal.jsonl"
JOURNAL_CSV_PATH = ROOT / "reports" / "paper-journal.csv"


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def round4(value: float) -> float:
    return round(float(value), 4)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def lifecycle_from_trade(trade: dict) -> str:
    return "|".join(
        str(part or "")
        for part in (
            trade.get("symbol"),
            trade.get("interval"),
            trade.get("entrySignalTime") or trade.get("entryTime"),
            trade.get("entryFillTime") or trade.get("entryTime"),
        )
    )


def lifecycle_from_event(event: dict) -> str:
    trade_id = str(event.get("tradeId") or "")
    parts = trade_id.split(":")
    entry_fill_time = parts[2] if len(parts) >= 3 else ""
    return "|".join(
        str(part or "")
        for part in (
            event.get("symbol"),
            event.get("interval"),
            event.get("candleTime") if event.get("eventType") == "SIGNAL" else "",
            entry_fill_time,
        )
    )


def dedupe_closed_trades(state: dict) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    removed: list[dict] = []
    seen: set[str] = set()
    for trade in state.get("closedTrades") or []:
        lifecycle = trade.get("lifecycleId") or lifecycle_from_trade(trade)
        trade["lifecycleId"] = lifecycle
        if lifecycle in seen:
            removed.append(trade)
            continue
        seen.add(lifecycle)
        kept.append(trade)
    return kept, removed


def dedupe_journal_events(events: list[dict]) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    removed: list[dict] = []
    seen_closed_lifecycle_events: set[tuple[str, str]] = set()
    for event in events:
        if event.get("eventType") not in {"ENTRY", "EXIT"}:
            kept.append(event)
            continue
        lifecycle = lifecycle_from_event(event)
        marker = (str(event.get("eventType") or ""), lifecycle)
        if marker in seen_closed_lifecycle_events:
            removed.append(event)
            continue
        seen_closed_lifecycle_events.add(marker)
        kept.append(event)
    return kept, removed


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events), encoding="utf-8")


def rewrite_csv(path: Path, events: list[dict]) -> None:
    cols = [
        "timestamp",
        "symbol",
        "interval",
        "eventType",
        "reason",
        "signalPrice",
        "fillPrice",
        "size",
        "feePaid",
        "slippagePaid",
        "netPnl",
        "accountEquity",
        "strategy",
        "paramsHash",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for event in events:
            writer.writerow({col: event.get(col, "") for col in cols})


def backup(path: Path, tag: str) -> Path | None:
    if not path.exists():
        return None
    backup_path = path.with_name(f"{path.name}.backup-{tag}")
    shutil.copy2(path, backup_path)
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Dedupe paper state closes by stable trade lifecycle.")
    parser.add_argument("--apply", action="store_true", help="Write repaired paper state and journal files. Default is dry-run.")
    args = parser.parse_args()

    state = load_json(STATE_PATH)
    kept_trades, removed_trades = dedupe_closed_trades(state)
    events = read_jsonl(JOURNAL_JSONL_PATH)
    kept_events, removed_exit_events = dedupe_journal_events(events)

    realized = round4(sum(float(trade.get("pnl") or trade.get("netPnl") or 0) for trade in kept_trades))
    fees = round4(sum(float(trade.get("feePaid") or 0) for trade in kept_trades))
    slippage = round4(sum(float(trade.get("slippagePaid") or 0) for trade in kept_trades))
    unrealized = round4(sum(float(position.get("unrealizedPnl") or 0) for position in state.get("openPositions") or []))
    initial_equity = float(state.get("accountEquity", 10000)) - float(state.get("realizedPnl", 0)) - float(state.get("unrealizedPnl", 0))
    repaired_equity = round4(initial_equity + realized + unrealized)

    payload = {
        "ok": True,
        "applied": bool(args.apply),
        "statePath": str(STATE_PATH),
        "journalJsonlPath": str(JOURNAL_JSONL_PATH),
        "journalCsvPath": str(JOURNAL_CSV_PATH),
        "closedTradesBefore": len(state.get("closedTrades") or []),
        "closedTradesAfter": len(kept_trades),
        "removedClosedTrades": [
            {
                "id": trade.get("id"),
                "lifecycleId": trade.get("lifecycleId"),
                "pnl": trade.get("pnl") or trade.get("netPnl"),
                "exitReason": trade.get("exitReason"),
            }
            for trade in removed_trades
        ],
        "journalEventsBefore": len(events),
        "journalEventsAfter": len(kept_events),
        "removedDuplicateJournalEvents": len(removed_exit_events),
        "realizedPnlBefore": state.get("realizedPnl"),
        "realizedPnlAfter": realized,
        "accountEquityBefore": state.get("accountEquity"),
        "accountEquityAfter": repaired_equity,
    }

    if args.apply:
        tag = now_tag()
        payload["backups"] = {
            "state": str(backup(STATE_PATH, tag)),
            "journalJsonl": str(backup(JOURNAL_JSONL_PATH, tag)),
            "journalCsv": str(backup(JOURNAL_CSV_PATH, tag)),
        }
        state["closedTrades"] = kept_trades
        state["realizedPnl"] = realized
        state["cumulativeFees"] = fees
        state["cumulativeSlippage"] = slippage
        state["unrealizedPnl"] = unrealized
        state["accountEquity"] = repaired_equity
        state["updatedAt"] = datetime.now(timezone.utc).isoformat()
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
        write_jsonl(JOURNAL_JSONL_PATH, kept_events)
        rewrite_csv(JOURNAL_CSV_PATH, kept_events)

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
