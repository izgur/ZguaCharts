from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from data_source import DATA_SOURCE_CONFIG, fetch_candles, fetch_historical_candles
from indicators import available_indicators, build_indicator_payload
from signals import build_signal_payload
from strategy import DEFAULT_PRESET_ID, preset_options


app = Flask(__name__)

BACKTEST_HISTORY_PATH = Path(app.root_path) / "data" / "backtest-history.json"
PAPER_CANDIDATE_PATH = Path(app.root_path) / "config" / "paper-candidate.json"
RESEARCH_RUNS_PATH = Path(app.root_path) / "data" / "research-runs.json"
LEARNING_CONFIG_PATH = Path(app.root_path) / "config" / "learning-runner.json"
LEARNING_REPORTS_PATH = Path(app.root_path) / "data" / "learning-reports.json"
MAX_RESEARCH_RUNS = 200
MAX_RESEARCH_ROWS = 50
MAX_LEARNING_REPORTS = 100

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
@app.get("/learning")
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


@app.get("/api/strategy-optimize")
def strategy_optimize():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "1h")
    strategy = request.args.get("strategy") or request.args.get("preset") or "RegimeFilteredTrendStrategy"
    period = request.args.get("period", "365d")
    limit = int(request.args.get("limit", "9000"))
    max_combos = int(request.args.get("max_combos", "500"))
    train_ratio = float(request.args.get("train_ratio", "0.7"))
    fee_pct = float(request.args.get("fee_pct", "0"))
    slippage_pct = float(request.args.get("slippage_pct", "0"))
    save_run = request.args.get("save", "true").lower() not in {"0", "false", "no", "off"}

    try:
        return jsonify(run_strategy_optimization_payload(
            source,
            symbol,
            timeframe,
            period,
            strategy,
            limit,
            max_combos,
            train_ratio,
            fee_pct,
            slippage_pct,
            save_run=save_run,
        ))
    except Exception as exc:
        return jsonify({"error": f"Could not run strategy optimizer: {exc}"}), 502


@app.get("/api/research/runs")
def research_runs():
    limit = int(request.args.get("limit", "50"))
    run_type = request.args.get("type")
    runs = load_research_runs()
    if run_type:
        runs = [run for run in runs if run.get("type") == run_type]
    return jsonify({
        "runs": runs[-limit:][::-1],
        "summary": summarize_research_runs(load_research_runs()),
    })


@app.get("/api/research/runs/<run_id>")
def research_run(run_id):
    run = get_research_run_by_id(run_id)
    if not run:
        return jsonify({"error": f"Research run not found: {run_id}"}), 404
    return jsonify(run)


@app.get("/api/research/best-candidate")
def research_best_candidate():
    candidate = best_saved_candidate(load_research_runs())
    if not candidate:
        return jsonify({"candidate": None, "reason": "No valid saved research candidate found."})
    return jsonify({"candidate": candidate})


@app.post("/api/research/suggest-candidate")
def research_suggest_candidate():
    candidate = best_saved_candidate(load_research_runs())
    current = candidate_summary(read_json_file(str(PAPER_CANDIDATE_PATH), {}))
    if not candidate:
        return jsonify({
            "action": "NO_VALID_CANDIDATE",
            "reason": "No valid saved research candidate found.",
            "candidate": None,
            "currentCandidate": current,
        })
    current_score = current_candidate_score(current)
    if current_score is not None and current_score >= safe_float(candidate.get("score")):
        return jsonify({
            "action": "KEEP_CURRENT",
            "reason": f"Current candidate score ({current_score}) is at least as strong as best saved candidate ({candidate.get('score')}).",
            "candidate": candidate,
            "currentCandidate": current,
        })
    return jsonify({
        "action": "PROMOTE",
        "reason": "Best saved candidate has a stronger backend research score than the current promoted candidate.",
        "candidate": candidate,
        "currentCandidate": current,
    })


@app.post("/api/research/suggest-replacement")
def research_suggest_replacement():
    health_payload = build_candidate_health(candidate_health_rules(request.args))
    health = health_payload["health"]
    current = health_payload["candidate"]
    if health["status"] == "HEALTHY":
        return jsonify({
            "action": "KEEP_CURRENT",
            "reason": "Current paper candidate health is still aligned with expectations.",
            "health": health,
            "candidate": None,
            "currentCandidate": current,
        })
    if health["status"] == "UNKNOWN":
        return jsonify({
            "action": "WAIT_FOR_MORE_DATA",
            "reason": "Not enough paper trades to judge candidate health.",
            "health": health,
            "candidate": None,
            "currentCandidate": current,
        })
    candidate = best_saved_candidate(load_research_runs())
    if not candidate:
        return jsonify({
            "action": "NO_VALID_CANDIDATE",
            "reason": "Candidate health is weak, but no valid saved replacement candidate exists.",
            "health": health,
            "candidate": None,
            "currentCandidate": current,
        })
    return jsonify({
        "action": "PROMOTE",
        "reason": "Candidate health is weak; a saved research candidate is available for manual review.",
        "health": health,
        "candidate": candidate,
        "currentCandidate": current,
    })


@app.get("/api/learning/config")
def learning_config():
    return jsonify(safe_learning_config(load_learning_config()))


@app.post("/api/learning/config")
def update_learning_config():
    payload = request.get_json(silent=True) or {}
    config = load_learning_config()
    config.update(safe_learning_config_updates(payload))
    config["autoPromote"] = False
    config["autoEnablePaper"] = False
    config["nextRunAt"] = compute_next_learning_run(config, datetime.now().astimezone()).isoformat()
    write_learning_config(config)
    return jsonify(safe_learning_config(config))


@app.get("/api/learning/status")
def learning_status():
    config = load_learning_config()
    latest = latest_learning_report_summary()
    return jsonify({
        "enabled": config.get("enabled", False),
        "schedule": config.get("schedule", {}),
        "lastRunAt": config.get("lastRunAt"),
        "nextRunAt": config.get("nextRunAt") or compute_next_learning_run(config, datetime.now().astimezone()).isoformat(),
        "lock": config.get("lock", {}),
        "latestReport": latest,
        "latestRecommendation": (latest or {}).get("recommendation"),
    })


@app.post("/api/learning/run")
def learning_run():
    config = load_learning_config()
    overrides = safe_learning_config_updates(request.get_json(silent=True) or {})
    config.update(overrides)
    config["autoPromote"] = False
    config["autoEnablePaper"] = False
    report = run_learning_cycle(config)
    append_learning_report(report)
    return jsonify(report)


@app.post("/api/learning/tick")
def learning_tick():
    payload = request.get_json(silent=True) or {}
    result = run_due_learning_cycle(force=bool(payload.get("force")))
    return jsonify(result)


@app.get("/api/learning/reports")
def learning_reports():
    limit = int(request.args.get("limit", "20"))
    reports = load_learning_reports()
    return jsonify({
        "reports": reports[-limit:][::-1],
        "summary": {
            "totalReports": len(reports),
            "latestReportAt": reports[-1].get("createdAt") if reports else None,
            "latestRecommendation": (reports[-1].get("recommendation") if reports else None),
        },
    })


