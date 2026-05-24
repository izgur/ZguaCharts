from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from data_source import DATA_SOURCE_CONFIG, fetch_candles, fetch_historical_candles
from indicators import available_indicators, build_indicator_payload
from signals import build_signal_payload
from strategy import DEFAULT_PRESET_ID, preset_options


app = Flask(__name__)

BACKTEST_HISTORY_PATH = Path(app.root_path) / "data" / "backtest-history.json"
PAPER_CANDIDATE_PATH = Path(app.root_path) / "config" / "paper-candidate.json"

NODE_STRATEGIES = {
    "conservative_trend": "ConservativeTrend",
    "regime_filtered_trend": "RegimeFilteredTrendStrategy",
    "RegimeFilteredTrendStrategy": "RegimeFilteredTrendStrategy",
    "momentum_scalping": "MomentumScalping",
    "mean_reversion": "MeanReversion",
    "pullback_trend": "PullbackTrend",
    "original": "ConservativeTrend",
}


@app.get("/")
@app.get("/charts")
@app.get("/backtest")
@app.get("/analysis")
@app.get("/settings")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "app": "ZguaCharts"})


@app.get("/api/config")
def config():
    payload = dict(DATA_SOURCE_CONFIG)
    payload["indicators"] = available_indicators()
    payload["strategy_presets"] = preset_options() + [
        {
            "id": "regime_filtered_trend",
            "label": "Regime Filtered Trend Strategy",
            "intended_timeframes": "1h with BTCUSDT 4h regime filter",
        }
    ]
    payload["default_strategy_preset"] = DEFAULT_PRESET_ID
    return jsonify(payload)


@app.get("/api/candles")
def candles():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "1m")
    limit = int(request.args.get("limit", "240"))
    visible_charts = int(request.args.get("visible_charts", "1"))

    try:
        payload = fetch_candles(source, symbol, timeframe, limit=limit, visible_charts=visible_charts)
        source_diagnostics = payload.get("diagnostics", {})
        payload["diagnostics"] = {
            **source_diagnostics,
            **candle_diagnostics(payload.get("candles", []), requested_limit=limit),
        }
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Could not load candles: {exc}"}), 502

    return jsonify(payload)


@app.get("/api/indicators")
def indicators():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "1m")
    names = request.args.get("indicators", "")
    sma_period = int(request.args.get("sma_period", "20"))
    limit = int(request.args.get("limit", "300"))
    chart_candles_count = request.args.get("chart_candles_count")
    first_chart_candle_time = request.args.get("first_chart_candle_time")
    last_chart_candle_time = request.args.get("last_chart_candle_time")

    try:
        candles_payload = fetch_candles(source, symbol, timeframe, limit=limit)
        payload = build_indicator_payload(
            candles_payload["candles"],
            names.split(","),
            sma_period=sma_period,
        )
        payload.setdefault("diagnostics", {})
        payload["diagnostics"].update(candles_payload.get("diagnostics", {}))
        payload["diagnostics"].update(candle_diagnostics(candles_payload["candles"], requested_limit=limit))
        payload["diagnostics"]["chartCandlesCount"] = int(chart_candles_count or len(candles_payload["candles"]))
        payload["diagnostics"]["firstChartCandleTime"] = int(first_chart_candle_time or payload["diagnostics"]["firstChartCandleTime"] or 0) or None
        payload["diagnostics"]["lastChartCandleTime"] = int(last_chart_candle_time or payload["diagnostics"]["lastChartCandleTime"] or 0) or None
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Could not load indicators: {exc}"}), 502

    return jsonify(payload)


@app.get("/api/signals")
def signals():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "1m")
    limit = int(request.args.get("limit", "300"))
    include_timeframes = request.args.get("include_timeframes", "true").lower() != "false"

    try:
        candles_payload = fetch_candles(source, symbol, timeframe, limit=limit)
        payload = build_signal_payload(candles_payload["candles"])
        payload["timeframe"] = timeframe
        if include_timeframes:
            payload["timeframeMatrix"] = build_signal_timeframe_matrix(source, symbol, timeframe, limit)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Could not load signals: {exc}"}), 502

    return jsonify(payload)


