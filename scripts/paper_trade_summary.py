from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "paper-state.json"


def as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def iso_time(value) -> str:
    try:
        stamp = int(float(value))
    except (TypeError, ValueError):
        return "-"
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat().replace("+00:00", "Z")


def fmt_pct(value) -> str:
    return f"{as_float(value):.4f}%"


def fmt_money(value) -> str:
    return f"{as_float(value):.4f}"


def trade_pnl(trade: dict) -> float:
    return as_float(trade.get("pnl", trade.get("netPnl")))


def print_trade(index: int, trade: dict) -> None:
    entry = trade.get("entryFillTime") or trade.get("entryTime")
    exit_ = trade.get("exitFillTime") or trade.get("exitTime")
    print(
        f"#{index} {trade.get('symbol', '-')}/{trade.get('interval', '-')} "
        f"{trade.get('side', 'long')} pnl={fmt_money(trade_pnl(trade))} "
        f"return={fmt_pct(trade.get('returnPct'))} bars={trade.get('barsHeld', '-')} "
        f"exit={trade.get('exitReason', '-')}"
    )
    print(
        f"   entry {iso_time(entry)} @ {fmt_money(trade.get('entryFillPrice', trade.get('entryPrice')))} | "
        f"exit {iso_time(exit_)} @ {fmt_money(trade.get('exitFillPrice', trade.get('exitPrice')))}"
    )
    print(
        f"   fees={fmt_money(trade.get('feePaid'))} slippage={fmt_money(trade.get('slippagePaid'))} "
        f"id={trade.get('id', '-')}"
    )


def build_summary(limit: int) -> dict:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    trades = list(state.get("closedTrades") or [])
    trades.sort(key=lambda item: as_float(item.get("exitFillTime", item.get("exitTime"))))
    wins = [trade for trade in trades if trade_pnl(trade) > 0]
    losses = [trade for trade in trades if trade_pnl(trade) < 0]
    total_pnl = sum(trade_pnl(trade) for trade in trades)
    return {
        "state": state,
        "trades": trades[-limit:] if limit else trades,
        "allTrades": trades,
        "totalPnl": round(total_pnl, 4),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": round((len(wins) / len(trades) * 100), 4) if trades else 0,
        "avgPnl": round((total_pnl / len(trades)), 4) if trades else 0,
        "best": max(trades, key=trade_pnl) if trades else None,
        "worst": min(trades, key=trade_pnl) if trades else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize current paper trades.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = build_summary(max(0, args.limit))
    state = summary["state"]
    payload = {
        "ok": True,
        "paper": {
            "strategy": state.get("strategy"),
            "symbol": state.get("symbol"),
            "timeframe": state.get("timeframe"),
            "equity": state.get("accountEquity"),
            "realizedPnl": state.get("realizedPnl"),
            "unrealizedPnl": state.get("unrealizedPnl"),
            "openPositions": len(state.get("openPositions") or []),
            "closedTrades": len(summary["allTrades"]),
        },
        "summary": {
            "totalPnl": summary["totalPnl"],
            "wins": summary["wins"],
            "losses": summary["losses"],
            "winRate": summary["winRate"],
            "avgPnl": summary["avgPnl"],
            "bestTradeId": (summary["best"] or {}).get("id"),
            "worstTradeId": (summary["worst"] or {}).get("id"),
        },
        "trades": summary["trades"],
        "note": "Per-trade max unrealized is not tracked yet; this summary uses recorded closed trade fields.",
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    paper = payload["paper"]
    print(f"Paper trade summary: {paper['strategy']} {paper['symbol']} {paper['timeframe']}")
    print(
        f"Equity={fmt_money(paper['equity'])} realized={fmt_money(paper['realizedPnl'])} "
        f"unrealized={fmt_money(paper['unrealizedPnl'])} open={paper['openPositions']} closed={paper['closedTrades']}"
    )
    print(
        f"Closed PnL={fmt_money(summary['totalPnl'])} wins={summary['wins']} losses={summary['losses']} "
        f"winRate={summary['winRate']:.2f}% avgPnl={fmt_money(summary['avgPnl'])}"
    )
    print("Note: max unrealized per trade is not tracked yet.")
    for index, trade in enumerate(summary["trades"], 1):
        print_trade(index, trade)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