@app.get("/api/learning/reports/<report_id>")
def learning_report(report_id):
    report = get_learning_report_by_id(report_id)
    if not report:
        return jsonify({"error": f"Learning report not found: {report_id}"}), 404
    return jsonify(report)


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
    save_run = request.args.get("save", "true").lower() not in {"0", "false", "no", "off"}
    try:
        return jsonify(run_strategy_ranking_payload(
            source,
            symbols,
            timeframes,
            presets,
            period,
            limit,
            fee_pct,
            slippage_pct,
            min_trades,
            allow_shorts,
            max_runs,
            save_run=save_run,
        ))
    except ValueError as exc:
        return jsonify({
            "error": str(exc),
            "requested": {
                "symbols": symbols,
                "timeframes": timeframes,
                "presets": presets,
                "limit": limit,
                "minTrades": min_trades,
                "maxRuns": max_runs,
            },
        }), 400


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


@app.get("/api/candidate/health")
def candidate_health():
    try:
        return jsonify(build_candidate_health(candidate_health_rules(request.args)))
    except Exception as exc:
        return jsonify({"error": f"Could not calculate candidate health: {exc}"}), 502


@app.get("/api/candidate/validate")
def validate_candidate():
    try:
        candidate = read_json_file(str(PAPER_CANDIDATE_PATH), {})
        validation = validate_candidate_config(candidate, candidate_validation_rules(request.args))
        return jsonify({
            "candidate": candidate_summary(candidate),
            "validation": validation,
        })
    except Exception as exc:
        return jsonify({"error": f"Could not validate candidate: {exc}"}), 502


@app.post("/api/candidate/enable-paper")
def enable_paper_candidate():
    try:
        payload = request.get_json(silent=True) or {}
        force = bool(payload.get("force"))
        candidate = read_json_file(str(PAPER_CANDIDATE_PATH), {})
        validation = validate_candidate_config(candidate, candidate_validation_rules(request.args))
        if validation["status"] != "PASS" and not force:
            return jsonify({
                "error": f"Candidate validation status is {validation['status']}; paper simulation was not enabled.",
                "validation": validation,
                "candidate": candidate_summary(candidate),
            }), 400

        backup_path = backup_candidate_config(candidate)
        updated = dict(candidate)
        updated["enabled"] = True
        updated["enabledAt"] = datetime.now(timezone.utc).isoformat()
        if validation["status"] != "PASS":
            updated.setdefault("validationWarnings", []).append({
                "enabledWithForce": True,
                "enabledAt": updated["enabledAt"],
                "status": validation["status"],
                "reasons": collect_validation_reasons(validation),
            })
        write_candidate_config(updated)
        return jsonify({
            "ok": True,
            "message": "Paper simulation enabled for the current candidate.",
            "backupPath": str(backup_path.relative_to(app.root_path)),
            "candidate": candidate_summary(updated),
            "validation": validation,
        })
    except Exception as exc:
        return jsonify({"error": f"Could not enable paper simulation: {exc}"}), 502


@app.post("/api/candidate/disable-paper")
def disable_paper_candidate():
    try:
        candidate = read_json_file(str(PAPER_CANDIDATE_PATH), {})
        backup_path = backup_candidate_config(candidate)
        updated = dict(candidate)
        updated["enabled"] = False
        updated["disabledAt"] = datetime.now(timezone.utc).isoformat()
        write_candidate_config(updated)
        return jsonify({
            "ok": True,
            "message": "Paper simulation disabled.",
            "backupPath": str(backup_path.relative_to(app.root_path)),
            "candidate": candidate_summary(updated),
        })
    except Exception as exc:
        return jsonify({"error": f"Could not disable paper simulation: {exc}"}), 502


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
        write_candidate_config(updated)

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


def run_strategy_ranking_payload(
    source: str,
    symbols: list[str],
    timeframes: list[str],
    presets: list[str],
    period: str,
    limit: int,
    fee_pct: float,
    slippage_pct: float,
    min_trades: int,
    allow_shorts: bool,
    max_runs: int,
    save_run: bool = True,
) -> dict:
    runs_requested = len(symbols) * len(timeframes) * len(presets)
    if runs_requested > max_runs:
        raise ValueError(
            f"Requested {runs_requested} ranking runs, but max_runs is {max_runs}. "
            "Narrow symbols, timeframes, presets, or raise max_runs intentionally."
        )

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
                    rows.append({
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
                    })
                except Exception as exc:
                    errors.append({"symbol": symbol, "timeframe": timeframe, "preset": preset, "error": str(exc)})
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

    payload = {
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
    }
    if save_run:
        payload["researchRunId"] = append_research_run(research_record_from_ranking(payload))
    return payload


def run_strategy_optimization_payload(
    source: str,
    symbol: str,
    timeframe: str,
    period: str,
    strategy: str,
    limit: int,
    max_combos: int,
    train_ratio: float,
    fee_pct: float,
    slippage_pct: float,
    save_run: bool = True,
) -> dict:
    raw = run_strategy_optimizer_engine(source, symbol, timeframe, period, strategy, limit, max_combos, train_ratio, fee_pct, slippage_pct)
    payload = normalize_optimizer_payload(raw, source, symbol, timeframe, strategy, period, limit, max_combos, train_ratio, fee_pct, slippage_pct)
    if save_run:
        payload["researchRunId"] = append_research_run(research_record_from_optimization(payload))
    return payload


def default_learning_config() -> dict:
    return {
        "enabled": False,
        "schedule": {
            "enabled": False,
            "mode": "manual",
            "intervalMinutes": 1440,
            "runAtHour": 3,
            "runAtMinute": 0,
            "timezone": "local",
        },
        "lastRunAt": None,
        "nextRunAt": None,
        "lock": {
            "running": False,
            "startedAt": None,
        },
        "source": "bybit",
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "timeframes": ["1h"],
        "rankingPresets": ["conservative_trend", "regime_filtered_trend", "pullback_trend"],
        "optimizationStrategies": ["regime_filtered_trend"],
        "period": "365d",
        "rankingLimit": 5000,
        "optimizationLimit": 9000,
        "maxRankingRuns": 20,
        "maxOptimizationCombos": 300,
        "minTrades": 20,
        "feePct": 0,
        "slippagePct": 0,
        "allowShorts": False,
        "autoPromote": False,
        "autoEnablePaper": False,
    }