@app.get("/api/backtest")
def backtest():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "15m")
    period = request.args.get("period", "60d")
    preset = request.args.get("preset", DEFAULT_PRESET_ID)
    fee_pct = float(request.args.get("fee_pct", "0"))
    slippage_pct = float(request.args.get("slippage_pct", "0"))
    limit = int(request.args.get("limit", "5000"))
    debug = request.args.get("debug", "false").lower() == "true"
    allow_shorts = request.args.get("allowShorts", "false").lower() == "true"
    chart_candles_count = int(request.args.get("chart_candles_count", "0") or "0")
    first_chart_candle_time = request.args.get("first_chart_candle_time")
    last_chart_candle_time = request.args.get("last_chart_candle_time")

    try:
        payload = run_shared_backtest_engine(source, symbol, timeframe, period, preset, fee_pct, slippage_pct, limit, debug, allow_shorts)
        payload.setdefault("diagnostics", {})
        overlay_diag = overlay_diagnostics_from_payload(payload)
        payload["diagnostics"]["overlay_rendering"] = {
            **overlay_diag,
            "chartCandlesCount": chart_candles_count or overlay_diag.get("chartCandlesCount"),
            "backtestCandlesCount": overlay_diag.get("backtestCandlesCount"),
            "firstChartCandleTime": int(first_chart_candle_time) if first_chart_candle_time else overlay_diag.get("firstChartCandleTime"),
            "lastChartCandleTime": int(last_chart_candle_time) if last_chart_candle_time else overlay_diag.get("lastChartCandleTime"),
        }
        record_backtest_history(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Could not run backtest: {exc}"}), 502

    return jsonify(payload)


@app.get("/api/optimize")
def optimize():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "1h")
    period = request.args.get("period", "365d")
    preset = request.args.get("preset", "regime_filtered_trend")
    limit = int(request.args.get("limit", "9000"))
    max_combos = int(request.args.get("max_combos", "1000"))

    if NODE_STRATEGIES.get(preset, preset) != "RegimeFilteredTrendStrategy":
        return jsonify({"error": "Optimizer endpoint is currently enabled only for RegimeFilteredTrendStrategy."}), 400

    try:
        payload = run_shared_optimizer_engine(source, symbol, timeframe, period, preset, limit, max_combos)
    except Exception as exc:
        return jsonify({"error": f"Could not run optimizer: {exc}"}), 502
    return jsonify(payload)


@app.get("/api/backtest-history")
def backtest_history():
    limit = int(request.args.get("limit", "100"))
    records = load_backtest_history()
    return jsonify({
        "runs": records[-limit:][::-1],
        "strategySummary": summarize_backtest_history(records),
        "totalRuns": len(records),
    })