def load_learning_config() -> dict:
    config = default_learning_config()
    if LEARNING_CONFIG_PATH.exists():
        try:
            with open(LEARNING_CONFIG_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                config.update(data)
        except Exception:
            pass
    config["autoPromote"] = False
    config["autoEnablePaper"] = False
    return config


def write_learning_config(config: dict) -> None:
    LEARNING_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEARNING_CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(safe_learning_config(config), handle, indent=2)
        handle.write("\n")


def save_learning_config(config: dict) -> None:
    write_learning_config(config)


def safe_learning_config(config: dict) -> dict:
    base = default_learning_config()
    base.update({key: config.get(key, base[key]) for key in base})
    base["schedule"] = safe_learning_schedule(base.get("schedule", {}))
    lock = base.get("lock") if isinstance(base.get("lock"), dict) else {}
    base["lock"] = {"running": bool(lock.get("running", False)), "startedAt": lock.get("startedAt")}
    base["lastRunAt"] = base.get("lastRunAt")
    base["nextRunAt"] = base.get("nextRunAt")
    base["symbols"] = list_string_values(base.get("symbols"), default_learning_config()["symbols"])
    base["timeframes"] = list_string_values(base.get("timeframes"), default_learning_config()["timeframes"])
    base["rankingPresets"] = list_string_values(base.get("rankingPresets"), default_learning_config()["rankingPresets"])
    base["optimizationStrategies"] = list_string_values(base.get("optimizationStrategies"), default_learning_config()["optimizationStrategies"])
    base["enabled"] = bool(base.get("enabled", False))
    base["allowShorts"] = bool(base.get("allowShorts", False))
    base["autoPromote"] = False
    base["autoEnablePaper"] = False
    for key in ("rankingLimit", "optimizationLimit", "maxRankingRuns", "maxOptimizationCombos", "minTrades"):
        base[key] = int(safe_float(base.get(key), default_learning_config()[key]))
    for key in ("feePct", "slippagePct"):
        base[key] = safe_float(base.get(key), default_learning_config()[key])
    return base


def safe_learning_schedule(schedule: dict) -> dict:
    defaults = default_learning_config()["schedule"]
    if not isinstance(schedule, dict):
        schedule = {}
    interval = max(15, int(safe_float(schedule.get("intervalMinutes", defaults["intervalMinutes"]), defaults["intervalMinutes"])))
    hour = min(23, max(0, int(safe_float(schedule.get("runAtHour", defaults["runAtHour"]), defaults["runAtHour"]))))
    minute = min(59, max(0, int(safe_float(schedule.get("runAtMinute", defaults["runAtMinute"]), defaults["runAtMinute"]))))
    mode = schedule.get("mode", defaults["mode"])
    if mode not in {"manual", "interval", "daily"}:
        mode = "manual"
    return {
        "enabled": bool(schedule.get("enabled", defaults["enabled"])),
        "mode": mode,
        "intervalMinutes": interval,
        "runAtHour": hour,
        "runAtMinute": minute,
        "timezone": schedule.get("timezone") or defaults["timezone"],
    }


def safe_learning_config_updates(payload: dict) -> dict:
    allowed = {
        "enabled",
        "source",
        "symbols",
        "timeframes",
        "rankingPresets",
        "optimizationStrategies",
        "period",
        "rankingLimit",
        "optimizationLimit",
        "maxRankingRuns",
        "maxOptimizationCombos",
        "minTrades",
        "feePct",
        "slippagePct",
        "allowShorts",
        "schedule",
    }
    updates = {key: value for key, value in payload.items() if key in allowed}
    if "autoPromote" in payload or "autoEnablePaper" in payload:
        updates["autoPromote"] = False
        updates["autoEnablePaper"] = False
    return safe_learning_config({**load_learning_config(), **updates})


def list_string_values(value, fallback: list[str]) -> list[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = []
    return items or fallback


def load_learning_reports() -> list[dict]:
    if not LEARNING_REPORTS_PATH.exists():
        return []
    try:
        with open(LEARNING_REPORTS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return data
        return data.get("reports", []) if isinstance(data, dict) else []
    except Exception:
        return []


def save_learning_reports(reports: list[dict]) -> None:
    LEARNING_REPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    capped = reports[-MAX_LEARNING_REPORTS:]
    with open(LEARNING_REPORTS_PATH, "w", encoding="utf-8") as handle:
        json.dump(capped, handle, indent=2)
        handle.write("\n")


def append_learning_report(report: dict) -> str:
    reports = load_learning_reports()
    reports.append(report)
    save_learning_reports(reports)
    return report["id"]


def get_learning_report_by_id(report_id: str) -> dict | None:
    return next((report for report in load_learning_reports() if report.get("id") == report_id), None)


def latest_learning_report_summary() -> dict | None:
    reports = load_learning_reports()
    if not reports:
        return None
    report = reports[-1]
    return {
        "id": report.get("id"),
        "createdAt": report.get("createdAt"),
        "status": report.get("status"),
        "recommendation": report.get("recommendation"),
        "rankingRunIds": report.get("rankingRunIds", []),
        "optimizationRunIds": report.get("optimizationRunIds", []),
    }


def parse_learning_time(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed
    except Exception:
        return None


def compute_next_learning_run(config: dict, now: datetime) -> datetime:
    config = safe_learning_config(config)
    schedule = config["schedule"]
    if now.tzinfo is None:
        now = now.astimezone()
    last_run = parse_learning_time(config.get("lastRunAt"))
    if schedule["mode"] == "daily":
        candidate = now.replace(hour=schedule["runAtHour"], minute=schedule["runAtMinute"], second=0, microsecond=0)
        if candidate <= now:
            candidate = candidate.replace(day=candidate.day) + timedelta(days=1)
        return candidate
    base = last_run or now
    if last_run and last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=now.tzinfo)
    next_run = base + timedelta(minutes=schedule["intervalMinutes"])
    return next_run if next_run > now else now


def should_run_learning_cycle(config: dict, now: datetime) -> tuple[bool, str]:
    config = safe_learning_config(config)
    if not config.get("enabled", False):
        return False, "Learning runner disabled."
    schedule = config.get("schedule", {})
    if not schedule.get("enabled", False):
        return False, "Learning schedule disabled."
    if schedule.get("mode") == "manual":
        return False, "Learning schedule mode is manual."
    next_run = parse_learning_time(config.get("nextRunAt")) or compute_next_learning_run(config, now)
    if next_run <= now:
        return True, "Learning cycle is due."
    return False, f"Next learning cycle is scheduled for {next_run.isoformat()}."


def acquire_learning_lock() -> bool:
    config = load_learning_config()
    if config.get("lock", {}).get("running"):
        return False
    config["lock"] = {"running": True, "startedAt": datetime.now().astimezone().isoformat()}
    save_learning_config(config)
    return True


def release_learning_lock() -> None:
    config = load_learning_config()
    config["lock"] = {"running": False, "startedAt": None}
    save_learning_config(config)


def run_due_learning_cycle(force: bool = False) -> dict:
    now = datetime.now().astimezone()
    config = load_learning_config()
    due, reason = (True, "Forced learning tick.") if force else should_run_learning_cycle(config, now)
    if not due:
        next_run = config.get("nextRunAt") or compute_next_learning_run(config, now).isoformat()
        return {"ran": False, "reason": reason, "report": None, "lastRunAt": config.get("lastRunAt"), "nextRunAt": next_run}
    if not acquire_learning_lock():
        config = load_learning_config()
        return {"ran": False, "reason": "Learning cycle already running.", "report": None, "lastRunAt": config.get("lastRunAt"), "nextRunAt": config.get("nextRunAt")}
    try:
        config = load_learning_config()
        report = run_learning_cycle(config)
        append_learning_report(report)
        config = load_learning_config()
        config["lastRunAt"] = datetime.now().astimezone().isoformat()
        config["nextRunAt"] = compute_next_learning_run(config, datetime.now().astimezone()).isoformat()
        config["lock"] = {"running": False, "startedAt": None}
        config["autoPromote"] = False
        config["autoEnablePaper"] = False
        save_learning_config(config)
        return {"ran": True, "reason": reason, "report": report, "lastRunAt": config.get("lastRunAt"), "nextRunAt": config.get("nextRunAt")}
    except Exception as exc:
        config = load_learning_config()
        config["lock"] = {"running": False, "startedAt": None}
        save_learning_config(config)
        return {"ran": False, "reason": f"Learning cycle failed: {exc}", "report": None, "lastRunAt": config.get("lastRunAt"), "nextRunAt": config.get("nextRunAt")}


def run_learning_cycle(config: dict) -> dict:
    config = safe_learning_config(config)
    created_at = datetime.now(timezone.utc).isoformat()
    report = {
        "id": f"learning-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "createdAt": created_at,
        "status": "completed",
        "config": learning_config_summary(config),
        "rankingRunIds": [],
        "optimizationRunIds": [],
        "candidateHealth": None,
        "bestSavedCandidate": None,
        "replacementSuggestion": None,
        "recommendation": None,
        "warnings": [],
        "errors": [],
    }

    try:
        ranking_payload = run_strategy_ranking_payload(
            config["source"],
            config["symbols"],
            config["timeframes"],
            config["rankingPresets"],
            config["period"],
            config["rankingLimit"],
            config["feePct"],
            config["slippagePct"],
            config["minTrades"],
            config["allowShorts"],
            config["maxRankingRuns"],
            save_run=True,
        )
        if ranking_payload.get("researchRunId"):
            report["rankingRunIds"].append(ranking_payload["researchRunId"])
        report["warnings"].extend(ranking_payload.get("errors") or [])
    except Exception as exc:
        report["errors"].append({"stage": "ranking", "error": str(exc)})

    optimization_runs = 0
    for strategy in config["optimizationStrategies"]:
        for symbol in config["symbols"]:
            for timeframe in config["timeframes"]:
                optimization_runs += 1
                try:
                    payload = run_strategy_optimization_payload(
                        config["source"],
                        symbol,
                        timeframe,
                        config["period"],
                        strategy,
                        config["optimizationLimit"],
                        config["maxOptimizationCombos"],
                        0.7,
                        config["feePct"],
                        config["slippagePct"],
                        save_run=True,
                    )
                    if payload.get("researchRunId"):
                        report["optimizationRunIds"].append(payload["researchRunId"])
                except Exception as exc:
                    report["errors"].append({"stage": "optimization", "strategy": strategy, "symbol": symbol, "timeframe": timeframe, "error": str(exc)})

    if optimization_runs == 0:
        report["warnings"].append("No optimization strategies were configured.")

    report["candidateHealth"] = build_candidate_health(candidate_health_rules({}))["health"]
    report["bestSavedCandidate"] = best_saved_candidate(load_research_runs())
    report["replacementSuggestion"] = replacement_suggestion_from_health(report["candidateHealth"])
    report["recommendation"] = learning_recommendation(report["candidateHealth"], report["bestSavedCandidate"], read_json_file(str(PAPER_CANDIDATE_PATH), {}))
    if report["errors"] and (report["rankingRunIds"] or report["optimizationRunIds"]):
        report["status"] = "partial"
    elif report["errors"]:
        report["status"] = "failed"
    return report


def learning_config_summary(config: dict) -> dict:
    return {
        "enabled": bool(config.get("enabled", False)),
        "source": config.get("source"),
        "symbols": config.get("symbols", []),
        "timeframes": config.get("timeframes", []),
        "rankingPresets": config.get("rankingPresets", []),
        "optimizationStrategies": config.get("optimizationStrategies", []),
        "period": config.get("period"),
        "rankingLimit": config.get("rankingLimit"),
        "optimizationLimit": config.get("optimizationLimit"),
        "maxRankingRuns": config.get("maxRankingRuns"),
        "maxOptimizationCombos": config.get("maxOptimizationCombos"),
        "minTrades": config.get("minTrades"),
        "feePct": config.get("feePct"),
        "slippagePct": config.get("slippagePct"),
        "allowShorts": bool(config.get("allowShorts", False)),
        "autoPromote": False,
        "autoEnablePaper": False,
    }


def replacement_suggestion_from_health(health: dict) -> dict:
    if health.get("status") == "HEALTHY":
        return {"action": "KEEP_CURRENT", "reason": "Current paper candidate health is still aligned with expectations.", "candidate": None}
    if health.get("status") == "UNKNOWN":
        return {"action": "WAIT_FOR_MORE_DATA", "reason": "Not enough paper trades or no expected baseline is available.", "candidate": None}
    candidate = best_saved_candidate(load_research_runs())
    if not candidate:
        return {"action": "NO_VALID_CANDIDATE", "reason": "No valid saved candidate exists.", "candidate": None}
    return {"action": "PROMOTE", "reason": "A valid saved candidate is available for manual review.", "candidate": candidate}


def learning_recommendation(health: dict, best_candidate: dict | None, current_candidate: dict) -> dict:
    health_status = health.get("status")
    has_current = bool(current_candidate.get("strategy") and (current_candidate.get("promotedAt") or expected_metrics_from_candidate(current_candidate).get("source")))
    if not best_candidate:
        return {"action": "NO_VALID_CANDIDATE", "reason": "No valid saved candidate exists.", "candidate": None}
    if not has_current:
        return {"action": "PROMOTE_CANDIDATE", "reason": "No promoted candidate with an expected baseline exists; manual promotion is available.", "candidate": best_candidate}
    current_score = current_candidate_score(candidate_summary(current_candidate))
    best_score = safe_float(best_candidate.get("score"))
    if health_status == "HEALTHY":
        if current_score is not None and best_score > current_score * 1.2:
            return {"action": "PROMOTE_CANDIDATE", "reason": "Current health is healthy, but saved candidate score is significantly better. Manual review required.", "candidate": best_candidate}
        return {"action": "KEEP_CURRENT", "reason": "Current paper candidate health is aligned with expectations.", "candidate": None}
    if health_status == "UNKNOWN":
        return {"action": "WAIT_FOR_MORE_PAPER_DATA", "reason": "Candidate health is unknown; wait for more paper trades before replacement unless manually chosen.", "candidate": None}
    return {"action": "PROMOTE_CANDIDATE", "reason": f"Current health is {health_status}; a valid saved candidate is available for manual review.", "candidate": best_candidate}


def load_research_runs() -> list[dict]:
    if not RESEARCH_RUNS_PATH.exists():
        return []
    try:
        with open(RESEARCH_RUNS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return data
        return data.get("runs", []) if isinstance(data, dict) else []
    except Exception:
        return []


def save_research_runs(runs: list[dict]) -> None:
    RESEARCH_RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    capped = runs[-MAX_RESEARCH_RUNS:]
    with open(RESEARCH_RUNS_PATH, "w", encoding="utf-8") as handle:
        json.dump(capped, handle, indent=2)
        handle.write("\n")


def append_research_run(record: dict) -> str:
    runs = load_research_runs()
    record = dict(record)
    record.setdefault("id", f"research-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}")
    record.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
    runs.append(record)
    save_research_runs(runs)
    return record["id"]


def get_research_run_by_id(run_id: str) -> dict | None:
    return next((run for run in load_research_runs() if run.get("id") == run_id), None)


def summarize_research_runs(runs: list[dict]) -> dict:
    best = best_saved_candidate(runs)
    return {
        "totalRuns": len(runs),
        "rankingRuns": len([run for run in runs if run.get("type") == "ranking"]),
        "optimizationRuns": len([run for run in runs if run.get("type") == "optimization"]),
        "latestRunAt": runs[-1].get("createdAt") if runs else None,
        "bestSavedCandidate": best,
    }


def research_record_from_ranking(payload: dict) -> dict:
    rows = payload.get("rows", [])
    return {
        "id": f"ranking-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "type": "ranking",
        "status": "completed" if not payload.get("errors") else "failed" if not rows else "completed",
        "source": payload.get("source"),
        "symbols": payload.get("requested", {}).get("symbols", []),
        "timeframes": payload.get("requested", {}).get("timeframes", []),
        "strategies": payload.get("requested", {}).get("presets", []),
        "presets": payload.get("requested", {}).get("presets", []),
        "period": payload.get("period"),
        "limit": payload.get("requested", {}).get("limit"),
        "fee_pct": payload.get("requested", {}).get("feePct"),
        "slippage_pct": payload.get("requested", {}).get("slippagePct"),
        "summary": payload.get("summary", {}),
        "bestCandidate": research_candidate_from_ranking_row(best_valid_or_first(rows), payload),
        "rows": [research_candidate_from_ranking_row(row, payload) for row in rows[:MAX_RESEARCH_ROWS]],
        "errors": payload.get("errors", []),
    }


def research_record_from_optimization(payload: dict) -> dict:
    candidates = payload.get("topCandidates", [])
    return {
        "id": f"optimization-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "type": "optimization",
        "status": "completed" if not payload.get("errors") else "failed" if not candidates else "completed",
        "source": payload.get("source"),
        "symbols": [payload.get("symbol")],
        "timeframes": [payload.get("timeframe")],
        "strategies": [payload.get("strategy")],
        "presets": [payload.get("strategy")],
        "period": payload.get("period"),
        "limit": payload.get("requested", {}).get("limit"),
        "fee_pct": payload.get("requested", {}).get("feePct"),
        "slippage_pct": payload.get("requested", {}).get("slippagePct"),
        "train_ratio": payload.get("requested", {}).get("trainRatio"),
        "max_combos": payload.get("requested", {}).get("maxCombos"),
        "summary": payload.get("summary", {}),
        "bestCandidate": research_candidate_from_optimization_row(best_valid_or_first(candidates), payload),
        "topCandidates": [research_candidate_from_optimization_row(row, payload) for row in candidates[:MAX_RESEARCH_ROWS]],
        "errors": payload.get("errors", []),
    }


def best_valid_or_first(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return next((row for row in rows if row.get("valid")), rows[0])


def research_candidate_from_ranking_row(row: dict | None, payload: dict) -> dict | None:
    if not row:
        return None
    return {
        "source": payload.get("source"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "strategy": row.get("strategy"),
        "preset": row.get("preset"),
        "period": row.get("period", payload.get("period")),
        "params": row.get("params", {}),
        "valid": row.get("valid", False),
        "rank": row.get("rank"),
        "score": row.get("score"),
        "totalReturnPct": row.get("totalReturnPct"),
        "winRate": row.get("winRate"),
        "maxDrawdown": row.get("maxDrawdown"),
        "profitFactor": row.get("profitFactor"),
        "trades": row.get("trades"),
        "warnings": row.get("warnings", []),
        "origin": "ranking",
    }


def research_candidate_from_optimization_row(row: dict | None, payload: dict) -> dict | None:
    if not row:
        return None
    test = row.get("test") or {}
    full = row.get("full") or {}
    return {
        "source": payload.get("source"),
        "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"),
        "strategy": payload.get("strategy"),
        "preset": payload.get("strategy"),
        "period": payload.get("period"),
        "params": row.get("params", {}),
        "valid": row.get("valid", False),
        "rank": row.get("rank"),
        "score": row.get("score"),
        "totalReturnPct": test.get("totalReturn", full.get("totalReturn", 0)),
        "winRate": test.get("winRate", full.get("winRate", 0)),
        "maxDrawdown": test.get("maxDrawdown", full.get("maxDrawdown", 0)),
        "profitFactor": test.get("profitFactor", full.get("profitFactor", 0)),
        "trades": test.get("trades", full.get("trades", 0)),
        "train": row.get("train", {}),
        "test": test,
        "full": full,
        "warnings": row.get("warnings", []),
        "overfitWarning": row.get("overfitWarning"),
        "origin": "optimization",
    }


def best_saved_candidate(runs: list[dict]) -> dict | None:
    candidates = []
    for run in runs:
        for candidate in ([run.get("bestCandidate")] + run.get("rows", []) + run.get("topCandidates", [])):
            if candidate and candidate.get("valid"):
                item = dict(candidate)
                item["researchRunId"] = run.get("id")
                item["researchRunType"] = run.get("type")
                item["createdAt"] = run.get("createdAt")
                candidates.append(item)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: safe_float(item.get("score")), reverse=True)[0]


def current_candidate_score(current: dict) -> float | None:
    for key in ("promotedFromOptimization", "promotedFromRanking"):
        source = current.get(key)
        if isinstance(source, dict) and source.get("score") is not None:
            return safe_float(source.get("score"))
    return None


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
        "promotedFromOptimization": candidate.get("promotedFromOptimization"),
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


def write_candidate_config(candidate: dict) -> None:
    PAPER_CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PAPER_CANDIDATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(candidate, handle, indent=2)
        handle.write("\n")


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
    optimization_snapshot = payload.get("optimizationSnapshot") if isinstance(payload.get("optimizationSnapshot"), dict) else None
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
        "promotedFromOptimization": optimization_snapshot,
    })
    return preserved


def candidate_validation_rules(args) -> dict:
    return {
        "period": args.get("period", "365d"),
        "limit": int(args.get("limit", "5000")),
        "minTrades": int(args.get("min_trades", "20")),
        "minProfitFactor": float(args.get("min_profit_factor", "1.1")),
        "maxDrawdown": float(args.get("max_drawdown", "25")),
        "minTotalReturnPct": float(args.get("min_total_return_pct", "0")),
        "allowShorts": args.get("allowShorts", "false").lower() in {"1", "true", "yes", "on"},
    }


def validate_candidate_config(candidate: dict, rules: dict) -> dict:
    source = candidate.get("source", "bybit")
    strategy = candidate.get("strategy")
    active_markets = candidate_symbols_by_mode(candidate, "active")
    rows = []
    if not strategy:
        rows.append(validation_error_row("-", "-", "-", ["Candidate has no strategy configured."]))
    if not active_markets:
        rows.append(validation_error_row("-", "-", strategy or "-", ["Candidate has no active markets configured."]))

    fee_pct = safe_float(candidate.get("takerFeePct", 0))
    slippage_pct = safe_float(candidate.get("slippageBps", 0)) / 100
    params = candidate.get("params") if isinstance(candidate.get("params"), dict) else {}

    for market in active_markets:
        symbol = market.get("symbol")
        timeframe = market.get("interval") or market.get("timeframe")
        try:
            payload = run_shared_backtest_engine(
                source,
                symbol,
                timeframe,
                rules["period"],
                strategy,
                fee_pct,
                slippage_pct,
                rules["limit"],
                allow_shorts=rules["allowShorts"],
                strategy_params=params,
            )
            metrics = ranking_metrics_from_backtest(payload)
            status, reasons = candidate_market_status(metrics, rules)
            rows.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "strategy": payload.get("preset") or strategy,
                "totalReturnPct": metrics["totalReturn"],
                "winRate": metrics["winRate"],
                "maxDrawdown": metrics["maxDrawdown"],
                "profitFactor": metrics["profitFactor"],
                "trades": metrics["trades"],
                "averageBarsHeld": metrics["averageBarsHeld"],
                "score": ranking_score(metrics, min_trades=rules["minTrades"]),
                "status": status,
                "reasons": reasons,
                "warnings": payload.get("warnings", []),
            })
        except Exception as exc:
            rows.append(validation_error_row(symbol, timeframe, strategy, [str(exc)]))

    pass_count = len([row for row in rows if row["status"] == "PASS"])
    warn_count = len([row for row in rows if row["status"] == "WARN"])
    fail_count = len([row for row in rows if row["status"] == "FAIL"])
    overall = "FAIL" if fail_count else "WARN" if warn_count else "PASS"
    # TODO: Add scheduled ranking, auto candidate suggestion, paper performance
    # comparison, auto-promotion only after validation, and human approval mode.
    return {
        "status": overall,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "period": rules["period"],
        "rules": {
            "minTrades": rules["minTrades"],
            "minProfitFactor": rules["minProfitFactor"],
            "maxDrawdown": rules["maxDrawdown"],
            "minTotalReturnPct": rules["minTotalReturnPct"],
            "limit": rules["limit"],
            "allowShorts": rules["allowShorts"],
        },
        "summary": {
            "marketsValidated": len(rows),
            "pass": pass_count,
            "warn": warn_count,
            "fail": fail_count,
        },
        "rows": rows,
    }


def validation_error_row(symbol: str | None, timeframe: str | None, strategy: str | None, reasons: list[str]) -> dict:
    return {
        "symbol": symbol or "-",
        "timeframe": timeframe or "-",
        "strategy": strategy or "-",
        "totalReturnPct": 0,
        "winRate": 0,
        "maxDrawdown": 0,
        "profitFactor": 0,
        "trades": 0,
        "averageBarsHeld": 0,
        "score": -999,
        "status": "FAIL",
        "reasons": reasons,
    }


def candidate_market_status(metrics: dict, rules: dict) -> tuple[str, list[str]]:
    reasons = []
    hard_fail = False
    if metrics["trades"] < rules["minTrades"]:
        hard_fail = True
        reasons.append(f"FAIL: trades below minimum ({metrics['trades']} < {rules['minTrades']}).")
    if metrics["profitFactor"] <= 1:
        hard_fail = True
        reasons.append(f"FAIL: profit factor is not above 1 ({metrics['profitFactor']}).")
    if metrics["maxDrawdown"] > rules["maxDrawdown"]:
        hard_fail = True
        reasons.append(f"FAIL: max drawdown above limit ({metrics['maxDrawdown']} > {rules['maxDrawdown']}).")
    if hard_fail:
        return "FAIL", reasons
    if metrics["profitFactor"] < rules["minProfitFactor"]:
        reasons.append(f"WARN: profit factor below target ({metrics['profitFactor']} < {rules['minProfitFactor']}).")
    close_threshold = max(0.5, abs(rules["minTotalReturnPct"]) + 0.5)
    if metrics["totalReturn"] < rules["minTotalReturnPct"]:
        reasons.append(f"WARN: total return below target ({metrics['totalReturn']} < {rules['minTotalReturnPct']}).")
    elif abs(metrics["totalReturn"] - rules["minTotalReturnPct"]) <= close_threshold:
        reasons.append("WARN: total return is close to zero.")
    return ("WARN" if reasons else "PASS"), reasons