@app.get("/api/strategy-ranking")
def strategy_ranking():
    source = request.args.get("source", "bybit")
    symbols = parse_csv_arg(request.args.get("symbols"), ["BTCUSDT"])
    timeframes = parse_csv_arg(request.args.get("timeframes"), ["1h"])
    period = request.args.get("period", "365d")
    default_presets = [item["id"] for item in preset_options()] + ["regime_filtered_trend"]
    presets = parse_csv_arg(request.args.get("presets"), default_presets)
    limit = int(request.args.get("limit", "5000"))
    fee_pct = float(request.args.get("fee_pct", "0"))
    slippage_pct = float(request.args.get("slippage_pct", "0"))
    min_trades = int(request.args.get("min_trades", "10"))
    allow_shorts = request.args.get("allowShorts", "false").lower() in {"1", "true", "yes", "on"}
    max_runs = int(request.args.get("max_runs", "30"))
    runs_requested = len(symbols) * len(timeframes) * len(presets)

    if runs_requested > max_runs:
        return jsonify({
            "error": f"Requested {runs_requested} ranking runs, but max_runs is {max_runs}. Narrow symbols, timeframes, presets, or raise max_runs intentionally.",
            "requested": {
                "symbols": symbols,
                "timeframes": timeframes,
                "presets": presets,
                "limit": limit,
                "minTrades": min_trades,
                "maxRuns": max_runs,
            },
        }), 400

    rows = []
    errors = []
    # TODO: Move this synchronous matrix run to a background job/cache when the
    # requested symbol/timeframe/preset matrix becomes too slow for one request.
    for symbol in symbols:
        for timeframe in timeframes:
            for preset in presets:
                try:
                    payload = run_shared_backtest_engine(
                        source,
                        symbol,
                        timeframe,
                        period,
                        preset,
                        fee_pct,
                        slippage_pct,
                        limit,
                        allow_shorts=allow_shorts,
                    )
                    metrics = ranking_metrics_from_backtest(payload)
                    valid = metrics["trades"] >= min_trades
                    warnings = list(payload.get("warnings") or [])
                    if metrics["trades"] == 0:
                        warnings.append("zero-trade result")
                    if metrics["trades"] < min_trades:
                        warnings.append(f"trades below min_trades ({metrics['trades']} < {min_trades})")
                    row = {
                        "rank": None,
                        "valid": valid,
                        "strategy": payload.get("preset") or preset,
                        "preset": preset,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "period": period,
                        "totalReturnPct": metrics["totalReturn"],
                        "winRate": metrics["winRate"],
                        "maxDrawdown": metrics["maxDrawdown"],
                        "profitFactor": metrics["profitFactor"],
                        "trades": metrics["trades"],
                        "averageBarsHeld": metrics["averageBarsHeld"],
                        "score": ranking_score(metrics, min_trades=min_trades),
                        "diagnostics": payload.get("diagnostics") or {},
                        "params": (payload.get("diagnostics") or {}).get("params", {}),
                        "warnings": warnings,
                    }
                    rows.append(row)
                except Exception as exc:
                    error = {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "preset": preset,
                        "error": str(exc),
                    }
                    errors.append(error)
                    rows.append({
                        "rank": None,
                        "valid": False,
                        "strategy": NODE_STRATEGIES.get(preset, preset),
                        "preset": preset,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "period": period,
                        "totalReturnPct": 0,
                        "winRate": 0,
                        "maxDrawdown": 0,
                        "profitFactor": 0,
                        "trades": 0,
                        "averageBarsHeld": 0,
                        "score": -999,
                        "diagnostics": {},
                        "warnings": [str(exc)],
                    })

    rows.sort(key=lambda item: item["score"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    return jsonify({
        "source": source,
        "period": period,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "requested": {
            "symbols": symbols,
            "timeframes": timeframes,
            "presets": presets,
            "limit": limit,
            "minTrades": min_trades,
            "maxRuns": max_runs,
            "allowShorts": allow_shorts,
            "feePct": fee_pct,
            "slippagePct": slippage_pct,
        },
        "summary": {
            "runsRequested": runs_requested,
            "runsCompleted": len(rows) - len(errors),
            "validCandidates": len([row for row in rows if row.get("valid")]),
            "errors": len(errors),
        },
        "cards": ranking_cards(rows),
        "rows": rows,
        "errors": errors,
    })


@app.get("/api/paper/status")
def paper_status():
    try:
        state_path = os.path.join(app.root_path, "data", "paper-state.json")
        journal_path = os.path.join(app.root_path, "reports", "paper-journal.jsonl")
        state = read_json_file(state_path, {})
        candidate = read_json_file(str(PAPER_CANDIDATE_PATH), {})
        events = read_jsonl_tail(journal_path, 30)
        return jsonify({
            "openPositions": state.get("openPositions", []),
            "closedTrades": state.get("closedTrades", [])[-50:],
            "equity": state.get("accountEquity"),
            "realizedPnL": state.get("realizedPnl", 0),
            "unrealizedPnL": state.get("unrealizedPnl", 0),
            "totalFees": state.get("cumulativeFees", 0),
            "totalSlippage": state.get("cumulativeSlippage", 0),
            "lastSignals": events,
            "lastProcessedCandle": state.get("lastProcessedCandleTime", {}),
            "warnings": state.get("warnings", []),
            "candidate": {
                "enabled": candidate.get("enabled", False),
                "strategy": candidate.get("strategy"),
                "source": candidate.get("source"),
                "regimeMode": candidate.get("regimeMode"),
                "params": candidate.get("params", {}),
                "activeSymbols": candidate_symbols_by_mode(candidate, "active"),
                "watchSymbols": candidate_symbols_by_mode(candidate, "watch"),
                "promotedAt": candidate.get("promotedAt"),
                "promotedFromRanking": candidate.get("promotedFromRanking"),
                "fillModel": candidate.get("fillModel"),
                "makerFeePct": candidate.get("makerFeePct"),
                "takerFeePct": candidate.get("takerFeePct"),
                "slippageBps": candidate.get("slippageBps"),
            },
            "equityCurve": state.get("equityCurve", [])[-500:],
        })
    except Exception as exc:
        return jsonify({"error": f"Could not load paper status: {exc}"}), 502


@app.get("/api/candidate/current")
def current_candidate():
    try:
        candidate = read_json_file(str(PAPER_CANDIDATE_PATH), {})
        return jsonify(candidate_summary(candidate))
    except Exception as exc:
        return jsonify({"error": f"Could not load current candidate: {exc}"}), 502


@app.post("/api/candidate/promote")
def promote_candidate():
    try:
        payload = request.get_json(force=True) or {}
        force = bool(payload.get("force")) or request.args.get("force", "false").lower() in {"1", "true", "yes", "on"}
        ranking_snapshot = payload.get("rankingSnapshot") or {}
        min_trades = int(payload.get("minTrades") or ranking_snapshot.get("minTrades") or 10)
        promotion_error = validate_candidate_promotion(payload, ranking_snapshot, min_trades, force)
        if promotion_error:
            return jsonify({"error": promotion_error}), 400

        current = read_json_file(str(PAPER_CANDIDATE_PATH), {})
        backup_path = backup_candidate_config(current)
        promoted_symbol = str(payload.get("symbol") or "").strip()
        promoted_interval = str(payload.get("timeframe") or payload.get("interval") or "").strip()
        updated = merge_promoted_candidate(current, payload, ranking_snapshot, promoted_symbol, promoted_interval)
        PAPER_CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PAPER_CANDIDATE_PATH, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, indent=2)
            handle.write("\n")

        return jsonify({
            "ok": True,
            "message": "Candidate promoted. Paper simulation remains disabled until explicitly enabled.",
            "backupPath": str(backup_path.relative_to(app.root_path)),
            "candidate": candidate_summary(updated),
        })
    except Exception as exc:
        return jsonify({"error": f"Could not promote candidate: {exc}"}), 502


def read_json_file(path: str, fallback):
    if not os.path.exists(path):
        return fallback
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl_tail(path: str, limit: int):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        lines = [line.strip() for line in handle.readlines() if line.strip()]
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def candidate_symbols_by_mode(candidate: dict, mode: str) -> list[dict]:
    return [
        item for item in candidate.get("symbols", [])
        if item.get("mode", "active") == mode
    ]


def candidate_summary(candidate: dict) -> dict:
    return {
        "enabled": candidate.get("enabled", False),
        "strategy": candidate.get("strategy"),
        "source": candidate.get("source"),
        "regimeMode": candidate.get("regimeMode"),
        "activeSymbols": candidate_symbols_by_mode(candidate, "active"),
        "watchSymbols": candidate_symbols_by_mode(candidate, "watch"),
        "params": candidate.get("params", {}),
        "promotedAt": candidate.get("promotedAt"),
        "promotedFromRanking": candidate.get("promotedFromRanking"),
        "fillModel": candidate.get("fillModel"),
        "makerFeePct": candidate.get("makerFeePct"),
        "takerFeePct": candidate.get("takerFeePct"),
        "slippageBps": candidate.get("slippageBps"),
        "accountEquity": candidate.get("accountEquity"),
        "riskPct": candidate.get("riskPct"),
        "maxOpenTrades": candidate.get("maxOpenTrades"),
        "maxNotionalPerTrade": candidate.get("maxNotionalPerTrade"),
    }


def validate_candidate_promotion(payload: dict, ranking_snapshot: dict, min_trades: int, force: bool) -> str | None:
    if not payload.get("symbol") or not (payload.get("timeframe") or payload.get("interval")):
        return "Promotion requires symbol and timeframe."
    if not (payload.get("preset") or payload.get("strategy")):
        return "Promotion requires preset or strategy."
    if ranking_snapshot.get("valid") is False and not force:
        return "Cannot promote an invalid ranking row without force=true."
    trades = int(safe_float(ranking_snapshot.get("trades", 0)))
    profit_factor = safe_float(ranking_snapshot.get("profitFactor", 0))
    max_drawdown = safe_float(ranking_snapshot.get("maxDrawdown", 0))
    if trades < min_trades:
        return f"Cannot promote candidate with too few trades ({trades} < {min_trades})."
    if profit_factor <= 1 and not force:
        return f"Cannot promote candidate with profitFactor <= 1 ({profit_factor})."
    if max_drawdown > 30 and not force:
        return f"Cannot promote candidate with maxDrawdown > 30 ({max_drawdown})."
    return None


def backup_candidate_config(candidate: dict) -> Path:
    backup_dir = Path(app.root_path) / "config" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"paper-candidate-{timestamp}.json"
    with open(backup_path, "w", encoding="utf-8") as handle:
        json.dump(candidate, handle, indent=2)
        handle.write("\n")
    return backup_path


def merge_promoted_candidate(current: dict, payload: dict, ranking_snapshot: dict, promoted_symbol: str, promoted_interval: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    preserved = dict(current)
    existing_symbols = current.get("symbols", [])
    watch_symbols = [
        {**item, "mode": "watch"}
        for item in existing_symbols
        if item.get("mode") == "watch" and not (
            item.get("symbol") == promoted_symbol and item.get("interval") == promoted_interval
        )
    ]
    promoted_market = {"symbol": promoted_symbol, "interval": promoted_interval, "mode": "active"}
    symbols = [promoted_market]
    seen = {(promoted_symbol, promoted_interval, "active")}
    for item in watch_symbols:
        key = (item.get("symbol"), item.get("interval"), item.get("mode"))
        if key not in seen:
            symbols.append(item)
            seen.add(key)

    snapshot = {
        "rank": ranking_snapshot.get("rank"),
        "score": ranking_snapshot.get("score"),
        "period": payload.get("period"),
        "totalReturnPct": ranking_snapshot.get("totalReturnPct"),
        "winRate": ranking_snapshot.get("winRate"),
        "maxDrawdown": ranking_snapshot.get("maxDrawdown"),
        "profitFactor": ranking_snapshot.get("profitFactor"),
        "trades": ranking_snapshot.get("trades"),
    }
    # TODO: Add scheduled ranking runs, automatic candidate suggestions,
    # a human approval queue, and automatic promotion only after paper validation.
    preserved.update({
        "enabled": False,
        "source": payload.get("source", current.get("source", "bybit")),
        "strategy": payload.get("strategy") or payload.get("preset") or current.get("strategy"),
        "regimeMode": payload.get("regimeMode", current.get("regimeMode")),
        "params": payload.get("params") if isinstance(payload.get("params"), dict) else current.get("params", {}),
        "symbols": symbols,
        "promotedAt": now,
        "promotedFromRanking": snapshot,
    })
    return preserved


def node_executable() -> str:
    """Return a Node binary new enough for the shared research engine.

    Some local Windows machines have an old `node.exe` earlier on PATH while
    npm uses a newer bundled runtime. Render normally resolves `node` directly,
    so this only changes behavior when multiple local candidates are present.
    """
    env_node = os.environ.get("NODE_BINARY") or os.environ.get("NODE_EXE")
    candidates = [env_node] if env_node else []
    path_node = shutil.which("node")
    if path_node:
        candidates.append(path_node)
    if os.name == "nt":
        try:
            where = subprocess.run(["where.exe", "node"], text=True, capture_output=True, timeout=5)
            if where.returncode == 0:
                candidates.extend(line.strip() for line in where.stdout.splitlines() if line.strip())
        except Exception:
            pass

    seen = set()
    fallback = None
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        fallback = fallback or candidate
        try:
            completed = subprocess.run([candidate, "-v"], text=True, capture_output=True, timeout=5)
            version = (completed.stdout or completed.stderr).strip().lstrip("v")
            major = int(version.split(".", 1)[0])
            if major >= 18:
                return candidate
        except Exception:
            continue
    return fallback or "node"


def run_shared_backtest_engine(source: str, symbol: str, timeframe: str, period: str, preset: str, fee_pct: float, slippage_pct: float, limit: int, debug: bool = False, allow_shorts: bool = False) -> dict:
    """Bridge Flask to the reusable Node research engine.

    Python keeps responsibility for broker adapters that already work here
    (yfinance, Bybit cache, Hyperliquid). Simulation rules live in /core so
    the UI, CLI optimizer, and future workers all share one backtest engine.
    """
    candles_payload = fetch_historical_candles(source, symbol, timeframe, period=period, limit=limit)
    engine_input = {
        "source": source,
        "symbol": symbol,
        "interval": timeframe,
        "timeframe": timeframe,
        "strategy": NODE_STRATEGIES.get(preset, preset),
        "preset": NODE_STRATEGIES.get(preset, preset),
        "limit": limit,
        "params": {
            "feePct": fee_pct,
            "slippagePct": slippage_pct,
            "shortMode": allow_shorts,
        },
        "debug": debug,
        "candles": candles_payload["candles"],
    }
    completed = subprocess.run(
        [node_executable(), "cli/backtest.js", "--stdin-json"],
        input=json.dumps(engine_input, allow_nan=False),
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Node backtest engine failed")
    payload = json.loads(completed.stdout)
    payload["source"] = source
    payload["symbol"] = symbol
    payload["timeframe"] = timeframe
    payload["period"] = period
    payload["presets"] = preset_options()
    payload.setdefault("diagnostics", {})
    payload["diagnostics"]["requested_period"] = period
    payload["diagnostics"]["effective_period"] = candles_payload.get("effective_period", period)
    payload["diagnostics"]["api_candles"] = candle_diagnostics(candles_payload["candles"], requested_limit=limit)
    return payload


def build_signal_timeframe_matrix(source: str, symbol: str, selected_timeframe: str, limit: int) -> list[dict]:
    source_config = DATA_SOURCE_CONFIG.get("sources", {}).get(source, {})
    timeframes = source_config.get("timeframes", [selected_timeframe])
    rows = []
    for timeframe in timeframes:
        try:
            candles_payload = fetch_candles(source, symbol, timeframe, limit=max(300, min(limit, 1000)))
            payload = build_signal_payload(candles_payload["candles"])
            rows.append({
                "timeframe": timeframe,
                "selected": timeframe == selected_timeframe,
                "score": payload.get("score", 0),
                "label": payload.get("label", "NEUTRAL"),
                "tone": payload.get("tone", "neutral"),
                "buySuggestionPct": payload.get("buySuggestionPct", 0),
                "signalDirection": payload.get("signalDirection", "NEUTRAL"),
                "longSignalPct": payload.get("longSignalPct", payload.get("buySuggestionPct", 0)),
                "shortSignalPct": payload.get("shortSignalPct", 0),
                "components": payload.get("components", []),
                "warnings": payload.get("warnings", []),
                "error": None,
            })
        except Exception as exc:
            rows.append({
                "timeframe": timeframe,
                "selected": timeframe == selected_timeframe,
                "score": None,
                "label": "ERROR",
                "tone": "neutral",
                "buySuggestionPct": 0,
                "signalDirection": "NEUTRAL",
                "longSignalPct": 0,
                "shortSignalPct": 0,
                "components": [],
                "warnings": [],
                "error": str(exc),
            })
    return rows


def load_backtest_history() -> list[dict]:
    if not BACKTEST_HISTORY_PATH.exists():
        return []
    try:
        with BACKTEST_HISTORY_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def save_backtest_history(records: list[dict]) -> None:
    BACKTEST_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BACKTEST_HISTORY_PATH.open("w", encoding="utf-8") as handle:
        json.dump(records[-500:], handle, indent=2)


def record_backtest_history(payload: dict) -> None:
    record = {
        "id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "source": payload.get("source"),
        "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"),
        "period": payload.get("period"),
        "strategy": payload.get("preset") or payload.get("strategy") or "unknown",
        "totalReturnPct": safe_float(payload.get("total_return_pct", payload.get("totalReturn", 0))),
        "trades": int(safe_float(payload.get("number_of_trades", payload.get("trades", 0)))),
        "winRate": safe_float(payload.get("win_rate", payload.get("winRate", 0))),
        "maxDrawdown": safe_float(payload.get("max_drawdown", payload.get("maxDrawdown", 0))),
        "profitFactor": safe_float(payload.get("profit_factor", payload.get("profitFactor", 0))),
        "averageBarsHeld": safe_float(payload.get("average_bars_held", payload.get("avgBarsHeld", 0))),
    }
    records = load_backtest_history()
    records.append(record)
    save_backtest_history(records)


def summarize_backtest_history(records: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in records:
        strategy = record.get("strategy") or "unknown"
        item = grouped.setdefault(strategy, {
            "strategy": strategy,
            "tests": 0,
            "totalTrades": 0,
            "sumReturnPct": 0.0,
            "avgReturnPct": 0.0,
            "avgWinRate": 0.0,
            "avgProfitFactor": 0.0,
            "worstDrawdown": 0.0,
            "bestReturnPct": None,
            "worstReturnPct": None,
        })
        item["tests"] += 1
        item["totalTrades"] += int(record.get("trades") or 0)
        item["sumReturnPct"] += safe_float(record.get("totalReturnPct"))
        item["avgWinRate"] += safe_float(record.get("winRate"))
        item["avgProfitFactor"] += safe_float(record.get("profitFactor"))
        item["worstDrawdown"] = max(item["worstDrawdown"], safe_float(record.get("maxDrawdown")))
        total_return = safe_float(record.get("totalReturnPct"))
        item["bestReturnPct"] = total_return if item["bestReturnPct"] is None else max(item["bestReturnPct"], total_return)
        item["worstReturnPct"] = total_return if item["worstReturnPct"] is None else min(item["worstReturnPct"], total_return)
    summaries = []
    for item in grouped.values():
        tests = max(1, item["tests"])
        item["avgReturnPct"] = round(item["sumReturnPct"] / tests, 2)
        item["avgWinRate"] = round(item["avgWinRate"] / tests, 2)
        item["avgProfitFactor"] = round(item["avgProfitFactor"] / tests, 3)
        item["sumReturnPct"] = round(item["sumReturnPct"], 2)
        item["worstDrawdown"] = round(item["worstDrawdown"], 2)
        item["bestReturnPct"] = round(item["bestReturnPct"] or 0, 2)
        item["worstReturnPct"] = round(item["worstReturnPct"] or 0, 2)
        summaries.append(item)
    summaries.sort(key=lambda item: (item["tests"], item["sumReturnPct"]), reverse=True)
    return summaries


def candle_diagnostics(candles: list[dict], requested_limit=None) -> dict:
    return {
        "chartCandlesCount": len(candles),
        "backtestCandlesCount": None,
        "requestedLimit": requested_limit,
        "firstChartCandleTime": int(candles[0]["time"]) if candles else None,
        "lastChartCandleTime": int(candles[-1]["time"]) if candles else None,
        "firstOverlayTime": None,
        "lastOverlayTime": None,
        "warmupBars": {"EMA50": 50, "EMA200": 200, "Donchian55": 55, "ATR14": 14},
        "droppedBarsReason": "none at candle endpoint",
    }


def overlay_diagnostics_from_payload(payload: dict) -> dict:
    overlays = payload.get("overlays") or []
    counts = {item.get("name", f"overlay_{idx}"): len(item.get("data", [])) for idx, item in enumerate(overlays)}
    times = [
        point.get("time")
        for item in overlays
        for point in item.get("data", [])
        if point.get("value") is not None
    ]
    return {
        "chartCandlesCount": payload.get("candlesLoaded"),
        "backtestCandlesCount": payload.get("candlesLoaded"),
        "overlayPoints": counts,
        "firstChartCandleTime": payload.get("firstCandleTime"),
        "lastChartCandleTime": payload.get("lastCandleTime"),
        "firstOverlayTime": min(times) if times else None,
        "lastOverlayTime": max(times) if times else None,
        "warmupBars": {"EMA50": 50, "EMA200": 200, "Donchian55": 55, "ATR14": 14},
        "droppedBarsReason": "none; overlays are full-length arrays with null values before warmup",
    }


def run_shared_optimizer_engine(source: str, symbol: str, timeframe: str, period: str, preset: str, limit: int, max_combos: int) -> dict:
    days = period[:-1] if period.endswith("d") else "365"
    completed = subprocess.run(
        [
            node_executable(),
            "cli/optimize.js",
            "--staged",
            "--source",
            source,
            "--symbol",
            symbol,
            "--interval",
            timeframe,
            "--days",
            days,
            "--strategy",
            NODE_STRATEGIES.get(preset, preset),
            "--limit",
            str(limit),
            "--max-combos",
            str(max_combos),
            "--progress-every",
            "200",
        ],
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=360,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Node optimizer failed")
    return json.loads(completed.stdout)


def parse_csv_arg(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or fallback


def ranking_metrics_from_backtest(payload: dict) -> dict:
    return {
        "totalReturn": safe_float(payload.get("total_return_pct", payload.get("totalReturn", 0))),
        "winRate": safe_float(payload.get("win_rate", payload.get("winRate", 0))),
        "maxDrawdown": safe_float(payload.get("max_drawdown", payload.get("maxDrawdown", 0))),
        "profitFactor": safe_float(payload.get("profit_factor", payload.get("profitFactor", 0))),
        "trades": int(safe_float(payload.get("number_of_trades", payload.get("trades", 0)))),
        "averageBarsHeld": safe_float(payload.get("average_bars_held", payload.get("avgBarsHeld", 0))),
    }


def safe_float(value, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(number) or math.isinf(number):
        return fallback
    return number


def ranking_score(metrics: dict, min_trades: int = 10) -> float:
    """Backend-only ranking score for the /analysis page.

    The frontend must render this score as data and must not duplicate this
    formula in JavaScript.
    """
    profit_factor = min(metrics["profitFactor"], 5)
    trades = metrics["trades"]
    score = (
        metrics["totalReturn"] * 2
        + max(0, profit_factor - 1) * 30
        + min(trades, 100) * 0.1
        - metrics["maxDrawdown"] * 2
    )
    if trades == 0:
        score -= 100
    elif trades < min_trades:
        score -= (min_trades - trades) * 8
    return round(score, 2)


def ranking_cards(rows: list[dict]) -> dict:
    if not rows:
        return {
            "bestOverall": None,
            "bestWinRate": None,
            "lowestDrawdown": None,
            "worstResult": None,
        }
    metric_rows = [row for row in rows if not row.get("warnings") or row.get("trades", 0) > 0] or rows
    return {
        "bestOverall": rows[0],
        "bestWinRate": max(metric_rows, key=lambda item: item["winRate"]),
        "lowestDrawdown": min(metric_rows, key=lambda item: item["maxDrawdown"]),
        "worstResult": min(rows, key=lambda item: item["score"]),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