def collect_validation_reasons(validation: dict) -> list[str]:
    reasons = []
    for row in validation.get("rows", []):
        for reason in row.get("reasons", []):
            reasons.append(f"{row.get('symbol')} {row.get('timeframe')}: {reason}")
    return reasons


def candidate_health_rules(args) -> dict:
    return {
        "minPaperTrades": int(args.get("min_paper_trades", "10")),
        "failProfitFactorBelow": float(args.get("fail_profit_factor_below", "0.9")),
        "watchProfitFactorBelow": float(args.get("watch_profit_factor_below", "1.05")),
        "failDrawdownAbove": float(args.get("fail_drawdown_above", "15")),
        "failIfRealizedLossPctBelow": float(args.get("fail_if_realized_loss_pct_below", "-5")),
        "watchIfWinRateUnderExpectedBy": float(args.get("watch_if_win_rate_under_expected_by", "15")),
    }


def build_candidate_health(rules: dict) -> dict:
    candidate = read_json_file(str(PAPER_CANDIDATE_PATH), {})
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    journal = read_jsonl_tail(os.path.join(app.root_path, "reports", "paper-journal.jsonl"), 500)
    expected = expected_metrics_from_candidate(candidate)
    paper = paper_metrics_from_state(state, candidate, journal)
    status, reasons, recommendation = candidate_health_status(expected, paper, rules)
    return {
        "candidate": candidate_summary(candidate),
        "health": {
            "status": status,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "reason": reasons[0] if reasons else "Paper performance is aligned with expectations.",
            "rules": rules,
            "expected": expected,
            "paper": paper,
            "reasons": reasons,
            "recommendation": recommendation,
        },
    }


def expected_metrics_from_candidate(candidate: dict) -> dict:
    source = candidate.get("promotedFromOptimization") if isinstance(candidate.get("promotedFromOptimization"), dict) else None
    if source:
        test = source.get("test") or {}
        full = source.get("full") or {}
        return {
            "source": "promotedFromOptimization",
            "totalReturnPct": safe_float(test.get("totalReturn", full.get("totalReturn", source.get("totalReturnPct", 0)))),
            "winRate": safe_float(test.get("winRate", full.get("winRate", source.get("winRate", 0)))),
            "maxDrawdown": safe_float(test.get("maxDrawdown", full.get("maxDrawdown", source.get("maxDrawdown", 0)))),
            "profitFactor": safe_float(test.get("profitFactor", full.get("profitFactor", source.get("profitFactor", 0)))),
            "trades": int(safe_float(test.get("trades", full.get("trades", source.get("trades", 0))))),
        }
    source = candidate.get("promotedFromRanking") if isinstance(candidate.get("promotedFromRanking"), dict) else None
    if source:
        return {
            "source": "promotedFromRanking",
            "totalReturnPct": safe_float(source.get("totalReturnPct", 0)),
            "winRate": safe_float(source.get("winRate", 0)),
            "maxDrawdown": safe_float(source.get("maxDrawdown", 0)),
            "profitFactor": safe_float(source.get("profitFactor", 0)),
            "trades": int(safe_float(source.get("trades", 0))),
        }
    return {"source": None}


def paper_metrics_from_state(state: dict, candidate: dict, journal: list[dict]) -> dict:
    closed = state.get("closedTrades", []) or []
    account_equity = safe_float(candidate.get("accountEquity", state.get("accountEquity", 10000)), 10000)
    returns = [trade_return_pct(trade) for trade in closed]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    realized = safe_float(state.get("realizedPnl", state.get("realizedPnL", 0)))
    realized_pct = (realized / account_equity * 100) if account_equity else 0
    equity_curve = state.get("equityCurve", []) or []
    return {
        "closedTrades": len(closed),
        "winRate": round(len(wins) / len(returns) * 100, 4) if returns else 0,
        "totalReturnPct": round(sum(returns), 4),
        "realizedPnLPct": round(realized_pct, 4),
        "realizedPnL": round(realized, 4),
        "maxDrawdown": paper_max_drawdown(equity_curve),
        "profitFactor": paper_profit_factor(wins, losses, closed),
        "averageTradeReturn": round(sum(returns) / len(returns), 4) if returns else 0,
        "averageBarsHeld": paper_average_bars_held(closed),
        "openPositions": len(state.get("openPositions", []) or []),
        "timeSincePromotion": seconds_since(candidate.get("promotedAt")),
        "timeSinceEnabled": seconds_since(candidate.get("enabledAt")),
        "journalEvents": len(journal),
    }


def trade_return_pct(trade: dict) -> float:
    for key in ("returnPct", "return_pct", "netReturnPct"):
        if key in trade:
            return safe_float(trade.get(key))
    pnl = safe_float(trade.get("netPnl", trade.get("pnl", 0)))
    entry = safe_float(trade.get("entryFillPrice", trade.get("entryPrice", 0)))
    size = abs(safe_float(trade.get("size", 1), 1))
    notional = entry * size
    return (pnl / notional * 100) if notional else 0


def paper_profit_factor(wins: list[float], losses: list[float], trades: list[dict]) -> float:
    if trades and any("netPnl" in trade or "pnl" in trade for trade in trades):
        gross_win = sum(max(0, safe_float(trade.get("netPnl", trade.get("pnl", 0)))) for trade in trades)
        gross_loss = abs(sum(min(0, safe_float(trade.get("netPnl", trade.get("pnl", 0)))) for trade in trades))
    else:
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
    if gross_loss == 0:
        return round(gross_win, 4) if gross_win else 0
    return round(gross_win / gross_loss, 4)


def paper_average_bars_held(trades: list[dict]) -> float:
    values = [safe_float(trade.get("barsHeld", trade.get("bars_held", 0))) for trade in trades if trade.get("barsHeld") or trade.get("bars_held")]
    return round(sum(values) / len(values), 4) if values else 0


def paper_max_drawdown(equity_curve: list[dict]) -> float:
    if not equity_curve:
        return 0
    peak = -math.inf
    max_dd = 0
    for point in equity_curve:
        equity = safe_float(point.get("equity", 0))
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return round(max_dd, 4)


def seconds_since(timestamp: str | None):
    if not timestamp:
        return None
    try:
        normalized = timestamp.replace("Z", "+00:00")
        then = datetime.fromisoformat(normalized)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - then).total_seconds())
    except Exception:
        return None


def candidate_health_status(expected: dict, paper: dict, rules: dict) -> tuple[str, list[str], dict]:
    if not expected.get("source"):
        return "UNKNOWN", ["No promoted ranking or optimization expectation is available."], {
            "action": "WATCH",
            "reason": "No expected baseline exists for this candidate.",
        }
    if paper["closedTrades"] < rules["minPaperTrades"]:
        return "UNKNOWN", [f"Not enough closed paper trades ({paper['closedTrades']} < {rules['minPaperTrades']})."], {
            "action": "WATCH",
            "reason": "Wait for more forward paper trades before judging health.",
        }

    reasons = []
    status = "HEALTHY"
    if paper["profitFactor"] < rules["failProfitFactorBelow"]:
        status = "FAILED"
        reasons.append(f"Paper profit factor below failure threshold ({paper['profitFactor']} < {rules['failProfitFactorBelow']}).")
    elif paper["profitFactor"] < rules["watchProfitFactorBelow"]:
        status = "WATCH"
        reasons.append(f"Paper profit factor below watch threshold ({paper['profitFactor']} < {rules['watchProfitFactorBelow']}).")
    if paper["maxDrawdown"] > rules["failDrawdownAbove"]:
        status = "FAILED"
        reasons.append(f"Paper drawdown above failure threshold ({paper['maxDrawdown']} > {rules['failDrawdownAbove']}).")
    if paper["realizedPnLPct"] < rules["failIfRealizedLossPctBelow"]:
        status = "FAILED"
        reasons.append(f"Realized PnL percent below guardrail ({paper['realizedPnLPct']} < {rules['failIfRealizedLossPctBelow']}).")
    expected_win = safe_float(expected.get("winRate"))
    if expected_win and paper["winRate"] < expected_win - rules["watchIfWinRateUnderExpectedBy"]:
        if status == "HEALTHY":
            status = "WATCH"
        reasons.append(f"Paper win rate trails expected by more than {rules['watchIfWinRateUnderExpectedBy']} points.")
    if status == "HEALTHY" and paper["profitFactor"] < safe_float(expected.get("profitFactor", 0)) * 0.75:
        status = "DEGRADED"
        reasons.append("Paper profit factor is materially below expected baseline.")

    if status == "FAILED":
        recommendation = {"action": "DISABLE_PAPER", "reason": "Paper performance breached failure guardrails. Review before continuing."}
    elif status in {"DEGRADED", "WATCH"}:
        recommendation = {"action": "SEARCH_REPLACEMENT", "reason": "Search saved research for a replacement candidate, but require manual approval."}
    else:
        recommendation = {"action": "KEEP", "reason": "Paper performance is reasonably aligned with expectations."}
    return status, reasons or ["Paper performance is reasonably aligned with expectations."], recommendation


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


def run_shared_backtest_engine(source: str, symbol: str, timeframe: str, period: str, preset: str, fee_pct: float, slippage_pct: float, limit: int, debug: bool = False, allow_shorts: bool = False, strategy_params: dict | None = None) -> dict:
    """Bridge Flask to the reusable Node research engine.

    Python keeps responsibility for broker adapters that already work here
    (yfinance, Bybit cache, Hyperliquid). Simulation rules live in /core so
    the UI, CLI optimizer, and future workers all share one backtest engine.
    """
    candles_payload = fetch_historical_candles(source, symbol, timeframe, period=period, limit=limit)
    params = {
        **(strategy_params or {}),
        "feePct": fee_pct,
        "slippagePct": slippage_pct,
        "shortMode": allow_shorts,
    }
    engine_input = {
        "source": source,
        "symbol": symbol,
        "interval": timeframe,
        "timeframe": timeframe,
        "strategy": NODE_STRATEGIES.get(preset, preset),
        "preset": NODE_STRATEGIES.get(preset, preset),
        "limit": limit,
        "params": params,
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


def run_strategy_optimizer_engine(source: str, symbol: str, timeframe: str, period: str, strategy: str, limit: int, max_combos: int, train_ratio: float, fee_pct: float, slippage_pct: float) -> dict:
    days = period[:-1] if period.endswith("d") else "365"
    completed = subprocess.run(
        [
            node_executable(),
            "cli/optimize.js",
            "--source",
            source,
            "--symbol",
            symbol,
            "--interval",
            timeframe,
            "--days",
            days,
            "--strategy",
            NODE_STRATEGIES.get(strategy, strategy),
            "--limit",
            str(limit),
            "--max-combos",
            str(max_combos),
            "--trainRatio",
            str(train_ratio),
            "--fee-pct",
            str(fee_pct),
            "--slippage-pct",
            str(slippage_pct),
            "--progress-every",
            "999999",
        ],
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=360,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Node strategy optimizer failed")
    return json.loads(completed.stdout)


def normalize_optimizer_payload(raw: dict, source: str, symbol: str, timeframe: str, strategy: str, period: str, limit: int, max_combos: int, train_ratio: float, fee_pct: float, slippage_pct: float) -> dict:
    candidates = optimizer_candidates(raw)
    rows = [normalize_optimizer_candidate(row, index + 1) for index, row in enumerate(candidates[:20])]
    return {
        "source": source,
        "strategy": NODE_STRATEGIES.get(strategy, strategy),
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "requested": {
            "limit": limit,
            "maxCombos": max_combos,
            "trainRatio": train_ratio,
            "feePct": fee_pct,
            "slippagePct": slippage_pct,
        },
        "summary": {
            "combinationsTested": optimizer_combinations_tested(raw),
            "validCandidates": raw.get("validCandidates", 0),
            "warnings": optimizer_warnings(raw),
        },
        "topCandidates": rows,
        "rawSummary": raw.get("summary") or {key: raw.get(key) for key in ("robustnessAssessment", "bestTestResult", "combinationsTested")},
        "errors": [],
    }


def optimizer_candidates(raw: dict) -> list[dict]:
    if isinstance(raw.get("results"), list):
        return raw["results"]
    if isinstance(raw.get("top5"), list):
        return raw["top5"]
    if isinstance(raw.get("stage3"), list):
        return raw["stage3"]
    best = raw.get("optimizedPerformance") or raw.get("bestTestResult")
    return [best] if best else []


def normalize_optimizer_candidate(row: dict, rank: int) -> dict:
    train = row.get("train") or {}
    test = row.get("test") or {}
    full = row.get("full") or {}
    walk_forward = row.get("walkForward") or []
    score = optimizer_candidate_score(row)
    warnings = optimizer_candidate_warnings(row, train, test, full, walk_forward)
    return {
        "rank": rank,
        "valid": bool(row.get("valid")) and not any(warning.startswith("FAIL") for warning in warnings),
        "params": row.get("params", {}),
        "score": score,
        "train": train,
        "test": test,
        "full": full,
        "walkForward": walk_forward,
        "warnings": warnings,
        "overfitWarning": train_test_overfit_warning(train, test),
    }


def optimizer_candidate_score(row: dict) -> float:
    train = row.get("train") or {}
    test = row.get("test") or {}
    full = row.get("full") or {}
    train_return = safe_float(train.get("totalReturn"))
    test_return = safe_float(test.get("totalReturn"))
    full_return = safe_float(full.get("totalReturn", test_return))
    test_pf = safe_float(test.get("profitFactor"))
    full_pf = safe_float(full.get("profitFactor", test_pf))
    test_dd = safe_float(test.get("maxDrawdown"))
    trades = safe_float(test.get("trades"))
    mismatch = abs(train_return - test_return)
    score = (
        test_return * 2
        + max(0, test_pf - 1) * 35
        + max(0, full_pf - 1) * 15
        + min(trades, 100) * 0.1
        - test_dd * 2
        - mismatch
    )
    return round(score, 4)


def optimizer_candidate_warnings(row: dict, train: dict, test: dict, full: dict, walk_forward: list) -> list[str]:
    warnings = []
    if safe_float(test.get("trades")) < 20:
        warnings.append("FAIL: low test trade count")
    if safe_float(test.get("profitFactor")) <= 1:
        warnings.append("FAIL: test profit factor <= 1")
    elif safe_float(test.get("profitFactor")) < 1.1:
        warnings.append("WARN: weak test profit factor")
    if safe_float(test.get("maxDrawdown")) > 25:
        warnings.append("FAIL: high test drawdown")
    if train_test_overfit_warning(train, test):
        warnings.append(train_test_overfit_warning(train, test))
    if full and safe_float(full.get("profitFactor")) <= 1:
        warnings.append("WARN: full-period profit factor <= 1")
    negative_folds = len([fold for fold in walk_forward if safe_float((fold.get("test") or fold).get("totalReturn")) < 0])
    if walk_forward and negative_folds > len(walk_forward) / 2:
        warnings.append("WARN: unstable walk-forward result")
    if not row.get("valid"):
        warnings.append("WARN: optimizer did not mark this candidate valid")
    return warnings


def train_test_overfit_warning(train: dict, test: dict) -> str | None:
    train_return = safe_float(train.get("totalReturn"))
    test_return = safe_float(test.get("totalReturn"))
    if train_return > 10 and test_return <= 0:
        return "WARN: strong train result but weak/negative test result"
    if abs(train_return - test_return) > 25:
        return "WARN: large train/test mismatch"
    return None


def optimizer_combinations_tested(raw: dict):
    if isinstance(raw.get("combinationsTested"), dict):
        return raw["combinationsTested"]
    return raw.get("combinations") or raw.get("totalResults") or 0


def optimizer_warnings(raw: dict) -> list[str]:
    warnings = []
    summary = raw.get("summary") or {}
    for value in (raw.get("warning"), summary.get("warning"), raw.get("robustnessAssessment"), summary.get("robustnessAssessment")):
        if value:
            warnings.append(value)
    # TODO: Add scheduled optimization runs, automatic candidate suggestions,
    # human approval queues, auto-promotion after validation, and paper-performance monitoring.
    return warnings


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
