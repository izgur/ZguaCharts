from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from data_source import (
    BYBIT_MAX_CACHE_CANDLES,
    DATA_SOURCE_CONFIG,
    bybit_symbol_validation_payload,
    fetch_candles,
    fetch_historical_candles,
    inspect_all_bybit_cache,
    inspect_bybit_cache,
    load_bybit_disk_cache,
    parse_period_to_days,
    historical_limit_for_period,
    research_data_readiness,
    validate_bybit_symbol,
)
from indicators import available_indicators, build_indicator_payload
from signals import build_signal_payload
from strategy import DEFAULT_PRESET_ID, preset_options


app = Flask(__name__)

BACKTEST_HISTORY_PATH = Path(app.root_path) / "data" / "backtest-history.json"
CONFIG_DIR = Path(app.root_path) / "config"
LOCAL_CONFIG_DIR = CONFIG_DIR / "local"
PAPER_CANDIDATE_DEFAULT_PATH = CONFIG_DIR / "paper-candidate.default.json"
PAPER_CANDIDATE_LOCAL_PATH = LOCAL_CONFIG_DIR / "paper-candidate.json"
PAPER_STATE_PATH = Path(app.root_path) / "data" / "paper-state.json"
PAPER_JOURNAL_PATH = Path(app.root_path) / "reports" / "paper-journal.jsonl"
RESEARCH_RUNS_PATH = Path(app.root_path) / "data" / "research-runs.json"
LEARNING_CONFIG_DEFAULT_PATH = CONFIG_DIR / "learning-runner.default.json"
LEARNING_CONFIG_LOCAL_PATH = LOCAL_CONFIG_DIR / "learning-runner.json"
LEARNING_REPORTS_PATH = Path(app.root_path) / "data" / "learning-reports.json"
LEARNING_DECISIONS_PATH = Path(app.root_path) / "data" / "learning-decisions.json"
RESEARCH_AUTOPILOT_DIR = Path(app.root_path) / "reports" / "research-autopilot"
RESEARCH_AUTOPILOT_QUEUE_PATH = RESEARCH_AUTOPILOT_DIR / "research-queue.json"
RESEARCH_AUTOPILOT_MEMORY_PATH = RESEARCH_AUTOPILOT_DIR / "research-memory.json"
RESEARCH_DOSSIER_DIR = Path(app.root_path) / "reports" / "research-dossiers"
PAPER_CANDIDATE_REVIEW_DIR = Path(app.root_path) / "reports" / "paper-candidates"
PAPER_CANDIDATE_ENABLE_AUDIT_DIR = PAPER_CANDIDATE_REVIEW_DIR / "enable-audits"
PAPER_TICK_AUDIT_DIR = PAPER_CANDIDATE_REVIEW_DIR / "tick-audits"
DEPLOY_REVIEW_CANDIDATE_DIR = Path(app.root_path) / "data" / "review-candidates"
MAX_RESEARCH_RUNS = 200
MAX_RESEARCH_ROWS = 50
MAX_LEARNING_REPORTS = 100
MAX_LEARNING_DECISIONS = 300

NODE_STRATEGIES = {
    "conservative_trend": "ConservativeTrend",
    "ConservativeTrend": "ConservativeTrend",
    "ConservativeTrendLoose": "ConservativeTrendLoose",
    "regime_filtered_trend": "RegimeFilteredTrendStrategy",
    "RegimeFilteredTrendStrategy": "RegimeFilteredTrendStrategy",
    "momentum_scalping": "MomentumScalping",
    "MomentumScalping": "MomentumScalping",
    "mean_reversion": "MeanReversion",
    "MeanReversion": "MeanReversion",
    "pullback_trend": "PullbackTrend",
    "PullbackTrend": "PullbackTrend",
    "SimpleAtrTrendV2": "SimpleAtrTrendV2",
    "PullbackReclaimV2": "PullbackReclaimV2",
    "original": "ConservativeTrend",
}

LEARNING_FORMULA_REVIEW_STRATEGIES = {
    "RegimeFilteredTrendStrategy",
    "regime_filtered_trend",
    "PullbackReclaimV2",
    "EmaBounceV2",
    "BreakoutRetestV2",
    "RangeExpansionV2",
    "RelativeStrengthV2",
}

LEARNING_OPTIMIZER_STRATEGY_OPTIONS = [
    {"id": "SimpleAtrTrendV2", "label": "Simple ATR Trend V2", "role": "Primary"},
    {"id": "ConservativeTrendLoose", "label": "Conservative Trend Loose", "role": "Baseline"},
    {"id": "MeanReversion", "label": "Mean Reversion", "role": "Baseline"},
    {"id": "MomentumScalping", "label": "Momentum Scalping", "role": "Baseline"},
    {"id": "PullbackTrend", "label": "Pullback Trend", "role": "Baseline"},
    {"id": "ConservativeTrend", "label": "Conservative Trend", "role": "Baseline"},
]


@app.get("/")
@app.get("/dashboard")
@app.get("/charts")
@app.get("/research")
@app.get("/research/paper-review")
@app.get("/candidate")
@app.get("/paper")
@app.get("/backtest")
@app.get("/analysis")
@app.get("/learning")
@app.get("/ops")
@app.get("/settings")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "app": "ZguaCharts"})


@app.get("/api/system/health")
def system_health():
    include_optimizer = request.args.get("optimizer", "").lower() in {"1", "true", "yes"}
    return jsonify(build_system_health(quick=False, include_optimizer=include_optimizer))


@app.get("/api/system/health/quick")
def system_health_quick():
    return jsonify(build_system_health(quick=True))


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
    payload["optimizer_strategy_presets"] = LEARNING_OPTIMIZER_STRATEGY_OPTIONS
    payload["default_strategy_preset"] = DEFAULT_PRESET_ID
    return jsonify(payload)


@app.post("/api/config/bootstrap-local")
def bootstrap_local_config():
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force")) or request.args.get("force", "false").lower() in {"1", "true", "yes", "on"}
    jobs = [
        ("paper-candidate", PAPER_CANDIDATE_DEFAULT_PATH, PAPER_CANDIDATE_LOCAL_PATH, {}),
        ("learning-runner", LEARNING_CONFIG_DEFAULT_PATH, LEARNING_CONFIG_LOCAL_PATH, default_learning_config()),
    ]
    results = []
    for name, default_path, local_path, fallback in jobs:
        try:
            if not default_path.exists():
                results.append({"name": name, "status": "failed", "reason": "default config missing", "path": str(default_path.relative_to(app.root_path))})
                continue
            if local_path.exists() and not force:
                results.append({"name": name, "status": "skipped", "reason": "local config already exists", "path": str(local_path.relative_to(app.root_path))})
                continue
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as handle:
                json.dump(read_json_file(str(default_path), fallback), handle, indent=2)
                handle.write("\n")
            results.append({"name": name, "status": "written" if force else "created", "path": str(local_path.relative_to(app.root_path))})
        except Exception as exc:
            results.append({"name": name, "status": "failed", "reason": str(exc), "path": str(local_path.relative_to(app.root_path))})
    return jsonify({"ok": not any(item["status"] == "failed" for item in results), "force": force, "results": results})


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
    stage = "argument parsing"
    context = {}
    try:
        args = parse_backtest_request_args(request.args)
        context = args
        stage = "node backtest execution"
        payload = run_shared_backtest_engine(
            args["source"],
            args["symbol"],
            args["timeframe"],
            args["period"],
            args["preset"],
            args["fee_pct"],
            args["slippage_pct"],
            args["limit"],
            args["debug"],
            args["allow_shorts"],
        )
        stage = "diagnostics normalization"
        payload = normalize_backtest_response(
            payload,
            args["source"],
            args["symbol"],
            args["timeframe"],
            args["period"],
            args["preset"],
            args["fee_pct"],
            args["slippage_pct"],
        )
        payload.setdefault("diagnostics", {})
        payload["diagnostics"]["requestedLimitRaw"] = args["limit_arg"]
        if int(safe_float(payload.get("number_of_trades", payload.get("trades", 0)))) == 0:
            readiness = research_data_readiness(args["source"], [args["symbol"]], [args["timeframe"]], args["period"], args["limit_arg"])
            payload["tradeGenerationDiagnostics"] = build_trade_generation_diagnostics(
                payload,
                readiness.get("rows", [{}])[0],
                args["allow_shorts"],
            )
            payload.setdefault("warnings", [])
            payload["warnings"] = dedupe_list(payload["warnings"] + [payload["tradeGenerationDiagnostics"]["summary"]["likelyReason"]])
        overlay_diag = overlay_diagnostics_from_payload(payload)
        payload["diagnostics"]["overlay_rendering"] = {
            **overlay_diag,
            "chartCandlesCount": args["chart_candles_count"] or overlay_diag.get("chartCandlesCount"),
            "backtestCandlesCount": overlay_diag.get("backtestCandlesCount"),
            "firstChartCandleTime": safe_int_arg(args["first_chart_candle_time"], overlay_diag.get("firstChartCandleTime")),
            "lastChartCandleTime": safe_int_arg(args["last_chart_candle_time"], overlay_diag.get("lastChartCandleTime")),
        }
        stage = "backtest history recording"
        record_backtest_history(payload)
    except ValueError as exc:
        return jsonify(backtest_error_payload(exc, stage, context)), 400
    except Exception as exc:
        return jsonify(backtest_error_payload(exc, stage, context)), 502

    return jsonify(payload)


@app.get("/api/backtest/strategy-metadata")
def backtest_strategy_metadata():
    try:
        candidate = load_paper_candidate_config()
        payload = load_backtest_strategy_metadata(candidate)
        payload["activeCandidate"] = candidate_summary(candidate)
        payload["paperEnabled"] = canonical_paper_enabled(candidate)
        payload["realTradingEnabled"] = False
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": f"Could not load strategy metadata: {exc}"}), 502


@app.post("/api/backtest/run-custom")
def backtest_run_custom():
    stage = "argument parsing"
    context = {}
    try:
        payload = request.get_json(silent=True) or {}
        source = str(payload.get("source") or "bybit")
        symbol = str(payload.get("symbol") or "ETHUSDT").upper()
        timeframe = str(payload.get("timeframe") or payload.get("interval") or "1h")
        period = str(payload.get("period") or "365d")
        strategy = str(payload.get("strategy") or payload.get("preset") or "SimpleAtrTrendV2")
        limit_arg = payload.get("limit", "auto")
        limit = research_limit_for(source, timeframe, period, limit_arg)
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        fee_pct = safe_float(payload.get("feePct", payload.get("fee_pct", params.get("feePct", 0))), 0)
        slippage_pct = safe_float(payload.get("slippagePct", payload.get("slippage_pct", params.get("slippagePct", 0))), 0)
        allow_shorts = bool(payload.get("allowShorts", params.get("shortMode", False)))
        context = {
            "source": source,
            "symbol": symbol,
            "timeframe": timeframe,
            "period": period,
            "preset": strategy,
            "limit_arg": limit_arg,
        }
        metadata = load_backtest_strategy_metadata(load_paper_candidate_config())
        supported = {item.get("name") for item in metadata.get("strategies", []) if item.get("supported")}
        if strategy not in supported:
            raise ValueError(f"Strategy is not available for manual backtesting: {strategy}")

        stage = "node custom backtest execution"
        result_payload = run_shared_backtest_engine(
            source,
            symbol,
            timeframe,
            period,
            strategy,
            fee_pct,
            slippage_pct,
            limit,
            debug=False,
            allow_shorts=allow_shorts,
            strategy_params=params,
        )
        stage = "custom response normalization"
        result_payload = normalize_backtest_response(result_payload, source, symbol, timeframe, period, strategy, fee_pct, slippage_pct)
        params_used = (result_payload.get("diagnostics") or {}).get("params") or params
        params_used = {**(params_used or {}), "feePct": fee_pct, "slippagePct": slippage_pct}
        active_candidate = load_paper_candidate_config()
        active_comparison = compare_manual_backtest_to_active_candidate(
            strategy,
            symbol,
            timeframe,
            params_used,
            active_candidate,
            str(payload.get("paramsSource") or payload.get("presetName") or payload.get("preset") or "custom"),
            source,
            fee_pct,
            slippage_pct,
        )
        run_context, context_warnings = manual_backtest_run_context(
            result_payload,
            period,
            limit_arg,
            source,
            strategy,
            symbol,
            timeframe,
            params_used,
            active_comparison.get("paramsSource") or "custom",
            active_candidate,
        )
        comparability = manual_backtest_comparability(run_context, active_comparison, context_warnings)
        metrics = manual_backtest_result_summary(result_payload, period)
        trades = result_payload.get("trade_list") or result_payload.get("tradeList") or []
        warnings = dedupe_list(list(result_payload.get("warnings") or []) + list((result_payload.get("diagnostics") or {}).get("warnings") or []))
        warnings = dedupe_list(warnings + context_warnings + active_comparison.get("warnings", []))
        return jsonify({
            "ok": True,
            "readOnly": True,
            "paperEnabled": canonical_paper_enabled(),
            "realTradingEnabled": False,
            "strategy": result_payload.get("strategy") or strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            "period": period,
            "paramsUsed": params_used,
            "runContext": run_context,
            "activeCandidateComparison": active_comparison,
            "comparability": comparability,
            "result": {
                **metrics,
                "warnings": warnings,
            },
            "diagnostics": result_payload.get("diagnostics") or {},
            "trades": trades[:75],
            "warnings": warnings,
        })
    except ValueError as exc:
        return jsonify(backtest_error_payload(exc, stage, context)), 400
    except Exception as exc:
        return jsonify(backtest_error_payload(exc, stage, context)), 502


@app.get("/api/backtest/debug")
def backtest_debug():
    stages = []
    context = {}
    try:
        args = parse_backtest_request_args(request.args, force_debug=True)
        context = args
        stages.append({"stage": "parsed args", "ok": True, "args": public_backtest_args(args)})
        candles_payload = fetch_historical_candles(args["source"], args["symbol"], args["timeframe"], period=args["period"], limit=args["limit"])
        candles = candles_payload.get("candles", [])
        stages.append({
            "stage": "candle fetch",
            "ok": True,
            "candleCount": len(candles),
            "firstCandleTime": candles[0].get("time") if candles else None,
            "lastCandleTime": candles[-1].get("time") if candles else None,
            "effectiveHistoricalLimit": candles_payload.get("limit"),
            "diagnostics": candles_payload.get("diagnostics", {}),
        })
        payload = run_shared_backtest_engine(
            args["source"],
            args["symbol"],
            args["timeframe"],
            args["period"],
            args["preset"],
            args["fee_pct"],
            args["slippage_pct"],
            args["limit"],
            True,
            args["allow_shorts"],
        )
        payload = normalize_backtest_response(payload, args["source"], args["symbol"], args["timeframe"], args["period"], args["preset"], args["fee_pct"], args["slippage_pct"])
        stages.append({
            "stage": "node command/final payload",
            "ok": True,
            "diagnosticsKeys": sorted((payload.get("diagnostics") or {}).keys()),
            "finalPayloadKeys": sorted(payload.keys()),
            "trades": payload.get("number_of_trades") or payload.get("trades"),
        })
        return jsonify({"ok": True, "stages": stages})
    except Exception as exc:
        stages.append({"stage": "error", "ok": False, "error": str(exc)})
        return jsonify({**backtest_error_payload(exc, "debug", context, include_traceback=True), "stages": stages}), 502


@app.get("/api/backtest/diagnose")
def backtest_diagnose():
    context = {}
    try:
        args = parse_backtest_request_args(request.args, force_debug=True)
        context = args
        return jsonify(run_backtest_diagnosis_payload(
            args["source"],
            args["symbol"],
            args["timeframe"],
            args["period"],
            args["preset"],
            args["fee_pct"],
            args["slippage_pct"],
            args["limit_arg"],
            args["allow_shorts"],
        ))
    except ValueError as exc:
        return jsonify(backtest_error_payload(exc, "diagnose argument parsing", context)), 400
    except Exception as exc:
        return jsonify(backtest_error_payload(exc, "diagnose backtest execution", context, include_traceback=True)), 502


def run_backtest_diagnosis_payload(source: str, symbol: str, timeframe: str, period: str, preset: str, fee_pct: float, slippage_pct: float, limit_arg, allow_shorts: bool) -> dict:
    limit = None if str(limit_arg).strip().lower() == "auto" else int(limit_arg)
    payload = run_shared_backtest_engine(source, symbol, timeframe, period, preset, fee_pct, slippage_pct, limit, True, allow_shorts)
    payload = normalize_backtest_response(payload, source, symbol, timeframe, period, preset, fee_pct, slippage_pct)
    readiness = research_data_readiness(source, [symbol], [timeframe], period, limit_arg)
    diagnostic = build_trade_generation_diagnostics(payload, readiness.get("rows", [{}])[0], allow_shorts)
    return {
        "ok": True,
        "summary": diagnostic["summary"],
        "dataReadiness": readiness,
        "historicalCoverage": payload.get("historicalCoverage") or {},
        "strategy": diagnostic["strategy"],
        "diagnostics": diagnostic["diagnostics"],
        "reasonCounters": diagnostic["reasonCounters"],
        "suggestedActions": diagnostic["suggestedActions"],
        "warnings": diagnostic["warnings"],
        "tradeGenerationDiagnostics": diagnostic,
    }


def parse_backtest_request_args(args, force_debug: bool = False) -> dict:
    limit_arg = args.get("limit", "5000")
    limit = None if str(limit_arg).strip().lower() == "auto" else int(limit_arg)
    return {
        "source": args.get("source", "bybit"),
        "symbol": args.get("symbol", "BTCUSDT"),
        "timeframe": args.get("timeframe", "15m"),
        "period": args.get("period", "60d"),
        "preset": args.get("preset", DEFAULT_PRESET_ID),
        "fee_pct": float(args.get("fee_pct", "0") or 0),
        "slippage_pct": float(args.get("slippage_pct", "0") or 0),
        "limit_arg": limit_arg,
        "limit": limit,
        "debug": force_debug or args.get("debug", "false").lower() == "true",
        "allow_shorts": args.get("allowShorts", "false").lower() == "true",
        "chart_candles_count": int(args.get("chart_candles_count", "0") or "0"),
        "first_chart_candle_time": args.get("first_chart_candle_time"),
        "last_chart_candle_time": args.get("last_chart_candle_time"),
    }


def research_limit_for(source: str, timeframe: str, period: str, limit_arg, require_number: bool = False):
    if str(limit_arg).strip().lower() == "auto":
        if require_number:
            return historical_limit_for_period(period, timeframe, None) if source == "bybit" else 5000
        return None
    return int(limit_arg)


def public_backtest_args(args: dict) -> dict:
    return {
        "source": args.get("source"),
        "symbol": args.get("symbol"),
        "timeframe": args.get("timeframe"),
        "period": args.get("period"),
        "preset": args.get("preset"),
        "feePct": args.get("fee_pct"),
        "slippagePct": args.get("slippage_pct"),
        "limitArg": args.get("limit_arg"),
        "parsedLimit": args.get("limit"),
        "allowShorts": args.get("allow_shorts"),
    }


def safe_int_arg(value, fallback=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def backtest_error_payload(exc: Exception, stage: str, context: dict | None = None, include_traceback: bool = False) -> dict:
    context = context or {}
    payload = {
        "error": f"Could not run backtest: {exc}",
        "stage": stage,
        "source": context.get("source"),
        "symbol": context.get("symbol"),
        "timeframe": context.get("timeframe"),
        "period": context.get("period"),
        "limitArg": context.get("limit_arg"),
    }
    debug_requested = bool(context.get("debug")) or request.args.get("debug", "false").lower() == "true"
    if include_traceback or debug_requested:
        payload["tracebackTail"] = "\n".join(traceback.format_exc().splitlines()[-8:])
    return payload


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


@app.get("/api/market/bybit/symbols")
def market_bybit_symbols():
    symbols_arg = request.args.get("symbols")
    symbols = parse_csv_arg(symbols_arg, DATA_SOURCE_CONFIG["sources"]["bybit"]["symbols"]) if symbols_arg else None
    return jsonify(bybit_symbol_validation_payload(symbols))


@app.get("/api/market/cache/status")
def market_cache_status():
    source = request.args.get("source", "bybit")
    if source != "bybit":
        return jsonify({"error": "Only Bybit cache inspection is supported for now."}), 400
    symbols = parse_csv_arg(request.args.get("symbols"), DATA_SOURCE_CONFIG["sources"]["bybit"]["symbols"])
    timeframes = parse_csv_arg(request.args.get("timeframes"), ["15m", "1h", "4h"])
    return jsonify(inspect_all_bybit_cache(symbols, timeframes))


@app.get("/api/research/data-readiness")
def research_data_readiness_endpoint():
    source = request.args.get("source", "bybit")
    symbols = parse_csv_arg(request.args.get("symbols"), ["BTCUSDT"])
    timeframes = parse_csv_arg(request.args.get("timeframes"), ["1h"])
    period = request.args.get("period", "365d")
    limit = request.args.get("limit", "auto")
    return jsonify(research_data_readiness(source, symbols, timeframes, period, limit))


@app.post("/api/market/cache/prefetch")
def market_cache_prefetch():
    payload = request.get_json(silent=True) or {}
    source = payload.get("source", "bybit")
    if source != "bybit":
        return jsonify({"error": "Only Bybit prefetch is supported for now."}), 400
    symbols = parse_csv_arg(payload.get("symbols"), ["BTCUSDT", "ETHUSDT"])
    timeframes = parse_csv_arg(payload.get("timeframes"), ["1h"])
    period = payload.get("period", "max")
    limit = min(int(payload.get("limit", BYBIT_MAX_CACHE_CANDLES)), BYBIT_MAX_CACHE_CANDLES)
    force = bool(payload.get("force", False))
    pair_count = len(symbols) * len(timeframes)
    if pair_count > 20 and not force:
        return jsonify({"error": f"Prefetch request has {pair_count} pairs; max is 20 unless force=true."}), 400

    validation = bybit_symbol_validation_payload(symbols)
    aliases = validation.get("suggestedAliases", {})
    validation_unavailable = bool(validation.get("warnings")) and not validation.get("validSymbols") and not validation.get("invalidSymbols")
    results = []
    for symbol in symbols:
        if validation_unavailable:
            validation_item = {"symbol": symbol, "valid": True, "alias": aliases.get(symbol), "message": "Bybit validation unavailable; attempting fetch directly."}
        else:
            try:
                validation_item = validate_bybit_symbol(symbol)
            except Exception as exc:
                validation_item = {"symbol": symbol, "valid": False, "alias": aliases.get(symbol), "message": f"Could not validate symbol: {exc}"}
        if not validation_item.get("valid") and not validation_item.get("alias"):
            for timeframe in timeframes:
                results.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "ERROR",
                    "error": validation_item.get("message", "Invalid Bybit symbol."),
                    "warnings": [],
                })
            continue
        fetch_symbol = validation_item.get("alias") or symbol
        for timeframe in timeframes:
            try:
                historical = fetch_historical_candles("bybit", fetch_symbol, timeframe, period=period, limit=limit)
                diagnostics = historical.get("diagnostics", {})
                results.append({
                    "symbol": symbol,
                    "resolvedSymbol": fetch_symbol,
                    "timeframe": timeframe,
                    "status": "OK",
                    "candles": len(historical.get("candles", [])),
                    "diagnostics": diagnostics,
                    "warnings": (["Bybit symbol validation was unavailable; fetched directly."] if validation_unavailable else []) + diagnostics.get("warnings", []),
                })
            except Exception as exc:
                results.append({
                    "symbol": symbol,
                    "resolvedSymbol": fetch_symbol,
                    "timeframe": timeframe,
                    "status": "ERROR",
                    "error": str(exc),
                    "warnings": [],
                })
    return jsonify({
        "source": "bybit",
        "period": period,
        "limit": limit,
        "validation": validation,
        "summary": {
            "pairsRequested": pair_count,
            "ok": sum(1 for item in results if item["status"] == "OK"),
            "errors": sum(1 for item in results if item["status"] == "ERROR"),
            "aliases": aliases,
        },
        "results": results,
    })


@app.get("/api/strategy-optimize")
def strategy_optimize():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "1h")
    strategy = request.args.get("strategy") or request.args.get("preset") or "RegimeFilteredTrendStrategy"
    period = request.args.get("period", "365d")
    limit_arg = request.args.get("limit", "auto")
    limit = research_limit_for(source, timeframe, period, limit_arg, require_number=True)
    max_combos = int(request.args.get("max_combos", "500"))
    train_ratio = float(request.args.get("train_ratio", "0.7"))
    fee_pct = float(request.args.get("fee_pct", "0"))
    slippage_pct = float(request.args.get("slippage_pct", "0"))
    save_run = request.args.get("save", "true").lower() not in {"0", "false", "no", "off"}
    readiness = research_data_readiness(source, [symbol], [timeframe], period, limit_arg)

    try:
        payload = run_strategy_optimization_payload(
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
        )
        payload.setdefault("requested", {})["limitRaw"] = limit_arg
        return jsonify(payload)
    except Exception as exc:
        zero_trade_diagnostics = None
        if "0 trades" in str(exc).lower() or "zero" in str(exc).lower():
            try:
                diagnostic_payload = run_backtest_diagnosis_payload(source, symbol, timeframe, period, strategy, fee_pct, slippage_pct, limit_arg, False)
                zero_trade_diagnostics = diagnostic_payload.get("tradeGenerationDiagnostics")
            except Exception as diag_exc:
                zero_trade_diagnostics = {"error": f"Could not collect zero-trade diagnostics: {diag_exc}"}
        return jsonify({
            "error": f"Could not run strategy optimizer: {exc}",
            "source": source,
            "symbol": symbol,
            "timeframe": timeframe,
            "period": period,
            "limit": limit,
            "limitRaw": limit_arg,
            "dataReadiness": readiness,
            "partialData": not readiness.get("summary", {}).get("allReady", False),
            "warnings": readiness_warnings(readiness),
            "zeroTradeDiagnostics": zero_trade_diagnostics,
        }), 502


@app.get("/api/strategy-optimizer/grids")
def strategy_optimizer_grids():
    try:
        return jsonify(load_optimizer_grid_catalog())
    except Exception as exc:
        return jsonify({"error": f"Could not load optimizer grids: {exc}", "grids": []}), 502


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
    current = candidate_summary(load_paper_candidate_config())
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


@app.get("/api/learning/audit")
def learning_audit():
    try:
        return jsonify(build_learning_quality_audit())
    except Exception as exc:
        return jsonify({"error": f"Could not build learning audit: {exc}"}), 502


@app.get("/api/learning/audit-summary")
def learning_audit_summary():
    try:
        return jsonify(build_learning_audit_summary())
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not build learning audit summary: {exc}"}), 502


@app.get("/api/learning/evidence")
def learning_evidence():
    try:
        include_stability = request.args.get("stability", "true").lower() not in {"0", "false", "no", "off"}
        return jsonify(build_learning_evidence(include_stability=include_stability))
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Could not build learning evidence: {exc}"}), 502


@app.get("/api/learning/auto-promote/status")
def learning_auto_promote_status():
    config = load_learning_config()
    latest = (load_learning_reports() or [None])[-1]
    audit = build_learning_quality_audit()
    result = evaluate_auto_promotion(config, audit, latest, load_paper_candidate_config())
    payload = {
        "autoPromote": bool(config.get("autoPromote", False)),
        "autoPromoteMode": config.get("autoPromoteMode", "candidate_only"),
        "autoEnablePaper": False,
        "rules": config.get("autoPromoteRules", {}),
        "latestAuditStatus": audit.get("status"),
        "allowed": result.get("allowed"),
        "reason": result.get("reason"),
        "candidate": result.get("candidate"),
        "checks": result.get("checks", []),
    }
    append_learning_decision_from_context(
        source="audit",
        action="PROMOTE_CANDIDATE" if result.get("allowed") else "REJECT_AUTO_PROMOTE",
        reason=result.get("reason", "Auto-promotion eligibility evaluated."),
        candidate=result.get("candidate"),
        audit=audit,
        checks=result.get("checks", []),
        report_id=(latest or {}).get("id"),
    )
    return jsonify(payload)


@app.post("/api/learning/auto-promote")
def learning_auto_promote():
    latest = (load_learning_reports() or [None])[-1]
    if not latest:
        result = {"promoted": False, "reason": "No learning reports exist.", "checks": []}
        append_learning_decision_from_context(
            source="manual_auto_promote",
            action="REJECT_AUTO_PROMOTE",
            reason=result["reason"],
            checks=[],
        )
        return jsonify(result), 400
    result = auto_promote_candidate_if_allowed(load_learning_config(), latest, decision_source="manual_auto_promote")
    if not result.get("promoted"):
        result["error"] = result.get("reason", "Auto-promotion rejected.")
    status_code = 200 if result.get("promoted") else 400
    return jsonify(result), status_code


@app.post("/api/learning/run")
def learning_run():
    config = load_learning_config()
    overrides = safe_learning_config_updates(request.get_json(silent=True) or {})
    config.update(overrides)
    config["autoEnablePaper"] = False
    report = run_learning_cycle(config)
    append_learning_report(report)
    append_learning_decision_for_report("learning_run", report)
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


@app.get("/api/learning/decisions")
def learning_decisions():
    limit = max(1, min(300, int(request.args.get("limit", "50"))))
    decisions = load_learning_decisions()
    return jsonify({
        "decisions": decisions[-limit:][::-1],
        "summary": summarize_learning_decisions(decisions),
    })


@app.get("/api/learning/decisions/<decision_id>")
def learning_decision(decision_id):
    decision = get_learning_decision_by_id(decision_id)
    if not decision:
        return jsonify({"error": f"Learning decision not found: {decision_id}"}), 404
    return jsonify(decision)


@app.get("/api/learning/decision-summary")
def learning_decision_summary():
    return jsonify(summarize_learning_decisions())


def health_check_item(checks: list[dict], check_id: str, label: str, status: str, message: str, details: dict | None = None) -> None:
    checks.append({
        "id": check_id,
        "label": label,
        "status": status if status in {"PASS", "WARN", "FAIL"} else "WARN",
        "message": message,
        "details": details or {},
    })


def generated_file_info(path: Path) -> dict:
    if not path.exists():
        return {"exists": False, "sizeBytes": 0, "sizeKb": 0, "updatedAt": None}
    stat = path.stat()
    return {
        "exists": True,
        "sizeBytes": stat.st_size,
        "sizeKb": round(stat.st_size / 1024, 2),
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def readable_json_file(path: Path) -> tuple[str, str, dict]:
    info = generated_file_info(path)
    if not path.exists():
        return "WARN", "File is missing; this is OK before first use.", info
    try:
        with open(path, "r", encoding="utf-8") as handle:
            json.load(handle)
        return "PASS", "JSON is readable.", info
    except Exception as exc:
        info["error"] = str(exc)
        return "FAIL", f"JSON is not readable: {exc}", info


def latest_record_age(records: list[dict], time_key: str = "createdAt") -> dict:
    if not records:
        return {"latestAt": None, "ageHours": None}
    latest = records[-1].get(time_key)
    parsed = parse_learning_time(latest)
    age = None
    if parsed:
        age = round((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600, 2)
    return {"latestAt": latest, "ageHours": age}


def synthetic_health_candles(count: int = 80) -> list[dict]:
    base = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = []
    price = 100.0
    for index in range(count):
        drift = 0.15 if index % 9 else -0.4
        open_price = price
        close = max(1, open_price + drift)
        high = max(open_price, close) + 0.6
        low = min(open_price, close) - 0.6
        candles.append({
            "time": base + index * 3600,
            "open": round(open_price, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(close, 4),
            "volume": 1000 + index,
        })
        price = close
    return candles


def run_node_backtest_smoke() -> dict:
    engine_input = {
        "source": "synthetic",
        "symbol": "HEALTH",
        "interval": "1h",
        "strategy": "AlwaysLongTest",
        "preset": "AlwaysLongTest",
        "limit": 80,
        "params": {},
        "candles": synthetic_health_candles(),
    }
    completed = subprocess.run(
        [node_executable(), "cli/backtest.js", "--stdin-json"],
        input=json.dumps(engine_input, allow_nan=False),
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Node backtest smoke failed")
    payload = json.loads(completed.stdout)
    return {
        "strategy": payload.get("strategy"),
        "trades": payload.get("trades") or payload.get("numberOfTrades") or len(payload.get("tradeList", [])),
        "equityPoints": len(payload.get("equityCurve", [])),
    }


def run_optimizer_smoke() -> dict:
    completed = subprocess.run(
        [node_executable(), "cli/optimize.js", "--symbol", "BTCUSDT", "--interval", "1h", "--days", "7", "--strategy", "AlwaysLongTest", "--limit", "120", "--max-combos", "1"],
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=45,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Optimizer smoke failed")
    return {"stdoutPreview": completed.stdout[:300]}


def build_system_health(quick: bool = False, include_optimizer: bool = False) -> dict:
    checks: list[dict] = []

    health_check_item(checks, "python", "Python runtime", "PASS", f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}", {
        "executable": sys.executable,
        "version": sys.version.split()[0],
    })
    health_check_item(checks, "flask", "Flask app", "PASS", "Flask application object is loaded.", {"appName": app.name})
    health_check_item(checks, "config_paths", "Config paths", "PASS", "Default/local config paths resolved.", {
        "appRoot": app.root_path,
        "configDir": str(CONFIG_DIR),
        "localConfigDir": str(LOCAL_CONFIG_DIR),
        "paperCandidateDefault": str(PAPER_CANDIDATE_DEFAULT_PATH.relative_to(app.root_path)),
        "paperCandidateLocal": str(PAPER_CANDIDATE_LOCAL_PATH.relative_to(app.root_path)),
        "learningDefault": str(LEARNING_CONFIG_DEFAULT_PATH.relative_to(app.root_path)),
        "learningLocal": str(LEARNING_CONFIG_LOCAL_PATH.relative_to(app.root_path)),
    })

    generated_files = {
        "researchRuns": generated_file_info(RESEARCH_RUNS_PATH),
        "learningReports": generated_file_info(LEARNING_REPORTS_PATH),
        "learningDecisions": generated_file_info(LEARNING_DECISIONS_PATH),
        "paperState": generated_file_info(Path(app.root_path) / "data" / "paper-state.json"),
    }

    for path, label, check_id, required in (
        (PAPER_CANDIDATE_DEFAULT_PATH, "Paper candidate default config", "paper_candidate_default_config", True),
        (LEARNING_CONFIG_DEFAULT_PATH, "Learning runner default config", "learning_default_config", True),
        (PAPER_CANDIDATE_LOCAL_PATH, "Paper candidate local runtime config", "paper_candidate_local_config", False),
        (LEARNING_CONFIG_LOCAL_PATH, "Learning runner local runtime config", "learning_local_config", False),
    ):
        status, message, details = readable_json_file(path)
        if required and not path.exists():
            status = "FAIL"
            message = "Required config file is missing."
        health_check_item(checks, check_id, label, status, message, details)

    paper_candidate = load_paper_candidate_config()
    candidate_ok = bool(paper_candidate.get("strategy") and paper_candidate.get("source") and paper_candidate.get("symbols"))
    health_check_item(
        checks,
        "paper_candidate_summary",
        "Paper candidate",
        "PASS" if candidate_ok else "WARN",
        "Paper candidate has strategy/source/symbols." if candidate_ok else "Paper candidate is incomplete.",
        candidate_summary(paper_candidate),
    )

    learning_config_data = safe_learning_config(load_learning_config())
    health_check_item(checks, "learning_runner_config", "Learning runner safety", "PASS", "Learning runner config parsed with safe defaults.", {
        "enabled": learning_config_data.get("enabled"),
        "schedule": learning_config_data.get("schedule"),
        "lastRunAt": learning_config_data.get("lastRunAt"),
        "nextRunAt": learning_config_data.get("nextRunAt"),
        "autoPromote": learning_config_data.get("autoPromote"),
        "autoEnablePaper": learning_config_data.get("autoEnablePaper"),
        "defaultConfig": generated_file_info(LEARNING_CONFIG_DEFAULT_PATH),
        "localConfig": generated_file_info(LEARNING_CONFIG_LOCAL_PATH),
        "localOverridesDefault": LEARNING_CONFIG_LOCAL_PATH.exists(),
    })
    health_check_item(checks, "runtime_config_split", "Runtime config split", "PASS", "Tracked defaults and ignored local runtime configs are separated.", {
        "paperDefault": PAPER_CANDIDATE_DEFAULT_PATH.name,
        "paperLocal": str(PAPER_CANDIDATE_LOCAL_PATH.relative_to(app.root_path)),
        "learningDefault": LEARNING_CONFIG_DEFAULT_PATH.name,
        "learningLocal": str(LEARNING_CONFIG_LOCAL_PATH.relative_to(app.root_path)),
    })
    auto_status = "WARN" if learning_config_data.get("autoPromote") else "PASS"
    health_check_item(checks, "auto_promotion", "Auto-promotion mode", auto_status, "Auto-promotion is candidate-only and paper auto-enable is blocked.", {
        "autoPromote": learning_config_data.get("autoPromote"),
        "autoPromoteMode": learning_config_data.get("autoPromoteMode"),
        "autoEnablePaper": False,
    })

    node_path = node_executable()
    try:
        completed = subprocess.run([node_path, "-v"], text=True, capture_output=True, timeout=5)
        version = (completed.stdout or completed.stderr).strip()
        major = 0
        try:
            major = int(version.lstrip("v").split(".", 1)[0])
        except Exception:
            pass
        status = "PASS" if completed.returncode == 0 and major >= 18 else "FAIL"
        message = f"Node runtime found: {version}" if status == "PASS" else f"Node 18+ is required; resolved version was {version or 'unknown'}."
        health_check_item(checks, "node", "Node runtime", status, message, {"path": node_path, "version": version})
    except Exception as exc:
        health_check_item(checks, "node", "Node runtime", "FAIL", f"Node runtime unavailable: {exc}", {"path": node_path})

    for script, check_id in (("cli/backtest.js", "cli_backtest"), ("cli/optimize.js", "cli_optimize")):
        path = Path(app.root_path) / script
        health_check_item(checks, check_id, script, "PASS" if path.exists() else "FAIL", "Script exists." if path.exists() else "Script is missing.", generated_file_info(path))

    for path, label, check_id in (
        (RESEARCH_RUNS_PATH, "Research runs memory", "research_runs"),
        (LEARNING_REPORTS_PATH, "Learning reports", "learning_reports"),
        (LEARNING_DECISIONS_PATH, "Learning decision log", "learning_decisions"),
        (Path(app.root_path) / "data" / "paper-state.json", "Paper state", "paper_state"),
    ):
        status, message, details = readable_json_file(path)
        health_check_item(checks, check_id, label, status, message, details)

    health_check_item(checks, "generated_data_sizes", "Generated data file sizes", "PASS", "Generated runtime data sizes collected.", generated_files)
    health_check_item(checks, "learning_report_age", "Latest learning report age", "PASS" if load_learning_reports() else "WARN", "Latest learning report age calculated." if load_learning_reports() else "No learning reports yet.", latest_record_age(load_learning_reports()))
    health_check_item(checks, "decision_age", "Latest decision age", "PASS" if load_learning_decisions() else "WARN", "Latest decision age calculated." if load_learning_decisions() else "No learning decisions yet.", latest_record_age(load_learning_decisions()))

    if not quick:
        try:
            candles_payload = fetch_candles("bybit", "BTCUSDT", "1h", limit=5, visible_charts=1)
            health_check_item(checks, "data_bybit", "Bybit data source", "PASS", "Fetched small BTCUSDT 1h candle sample.", {
                "candles": len(candles_payload.get("candles", [])),
                "diagnostics": candles_payload.get("diagnostics", {}),
            })
        except Exception as exc:
            health_check_item(checks, "data_bybit", "Bybit data source", "WARN", f"Bybit smoke fetch failed: {exc}", {})

        try:
            smoke = run_node_backtest_smoke()
            status = "PASS" if safe_float(smoke.get("trades")) > 0 else "WARN"
            health_check_item(checks, "node_backtest_smoke", "Node backtest smoke", status, "Synthetic AlwaysLongTest backtest completed.", smoke)
        except Exception as exc:
            health_check_item(checks, "node_backtest_smoke", "Node backtest smoke", "FAIL", f"Node backtest smoke failed: {exc}", {})

        if include_optimizer:
            try:
                health_check_item(checks, "optimizer_smoke", "Optimizer smoke", "PASS", "Optimizer smoke completed.", run_optimizer_smoke())
            except Exception as exc:
                health_check_item(checks, "optimizer_smoke", "Optimizer smoke", "WARN", f"Optimizer smoke is non-blocking and failed: {exc}", {})
        else:
            health_check_item(checks, "optimizer_smoke", "Optimizer smoke", "WARN", "Skipped by default; call /api/system/health?optimizer=1 to run the optional optimizer smoke.", {})

    summary = {
        "pass": sum(1 for check in checks if check["status"] == "PASS"),
        "warn": sum(1 for check in checks if check["status"] == "WARN"),
        "fail": sum(1 for check in checks if check["status"] == "FAIL"),
    }
    return {
        "ok": summary["fail"] == 0,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "summary": summary,
    }


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
    limit_arg = request.args.get("limit", "auto")
    limit = research_limit_for(source, timeframes[0] if timeframes else "1h", period, limit_arg)
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
            limit_arg,
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
                "limitRaw": limit_arg,
                "minTrades": min_trades,
                "maxRuns": max_runs,
            },
        }), 400


@app.get("/api/research/activity-lab")
def research_activity_lab():
    try:
        payload, status_code = build_research_activity_lab(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research activity lab: {exc}"}), 502


@app.get("/api/research/parameter-robustness")
def research_parameter_robustness():
    try:
        payload, status_code = build_research_parameter_robustness(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build parameter robustness lab: {exc}"}), 502


@app.get("/api/research/blocker-analytics")
def research_blocker_analytics():
    try:
        payload, status_code = build_research_blocker_analytics(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build strategy blocker analytics: {exc}"}), 502


@app.get("/api/research/strategy-variant-lab")
def research_strategy_variant_lab():
    try:
        payload, status_code = build_research_strategy_variant_lab(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build strategy variant lab: {exc}"}), 502


@app.get("/api/research/candidate-review-report")
def research_candidate_review_report():
    try:
        payload, status_code = build_research_candidate_review_report(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build candidate review report: {exc}"}), 502


@app.get("/api/research/evidence-scorecard")
def research_evidence_scorecard():
    try:
        payload, status_code = build_research_evidence_scorecard(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research evidence scorecard: {exc}"}), 502


@app.get("/api/research/candidate-leaderboard")
def research_candidate_leaderboard():
    try:
        payload, status_code = build_research_candidate_leaderboard(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research candidate leaderboard: {exc}"}), 502


@app.get("/api/research/fee-slippage-stress")
def research_fee_slippage_stress():
    try:
        payload, status_code = build_research_fee_slippage_stress(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build fee/slippage stress lab: {exc}"}), 502


@app.get("/api/research/walk-forward-review")
def research_walk_forward_review():
    try:
        payload, status_code = build_research_walk_forward_review(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build walk-forward review: {exc}"}), 502


@app.get("/api/research/regime-breakdown")
def research_regime_breakdown():
    try:
        payload, status_code = build_research_regime_breakdown(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build regime breakdown lab: {exc}"}), 502


@app.get("/api/research/regime-filter-counterfactual")
def research_regime_filter_counterfactual():
    try:
        payload, status_code = build_research_regime_filter_counterfactual(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build regime filter counterfactual lab: {exc}"}), 502


@app.get("/api/research/stability-first-challenger-search")
def research_stability_first_challenger_search():
    try:
        payload, status_code = build_research_stability_first_challenger_search(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build stability-first challenger search: {exc}"}), 502


@app.get("/api/research/campaign-runner")
def research_campaign_runner():
    try:
        payload, status_code = build_research_campaign_runner(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research campaign runner: {exc}"}), 502


@app.get("/api/research/candidate-evidence-ledger")
def research_candidate_evidence_ledger():
    try:
        payload, status_code = build_research_candidate_evidence_ledger(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build candidate evidence ledger: {exc}"}), 502


@app.get("/api/research/result-diff")
def research_result_diff():
    try:
        payload, status_code = build_research_result_diff(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research result diff: {exc}"}), 502


@app.get("/api/research/promotion-checklist-v2")
def research_promotion_checklist_v2():
    try:
        payload, status_code = build_research_promotion_checklist_v2(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build promotion checklist v2: {exc}"}), 502


@app.get("/api/research/autopilot/status")
def research_autopilot_status():
    try:
        return jsonify(build_research_autopilot_status())
    except Exception as exc:
        return jsonify({"error": f"Could not build research autopilot status: {exc}"}), 502


@app.post("/api/research/autopilot/plan")
def research_autopilot_plan():
    try:
        args = {**request.args.to_dict(), **(request.get_json(silent=True) or {})}
        payload, status_code = build_research_autopilot_plan(args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not plan research autopilot jobs: {exc}"}), 502


@app.post("/api/research/autopilot/run-next")
def research_autopilot_run_next():
    try:
        payload, status_code = build_research_autopilot_run_next()
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not run next research autopilot job: {exc}"}), 502


@app.post("/api/research/autopilot/run-batch")
def research_autopilot_run_batch():
    try:
        args = {**request.args.to_dict(), **(request.get_json(silent=True) or {})}
        payload, status_code = build_research_autopilot_run_batch(args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not run research autopilot batch: {exc}"}), 502


@app.post("/api/research/autopilot/reset-queue")
def research_autopilot_reset_queue():
    try:
        args = {**request.args.to_dict(), **(request.get_json(silent=True) or {})}
        payload, status_code = build_research_autopilot_reset_queue(args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not reset research autopilot queue: {exc}"}), 502


@app.post("/api/research/autopilot/backfill-memory")
def research_autopilot_backfill_memory():
    try:
        args = {**request.args.to_dict(), **(request.get_json(silent=True) or {})}
        payload, status_code = build_research_autopilot_backfill_memory(args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not backfill research autopilot memory: {exc}"}), 502


@app.post("/api/research/autopilot/candidate-dossier")
def research_autopilot_candidate_dossier():
    try:
        args = {**request.args.to_dict(), **(request.get_json(silent=True) or {})}
        payload, status_code = build_research_autopilot_candidate_dossier(args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research autopilot candidate dossier: {exc}"}), 502


@app.post("/api/research/autopilot/prepare-paper-candidate")
def research_autopilot_prepare_paper_candidate():
    try:
        args = {**request.args.to_dict(), **(request.get_json(silent=True) or {})}
        payload, status_code = build_research_autopilot_prepare_paper_candidate(args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not prepare disabled paper candidate package: {exc}"}), 502


@app.get("/api/research/paper-candidates")
def research_paper_candidates():
    try:
        return jsonify(build_research_paper_candidates())
    except Exception as exc:
        return jsonify({"error": f"Could not list disabled paper candidates: {exc}"}), 502


@app.get("/api/research/autopilot/summary")
def research_autopilot_summary():
    try:
        return jsonify(build_research_autopilot_summary())
    except Exception as exc:
        return jsonify({"error": f"Could not build research autopilot summary: {exc}"}), 502


@app.get("/api/research/autopilot/journal")
def research_autopilot_journal():
    try:
        return jsonify(build_research_autopilot_journal())
    except Exception as exc:
        return jsonify({"error": f"Could not build research autopilot journal: {exc}"}), 502


@app.get("/api/research/signal-replay-report")
def research_signal_replay_report():
    try:
        payload, status_code = build_research_signal_replay_report(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build signal replay report: {exc}"}), 502


@app.get("/api/research/data-cost-consistency-audit")
def research_data_cost_consistency_audit():
    try:
        payload, status_code = build_research_data_cost_consistency_audit(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build data/cost consistency audit: {exc}"}), 502


@app.get("/api/research/timeframe-preset-search")
def research_timeframe_preset_search():
    try:
        payload, status_code = build_research_timeframe_preset_search(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build timeframe preset search lab: {exc}"}), 502


@app.get("/api/research/candidate-deep-compare")
def research_candidate_deep_compare():
    try:
        payload, status_code = build_research_candidate_deep_compare(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build candidate deep compare: {exc}"}), 502


@app.get("/api/research/multi-strategy-matrix")
def research_multi_strategy_matrix():
    try:
        payload, status_code = build_research_multi_strategy_matrix(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build multi-strategy matrix: {exc}"}), 502


@app.get("/api/research/multi-strategy-optimizer-batch")
def research_multi_strategy_optimizer_batch():
    try:
        payload, status_code = build_research_multi_strategy_optimizer_batch(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build multi-strategy optimizer batch: {exc}"}), 502


@app.get("/api/research/optimizer-reproducibility-audit")
def research_optimizer_reproducibility_audit():
    try:
        payload, status_code = build_research_optimizer_reproducibility_audit(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build optimizer reproducibility audit: {exc}"}), 502


@app.get("/api/research/reproducible-candidate-drilldown")
def research_reproducible_candidate_drilldown():
    try:
        payload, status_code = build_research_reproducible_candidate_drilldown(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build reproducible candidate drilldown: {exc}"}), 502


@app.get("/api/research/lead-review")
def research_lead_review():
    try:
        payload, status_code = build_research_lead_review(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research lead review: {exc}"}), 502


@app.get("/api/research/snapshot-export")
def research_snapshot_export():
    try:
        payload, status_code = build_research_snapshot_export(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build research snapshot export: {exc}"}), 502


@app.get("/api/paper/status")
def paper_status():
    try:
        state_path = os.path.join(app.root_path, "data", "paper-state.json")
        journal_path = os.path.join(app.root_path, "reports", "paper-journal.jsonl")
        state = read_json_file(state_path, {})
        candidate = load_paper_candidate_config()
        paper_enabled = canonical_paper_enabled(candidate)
        events = read_jsonl_tail(journal_path, 30)
        health_payload = build_candidate_health(candidate_health_rules(request.args))
        readiness = build_paper_readiness_report(request.args)
        runtime = build_paper_runtime_status(request.args)
        stop_rules = build_paper_stop_rules(request.args)
        session_summary = build_paper_session_summary(request.args)
        observation_quality = build_paper_observation_quality(request.args)
        observation_targets = build_paper_observation_targets(request.args)
        runner_summary = build_paper_runner_summary(request.args)
        session_events_summary = build_paper_session_events_summary(request.args)
        session_events_detail = build_paper_session_events(request.args)
        session_trades_detail = build_paper_session_trades(request.args)
        active_observation = build_paper_active_observation(request.args)
        tick_readiness = build_paper_tick_readiness(request.args)
        observation_report = build_paper_observation_report(request.args)
        observation_counters = build_paper_observation_counters({**dict(request.args), "activeOnly": "true"})
        compact_counters = compact_observation_counters(observation_counters)
        real_enabled, _ = paper_real_trading_enabled()
        runtime_journal = runtime.get("journal", {}) or {}
        blocking_warnings = runtime_journal.get("blockingWarnings", []) or []
        active_warnings = runtime_journal.get("activeWarnings", []) or []
        watch_warnings = runtime_journal.get("watchWarnings", []) or []
        stale_watch_warnings = runtime_journal.get("staleWatchWarnings", []) or []
        informational_warnings = runtime_journal.get("informationalWarnings", []) or []
        return jsonify({
            "ok": True,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
            "initializationNeeded": runtime.get("initializationStatus") == "NEEDS_INIT",
            "openPositions": state.get("openPositions", []),
            "closedTrades": state.get("closedTrades", [])[-50:],
            "equity": state.get("accountEquity"),
            "realizedPnL": state.get("realizedPnl", 0),
            "unrealizedPnL": state.get("unrealizedPnl", 0),
            "totalFees": state.get("cumulativeFees", 0),
            "totalSlippage": state.get("cumulativeSlippage", 0),
            "lastSignals": events,
            "lastProcessedCandle": state.get("lastProcessedCandleTime", {}),
            "warnings": blocking_warnings,
            "informationalWarnings": informational_warnings,
            "candidate": candidate_summary(candidate),
            "health": health_payload.get("health", {}),
            "readiness": readiness,
            "runtimeStatus": {
                "initialized": runtime.get("initialized"),
                "initializationStatus": runtime.get("initializationStatus"),
                "health": runtime.get("health"),
                "lastTick": runtime.get("lastTick"),
                "nextAction": runtime.get("nextAction"),
            },
            "stopRules": {
                "status": stop_rules.get("status"),
                "failed": len([rule for rule in stop_rules.get("rules", []) if not rule.get("pass")]),
                "nextAction": stop_rules.get("nextAction"),
            },
            "sessionSummary": {
                "status": session_summary.get("session", {}).get("status"),
                "ticks": session_summary.get("activity", {}).get("ticks"),
                "signals": session_summary.get("activity", {}).get("signals"),
                "openPositions": session_summary.get("activity", {}).get("openPositions"),
                "closedTrades": session_summary.get("activity", {}).get("closedTrades"),
                "returnPct": session_summary.get("performance", {}).get("returnPct"),
                "baselineComparisonStatus": session_summary.get("baselineComparison", {}).get("status"),
                "nextAction": session_summary.get("nextAction"),
            },
            "observationQuality": {
                "status": observation_quality.get("quality", {}).get("status"),
                "score": observation_quality.get("quality", {}).get("score"),
                "nextAction": observation_quality.get("nextAction"),
                "evidence": observation_quality.get("evidence"),
            },
            "observationTargets": compact_observation_targets(observation_targets),
            "observationReport": {
                "status": observation_report.get("verdict", {}).get("status"),
                "title": observation_report.get("verdict", {}).get("title"),
                "nextAction": observation_report.get("verdict", {}).get("nextAction"),
            },
            "observationCounters": compact_counters,
            "runnerTicksRun": compact_counters.get("runnerTicksRun"),
            "runnerTicksSkipped": compact_counters.get("runnerTicksSkipped"),
            "processedCandleDeltaTotal": compact_counters.get("processedCandleDeltaTotal"),
            "counterConsistencyStatus": compact_counters.get("counterConsistencyStatus"),
            "runnerSummary": compact_runner_summary(runner_summary),
            "sessionEventsSummary": compact_session_events_summary(session_events_summary),
            "recentSignalCount": session_events_detail.get("counts", {}).get("signals"),
            "recentTradeEventCount": session_trades_detail.get("totals", {}).get("recentTradeEvents"),
            "currentSessionTradeCount": session_trades_detail.get("totals", {}).get("currentSessionTradeEvents"),
            "activeObservation": compact_active_observation(active_observation),
            "activeSessionEventCount": active_observation.get("session", {}).get("activeSessionEventCount"),
            "activeTradeEventCount": active_observation.get("trades", {}).get("tradeEventCount"),
            "activeSignalCount": active_observation.get("signals", {}).get("count"),
            "activeWarningCount": active_observation.get("warnings", {}).get("count"),
            "tickReadiness": {
                "status": tick_readiness.get("tickReadiness", {}).get("status"),
                "usefulNow": tick_readiness.get("tickReadiness", {}).get("usefulNow"),
                "nextUsefulTickAt": tick_readiness.get("tickReadiness", {}).get("nextUsefulTickAt"),
                "secondsUntilNextUsefulTick": tick_readiness.get("tickReadiness", {}).get("secondsUntilNextUsefulTick"),
                "activeMarketReason": tick_readiness.get("tickReadiness", {}).get("activeMarketReason"),
                "activeWarningCount": len(tick_readiness.get("tickReadiness", {}).get("activeWarnings") or []),
                "watchWarningCount": len(tick_readiness.get("tickReadiness", {}).get("watchWarnings") or []),
                "staleWatchWarningCount": len(tick_readiness.get("tickReadiness", {}).get("staleWatchWarnings") or []),
            },
            "staleWarnings": runtime_journal.get("staleWarnings", []),
            "recentWarnings": runtime_journal.get("recentWarnings", []),
            "activeWarnings": active_warnings,
            "watchWarnings": watch_warnings,
            "staleWatchWarnings": stale_watch_warnings,
            "blockingWarnings": blocking_warnings,
            "activeWarningCount": len(active_warnings),
            "watchWarningCount": len(watch_warnings),
            "staleWatchWarningCount": len(stale_watch_warnings),
            "lastUpdated": candidate.get("enabledAt") or candidate.get("disabledAt") or state.get("updatedAt") or candidate.get("promotedAt"),
            "equityCurve": state.get("equityCurve", [])[-500:],
        })
    except Exception as exc:
        return jsonify({"error": f"Could not load paper status: {exc}"}), 502


@app.get("/api/paper/readiness")
def paper_readiness():
    try:
        return jsonify(build_paper_readiness_report(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper readiness report: {exc}"}), 502


@app.get("/api/paper/runtime-status")
def paper_runtime_status():
    try:
        return jsonify(build_paper_runtime_status(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper runtime status: {exc}"}), 502


@app.get("/api/paper/session-summary")
def paper_session_summary():
    try:
        return jsonify(build_paper_session_summary(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper session summary: {exc}"}), 502


@app.get("/api/paper/observation-quality")
def paper_observation_quality():
    try:
        return jsonify(build_paper_observation_quality(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper observation quality: {exc}"}), 502


@app.get("/api/paper/active-observation")
def paper_active_observation():
    try:
        return jsonify(build_paper_active_observation(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build active paper observation: {exc}"}), 502


@app.get("/api/paper/active-signal-diagnostics")
def paper_active_signal_diagnostics():
    try:
        payload, status_code = build_paper_active_signal_diagnostics(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build active signal diagnostics: {exc}"}), 502


@app.get("/api/paper/forward-signal-diagnostics")
def paper_forward_signal_diagnostics():
    try:
        payload, status_code = build_paper_forward_signal_diagnostics(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not build forward signal diagnostics: {exc}"}), 502


@app.get("/api/paper/candidate-comparison")
def paper_candidate_comparison():
    try:
        return jsonify(build_paper_candidate_comparison(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper candidate comparison: {exc}"}), 502


@app.get("/api/paper/discover-fast-candidate")
def paper_discover_fast_candidate():
    try:
        payload, status_code = build_paper_fast_candidate_discovery(request.args)
        return jsonify(payload), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not discover fast paper candidate: {exc}"}), 502


@app.get("/api/paper/observation-report")
def paper_observation_report():
    try:
        return jsonify(build_paper_observation_report(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper observation report: {exc}"}), 502


@app.get("/api/paper/observation-targets")
def paper_observation_targets():
    try:
        return jsonify(build_paper_observation_targets(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper observation targets: {exc}"}), 502


@app.get("/api/paper/observation-counters")
def paper_observation_counters():
    try:
        return jsonify(build_paper_observation_counters(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper observation counters: {exc}"}), 502


@app.get("/api/paper/runner-summary")
def paper_runner_summary():
    try:
        return jsonify(build_paper_runner_summary(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper runner summary: {exc}"}), 502


@app.get("/api/paper/run-quality-report")
def paper_run_quality_report():
    try:
        return jsonify(build_paper_run_quality_report(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper run quality report: {exc}"}), 502


@app.get("/api/paper/session-events-summary")
def paper_session_events_summary():
    try:
        return jsonify(build_paper_session_events_summary(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper session events summary: {exc}"}), 502


@app.get("/api/paper/session-events")
def paper_session_events():
    try:
        return jsonify(build_paper_session_events(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper session events: {exc}"}), 502


@app.get("/api/paper/session-trades")
def paper_session_trades():
    try:
        return jsonify(build_paper_session_trades(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper session trades: {exc}"}), 502


@app.get("/api/paper/recent-events")
def paper_recent_events():
    try:
        return jsonify(build_paper_recent_events(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not load paper recent events: {exc}"}), 502


@app.get("/api/paper/init-instructions")
def paper_init_instructions():
    return jsonify({
        "ok": True,
        "commands": ["npm run paper:init"],
        "notes": [
            "Run this before enabling paper simulation if runtime status says NEEDS_INIT.",
            "This initializes paper simulation state only and does not enable real trading.",
        ],
    })


@app.get("/api/paper/tick-instructions")
def paper_tick_instructions():
    try:
        candidate = load_paper_candidate_config()
        paper_enabled = canonical_paper_enabled(candidate)
        real_enabled, _ = paper_real_trading_enabled()
        readiness = build_paper_tick_readiness(request.args)
        useful_now = readiness.get("tickReadiness", {}).get("usefulNow")
        recommended = package_script_command("paper:tick") if useful_now else None
        return jsonify({
            "ok": True,
            "commands": [package_script_command("paper:tick")],
            "recommendedCommand": recommended,
            "recommendedApiAction": "POST /api/paper/tick-once" if useful_now else None,
            "tickReadiness": readiness.get("tickReadiness"),
            "nextAction": readiness.get("nextAction"),
            "notes": [
                "Run this to process one paper tick only when tickReadiness.usefulNow is true.",
                "This is simulated only and cannot place real trades.",
            ],
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        })
    except Exception as exc:
        return jsonify({"error": f"Could not build paper tick instructions: {exc}"}), 502


@app.get("/api/paper/runner-instructions")
def paper_runner_instructions():
    try:
        candidate = load_paper_candidate_config()
        paper_enabled = canonical_paper_enabled(candidate)
        real_enabled, _ = paper_real_trading_enabled()
        observation_targets = build_paper_observation_targets(request.args)
        return jsonify({
            "ok": True,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "oneShotCommand": package_script_command("paper:run-once"),
            "guidedCommand": "python scripts/paper_run_once.py --guided --log-file reports/paper-runner-guided-session.jsonl",
            "loopCommand": "python scripts/paper_run_once.py --loop --interval-minutes 5 --max-iterations 12 --log-file reports/paper-runner-session.jsonl",
            "guidedLoopCommand": "python scripts/paper_run_once.py --guided --loop --interval-minutes 5 --max-iterations 12 --log-file reports/paper-runner-guided-session.jsonl",
            "notes": [
                "This is local paper simulation only and cannot place real trades.",
                "Guided mode runs read-only preflight checks and skips before POST /api/paper/run-once when paper is disabled, real trading is enabled, or stop rules recommend pause.",
                "The loop runs only when started manually; no daemon or scheduled task is created.",
                "Press Ctrl+C to stop a manually started loop; the runner prints a final interrupted summary.",
                "Ctrl+C does not disable paper automatically; run POST /api/paper/disable manually when finished.",
                "Generated runner JSONL logs are local runtime files and should not be committed.",
            ],
            "observationTargets": compact_observation_targets(observation_targets),
            "nextAction": observation_targets.get("nextAction"),
            "warnings": [
                "This endpoint only returns instructions. It does not start the runner, enable paper, or enable real trading.",
            ],
        })
    except Exception as exc:
        return jsonify({"error": f"Could not build paper runner instructions: {exc}"}), 502


@app.get("/api/paper/tick-readiness")
def paper_tick_readiness():
    try:
        return jsonify(build_paper_tick_readiness(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper tick readiness: {exc}"}), 502


@app.post("/api/paper/refresh-active-market")
def paper_refresh_active_market():
    try:
        result, status_code = refresh_active_paper_market(request.args, request.get_json(silent=True) or {})
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not refresh active paper market: {exc}"}), 502


@app.post("/api/paper/run-once")
def paper_run_once():
    try:
        result, status_code = run_paper_once_controlled(request.args, request.get_json(silent=True) or {})
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not run paper once: {exc}"}), 502


@app.post("/api/paper/tick-once")
def paper_tick_once():
    try:
        result, status_code = run_paper_tick_once(request.args)
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not run paper tick: {exc}"}), 502


@app.get("/api/paper/stop-rules")
def paper_stop_rules():
    try:
        return jsonify(build_paper_stop_rules(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper stop rules: {exc}"}), 502


@app.post("/api/paper/enable-preview")
def paper_enable_preview():
    try:
        preview = build_paper_enable_preview(request.args)
        if not preview.get("ok"):
            return jsonify(preview), 400
        return jsonify(preview)
    except Exception as exc:
        return jsonify({"error": f"Could not preview paper enablement: {exc}"}), 502


@app.post("/api/paper/enable")
def paper_enable():
    try:
        result, status_code = enable_paper_simulation_controlled(request.args)
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not enable paper simulation: {exc}"}), 502


@app.post("/api/paper/disable")
def paper_disable():
    try:
        result = disable_paper_simulation_controlled(request.args)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": f"Could not disable paper simulation: {exc}"}), 502


@app.get("/api/candidate/current")
def current_candidate():
    try:
        candidate = load_paper_candidate_config()
        paper_enabled = canonical_paper_enabled(candidate)
        payload = candidate_summary(candidate)
        payload["paperEnabled"] = paper_enabled
        payload["consistencyWarnings"] = paper_enabled_consistency_warnings(candidate, paper_enabled)
        return jsonify(payload)
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
        candidate = load_paper_candidate_config()
        validation = validate_candidate_config(candidate, candidate_validation_rules(request.args))
        return jsonify({
            "candidate": candidate_summary(candidate),
            "validation": validation,
            "configWarnings": candidate_config_warnings(candidate),
        })
    except Exception as exc:
        return jsonify({"error": f"Could not validate candidate: {exc}"}), 502


@app.get("/api/candidate/review")
def candidate_review():
    try:
        return jsonify(build_candidate_review())
    except Exception as exc:
        return jsonify({"error": f"Could not build candidate review: {exc}"}), 502


@app.get("/api/candidate/stability")
def candidate_stability():
    try:
        return jsonify(build_candidate_stability_report(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not validate candidate stability: {exc}"}), 502


@app.post("/api/candidate/promote-preview")
def promote_candidate_preview():
    try:
        payload = request.get_json(silent=True) or {}
        force = bool(payload.get("force")) or request.args.get("force", "false").lower() in {"1", "true", "yes", "on"}
        preview = build_candidate_promotion_plan(payload, dry_run=True, force=force)
        if not preview.get("ok"):
            return jsonify(preview), 400
        return jsonify(public_promotion_preview(preview))
    except Exception as exc:
        return jsonify({"error": f"Could not preview candidate promotion: {exc}"}), 502


@app.post("/api/candidate/enable-paper")
def enable_paper_candidate():
    try:
        result, status_code = enable_paper_simulation_controlled(request.args)
        return jsonify(result), status_code
    except Exception as exc:
        return jsonify({"error": f"Could not enable paper simulation: {exc}"}), 502


@app.post("/api/candidate/disable-paper")
def disable_paper_candidate():
    try:
        return jsonify(disable_paper_simulation_controlled(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not disable paper simulation: {exc}"}), 502


@app.post("/api/candidate/promote")
def promote_candidate():
    try:
        payload = request.get_json(force=True) or {}
        dry_run = request.args.get("dryRun", "false").lower() in {"1", "true", "yes", "on"}
        force = bool(payload.get("force")) or request.args.get("force", "false").lower() in {"1", "true", "yes", "on"}
        preview = build_candidate_promotion_plan(payload, dry_run=dry_run, force=force)
        if not preview.get("ok"):
            return jsonify(preview), 400
        if dry_run:
            return jsonify(public_promotion_preview(preview))

        current = preview["currentPaperCandidateRaw"]
        backup_path = backup_candidate_config(current)
        updated = preview["candidateConfigPreview"]
        write_candidate_config(updated)

        return jsonify({
            "ok": True,
            "promoted": True,
            "message": "Candidate promoted. Paper simulation remains disabled until explicitly enabled.",
            "backupPath": str(backup_path.relative_to(app.root_path)),
            "writtenPath": str(PAPER_CANDIDATE_LOCAL_PATH.relative_to(app.root_path)),
            "paperRemainsDisabled": not bool(updated.get("enabled")),
            "candidateConfig": updated,
            "candidate": candidate_summary(updated),
            "changedFields": preview.get("changedFields", []),
            "expectedBaselineMetrics": preview.get("expectedBaselineMetrics", {}),
            "configWarnings": candidate_config_warnings(updated),
            "warnings": preview.get("warnings", []),
        })
    except Exception as exc:
        return jsonify({"error": f"Could not promote candidate: {exc}"}), 502


def read_json_file(path: str, fallback):
    if not os.path.exists(path):
        return fallback
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def shallow_merge_config(defaults: dict, local: dict) -> dict:
    merged = dict(defaults)
    for key, value in (local or {}).items():
        if key == "params" and local.get("_replaceParams") is True:
            merged[key] = value
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def load_config_pair(default_path: Path, local_path: Path, fallback: dict | None = None) -> dict:
    defaults = read_json_file(str(default_path), fallback or {})
    if local_path.exists():
        return shallow_merge_config(defaults, read_json_file(str(local_path), {}))
    return defaults


def merged_config(default_path: Path, local_path: Path, fallback: dict | None = None) -> dict:
    return load_config_pair(default_path, local_path, fallback)


def ensure_local_config_from_default(default_path: Path, local_path: Path, fallback: dict | None = None) -> bool:
    if local_path.exists():
        return False
    data = read_json_file(str(default_path), fallback or {})
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    return True


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


def load_paper_candidate_config() -> dict:
    return normalize_promoted_candidate_config(load_config_pair(
        PAPER_CANDIDATE_DEFAULT_PATH,
        PAPER_CANDIDATE_LOCAL_PATH,
        {},
    ))


def canonical_paper_enabled(candidate: dict | None = None) -> bool:
    config = normalize_promoted_candidate_config(candidate if candidate is not None else load_paper_candidate_config())
    value = config.get("enabled", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def paper_enabled_consistency_warnings(candidate: dict, paper_enabled: bool | None = None) -> list[str]:
    config = normalize_promoted_candidate_config(candidate or {})
    canonical = canonical_paper_enabled(config) if paper_enabled is None else bool(paper_enabled)
    warnings = []
    raw_enabled = config.get("enabled", False)
    raw_bool = raw_enabled if isinstance(raw_enabled, bool) else canonical_paper_enabled(config)
    if not isinstance(raw_enabled, bool):
        warnings.append("candidate.enabled is not a boolean; parsed canonical paperEnabled is used.")
    if bool(raw_bool) != canonical:
        warnings.append("paperEnabled does not match normalized candidate.enabled; normalized candidate.enabled is canonical.")
    return warnings


def run_strategy_ranking_payload(
    source: str,
    symbols: list[str],
    timeframes: list[str],
    presets: list[str],
    period: str,
    limit,
    limit_raw,
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
    readiness = research_data_readiness(source, symbols, timeframes, period, limit_raw)
    readiness_by_pair = {(row["symbol"], row["timeframe"]): row for row in readiness.get("rows", [])}
    # TODO: Move this synchronous matrix run to a background job/cache when the
    # requested symbol/timeframe/preset matrix becomes too slow for one request.
    for symbol in symbols:
        for timeframe in timeframes:
            for preset in presets:
                readiness_row = readiness_by_pair.get((symbol, timeframe), {})
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
                    zero_trade_diagnostics = None
                    if readiness_row.get("status") != "READY":
                        warnings.append(f"Data readiness is {readiness_row.get('status', 'UNKNOWN')}: {readiness_row.get('recommendedAction', '')}")
                    if metrics["trades"] == 0:
                        zero_trade_diagnostics = build_trade_generation_diagnostics(payload, readiness_row, allow_shorts)
                        warnings.append("zero-trade result")
                        warnings.append(zero_trade_diagnostics["summary"]["likelyReason"])
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
                        "dataReadiness": readiness_row,
                        "partialData": readiness_row.get("status") != "READY",
                        "tradeGenerationDiagnostics": zero_trade_diagnostics,
                        "effectiveLimit": (payload.get("historicalCoverage") or payload.get("historical_coverage") or {}).get("effective_limit"),
                        "returnedCoverageDays": (payload.get("historicalCoverage") or payload.get("historical_coverage") or {}).get("approximate_days_returned"),
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
                        "dataReadiness": readiness_row,
                        "partialData": True,
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
            "limitRaw": limit_raw,
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
            "partialDataRows": len([row for row in rows if row.get("partialData")]),
        },
        "dataReadiness": readiness,
        "warnings": readiness_warnings(readiness),
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
    readiness = research_data_readiness(source, [symbol], [timeframe], period, limit)
    raw = run_strategy_optimizer_engine(source, symbol, timeframe, period, strategy, limit, max_combos, train_ratio, fee_pct, slippage_pct)
    payload = normalize_optimizer_payload(raw, source, symbol, timeframe, strategy, period, limit, max_combos, train_ratio, fee_pct, slippage_pct)
    payload["dataReadiness"] = readiness
    payload["partialData"] = not readiness.get("summary", {}).get("allReady", False)
    payload.setdefault("warnings", [])
    payload["warnings"].extend(readiness_warnings(readiness))
    if save_run:
        payload["researchRunId"] = append_research_run(research_record_from_optimization(payload))
    return payload


def readiness_warnings(readiness: dict) -> list[str]:
    warnings = []
    for row in readiness.get("rows", []):
        if row.get("status") != "READY":
            warnings.append(f"{row.get('symbol')} {row.get('timeframe')} data readiness {row.get('status')}: {row.get('recommendedAction')}")
    return warnings


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
        "optimizationStrategies": [
            "SimpleAtrTrendV2",
            "ConservativeTrendLoose",
            "MeanReversion",
            "MomentumScalping",
            "PullbackTrend",
            "ConservativeTrend",
        ],
        "period": "365d",
        "rankingLimit": "auto",
        "optimizationLimit": "auto",
        "maxRankingRuns": 20,
        "maxOptimizationCombos": 300,
        "minTrades": 20,
        "feePct": 0.055,
        "slippagePct": 0.02,
        "allowShorts": False,
        "autoPromote": False,
        "autoPromoteMode": "candidate_only",
        "autoPromoteRules": {
            "requireAuditStatus": "READY_FOR_AUTO_PROMOTE_LATER",
            "minLearningReports": 3,
            "minRepeatedRecommendations": 2,
            "maxRecommendationChurn": 0.6,
            "minRobustnessScore": 60,
            "minTrades": 30,
            "minProfitFactor": 1.15,
            "maxDrawdown": 20,
            "rejectIfPaperHealthFailed": True,
            "rejectIfSevereOverfitWarning": True,
            "requireCandidateBetterThanCurrentBy": 5,
        },
        "autoEnablePaper": False,
    }


def load_learning_config() -> dict:
    config = load_config_pair(
        LEARNING_CONFIG_DEFAULT_PATH,
        LEARNING_CONFIG_LOCAL_PATH,
        default_learning_config(),
    )
    config["autoEnablePaper"] = False
    return config


def write_learning_config(config: dict) -> None:
    ensure_local_config_from_default(
        LEARNING_CONFIG_DEFAULT_PATH,
        LEARNING_CONFIG_LOCAL_PATH,
        default_learning_config(),
    )
    LEARNING_CONFIG_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEARNING_CONFIG_LOCAL_PATH, "w", encoding="utf-8") as handle:
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
    base["autoPromote"] = bool(base.get("autoPromote", False))
    base["autoPromoteMode"] = "candidate_only"
    base["autoPromoteRules"] = safe_auto_promote_rules(base.get("autoPromoteRules", {}))
    base["autoEnablePaper"] = False
    for key in ("rankingLimit", "optimizationLimit"):
        value = base.get(key, default_learning_config()[key])
        base[key] = "auto" if str(value).strip().lower() == "auto" else int(safe_float(value, 5000))
    for key in ("maxRankingRuns", "maxOptimizationCombos", "minTrades"):
        base[key] = int(safe_float(base.get(key), default_learning_config()[key]))
    for key in ("feePct", "slippagePct"):
        base[key] = safe_float(base.get(key), default_learning_config()[key])
    return base


def safe_auto_promote_rules(rules: dict) -> dict:
    defaults = default_learning_config()["autoPromoteRules"]
    if not isinstance(rules, dict):
        rules = {}
    merged = {**defaults, **rules}
    return {
        "requireAuditStatus": str(merged.get("requireAuditStatus") or defaults["requireAuditStatus"]),
        "minLearningReports": int(safe_float(merged.get("minLearningReports"), defaults["minLearningReports"])),
        "minRepeatedRecommendations": int(safe_float(merged.get("minRepeatedRecommendations"), defaults["minRepeatedRecommendations"])),
        "maxRecommendationChurn": safe_float(merged.get("maxRecommendationChurn"), defaults["maxRecommendationChurn"]),
        "minRobustnessScore": safe_float(merged.get("minRobustnessScore"), defaults["minRobustnessScore"]),
        "minTrades": int(safe_float(merged.get("minTrades"), defaults["minTrades"])),
        "minProfitFactor": safe_float(merged.get("minProfitFactor"), defaults["minProfitFactor"]),
        "maxDrawdown": safe_float(merged.get("maxDrawdown"), defaults["maxDrawdown"]),
        "rejectIfPaperHealthFailed": bool(merged.get("rejectIfPaperHealthFailed", defaults["rejectIfPaperHealthFailed"])),
        "rejectIfSevereOverfitWarning": bool(merged.get("rejectIfSevereOverfitWarning", defaults["rejectIfSevereOverfitWarning"])),
        "requireCandidateBetterThanCurrentBy": safe_float(merged.get("requireCandidateBetterThanCurrentBy"), defaults["requireCandidateBetterThanCurrentBy"]),
    }


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
        "autoPromote",
        "autoPromoteMode",
        "autoPromoteRules",
    }
    updates = {key: value for key, value in payload.items() if key in allowed}
    if "autoPromote" in payload or "autoEnablePaper" in payload:
        updates["autoPromote"] = bool(payload.get("autoPromote", False))
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


def load_learning_decisions() -> list[dict]:
    if not LEARNING_DECISIONS_PATH.exists():
        return []
    try:
        with open(LEARNING_DECISIONS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return data
        return data.get("decisions", []) if isinstance(data, dict) else []
    except Exception:
        return []


def save_learning_decisions(decisions: list[dict]) -> None:
    LEARNING_DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    capped = decisions[-MAX_LEARNING_DECISIONS:]
    with open(LEARNING_DECISIONS_PATH, "w", encoding="utf-8") as handle:
        json.dump(capped, handle, indent=2)
        handle.write("\n")


def append_learning_decision(record: dict) -> str:
    decision = dict(record)
    decision.setdefault("id", f"decision-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}")
    decision.setdefault("createdAt", datetime.now(timezone.utc).isoformat())
    decision.setdefault("promoted", False)
    decision.setdefault("paperEnabledAfter", canonical_paper_enabled())
    decision.setdefault("errors", [])
    decision.setdefault("warnings", [])
    decisions = load_learning_decisions()
    decisions.append(decision)
    save_learning_decisions(decisions)
    return decision["id"]


def get_learning_decision_by_id(decision_id: str) -> dict | None:
    return next((decision for decision in load_learning_decisions() if decision.get("id") == decision_id), None)


def summarize_learning_decisions(decisions: list[dict] | None = None) -> dict:
    items = decisions if decisions is not None else load_learning_decisions()
    latest = items[-1] if items else None
    auto_promotions = [item for item in items if item.get("promoted") or item.get("action") == "AUTO_PROMOTED"]
    rejected = [item for item in items if item.get("action") == "REJECT_AUTO_PROMOTE"]
    reason_counts = Counter((item.get("reason") or "Unknown").strip() for item in rejected)
    candidate_keys = [learning_recommendation_key(item.get("candidate")) for item in items if item.get("candidate")]
    candidate_keys = [key for key in candidate_keys if key]
    candidate_counts = Counter(candidate_keys)
    latest_promoted = next((item.get("candidate") for item in reversed(auto_promotions) if item.get("candidate")), None)
    return {
        "totalDecisions": len(items),
        "autoPromotions": len(auto_promotions),
        "rejectedAutoPromotions": len(rejected),
        "mostCommonRejectReasons": [
            {"reason": reason, "count": count}
            for reason, count in reason_counts.most_common(5)
        ],
        "latestAction": latest.get("action") if latest else None,
        "latestDecisionAt": latest.get("createdAt") if latest else None,
        "latestPromotedCandidate": latest_promoted,
        "candidateChurn": {
            "observations": len(candidate_keys),
            "uniqueCandidates": len(candidate_counts),
            "topCandidateKey": candidate_counts.most_common(1)[0][0] if candidate_counts else None,
            "topCandidateCount": candidate_counts.most_common(1)[0][1] if candidate_counts else 0,
        },
    }


def compact_learning_candidate(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    source = candidate.get("source") or candidate.get("dataSource") or "bybit"
    return {
        "source": source,
        "strategy": candidate.get("strategy") or candidate.get("preset"),
        "preset": candidate.get("preset") or candidate.get("strategy"),
        "symbol": candidate.get("symbol"),
        "timeframe": candidate.get("timeframe") or candidate.get("interval"),
        "period": candidate.get("period"),
        "score": candidate.get("score"),
        "trades": candidate.get("trades") or candidate.get("numberOfTrades"),
        "profitFactor": candidate.get("profitFactor"),
        "maxDrawdown": candidate.get("maxDrawdown"),
        "totalReturnPct": candidate.get("totalReturnPct") or candidate.get("totalReturn"),
        "winRate": candidate.get("winRate"),
        "params": candidate.get("params", {}),
        "origin": candidate.get("origin"),
    }


def append_learning_decision_from_context(
    source: str,
    action: str,
    reason: str,
    candidate: dict | None = None,
    audit: dict | None = None,
    checks: list[dict] | None = None,
    report_id: str | None = None,
    promoted: bool = False,
    errors: list | None = None,
    warnings: list | None = None,
) -> str:
    current = load_paper_candidate_config()
    audit_summary = (audit or {}).get("summary", {})
    record = {
        "source": source,
        "action": action,
        "candidate": compact_learning_candidate(candidate),
        "currentCandidate": candidate_summary(current),
        "auditStatus": (audit or {}).get("status"),
        "robustnessScore": audit_summary.get("robustnessScore"),
        "checks": checks or [],
        "reason": reason,
        "reportId": report_id,
        "promoted": bool(promoted),
        "paperEnabledAfter": canonical_paper_enabled(current),
        "errors": errors or [],
        "warnings": warnings or [],
    }
    return append_learning_decision(record)


def append_learning_decision_for_report(source: str, report: dict) -> str:
    rec = report.get("recommendation") or {}
    action = rec.get("action") or ("ERROR" if report.get("status") == "failed" else "KEEP_CURRENT")
    if report.get("status") == "failed":
        action = "ERROR"
    audit = build_learning_quality_audit()
    return append_learning_decision_from_context(
        source=source,
        action=action,
        reason=rec.get("reason") or f"Learning report {report.get('status', 'completed')}.",
        candidate=rec.get("candidate") or report.get("bestSavedCandidate"),
        audit=audit,
        checks=(report.get("autoPromotion") or {}).get("checks", []),
        report_id=report.get("id"),
        promoted=bool((report.get("autoPromotion") or {}).get("promoted")),
        errors=report.get("errors", []),
        warnings=report.get("warnings", []),
    )


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
        result = {"ran": False, "reason": reason, "report": None, "lastRunAt": config.get("lastRunAt"), "nextRunAt": next_run}
        append_learning_decision_from_context(
            source="scheduled_tick",
            action="WAIT_FOR_MORE_DATA",
            reason=reason,
            warnings=["Learning tick skipped because no cycle was due."],
        )
        return result
    if not acquire_learning_lock():
        config = load_learning_config()
        reason = "Learning cycle already running."
        append_learning_decision_from_context(
            source="scheduled_tick",
            action="WAIT_FOR_MORE_DATA",
            reason=reason,
            warnings=["Learning lock was already active."],
        )
        return {"ran": False, "reason": reason, "report": None, "lastRunAt": config.get("lastRunAt"), "nextRunAt": config.get("nextRunAt")}
    try:
        config = load_learning_config()
        report = run_learning_cycle(config)
        append_learning_report(report)
        config = load_learning_config()
        config["lastRunAt"] = datetime.now().astimezone().isoformat()
        config["nextRunAt"] = compute_next_learning_run(config, datetime.now().astimezone()).isoformat()
        config["lock"] = {"running": False, "startedAt": None}
        config["autoEnablePaper"] = False
        save_learning_config(config)
        append_learning_decision_for_report("scheduled_tick", report)
        return {"ran": True, "reason": reason, "report": report, "lastRunAt": config.get("lastRunAt"), "nextRunAt": config.get("nextRunAt")}
    except Exception as exc:
        config = load_learning_config()
        config["lock"] = {"running": False, "startedAt": None}
        save_learning_config(config)
        reason = f"Learning cycle failed: {exc}"
        append_learning_decision_from_context(
            source="scheduled_tick",
            action="ERROR",
            reason=reason,
            errors=[str(exc)],
        )
        return {"ran": False, "reason": reason, "report": None, "lastRunAt": config.get("lastRunAt"), "nextRunAt": config.get("nextRunAt")}


def build_learning_quality_audit(window: int = 5, extra_report: dict | None = None) -> dict:
    reports = load_learning_reports()
    if extra_report:
        reports = reports + [extra_report]
    research_runs = load_research_runs()
    current_candidate = load_paper_candidate_config()
    paper_health = build_candidate_health(candidate_health_rules({}))["health"]
    recent = reports[-window:]
    latest = reports[-1] if reports else None
    previous = reports[-2] if len(reports) >= 2 else None
    stability = learning_candidate_stability(recent)
    trend = learning_score_trend(recent)
    best_candidate = best_saved_candidate(research_runs)
    robustness_score = learning_robustness_score(stability, trend, paper_health, best_candidate, len(reports))
    warnings = learning_audit_warnings(reports, stability, trend, paper_health, best_candidate)
    latest_readiness = (latest or {}).get("dataReadiness")
    if latest_readiness and not latest_readiness.get("summary", {}).get("allReady", False):
        warnings.append("Latest learning report used partial/stale/capped research data.")
    status, recommendation = learning_audit_status(robustness_score, warnings, stability, paper_health, best_candidate, len(reports))
    return {
        "status": status,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "learningReports": len(reports),
            "researchRuns": len(research_runs),
            "window": window,
            "robustnessScore": robustness_score,
            "currentCandidate": candidate_summary(current_candidate),
            "bestSavedCandidate": best_candidate,
        },
        "latestRecommendation": (latest or {}).get("recommendation"),
        "previousRecommendation": (previous or {}).get("recommendation"),
        "candidateStability": stability,
        "scoreTrend": trend,
        "paperHealth": paper_health,
        "dataReadiness": latest_readiness,
        "trustRules": learning_trust_rules(),
        "warnings": warnings,
        "recommendation": recommendation,
    }


def build_learning_audit_summary() -> dict:
    reports = load_learning_reports()
    research_runs = load_research_runs()
    current_candidate = load_paper_candidate_config()
    candidate_health_payload = build_candidate_health(candidate_health_rules({}))
    candidate_health = candidate_health_payload.get("health", {})
    latest_learning = reports[-1] if reports else None
    latest_ranking = latest_research_run_by_type(research_runs, "ranking")
    latest_optimization = latest_research_run_by_type(research_runs, "optimization")
    latest_recommendation_candidate = latest_learning_recommendation_candidate(latest_learning)
    best_candidate = best_saved_candidate(research_runs)
    optimizer_quality = optimizer_quality_from_run(latest_optimization)
    grid_audit = (latest_optimization or {}).get("gridAudit") or {}
    zero_trade = zero_trade_summary_from_run(latest_optimization)
    readiness = learning_audit_readiness(reports, best_candidate, current_candidate, candidate_health, optimizer_quality)
    comparison = candidate_comparison(latest_recommendation_candidate, current_candidate)
    evidence = build_learning_evidence(include_stability=False, reports=reports, latest_report=latest_learning, audit_status=None)
    next_action = learning_audit_next_action(latest_learning, optimizer_quality, zero_trade, readiness, candidate_health, best_candidate, latest_recommendation_candidate, evidence)
    warnings = learning_audit_summary_warnings(latest_learning, latest_optimization, optimizer_quality, zero_trade, readiness, best_candidate, latest_recommendation_candidate)
    warnings = dedupe_list(warnings + (evidence.get("warnings") or []))
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "latestLearningReport": compact_learning_report(latest_learning),
        "latestRankingRun": compact_research_run(latest_ranking),
        "latestOptimizationRun": compact_research_run(latest_optimization),
        "latestOptimizerRun": compact_latest_optimizer_run(latest_optimization, optimizer_quality),
        "latestLearningRecommendationCandidate": compact_candidate(latest_recommendation_candidate),
        "latestLearningRecommendationUsableForManualInspection": candidate_passes_quality(latest_recommendation_candidate),
        "bestSavedCandidate": compact_candidate(best_candidate),
        "currentPaperCandidate": candidate_summary(current_candidate),
        "candidateComparison": comparison,
        "evidenceSummary": evidence.get("repeatability"),
        "candidateHealth": candidate_health,
        "optimizerQuality": optimizer_quality,
        "gridAudit": grid_audit,
        "zeroTrade": zero_trade,
        "readiness": readiness,
        "nextAction": next_action,
        "warnings": warnings,
    }


def latest_research_run_by_type(runs: list[dict], run_type: str) -> dict | None:
    return next((run for run in reversed(runs) if run.get("type") == run_type), None)


def latest_learning_report() -> dict | None:
    reports = load_learning_reports()
    return reports[-1] if reports else None


def compact_learning_report(report: dict | None) -> dict | None:
    if not report:
        return None
    return {
        "id": report.get("id"),
        "createdAt": report.get("createdAt"),
        "status": report.get("status"),
        "rankingRunIds": report.get("rankingRunIds", []),
        "optimizationRunIds": report.get("optimizationRunIds", []),
        "recommendation": report.get("recommendation"),
        "errors": report.get("errors", []),
        "warningsCount": len(report.get("warnings") or []),
    }


def compact_research_run(run: dict | None) -> dict | None:
    if not run:
        return None
    return {
        "id": run.get("id"),
        "createdAt": run.get("createdAt"),
        "type": run.get("type"),
        "status": run.get("status"),
        "source": run.get("source"),
        "symbols": run.get("symbols", []),
        "timeframes": run.get("timeframes", []),
        "strategies": run.get("strategies", []),
        "period": run.get("period"),
        "summary": run.get("summary", {}),
        "bestCandidate": compact_candidate(run.get("bestCandidate")),
        "optimizerGrid": run.get("optimizerGrid"),
        "gridAudit": run.get("gridAudit"),
        "zeroTradeSummary": run.get("zeroTradeSummary"),
        "allZeroTradeCandidates": run.get("allZeroTradeCandidates"),
        "errors": run.get("errors", []),
    }


def compact_latest_optimizer_run(run: dict | None, optimizer_quality: dict) -> dict | None:
    compact = compact_research_run(run)
    if not compact:
        return None
    compact["latestSelectedStatus"] = optimizer_quality.get("latestSelectedStatus")
    compact["passCandidates"] = optimizer_quality.get("passCandidates")
    compact["warnCandidates"] = optimizer_quality.get("warnCandidates")
    compact["failCandidates"] = optimizer_quality.get("failCandidates")
    compact["totalCandidates"] = optimizer_quality.get("totalCandidates")
    compact["topRejectionReasons"] = optimizer_quality.get("topRejectionReasons", [])
    return compact


def compact_candidate(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    return {
        "source": candidate.get("source"),
        "symbol": candidate.get("symbol"),
        "timeframe": candidate.get("timeframe"),
        "strategy": candidate.get("strategy") or candidate.get("preset"),
        "preset": candidate.get("preset"),
        "period": candidate.get("period"),
        "params": candidate.get("params", {}),
        "valid": candidate.get("valid"),
        "qualityStatus": candidate.get("qualityStatus"),
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "totalReturnPct": candidate.get("totalReturnPct"),
        "winRate": candidate.get("winRate"),
        "maxDrawdown": candidate.get("maxDrawdown"),
        "profitFactor": candidate.get("profitFactor"),
        "trades": candidate.get("trades"),
        "train": candidate.get("train", {}),
        "test": candidate.get("test", {}),
        "full": candidate.get("full", {}),
        "qualityMetrics": candidate.get("qualityMetrics", {}),
        "origin": candidate.get("origin"),
        "warnings": candidate.get("warnings", []),
        "rejectionReasons": candidate.get("rejectionReasons", []),
    }


def latest_learning_recommendation_candidate(report: dict | None) -> dict | None:
    if not report:
        return None
    recommendation = report.get("recommendation") or {}
    candidate = recommendation.get("candidate") or report.get("bestSavedCandidate")
    return candidate if isinstance(candidate, dict) else None


def candidate_passes_quality(candidate: dict | None) -> bool:
    if not candidate:
        return False
    quality_status = candidate.get("qualityStatus")
    if not quality_status and candidate.get("origin") == "ranking" and candidate.get("valid"):
        quality_status = "PASS"
    if quality_status != "PASS":
        return False
    return bool(candidate.get("valid", True)) and not candidate_has_robustness_blockers(candidate)


def candidate_primary_symbol(candidate: dict | None) -> str | None:
    if not candidate:
        return None
    if candidate.get("symbol"):
        return str(candidate.get("symbol"))
    active = candidate.get("activeSymbols") or []
    if active and isinstance(active[0], dict):
        return str(active[0].get("symbol") or "") or None
    symbols = candidate.get("symbols") or []
    if symbols and isinstance(symbols[0], dict):
        return str(symbols[0].get("symbol") or "") or None
    return None


def candidate_primary_timeframe(candidate: dict | None) -> str | None:
    if not candidate:
        return None
    if candidate.get("timeframe"):
        return str(candidate.get("timeframe"))
    if candidate.get("interval"):
        return str(candidate.get("interval"))
    active = candidate.get("activeSymbols") or []
    if active and isinstance(active[0], dict):
        return str(active[0].get("interval") or active[0].get("timeframe") or "") or None
    symbols = candidate.get("symbols") or []
    if symbols and isinstance(symbols[0], dict):
        return str(symbols[0].get("interval") or symbols[0].get("timeframe") or "") or None
    return None


def candidate_comparison(recommended: dict | None, current: dict | None) -> dict:
    current_summary = candidate_summary(current or {})
    recommended_strategy = (recommended or {}).get("strategy") or (recommended or {}).get("preset")
    current_strategy = current_summary.get("strategy")
    recommended_symbol = candidate_primary_symbol(recommended)
    current_symbol = candidate_primary_symbol(current_summary)
    recommended_timeframe = candidate_primary_timeframe(recommended)
    current_timeframe = candidate_primary_timeframe(current_summary)
    same_as_current = bool(
        recommended_strategy and
        recommended_strategy == current_strategy and
        recommended_symbol == current_symbol and
        recommended_timeframe == current_timeframe
    )
    recommended_score = safe_float((recommended or {}).get("score"), None)
    current_score = current_candidate_score(current_summary)
    better = None
    if recommended_score is not None and current_score is not None:
        better = recommended_score > current_score
    notes = []
    if not recommended:
        notes.append("No latest learning recommendation candidate is available.")
    elif candidate_has_robustness_blockers(recommended):
        notes.append("Latest recommendation candidate has robustness blockers.")
    elif candidate_passes_quality(recommended):
        notes.append("Latest learning recommendation candidate passed optimizer quality checks.")
    if same_as_current:
        notes.append("Latest recommendation matches the current paper candidate family and primary market.")
    elif recommended and current_strategy:
        notes.append("Latest recommendation differs from the current paper candidate.")
    return {
        "recommendedStrategy": recommended_strategy,
        "recommendedSymbol": recommended_symbol,
        "recommendedTimeframe": recommended_timeframe,
        "currentPaperStrategy": current_strategy,
        "currentPaperSymbol": current_symbol,
        "currentPaperTimeframe": current_timeframe,
        "sameAsCurrentPaper": same_as_current,
        "recommendedBetterThanCurrent": better,
        "notes": notes,
    }


def candidate_review_comparison(recommended: dict | None, current: dict | None, preview_config: dict | None = None) -> dict:
    current_summary = candidate_summary(current or {})
    base = candidate_comparison(recommended, current or {})
    same_strategy = bool(base.get("recommendedStrategy") and base.get("recommendedStrategy") == base.get("currentPaperStrategy"))
    same_symbol = bool(base.get("recommendedSymbol") and base.get("recommendedSymbol") == base.get("currentPaperSymbol"))
    same_timeframe = bool(base.get("recommendedTimeframe") and base.get("recommendedTimeframe") == base.get("currentPaperTimeframe"))
    param_diffs = dict_diffs((current or {}).get("params") or {}, (recommended or {}).get("params") or {})
    risk_diffs = risk_diffs_for_candidate(current or {}, preview_config or {})
    expected_diffs = expected_metric_diffs(expected_metrics_from_candidate(current or {}), expected_metrics_from_candidate(preview_config or {}))
    summary_bits = []
    if not recommended:
        summary_bits.append("No recommended candidate is available.")
    elif same_strategy and same_symbol and same_timeframe and not param_diffs:
        summary_bits.append("Recommended candidate matches the current paper candidate configuration.")
    else:
        changes = []
        if not same_strategy:
            changes.append("strategy")
        if not same_symbol:
            changes.append("symbol")
        if not same_timeframe:
            changes.append("timeframe")
        if param_diffs:
            changes.append("parameters")
        summary_bits.append(f"Config-only promotion would update {', '.join(changes) or 'candidate metadata'}.")
    if current_summary.get("enabled"):
        summary_bits.append("Current paper simulation is enabled; review carefully before replacing the config.")
    else:
        summary_bits.append("Paper is disabled and would remain disabled.")
    return {
        "sameStrategy": same_strategy,
        "sameSymbol": same_symbol,
        "sameTimeframe": same_timeframe,
        "paramDiffs": param_diffs,
        "riskDiffs": risk_diffs,
        "expectedMetricDiffs": expected_diffs,
        "summary": " ".join(summary_bits),
        **base,
    }


def dict_diffs(before: dict, after: dict) -> list[dict]:
    keys = sorted(set((before or {}).keys()) | set((after or {}).keys()))
    return [
        {"field": key, "current": (before or {}).get(key), "recommended": (after or {}).get(key)}
        for key in keys
        if (before or {}).get(key) != (after or {}).get(key)
    ]


def risk_diffs_for_candidate(current: dict, preview: dict) -> list[dict]:
    fields = ["fillModel", "makerFeePct", "takerFeePct", "slippageBps", "accountEquity", "riskPct", "maxOpenTrades", "maxNotionalPerTrade"]
    return [
        {"field": field, "current": current.get(field), "preview": preview.get(field)}
        for field in fields
        if current.get(field) != preview.get(field)
    ]


def expected_metric_diffs(current_expected: dict, preview_expected: dict) -> list[dict]:
    fields = ["totalReturnPct", "winRate", "maxDrawdown", "profitFactor", "trades"]
    return [
        {"field": field, "current": current_expected.get(field), "preview": preview_expected.get(field)}
        for field in fields
        if current_expected.get(field) != preview_expected.get(field)
    ]


def promotion_payload_from_candidate(candidate: dict | None) -> dict | None:
    if not candidate:
        return None
    ranking_snapshot = {
        "valid": candidate.get("valid", True),
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "period": candidate.get("period"),
        "totalReturnPct": candidate.get("totalReturnPct"),
        "winRate": candidate.get("winRate"),
        "maxDrawdown": candidate.get("maxDrawdown"),
        "profitFactor": candidate.get("profitFactor"),
        "trades": candidate.get("trades"),
        "qualityStatus": candidate.get("qualityStatus"),
        "qualityMetrics": candidate.get("qualityMetrics", {}),
    }
    optimization_snapshot = None
    ranking_origin_snapshot = None
    if candidate.get("origin") == "optimization":
        optimization_snapshot = {
            "researchRunId": candidate.get("researchRunId"),
            "score": candidate.get("score"),
            "train": candidate.get("train"),
            "test": candidate.get("test"),
            "full": candidate.get("full"),
            "qualityStatus": candidate.get("qualityStatus"),
            "qualityMetrics": candidate.get("qualityMetrics", {}),
            "warnings": candidate.get("warnings"),
            "rejectionReasons": candidate.get("rejectionReasons", []),
        }
    else:
        ranking_origin_snapshot = {
            "researchRunId": candidate.get("researchRunId"),
            "score": candidate.get("score"),
            "warnings": candidate.get("warnings"),
        }
    return {
        "source": candidate.get("source") or "bybit",
        "symbol": candidate.get("symbol"),
        "timeframe": candidate.get("timeframe"),
        "preset": candidate.get("preset") or candidate.get("strategy"),
        "strategy": candidate.get("strategy"),
        "period": candidate.get("period"),
        "params": candidate.get("params", {}),
        "qualityStatus": candidate.get("qualityStatus"),
        "rankingSnapshot": ranking_snapshot,
        "optimizationSnapshot": optimization_snapshot,
        "rankingOriginSnapshot": ranking_origin_snapshot,
    }


def candidate_matches_promotion_selectors(candidate: dict | None, selectors: dict) -> bool:
    if not candidate:
        return False
    expected_symbol = str(selectors.get("symbol") or "").strip()
    expected_timeframe = str(selectors.get("timeframe") or selectors.get("interval") or "").strip()
    expected_strategy = str(selectors.get("strategy") or selectors.get("preset") or "").strip()
    expected_source = str(selectors.get("source") or "").strip()
    candidate_symbol = str(candidate_primary_symbol(candidate) or "").strip()
    candidate_timeframe = str(candidate_primary_timeframe(candidate) or "").strip()
    candidate_strategy = str(candidate.get("strategy") or candidate.get("preset") or "").strip()
    candidate_source = str(candidate.get("source") or "bybit").strip()
    checks = [
        (expected_symbol, candidate_symbol),
        (expected_timeframe, candidate_timeframe),
        (expected_strategy, candidate_strategy),
        (expected_source, candidate_source),
    ]
    return all(not expected or expected == actual for expected, actual in checks)


def saved_promotion_candidates() -> list[dict]:
    candidates = []
    latest_candidate = latest_learning_recommendation_candidate(latest_learning_report())
    if latest_candidate:
        candidates.append(latest_candidate)
    best = best_saved_candidate(load_research_runs())
    if best:
        candidates.append(best)
    for run in reversed(load_research_runs()):
        for candidate in ([run.get("bestCandidate")] + run.get("rows", []) + run.get("topCandidates", [])):
            if not isinstance(candidate, dict):
                continue
            item = dict(candidate)
            item.setdefault("researchRunId", run.get("id"))
            item.setdefault("researchRunType", run.get("type"))
            item.setdefault("createdAt", run.get("createdAt"))
            candidates.append(item)
    deduped = []
    seen = set()
    for candidate in candidates:
        key = (
            normalized_candidate_key(candidate),
            candidate.get("researchRunId"),
            candidate.get("rank"),
            candidate.get("score"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def resolve_promotion_candidate(payload: dict) -> dict | None:
    payload = payload or {}
    embedded = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else None
    selectors_present = any(payload.get(key) for key in ("symbol", "timeframe", "interval", "strategy", "preset", "source"))
    for candidate in saved_promotion_candidates():
        if selectors_present:
            if candidate_matches_promotion_selectors(candidate, payload):
                return candidate
        elif candidate:
            return candidate
    if embedded and (not selectors_present or candidate_matches_promotion_selectors(embedded, payload)):
        return embedded
    if payload.get("rankingSnapshot") and payload.get("params"):
        return payload
    return None


def promotion_safety_gate(candidate: dict | None, force: bool) -> tuple[bool, list[str], dict]:
    if not candidate:
        return False, ["No saved recommendation candidate is available for promotion."], {}
    details = {
        "candidateQualityStatus": candidate.get("qualityStatus"),
    }
    if candidate.get("qualityStatus") == "FAIL":
        return False, ["Candidate qualityStatus is FAIL."], details
    if candidate_has_robustness_blockers(candidate) and not force:
        details["robustnessWarnings"] = candidate_robustness_warnings(candidate)
        return False, ["Candidate has robustness blockers."], details
    stability = build_candidate_stability_report({"compareCurrent": "true"})
    stability_validation = stability.get("validation") or {}
    stability_status = stability_validation.get("status")
    details["candidateStability"] = {
        "status": stability_status,
        "nextAction": stability.get("nextAction"),
        "summary": stability_validation.get("summary"),
        "robustnessFlags": stability_validation.get("robustnessFlags", []),
    }
    if stability_status != "PASS":
        return False, [f"Candidate stability is {stability_status or 'UNKNOWN'}, not PASS."], details
    evidence = build_learning_evidence(include_stability=False)
    repeatability = evidence.get("repeatability") or {}
    readiness = repeatability.get("readiness") or {}
    next_action = evidence.get("nextAction") or {}
    details["learningEvidence"] = {
        "readiness": readiness,
        "nextAction": next_action,
    }
    if readiness.get("status") != "READY_FOR_CONFIG_REVIEW" and next_action.get("action") != "REVIEW_CONFIG_ONLY_PROMOTION":
        return False, [f"Learning evidence is {readiness.get('status') or 'UNKNOWN'} with nextAction {next_action.get('action') or 'UNKNOWN'}."], details
    return True, [], details


def build_candidate_promotion_plan(payload: dict, dry_run: bool = True, force: bool = False) -> dict:
    candidate = resolve_promotion_candidate(payload)
    if not candidate:
        return {"ok": False, "error": "No saved recommendation candidate is available for promotion."}
    allowed, blocked_reasons, gate_details = promotion_safety_gate(candidate, force)
    if not allowed:
        return {
            "ok": False,
            "error": "Candidate promotion is blocked by safety gates.",
            "blockedReasons": blocked_reasons,
            "safetyGates": gate_details,
            "candidate": compact_candidate(candidate),
        }
    promotion_payload = promotion_payload_from_candidate(candidate)
    if not promotion_payload:
        return {"ok": False, "error": "No candidate is available for promotion preview."}
    preview = build_candidate_promotion_preview(promotion_payload, dry_run=dry_run, force=force, candidate=candidate)
    preview["selectedCandidate"] = compact_candidate(candidate)
    preview["safetyGates"] = gate_details
    if not preview.get("paperRemainsDisabled"):
        return {
            "ok": False,
            "error": "Promotion would enable paper; blocked.",
            "safetyGates": gate_details,
            "candidate": compact_candidate(candidate),
        }
    return preview


def promotion_quality_status(payload: dict, ranking_snapshot: dict) -> str | None:
    for source in (payload, ranking_snapshot, payload.get("optimizationSnapshot") or {}):
        if isinstance(source, dict) and source.get("qualityStatus"):
            return source.get("qualityStatus")
    return None


def promotion_warnings(current: dict, updated: dict, payload: dict, candidate: dict | None = None) -> list[str]:
    warnings = ["Promotion preview is config-only. Paper remains disabled and no trades will be placed."]
    if current.get("enabled"):
        warnings.append("Current paper simulation is enabled; promotion would replace the candidate config and disable paper.")
    if candidate_has_robustness_blockers(candidate or payload):
        warnings.extend(candidate_robustness_warnings(candidate or payload))
    if updated.get("enabled"):
        warnings.append("Preview unexpectedly has paper enabled; promotion is blocked.")
    return dedupe_list(warnings)


def changed_field_rows(before: dict, after: dict) -> list[dict]:
    fields = ["enabled", "source", "strategy", "regimeMode", "params", "symbols", "promotedAt", "promotedFromRanking", "promotedFromOptimization"]
    return [
        {"field": field, "current": before.get(field), "preview": after.get(field)}
        for field in fields
        if before.get(field) != after.get(field)
    ]


def public_promotion_preview(preview: dict) -> dict:
    public = dict(preview)
    public.pop("currentPaperCandidateRaw", None)
    return public


def build_candidate_promotion_preview(payload: dict, dry_run: bool = True, force: bool = False, candidate: dict | None = None) -> dict:
    payload = payload or {}
    ranking_snapshot = payload.get("rankingSnapshot") or {}
    min_trades = int(payload.get("minTrades") or ranking_snapshot.get("minTrades") or 10)
    quality_status = promotion_quality_status(payload, ranking_snapshot)
    if quality_status == "FAIL":
        return {"ok": False, "error": "Cannot promote a FAIL quality candidate.", "warnings": ["FAIL candidates are blocked from config promotion."]}
    promotion_error = validate_candidate_promotion(payload, ranking_snapshot, min_trades, force)
    if promotion_error:
        return {"ok": False, "error": promotion_error}
    current = load_paper_candidate_config()
    promoted_symbol = str(payload.get("symbol") or "").strip()
    promoted_interval = str(payload.get("timeframe") or payload.get("interval") or "").strip()
    updated = merge_promoted_candidate(current, payload, ranking_snapshot, promoted_symbol, promoted_interval)
    warnings = promotion_warnings(current, updated, payload, candidate)
    if updated.get("enabled"):
        return {"ok": False, "error": "Promotion preview would enable paper; blocked.", "warnings": warnings}
    preview = {
        "ok": True,
        "dryRun": bool(dry_run),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "candidateConfigPreview": updated,
        "currentPaperCandidate": candidate_summary(current),
        "currentPaperCandidateRaw": current,
        "changedFields": changed_field_rows(current, updated),
        "expectedBaselineMetrics": expected_metrics_from_candidate(updated),
        "configWarnings": candidate_config_warnings(updated),
        "warnings": warnings,
        "paperRemainsDisabled": not bool(updated.get("enabled")),
        "message": "Dry run only; no config was written." if dry_run else "Promotion preview is ready.",
    }
    return preview


def build_candidate_review() -> dict:
    latest = latest_learning_report()
    audit = build_learning_quality_audit()
    recommended = latest_learning_recommendation_candidate(latest)
    best = best_saved_candidate(load_research_runs())
    current = load_paper_candidate_config()
    promotion_payload = promotion_payload_from_candidate(recommended)
    preview = build_candidate_promotion_preview(promotion_payload, dry_run=True) if promotion_payload else {"ok": False}
    preview_config = preview.get("candidateConfigPreview") if preview.get("ok") else None
    quality_pass = candidate_passes_quality(recommended)
    candidate_exists = bool(recommended)
    paper_enabled = canonical_paper_enabled(current)
    warnings = []
    if not candidate_exists:
        next_action = {"action": "NO_CANDIDATE", "reason": "No latest learning recommendation candidate is available."}
    elif (recommended or {}).get("qualityStatus") == "FAIL" or candidate_has_robustness_blockers(recommended):
        next_action = {"action": "BLOCKED", "reason": "Latest learning recommendation candidate is blocked by quality or robustness checks."}
        warnings.extend(candidate_robustness_warnings(recommended))
    elif quality_pass:
        next_action = {"action": "REVIEW_AND_OPTIONALLY_PROMOTE_CONFIG_ONLY", "reason": "Candidate can be previewed for manual config-only promotion. Paper remains disabled."}
    else:
        next_action = {"action": "OBSERVE_MORE", "reason": "Latest learning candidate is not a clean PASS candidate yet."}
    if paper_enabled:
        warnings.append("Paper is currently enabled; a manual promotion would replace the paper candidate config and keep paper disabled.")
    else:
        warnings.append("Paper is disabled. Config-only promotion preview keeps paper disabled.")
    if preview.get("warnings"):
        warnings.extend(preview.get("warnings", []))
    comparison = candidate_review_comparison(recommended, current, preview_config)
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "recommendedCandidate": compact_candidate(recommended),
        "bestSavedCandidate": compact_candidate(best),
        "currentPaperCandidate": candidate_summary(current),
        "comparison": comparison,
        "readiness": {
            "candidateExists": candidate_exists,
            "candidateQualityPass": quality_pass,
            "auditStatus": audit.get("status"),
            "safeForManualReview": build_learning_audit_summary().get("readiness", {}).get("safeForManualReview", False),
            "paperEnabled": paper_enabled,
            "canPromoteConfigOnly": bool(candidate_exists and quality_pass and preview.get("ok")),
            "canEnablePaper": False,
        },
        "promotionPreview": public_promotion_preview(preview) if preview.get("ok") else None,
        "warnings": dedupe_list(warnings),
        "nextAction": next_action,
    }


def candidate_fee_slippage(candidate: dict | None, fallback: dict | None = None) -> tuple[float, float]:
    candidate = candidate or {}
    fallback = fallback or {}
    fee = candidate.get("feePct")
    if fee is None:
        fee = candidate.get("takerFeePct", fallback.get("takerFeePct", 0.055))
    slippage = candidate.get("slippagePct")
    if slippage is None and candidate.get("slippageBps") is not None:
        slippage = safe_float(candidate.get("slippageBps")) / 100.0
    if slippage is None and fallback.get("slippageBps") is not None:
        slippage = safe_float(fallback.get("slippageBps")) / 100.0
    if slippage is None:
        slippage = fallback.get("slippagePct", 0.02)
    return safe_float(fee, 0.055), safe_float(slippage, 0.02)


def candidate_stability_windows(period: str) -> list[dict]:
    requested_days = parse_period_to_days(period) or 365
    windows = []
    for label, days in (("full", int(requested_days)), ("recent_180d", 180), ("recent_90d", 90)):
        if days <= requested_days:
            windows.append({"label": label, "period": f"{days}d", "minTrades": 30 if days >= 365 else 15 if days >= 180 else 8})
    return windows or [{"label": "full", "period": period, "minTrades": 30}]


def stability_window_status(metrics: dict, min_trades: int, allow_watch: bool = True) -> tuple[str, list[str]]:
    warnings = []
    trades = int(safe_float(metrics.get("trades")))
    total_return = safe_float(metrics.get("totalReturn"))
    drawdown = safe_float(metrics.get("maxDrawdown"))
    profit_factor = safe_float(metrics.get("profitFactor"))
    if trades == 0:
        return "FAIL", ["Window generated zero trades."]
    if total_return < 0:
        warnings.append("Window return is negative.")
    if trades < min_trades:
        warnings.append(f"Window has too few trades ({trades} < {min_trades}).")
    if drawdown > 25:
        warnings.append(f"Window drawdown is high ({round(drawdown, 4)}%).")
    if profit_factor <= 1:
        warnings.append("Window profit factor is <= 1.")
    if any("zero trades" in item.lower() for item in warnings):
        return "FAIL", warnings
    if warnings and allow_watch:
        return "WATCH", warnings
    return ("FAIL" if warnings else "PASS"), warnings


def run_candidate_stability_windows(candidate: dict, source: str, symbol: str, timeframe: str, period: str, current_fallback: dict | None = None) -> dict:
    fee_pct, slippage_pct = candidate_fee_slippage(candidate, current_fallback)
    windows = []
    all_warnings = []
    for window in candidate_stability_windows(period):
        try:
            limit = research_limit_for(source, timeframe, window["period"], "auto")
            payload = run_shared_backtest_engine(
                source,
                symbol,
                timeframe,
                window["period"],
                candidate.get("strategy") or candidate.get("preset"),
                fee_pct,
                slippage_pct,
                limit,
                False,
                False,
                candidate.get("params") or {},
            )
            payload = normalize_backtest_response(payload, source, symbol, timeframe, window["period"], candidate.get("strategy") or candidate.get("preset"), fee_pct, slippage_pct)
            metrics = ranking_metrics_from_backtest(payload)
            status, warnings = stability_window_status(metrics, int(window["minTrades"]))
            windows.append({
                "label": window["label"],
                "period": window["period"],
                "status": status,
                "trades": metrics["trades"],
                "profitFactor": metrics["profitFactor"],
                "totalReturnPct": metrics["totalReturn"],
                "maxDrawdownPct": metrics["maxDrawdown"],
                "winRate": metrics["winRate"],
                "warnings": warnings + (payload.get("diagnostics") or {}).get("warnings", []),
                "candlesLoaded": payload.get("candlesLoaded"),
            })
            all_warnings.extend(warnings)
        except Exception as exc:
            windows.append({
                "label": window["label"],
                "period": window["period"],
                "status": "FAIL",
                "trades": 0,
                "profitFactor": 0,
                "totalReturnPct": 0,
                "maxDrawdownPct": 0,
                "winRate": 0,
                "warnings": [f"Backtest crashed: {exc}"],
                "candlesLoaded": 0,
            })
            all_warnings.append(f"{window['period']} backtest crashed: {exc}")
    aggregate = stability_aggregate(windows)
    flags = stability_flags(windows, aggregate)
    status = "UNKNOWN"
    if windows:
        if any(row.get("status") == "FAIL" for row in windows) or "negative_full_return" in flags or "too_few_full_trades" in flags:
            status = "FAIL"
        elif any(row.get("status") == "WATCH" for row in windows) or flags:
            status = "WATCH"
        else:
            status = "PASS"
    return {
        "status": status,
        "summary": stability_summary(status, aggregate, flags),
        "windows": windows,
        "aggregate": aggregate,
        "robustnessFlags": flags,
        "warnings": dedupe_list(all_warnings),
    }


def stability_aggregate(windows: list[dict]) -> dict:
    full = next((row for row in windows if row.get("label") == "full"), windows[0] if windows else {})
    return {
        "trades": int(safe_float(full.get("trades"))),
        "profitFactor": safe_float(full.get("profitFactor")),
        "totalReturnPct": safe_float(full.get("totalReturnPct")),
        "maxDrawdownPct": safe_float(full.get("maxDrawdownPct")),
        "winRate": safe_float(full.get("winRate")),
    }


def stability_flags(windows: list[dict], aggregate: dict) -> list[str]:
    flags = []
    if safe_float(aggregate.get("totalReturnPct")) < 0:
        flags.append("negative_full_return")
    if safe_float(aggregate.get("trades")) < 30:
        flags.append("too_few_full_trades")
    recent_90 = next((row for row in windows if row.get("label") == "recent_90d"), None)
    if recent_90 and safe_float(recent_90.get("totalReturnPct")) < 0:
        flags.append("negative_recent_90d_return")
    if any(safe_float(row.get("trades")) < 8 for row in windows):
        flags.append("thin_window_trades")
    if any(safe_float(row.get("maxDrawdownPct")) > 25 for row in windows):
        flags.append("high_drawdown")
    if any(row.get("status") == "FAIL" for row in windows):
        flags.append("window_failed")
    return dedupe_list(flags)


def stability_summary(status: str, aggregate: dict, flags: list[str]) -> str:
    base = f"{status}: full-window PF {round(safe_float(aggregate.get('profitFactor')), 4)}, return {round(safe_float(aggregate.get('totalReturnPct')), 4)}%, trades {int(safe_float(aggregate.get('trades')))}."
    if flags:
        return f"{base} Flags: {', '.join(flags)}."
    return f"{base} No major stability flags."


def current_candidate_as_recommended(current: dict) -> dict | None:
    active = candidate_symbols_by_mode(current, "active")
    primary = active[0] if active else {}
    if not current.get("strategy") or not primary.get("symbol"):
        return None
    params = dict(current.get("params", {}) or {})
    if current.get("regimeMode") and params.get("regimeMode") is None:
        params["regimeMode"] = current.get("regimeMode")
    return {
        "source": current.get("source", "bybit"),
        "symbol": primary.get("symbol"),
        "timeframe": primary.get("interval") or primary.get("timeframe"),
        "strategy": current.get("strategy"),
        "preset": current.get("strategy"),
        "params": params,
        "qualityStatus": "UNKNOWN",
        "valid": True,
    }


def compare_stability_results(candidate_validation: dict, current_validation: dict | None, candidate: dict | None, current: dict | None) -> dict:
    if not current_validation:
        return {"available": False, "candidateBetter": None, "metricDiffs": [], "summary": "Current paper candidate comparison was not requested or unavailable."}
    candidate_metrics = candidate_validation.get("aggregate") or {}
    current_metrics = current_validation.get("aggregate") or {}
    diffs = []
    for field in ("totalReturnPct", "profitFactor", "maxDrawdownPct", "trades", "winRate"):
        diffs.append({
            "field": field,
            "candidate": candidate_metrics.get(field),
            "current": current_metrics.get(field),
            "diff": safe_float(candidate_metrics.get(field)) - safe_float(current_metrics.get(field)),
        })
    candidate_better = None
    if candidate_validation.get("status") != "FAIL" and current_validation.get("status") == "FAIL":
        candidate_better = True
    elif candidate_validation.get("status") != "FAIL" and current_validation.get("status") != "UNKNOWN":
        candidate_better = (
            safe_float(candidate_metrics.get("profitFactor")) >= safe_float(current_metrics.get("profitFactor")) and
            safe_float(candidate_metrics.get("totalReturnPct")) >= safe_float(current_metrics.get("totalReturnPct")) and
            safe_float(candidate_metrics.get("maxDrawdownPct")) <= safe_float(current_metrics.get("maxDrawdownPct"), 999)
        )
    symbol_note = "same market" if candidate_primary_symbol(candidate) == candidate_primary_symbol(current) else "different market"
    return {
        "available": True,
        "candidateBetter": candidate_better,
        "metricDiffs": diffs,
        "summary": f"Candidate compared against current paper candidate on bounded windows ({symbol_note}).",
    }


def build_candidate_stability_report(args) -> dict:
    latest = latest_learning_report()
    candidate = latest_learning_recommendation_candidate(latest)
    current = load_paper_candidate_config()
    if not candidate:
        return {
            "ok": True,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "candidate": None,
            "currentPaperCandidate": candidate_summary(current),
            "validation": {"status": "FAIL", "summary": "No latest learning recommendation candidate exists.", "windows": [], "aggregate": {}, "robustnessFlags": ["no_candidate"], "warnings": ["No candidate available."]},
            "comparisonToCurrent": {"available": False, "candidateBetter": None, "metricDiffs": [], "summary": "No candidate to compare."},
            "nextAction": {"action": "NO_CANDIDATE", "reason": "No latest learning recommendation candidate exists."},
        }
    source = args.get("source") or candidate.get("source") or "bybit"
    symbol = args.get("symbol") or candidate.get("symbol")
    timeframe = args.get("timeframe") or candidate.get("timeframe")
    period = args.get("period") or candidate.get("period") or "365d"
    compare_current = str(args.get("compareCurrent", "true")).lower() not in {"0", "false", "no", "off"}
    candidate_for_run = dict(candidate)
    candidate_for_run.update({"source": source, "symbol": symbol, "timeframe": timeframe})
    validation = run_candidate_stability_windows(candidate_for_run, source, symbol, timeframe, period, current)
    audit = build_learning_quality_audit()
    current_validation = None
    current_candidate = current_candidate_as_recommended(current)
    if compare_current and current_candidate:
        current_validation = run_candidate_stability_windows(
            current_candidate,
            current_candidate.get("source") or "bybit",
            current_candidate.get("symbol"),
            current_candidate.get("timeframe"),
            period,
            current,
        )
    comparison = compare_stability_results(validation, current_validation, candidate_for_run, current_candidate)
    if validation.get("status") == "FAIL":
        next_action = {"action": "BLOCKED", "reason": "Candidate failed bounded stability validation."}
    elif validation.get("status") == "PASS" and audit.get("status") != "WATCH":
        next_action = {"action": "REVIEW_CONFIG_ONLY_PROMOTION", "reason": "Candidate passed bounded validation and can be reviewed for config-only promotion. Paper remains disabled."}
    elif validation.get("status") == "PASS":
        next_action = {"action": "OBSERVE_MORE", "reason": "Candidate passed bounded validation, but learning audit remains WATCH; observe more before promotion."}
    else:
        next_action = {"action": "OBSERVE_MORE", "reason": "Candidate has stability warnings; observe more before config-only promotion."}
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "candidate": compact_candidate(candidate_for_run),
        "currentPaperCandidate": candidate_summary(current),
        "validation": validation,
        "comparisonToCurrent": comparison,
        "currentValidation": current_validation,
        "nextAction": next_action,
    }


def normalized_candidate_key(candidate: dict | None) -> str | None:
    if not candidate:
        return None
    strategy = candidate.get("strategy") or candidate.get("preset")
    symbol = candidate_primary_symbol(candidate)
    timeframe = candidate_primary_timeframe(candidate)
    if not strategy or not symbol or not timeframe:
        return None
    params = candidate.get("params") or {}
    params_key = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return "|".join([str(strategy), str(symbol), str(timeframe), params_key])


def family_candidate_key(candidate: dict | None) -> str | None:
    if not candidate:
        return None
    strategy = candidate.get("strategy") or candidate.get("preset")
    symbol = candidate_primary_symbol(candidate)
    timeframe = candidate_primary_timeframe(candidate)
    if not strategy or not symbol or not timeframe:
        return None
    return "|".join([str(strategy), str(symbol), str(timeframe)])


def candidate_metric_value(candidate: dict | None, metric: str) -> float:
    candidate = candidate or {}
    quality = candidate.get("qualityMetrics") or {}
    aliases = {
        "profitFactor": ("profitFactor", "testProfitFactor", "fullProfitFactor"),
        "return": ("totalReturnPct", "testReturnPct", "fullReturnPct"),
        "drawdown": ("maxDrawdown", "testMaxDrawdownPct", "fullMaxDrawdownPct"),
        "trades": ("trades", "testTrades", "fullTrades"),
    }
    for key in aliases.get(metric, (metric,)):
        if candidate.get(key) is not None:
            return safe_float(candidate.get(key))
        if quality.get(key) is not None:
            return safe_float(quality.get(key))
    if metric == "return":
        return candidate_metric(candidate, "test", "totalReturn", ("totalReturnPct",))
    if metric == "trades":
        return candidate_metric(candidate, "test", "trades", ("trades",))
    return 0.0


def candidate_appearance_from_report(report: dict, candidate: dict, target_key: str | None = None, target_family_key: str | None = None) -> dict:
    key = normalized_candidate_key(candidate)
    family_key = family_candidate_key(candidate)
    return {
        "reportId": report.get("id"),
        "createdAt": report.get("createdAt"),
        "candidateKey": key,
        "exactCandidateKey": key,
        "familyCandidateKey": family_key,
        "matches": bool(target_key and key == target_key),
        "exactMatches": bool(target_key and key == target_key),
        "familyMatches": bool(target_family_key and family_key == target_family_key),
        "strategy": candidate.get("strategy") or candidate.get("preset"),
        "symbol": candidate_primary_symbol(candidate),
        "timeframe": candidate_primary_timeframe(candidate),
        "params": candidate.get("params") or {},
        "qualityStatus": candidate.get("qualityStatus"),
        "score": candidate.get("score"),
        "profitFactor": candidate_metric_value(candidate, "profitFactor"),
        "returnPct": candidate_metric_value(candidate, "return"),
        "drawdownPct": candidate_metric_value(candidate, "drawdown"),
        "trades": int(candidate_metric_value(candidate, "trades")),
    }


def metric_stability(appearances: list[dict]) -> dict:
    def values(key: str) -> list[float]:
        return [safe_float(item.get(key)) for item in appearances if item.get(key) is not None]

    pf = values("profitFactor")
    returns = values("returnPct")
    drawdowns = values("drawdownPct")
    trades = values("trades")
    return {
        "profitFactorMin": min(pf) if pf else 0,
        "profitFactorMax": max(pf) if pf else 0,
        "profitFactorSpread": round(max(pf) - min(pf), 6) if pf else 0,
        "returnMin": min(returns) if returns else 0,
        "returnMax": max(returns) if returns else 0,
        "returnSpread": round(max(returns) - min(returns), 6) if returns else 0,
        "drawdownMax": max(drawdowns) if drawdowns else 0,
        "tradesMin": int(min(trades)) if trades else 0,
    }


def param_drift_summary(appearances: list[dict]) -> dict:
    family = [item for item in appearances if item.get("familyMatches")]
    if len(family) < 2:
        return {
            "familyReportsCompared": len(family),
            "changedParamCount": 0,
            "stableParamCount": 0,
            "changedParams": [],
            "stableParams": [],
            "driftStatus": "UNKNOWN",
            "summary": "Need at least two family-matching reports to measure parameter drift.",
        }
    param_sets = [item.get("params") or {} for item in family]
    keys = sorted(set().union(*[set(params.keys()) for params in param_sets]))
    changed = []
    stable = []
    for key in keys:
        values = [params.get(key) for params in param_sets]
        unique_values = []
        for value in values:
            if value not in unique_values:
                unique_values.append(value)
        if len(unique_values) > 1:
            changed.append({"param": key, "values": unique_values})
        else:
            stable.append({"param": key, "value": unique_values[0] if unique_values else None})
    core_params = {"atrMultiplier", "emaFast", "emaSlow", "emaTrend", "rsiMin", "rsiMax", "cooldownBars", "minHoldBars", "regimeMode", "volumeFilter"}
    changed_core = len([item for item in changed if item["param"] in core_params])
    total = max(1, len(keys))
    changed_ratio = len(changed) / total
    if changed_core == 0 or changed_ratio <= 0.25:
        drift_status = "LOW"
        summary = "Same family repeats with mostly stable parameters."
    elif changed_core <= 2 or changed_ratio <= 0.5:
        drift_status = "MEDIUM"
        summary = "Same family repeats with some parameter drift."
    else:
        drift_status = "HIGH"
        summary = "Same family repeats, but parameters are still drifting."
    return {
        "familyReportsCompared": len(family),
        "changedParamCount": len(changed),
        "stableParamCount": len(stable),
        "changedParams": changed,
        "stableParams": stable,
        "driftStatus": drift_status,
        "summary": summary,
    }


def close_report_warning(appearances: list[dict]) -> str | None:
    times = [parse_learning_time(item.get("createdAt")) for item in appearances if item.get("createdAt")]
    times = [item for item in times if item]
    if len(times) < 2:
        return None
    times = sorted(times)
    min_gap_hours = min((later - earlier).total_seconds() / 3600 for earlier, later in zip(times, times[1:]))
    if min_gap_hours < 6:
        return "Repeated learning reports are close together; this may not represent independent market evidence."
    return None


def close_learning_report_warning(reports: list[dict]) -> str | None:
    appearances = [{"createdAt": report.get("createdAt")} for report in reports if report.get("createdAt")]
    warning = close_report_warning(appearances)
    if warning:
        return "Recent learning reports are close together; this may not represent independent market evidence."
    return None


def learning_evidence_readiness(
    reports_considered: int,
    exact_repeat_count: int,
    required_exact_repeat_count: int,
    family_repeat_count: int,
    required_family_repeat_count: int,
    churn_ratio: float,
    candidate: dict | None,
    stability_summary: dict | None,
    param_drift: dict,
    warnings: list[str],
) -> dict:
    missing_exact = max(0, required_exact_repeat_count - exact_repeat_count)
    missing_family = max(0, required_family_repeat_count - family_repeat_count)
    missing_reports = max(0, 3 - reports_considered, missing_exact)
    if not candidate:
        return {"status": "BLOCKED", "missingReports": 3, "reason": "No latest recommendation candidate is available."}
    if candidate.get("qualityStatus") == "FAIL" or candidate_has_robustness_blockers(candidate):
        return {"status": "BLOCKED", "missingReports": missing_reports, "reason": "Candidate has quality or robustness blockers."}
    stability_status = (stability_summary or {}).get("status")
    if stability_status == "FAIL":
        return {"status": "BLOCKED", "missingReports": missing_reports, "reason": "Candidate stability validation failed."}
    if reports_considered < 3:
        return {"status": "COLLECTING", "missingReports": missing_reports, "reason": "Need at least 3 learning reports before readiness review."}
    if churn_ratio > 0.6:
        return {"status": "WATCH", "missingReports": 0, "reason": f"Recommendation churn is too high ({round(churn_ratio, 4)} > 0.6)."}
    if exact_repeat_count >= required_exact_repeat_count and stability_status in (None, "PASS", "UNKNOWN"):
        return {"status": "READY_FOR_CONFIG_REVIEW", "missingReports": 0, "reason": "Repeatability and stability evidence support config-only manual review."}
    drift_status = (param_drift or {}).get("driftStatus", "UNKNOWN")
    if family_repeat_count >= required_family_repeat_count and stability_status in (None, "PASS", "UNKNOWN") and drift_status in {"LOW", "MEDIUM"}:
        return {"status": "FAMILY_STABLE", "missingReports": 0, "missingExactReports": missing_exact, "missingFamilyReports": 0, "reason": "Strategy/symbol/timeframe family is repeating with acceptable parameter drift."}
    if family_repeat_count >= 2 and exact_repeat_count < required_exact_repeat_count:
        if drift_status == "HIGH":
            return {"status": "WATCH_PARAM_DRIFT", "missingReports": missing_exact, "missingExactReports": missing_exact, "missingFamilyReports": missing_family, "reason": "Candidate family is repeating, but optimizer parameters are drifting too much."}
        return {"status": "WATCH", "missingReports": missing_exact, "missingExactReports": missing_exact, "missingFamilyReports": missing_family, "reason": f"Candidate family is repeating ({family_repeat_count}/{required_family_repeat_count}), but exact parameters need more stability ({exact_repeat_count}/{required_exact_repeat_count})."}
    if exact_repeat_count < required_exact_repeat_count:
        return {"status": "WATCH", "missingReports": missing_reports, "missingExactReports": missing_exact, "missingFamilyReports": missing_family, "reason": f"Need exact candidate repeated {required_exact_repeat_count} times; current exact repeat count is {exact_repeat_count}."}
    return {"status": "WATCH", "missingReports": 0, "reason": "Candidate has stability warnings; observe more before config-only review."}


def build_learning_evidence(include_stability: bool = True, reports: list[dict] | None = None, latest_report: dict | None = None, audit_status: str | None = None) -> dict:
    reports = reports if reports is not None else load_learning_reports()
    latest_report = latest_report if latest_report is not None else (reports[-1] if reports else None)
    candidate = latest_learning_recommendation_candidate(latest_report)
    candidate_key = normalized_candidate_key(candidate)
    family_key = family_candidate_key(candidate)
    considered = reports[-10:]
    appearances = []
    recommendation_keys = []
    family_keys = []
    for report in considered:
        rec_candidate = latest_learning_recommendation_candidate(report)
        key = normalized_candidate_key(rec_candidate)
        fam_key = family_candidate_key(rec_candidate)
        if key:
            recommendation_keys.append(key)
        if fam_key:
            family_keys.append(fam_key)
        if rec_candidate:
            appearances.append(candidate_appearance_from_report(report, rec_candidate, candidate_key, family_key))
    matching = [item for item in appearances if item.get("matches")]
    family_matching = [item for item in appearances if item.get("familyMatches")]
    counts = {}
    for key in recommendation_keys:
        counts[key] = counts.get(key, 0) + 1
    family_counts = {}
    for key in family_keys:
        family_counts[key] = family_counts.get(key, 0) + 1
    total_recommendations = len(recommendation_keys)
    unique_candidates = len(counts)
    churn_ratio = (unique_candidates - 1) / max(1, total_recommendations - 1) if total_recommendations > 1 else 0
    warnings = []
    for warning in (close_learning_report_warning(considered), close_report_warning(matching)):
        if warning:
            warnings.append(warning)
    param_drift = param_drift_summary(appearances)
    if len(family_matching) > len(matching):
        warnings.append(f"{family_key or 'Latest candidate family'} is repeating as a family, but exact optimizer parameters differ.")
    stability = None
    if include_stability and candidate:
        stability_payload = build_candidate_stability_report({"compareCurrent": "true"})
        stability = {
            "status": (stability_payload.get("validation") or {}).get("status"),
            "summary": (stability_payload.get("validation") or {}).get("summary"),
            "aggregate": (stability_payload.get("validation") or {}).get("aggregate", {}),
            "robustnessFlags": (stability_payload.get("validation") or {}).get("robustnessFlags", []),
            "nextAction": stability_payload.get("nextAction", {}),
        }
    required_repeat_count = 3
    required_family_repeat_count = 3
    readiness = learning_evidence_readiness(
        len(considered),
        len(matching),
        required_repeat_count,
        len(family_matching),
        required_family_repeat_count,
        churn_ratio,
        candidate,
        stability,
        param_drift,
        warnings,
    )
    next_action = {"action": "OBSERVE_MORE", "reason": readiness.get("reason")}
    if readiness["status"] == "COLLECTING":
        next_action = {"action": "RUN_MORE_LEARNING", "reason": readiness["reason"]}
    elif readiness["status"] in {"READY_FOR_CONFIG_REVIEW", "FAMILY_STABLE"}:
        next_action = {"action": "REVIEW_CONFIG_ONLY_PROMOTION", "reason": "Evidence supports config-only manual promotion review. Paper remains disabled."}
    elif readiness["status"] == "BLOCKED":
        next_action = {"action": "BLOCKED", "reason": readiness["reason"]}
    elif len(family_matching) >= 2 and len(matching) < required_repeat_count:
        next_action = {"action": "OBSERVE_PARAM_STABILITY", "reason": readiness["reason"]}
    repeatability = {
        "candidateKey": candidate_key,
        "exactCandidateKey": candidate_key,
        "familyCandidateKey": family_key,
        "strategy": (candidate or {}).get("strategy") or (candidate or {}).get("preset"),
        "symbol": candidate_primary_symbol(candidate),
        "timeframe": candidate_primary_timeframe(candidate),
        "reportsConsidered": len(considered),
        "repeatCount": len(matching),
        "exactRepeatCount": len(matching),
        "familyRepeatCount": len(family_matching),
        "requiredRepeatCount": required_repeat_count,
        "requiredExactRepeatCount": required_repeat_count,
        "requiredFamilyRepeatCount": required_family_repeat_count,
        "recentAppearances": appearances,
        "metricStability": metric_stability(matching),
        "familyMetricStability": metric_stability(family_matching),
        "paramDrift": param_drift,
        "churn": {
            "uniqueCandidates": unique_candidates,
            "uniqueFamilies": len(family_counts),
            "totalRecommendations": total_recommendations,
            "churnRatio": round(churn_ratio, 4),
        },
        "readiness": readiness,
        "warnings": warnings,
    }
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "latestRecommendedCandidate": compact_candidate(candidate),
        "repeatability": repeatability,
        "candidateStability": stability,
        "learningAuditStatus": audit_status if audit_status is not None else build_learning_quality_audit().get("status"),
        "nextAction": next_action,
        "warnings": warnings,
    }


def optimizer_quality_from_run(run: dict | None) -> dict:
    summary = (run or {}).get("summary") or {}
    quality = (run or {}).get("qualitySummary") or summary.get("qualitySummary") or {}
    return {
        "latestSelectedStatus": quality.get("selectedStatus") or "UNKNOWN",
        "passCandidates": int(safe_float(quality.get("passCandidates", 0))),
        "warnCandidates": int(safe_float(quality.get("warnCandidates", 0))),
        "failCandidates": int(safe_float(quality.get("failCandidates", 0))),
        "totalCandidates": int(safe_float(quality.get("totalCandidates", 0))),
        "topRejectionReasons": quality.get("topRejectionReasons", []),
        "warnings": quality.get("warnings", []),
    }


def zero_trade_summary_from_run(run: dict | None) -> dict:
    zero = (run or {}).get("zeroTradeSummary") or {}
    top_reasons = zero.get("topReasons", [])
    has_problem = bool((run or {}).get("allZeroTradeCandidates")) or safe_float(zero.get("zeroTradeCandidates")) > 0
    return {
        "hasZeroTradeProblem": has_problem,
        "zeroTradeCandidates": int(safe_float(zero.get("zeroTradeCandidates", 0))),
        "totalCandidates": int(safe_float(zero.get("totalCandidates", 0))),
        "topReasons": top_reasons,
        "suggestedActions": zero_trade_suggested_actions(zero, top_reasons),
    }


def zero_trade_suggested_actions(zero: dict, top_reasons: list[dict]) -> list[str]:
    actions = []
    reason = (top_reasons[0] or {}).get("reason") if top_reasons else None
    if reason in {"zero_trades", "no_entry_signal", "trend_filter_blocked", "regime_filter_blocked"}:
        actions.append("Inspect rejection reasons before widening grids.")
        actions.append("Try a longer period or higher timeframe before trusting zero-trade scans.")
    if reason in {"warmup_not_met", "too_few_test_trades"}:
        actions.append("Use Auto/50000 candle limits or a longer period.")
    if zero.get("suggestedGridAction"):
        actions.append(zero["suggestedGridAction"])
    if not actions and zero:
        actions.append("Run /api/backtest/diagnose on the strategy and market before changing formulas.")
    return dedupe_list(actions)


def candidate_metric(candidate: dict | None, section: str, key: str, fallback_keys: tuple[str, ...] = ()) -> float:
    candidate = candidate or {}
    quality = candidate.get("qualityMetrics") or {}
    quality_keys = {
        "totalReturn": f"{section}ReturnPct",
        "trades": f"{section}Trades",
        "maxDrawdown": f"{section}MaxDrawdownPct",
        "profitFactor": f"{section}ProfitFactor",
    }
    for quality_key in (quality_keys.get(key), f"{section}{key[0].upper()}{key[1:]}"):
        if quality_key and quality.get(quality_key) is not None:
            return safe_float(quality.get(quality_key))
    bucket = candidate.get(section) if isinstance(candidate.get(section), dict) else {}
    if bucket.get(key) is not None:
        return safe_float(bucket.get(key))
    for fallback in fallback_keys:
        if candidate.get(fallback) is not None:
            return safe_float(candidate.get(fallback))
    return 0.0


def candidate_reason_codes(candidate: dict | None) -> set[str]:
    codes = set()
    for item in (candidate or {}).get("rejectionReasons") or []:
        if isinstance(item, dict):
            codes.add(str(item.get("code") or item.get("reason") or ""))
        elif item:
            codes.add(str(item))
    warnings_text = " ".join((candidate or {}).get("warnings") or []).lower()
    if "negative full-period return" in warnings_text:
        codes.add("negative_full_return")
    if "train/test direction mismatch" in warnings_text:
        codes.add("train_test_direction_mismatch")
    if "strongly negative train return" in warnings_text:
        codes.add("strongly_negative_train_return")
    if "low test-trade evidence" in warnings_text:
        codes.add("low_test_trade_evidence")
    return {code for code in codes if code}


def candidate_robustness_warnings(candidate: dict | None) -> list[str]:
    if not candidate:
        return []
    warnings = []
    codes = candidate_reason_codes(candidate)
    train_return = candidate_metric(candidate, "train", "totalReturn")
    test_return = candidate_metric(candidate, "test", "totalReturn", ("totalReturnPct",))
    full_return = candidate_metric(candidate, "full", "totalReturn")
    test_trades = candidate_metric(candidate, "test", "trades", ("trades",))
    min_test_trades = 10
    if full_return < 0 or "negative_full_return" in codes:
        warnings.append(f"Best candidate has negative full-period return ({round(full_return, 4)}%).")
    if (train_return < 0 and test_return > 0) or "train_test_direction_mismatch" in codes:
        warnings.append(f"Best candidate has train/test direction mismatch (train {round(train_return, 4)}%, test {round(test_return, 4)}%).")
    if (train_return < -5 and test_return > 0) or "strongly_negative_train_return" in codes:
        warnings.append(f"Best candidate has strongly negative train return ({round(train_return, 4)}%).")
    if (min_test_trades <= test_trades <= min_test_trades + 2) or "low_test_trade_evidence" in codes:
        warnings.append(f"Best candidate has low test-trade evidence ({int(test_trades)} test trades).")
    return dedupe_list(warnings)


def candidate_has_robustness_blockers(candidate: dict | None) -> bool:
    if not candidate:
        return False
    codes = candidate_reason_codes(candidate)
    blocking_codes = {"negative_full_return", "strongly_negative_train_return"}
    if codes & blocking_codes:
        return True
    train_return = candidate_metric(candidate, "train", "totalReturn")
    test_return = candidate_metric(candidate, "test", "totalReturn", ("totalReturnPct",))
    full_return = candidate_metric(candidate, "full", "totalReturn")
    return full_return < 0 or (train_return < -5 and test_return > 0)


def learning_audit_readiness(reports: list[dict], best_candidate: dict | None, current_candidate: dict, health: dict, optimizer_quality: dict) -> dict:
    has_pass = optimizer_quality.get("latestSelectedStatus") == "PASS" or safe_float(optimizer_quality.get("passCandidates")) > 0
    candidate_blocked = candidate_has_robustness_blockers(best_candidate)
    return {
        "enoughLearningReports": len(reports) >= learning_trust_rules()["minReports"],
        "hasValidCandidate": bool(best_candidate),
        "hasPassOptimizerCandidate": bool(has_pass),
        "paperEnabled": canonical_paper_enabled(current_candidate),
        "safeForManualReview": bool(best_candidate and not candidate_blocked and (has_pass or best_candidate.get("origin") == "ranking") and health.get("status") != "FAILED"),
    }


def learning_audit_next_action(
    latest_learning: dict | None,
    optimizer_quality: dict,
    zero_trade: dict,
    readiness: dict,
    health: dict,
    best_candidate: dict | None,
    latest_recommendation_candidate: dict | None = None,
    evidence: dict | None = None,
) -> dict:
    commands = ["python run_server.py"]
    if not latest_learning:
        return {"action": "RUN_LEARNING", "reason": "No learning reports exist yet.", "commands": commands + ["POST /api/learning/run"]}
    if latest_learning.get("status") == "failed":
        return {"action": "INSPECT_REJECTIONS", "reason": "Latest learning report failed; inspect errors before rerunning.", "commands": commands + ["GET /api/learning/reports"]}
    if readiness.get("paperEnabled") and health.get("status") == "UNKNOWN":
        return {"action": "WAIT_FOR_PAPER_DATA", "reason": "Paper simulation is enabled but there are not enough closed paper trades for health scoring.", "commands": ["npm run paper:tick -- --config config/local/paper-candidate.json --refresh-first"]}
    if health.get("status") == "HEALTHY":
        return {"action": "KEEP_CURRENT", "reason": "Current paper candidate health is aligned with expectations.", "commands": []}
    evidence_readiness = ((evidence or {}).get("repeatability") or {}).get("readiness") or {}
    if evidence_readiness.get("status") == "READY_FOR_CONFIG_REVIEW":
        return {
            "action": "REVIEW_CONFIG_ONLY_PROMOTION",
            "reason": "Learning evidence is repeatable enough for config-only manual promotion review. Paper remains disabled.",
            "commands": ["GET /api/learning/evidence", "GET /api/candidate/review", "GET /api/candidate/promote-preview"],
        }
    if evidence_readiness.get("status") in {"COLLECTING", "WATCH"}:
        return {
            "action": "OBSERVE_MORE",
            "reason": evidence_readiness.get("reason") or "More learning evidence is needed before config-only manual review.",
            "commands": ["GET /api/learning/evidence", "POST /api/learning/run"],
        }
    if candidate_passes_quality(latest_recommendation_candidate):
        return {
            "action": "REVIEW_CANDIDATE",
            "reason": "Latest learning report contains a PASS recommendation candidate. Review it manually before tuning global optimizer policy; paper remains disabled.",
            "commands": ["GET /api/learning/audit", "GET /api/research/best-candidate", "GET /api/candidate/validate"],
        }
    if latest_recommendation_candidate and candidate_has_robustness_blockers(latest_recommendation_candidate):
        return {
            "action": "TUNE_QUALITY_POLICY",
            "reason": "Latest learning recommendation candidate has robustness blockers; inspect quality reasons before manual review.",
            "commands": ["GET /api/learning/audit", "GET /api/strategy-optimize?source=bybit&symbol=BTCUSDT&timeframe=1h&period=365d&limit=auto&max_combos=20"],
        }
    if optimizer_quality.get("latestSelectedStatus") == "NONE":
        reasons = {item.get("reason") for item in optimizer_quality.get("topRejectionReasons", [])}
        if reasons & {"zero_trades", "too_few_test_trades", "too_few_full_trades", "zero_trade_diagnostics"} or zero_trade.get("hasZeroTradeProblem"):
            return {"action": "TUNE_GRIDS", "reason": "Latest optimizer produced no acceptable candidates and failures are dominated by trade generation or trade-count issues.", "commands": ["GET /api/backtest/diagnose?source=bybit&symbol=BTCUSDT&timeframe=1h&period=365d&limit=auto"]}
        return {"action": "TUNE_QUALITY_POLICY", "reason": "Latest optimizer produced no acceptable candidates; inspect quality thresholds and rejection reasons manually.", "commands": ["GET /api/strategy-optimize?source=bybit&symbol=BTCUSDT&timeframe=1h&period=365d&limit=auto&max_combos=20"]}
    if readiness.get("safeForManualReview") and best_candidate:
        return {"action": "MANUAL_PROMOTE_REVIEW", "reason": "A backend-valid saved candidate is available for manual review. Paper remains disabled until explicitly enabled.", "commands": ["GET /api/research/best-candidate", "GET /api/candidate/validate"]}
    if not best_candidate:
        return {"action": "RUN_LEARNING", "reason": "No valid saved candidate is available yet.", "commands": commands + ["POST /api/learning/run"]}
    return {"action": "NO_ACTION", "reason": "No safe manual action is required right now.", "commands": []}


def learning_audit_summary_warnings(
    latest_learning: dict | None,
    latest_optimization: dict | None,
    optimizer_quality: dict,
    zero_trade: dict,
    readiness: dict,
    best_candidate: dict | None = None,
    latest_recommendation_candidate: dict | None = None,
) -> list[str]:
    warnings = []
    if latest_learning and latest_learning.get("errors"):
        warnings.append("Latest learning report contains errors.")
    if not latest_optimization:
        warnings.append("No optimization research run is saved yet.")
    if optimizer_quality.get("latestSelectedStatus") == "NONE":
        if candidate_passes_quality(latest_recommendation_candidate):
            warnings.append("Latest optimizer run failed, but latest learning report contains a PASS recommendation candidate. Review the recommended candidate before tuning global policy.")
        else:
            warnings.append("Latest optimizer run has no acceptable PASS/WARN candidate.")
    if zero_trade.get("hasZeroTradeProblem"):
        warnings.append("Zero-trade or low-trade optimizer candidates were detected.")
    if not readiness.get("safeForManualReview"):
        warnings.append("No candidate is currently marked safe for manual promotion review.")
    warnings.extend(candidate_robustness_warnings(best_candidate))
    warnings.append("Audit summary is advisory only; it never promotes candidates, enables paper simulation, or trades.")
    return dedupe_list(warnings)


def learning_trust_rules() -> dict:
    return {
        "minReports": 3,
        "minRepeatedRecommendations": 2,
        "minTrades": 20,
        "minProfitFactor": 1.1,
        "maxDrawdown": 25,
        "maxChurn": 0.6,
        "paperHealthCannotBe": ["FAILED"],
        "severeWarningsRejected": ["overfit", "audit failed", "zero-trade"],
    }


def learning_candidate_stability(reports: list[dict]) -> dict:
    keys = [learning_recommendation_key((report.get("recommendation") or {}).get("candidate")) for report in reports]
    candidate_keys = [key for key in keys if key]
    counts = {}
    for key in candidate_keys:
        counts[key] = counts.get(key, 0) + 1
    top_key = max(counts, key=counts.get) if counts else None
    changes = sum(1 for previous, current in zip(candidate_keys, candidate_keys[1:]) if previous != current)
    churn = (changes / max(1, len(candidate_keys) - 1)) if len(candidate_keys) > 1 else 0
    return {
        "reportsChecked": len(reports),
        "recommendationsWithCandidates": len(candidate_keys),
        "topCandidateKey": top_key,
        "topCandidateCount": counts.get(top_key, 0) if top_key else 0,
        "uniqueCandidates": len(counts),
        "recommendationChurn": round(churn, 4),
        "unstable": churn > learning_trust_rules()["maxChurn"] or len(counts) > max(2, len(candidate_keys) // 2 + 1),
        "counts": counts,
    }


def learning_recommendation_key(candidate: dict | None) -> str | None:
    if not candidate:
        return None
    params = candidate.get("params") or {}
    params_key = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return "|".join([
        str(candidate.get("strategy") or candidate.get("preset") or ""),
        str(candidate.get("symbol") or ""),
        str(candidate.get("timeframe") or ""),
        params_key,
    ])


def learning_score_trend(reports: list[dict]) -> dict:
    values = []
    for report in reports:
        candidate = report.get("bestSavedCandidate") or (report.get("recommendation") or {}).get("candidate") or {}
        if candidate.get("score") is not None:
            values.append(safe_float(candidate.get("score")))
    if len(values) < 2:
        direction = "insufficient"
        delta = 0
    else:
        delta = values[-1] - values[0]
        if abs(delta) < max(1, abs(values[0]) * 0.05):
            direction = "flat"
        elif delta > 0:
            direction = "improving"
        else:
            direction = "degrading"
    volatility = 0
    if values:
        mean = sum(values) / len(values)
        volatility = sum(abs(value - mean) for value in values) / len(values)
    return {
        "scores": values,
        "latestScore": values[-1] if values else None,
        "previousScore": values[-2] if len(values) >= 2 else None,
        "delta": round(delta, 4) if values else 0,
        "direction": "unstable" if volatility > 25 and len(values) >= 3 else direction,
        "volatility": round(volatility, 4),
    }


def learning_robustness_score(stability: dict, trend: dict, paper_health: dict, candidate: dict | None, report_count: int) -> float:
    score = 0.0
    candidate = candidate or {}
    repeated = stability.get("topCandidateCount", 0)
    score += min(30, repeated * 12)
    score -= safe_float(stability.get("recommendationChurn")) * 25
    score += max(0, safe_float(candidate.get("profitFactor")) - 1) * 18
    score += min(20, safe_float(candidate.get("trades")) / 5)
    score -= safe_float(candidate.get("maxDrawdown")) * 0.8
    if paper_health.get("status") == "HEALTHY":
        score += 15
    elif paper_health.get("status") in {"WATCH", "DEGRADED"}:
        score -= 10
    elif paper_health.get("status") == "FAILED":
        score -= 35
    warnings = " ".join(candidate.get("warnings", []) or []).lower()
    if "overfit" in warnings:
        score -= 20
    if "zero-trade" in warnings:
        score -= 20
    if candidate_has_robustness_blockers(candidate):
        score -= 35
    if trend.get("direction") == "improving":
        score += 8
    elif trend.get("direction") == "degrading":
        score -= 8
    elif trend.get("direction") == "unstable":
        score -= 12
    if report_count < learning_trust_rules()["minReports"]:
        score -= 25
    return round(max(0, min(100, score)), 4)


def learning_audit_warnings(reports: list[dict], stability: dict, trend: dict, paper_health: dict, candidate: dict | None) -> list[str]:
    rules = learning_trust_rules()
    warnings = []
    candidate = candidate or {}
    if len(reports) < rules["minReports"]:
        warnings.append(f"Only {len(reports)} learning reports exist; need at least {rules['minReports']}.")
    if stability.get("topCandidateCount", 0) < rules["minRepeatedRecommendations"]:
        warnings.append("No candidate has repeated enough across recent learning reports.")
    if stability.get("unstable"):
        warnings.append("Recommendation churn is too high.")
    if trend.get("direction") in {"degrading", "unstable"}:
        warnings.append(f"Best-candidate score trend is {trend.get('direction')}.")
    if not candidate:
        warnings.append("No valid saved candidate is available.")
        return warnings
    if safe_float(candidate.get("trades")) < rules["minTrades"]:
        warnings.append(f"Best candidate has too few trades ({candidate.get('trades')} < {rules['minTrades']}).")
    if safe_float(candidate.get("profitFactor")) < rules["minProfitFactor"]:
        warnings.append(f"Best candidate profit factor is below threshold ({candidate.get('profitFactor')} < {rules['minProfitFactor']}).")
    if safe_float(candidate.get("maxDrawdown")) > rules["maxDrawdown"]:
        warnings.append(f"Best candidate drawdown is above threshold ({candidate.get('maxDrawdown')} > {rules['maxDrawdown']}).")
    warnings.extend(candidate_robustness_warnings(candidate))
    if paper_health.get("status") == "FAILED":
        warnings.append("Paper health is FAILED.")
    candidate_warnings = " ".join(candidate.get("warnings", []) or []).lower()
    if any(term in candidate_warnings for term in rules["severeWarningsRejected"]):
        warnings.append("Best candidate has severe warning text.")
    return warnings


def learning_audit_status(score: float, warnings: list[str], stability: dict, paper_health: dict, candidate: dict | None, report_count: int) -> tuple[str, dict]:
    if not candidate or report_count == 0:
        return "NOT_READY", {"action": "KEEP_MANUAL_ONLY", "reason": "No learning evidence is available yet."}
    if report_count < learning_trust_rules()["minReports"] or stability.get("unstable"):
        return "WATCH", {"action": "OBSERVE_MORE", "reason": "More stable learning reports are needed before trusting recommendations."}
    if warnings:
        return "NOT_READY", {"action": "KEEP_MANUAL_ONLY", "reason": "Trust rules are not satisfied."}
    if score >= 75 and paper_health.get("status") != "FAILED":
        return "READY_FOR_AUTO_PROMOTE_LATER", {"action": "CONSIDER_AUTO_PROMOTE_LATER", "reason": "Recommendation appears stable enough for a future auto-promotion design, but this phase remains manual."}
    return "READY_FOR_MANUAL", {"action": "KEEP_MANUAL_ONLY", "reason": "Evidence supports manual review only."}


def evaluate_auto_promotion(config: dict, audit: dict, latest_report: dict | None, current_candidate: dict) -> dict:
    config = safe_learning_config(config)
    rules = config.get("autoPromoteRules", {})
    candidate = ((latest_report or {}).get("recommendation") or {}).get("candidate") or audit.get("summary", {}).get("bestSavedCandidate")
    checks = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("autoPromote enabled", bool(config.get("autoPromote")), "autoPromote must be true.")
    check("mode candidate_only", config.get("autoPromoteMode") == "candidate_only", "Only candidate_only mode is supported.")
    check("autoEnablePaper disabled", not bool(config.get("autoEnablePaper")), "autoEnablePaper is intentionally blocked.")
    check("candidate exists", bool(candidate), "A recommended candidate is required.")
    check("audit status", audit.get("status") == rules["requireAuditStatus"], f"Audit status {audit.get('status')} must equal {rules['requireAuditStatus']}.")
    check("learning reports", safe_float(audit.get("summary", {}).get("learningReports")) >= rules["minLearningReports"], f"Need at least {rules['minLearningReports']} learning reports.")
    stability = audit.get("candidateStability", {})
    check("repeated recommendation", safe_float(stability.get("topCandidateCount")) >= rules["minRepeatedRecommendations"], f"Need candidate repeated at least {rules['minRepeatedRecommendations']} times.")
    check("recommendation churn", safe_float(stability.get("recommendationChurn")) <= rules["maxRecommendationChurn"], f"Churn must be <= {rules['maxRecommendationChurn']}.")
    check("robustness score", safe_float(audit.get("summary", {}).get("robustnessScore")) >= rules["minRobustnessScore"], f"Robustness must be >= {rules['minRobustnessScore']}.")
    if candidate:
        check("trade count", safe_float(candidate.get("trades")) >= rules["minTrades"], f"Trades must be >= {rules['minTrades']}.")
        check("profit factor", safe_float(candidate.get("profitFactor")) >= rules["minProfitFactor"], f"Profit factor must be >= {rules['minProfitFactor']}.")
        check("drawdown", safe_float(candidate.get("maxDrawdown")) <= rules["maxDrawdown"], f"Drawdown must be <= {rules['maxDrawdown']}.")
        warning_text = " ".join(candidate.get("warnings", []) or []).lower()
        severe = any(term in warning_text for term in ("overfit", "audit failed", "zero-trade"))
        check("severe warning", not (rules["rejectIfSevereOverfitWarning"] and severe), "Candidate must not include severe warning text.")
    paper_failed = audit.get("paperHealth", {}).get("status") == "FAILED"
    check("paper health", not (rules["rejectIfPaperHealthFailed"] and paper_failed), "Paper health must not be FAILED.")
    current_score = current_candidate_score(candidate_summary(current_candidate)) or 0
    candidate_score = safe_float((candidate or {}).get("score"))
    check("better than current", candidate_score >= current_score + rules["requireCandidateBetterThanCurrentBy"], f"Candidate score must beat current by {rules['requireCandidateBetterThanCurrentBy']}.")

    allowed = all(item["passed"] for item in checks)
    reason = "All auto-promotion checks passed." if allowed else next((item["detail"] for item in checks if not item["passed"]), "Auto-promotion rejected.")
    return {"allowed": allowed, "reason": reason, "candidate": candidate if allowed else candidate, "checks": checks}


def auto_promote_candidate_if_allowed(config: dict, learning_report: dict | None, decision_source: str = "learning_run") -> dict:
    attempted = bool(safe_learning_config(config).get("autoPromote"))
    audit = build_learning_quality_audit(extra_report=learning_report)
    current = load_paper_candidate_config()
    evaluation = evaluate_auto_promotion(config, audit, learning_report, current)
    result = {
        "attempted": attempted,
        "promoted": False,
        "reason": evaluation["reason"],
        "candidate": evaluation.get("candidate"),
        "checks": evaluation.get("checks", []),
    }
    if not attempted or not evaluation.get("allowed"):
        append_learning_decision_from_context(
            source=decision_source,
            action="REJECT_AUTO_PROMOTE",
            reason=evaluation["reason"],
            candidate=evaluation.get("candidate"),
            audit=audit,
            checks=evaluation.get("checks", []),
            report_id=(learning_report or {}).get("id"),
        )
        return result
    candidate = evaluation.get("candidate") or {}
    payload = {
        "source": candidate.get("source", "bybit"),
        "symbol": candidate.get("symbol"),
        "timeframe": candidate.get("timeframe"),
        "preset": candidate.get("preset") or candidate.get("strategy"),
        "strategy": candidate.get("strategy"),
        "period": candidate.get("period"),
        "params": candidate.get("params", {}),
        "rankingSnapshot": {
            "valid": candidate.get("valid", True),
            "rank": candidate.get("rank"),
            "score": candidate.get("score"),
            "totalReturnPct": candidate.get("totalReturnPct"),
            "winRate": candidate.get("winRate"),
            "maxDrawdown": candidate.get("maxDrawdown"),
            "profitFactor": candidate.get("profitFactor"),
            "trades": candidate.get("trades"),
        },
        "optimizationSnapshot": {
            "researchRunId": candidate.get("researchRunId"),
            "score": candidate.get("score"),
            "train": candidate.get("train"),
            "test": candidate.get("test"),
            "full": candidate.get("full"),
            "warnings": candidate.get("warnings"),
            "autoPromotedFromLearningReport": (learning_report or {}).get("id"),
        } if candidate.get("origin") == "optimization" else None,
    }
    backup_path = backup_candidate_config(current)
    updated = merge_promoted_candidate(current, payload, payload["rankingSnapshot"], str(candidate.get("symbol") or ""), str(candidate.get("timeframe") or ""))
    updated.update({
        "enabled": False,
        "autoPromoted": True,
        "autoPromotedAt": datetime.now(timezone.utc).isoformat(),
        "autoPromotedFromLearningReport": (learning_report or {}).get("id"),
        "autoPromoteChecks": evaluation.get("checks", []),
    })
    write_candidate_config(updated)
    result.update({
        "promoted": True,
        "reason": "Candidate auto-promoted into paper config. Paper simulation remains disabled.",
        "backupPath": str(backup_path.relative_to(app.root_path)),
        "candidate": candidate_summary(updated),
    })
    append_learning_decision_from_context(
        source=decision_source,
        action="AUTO_PROMOTED",
        reason=result["reason"],
        candidate=result.get("candidate"),
        audit=audit,
        checks=evaluation.get("checks", []),
        report_id=(learning_report or {}).get("id"),
        promoted=True,
    )
    return result


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
    formula_review_strategies = [
        strategy for strategy in config.get("optimizationStrategies", [])
        if strategy in LEARNING_FORMULA_REVIEW_STRATEGIES or NODE_STRATEGIES.get(strategy) in LEARNING_FORMULA_REVIEW_STRATEGIES
    ]
    if formula_review_strategies:
        report["warnings"].append({
            "type": "learning_strategy_formula_review",
            "strategies": formula_review_strategies,
            "message": "Recent diagnostics found these strategy families generated zero trades on ready BTC/ETH 1h/4h data; review formulas or default regime behavior before re-adding them to learning.",
        })
    report["dataReadiness"] = research_data_readiness(config["source"], config["symbols"], config["timeframes"], config["period"], config["rankingLimit"])
    report["warnings"].extend(readiness_warnings(report["dataReadiness"]))

    try:
        ranking_limit = research_limit_for(config["source"], config["timeframes"][0] if config["timeframes"] else "1h", config["period"], config["rankingLimit"])
        ranking_payload = run_strategy_ranking_payload(
            config["source"],
            config["symbols"],
            config["timeframes"],
            config["rankingPresets"],
            config["period"],
            ranking_limit,
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
        zero_rows = [row for row in ranking_payload.get("rows", []) if int(safe_float(row.get("trades"))) == 0]
        if zero_rows:
            sample = zero_rows[0]
            diag = sample.get("tradeGenerationDiagnostics") or {}
            report["warnings"].append({
                "type": "zero_trade_ranking_rows",
                "count": len(zero_rows),
                "sample": {
                    "strategy": sample.get("strategy"),
                    "symbol": sample.get("symbol"),
                    "timeframe": sample.get("timeframe"),
                    "likelyReason": (diag.get("summary") or {}).get("likelyReason") or "No trades generated.",
                },
            })
    except Exception as exc:
        report["errors"].append({"stage": "ranking", "error": str(exc)})

    optimization_runs = 0
    for strategy in config["optimizationStrategies"]:
        for symbol in config["symbols"]:
            for timeframe in config["timeframes"]:
                optimization_runs += 1
                try:
                    optimization_limit = research_limit_for(config["source"], timeframe, config["period"], config["optimizationLimit"], require_number=True)
                    payload = run_strategy_optimization_payload(
                        config["source"],
                        symbol,
                        timeframe,
                        config["period"],
                        strategy,
                        optimization_limit,
                        config["maxOptimizationCombos"],
                        0.7,
                        config["feePct"],
                        config["slippagePct"],
                        save_run=True,
                    )
                    if payload.get("researchRunId"):
                        report["optimizationRunIds"].append(payload["researchRunId"])
                    grid_meta = payload.get("optimizerGrid") or {}
                    grid_audit = payload.get("gridAudit") or {}
                    if grid_meta:
                        report["warnings"].append({
                            "type": "optimizer_grid",
                            "strategy": strategy,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "gridName": grid_meta.get("gridName"),
                            "candidateCountTested": grid_meta.get("candidateCountTested"),
                            "fallbackUsed": grid_meta.get("fallbackUsed"),
                        })
                    if grid_audit:
                        report["warnings"].append({
                            "type": "optimizer_grid_audit",
                            "strategy": strategy,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "diagnosis": grid_audit.get("diagnosis"),
                            "dominantReasons": grid_audit.get("dominantReasons", [])[:3],
                            "suggestedChanges": grid_audit.get("suggestedChanges", [])[:3],
                        })
                    if payload.get("allZeroTradeCandidates"):
                        report["warnings"].append({
                            "type": "zero_trade_optimizer_result",
                            "strategy": strategy,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "zeroTradeSummary": payload.get("zeroTradeSummary"),
                        })
                    quality_summary = payload.get("qualitySummary") or {}
                    if quality_summary:
                        report["warnings"].append({
                            "type": "optimizer_quality",
                            "strategy": strategy,
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "selectedStatus": quality_summary.get("selectedStatus"),
                            "passCandidates": quality_summary.get("passCandidates"),
                            "warnCandidates": quality_summary.get("warnCandidates"),
                            "failCandidates": quality_summary.get("failCandidates"),
                            "topRejectionReasons": quality_summary.get("topRejectionReasons", [])[:3],
                        })
                        if quality_summary.get("selectedStatus") == "NONE":
                            report["warnings"].append({
                                "type": "optimizer_no_acceptable_candidate",
                                "strategy": strategy,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "message": "Optimizer returned no PASS/WARN candidate after quality filtering.",
                            })
                except Exception as exc:
                    report["errors"].append({"stage": "optimization", "strategy": strategy, "symbol": symbol, "timeframe": timeframe, "error": str(exc)})
                    if "0 trades" in str(exc).lower() or "zero" in str(exc).lower():
                        try:
                            diag_payload = run_backtest_diagnosis_payload(
                                config["source"],
                                symbol,
                                timeframe,
                                config["period"],
                                strategy,
                                config["feePct"],
                                config["slippagePct"],
                                config["optimizationLimit"],
                                config["allowShorts"],
                            )
                            report["warnings"].append({
                                "type": "zero_trade_optimization",
                                "strategy": strategy,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "likelyReason": diag_payload.get("summary", {}).get("likelyReason"),
                                "confidence": diag_payload.get("summary", {}).get("confidence"),
                            })
                        except Exception as diag_exc:
                            report["warnings"].append({
                                "type": "zero_trade_optimization",
                                "strategy": strategy,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "likelyReason": f"Optimizer produced zero trades; diagnostics failed: {diag_exc}",
                                "confidence": "LOW",
                            })

    if optimization_runs == 0:
        report["warnings"].append("No optimization strategies were configured.")

    report["candidateHealth"] = build_candidate_health(candidate_health_rules({}))["health"]
    report["bestSavedCandidate"] = best_saved_candidate(load_research_runs())
    report["replacementSuggestion"] = replacement_suggestion_from_health(report["candidateHealth"])
    report["recommendation"] = learning_recommendation(report["candidateHealth"], report["bestSavedCandidate"], load_paper_candidate_config())
    report["autoPromotion"] = auto_promote_candidate_if_allowed(config, report)
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
        "autoPromote": bool(config.get("autoPromote", False)),
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
    manual_only = "Candidate is available for manual inspection only; audit status must be READY before promotion."
    if candidate_has_robustness_blockers(best_candidate):
        manual_only = "Candidate has robustness warnings and is available for research inspection only; audit status blocks promotion."
    if not has_current:
        return {"action": "PROMOTE_CANDIDATE", "reason": f"No promoted candidate with an expected baseline exists. {manual_only}", "candidate": best_candidate}
    current_score = current_candidate_score(candidate_summary(current_candidate))
    best_score = safe_float(best_candidate.get("score"))
    if health_status == "HEALTHY":
        if current_score is not None and best_score > current_score * 1.2:
            return {"action": "PROMOTE_CANDIDATE", "reason": f"Current health is healthy, but saved candidate score is significantly better. {manual_only}", "candidate": best_candidate}
        return {"action": "KEEP_CURRENT", "reason": "Current paper candidate health is aligned with expectations.", "candidate": None}
    if health_status == "UNKNOWN":
        return {"action": "WAIT_FOR_MORE_PAPER_DATA", "reason": "Candidate health is unknown; wait for more paper trades before replacement unless manually chosen.", "candidate": None}
    return {"action": "PROMOTE_CANDIDATE", "reason": f"Current health is {health_status}; a saved candidate is available. {manual_only}", "candidate": best_candidate}


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
        "dataReadiness": payload.get("dataReadiness"),
        "partialData": bool(payload.get("summary", {}).get("partialDataRows")),
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
        "dataReadiness": payload.get("dataReadiness"),
        "partialData": payload.get("partialData"),
        "optimizerGrid": payload.get("optimizerGrid"),
        "gridAudit": payload.get("gridAudit"),
        "zeroTradeSummary": payload.get("zeroTradeSummary"),
        "allZeroTradeCandidates": payload.get("allZeroTradeCandidates"),
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
        "valid": row.get("valid", False) and row.get("qualityStatus", "PASS") != "FAIL",
        "qualityStatus": row.get("qualityStatus"),
        "rejectionReasons": row.get("rejectionReasons", []),
        "qualityMetrics": row.get("qualityMetrics", {}),
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
        "valid": row.get("valid", False) and row.get("qualityStatus", "PASS") != "FAIL",
        "qualityStatus": row.get("qualityStatus"),
        "rejectionReasons": row.get("rejectionReasons", []),
        "qualityMetrics": row.get("qualityMetrics", {}),
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
                if candidate.get("origin") == "optimization" and candidate.get("qualityStatus") not in (None, "PASS"):
                    continue
                if candidate.get("origin") == "optimization" and candidate_has_robustness_blockers(candidate):
                    continue
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


def candidate_config_warnings(candidate: dict) -> list[str]:
    warnings = []
    params = candidate.get("params") if isinstance(candidate.get("params"), dict) else {}
    top_regime = candidate.get("regimeMode")
    param_regime = params.get("regimeMode")
    if top_regime and param_regime and top_regime != param_regime:
        warnings.append(f"Top-level regimeMode ({top_regime}) conflicts with params.regimeMode ({param_regime}); params.regimeMode is canonical.")
    active_symbols = candidate.get("activeSymbols") if isinstance(candidate.get("activeSymbols"), list) else None
    symbols_active = candidate_symbols_by_mode(candidate, "active")
    if active_symbols is not None and active_symbols != symbols_active:
        warnings.append("activeSymbols does not match active entries in symbols.")
    if candidate.get("promotedAt") and candidate.get("enabled") and not candidate.get("enabledAt"):
        warnings.append("Promoted candidate is enabled without an enabledAt audit marker.")
    if not isinstance(candidate.get("promotedFromOptimization"), dict):
        warnings.append("promotedFromOptimization baseline is missing.")
    if not isinstance(candidate.get("promotedFromRanking"), dict):
        warnings.append("promotedFromRanking baseline is missing.")
    expected = expected_metrics_from_candidate(candidate)
    if safe_float(expected.get("trades")) <= 0:
        warnings.append("Expected baseline trades are missing.")
    if safe_float(expected.get("profitFactor")) <= 0:
        warnings.append("Expected baseline profitFactor is missing.")
    return dedupe_list(warnings)


def paper_readiness_check(checks: list[dict], name: str, passed: bool, severity: str, detail: str) -> None:
    checks.append({
        "name": name,
        "pass": bool(passed),
        "severity": severity,
        "detail": detail,
    })


def paper_real_trading_enabled() -> tuple[bool, str]:
    enabled_flags = [
        "ZGUA_REAL_TRADING_ENABLED",
        "ZGUA_LIVE_TRADING_ENABLED",
        "REAL_TRADING_ENABLED",
        "LIVE_TRADING_ENABLED",
    ]
    enabled = [key for key in enabled_flags if str(os.environ.get(key, "")).lower() in {"1", "true", "yes", "on"}]
    if enabled:
        return True, f"Real trading flag(s) enabled: {', '.join(enabled)}."
    return False, "No real-trading mode or order-execution API path is enabled."


def package_script_command(script_name: str) -> str:
    package = read_json_file(os.path.join(app.root_path, "package.json"), {})
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    if script_name not in scripts:
        raise ValueError(f"package.json script {script_name} is missing.")
    return f"npm run {script_name}"


def package_node_script_args(script_name: str) -> list[str]:
    package = read_json_file(os.path.join(app.root_path, "package.json"), {})
    script = ((package.get("scripts") or {}).get(script_name) or "").strip()
    parts = script.split()
    if len(parts) < 2 or parts[0] != "node":
        raise ValueError(f"package.json script {script_name} is not a direct node command.")
    return [node_executable()] + parts[1:]


def paper_market_key(market: dict) -> str:
    symbol = market.get("symbol")
    interval = market.get("interval") or market.get("timeframe")
    return f"{symbol}:{interval}" if symbol and interval else ""


def event_timestamp(event: dict) -> str | None:
    return event.get("processedAt") or event.get("timestamp") or event.get("time")


def parse_iso_timestamp(timestamp: str | None):
    if not timestamp:
        return None
    try:
        normalized = str(timestamp).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def compact_journal_event(event: dict) -> dict:
    return {
        "eventType": event.get("eventType"),
        "reason": event.get("reason"),
        "marketKey": event.get("marketKey") or paper_market_key(event),
        "symbol": event.get("symbol"),
        "interval": event.get("interval") or event.get("timeframe"),
        "processedAt": event_timestamp(event),
    }


def warning_market_key(warning: dict) -> str | None:
    key = warning.get("marketKey")
    if key:
        return key
    symbol = warning.get("symbol")
    interval = warning.get("interval") or warning.get("timeframe")
    if symbol and interval:
        return f"{symbol}:{interval}"
    reason = str(warning.get("reason") or "")
    prefix = reason.split(":", 1)[0].strip()
    parts = prefix.split()
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    return None


def paper_runtime_warning_buckets(state: dict, journal: list[dict], initialized_markets: set[str], active_market_keys: set[str] | None = None, watch_market_keys: set[str] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    updated_at = parse_iso_timestamp(state.get("updatedAt"))
    active_market_keys = active_market_keys or set()
    watch_market_keys = watch_market_keys or set()
    warning_events = [
        event for event in journal
        if str(event.get("eventType", "")).upper() in {"WARNING", "ERROR"} or event.get("reason")
    ]
    stale = []
    recent = []
    for event in warning_events:
        event_time = parse_iso_timestamp(event_timestamp(event))
        market_key = event.get("marketKey") or paper_market_key(event)
        reason = str(event.get("reason") or "")
        old_by_state = bool(updated_at and event_time and event_time < updated_at)
        old_by_age = bool(event_time and (now - event_time) > timedelta(hours=24))
        stale_init_warning = "Market not initialized" in reason and market_key in initialized_markets
        compact = compact_journal_event(event)
        compact["marketKey"] = warning_market_key(compact) or market_key
        if old_by_state or old_by_age or stale_init_warning:
            stale.append(compact)
        else:
            recent.append(compact)
    state_warnings = [
        {
            "eventType": "STATE_WARNING",
            "reason": warning,
            "marketKey": warning_market_key({"reason": warning}),
            "processedAt": state.get("updatedAt"),
        }
        for warning in (state.get("warnings") or [])
    ]
    current = recent + state_warnings
    active_warnings = [warning for warning in current if warning_market_key(warning) in active_market_keys]
    watch_warnings = [warning for warning in current if warning_market_key(warning) in watch_market_keys]
    unknown_warnings = [warning for warning in current if warning_market_key(warning) not in active_market_keys and warning_market_key(warning) not in watch_market_keys]
    stale_watch = [
        warning for warning in watch_warnings
        if "stale" in str(warning.get("reason", "")).lower()
    ]
    blocking = [
        warning for warning in active_warnings + unknown_warnings
        if str(warning.get("eventType", "")).upper() == "ERROR" or warning.get("reason")
    ]
    return {
        "staleWarnings": stale[-25:],
        "recentWarnings": current[-25:],
        "activeWarnings": active_warnings[-25:],
        "watchWarnings": watch_warnings[-25:],
        "staleWatchWarnings": stale_watch[-25:],
        "blockingWarnings": blocking[-25:],
        "informationalWarnings": watch_warnings[-25:],
    }


def latest_journal_event(journal: list[dict], types: set[str] | None = None) -> dict | None:
    for event in reversed(journal):
        event_type = str(event.get("eventType", "")).upper()
        if types is None or event_type in types:
            return compact_journal_event(event)
    return None


def paper_session_window(candidate: dict) -> dict:
    enabled_at = candidate.get("enabledAt")
    disabled_at = candidate.get("disabledAt")
    enabled_dt = parse_iso_timestamp(enabled_at)
    disabled_dt = parse_iso_timestamp(disabled_at)
    if disabled_dt and enabled_dt and disabled_dt < enabled_dt:
        disabled_at = None
        disabled_dt = None
    if not enabled_dt:
        return {
            "sessionId": "no-paper-session",
            "startedAt": None,
            "endedAt": None,
            "started": None,
            "ended": None,
        }
    end_value = disabled_at if disabled_dt and not candidate.get("enabled") else None
    session_key = f"{candidate.get('strategy', 'paper')}|{enabled_at}|{end_value or 'running'}"
    return {
        "sessionId": str(uuid.uuid5(uuid.NAMESPACE_URL, session_key)),
        "startedAt": enabled_at,
        "endedAt": end_value,
        "started": enabled_dt,
        "ended": disabled_dt if end_value else None,
    }


def event_in_session(event: dict, session: dict) -> bool:
    started = session.get("started")
    if not started:
        return False
    timestamp = parse_iso_timestamp(event_timestamp(event))
    if not timestamp:
        return False
    ended = session.get("ended")
    return timestamp >= started and (not ended or timestamp <= ended)


def normalized_paper_event(event: dict, session: dict, initialized_markets: set[str] | None = None) -> dict:
    timestamp = event_timestamp(event)
    current_session = event_in_session(event, session)
    market_key = event.get("marketKey") or paper_market_key(event)
    reason = event.get("reason") or event.get("message") or ""
    event_dt = parse_iso_timestamp(timestamp)
    older_than_session = bool(session.get("started") and event_dt and event_dt < session["started"])
    stale_init_warning = bool(initialized_markets and "Market not initialized" in str(reason) and market_key in initialized_markets)
    return {
        "timestamp": timestamp,
        "eventType": event.get("eventType"),
        "symbol": event.get("symbol"),
        "interval": event.get("interval") or event.get("timeframe"),
        "marketKey": market_key,
        "reason": reason,
        "message": reason,
        "action": event.get("eventType"),
        "signalPrice": event.get("signalPrice"),
        "fillPrice": event.get("fillPrice"),
        "netPnl": event.get("netPnl"),
        "stale": bool(older_than_session or stale_init_warning),
        "currentSession": current_session,
    }


def session_filtered_equity_curve(state: dict, session: dict) -> list[dict]:
    started = session.get("started")
    ended = session.get("ended")
    if not started:
        return state.get("equityCurve", []) or []
    start_epoch = int(started.timestamp())
    end_epoch = int(ended.timestamp()) if ended else None
    return [
        point for point in (state.get("equityCurve", []) or [])
        if safe_float(point.get("time")) >= start_epoch and (end_epoch is None or safe_float(point.get("time")) <= end_epoch)
    ]


def paper_tick_bucket(timestamp: str | None) -> str | None:
    parsed = parse_iso_timestamp(timestamp)
    if not parsed:
        return None
    return parsed.replace(microsecond=0).isoformat()


def build_paper_baseline_comparison(candidate: dict, state: dict, session_events: list[dict]) -> dict:
    expected = expected_metrics_from_candidate(candidate)
    paper_entries = [event for event in session_events if str(event.get("eventType", "")).upper() == "ENTRY"]
    paper_exits = [event for event in session_events if str(event.get("eventType", "")).upper() == "EXIT"]
    account_equity = safe_float(candidate.get("accountEquity", state.get("accountEquity", 10000)), 10000)
    equity = safe_float(state.get("accountEquity", account_equity), account_equity)
    paper_return = round(((equity - account_equity) / account_equity * 100) if account_equity else 0, 4)
    available = bool(expected.get("source"))
    expected_trades = int(safe_float(expected.get("trades")))
    paper_trades = len(paper_exits)
    if not available:
        status = "TOO_EARLY"
    elif paper_trades < max(3, min(10, expected_trades // 5 if expected_trades else 3)):
        status = "TOO_EARLY"
    elif paper_return < safe_float(expected.get("totalReturnPct")) * 0.25:
        status = "UNDERPERFORMING"
    elif paper_return < safe_float(expected.get("totalReturnPct")) * 0.75:
        status = "WATCH"
    else:
        status = "OK"
    return {
        "available": available,
        "expectedProfitFactor": expected.get("profitFactor"),
        "expectedTrades": expected_trades,
        "expectedReturnPct": expected.get("totalReturnPct"),
        "paperTrades": paper_trades,
        "paperReturnPct": paper_return,
        "status": status,
    }


def paper_session_observation_status(paper_enabled: bool, real_enabled: bool, runtime: dict, stop_rules: dict, session_events: list[dict], session: dict) -> tuple[str, dict]:
    if real_enabled:
        return "STOP_RECOMMENDED", {"action": "DISABLE_REAL_TRADING_FLAG", "reason": "A real-trading flag is enabled; paper observation is blocked until it is disabled."}
    if not paper_enabled:
        return "DISABLED", {"action": "REVIEW_ENABLE_PAPER_SIMULATION", "reason": "Paper simulation is disabled. Enable manually only after readiness review."}
    if stop_rules.get("status") == "STOP_RECOMMENDED":
        return "STOP_RECOMMENDED", stop_rules.get("nextAction") or {"action": "REVIEW_STOP_RULES", "reason": "One or more stop rules recommend stopping paper simulation."}
    last_tick = runtime.get("lastTick") or {}
    updated_at = parse_iso_timestamp(last_tick.get("updatedAt"))
    if not session_events and (not updated_at or (session.get("started") and updated_at < session["started"])):
        return "WATCH_WAITING_FOR_FIRST_TICK", {"action": "RUN_ONE_PAPER_TICK", "reason": "Paper is enabled but no tick has been observed for this session."}
    if last_tick.get("stale"):
        return "WATCH_TICK_STALE", {"action": "RUN_ONE_PAPER_TICK", "reason": "Paper is enabled but the latest tick is stale."}
    return "RUNNING", {"action": "MONITOR_PAPER_SESSION", "reason": "Paper session has recent tick activity. Continue observing without enabling real trading."}


def first_baseline_value(*values, fallback=None):
    for value in values:
        if value is None:
            continue
        return value
    return fallback


def candidate_observation_baseline(candidate: dict) -> dict:
    optimization = candidate.get("promotedFromOptimization") if isinstance(candidate.get("promotedFromOptimization"), dict) else {}
    ranking = candidate.get("promotedFromRanking") if isinstance(candidate.get("promotedFromRanking"), dict) else {}
    test = optimization.get("test") if isinstance(optimization.get("test"), dict) else {}
    full = optimization.get("full") if isinstance(optimization.get("full"), dict) else {}
    quality = optimization.get("qualityMetrics") if isinstance(optimization.get("qualityMetrics"), dict) else {}
    expected_test_trades = int(safe_float(first_baseline_value(test.get("trades"), quality.get("testTrades"), ranking.get("trades")), 0))
    expected_full_trades = int(safe_float(first_baseline_value(full.get("trades"), quality.get("fullTrades"), optimization.get("trades")), 0))
    expected_profit_factor = safe_float(first_baseline_value(test.get("profitFactor"), quality.get("testProfitFactor"), full.get("profitFactor"), quality.get("fullProfitFactor"), ranking.get("profitFactor")), 0)
    expected_return = safe_float(first_baseline_value(test.get("totalReturn"), quality.get("testReturnPct"), full.get("totalReturn"), quality.get("fullReturnPct"), ranking.get("totalReturnPct")), 0)
    expected_drawdown = safe_float(first_baseline_value(test.get("maxDrawdown"), quality.get("testMaxDrawdownPct"), full.get("maxDrawdown"), quality.get("fullMaxDrawdownPct"), ranking.get("maxDrawdown")), 0)
    available = bool(optimization or ranking)
    return {
        "available": available,
        "source": "promotedFromOptimization" if optimization else "promotedFromRanking" if ranking else None,
        "expectedProfitFactor": expected_profit_factor or None,
        "expectedReturnPct": expected_return or None,
        "expectedTrades": expected_test_trades or expected_full_trades or int(safe_float(ranking.get("trades"), 0)) or None,
        "expectedMaxDrawdownPct": expected_drawdown or None,
        "expectedTestTrades": expected_test_trades or None,
        "expectedFullTrades": expected_full_trades or None,
        "expectedTestProfitFactor": safe_float(first_baseline_value(test.get("profitFactor"), quality.get("testProfitFactor")), 0) or None,
        "expectedFullProfitFactor": safe_float(first_baseline_value(full.get("profitFactor"), quality.get("fullProfitFactor")), 0) or None,
        "expectedTestReturnPct": safe_float(first_baseline_value(test.get("totalReturn"), quality.get("testReturnPct")), 0) or None,
        "expectedFullReturnPct": safe_float(first_baseline_value(full.get("totalReturn"), quality.get("fullReturnPct")), 0) or None,
        "expectedTestMaxDrawdownPct": safe_float(first_baseline_value(test.get("maxDrawdown"), quality.get("testMaxDrawdownPct")), 0) or None,
        "expectedFullMaxDrawdownPct": safe_float(first_baseline_value(full.get("maxDrawdown"), quality.get("fullMaxDrawdownPct")), 0) or None,
    }


def build_paper_observation_quality(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    journal = read_jsonl_tail(os.path.join(app.root_path, "reports", "paper-journal.jsonl"), 1000)
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    runtime = build_paper_runtime_status(args)
    stop_rules = build_paper_stop_rules(args)
    session_summary = build_paper_session_summary(args)
    activity = session_summary.get("activity") or {}
    session = session_summary.get("session") or {}
    if request_active_only(args):
        active_events = build_paper_session_events({**dict(args), "activeOnly": "true", "limit": "500"}).get("counts", {})
        active_trades_payload = build_paper_session_trades({**dict(args), "activeOnly": "true"})
        active_trades = active_trades_payload.get("totals", {})
        activity = {
            **activity,
            "signals": active_events.get("signals", 0),
            "entries": active_events.get("openTrades", 0),
            "exits": active_events.get("closeTrades", 0),
            "closedTrades": active_trades.get("closedTrades", 0),
            "openPositions": len(active_trades_payload.get("openTrades") or []),
        }
    performance = dict(session_summary.get("performance") or {})
    paper_metrics = paper_metrics_from_state(state, candidate, journal)
    baseline = candidate_observation_baseline(candidate)
    min_closed_trades = int(safe_float(args.get("min_closed_trades_for_judgment", 10), 10))
    min_signals = int(safe_float(args.get("min_signals_for_observation", 3), 3))
    min_duration = int(safe_float(args.get("min_duration_seconds_for_observation", 3600), 3600))
    ticks = int(safe_float(activity.get("ticks"), 0))
    signals = int(safe_float(activity.get("signals"), 0))
    entries = int(safe_float(activity.get("entries"), 0))
    exits = int(safe_float(activity.get("exits"), 0))
    closed_trades = int(safe_float(activity.get("closedTrades"), 0))
    open_positions = int(safe_float(activity.get("openPositions"), 0))
    duration = int(safe_float(session.get("durationSeconds"), 0))
    evidence = {
        "ticks": ticks,
        "signals": signals,
        "entries": entries,
        "exits": exits,
        "closedTrades": closed_trades,
        "openPositions": open_positions,
        "durationSeconds": duration,
        "enoughTime": duration >= min_duration,
        "enoughTrades": closed_trades >= min_closed_trades,
        "enoughSignals": signals >= min_signals,
        "thresholds": {
            "minClosedTradesForJudgment": min_closed_trades,
            "minSignalsForObservation": min_signals,
            "minDurationSecondsForObservation": min_duration,
        },
    }
    performance.update({
        "profitFactor": paper_metrics.get("profitFactor"),
        "winRate": paper_metrics.get("winRate"),
        "paperClosedTrades": paper_metrics.get("closedTrades"),
        "paperTotalReturnPct": paper_metrics.get("totalReturnPct"),
    })
    reasons = []
    warnings = []
    score = 0
    status = "TOO_EARLY"
    next_action = {
        "action": "CONTINUE_PAPER_OBSERVATION",
        "reason": "Continue collecting paper-only forward evidence. Do not enable real trading.",
    }
    runtime_health = ((runtime.get("health") or {}).get("status") or "UNKNOWN").upper()
    stop_status = str(stop_rules.get("status") or "UNKNOWN").upper()
    if not paper_enabled:
        status = "DISABLED"
        reasons.append("Paper simulation is disabled; no forward paper judgment is active.")
        next_action = {
            "action": "REVIEW_ENABLE_PAPER_SIMULATION",
            "reason": "Readiness may be reviewed manually, but this endpoint does not enable paper automatically.",
        }
    elif real_enabled:
        status = "PAUSE_RECOMMENDED"
        reasons.append(real_detail)
        warnings.append("Real trading must remain disabled while reviewing paper observation quality.")
        next_action = {"action": "DISABLE_REAL_TRADING_FLAG", "reason": real_detail}
    elif stop_status == "STOP_RECOMMENDED":
        status = "PAUSE_RECOMMENDED"
        score = 15
        reasons.append("Paper stop rules recommend stopping or pausing paper simulation.")
        warnings.extend([rule.get("detail") for rule in stop_rules.get("rules", []) if rule.get("severity") == "STOP" and not rule.get("pass") and rule.get("detail")])
        next_action = stop_rules.get("nextAction") or {"action": "REVIEW_STOP_RULES", "reason": "One or more stop rules failed."}
    elif runtime_health == "BLOCKED":
        status = "PAUSE_RECOMMENDED"
        score = 15
        reasons.extend((runtime.get("health") or {}).get("reasons") or ["Paper runtime health is blocked."])
        next_action = {"action": "REVIEW_RUNTIME_BLOCKERS", "reason": "Paper runtime health is BLOCKED."}
    elif ticks <= 0:
        status = "TOO_EARLY"
        score = 10
        reasons.append("Paper is enabled but no tick has been observed for this session.")
        next_action = {"action": "RUN_ONE_PAPER_TICK", "reason": "Collect at least one paper tick before judging observation quality."}
    elif closed_trades <= 0 and signals < min_signals:
        status = "TOO_EARLY"
        score = min(30, 15 + ticks * 5 + signals * 3)
        reasons.append(f"Only {ticks} tick(s), {signals} signal(s), and no closed paper trades are available.")
        next_action = {"action": "CONTINUE_PAPER_OBSERVATION", "reason": "Need more paper-only signals and closed trades before comparing to baseline."}
    else:
        score = min(70, 35 + min(ticks, 6) * 3 + min(signals, 10) * 2 + min(entries + exits, 10) * 2)
        status = "OBSERVE"
        reasons.append("Paper simulation is producing forward evidence, but judgment remains conservative.")
        if not evidence["enoughTrades"]:
            reasons.append(f"Closed trades are below the judgment threshold ({closed_trades}/{min_closed_trades}); profit factor is not compared strongly yet.")
        if runtime_health == "WATCH" or stop_status == "WATCH":
            status = "WATCH"
            score = min(score, 55)
            warnings.extend((runtime.get("health") or {}).get("reasons") or [])
            if stop_status == "WATCH":
                warnings.append("Paper stop rules are in WATCH status.")
        if evidence["enoughTrades"]:
            score = max(score, 72)
            paper_pf = safe_float(performance.get("profitFactor"), 0)
            paper_return = safe_float(performance.get("paperTotalReturnPct", performance.get("returnPct")), 0)
            paper_drawdown = safe_float(performance.get("maxDrawdownPct", paper_metrics.get("maxDrawdown")), 0)
            expected_pf = safe_float(baseline.get("expectedProfitFactor"), 0)
            expected_return = safe_float(baseline.get("expectedReturnPct"), 0)
            expected_drawdown = safe_float(baseline.get("expectedMaxDrawdownPct"), 0)
            if baseline.get("available") and expected_pf and paper_pf and paper_pf < expected_pf * 0.65:
                status = "WATCH"
                score = min(score, 55)
                warnings.append(f"Paper profit factor {paper_pf} is materially below baseline {expected_pf}.")
            if baseline.get("available") and expected_return and paper_return < expected_return * 0.25:
                status = "WATCH"
                score = min(score, 55)
                warnings.append(f"Paper return {round(paper_return, 4)}% is materially below baseline {expected_return}%.")
            if baseline.get("available") and expected_drawdown and paper_drawdown > max(expected_drawdown * 2, expected_drawdown + 5):
                status = "PAUSE_RECOMMENDED"
                score = min(score, 35)
                warnings.append(f"Paper drawdown {round(paper_drawdown, 4)}% is well above baseline {expected_drawdown}%.")
        next_action = {
            "action": "WATCH_PAPER_QUALITY" if status == "WATCH" else "REVIEW_BEFORE_CONTINUING_PAPER" if status == "PAUSE_RECOMMENDED" else "CONTINUE_PAPER_OBSERVATION",
            "reason": "Keep observing paper-only evidence; this endpoint never recommends real trading.",
        }
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "evidence": evidence,
        "performance": performance,
        "baseline": baseline,
        "quality": {
            "status": status,
            "score": int(max(0, min(100, score))),
            "reasons": dedupe_list([reason for reason in reasons if reason]),
            "warnings": dedupe_list([warning for warning in warnings if warning]),
        },
        "nextAction": next_action,
    }


def paper_observation_target_policy(args) -> dict:
    return {
        "minSessionHours": safe_float(args.get("min_session_hours", 72), 72),
        "minPaperTicks": int(safe_float(args.get("min_paper_ticks", 24), 24)),
        "minClosedTrades": int(safe_float(args.get("min_closed_trades", 10), 10)),
        "preferredClosedTrades": int(safe_float(args.get("preferred_closed_trades", 30), 30)),
        "minActiveMarketFreshnessStatus": ["READY", "WAIT_FOR_NEXT_CANDLE"],
        "maxStopRuleFailures": int(safe_float(args.get("max_stop_rule_failures", 0), 0)),
        "maxRuntimeActiveWarnings": int(safe_float(args.get("max_runtime_active_warnings", 0), 0)),
    }


def compact_observation_targets(targets: dict) -> dict:
    progress = targets.get("progress") or {}
    readiness = targets.get("readiness") or {}
    return {
        "status": targets.get("status"),
        "nextAction": targets.get("nextAction"),
        "blockingIssues": len(targets.get("blockingIssues") or []),
        "warnings": len(targets.get("warnings") or []),
        "progress": {
            "sessionAgeHours": progress.get("sessionAgeHours"),
            "ticksObserved": progress.get("ticksObserved"),
            "targetTicks": (targets.get("targets") or {}).get("minPaperTicks"),
            "closedTrades": progress.get("closedTrades"),
            "targetClosedTrades": (targets.get("targets") or {}).get("minClosedTrades"),
            "signalsObserved": progress.get("signalsObserved"),
            "activeWarningCount": progress.get("activeWarningCount"),
            "watchWarningCount": progress.get("watchWarningCount"),
        },
        "readiness": {
            "minimumTargetsMet": readiness.get("minimumTargetsMet"),
            "preferredTradesMet": readiness.get("preferredTradesMet"),
            "safeToReview": readiness.get("safeToReview"),
        },
        "observationCounters": targets.get("observationCounters"),
        "runnerTicksRun": targets.get("runnerTicksRun"),
        "runnerTicksSkipped": targets.get("runnerTicksSkipped"),
        "processedCandleDeltaTotal": targets.get("processedCandleDeltaTotal"),
        "counterConsistencyStatus": targets.get("counterConsistencyStatus"),
    }


def request_active_only(args) -> bool:
    return str(args.get("activeOnly", "false")).strip().lower() in {"1", "true", "yes", "on"}


def active_market_keys_for_candidate(candidate: dict) -> set[str]:
    return {paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "active") if paper_market_key(market)}


def primary_active_market(candidate: dict) -> dict:
    active = candidate_symbols_by_mode(candidate, "active")
    return active[0] if active else {}


def build_paper_observation_targets(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    journal = read_jsonl_tail(os.path.join(app.root_path, "reports", "paper-journal.jsonl"), 1000)
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    targets = paper_observation_target_policy(args)
    tick_readiness = build_paper_tick_readiness(args)
    runtime = build_paper_runtime_status(args)
    stop_rules = build_paper_stop_rules(args) if paper_enabled else {
        "status": "OK",
        "rules": [],
        "nextAction": {"action": "PAPER_DISABLED_NO_STOP_NEEDED", "reason": "Paper is disabled, so stop rules are informational only."},
    }
    session = paper_session_window(candidate)
    session_events = [event for event in journal if event_in_session(event, session)]
    if request_active_only(args):
        active_keys = active_market_keys_for_candidate(candidate)
        session_events = [event for event in session_events if (event.get("marketKey") or paper_market_key(event)) in active_keys]
    event_types = [str(event.get("eventType", "")).upper() for event in session_events]
    tick_times = {
        paper_tick_bucket(event_timestamp(event))
        for event in session_events
        if paper_tick_bucket(event_timestamp(event))
    }
    state_updated_at = parse_iso_timestamp(state.get("updatedAt"))
    if state_updated_at and session.get("started") and state_updated_at >= session["started"] and (not session.get("ended") or state_updated_at <= session["ended"]):
        bucket = paper_tick_bucket(state.get("updatedAt"))
        if bucket:
            tick_times.add(bucket)
    freshness_active = (tick_readiness.get("freshness") or {}).get("active") or []
    active = freshness_active[0] if freshness_active else {}
    tick_state = tick_readiness.get("tickReadiness") or {}
    runtime_journal = runtime.get("journal") or {}
    stop_failures = [
        rule for rule in stop_rules.get("rules", [])
        if not rule.get("pass") and rule.get("severity") == "STOP"
    ]
    warn_failures = [
        rule for rule in stop_rules.get("rules", [])
        if not rule.get("pass") and rule.get("severity") == "WARN"
    ]
    now = datetime.now(timezone.utc)
    started = session.get("started")
    ended = session.get("ended") or (None if not started else now)
    duration_seconds = int((ended - started).total_seconds()) if started and ended else 0
    session_age_hours = round(duration_seconds / 3600, 4)
    ticks = len(tick_times)
    if request_active_only(args):
        active_symbols = {market.get("symbol") for market in candidate_symbols_by_mode(candidate, "active") if market.get("symbol")}
        closed_trades = len([trade for trade in (state.get("closedTrades", []) or []) if not trade.get("symbol") or trade.get("symbol") in active_symbols])
        open_positions = len([position for position in (state.get("openPositions", []) or []) if not position.get("symbol") or position.get("symbol") in active_symbols])
    else:
        closed_trades = len(state.get("closedTrades", []) or [])
        open_positions = len(state.get("openPositions", []) or [])
    signals = event_types.count("SIGNAL")
    active_warning_count = int(safe_float(runtime_journal.get("activeWarningCount"), len(runtime_journal.get("activeWarnings") or [])))
    watch_warning_count = int(safe_float(runtime_journal.get("watchWarningCount"), len(runtime_journal.get("watchWarnings") or [])))
    stale_watch_warning_count = int(safe_float(runtime_journal.get("staleWatchWarningCount"), len(runtime_journal.get("staleWatchWarnings") or [])))
    if request_active_only(args):
        watch_warning_count = 0
        stale_watch_warning_count = 0
    freshness_status = tick_state.get("status") or "UNKNOWN"
    if not paper_enabled:
        quality_status = "DISABLED"
    elif closed_trades <= 0:
        quality_status = "TOO_EARLY"
    elif closed_trades < targets["minClosedTrades"]:
        quality_status = "OBSERVE"
    else:
        quality_status = "OBSERVE"
    stop_status = str(stop_rules.get("status") or "UNKNOWN").upper()

    meets_session = session_age_hours >= safe_float(targets.get("minSessionHours"), 0)
    meets_ticks = ticks >= int(safe_float(targets.get("minPaperTicks"), 0))
    meets_closed = closed_trades >= int(safe_float(targets.get("minClosedTrades"), 0))
    meets_preferred = closed_trades >= int(safe_float(targets.get("preferredClosedTrades"), 0))
    freshness_ok = freshness_status in set(targets.get("minActiveMarketFreshnessStatus") or [])
    stop_failures_ok = len(stop_failures) <= int(safe_float(targets.get("maxStopRuleFailures"), 0))
    active_warnings_ok = active_warning_count <= int(safe_float(targets.get("maxRuntimeActiveWarnings"), 0))

    blocking_issues = []
    warnings = []
    informational = []
    if real_enabled:
        blocking_issues.append(real_detail)
    if paper_enabled and stop_status == "STOP_RECOMMENDED":
        blocking_issues.append(f"{len(stop_failures)} stop-level paper rule(s) failed.")
    if paper_enabled and stop_status == "WATCH":
        warnings.append(f"{len(warn_failures)} warning-level paper rule(s) failed.")
    if paper_enabled and active_warning_count > targets["maxRuntimeActiveWarnings"]:
        blocking_issues.append(f"Active runtime warnings exceed policy ({active_warning_count}/{targets['maxRuntimeActiveWarnings']}).")
    if paper_enabled and freshness_status not in targets["minActiveMarketFreshnessStatus"]:
        warnings.append(f"Active-market tick readiness is {freshness_status}; target expects READY or WAIT_FOR_NEXT_CANDLE.")
    if watch_warning_count:
        informational.append(f"{watch_warning_count} watch-market warning(s) are informational and do not block active paper observation.")
    if stale_watch_warning_count:
        informational.append(f"{stale_watch_warning_count} stale watch-market warning(s) are separated from active-market blockers.")

    minimum_targets_met = meets_session and meets_ticks and meets_closed and freshness_ok and stop_failures_ok and active_warnings_ok
    evidence_exists = bool(ticks or signals or closed_trades or open_positions or session_age_hours > 0)
    if not paper_enabled:
        status = "DISABLED"
        next_action = {
            "action": "ENABLE_PAPER_SIMULATION",
            "reason": "Paper is disabled; enable it manually only after reviewing readiness. This endpoint never enables paper automatically.",
        }
    elif real_enabled or (stop_status == "STOP_RECOMMENDED") or not active_warnings_ok:
        status = "PAUSE_RECOMMENDED"
        next_action = {
            "action": "PAUSE_PAPER_SIMULATION",
            "reason": "Stop rules, real-trading safety, or active runtime warnings require review before more paper observation.",
        }
    elif warnings or stop_status == "WATCH" or quality_status == "WATCH":
        status = "WATCH"
        next_action = {
            "action": "OBSERVE_MORE" if evidence_exists else "RUN_PAPER_ONCE_WHEN_READY",
            "reason": "Some paper evidence exists, but warnings should be reviewed before judging the candidate.",
        }
    elif minimum_targets_met:
        status = "READY_FOR_PAPER_REVIEW"
        next_action = {
            "action": "REVIEW_PAPER_RESULTS",
            "reason": "Minimum forward paper targets are met with no active blockers. This is review-only and never recommends real trading.",
        }
    elif not evidence_exists or closed_trades <= 0:
        status = "TOO_EARLY"
        next_action_value = "WAIT_FOR_NEXT_CANDLE" if freshness_status == "WAIT_FOR_NEXT_CANDLE" and evidence_exists else "RUN_PAPER_ONCE_WHEN_READY"
        next_reason = "No closed paper trades are available yet; wait for the next closed active-market candle before another useful tick." if next_action_value == "WAIT_FOR_NEXT_CANDLE" else "No closed paper trades are available yet, so the forward evidence is still too early for judgment."
        next_action = {
            "action": next_action_value,
            "reason": next_reason,
        }
    elif freshness_status == "WAIT_FOR_NEXT_CANDLE":
        status = "OBSERVE_MORE"
        next_action = {
            "action": "WAIT_FOR_NEXT_CANDLE",
            "reason": "Continue observation after the next closed active-market candle becomes available.",
        }
    else:
        status = "OBSERVE_MORE"
        next_action = {
            "action": "OBSERVE_MORE",
            "reason": "Forward paper evidence is accumulating, but minimum observation targets are not met yet.",
        }

    progress = {
        "sessionStartedAt": session.get("startedAt"),
        "sessionAgeHours": session_age_hours,
        "ticksObserved": ticks,
        "closedTrades": closed_trades,
        "openPositions": open_positions,
        "signalsObserved": signals,
        "activeMarket": active.get("marketKey") or paper_market_key((candidate_symbols_by_mode(candidate, "active") or [{}])[0]),
        "latestClosedCandleTime": tick_state.get("latestClosedCandleTime") or active.get("latestClosedCandleAt"),
        "lastProcessedCandleTime": tick_state.get("lastProcessedCandleTime") or active.get("lastProcessedCandleAt"),
        "activeWarningCount": active_warning_count,
        "watchWarningCount": watch_warning_count,
        "staleWatchWarningCount": stale_watch_warning_count,
        "stopRulesStatus": stop_rules.get("status"),
        "observationQualityStatus": quality_status,
        "remainingSessionHours": round(max(0, targets["minSessionHours"] - session_age_hours), 4),
        "remainingPaperTicks": max(0, targets["minPaperTicks"] - ticks),
        "remainingClosedTrades": max(0, targets["minClosedTrades"] - closed_trades),
        "remainingPreferredClosedTrades": max(0, targets["preferredClosedTrades"] - closed_trades),
    }
    readiness = {
        "minimumTargetsMet": minimum_targets_met,
        "preferredTradesMet": meets_preferred,
        "safeToReview": status == "READY_FOR_PAPER_REVIEW",
        "meetsSessionHours": meets_session,
        "meetsPaperTicks": meets_ticks,
        "meetsClosedTrades": meets_closed,
        "activeMarketFreshnessOk": freshness_ok,
        "stopRuleFailuresOk": stop_failures_ok,
        "runtimeActiveWarningsOk": active_warnings_ok,
        "meaningfulEvidence": evidence_exists and (ticks > 0 or signals > 0 or closed_trades > 0),
    }
    if paper_enabled and not meets_session:
        warnings.append(f"Session age is below target ({session_age_hours}/{targets['minSessionHours']} hours).")
    if paper_enabled and not meets_ticks:
        warnings.append(f"Paper ticks are below target ({ticks}/{targets['minPaperTicks']}).")
    if paper_enabled and not meets_closed:
        warnings.append(f"Closed paper trades are below judgment target ({closed_trades}/{targets['minClosedTrades']}).")

    counter_args = {**dict(args), "activeOnly": "true" if request_active_only(args) else "false"}
    observation_counters = build_paper_observation_counters(counter_args)
    compact_counters = compact_observation_counters(observation_counters)
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "targets": targets,
        "progress": progress,
        "readiness": readiness,
        "status": status,
        "nextAction": next_action,
        "blockingIssues": dedupe_list([item for item in blocking_issues if item]),
        "warnings": dedupe_list([item for item in warnings if item]),
        "informationalWarnings": dedupe_list([item for item in informational if item]),
        "observationCounters": compact_counters,
        "runnerTicksRun": compact_counters.get("runnerTicksRun"),
        "runnerTicksSkipped": compact_counters.get("runnerTicksSkipped"),
        "processedCandleDeltaTotal": compact_counters.get("processedCandleDeltaTotal"),
        "counterConsistencyStatus": compact_counters.get("counterConsistencyStatus"),
    }


def requested_runner_log_path(args) -> tuple[Path, list[str]]:
    selection = resolve_runner_log_selection(args)
    return selection["path"], selection["warnings"]


def relative_app_path(path: Path) -> str:
    root = Path(app.root_path).resolve()
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(resolved)


def resolve_runner_log_selection(args) -> dict:
    warnings = []
    explicit = bool(args.get("logFile"))
    raw = str(args.get("logFile") or "reports/paper-runner-session.jsonl").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path(app.root_path) / path
    resolved = path.resolve()
    root = Path(app.root_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        warnings.append("Requested logFile is outside the app root; using the default runner log.")
        resolved = root / "reports" / "paper-runner-session.jsonl"
        explicit = False
    selected_by = "query" if explicit else "default"
    if not resolved.exists() and not explicit:
        candidates = sorted((root / "reports").glob("paper-runner-session*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates:
            resolved = candidates[0].resolve()
            selected_by = "latest"
            warnings.append(f"Default runner log was not found; using latest available runner log {relative_app_path(resolved)}.")
    return {
        "path": resolved,
        "selectedBy": selected_by,
        "availableLogs": [relative_app_path(item.resolve()) for item in sorted((root / "reports").glob("paper-runner-session*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)[:10]],
        "warnings": warnings,
    }


def runner_log_entries(args) -> tuple[dict, list[dict], int]:
    selection = resolve_runner_log_selection(args)
    limit = min(max(int(safe_float(args.get("limit", 500), 500)), 1), 5000)
    log_path = selection["path"]
    entries = read_jsonl_tail(str(log_path), limit) if log_path.exists() else []
    return selection, entries, limit


def runner_log_counter_summary(entries: list[dict]) -> dict:
    iterations = [entry for entry in entries if entry.get("type") == "iteration" or entry.get("iteration") is not None]
    summaries = [entry for entry in entries if entry.get("type") == "summary" or entry.get("iterationsAttempted") is not None]
    successful_iterations = [
        entry for entry in iterations
        if entry.get("ok") is not False and str(entry.get("runStatus", "")).upper() != "ERROR"
    ]
    return {
        "iterations": len(iterations),
        "summaries": len(summaries),
        "ticksRun": len([entry for entry in iterations if entry.get("tickRan")]),
        "ticksSkipped": len([entry for entry in iterations if not entry.get("tickRan")]),
        "errors": len([entry for entry in iterations if entry.get("ok") is False or str(entry.get("runStatus", "")).upper() == "ERROR"]),
        "processedCandleDeltaTotal": int(sum(safe_float(entry.get("processedCandlesDelta"), 0) for entry in successful_iterations if entry.get("processedCandlesDelta") is not None)),
        "refreshOk": len([entry for entry in iterations if entry.get("refreshStatus") == "OK" or entry.get("refreshOk") is True]),
        "refreshSkipped": len([entry for entry in iterations if entry.get("refreshStatus") == "SKIPPED" or entry.get("refreshAction") == "SKIPPED"]),
        "waitForNextCandle": len([entry for entry in iterations if entry.get("tickReadinessStatus") == "WAIT_FOR_NEXT_CANDLE" or runner_action_value(entry) == "WAIT_FOR_NEXT_CANDLE"]),
        "paperDisabled": len([entry for entry in iterations if entry.get("paperEnabled") is False or runner_action_value(entry) == "PAPER_DISABLED"]),
        "stopRuleBlocks": len([entry for entry in iterations if entry.get("stopRulesStatus") == "STOP_RECOMMENDED" or runner_action_value(entry) == "REVIEW_STOP_RULES"]),
    }


def current_session_journal_counters(candidate: dict, state: dict, active_only: bool) -> tuple[dict, dict]:
    records, session, active_keys, _watch_keys = paper_session_event_records(candidate, state)
    details = [compact_session_event_detail(event, active_keys, set()) for event in records]
    session_events = [event for event in details if event.get("currentSession")]
    if active_only:
        session_events = [event for event in session_events if event.get("marketRole") == "active"]
    tick_buckets = {
        paper_tick_bucket(event.get("processedAt"))
        for event in session_events
        if paper_tick_bucket(event.get("processedAt"))
    }
    signals = [event for event in session_events if paper_event_type_bucket(event) == "signal"]
    trade_events = [event for event in session_events if paper_event_type_bucket(event) in {"open_trade", "close_trade"}]
    active_events = [event for event in session_events if event.get("marketRole") == "active"]
    active_signals = [event for event in active_events if paper_event_type_bucket(event) == "signal"]
    active_warnings = [event for event in active_events if paper_event_type_bucket(event) in {"warning", "state_warning"}]
    active_symbols = {market.get("symbol") for market in candidate_symbols_by_mode(candidate, "active") if market.get("symbol")}
    if active_only:
        closed_trades = [trade for trade in (state.get("closedTrades") or []) if not trade.get("symbol") or trade.get("symbol") in active_symbols]
        open_positions = [position for position in (state.get("openPositions") or []) if not position.get("symbol") or position.get("symbol") in active_symbols]
    else:
        closed_trades = state.get("closedTrades") or []
        open_positions = state.get("openPositions") or []
    return {
        "paperTicks": len(tick_buckets) if session_events else 0,
        "signals": len(signals),
        "closedTrades": len(closed_trades),
        "openPositions": len(open_positions),
        "activeSessionEvents": len(active_events),
        "currentSessionTradeEvents": len(trade_events),
    }, {
        "session": session,
        "activeEvents": active_events,
        "activeSignals": active_signals,
        "activeWarnings": active_warnings,
    }


def compact_observation_counters(counters: dict) -> dict:
    runner = counters.get("runnerCounters") or {}
    session = counters.get("sessionCounters") or {}
    active = counters.get("activeMarketCounters") or {}
    consistency = counters.get("consistency") or {}
    return {
        "runnerIterations": runner.get("iterations"),
        "runnerTicksRun": runner.get("ticksRun"),
        "runnerTicksSkipped": runner.get("ticksSkipped"),
        "runnerErrors": runner.get("errors"),
        "processedCandleDeltaTotal": runner.get("processedCandleDeltaTotal"),
        "sessionPaperTicks": session.get("paperTicks"),
        "sessionSignals": session.get("signals"),
        "closedTrades": session.get("closedTrades"),
        "openPositions": session.get("openPositions"),
        "activeSessionEvents": session.get("activeSessionEvents"),
        "currentSessionTradeEvents": session.get("currentSessionTradeEvents"),
        "activeMarketProcessedCandleCount": active.get("processedCandleCount"),
        "counterConsistencyStatus": consistency.get("status"),
        "warnings": consistency.get("warnings") or [],
    }


def build_paper_observation_counters(args) -> dict:
    candidate = load_paper_candidate_config()
    state_path = Path(app.root_path) / "data" / "paper-state.json"
    journal_path = Path(app.root_path) / "reports" / "paper-journal.jsonl"
    state = read_json_file(str(state_path), {}) if state_path.exists() else {}
    active_only = str(args.get("activeOnly", "true")).strip().lower() not in {"0", "false", "no", "off"}
    selection, runner_entries, _limit = runner_log_entries(args)
    runner_counts = runner_log_counter_summary(runner_entries)
    session_counts, session_meta = current_session_journal_counters(candidate, state, active_only)
    active = primary_active_market(candidate)
    active_key = paper_market_key(active)
    consistency_warnings = []
    if runner_counts["ticksRun"] != session_counts["paperTicks"]:
        consistency_warnings.append(
            f"runnerTicksRun ({runner_counts['ticksRun']}) differs from sessionPaperTicks ({session_counts['paperTicks']}) because runner ticks come from the selected runner JSONL log while sessionPaperTicks are inferred from current-session paper journal events."
        )
    if runner_counts["processedCandleDeltaTotal"] and session_counts["paperTicks"] <= 0:
        consistency_warnings.append("processedCandleDeltaTotal is available from runner records, but no current-session active paper tick events were found in the journal.")
    processed_explanation = "Active-market processed candle count is not directly derivable from current journal/state; use runner processedCandleDeltaTotal for per-run progress."
    return {
        "ok": True,
        "paperEnabled": canonical_paper_enabled(candidate),
        "realTradingEnabled": paper_real_trading_enabled()[0],
        "candidate": candidate_summary(candidate),
        "filters": {"activeOnly": active_only},
        "counterSources": {
            "runnerLog": {
                "path": relative_app_path(selection["path"]),
                "exists": selection["path"].exists(),
                "selectedBy": selection.get("selectedBy"),
                "entriesRead": len(runner_entries),
                "availableLogs": selection.get("availableLogs") or [],
            },
            "paperJournal": {
                "path": relative_app_path(journal_path),
                "exists": journal_path.exists(),
            },
            "paperState": {
                "path": relative_app_path(state_path),
                "exists": state_path.exists(),
            },
        },
        "runnerCounters": {
            "iterations": runner_counts["iterations"],
            "ticksRun": runner_counts["ticksRun"],
            "ticksSkipped": runner_counts["ticksSkipped"],
            "errors": runner_counts["errors"],
            "processedCandleDeltaTotal": runner_counts["processedCandleDeltaTotal"],
        },
        "sessionCounters": session_counts,
        "activeMarketCounters": {
            "symbol": active.get("symbol"),
            "timeframe": active.get("interval") or active.get("timeframe"),
            "marketKey": active_key,
            "processedCandleCount": None,
            "processedCandleCountExplanation": processed_explanation,
            "signals": len(session_meta["activeSignals"]),
            "closedTrades": session_counts["closedTrades"],
            "warnings": len(session_meta["activeWarnings"]),
        },
        "consistency": {
            "status": "WATCH" if consistency_warnings else "OK",
            "warnings": dedupe_list(consistency_warnings + selection.get("warnings", [])),
        },
    }


def runner_action_value(entry: dict) -> str | None:
    action = entry.get("nextAction")
    if isinstance(action, dict):
        return action.get("action")
    return entry.get("action")


def runner_action_reason(entry: dict) -> str | None:
    action = entry.get("nextAction")
    if isinstance(action, dict):
        return action.get("reason")
    return entry.get("reason")


def compact_runner_summary(summary: dict) -> dict:
    counts = summary.get("counts") or {}
    latest_iteration = summary.get("latestIteration") or {}
    latest_summary = summary.get("latestSummary") or {}
    return {
        "exists": summary.get("exists"),
        "entriesRead": summary.get("entriesRead"),
        "latestIteration": latest_iteration,
        "latestSummary": latest_summary,
        "counts": counts,
        "nextAction": summary.get("nextAction"),
        "warnings": summary.get("warnings", []),
    }


def build_paper_runner_summary(args) -> dict:
    selection, entries, _limit = runner_log_entries({**dict(args), "limit": args.get("limit", 200)})
    log_path = selection["path"]
    warnings = list(selection.get("warnings") or [])
    exists = log_path.exists()
    iterations = [entry for entry in entries if entry.get("type") == "iteration" or entry.get("iteration") is not None]
    summaries = [entry for entry in entries if entry.get("type") == "summary" or entry.get("iterationsAttempted") is not None]
    latest_iteration = iterations[-1] if iterations else None
    latest_summary = summaries[-1] if summaries else None
    counts = runner_log_counter_summary(entries)
    recent_skip_reasons = dedupe_list([
        entry.get("tickSkipReason") or runner_action_reason(entry)
        for entry in iterations[-25:]
        if not entry.get("tickRan") and (entry.get("tickSkipReason") or runner_action_reason(entry))
    ])[-10:]
    recent_actions = [
        {
            "iteration": entry.get("iteration"),
            "action": runner_action_value(entry),
            "reason": runner_action_reason(entry),
            "timestamp": entry.get("timestamp"),
        }
        for entry in iterations[-10:]
        if runner_action_value(entry) or runner_action_reason(entry)
    ]
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, _ = paper_real_trading_enabled()
    if not exists:
        warnings.append("Runner log file does not exist yet. Run python scripts/paper_run_once.py --log-file reports/paper-runner-session.jsonl to create one.")
    if latest_iteration:
        next_action = latest_iteration.get("nextAction") or {"action": runner_action_value(latest_iteration), "reason": runner_action_reason(latest_iteration)}
    elif not exists:
        next_action = {"action": "RUN_PAPER_RUNNER_WITH_LOG", "reason": "No runner JSONL log exists for the requested path."}
    else:
        next_action = {"action": "NO_RUNNER_ITERATIONS_FOUND", "reason": "The runner log exists but no iteration records were found."}
    return {
        "ok": True,
        "logFile": relative_app_path(log_path),
        "selectedBy": selection.get("selectedBy"),
        "availableLogs": selection.get("availableLogs") or [],
        "exists": exists,
        "entriesRead": len(entries),
        "latestIteration": latest_iteration,
        "latestSummary": latest_summary,
        "counts": counts,
        "recentSkipReasons": recent_skip_reasons,
        "recentActions": recent_actions,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "nextAction": next_action,
        "warnings": dedupe_list(warnings),
    }


def resolve_app_file_arg(raw: str | None, default_relative: str) -> tuple[Path, list[str]]:
    warnings = []
    root = Path(app.root_path).resolve()
    path = Path(str(raw or default_relative).strip())
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        warnings.append(f"Requested file is outside the app root; using {default_relative}.")
        resolved = (root / default_relative).resolve()
    return resolved, warnings


def resolve_paper_quality_log_selection(args) -> dict:
    warnings = []
    root = Path(app.root_path).resolve()
    explicit = bool(args.get("logFile"))
    if explicit:
        path, path_warnings = resolve_app_file_arg(args.get("logFile"), "reports/paper-runner-session.jsonl")
        warnings.extend(path_warnings)
        selected_by = "query"
    else:
        candidates = sorted((root / "reports").glob("paper-runner-session*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
        if candidates:
            path = candidates[0].resolve()
            selected_by = "latest"
        else:
            path = (root / "reports" / "paper-runner-session.jsonl").resolve()
            selected_by = "default"
            warnings.append("No paper-runner-session*.jsonl files were found.")
    return {
        "path": path,
        "selectedBy": selected_by,
        "availableLogs": [relative_app_path(item.resolve()) for item in sorted((root / "reports").glob("paper-runner-session*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)[:10]],
        "warnings": warnings,
    }


def stringify_for_error_scan(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def classify_network_error_text(text: str) -> str | None:
    upper = (text or "").upper()
    if not upper or upper in {"NULL", "NONE"}:
        return None
    if "ENOTFOUND" in upper or "GETADDRINFO" in upper or "DNS" in upper:
        return "DNS_ENOTFOUND"
    if "ECONNRESET" in upper or "CONNECTION RESET" in upper:
        return "ECONNRESET"
    if "ETIMEDOUT" in upper:
        return "ETIMEDOUT"
    if "HTTPSCONNECTIONPOOL" in upper or "HTTPSCONNECTION" in upper:
        return "HTTPS_CONNECTION_POOL"
    if "READ TIMED OUT" in upper or "TIMEOUT" in upper or "TIMED OUT" in upper:
        return "HTTP_TIMEOUT"
    if "BYBIT" in upper or "/V5/MARKET/KLINE" in upper:
        return "BYBIT_ERROR"
    if "ERROR" in upper or "EXCEPTION" in upper or "FAILED" in upper:
        return "UNKNOWN_ERROR"
    return None


def paper_quality_network_errors(entries: list[dict]) -> dict:
    buckets = {
        "DNS_ENOTFOUND": 0,
        "ECONNRESET": 0,
        "ETIMEDOUT": 0,
        "HTTP_TIMEOUT": 0,
        "BYBIT_ERROR": 0,
        "HTTPS_CONNECTION_POOL": 0,
        "UNKNOWN_ERROR": 0,
    }
    examples = []
    for entry in entries:
        fragments = [
            entry.get("error"),
            entry.get("stderr"),
            entry.get("message"),
            entry.get("warning"),
            entry.get("warnings"),
            entry.get("tickResult"),
            entry.get("refresh"),
        ]
        text = " ".join(stringify_for_error_scan(fragment) for fragment in fragments if fragment is not None and fragment != "" and fragment != [])
        bucket = classify_network_error_text(text)
        if bucket:
            buckets[bucket] += 1
            if len(examples) < 8:
                examples.append({
                    "type": bucket,
                    "iteration": entry.get("iteration"),
                    "timestamp": entry.get("timestamp"),
                    "text": text[:500],
                })
    return {"total": sum(buckets.values()), "byType": buckets, "examples": examples}


def paper_quality_timestamp(entry: dict):
    return parse_iso_timestamp(entry.get("timestamp") or entry.get("processedAt") or entry.get("generatedAt"))


def paper_quality_summary(entries: list[dict]) -> tuple[dict, dict, list[dict], list[dict]]:
    iterations = [entry for entry in entries if entry.get("type") == "iteration" or entry.get("iteration") is not None]
    summaries = [entry for entry in entries if entry.get("type") == "summary" or entry.get("iterationsAttempted") is not None]
    iteration_times = [paper_quality_timestamp(entry) for entry in iterations]
    iteration_times = [stamp for stamp in iteration_times if stamp]
    first = min(iteration_times) if iteration_times else None
    last = max(iteration_times) if iteration_times else None
    elapsed_hours = round(((last - first).total_seconds() / 3600) if first and last else 0, 4)
    deltas = [int(safe_float(entry.get("processedCandlesDelta"), 0)) for entry in iterations if entry.get("processedCandlesDelta") is not None]
    timestamps_sorted = sorted(iteration_times)
    gaps = []
    for prev, cur in zip(timestamps_sorted, timestamps_sorted[1:]):
        gap_minutes = (cur - prev).total_seconds() / 60
        if gap_minutes > 45:
            gaps.append({"from": prev.isoformat(), "to": cur.isoformat(), "gapMinutes": round(gap_minutes, 2)})
    inferred_interval_minutes = None
    if len(timestamps_sorted) >= 3:
        diffs = sorted([(cur - prev).total_seconds() / 60 for prev, cur in zip(timestamps_sorted, timestamps_sorted[1:]) if (cur - prev).total_seconds() > 0])
        if diffs:
            inferred_interval_minutes = round(diffs[len(diffs) // 2], 2)
    expected_iterations = None
    missing_iterations = None
    if inferred_interval_minutes and elapsed_hours:
        expected_iterations = int(max(1, round(elapsed_hours * 60 / inferred_interval_minutes) + 1))
        missing_iterations = max(0, expected_iterations - len(iterations))
    summary_errors = int(sum(safe_float(entry.get("errors"), 0) for entry in summaries if entry.get("errors") is not None))
    iteration_errors = len([entry for entry in iterations if entry.get("ok") is False or str(entry.get("runStatus", "")).upper() == "ERROR" or entry.get("error")])
    errors = max(iteration_errors, summary_errors)
    refresh_errors = len([entry for entry in iterations if str(entry.get("refreshStatus", "")).upper() in {"ERROR", "FAIL", "FAILED"} or (entry.get("refreshOk") is False)])
    tick_errors = len([entry for entry in iterations if str(entry.get("tickStatus", "")).upper() in {"ERROR", "FAIL", "FAILED"} or str((entry.get("tickResult") or {}).get("status", "")).upper() == "ERROR"])
    counts = {
        "iterationsTotal": len(iterations),
        "summaries": len(summaries),
        "ticksRun": len([entry for entry in iterations if entry.get("tickRan")]),
        "ticksSkipped": len([entry for entry in iterations if not entry.get("tickRan")]),
        "errors": errors,
        "okCount": len([entry for entry in iterations if entry.get("ok") is not False and str(entry.get("runStatus", "")).upper() != "ERROR"]),
        "errorRatePct": round((errors / len(iterations) * 100) if iterations else 0, 4),
        "refreshOkCount": len([entry for entry in iterations if entry.get("refreshStatus") == "OK" or entry.get("refreshOk") is True]),
        "refreshErrorCount": refresh_errors,
        "tickOkCount": len([entry for entry in iterations if entry.get("tickRan") and entry.get("ok") is not False]),
        "tickErrorCount": tick_errors,
        "stopRulesOkCount": len([entry for entry in iterations if entry.get("stopRulesStatus") == "OK"]),
        "stopRulesNonOkCount": len([entry for entry in iterations if entry.get("stopRulesStatus") and entry.get("stopRulesStatus") != "OK"]),
        "realTradingFalseCount": len([entry for entry in iterations if entry.get("realTradingEnabled") is False]),
        "realTradingTrueCount": len([entry for entry in iterations if entry.get("realTradingEnabled") is True]),
        "paperEnabledTrueCount": len([entry for entry in iterations if entry.get("paperEnabled") is True]),
        "paperEnabledFalseCount": len([entry for entry in iterations if entry.get("paperEnabled") is False]),
        "processedCandleDeltaTotal": int(sum(deltas)),
        "maxProcessedCandlesDelta": max(deltas) if deltas else 0,
        "newSignalsTotal": int(sum(safe_float(entry.get("newSignals"), 0) for entry in iterations if entry.get("newSignals") is not None)),
        "closedTradesLatest": next((entry.get("closedTrades") for entry in reversed(iterations) if entry.get("closedTrades") is not None), None),
        "expectedIterations": expected_iterations,
        "missingIterations": missing_iterations,
        "inferredIntervalMinutes": inferred_interval_minutes,
    }
    run_window = {
        "firstTimestamp": first.isoformat() if first else None,
        "lastTimestamp": last.isoformat() if last else None,
        "elapsedHours": elapsed_hours,
        "longGaps": gaps[:10],
        "longGapCount": len(gaps),
    }
    return counts, run_window, iterations, summaries


def paper_quality_journal_summary(journal_path: Path, first, last, candidate: dict, active_only: bool) -> dict:
    journal = read_jsonl_tail(str(journal_path), 5000) if journal_path.exists() else []
    active_keys = {paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "active") if paper_market_key(market)}
    watch_keys = {paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "watch") if paper_market_key(market)}
    session = {
        "started": first,
        "ended": last,
        "startedAt": first.isoformat() if first else None,
        "endedAt": last.isoformat() if last else None,
    }
    normalized = [normalized_paper_event(event, session) for event in journal]
    window_events = [event for event in normalized if event.get("currentSession")]
    if active_only:
        window_events = [event for event in window_events if event.get("marketKey") in active_keys]
    stale_events = [event for event in normalized if event.get("stale")]
    event_types = [str(event.get("eventType", "")).upper() for event in window_events]
    warning_events = [event for event in window_events if str(event.get("eventType", "")).upper() in {"WARNING", "ERROR"}]
    state_warning_events = [event for event in window_events if str(event.get("eventType", "")).upper() == "STATE_WARNING"]
    return {
        "exists": journal_path.exists(),
        "path": relative_app_path(journal_path),
        "eventsInRunWindow": len(window_events),
        "signals": event_types.count("SIGNAL"),
        "stateWarnings": len(state_warning_events),
        "warnings": len(warning_events),
        "openedTrades": event_types.count("ENTRY"),
        "closedTrades": event_types.count("EXIT"),
        "activeMarketEvents": len([event for event in window_events if event.get("marketKey") in active_keys]),
        "watchOnlyEvents": len([event for event in window_events if event.get("marketKey") in watch_keys]),
        "journalWarnings": len(warning_events) + len(state_warning_events),
        "staleOldWarnings": len([event for event in stale_events if str(event.get("eventType", "")).upper() in {"WARNING", "ERROR", "STATE_WARNING"}]),
        "recentWarnings": dedupe_list([(event.get("reason") or event.get("message") or "") for event in warning_events[-10:] if event.get("reason") or event.get("message")]),
    }


def paper_quality_state_summary(state_path: Path, candidate: dict) -> dict:
    state = read_json_file(str(state_path), {}) if state_path.exists() else {}
    active = primary_active_market(candidate)
    active_key = paper_market_key(active)
    last_processed = state.get("lastProcessedCandleTime") if isinstance(state.get("lastProcessedCandleTime"), dict) else {}
    freshness = state.get("freshness") if isinstance(state.get("freshness"), dict) else {}
    initialized = sorted(last_processed.keys())
    required = [paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "active") if paper_market_key(market)]
    return {
        "exists": state_path.exists(),
        "path": relative_app_path(state_path),
        "activeMarketKey": active_key,
        "lastProcessedCandleTime": last_processed.get(active_key),
        "latestCandleTime": (freshness.get(active_key) or {}).get("latestCandleTime") if active_key else None,
        "freshness": freshness.get(active_key) if active_key else None,
        "initializedMarkets": initialized,
        "missingMarkets": [key for key in required if key not in last_processed],
        "openPositions": len(state.get("openPositions") or []),
        "closedTrades": len(state.get("closedTrades") or []),
        "warnings": state.get("warnings") if isinstance(state.get("warnings"), list) else [],
    }


def paper_quality_verdict(summary: dict, network: dict, journal: dict, state: dict, log_exists: bool) -> dict:
    reasons = []
    score = 100
    if not log_exists:
        return {
            "status": "INVALID",
            "score": 0,
            "reasons": ["Runner log file could not be found."],
            "evidenceTrust": "INVALID",
            "recommendation": {"action": "IGNORE_RUN", "reason": "No runner log is available to audit."},
        }
    if summary["realTradingTrueCount"] > 0:
        reasons.append("realTradingEnabled was true in at least one runner entry.")
        score -= 100
    if summary["stopRulesNonOkCount"] > 0:
        reasons.append(f"{summary['stopRulesNonOkCount']} runner entrie(s) reported non-OK stop rules.")
        score -= 50
    if summary["errors"] > 0:
        reasons.append(f"{summary['errors']} runner error(s) were reported.")
        score -= min(35, summary["errors"] * 2)
    if summary["errorRatePct"] >= 20:
        reasons.append(f"High runner error rate: {summary['errorRatePct']}%.")
        score -= 25
    elif summary["errorRatePct"] > 0:
        reasons.append(f"Runner error rate is non-zero: {summary['errorRatePct']}%.")
        score -= 10
    if network["total"] > 0:
        reasons.append(f"{network['total']} network/data-source error signal(s) detected in runner log.")
        score -= min(30, network["total"] * 4)
    if summary["maxProcessedCandlesDelta"] > 5:
        reasons.append(f"Suspicious processed candle jump detected: max delta {summary['maxProcessedCandlesDelta']}.")
        score -= 15
    if summary.get("missingIterations") and summary["missingIterations"] > max(2, summary["iterationsTotal"] * 0.1):
        reasons.append(f"Estimated missing runner iterations: {summary['missingIterations']}.")
        score -= 15
    if summary.get("longGapCount", 0) > 0:
        reasons.append(f"{summary['longGapCount']} long runner time gap(s) detected.")
        score -= min(15, summary["longGapCount"] * 3)
    if summary["paperEnabledFalseCount"] > 0 and summary["paperEnabledTrueCount"] > 0:
        reasons.append("Paper was disabled during part of the selected run log.")
        score -= 10
    if summary["ticksRun"] <= 0 and (summary.get("elapsedHours") or 0) >= 3:
        reasons.append("No useful paper ticks were run despite a multi-hour run window.")
        score -= 20
    if journal.get("journalWarnings", 0) > 0:
        reasons.append(f"{journal['journalWarnings']} journal warning event(s) occurred in the run window.")
        score -= min(15, journal["journalWarnings"] * 3)
    if state.get("missingMarkets"):
        reasons.append(f"State is missing active market(s): {', '.join(state.get('missingMarkets') or [])}.")
        score -= 15
    score = max(0, min(100, int(round(score))))
    if summary["realTradingTrueCount"] > 0 or summary["stopRulesNonOkCount"] >= 3:
        status = "INVALID"
        trust = "INVALID"
        action = "IGNORE_RUN"
    elif score < 55 or summary["errorRatePct"] >= 20 or network["total"] >= 5:
        status = "DEGRADED"
        trust = "LOW"
        action = "INVESTIGATE_NETWORK" if network["total"] else "RERUN_OBSERVATION"
    elif score < 85 or summary["errors"] or network["total"] or journal.get("journalWarnings", 0):
        status = "WATCH"
        trust = "MEDIUM"
        action = "EXTEND_RUN"
    else:
        status = "GOOD"
        trust = "HIGH"
        action = "ACCEPT_EVIDENCE"
    if not reasons:
        reasons.append("Runner log shows low errors, stop rules OK, real trading false, and useful ticks collected.")
    recommendation_reasons = {
        "ACCEPT_EVIDENCE": "Paper run evidence appears technically reliable for paper-only review.",
        "EXTEND_RUN": "Some reliability warnings exist; extend observation before judging the candidate.",
        "RERUN_OBSERVATION": "Run quality is degraded enough that a cleaner observation run is preferable.",
        "IGNORE_RUN": "Safety or parse issues make this run unsuitable as evidence.",
        "INVESTIGATE_NETWORK": "Network/data-source failures degraded the run; investigate before relying on the evidence.",
    }
    return {
        "status": status,
        "score": score,
        "reasons": dedupe_list(reasons),
        "evidenceTrust": trust,
        "recommendation": {"action": action, "reason": recommendation_reasons[action]},
    }


def _save_paper_run_quality_report(payload: dict) -> str:
    reports_dir = Path(app.root_path) / "reports" / "paper-quality"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = reports_dir / f"paper-run-quality-report-{stamp}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return str(path.relative_to(Path(app.root_path))).replace("\\", "/")


def build_paper_run_quality_report(args) -> dict:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active_only = str(args.get("activeOnly", args.get("active_only", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    save = str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"}
    log_selection = resolve_paper_quality_log_selection(args)
    log_path = log_selection["path"]
    journal_path, journal_warnings = resolve_app_file_arg(args.get("journalFile"), "reports/paper-journal.jsonl")
    state_path, state_warnings = resolve_app_file_arg(args.get("stateFile"), "data/paper-state.json")
    warnings = dedupe_list((log_selection.get("warnings") or []) + journal_warnings + state_warnings + ([real_detail] if real_enabled else []))
    entries = read_jsonl_tail(str(log_path), 10000) if log_path.exists() else []
    runner_summary, run_window, iterations, summaries = paper_quality_summary(entries)
    first = parse_iso_timestamp(run_window.get("firstTimestamp"))
    last = parse_iso_timestamp(run_window.get("lastTimestamp"))
    runner_summary["elapsedHours"] = run_window.get("elapsedHours")
    runner_summary["longGapCount"] = run_window.get("longGapCount")
    network = paper_quality_network_errors(entries)
    journal = paper_quality_journal_summary(journal_path, first, last, candidate, active_only)
    state = paper_quality_state_summary(state_path, candidate)
    quality = paper_quality_verdict(runner_summary, network, journal, state, log_path.exists())
    response = {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "selectedLogFile": relative_app_path(log_path),
        "selectedBy": log_selection.get("selectedBy"),
        "availableLogs": log_selection.get("availableLogs") or [],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "runWindow": run_window,
        "runnerSummary": runner_summary,
        "networkErrors": network,
        "journalSummary": journal,
        "stateSummary": state,
        "quality": quality,
        "filters": {"activeOnly": active_only},
        "warnings": warnings,
    }
    if save:
        response["savedPath"] = _save_paper_run_quality_report(response)
    return response


def compact_session_events_summary(summary: dict) -> dict:
    counts = summary.get("counts") or {}
    return {
        "sessionId": summary.get("session", {}).get("sessionId"),
        "latestEventTime": summary.get("latestEventTime"),
        "counts": counts,
        "paperEnabled": summary.get("paperEnabled"),
        "realTradingEnabled": summary.get("realTradingEnabled"),
        "nextAction": summary.get("nextAction"),
        "warnings": summary.get("warnings", []),
    }


def build_paper_session_events_summary(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    journal = read_jsonl_tail(os.path.join(app.root_path, "reports", "paper-journal.jsonl"), 1000)
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, _ = paper_real_trading_enabled()
    session = paper_session_window(candidate)
    active_keys = {paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "active") if paper_market_key(market)}
    watch_keys = {paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "watch") if paper_market_key(market)}
    normalized = [normalized_paper_event(event, session) for event in journal]
    if request_active_only(args):
        normalized = [event for event in normalized if event.get("marketKey") in active_keys]
    session_events = [event for event in normalized if event.get("currentSession")]
    stale_events = [event for event in normalized if event.get("stale")]
    event_types = [str(event.get("eventType", "")).upper() for event in session_events]
    latest_event = session_events[-1] if session_events else None
    state_warnings = state.get("warnings") if isinstance(state.get("warnings"), list) else []
    if request_active_only(args):
        state_warnings = [warning for warning in state_warnings if warning_market_key({"reason": warning}) in active_keys]
    counts = {
        "signals": event_types.count("SIGNAL"),
        "warnings": len([event for event in session_events if str(event.get("eventType", "")).upper() in {"WARNING", "ERROR"}]),
        "stateWarnings": len(state_warnings),
        "openedVirtualTrades": event_types.count("ENTRY"),
        "closedVirtualTrades": event_types.count("EXIT"),
        "currentSessionEvents": len(session_events),
        "staleEvents": len(stale_events),
        "activeMarketEvents": len([event for event in session_events if event.get("marketKey") in active_keys]),
        "watchMarketEvents": len([event for event in session_events if event.get("marketKey") in watch_keys]),
    }
    recent_warnings = [
        event.get("reason") or event.get("message")
        for event in session_events[-50:]
        if str(event.get("eventType", "")).upper() in {"WARNING", "ERROR"} and (event.get("reason") or event.get("message"))
    ]
    warnings = dedupe_list(recent_warnings + [str(warning) for warning in state_warnings if warning])
    if counts["currentSessionEvents"] <= 0:
        next_action = {
            "action": "WAIT_FOR_PAPER_SESSION_EVENTS" if paper_enabled else "PAPER_DISABLED",
            "reason": "No current-session paper journal events are available yet." if paper_enabled else "Paper is disabled; session event summary is read-only.",
        }
    else:
        next_action = {
            "action": "REVIEW_PAPER_SESSION_EVENTS",
            "reason": f"{counts['currentSessionEvents']} current-session paper journal event(s) are available for review.",
        }
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "session": {
            "sessionId": session.get("sessionId"),
            "startedAt": session.get("startedAt"),
            "endedAt": session.get("endedAt"),
        },
        "filters": {"activeOnly": request_active_only(args)},
        "latestEventTime": latest_event.get("timestamp") if latest_event else None,
        "counts": counts,
        "recentWarnings": warnings[-10:],
        "recentEvents": session_events[-10:],
        "nextAction": next_action,
        "warnings": warnings,
    }


def paper_event_type_bucket(event: dict) -> str:
    event_type = str(event.get("eventType") or "").upper()
    if event_type == "SIGNAL":
        return "signal"
    if event_type in {"WARNING", "ERROR", "STATE_WARNING"}:
        return "state_warning" if event_type == "STATE_WARNING" else "warning"
    if event_type == "ENTRY":
        return "open_trade"
    if event_type == "EXIT":
        return "close_trade"
    return event_type.lower() or "unknown"


def paper_event_market_role(event: dict, active_keys: set[str], watch_keys: set[str]) -> str:
    key = event.get("marketKey")
    if key in active_keys:
        return "active"
    if key in watch_keys:
        return "watch"
    mode = str(event.get("mode") or "").lower()
    if mode in {"active", "watch"}:
        return mode
    return "unknown"


def compact_session_event_detail(event: dict, active_keys: set[str], watch_keys: set[str]) -> dict:
    signal_price = safe_float(event.get("signalPrice"), None)
    fill_price = safe_float(event.get("fillPrice"), None)
    pnl = safe_float(event.get("netPnl"), None)
    raw = {
        key: event.get(key)
        for key in ("eventId", "tradeId", "candleTime", "mode", "strategy", "paramsHash", "feePaid", "slippagePaid", "accountEquity")
        if event.get(key) not in (None, "")
    }
    return {
        "processedAt": event.get("timestamp"),
        "eventType": event.get("eventType"),
        "symbol": event.get("symbol"),
        "interval": event.get("interval"),
        "marketKey": event.get("marketKey"),
        "marketRole": paper_event_market_role(event, active_keys, watch_keys),
        "currentSession": bool(event.get("currentSession")),
        "stale": bool(event.get("stale")),
        "reason": event.get("reason") or event.get("message"),
        "signal": event.get("signal") or event.get("action") or event.get("eventType"),
        "action": event.get("action") or event.get("eventType"),
        "side": event.get("side"),
        "price": fill_price if fill_price is not None else signal_price,
        "signalPrice": signal_price,
        "fillPrice": fill_price,
        "pnl": pnl,
        "rawEvent": raw,
    }


def paper_session_event_records(candidate: dict, state: dict | None = None) -> tuple[list[dict], dict, set[str], set[str]]:
    session = paper_session_window(candidate)
    journal = read_jsonl_tail(os.path.join(app.root_path, "reports", "paper-journal.jsonl"), 2000)
    active_keys = {paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "active") if paper_market_key(market)}
    watch_keys = {paper_market_key(market) for market in candidate_symbols_by_mode(candidate, "watch") if paper_market_key(market)}
    records = [normalized_paper_event(event, session) for event in journal]
    state = state or {}
    for idx, warning in enumerate(state.get("warnings") or []):
        records.append({
            "timestamp": state.get("updatedAt"),
            "eventType": "STATE_WARNING",
            "symbol": None,
            "interval": None,
            "marketKey": warning_market_key({"reason": warning}),
            "reason": str(warning),
            "message": str(warning),
            "action": "STATE_WARNING",
            "signalPrice": None,
            "fillPrice": None,
            "netPnl": None,
            "stale": False,
            "currentSession": bool(session.get("started")),
            "rawIndex": idx,
        })
    return records, session, active_keys, watch_keys


def build_paper_session_events(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, _ = paper_real_trading_enabled()
    limit = min(max(int(safe_float(args.get("limit", 50), 50)), 1), 500)
    type_filter = str(args.get("type", "all") or "all").strip().lower()
    market_filter = str(args.get("market", "all") or "all").strip().lower()
    if request_active_only(args):
        market_filter = "active"
    session_filter = str(args.get("currentSession", "all") or "all").strip().lower()
    valid_types = {"all", "signal", "warning", "state_warning", "open_trade", "close_trade"}
    valid_markets = {"all", "active", "watch"}
    valid_sessions = {"all", "true", "false"}
    warnings = []
    if type_filter not in valid_types:
        warnings.append(f"Unknown type filter {type_filter}; using all.")
        type_filter = "all"
    if market_filter not in valid_markets:
        warnings.append(f"Unknown market filter {market_filter}; using all.")
        market_filter = "all"
    if session_filter not in valid_sessions:
        warnings.append(f"Unknown currentSession filter {session_filter}; using all.")
        session_filter = "all"
    records, session, active_keys, watch_keys = paper_session_event_records(candidate, state)
    details = [compact_session_event_detail(event, active_keys, watch_keys) for event in records]
    if request_active_only(args):
        details = [event for event in details if event.get("marketRole") == "active"]
    counts = {
        "all": len(details),
        "signals": len([event for event in details if paper_event_type_bucket(event) == "signal"]),
        "warnings": len([event for event in details if paper_event_type_bucket(event) == "warning"]),
        "stateWarnings": len([event for event in details if paper_event_type_bucket(event) == "state_warning"]),
        "openTrades": len([event for event in details if paper_event_type_bucket(event) == "open_trade"]),
        "closeTrades": len([event for event in details if paper_event_type_bucket(event) == "close_trade"]),
        "active": len([event for event in details if event.get("marketRole") == "active"]),
        "watch": len([event for event in details if event.get("marketRole") == "watch"]),
        "currentSession": len([event for event in details if event.get("currentSession")]),
        "stale": len([event for event in details if event.get("stale")]),
    }
    filtered = details
    if type_filter != "all":
        filtered = [event for event in filtered if paper_event_type_bucket(event) == type_filter]
    if market_filter != "all":
        filtered = [event for event in filtered if event.get("marketRole") == market_filter]
    if session_filter != "all":
        expected = session_filter == "true"
        filtered = [event for event in filtered if bool(event.get("currentSession")) == expected]
    filtered = sorted(filtered, key=lambda item: parse_iso_timestamp(item.get("processedAt")) or datetime.min.replace(tzinfo=timezone.utc))
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "sessionStartedAt": session.get("startedAt"),
        "sessionEndedAt": session.get("endedAt"),
        "limit": limit,
        "filters": {
            "type": type_filter,
            "market": market_filter,
            "currentSession": session_filter,
            "activeOnly": request_active_only(args),
        },
        "counts": counts,
        "events": filtered[-limit:],
        "warnings": dedupe_list(warnings),
    }


def trade_state_item(item: dict, default_status: str) -> dict:
    pnl = safe_float(first_baseline_value(item.get("netPnl"), item.get("realizedPnl"), item.get("pnl")), 0)
    return {
        "tradeId": item.get("id") or item.get("tradeId") or item.get("key"),
        "symbol": item.get("symbol"),
        "interval": item.get("interval") or item.get("timeframe"),
        "side": item.get("side"),
        "entryPrice": safe_float(item.get("entryPrice"), None),
        "exitPrice": safe_float(item.get("exitPrice"), None),
        "size": safe_float(item.get("size"), None),
        "pnl": pnl,
        "feePaid": safe_float(first_baseline_value(item.get("feePaid"), item.get("fees")), 0),
        "openedAt": item.get("openedAt") or item.get("entryAt") or item.get("timestamp"),
        "closedAt": item.get("closedAt") or item.get("exitAt"),
        "status": item.get("status") or default_status,
    }


def build_paper_session_trades(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, _ = paper_real_trading_enabled()
    records, session, active_keys, watch_keys = paper_session_event_records(candidate, state)
    details = [compact_session_event_detail(event, active_keys, watch_keys) for event in records]
    trade_events = [
        event for event in details
        if paper_event_type_bucket(event) in {"open_trade", "close_trade"} and (event.get("currentSession") or event.get("stale"))
    ]
    open_trades = [trade_state_item(item, "open") for item in (state.get("openPositions") or [])]
    closed_trades = [trade_state_item(item, "closed") for item in (state.get("closedTrades") or [])]
    if request_active_only(args):
        active_symbols = {market.get("symbol") for market in candidate_symbols_by_mode(candidate, "active") if market.get("symbol")}
        trade_events = [event for event in trade_events if event.get("marketRole") == "active"]
        open_trades = [trade for trade in open_trades if not trade.get("symbol") or trade.get("symbol") in active_symbols]
        closed_trades = [trade for trade in closed_trades if not trade.get("symbol") or trade.get("symbol") in active_symbols]
    realized = sum(safe_float(item.get("pnl"), 0) for item in closed_trades)
    fees = sum(safe_float(item.get("feePaid"), 0) for item in open_trades + closed_trades)
    wins = [item for item in closed_trades if safe_float(item.get("pnl"), 0) > 0]
    losses = [item for item in closed_trades if safe_float(item.get("pnl"), 0) < 0]
    warnings = []
    if not trade_events and not open_trades and not closed_trades:
        warnings.append("No virtual trade events are available yet for the current paper session.")
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "sessionStartedAt": session.get("startedAt"),
        "sessionEndedAt": session.get("endedAt"),
        "filters": {"activeOnly": request_active_only(args)},
        "openTrades": open_trades,
        "closedTrades": closed_trades[-50:],
        "recentTradeEvents": trade_events[-50:],
        "totals": {
            "closedTrades": len(closed_trades),
            "recentTradeEvents": len(trade_events),
            "currentSessionTradeEvents": len([event for event in trade_events if event.get("currentSession")]),
            "realizedPnl": round(realized, 8),
            "fees": round(fees, 8),
            "winRate": round((len(wins) / len(closed_trades) * 100), 4) if closed_trades else None,
            "avgWin": round(sum(safe_float(item.get("pnl"), 0) for item in wins) / len(wins), 8) if wins else None,
            "avgLoss": round(sum(safe_float(item.get("pnl"), 0) for item in losses) / len(losses), 8) if losses else None,
        },
        "warnings": warnings,
    }


def compact_active_observation(payload: dict) -> dict:
    return {
        "activeMarket": payload.get("activeMarket"),
        "session": payload.get("session"),
        "tickReadiness": payload.get("tickReadiness"),
        "signals": payload.get("signals"),
        "warnings": payload.get("warnings"),
        "trades": {
            "tradeEventCount": (payload.get("trades") or {}).get("tradeEventCount"),
            "openCount": (payload.get("trades") or {}).get("openCount"),
            "closedCount": (payload.get("trades") or {}).get("closedCount"),
        },
        "observationTargets": payload.get("observationTargets"),
        "observationCounters": payload.get("observationCounters"),
        "runnerTicksRun": payload.get("runnerTicksRun"),
        "runnerTicksSkipped": payload.get("runnerTicksSkipped"),
        "processedCandleDeltaTotal": payload.get("processedCandleDeltaTotal"),
        "counterConsistencyStatus": payload.get("counterConsistencyStatus"),
        "nextAction": payload.get("nextAction"),
    }


def build_paper_active_observation(args) -> dict:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_key = paper_market_key(active)
    session_summary = build_paper_session_summary(args)
    tick_readiness_payload = build_paper_tick_readiness(args)
    active_freshness = ((tick_readiness_payload.get("freshness") or {}).get("active") or [{}])[0]
    tick_state = tick_readiness_payload.get("tickReadiness") or {}
    active_args = {**dict(args), "activeOnly": "true"}
    active_events = build_paper_session_events({**active_args, "limit": "200", "currentSession": "true"})
    active_event_summary = build_paper_session_events_summary(active_args)
    active_trades = build_paper_session_trades(active_args)
    active_targets = build_paper_observation_targets(active_args)
    observation_counters = build_paper_observation_counters(active_args)
    compact_counters = compact_observation_counters(observation_counters)
    events = active_events.get("events") or []
    signal_events = [event for event in events if paper_event_type_bucket(event) == "signal"]
    warning_events = [event for event in events if paper_event_type_bucket(event) in {"warning", "state_warning"}]
    trade_events = active_trades.get("recentTradeEvents") or []
    target_compact = compact_observation_targets(active_targets)
    if real_enabled:
        next_action = {"action": "DISABLE_REAL_TRADING_FLAG", "reason": real_detail}
    elif not active:
        next_action = {"action": "REVIEW_PAPER_CANDIDATE_CONFIG", "reason": "No active paper market is configured."}
    elif tick_state.get("status") == "READY":
        next_action = tick_readiness_payload.get("nextAction") or {"action": "RUN_PAPER_ONCE_WHEN_READY", "reason": "Active market has a useful closed candle available."}
    else:
        next_action = active_targets.get("nextAction") or tick_readiness_payload.get("nextAction")
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "activeMarket": {
            "symbol": active.get("symbol"),
            "timeframe": active.get("interval") or active.get("timeframe"),
            "marketKey": active_key,
            "source": candidate.get("source"),
        },
        "session": {
            "status": (session_summary.get("session") or {}).get("status"),
            "startedAt": (session_summary.get("session") or {}).get("startedAt"),
            "endedAt": (session_summary.get("session") or {}).get("endedAt"),
            "durationSeconds": (session_summary.get("session") or {}).get("durationSeconds"),
            "activeSessionEventCount": (active_event_summary.get("counts") or {}).get("currentSessionEvents"),
        },
        "freshness": active_freshness,
        "tickReadiness": {
            "status": tick_state.get("status"),
            "usefulNow": tick_state.get("usefulNow"),
            "latestClosedCandleTime": tick_state.get("latestClosedCandleTime"),
            "lastProcessedCandleTime": tick_state.get("lastProcessedCandleTime"),
            "nextUsefulTickAt": tick_state.get("nextUsefulTickAt"),
            "secondsUntilNextUsefulTick": tick_state.get("secondsUntilNextUsefulTick"),
            "activeMarketReason": tick_state.get("activeMarketReason"),
        },
        "signals": {
            "count": len(signal_events),
            "latest": signal_events[-1] if signal_events else None,
        },
        "warnings": {
            "count": len(warning_events),
            "latest": warning_events[-1] if warning_events else None,
            "items": warning_events[-5:],
        },
        "trades": {
            "tradeEventCount": (active_trades.get("totals") or {}).get("recentTradeEvents", 0),
            "currentSessionTradeEvents": (active_trades.get("totals") or {}).get("currentSessionTradeEvents", 0),
            "openCount": len(active_trades.get("openTrades") or []),
            "closedCount": (active_trades.get("totals") or {}).get("closedTrades", 0),
            "openPosition": (active_trades.get("openTrades") or [None])[-1],
            "latestClosedTrade": (active_trades.get("closedTrades") or [None])[-1],
            "latestTradeEvent": (trade_events or [None])[-1],
        },
        "observationTargets": target_compact,
        "observationCounters": compact_counters,
        "runnerTicksRun": compact_counters.get("runnerTicksRun"),
        "runnerTicksSkipped": compact_counters.get("runnerTicksSkipped"),
        "processedCandleDeltaTotal": compact_counters.get("processedCandleDeltaTotal"),
        "counterConsistencyStatus": compact_counters.get("counterConsistencyStatus"),
        "nextAction": next_action,
    }


def build_paper_active_signal_diagnostics(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    command = package_node_script_args("paper:signal-diagnostics")
    limit = min(max(int(safe_float(args.get("limit", 20), 20)), 1), 100)
    command.extend(["--limit", str(limit)])
    refresh_requested = str(args.get("refresh", "false")).strip().lower() in {"1", "true", "yes", "on"}
    if refresh_requested:
        command.append("--refresh")
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 90), 90)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Active signal diagnostics timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "refreshRequested": refresh_requested,
            "limit": limit,
            "command": " ".join(command),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Active signal diagnostics returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Active signal diagnostics returned no output."}
    payload["paperEnabled"] = paper_enabled
    payload["realTradingEnabled"] = real_enabled
    payload["refreshRequested"] = refresh_requested
    payload["limit"] = limit
    payload["command"] = " ".join(command)
    payload["consistencyWarnings"] = paper_enabled_consistency_warnings(candidate, paper_enabled)
    if real_enabled:
        payload.setdefault("warnings", []).append(real_detail)
    if completed.returncode != 0:
        payload["ok"] = False
        payload["returnCode"] = completed.returncode
        if completed.stderr.strip():
            payload["stderr"] = completed.stderr.strip()
    return payload, 200 if payload.get("ok") else 502


def timeframe_candles_per_month(timeframe: str) -> float:
    seconds = paper_interval_seconds(timeframe) or 3600
    return 30.4375 * 86400 / max(seconds, 1)


def build_paper_forward_signal_diagnostics(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    report = build_paper_observation_report({**dict(args), "activeOnly": "true"})
    evidence = report.get("evidence") or {}
    ticks = int(safe_float(evidence.get("runnerTicksRun", evidence.get("ticksObserved")), 0))
    signals = int(safe_float(evidence.get("signalsObserved"), 0))
    closed_trades = int(safe_float(evidence.get("closedTrades"), 0))
    activity, activity_status = build_research_activity_lab({
        "symbols": symbol,
        "timeframes": timeframe,
        "strategies": strategy,
        "period": period,
        "limit": args.get("limit", "auto"),
        "optimize": "false",
    })
    activity_row = (activity.get("rows") or [None])[0] or {}
    trades_per_month = safe_float(activity_row.get("tradesPerMonth"), 0)
    candles_per_month = timeframe_candles_per_month(timeframe)
    expected_per_tick = trades_per_month / max(candles_per_month, 1)
    expected_for_ticks = ticks * expected_per_tick
    diagnostic, diagnostic_status = build_paper_active_signal_diagnostics({"limit": args.get("signalLimit", "20")})
    latest = diagnostic.get("diagnostics") or {}
    blockers = latest.get("blockers") or latest.get("failedConditions") or []
    warnings = dedupe_list(
        (report.get("warnings") or [])
        + (activity.get("warnings") or [])
        + (diagnostic.get("warnings") or [])
        + ([real_detail] if real_enabled else [])
    )
    if real_enabled:
        status = "BLOCKED"
        summary = "Real trading appears enabled; signal diagnostics are safety-blocked until reviewed."
        next_action = {"action": "DISABLE_REAL_TRADING_FLAG", "reason": real_detail}
    elif ticks <= 0:
        status = "NO_FORWARD_DATA"
        summary = "No useful forward paper ticks are available yet."
        next_action = {"action": "RUN_PAPER_ONCE_WHEN_READY", "reason": "Collect useful paper ticks only when tick readiness says it is useful."}
    elif signals > 0:
        status = "ACTIVE"
        summary = f"Forward paper has produced {signals} signal(s) across {ticks} useful tick(s)."
        next_action = {"action": "REVIEW_FORWARD_SIGNALS", "reason": "Inspect signal events and paper-only trade behavior."}
    elif expected_for_ticks < 1:
        status = "NORMAL_QUIET"
        summary = f"Zero signals is not surprising yet; historical activity implies only about {round(expected_for_ticks, 2)} expected trade-like event(s) across {ticks} tick(s)."
        next_action = {"action": "OBSERVE_MORE", "reason": "Continue paper-only observation before judging signal frequency."}
    elif expected_for_ticks < 3:
        status = "WATCH_QUIET"
        summary = f"Zero signals is quiet but still plausible; historical expectation is about {round(expected_for_ticks, 2)} event(s)."
        next_action = {"action": "OBSERVE_MORE", "reason": "Collect more useful ticks and review blocker diagnostics."}
    else:
        status = "SUSPICIOUSLY_QUIET"
        summary = f"Zero signals is lower than expected; historical activity implies about {round(expected_for_ticks, 2)} event(s) across this many ticks."
        next_action = {"action": "REVIEW_SIGNAL_BLOCKERS", "reason": "Inspect active signal diagnostics and recent market state before more paper interpretation."}
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "activeMarket": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy},
        "forward": {
            "usefulTicks": ticks,
            "signalsObserved": signals,
            "closedTrades": closed_trades,
            "observationStatus": (report.get("verdict") or {}).get("status"),
        },
        "historicalExpectation": {
            "period": period,
            "activityStatus": activity_row.get("status"),
            "historicalTrades": activity_row.get("trades"),
            "tradesPerMonth": trades_per_month,
            "candlesPerMonth": round(candles_per_month, 4),
            "expectedEventsPerUsefulTick": round(expected_per_tick, 6),
            "expectedEventsForObservedTicks": round(expected_for_ticks, 4),
        },
        "latestSignalDiagnostics": {
            "status": diagnostic.get("status"),
            "signal": diagnostic.get("signal") or latest.get("signal"),
            "reason": diagnostic.get("reason") or latest.get("reason"),
            "blockers": blockers,
            "nextAction": diagnostic.get("nextAction"),
        },
        "verdict": {
            "status": status,
            "summary": summary,
            "nextAction": next_action,
        },
        "sourceStatuses": {
            "activity": activity_status,
            "signalDiagnostics": diagnostic_status,
        },
        "warnings": warnings,
    }, 200


def build_paper_candidate_comparison(args) -> dict:
    candidate = load_paper_candidate_config()
    real_enabled, real_detail = paper_real_trading_enabled()
    symbols = parse_csv_arg(args.get("symbols"), ["ETHUSDT", "BTCUSDT"])
    timeframes = parse_csv_arg(args.get("timeframes"), ["15m", "1h"])
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    limit_raw = args.get("limit", "auto")
    max_combos = max(1, min(int(safe_float(args.get("maxCombos", args.get("max_combos", 8)), 8)), 20))
    requested_pairs = [(symbol, timeframe) for symbol in symbols for timeframe in timeframes]
    pairs = requested_pairs[:max_combos]
    rules = candidate_validation_rules({
        "period": period,
        "limit": "5000",
    })
    rules["limitRaw"] = limit_raw
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("takerFeePct", 0))
    slippage_pct = safe_float(candidate.get("slippageBps", 0)) / 100
    active = primary_active_market(candidate)
    active_symbol = active.get("symbol")
    active_timeframe = active.get("interval") or active.get("timeframe")
    rows = []
    for symbol, timeframe in pairs:
        rows.append(compare_candidate_market_row(
            candidate,
            symbol,
            timeframe,
            strategy,
            period,
            rules,
            params,
            fee_pct,
            slippage_pct,
            active_symbol,
            active_timeframe,
        ))
    active_rows = [row for row in rows if row.get("comparableToActive")]
    active_trades = max([int(safe_float(row.get("trades"), 0)) for row in active_rows] or [0])
    for row in rows:
        row["diagnostics"]["moreActiveThanCurrent"] = int(safe_float(row.get("trades"), 0)) > active_trades
    recommendation = candidate_comparison_recommendation(rows, active_symbol, active_timeframe)
    warnings = []
    if len(requested_pairs) > len(pairs):
        warnings.append(f"Comparison capped at {max_combos} combination(s); narrow symbols/timeframes or raise maxCombos intentionally.")
    if real_enabled:
        warnings.append(real_detail)
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "realTradingEnabled": real_enabled,
        "paperEnabled": canonical_paper_enabled(candidate),
        "activePaperCandidate": candidate_summary(candidate),
        "request": {
            "symbols": symbols,
            "timeframes": timeframes,
            "strategy": strategy,
            "period": period,
            "limit": limit_raw,
            "maxCombos": max_combos,
            "evaluatedCombos": len(rows),
        },
        "rows": rows,
        "recommendation": recommendation,
        "warnings": warnings,
    }


def build_research_activity_lab(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    symbols = parse_csv_arg(args.get("symbols"), ["ETHUSDT", "BTCUSDT"])
    timeframes = parse_csv_arg(args.get("timeframes"), ["15m", "1h"])
    strategies = parse_csv_arg(args.get("strategies"), ["SimpleAtrTrendV2"])
    period = args.get("period", "365d")
    optimize = str(args.get("optimize", "false")).strip().lower() in {"1", "true", "yes", "on"}
    default_max = 50 if optimize else max(1, len(symbols) * len(timeframes) * len(strategies))
    max_combos = max(1, min(int(safe_float(args.get("maxCombos", args.get("max_combos", default_max)), default_max)), 200))
    fee_pct = safe_float(args.get("feePct", args.get("fee_pct", candidate.get("takerFeePct", 0.055))), 0.055)
    slippage_pct = safe_float(args.get("slippagePct", args.get("slippage_pct", safe_float(candidate.get("slippageBps", 2), 2) / 100)), 0.02)
    limit_raw = args.get("limit", "auto")
    command = package_node_script_args("research:activity-lab")
    command.extend([
        "--symbols", ",".join(symbols),
        "--timeframes", ",".join(timeframes),
        "--strategies", ",".join(strategies),
        "--period", period,
        "--maxCombos", str(max_combos),
        "--limit", str(limit_raw),
        "--optimize", "true" if optimize else "false",
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
        "--activeStrategy", str(candidate.get("strategy") or "SimpleAtrTrendV2"),
        "--activeParams", json.dumps(candidate.get("params") or {}),
    ])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 240), 240)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Research activity lab timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "activePaperCandidate": candidate_summary(candidate),
            "search": {"symbols": symbols, "timeframes": timeframes, "strategies": strategies, "period": period, "optimize": optimize, "maxCombos": max_combos},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Activity lab timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Research activity lab returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Research activity lab returned no output."}
    rows = [normalize_activity_lab_row(row) for row in payload.get("rows", [])]
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Research activity lab command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "realTradingEnabled": real_enabled,
        "paperEnabled": paper_enabled,
        "search": payload.get("search") or {"symbols": symbols, "timeframes": timeframes, "strategies": strategies, "period": period, "optimize": optimize, "maxCombos": max_combos, "limit": limit_raw, "feePct": fee_pct, "slippagePct": slippage_pct},
        "activePaperCandidate": candidate_summary(candidate),
        "rows": rows,
        "summary": activity_lab_summary(rows),
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def normalize_activity_lab_row(row: dict) -> dict:
    return {
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "mode": row.get("mode"),
        "status": row.get("status") or "FAIL",
        "trades": int(safe_float(row.get("trades"), 0)),
        "tradesPerDay": safe_float(row.get("tradesPerDay"), 0),
        "tradesPerMonth": safe_float(row.get("tradesPerMonth"), 0),
        "avgBarsHeld": safe_float(row.get("avgBarsHeld"), 0),
        "totalReturnPct": safe_float(row.get("totalReturnPct"), 0),
        "profitFactor": safe_float(row.get("profitFactor"), 0),
        "winRate": safe_float(row.get("winRate"), 0),
        "maxDrawdownPct": safe_float(row.get("maxDrawdownPct"), 0),
        "expectancyPctPerTrade": safe_float(row.get("expectancyPctPerTrade"), 0),
        "feesEstimatedPct": safe_float(row.get("feesEstimatedPct"), 0),
        "score": safe_float(row.get("score"), 0),
        "qualityStatus": row.get("qualityStatus") or row.get("status") or "FAIL",
        "mainFailureReason": row.get("mainFailureReason") or "ERROR",
        "warnings": row.get("warnings") or [],
        "params": row.get("params") or {},
    }


def activity_lab_viable(row: dict) -> bool:
    return (
        row.get("status") in {"PASS", "WARN"}
        and int(safe_float(row.get("trades"), 0)) >= 20
        and safe_float(row.get("totalReturnPct"), 0) > 0
        and safe_float(row.get("profitFactor"), 0) > 1
        and safe_float(row.get("expectancyPctPerTrade"), 0) > 0
        and safe_float(row.get("maxDrawdownPct"), 999) <= 25
    )


def best_activity_row(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return sorted(rows, key=lambda row: (
        1 if activity_lab_viable(row) else 0,
        safe_float(row.get("score"), 0),
        safe_float(row.get("profitFactor"), 0),
        safe_float(row.get("totalReturnPct"), 0),
        safe_float(row.get("tradesPerMonth"), 0),
    ), reverse=True)[0]


def activity_lab_summary(rows: list[dict]) -> dict:
    viable = [row for row in rows if activity_lab_viable(row)]
    best_overall = best_activity_row(rows)
    most_active_passing = sorted(viable, key=lambda row: safe_float(row.get("tradesPerMonth"), 0), reverse=True)[0] if viable else None
    best_15m = best_activity_row([row for row in rows if str(row.get("timeframe")) == "15m"])
    best_1h = best_activity_row([row for row in rows if str(row.get("timeframe")) == "1h"])
    fastest_viable = most_active_passing
    if fastest_viable:
        recommendation = {
            "action": "REVIEW_ACTIVITY_CANDIDATE",
            "reason": f"{fastest_viable.get('strategy')} {fastest_viable.get('symbol')} {fastest_viable.get('timeframe')} is viable in this read-only lab with {fastest_viable.get('tradesPerMonth')} trades/month. Review only; no promotion is automatic.",
        }
    elif best_overall:
        recommendation = {
            "action": "KEEP_CURRENT_OBSERVATION",
            "reason": "No lab row met the viability gate. Continue observing the current promoted paper candidate or widen research intentionally.",
        }
    else:
        recommendation = {
            "action": "NO_ACTION",
            "reason": "Activity lab returned no rows.",
        }
    return {
        "bestOverall": best_overall,
        "mostActivePassing": most_active_passing,
        "best15m": best_15m,
        "best1h": best_1h,
        "fastestViable": fastest_viable,
        "recommendation": recommendation,
    }


def build_research_candidate_leaderboard(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_symbol = active.get("symbol") or "ETHUSDT"
    active_timeframe = active.get("interval") or active.get("timeframe") or "1h"
    active_strategy = candidate.get("strategy") or "SimpleAtrTrendV2"
    symbols = parse_csv_arg(args.get("symbols"), ["ETHUSDT", "BTCUSDT", "SOLUSDT"])
    timeframes = parse_csv_arg(args.get("timeframes"), ["1h", "4h"])
    strategies = parse_csv_arg(args.get("strategies"), ["SimpleAtrTrendV2"])
    period = args.get("period", "365d")
    requested = len(symbols) * len(timeframes) * len(strategies)
    default_max = requested if str(args.get("maxCombos", args.get("max_combos", "auto"))).strip().lower() == "auto" else 24
    max_combos = max(1, min(int(safe_float(args.get("maxCombos", args.get("max_combos", default_max)), default_max)), 60))
    include_robustness = str(args.get("includeRobustness", "false")).strip().lower() in {"1", "true", "yes", "on"}
    include_variant = str(args.get("includeVariantSummary", "false")).strip().lower() in {"1", "true", "yes", "on"}
    activity_payload, activity_status = build_research_activity_lab({
        "symbols": ",".join(symbols),
        "timeframes": ",".join(timeframes),
        "strategies": ",".join(strategies),
        "period": period,
        "maxCombos": str(max_combos),
        "limit": args.get("limit", "auto"),
        "optimize": "false",
        "timeout_seconds": args.get("timeout_seconds", "240"),
    })
    rows = []
    for row in activity_payload.get("rows") or []:
        next_row = candidate_leaderboard_row(row, active_strategy, active_symbol, active_timeframe)
        rows.append(next_row)
    rows = sorted(rows, key=lambda item: candidate_leaderboard_sort_key(item), reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    warnings = list(activity_payload.get("warnings") or [])
    if real_enabled:
        warnings.append(real_detail)
    if activity_status >= 400 or activity_payload.get("ok") is False:
        warnings.append("Activity lab did not complete cleanly; leaderboard rows may be incomplete.")
    if include_robustness:
        for row in rows[: min(3, len(rows))]:
            robust_payload, robust_status = build_research_parameter_robustness({
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "strategy": row.get("strategy"),
                "period": period,
                "maxVariants": args.get("robustnessMaxVariants", "20"),
                "includeBase": "true",
            })
            row["robustness"] = compact_candidate_robustness_evidence(robust_payload)
            if robust_status >= 400 or robust_payload.get("ok") is False:
                row.setdefault("warnings", []).append("Bounded robustness summary failed for this row.")
    variant_summary = None
    if include_variant:
        variant_target = next((row for row in rows if row.get("isActivePaperCandidate")), rows[0] if rows else None)
        if variant_target:
            variant_payload, variant_status = build_research_strategy_variant_lab({
                "symbol": variant_target.get("symbol"),
                "timeframe": variant_target.get("timeframe"),
                "baseStrategy": variant_target.get("strategy"),
                "period": period,
                "maxVariants": args.get("variantMaxVariants", "10"),
            })
            variant_summary = compact_candidate_variant_evidence(variant_payload)
            if variant_status >= 400 or variant_payload.get("ok") is False:
                warnings.append("Bounded variant summary failed for the selected leaderboard row.")
    summary = candidate_leaderboard_summary(rows)
    if variant_summary:
        summary["variantSummary"] = variant_summary
    return {
        "ok": activity_payload.get("ok", True) is not False and activity_status < 400,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "search": {
            "symbols": symbols,
            "timeframes": timeframes,
            "strategies": strategies,
            "period": period,
            "maxCombos": max_combos,
            "includeRobustness": include_robustness,
            "includeVariantSummary": include_variant,
        },
        "activePaperCandidate": candidate_summary(candidate),
        "rows": rows,
        "summary": summary,
        "warnings": dedupe_list([warning for warning in warnings if warning]),
    }, 200 if activity_status < 400 else 502


def candidate_leaderboard_row(row: dict, active_strategy: str, active_symbol: str, active_timeframe: str) -> dict:
    is_active = (
        str(row.get("strategy")) == str(active_strategy)
        and str(row.get("symbol")) == str(active_symbol)
        and str(row.get("timeframe")) == str(active_timeframe)
    )
    score = candidate_leaderboard_score(row)
    recommendation = "KEEP_CURRENT" if is_active and row.get("status") in {"PASS", "WARN"} else (
        "REVIEW_NEW_CANDIDATE" if row.get("status") in {"PASS", "WARN"} and safe_float(row.get("profitFactor"), 0) >= 1.1 and safe_float(row.get("totalReturnPct"), 0) > 0 else
        "RESEARCH_MORE" if row.get("status") in {"FAIL", "ERROR"} else
        "NO_ACTION"
    )
    return {
        "rank": None,
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "status": row.get("status") or "FAIL",
        "totalReturnPct": safe_float(row.get("totalReturnPct"), 0),
        "profitFactor": safe_float(row.get("profitFactor"), 0),
        "maxDrawdownPct": safe_float(row.get("maxDrawdownPct"), 0),
        "winRate": safe_float(row.get("winRate"), 0),
        "trades": int(safe_float(row.get("trades"), 0)),
        "tradesPerMonth": safe_float(row.get("tradesPerMonth"), 0),
        "expectancyPctPerTrade": safe_float(row.get("expectancyPctPerTrade"), 0),
        "score": score,
        "qualityStatus": row.get("qualityStatus") or row.get("status") or "FAIL",
        "mainFailureReason": row.get("mainFailureReason") or candidate_leaderboard_failure_reason(row),
        "recommendation": recommendation,
        "isActivePaperCandidate": is_active,
        "warnings": row.get("warnings") or [],
    }


def candidate_leaderboard_score(row: dict) -> float:
    trades = safe_float(row.get("trades"), 0)
    trades_per_month = safe_float(row.get("tradesPerMonth"), 0)
    pf = safe_float(row.get("profitFactor"), 0)
    total_return = safe_float(row.get("totalReturnPct"), 0)
    drawdown = safe_float(row.get("maxDrawdownPct"), 0)
    expectancy = safe_float(row.get("expectancyPctPerTrade"), 0)
    status_bonus = 30 if row.get("status") == "PASS" else 12 if row.get("status") == "WARN" else -25
    score = (
        status_bonus
        + min(trades, 150) * 0.16
        + min(trades_per_month, 12) * 1.6
        + pf * 18
        + total_return * 2.2
        + expectancy * 12
        - drawdown * 1.25
    )
    if pf < 1.1:
        score -= 18
    if total_return <= 0:
        score -= 20
    if trades < 20:
        score -= 15
    if drawdown > 25:
        score -= 20
    return round(score, 5)


def candidate_leaderboard_failure_reason(row: dict) -> str:
    if safe_float(row.get("trades"), 0) <= 0:
        return "NO_TRADES"
    if safe_float(row.get("trades"), 0) < 20:
        return "TOO_FEW_TRADES"
    if safe_float(row.get("totalReturnPct"), 0) <= 0:
        return "NEGATIVE_RETURN"
    if safe_float(row.get("profitFactor"), 0) < 1.1:
        return "WEAK_PROFIT_FACTOR"
    if safe_float(row.get("maxDrawdownPct"), 0) > 25:
        return "HIGH_DRAWDOWN"
    return row.get("mainFailureReason") or "OK"


def candidate_leaderboard_sort_key(row: dict) -> tuple:
    return (
        1 if row.get("status") == "PASS" else 0,
        1 if row.get("status") == "WARN" else 0,
        safe_float(row.get("score"), 0),
        safe_float(row.get("profitFactor"), 0),
        safe_float(row.get("totalReturnPct"), 0),
        safe_float(row.get("tradesPerMonth"), 0),
    )


def candidate_leaderboard_summary(rows: list[dict]) -> dict:
    best_overall = rows[0] if rows else None
    best_by_timeframe = {}
    best_by_symbol = {}
    for row in rows:
        timeframe = str(row.get("timeframe") or "")
        symbol = str(row.get("symbol") or "")
        if timeframe and timeframe not in best_by_timeframe:
            best_by_timeframe[timeframe] = row
        if symbol and symbol not in best_by_symbol:
            best_by_symbol[symbol] = row
    active = next((row for row in rows if row.get("isActivePaperCandidate")), None)
    pass_count = len([row for row in rows if row.get("status") in {"PASS", "WARN"}])
    fail_count = len([row for row in rows if row.get("status") not in {"PASS", "WARN"}])
    if not rows:
        recommendation = {"action": "NO_ACTION", "reason": "No leaderboard rows were returned."}
    elif active and best_overall and active.get("rank") == best_overall.get("rank"):
        recommendation = {"action": "KEEP_CURRENT", "reason": "The active paper candidate is the top ranked research row. Continue paper-only observation."}
    elif best_overall and best_overall.get("status") in {"PASS", "WARN"} and (not active or safe_float(best_overall.get("score"), 0) > safe_float(active.get("score"), 0) + 10):
        recommendation = {
            "action": "REVIEW_NEW_CANDIDATE",
            "reason": f"{best_overall.get('strategy')} {best_overall.get('symbol')} {best_overall.get('timeframe')} ranks above the active paper candidate for research review only.",
        }
    elif pass_count:
        recommendation = {"action": "KEEP_CURRENT", "reason": "Passing rows exist, but no alternative clearly beats the active candidate enough for this read-only leaderboard."}
    else:
        recommendation = {"action": "RESEARCH_MORE", "reason": "No candidate rows passed the leaderboard gate. Widen research intentionally."}
    return {
        "bestOverall": best_overall,
        "bestByTimeframe": best_by_timeframe,
        "bestBySymbol": best_by_symbol,
        "activeCandidateRank": active.get("rank") if active else None,
        "activeCandidate": active,
        "passCount": pass_count,
        "failCount": fail_count,
        "recommendation": recommendation,
    }


def build_research_fee_slippage_stress(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    params, params_error, params_source = research_params_from_args(candidate, args)
    context = candidate_context_from_config(candidate)
    maker_fee = context["makerFeePct"]
    taker_fee = context["takerFeePct"]
    slippage_bps = context["slippageBps"]
    candidate_identity = candidate_identity_from_parts(strategy, symbol, timeframe, params or {}, context["fillModel"], maker_fee, taker_fee, slippage_bps)
    params_meta = validation_params_meta(params or {}, params_source, candidate_identity)
    if params_error:
        return {
            **validation_not_comparable(params_error, params_meta),
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "search": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period},
        }, 400
    command = package_node_script_args("research:fee-slippage-stress")
    command.extend([
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--strategy", strategy,
        "--period", period,
        "--scenarios", args.get("scenarios", "default"),
        "--baseParams", json.dumps(params or {}),
        "--makerFeePct", str(maker_fee),
        "--takerFeePct", str(taker_fee),
        "--slippageBps", str(slippage_bps),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 240), 240)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Fee/slippage stress lab timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "baseCostModel": {"makerFeePct": maker_fee, "takerFeePct": taker_fee, "slippageBps": slippage_bps},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Fee/slippage stress lab timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Fee/slippage stress lab returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Fee/slippage stress lab returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Fee/slippage stress lab command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "candidateKey": candidate_identity["candidateKey"],
        "paramsUsed": params_meta["paramsUsed"],
        "normalizedParams": params_meta["normalizedParams"],
        "paramsHash": params_meta["paramsHash"],
        "paramsSource": params_meta["paramsSource"],
        "candidateParamsHash": params_meta["candidateParamsHash"],
        "paramsMatchCandidate": params_meta["paramsMatchCandidate"],
        "search": payload.get("search") or {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "scenarios": args.get("scenarios", "default")},
        "baseCostModel": payload.get("baseCostModel") or {"makerFeePct": maker_fee, "takerFeePct": taker_fee, "slippageBps": slippage_bps},
        "rows": payload.get("rows") or [],
        "stress": payload.get("stress") or {},
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_walk_forward_review(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    folds = max(1, min(int(safe_float(args.get("folds", 4), 4)), 12))
    recent_windows = args.get("recentWindows", args.get("recent_windows", "90,180,365"))
    params, params_error, params_source = research_params_from_args(candidate, args)
    context = candidate_context_from_config(candidate)
    maker_fee = context["makerFeePct"]
    taker_fee = context["takerFeePct"]
    slippage_bps = context["slippageBps"]
    candidate_identity = candidate_identity_from_parts(strategy, symbol, timeframe, params or {}, context["fillModel"], maker_fee, taker_fee, slippage_bps)
    params_meta = validation_params_meta(params or {}, params_source, candidate_identity)
    if params_error:
        return {
            **validation_not_comparable(params_error, params_meta),
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "search": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "folds": folds, "recentWindows": recent_windows},
        }, 400
    command = package_node_script_args("research:walk-forward-review")
    command.extend([
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--strategy", strategy,
        "--period", period,
        "--folds", str(folds),
        "--recentWindows", str(recent_windows),
        "--baseParams", json.dumps(params or {}),
        "--makerFeePct", str(maker_fee),
        "--takerFeePct", str(taker_fee),
        "--slippageBps", str(slippage_bps),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 240), 240)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Walk-forward review timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Walk-forward review timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Walk-forward review returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Walk-forward review returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Walk-forward review command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "candidateKey": candidate_identity["candidateKey"],
        "paramsUsed": params_meta["paramsUsed"],
        "normalizedParams": params_meta["normalizedParams"],
        "paramsHash": params_meta["paramsHash"],
        "paramsSource": params_meta["paramsSource"],
        "candidateParamsHash": params_meta["candidateParamsHash"],
        "paramsMatchCandidate": params_meta["paramsMatchCandidate"],
        "search": payload.get("search") or {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "folds": folds, "recentWindows": recent_windows},
        "full": payload.get("full") or {},
        "recentWindows": payload.get("recentWindows") or [],
        "folds": payload.get("folds") or [],
        "stability": payload.get("stability") or {},
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_regime_breakdown(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    regime_basis = args.get("regimeBasis", args.get("regime_basis", "symbol1h"))
    include_trades = str(args.get("includeTrades", args.get("include_trades", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    params, params_error, params_source = research_params_from_args(candidate, args)
    context = candidate_context_from_config(candidate)
    maker_fee = context["makerFeePct"]
    taker_fee = context["takerFeePct"]
    slippage_bps = context["slippageBps"]
    candidate_identity = candidate_identity_from_parts(strategy, symbol, timeframe, params or {}, context["fillModel"], maker_fee, taker_fee, slippage_bps)
    params_meta = validation_params_meta(params or {}, params_source, candidate_identity)
    if params_error:
        return {
            **validation_not_comparable(params_error, params_meta),
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "search": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "regimeBasis": regime_basis, "includeTrades": include_trades},
        }, 400
    command = package_node_script_args("research:regime-breakdown")
    command.extend([
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--strategy", strategy,
        "--period", period,
        "--regimeBasis", str(regime_basis),
        "--includeTrades", "true" if include_trades else "false",
        "--baseParams", json.dumps(params or {}),
        "--makerFeePct", str(maker_fee),
        "--takerFeePct", str(taker_fee),
        "--slippageBps", str(slippage_bps),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 240), 240)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Regime breakdown lab timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Regime breakdown lab timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Regime breakdown lab returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Regime breakdown lab returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Regime breakdown lab command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "candidateKey": candidate_identity["candidateKey"],
        "paramsUsed": params_meta["paramsUsed"],
        "normalizedParams": params_meta["normalizedParams"],
        "paramsHash": params_meta["paramsHash"],
        "paramsSource": params_meta["paramsSource"],
        "candidateParamsHash": params_meta["candidateParamsHash"],
        "paramsMatchCandidate": params_meta["paramsMatchCandidate"],
        "search": payload.get("search") or {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "regimeBasis": regime_basis, "includeTrades": include_trades},
        "full": payload.get("full") or {},
        "summary": payload.get("summary") or {},
        "regimes": payload.get("regimes") or [],
        "tradeSamples": payload.get("tradeSamples") or [],
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_regime_filter_counterfactual(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    folds = max(1, min(int(safe_float(args.get("folds", 4), 4)), 12))
    include_stress = str(args.get("includeStress", args.get("include_stress", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    include_recent = str(args.get("includeRecentWindows", args.get("include_recent_windows", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    save = str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"}
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    for field in ("fillModel", "accountEquity", "riskPct", "maxOpenTrades", "maxNotional", "maxNotionalPerTrade", "regimeMode"):
        if candidate.get(field) is not None and params.get(field) is None:
            params[field] = candidate.get(field)
    if args.get("params"):
        try:
            params.update(json.loads(args.get("params")))
        except Exception as exc:
            return {
                "ok": False,
                "error": f"params must be valid JSON: {exc}",
                "paperEnabled": paper_enabled,
                "realTradingEnabled": real_enabled,
                "candidate": candidate_summary(candidate),
                "warnings": ["Regime filter counterfactual did not run because params JSON was invalid."],
            }, 400
    maker_fee = safe_float(candidate.get("makerFeePct"), 0)
    taker_fee = safe_float(candidate.get("takerFeePct"), safe_float(candidate.get("feePct"), 0.055))
    slippage_bps = safe_float(candidate.get("slippageBps"), safe_float(candidate.get("slippagePct"), 0.02) * 100)
    command = package_node_script_args("research:regime-filter-counterfactual")
    command.extend([
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--strategy", strategy,
        "--period", period,
        "--folds", str(folds),
        "--filterSet", str(args.get("filterSet", args.get("filter_set", "default"))),
        "--baseParams", json.dumps(params),
        "--makerFeePct", str(maker_fee),
        "--takerFeePct", str(taker_fee),
        "--slippageBps", str(slippage_bps),
        "--includeStress", "true" if include_stress else "false",
        "--includeRecentWindows", "true" if include_recent else "false",
        "--save", "true" if save else "false",
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 360), 360)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Regime filter counterfactual lab timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Counterfactual lab timed out before returning variants."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Regime filter counterfactual lab returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Regime filter counterfactual lab returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Regime filter counterfactual lab command failed.")
    response = dict(payload)
    response.update({
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "warnings": warnings,
        "command": " ".join(command),
    })
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_stability_first_challenger_search(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_symbol = active.get("symbol") or "ETHUSDT"
    active_timeframe = active.get("interval") or active.get("timeframe") or "1h"
    active_strategy = candidate.get("strategy") or "SimpleAtrTrendV2"
    active_params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    for field in ("fillModel", "accountEquity", "riskPct", "maxOpenTrades", "maxNotional", "maxNotionalPerTrade", "regimeMode"):
        if candidate.get(field) is not None and active_params.get(field) is None:
            active_params[field] = candidate.get(field)
    maker_fee = safe_float(candidate.get("makerFeePct"), 0)
    taker_fee = safe_float(candidate.get("takerFeePct"), safe_float(candidate.get("feePct"), 0.055))
    slippage_bps = safe_float(candidate.get("slippageBps"), safe_float(candidate.get("slippagePct"), 0.02) * 100)
    fill_model = candidate.get("fillModel") or "next-open"
    command = package_node_script_args("research:stability-first-challenger-search")
    command.extend([
        "--symbols", str(args.get("symbols", "ETHUSDT,BTCUSDT")),
        "--timeframes", str(args.get("timeframes", "1h,4h")),
        "--strategies", str(args.get("strategies", "all")),
        "--period", str(args.get("period", "365d")),
        "--folds", str(max(2, min(int(safe_float(args.get("folds", 4), 4)), 8))),
        "--maxCombosPerStrategy", str(max(1, min(int(safe_float(args.get("maxCombosPerStrategy", args.get("max_combos_per_strategy", 50)), 50)), 150))),
        "--topN", str(max(1, min(int(safe_float(args.get("topN", args.get("top_n", 20)), 20)), 50))),
        "--includeStress", "true" if str(args.get("includeStress", "true")).strip().lower() in {"1", "true", "yes", "on"} else "false",
        "--includeRecentWindows", "true" if str(args.get("includeRecentWindows", "true")).strip().lower() in {"1", "true", "yes", "on"} else "false",
        "--includeReproAudit", "true" if str(args.get("includeReproAudit", "true")).strip().lower() in {"1", "true", "yes", "on"} else "false",
        "--reproReruns", str(max(1, min(int(safe_float(args.get("reproReruns", args.get("repro_reruns", 2)), 2)), 5))),
        "--save", "true" if str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"} else "false",
        "--activeStrategy", active_strategy,
        "--activeSymbol", active_symbol,
        "--activeTimeframe", active_timeframe,
        "--activeParams", json.dumps(active_params),
        "--makerFeePct", str(maker_fee),
        "--takerFeePct", str(taker_fee),
        "--slippageBps", str(slippage_bps),
        "--fillModel", str(fill_model),
        "--paperEnabled", "true" if paper_enabled else "false",
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 540), 540)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Stability-first challenger search timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "benchmark": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Search timed out before final challenger ranking was returned."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Stability-first challenger search returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Stability-first challenger search returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Stability-first challenger search command failed.")
    response = dict(payload)
    response.update({
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "warnings": warnings,
        "command": " ".join(command),
    })
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def campaign_bool(args, name: str, default: bool) -> bool:
    return str(args.get(name, "true" if default else "false")).strip().lower() in {"1", "true", "yes", "on"}


def campaign_int(args, name: str, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(safe_float(args.get(name, default), default)), maximum))


CANONICAL_CANDIDATE_IDENTITY_VERSION = "candidate-identity-v1"


def stable_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def short_hash(value) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()[:16]


def current_git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=5,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def normalized_candidate_params(params: dict | None) -> dict:
    if not isinstance(params, dict):
        return {}
    normalized = {}
    for key in sorted(params):
        value = params[key]
        if value is None:
            continue
        if isinstance(value, float):
            normalized[key] = round(value, 10)
        elif isinstance(value, dict):
            normalized[key] = normalized_candidate_params(value)
        elif isinstance(value, list):
            normalized[key] = [normalized_candidate_params(item) if isinstance(item, dict) else item for item in value]
        else:
            normalized[key] = value
    return normalized


def parse_json_arg(args, *names: str) -> tuple[dict | None, str | None]:
    for name in names:
        raw = args.get(name) if hasattr(args, "get") else None
        if raw in (None, ""):
            continue
        if isinstance(raw, dict):
            return dict(raw), None
        try:
            parsed = json.loads(str(raw))
        except Exception as exc:
            return None, f"{name} must be valid JSON: {exc}"
        if not isinstance(parsed, dict):
            return None, f"{name} must be a JSON object."
        return parsed, None
    return None, None


def research_params_from_args(candidate: dict, args) -> tuple[dict | None, str | None, str]:
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    params_source = "current_candidate"
    for field in ("fillModel", "accountEquity", "riskPct", "maxOpenTrades", "maxNotional", "maxNotionalPerTrade", "regimeMode"):
        if candidate.get(field) is not None and params.get(field) is None:
            params[field] = candidate.get(field)
    override, error = parse_json_arg(args, "baseParams", "params")
    if error:
        return None, error, params_source
    if override is not None:
        params = override
        params_source = "explicit_args"
    return params, None, params_source


def candidate_identity_from_parts(
    strategy: str | None,
    symbol: str | None,
    timeframe: str | None,
    params: dict | None,
    fill_model: str | None = None,
    maker_fee_pct=None,
    taker_fee_pct=None,
    slippage_bps=None,
) -> dict:
    normalized_params = normalized_candidate_params(params)
    params_hash = short_hash(normalized_params)
    execution_context = {
        "fillModel": fill_model or "next-open",
        "makerFeePct": safe_float(maker_fee_pct, 0),
        "takerFeePct": safe_float(taker_fee_pct, 0),
        "slippageBps": safe_float(slippage_bps, 0),
    }
    identity_payload = {
        "candidateIdentityVersion": CANONICAL_CANDIDATE_IDENTITY_VERSION,
        "strategy": strategy or "-",
        "symbol": symbol or "-",
        "timeframe": timeframe or "-",
        "normalizedParams": normalized_params,
        "paramsHash": params_hash,
        **execution_context,
    }
    execution_context_hash = short_hash(execution_context)
    candidate_key = "|".join([
        CANONICAL_CANDIDATE_IDENTITY_VERSION,
        str(identity_payload["strategy"]),
        str(identity_payload["symbol"]),
        str(identity_payload["timeframe"]),
        params_hash,
        execution_context_hash,
    ])
    return {
        **identity_payload,
        "candidateKey": candidate_key,
        "executionContextHash": execution_context_hash,
    }


def candidate_identity_from_row(row: dict, default_context: dict | None = None) -> dict:
    default_context = default_context or {}
    if row.get("candidateKey") and row.get("paramsHash"):
        return {
            "candidateKey": row.get("candidateKey"),
            "paramsHash": row.get("paramsHash"),
            "normalizedParams": row.get("normalizedParams") or normalized_candidate_params(row.get("params") or {}),
            "executionContextHash": row.get("executionContextHash"),
            "candidateIdentityVersion": row.get("candidateIdentityVersion") or CANONICAL_CANDIDATE_IDENTITY_VERSION,
            "legacyIdentity": False,
        }
    params = row.get("normalizedParams") or row.get("params") or {}
    identity = candidate_identity_from_parts(
        row.get("strategy"),
        row.get("symbol"),
        row.get("timeframe"),
        params,
        row.get("fillModel") or default_context.get("fillModel"),
        row.get("makerFeePct", default_context.get("makerFeePct")),
        row.get("takerFeePct", default_context.get("takerFeePct")),
        row.get("slippageBps", default_context.get("slippageBps")),
    )
    identity["legacyIdentity"] = not bool(row.get("paramsHash"))
    return identity


def candidate_context_from_config(candidate: dict) -> dict:
    return {
        "fillModel": candidate.get("fillModel") or "next-open",
        "makerFeePct": safe_float(candidate.get("makerFeePct"), 0),
        "takerFeePct": safe_float(candidate.get("takerFeePct"), safe_float(candidate.get("feePct"), 0.055)),
        "slippageBps": safe_float(candidate.get("slippageBps"), safe_float(candidate.get("slippagePct"), 0.02) * 100),
    }


def is_real_research_candidate(row: dict | None) -> bool:
    if not isinstance(row, dict):
        return False
    strategy = str(row.get("strategy") or "").strip()
    symbol = str(row.get("symbol") or "").strip()
    timeframe = str(row.get("timeframe") or "").strip()
    if not strategy or not symbol or not timeframe or "-" in {strategy, symbol, timeframe}:
        return False
    candidate_key = str(row.get("candidateKey") or "")
    if candidate_key.startswith(f"{CANONICAL_CANDIDATE_IDENTITY_VERSION}|-|-|-|"):
        return False
    return True


def format_failed_gate(gate) -> dict:
    if isinstance(gate, str):
        return {"name": gate, "detail": gate}
    if not isinstance(gate, dict):
        return {"name": "gate", "detail": str(gate)}
    name = str(gate.get("name") or gate.get("reason") or "gate")
    detail = str(gate.get("detail") or gate.get("summary") or "")
    match = re.match(r"^\s*(-?\d+(?:\.\d+)?)%?\s*(>=|>|<=|<)\s*(-?\d+(?:\.\d+)?)%?\s*(.*)$", detail)
    if match:
        left, op, right, suffix = match.groups()
        suffix = suffix.strip()
        pct = "%" if "%" in detail else ""
        if name == "full trades":
            detail = f"{left} trades < required {right}" if op in {">=", ">"} else f"{left} trades > allowed {right}"
        elif name == "activity":
            detail = f"{left} trades < required {right} {suffix}".strip()
        elif name == "fold pass count":
            detail = f"fold pass count {left} < required {right}"
        elif name == "negative folds":
            detail = f"{left} negative folds > allowed {right}"
        elif name == "worst fold":
            detail = f"worst fold {left}% < allowed {right}%"
        elif name == "median fold return":
            detail = f"median fold return {left}% < required {right}%"
        elif name == "median fold PF":
            detail = f"median fold PF {left} < required {right}"
        elif name == "full PF":
            detail = f"PF {left} < required {right}"
        elif name == "full return":
            detail = f"return {left}% <= required {right}%"
        elif name == "drawdown":
            detail = f"drawdown {left}% > allowed {right}%"
        elif name == "concentration":
            detail = f"concentration {left}% > allowed {right}%"
        else:
            comparator = "< required" if op in {">=", ">"} else "> allowed"
            detail = f"{name} {left}{pct} {comparator} {right}{pct}".strip()
    return {**gate, "name": name, "detail": detail}


def format_failed_gates(gates) -> list[dict]:
    return [format_failed_gate(gate) for gate in (gates or [])]


def validation_params_meta(params: dict | None, params_source: str, candidate_identity: dict | None = None) -> dict:
    normalized = normalized_candidate_params(params)
    params_hash = short_hash(normalized)
    candidate_hash = (candidate_identity or {}).get("paramsHash")
    return {
        "paramsUsed": params or {},
        "normalizedParams": normalized,
        "paramsHash": params_hash,
        "paramsSource": params_source,
        "candidateParamsHash": candidate_hash,
        "paramsMatchCandidate": candidate_hash is None or params_hash == candidate_hash,
    }


def validation_not_comparable(reason: str, params_meta: dict | None = None) -> dict:
    return {
        "ok": False,
        "status": "VALIDATION_NOT_COMPARABLE",
        "reason": reason,
        **(params_meta or {}),
        "warnings": [reason],
    }


def compact_campaign_candidate(row: dict) -> dict:
    if not is_real_research_candidate(row):
        return {}
    identity = candidate_identity_from_row(row)
    return {
        "rank": row.get("rank"),
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "candidateKey": identity.get("candidateKey"),
        "paramsHash": identity.get("paramsHash"),
        "executionContextHash": identity.get("executionContextHash"),
        "candidateIdentityVersion": identity.get("candidateIdentityVersion"),
        "legacyIdentity": identity.get("legacyIdentity"),
        "normalizedParams": identity.get("normalizedParams"),
        "tier": row.get("tier"),
        "eligibilityStatus": (row.get("eligibility") or {}).get("status"),
        "stabilityScore": row.get("stabilityScore"),
        "trades": (row.get("fullPeriod") or row).get("trades"),
        "profitFactor": (row.get("fullPeriod") or row).get("profitFactor"),
        "totalReturnPct": (row.get("fullPeriod") or row).get("totalReturnPct"),
        "maxDrawdownPct": (row.get("fullPeriod") or row).get("maxDrawdownPct"),
        "foldPassCount": (row.get("walkForward") or {}).get("foldPassCount"),
        "negativeFoldCount": (row.get("walkForward") or {}).get("negativeFoldCount"),
        "returnConcentrationPct": (row.get("returnConcentration") or {}).get("bestFoldContributionPct"),
        "stressStatus": (row.get("stress") or {}).get("status"),
        "recentWindowStatus": (row.get("recentWindows") or {}).get("status"),
        "reproducibilityStatus": (row.get("reproducibility") or {}).get("status"),
        "params": row.get("params") or {},
        "failedGates": format_failed_gates((row.get("eligibility") or {}).get("failedGates") or row.get("failedGates") or []),
    }


def compact_campaign_validation(candidate: dict, fee_stress: dict, walk_forward: dict, regime: dict) -> dict:
    candidate_compact = compact_campaign_candidate(candidate)
    candidate_hash = candidate_compact.get("paramsHash")

    def comparable(module: dict) -> bool:
        if not module:
            return False
        if module.get("ok") is False or module.get("status") in {"PARAM_MISMATCH", "VALIDATION_NOT_COMPARABLE", "ERROR"}:
            return False
        if candidate_hash and module.get("candidateParamsHash") and module.get("candidateParamsHash") != candidate_hash:
            return False
        return module.get("paramsMatchCandidate") is not False

    module_status = {
        "feeSlippageStress": "COMPARABLE" if comparable(fee_stress) else "VALIDATION_NOT_COMPARABLE",
        "walkForward": "COMPARABLE" if comparable(walk_forward) else "VALIDATION_NOT_COMPARABLE",
        "regimeBreakdown": "COMPARABLE" if comparable(regime) else "VALIDATION_NOT_COMPARABLE",
    }
    return {
        "candidate": candidate_compact,
        "candidateKey": candidate_compact.get("candidateKey"),
        "paramsHash": candidate_hash,
        "validationComparability": module_status,
        "comparableEvidenceOnly": all(value == "COMPARABLE" for value in module_status.values()),
        "feeSlippageStress": compact_snapshot_fee_stress(fee_stress),
        "walkForward": compact_snapshot_walk_forward(walk_forward),
        "regimeBreakdown": compact_snapshot_regime_breakdown(regime),
        "warnings": dedupe_list((fee_stress.get("warnings") or []) + (walk_forward.get("warnings") or []) + (regime.get("warnings") or [])),
    }


def research_campaign_recommendation(stability: dict, validations: list[dict], real_enabled: bool) -> dict:
    if real_enabled:
        return {
            "action": "REAL_TRADING_BLOCKED",
            "reason": "Real trading is enabled or partially configured; research campaign remains advisory only.",
        }
    eligible = stability.get("bestEligibleChallenger") if is_real_research_candidate(stability.get("bestEligibleChallenger")) else None
    stable = stability.get("bestStableCandidate") if is_real_research_candidate(stability.get("bestStableCandidate")) else None
    if eligible:
        return {
            "action": "REVIEW_STABLE_CHALLENGER",
            "reason": "At least one challenger passed stability-first eligibility gates. Review manually; no promotion is automatic.",
        }
    if stable:
        return {
            "action": "RESEARCH_STABLE_CANDIDATE_MORE",
            "reason": "A stable research candidate exists, but no candidate cleared all eligibility gates.",
        }
    comparable_validations = [row for row in validations if row.get("comparableEvidenceOnly")]
    if validations and not comparable_validations:
        return {
            "action": "VALIDATION_NOT_COMPARABLE",
            "reason": "Deep validation did not match selected candidate parameters, so campaign conclusions exclude it.",
        }
    if comparable_validations:
        return {
            "action": "KEEP_CURRENT_RESEARCH_MORE",
            "reason": "Top research rows were found, but validation still shows unresolved stability, concentration, stress, or regime issues.",
        }
    return {
        "action": "NO_RESEARCH_LEAD",
        "reason": "No campaign candidate produced enough evidence for deeper review.",
    }


def build_research_campaign_runner(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_symbol = active.get("symbol") or "ETHUSDT"
    active_timeframe = active.get("interval") or active.get("timeframe") or "1h"
    active_strategy = candidate.get("strategy") or "SimpleAtrTrendV2"
    period = str(args.get("period", "365d"))
    symbols = str(args.get("symbols", "ETHUSDT,BTCUSDT"))
    timeframes = str(args.get("timeframes", "1h,4h"))
    strategies = str(args.get("strategies", "all"))
    max_combos = str(campaign_int(args, "maxCombosPerStrategy", 10, 1, 50))
    top_n = str(campaign_int(args, "topN", 8, 1, 20))
    validate_top = campaign_int(args, "validateTop", 2, 0, 5)
    save = campaign_bool(args, "save", False)
    include_regime = campaign_bool(args, "includeRegime", True)
    include_stress = campaign_bool(args, "includeStress", True)
    include_recent = campaign_bool(args, "includeRecentWindows", True)
    include_repro = campaign_bool(args, "includeReproAudit", True)
    timeout_seconds = str(campaign_int(args, "timeout_seconds", 900, 60, 1800))
    warnings = []

    def module_envelope(name: str, ok: bool, summary: dict, scope: dict, started_at: str) -> dict:
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "name": name,
            "ok": ok,
            "status": "PASS" if ok else "ERROR",
            "startedAt": started_at,
            "finishedAt": finished_at,
            "scope": scope,
            "inputProvenance": {"source": "backend_builder_args", "readOnly": True},
            "outputProvenance": {"source": "subprocess_or_backend_payload", "readOnly": True},
            "summary": summary,
        }

    def capture(name: str, builder, section_args: dict) -> tuple[dict, bool]:
        try:
            payload, status_code = builder(section_args)
            ok = status_code < 400 and payload.get("ok", True) is not False
            if not ok:
                warnings.append(f"{name} returned status {status_code}.")
            warnings.extend(payload.get("warnings") or [])
            return payload, ok
        except Exception as exc:
            warnings.append(f"{name} failed: {exc}")
            return {"ok": False, "error": str(exc)}, False

    generated_at = datetime.now(timezone.utc).isoformat()
    campaign_id = f"campaign-{generated_at.replace(':', '').replace('-', '').replace('+00:00', 'Z')}-{uuid.uuid4().hex[:8]}"
    git_commit = current_git_commit()
    search_args = {
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strategies,
        "period": period,
        "folds": args.get("folds", "4"),
        "maxCombosPerStrategy": max_combos,
        "topN": top_n,
        "includeStress": "true" if include_stress else "false",
        "includeRecentWindows": "true" if include_recent else "false",
        "includeReproAudit": "true" if include_repro else "false",
        "reproReruns": args.get("reproReruns", "2"),
        "timeout_seconds": timeout_seconds,
    }
    stability_started = datetime.now(timezone.utc).isoformat()
    stability, stability_ok = capture("stability-first challenger search", build_research_stability_first_challenger_search, search_args)
    leaderboard_started = datetime.now(timezone.utc).isoformat()
    leaderboard, leaderboard_ok = capture(
        "research candidate leaderboard",
        build_research_candidate_leaderboard,
        {
            "symbols": symbols,
            "timeframes": timeframes,
            "strategies": active_strategy if strategies == "all" else strategies,
            "period": period,
            "maxCombos": args.get("leaderboardMaxCombos", args.get("maxCombos", "auto")),
            "timeout_seconds": timeout_seconds,
        },
    )
    activity_started = datetime.now(timezone.utc).isoformat()
    activity, activity_ok = capture(
        "backtest activity lab",
        build_research_activity_lab,
        {
            "symbols": symbols,
            "timeframes": timeframes,
            "strategies": active_strategy if strategies == "all" else strategies,
            "period": period,
            "maxCombos": args.get("activityMaxCombos", "auto"),
            "optimize": args.get("activityOptimize", "false"),
            "timeout_seconds": timeout_seconds,
        },
    )

    top_rows = stability.get("topCandidates") or []
    validation_rows = []
    for row in top_rows[:validate_top]:
        row_identity = candidate_identity_from_row(row)
        selected_params = row_identity.get("normalizedParams") or row.get("params") or {}
        spec = {
            "symbol": row.get("symbol") or active_symbol,
            "timeframe": row.get("timeframe") or active_timeframe,
            "strategy": row.get("strategy") or active_strategy,
            "period": period,
            "timeout_seconds": timeout_seconds,
            "baseParams": json.dumps(selected_params),
        }
        fee_payload, _ = capture(f"fee/slippage stress {spec['strategy']} {spec['symbol']} {spec['timeframe']}", build_research_fee_slippage_stress, spec)
        walk_payload, _ = capture(f"walk-forward review {spec['strategy']} {spec['symbol']} {spec['timeframe']}", build_research_walk_forward_review, {**spec, "folds": args.get("folds", "4")})
        if include_regime:
            regime_payload, _ = capture(f"regime breakdown {spec['strategy']} {spec['symbol']} {spec['timeframe']}", build_research_regime_breakdown, {**spec, "includeTrades": "false"})
        else:
            regime_payload = {"ok": True, "summary": {"regimeDependencyStatus": "NOT_RUN", "recommendation": {"action": "NOT_RUN", "reason": "includeRegime=false"}}}
        validation_rows.append(compact_campaign_validation(row, fee_payload, walk_payload, regime_payload))

    stability_summary = {
            "verdict": stability.get("verdict") or {},
            "search": stability.get("search") or {},
            "benchmark": compact_campaign_candidate(stability.get("benchmark") or {}),
            "bestResearchedCandidate": compact_campaign_candidate(stability.get("bestResearchedCandidate") or {}),
            "bestStableCandidate": compact_campaign_candidate(stability.get("bestStableCandidate") or {}),
            "bestEligibleChallenger": compact_campaign_candidate(stability.get("bestEligibleChallenger") or {}),
            "topCandidates": [candidate for candidate in (compact_campaign_candidate(row) for row in top_rows[: int(top_n)]) if candidate],
        }
    leaderboard_summary = compact_snapshot_leaderboard(leaderboard)
    activity_summary = {
            "rowsTested": len(activity.get("rows") or []),
            "bestOverall": compact_snapshot_row((activity.get("summary") or {}).get("bestOverall") or {}),
            "recommendation": (activity.get("summary") or {}).get("recommendation") or {},
        }
    scope_common = {
        "symbols": symbols.split(","),
        "timeframes": timeframes.split(","),
        "period": period,
        "source": "bybit",
    }
    modules = {
        "stabilityFirstSearch": module_envelope("stabilityFirstSearch", stability_ok, stability_summary, {**scope_common, "strategies": strategies, "maxCombosPerStrategy": int(max_combos), "topN": int(top_n)}, stability_started),
        "leaderboard": module_envelope("leaderboard", leaderboard_ok, leaderboard_summary, {**scope_common, "strategies": active_strategy if strategies == "all" else strategies, "scopeMismatch": strategies == "all"}, leaderboard_started),
        "activity": module_envelope("activity", activity_ok, activity_summary, {**scope_common, "strategies": active_strategy if strategies == "all" else strategies, "scopeMismatch": strategies == "all"}, activity_started),
        "deepValidation": validation_rows,
    }
    recommendation = research_campaign_recommendation(stability, validation_rows, real_enabled)
    campaign = {
        "ok": stability_ok and leaderboard_ok and activity_ok,
        "schemaVersion": "research-campaign-v2",
        "campaignId": campaign_id,
        "generatedAt": generated_at,
        "gitCommit": git_commit,
        "candidateIdentityVersion": CANONICAL_CANDIDATE_IDENTITY_VERSION,
        "savedPath": None,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "activePaperCandidate": {
            **candidate_summary(candidate),
            "strategy": active_strategy,
            "symbol": active_symbol,
            "timeframe": active_timeframe,
        },
        "search": {
            "symbols": symbols.split(","),
            "timeframes": timeframes.split(","),
            "strategies": strategies,
            "period": period,
            "maxCombosPerStrategy": int(max_combos),
            "topN": int(top_n),
            "validateTop": validate_top,
            "folds": int(safe_float(args.get("folds", 4), 4)),
            "includeStress": include_stress,
            "includeRecentWindows": include_recent,
            "includeReproAudit": include_repro,
            "readOnly": True,
        },
        "dataContext": {
            "source": "bybit",
            "requestedPeriod": period,
            "requestedSymbols": symbols.split(","),
            "requestedTimeframes": timeframes.split(","),
        },
        "executionContext": {
            **candidate_context_from_config(candidate),
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
        },
        "scopeWarnings": [
            "leaderboard/activity scoped to active strategy while stability search used all strategies"
        ] if strategies == "all" else [],
        "comparableEvidenceOnly": all(row.get("comparableEvidenceOnly") for row in validation_rows) if validation_rows else True,
        "modules": modules,
        "recommendation": recommendation,
        "safety": {
            "promoted": False,
            "configWritten": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "realTradingTouched": False,
        },
        "warnings": dedupe_list(([real_detail] if real_enabled else []) + [warning for warning in warnings if warning]),
    }
    if save:
        campaign["savedPath"] = save_research_snapshot(campaign, "json")
    return campaign, 200 if campaign["ok"] else 502


AUTOPILOT_SCHEMA_VERSION = "research-autopilot-v1"
AUTOPILOT_DEFAULT_STRATEGIES = [
    "SimpleAtrTrendV2",
    "MeanReversion",
    "PullbackTrend",
    "MomentumScalping",
    "ConservativeTrendLoose",
    "MomentumContinuation",
    "RangeExpansionV2",
    "RegimeDonchian20",
    "RelativeStrengthV2",
    "EmaBounceV2",
    "VolatilitySqueezeBreakout",
]
AUTOPILOT_NEW_FAMILY_PREFERRED_STRATEGIES = [
    "MomentumContinuation",
    "RangeExpansionV2",
    "EmaBounceV2",
    "VolatilitySqueezeBreakout",
    "ConservativeTrendLoose",
    "RelativeStrengthV2",
]
AUTOPILOT_FIRST_PASS_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
AUTOPILOT_SECOND_PASS_SYMBOLS = ["BNBUSDT", "XRPUSDT"]
AUTOPILOT_TIMEFRAMES = ["1h", "4h"]
AUTOPILOT_PERIODS = ["365d", "730d"]
AUTOPILOT_REJECTED_CATEGORIES = {"NEGATIVE_RETURN", "LOW_PROFIT_FACTOR", "BAD_WALK_FORWARD", "STRESS_COLLAPSE", "REJECTED", "RECENTLY_WEAK"}
AUTOPILOT_NO_LEAD_CATEGORIES = {"NO_RESEARCH_LEAD"}
AUTOPILOT_LOWER_TIMEFRAME_REJECTED_CATEGORIES = {"NEGATIVE_RETURN", "RECENTLY_WEAK", "STRESS_COLLAPSE", "REJECTED"}
AUTOPILOT_COOL_DOWN_STATUSES = {"COOL_DOWN", "REJECTED_FAMILY", "EXHAUSTED_IN_CURRENT_SCOPE"}
AUTOPILOT_CONFIRMED_REVIEW_STATUSES = {"CONFIRMED_CHALLENGER_REVIEW", "REVIEW_READY"}
AUTOPILOT_PLANNING_MODES = {"conservative", "balanced", "exploratory"}


def autopilot_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def autopilot_list(value, fallback=None) -> list[str]:
    if fallback is None:
        fallback = []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in fallback if str(item).strip()]


def autopilot_display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path(app.root_path))).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def autopilot_paths() -> dict:
    return {
        "dir": RESEARCH_AUTOPILOT_DIR,
        "queue": RESEARCH_AUTOPILOT_QUEUE_PATH,
        "memory": RESEARCH_AUTOPILOT_MEMORY_PATH,
    }


def load_autopilot_queue() -> dict:
    try:
        payload = read_json_file(str(RESEARCH_AUTOPILOT_QUEUE_PATH), {})
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schemaVersion", AUTOPILOT_SCHEMA_VERSION)
    payload.setdefault("generatedAt", None)
    payload.setdefault("updatedAt", None)
    payload.setdefault("jobs", [])
    payload.setdefault("lastPlanSkippedJobs", [])
    payload.setdefault("lastPlanWarnings", [])
    return payload


def save_autopilot_queue(payload: dict) -> None:
    RESEARCH_AUTOPILOT_DIR.mkdir(parents=True, exist_ok=True)
    payload["schemaVersion"] = AUTOPILOT_SCHEMA_VERSION
    payload["updatedAt"] = autopilot_now()
    if not payload.get("generatedAt"):
        payload["generatedAt"] = payload["updatedAt"]
    write_autopilot_json(RESEARCH_AUTOPILOT_QUEUE_PATH, payload)


def load_autopilot_memory() -> dict:
    try:
        payload = read_json_file(str(RESEARCH_AUTOPILOT_MEMORY_PATH), {})
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schemaVersion", AUTOPILOT_SCHEMA_VERSION)
    payload.setdefault("generatedAt", None)
    payload.setdefault("updatedAt", None)
    payload["branches"] = [sanitize_autopilot_memory_row(row) for row in payload.get("branches", []) if is_real_research_candidate(row)]
    payload["candidates"] = [sanitize_autopilot_memory_row(row) for row in payload.get("candidates", []) if is_real_research_candidate(row)]
    payload.setdefault("sourceReports", [])
    return payload


def save_autopilot_memory(payload: dict) -> None:
    RESEARCH_AUTOPILOT_DIR.mkdir(parents=True, exist_ok=True)
    payload["schemaVersion"] = AUTOPILOT_SCHEMA_VERSION
    payload["updatedAt"] = autopilot_now()
    if not payload.get("generatedAt"):
        payload["generatedAt"] = payload["updatedAt"]
    write_autopilot_json(RESEARCH_AUTOPILOT_MEMORY_PATH, payload)


def write_autopilot_json(path: Path, payload: dict) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(path)


def sanitize_autopilot_memory_row(row: dict) -> dict:
    return {**row, "failedGates": format_failed_gates(row.get("failedGates") or [])}


def autopilot_job_signature(job: dict) -> str:
    return stable_json({
        "strategies": sorted(autopilot_list(job.get("strategies") or job.get("strategy"))),
        "symbols": sorted(autopilot_list(job.get("symbols") or job.get("symbol"))),
        "timeframes": sorted(autopilot_list(job.get("timeframes") or job.get("timeframe"))),
        "period": job.get("period"),
        "maxCombosPerStrategy": int(safe_float(job.get("maxCombosPerStrategy"), 0)),
        "topN": int(safe_float(job.get("topN"), 0)),
        "includeStress": bool(job.get("includeStress")),
        "includeRecentWindows": bool(job.get("includeRecentWindows")),
        "includeReproAudit": bool(job.get("includeReproAudit")),
    })


def make_autopilot_job(
    strategies,
    symbols,
    timeframes,
    period: str,
    reason: str,
    priority: int = 50,
    parent_job_id: str | None = None,
    generated_by: str = "planner",
    previous_evidence: dict | None = None,
    max_combos: int = 12,
    top_n: int = 18,
    include_stress: bool = True,
    include_recent: bool = True,
    include_repro: bool = True,
    repro_reruns: int = 2,
    timeout_seconds: int = 900,
) -> dict:
    strategy_list = autopilot_list(strategies)
    symbol_list = autopilot_list(symbols)
    timeframe_list = autopilot_list(timeframes)
    job = {
        "jobId": f"research-job-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}",
        "createdAt": autopilot_now(),
        "updatedAt": None,
        "startedAt": None,
        "finishedAt": None,
        "status": "QUEUED",
        "priority": int(priority),
        "reason": reason,
        "strategy": ",".join(strategy_list),
        "strategies": strategy_list,
        "symbol": ",".join(symbol_list),
        "symbols": symbol_list,
        "timeframe": ",".join(timeframe_list),
        "timeframes": timeframe_list,
        "period": period,
        "maxCombosPerStrategy": max(1, min(int(max_combos), 20)),
        "topN": max(5, min(int(top_n), 25)),
        "includeStress": bool(include_stress),
        "includeRecentWindows": bool(include_recent),
        "includeReproAudit": bool(include_repro),
        "reproReruns": max(1, min(int(repro_reruns), 3)),
        "timeoutSeconds": max(60, min(int(timeout_seconds), 1200)),
        "parentJobId": parent_job_id,
        "generatedBy": generated_by,
        "previousEvidenceSummary": previous_evidence or {},
    }
    job["signature"] = autopilot_job_signature(job)
    return job


def autopilot_enqueue(queue: dict, jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    existing = {job.get("signature") for job in queue.get("jobs", []) if job.get("status") in {"QUEUED", "RUNNING", "DONE"}}
    added = []
    skipped = []
    for job in jobs:
        signature = job.get("signature") or autopilot_job_signature(job)
        job["signature"] = signature
        if signature in existing:
            skipped.append({**job, "status": "SKIPPED", "skipReason": "duplicate_job_signature"})
            continue
        queue.setdefault("jobs", []).append(job)
        existing.add(signature)
        added.append(job)
    queue["jobs"] = sorted(queue.get("jobs", []), key=lambda item: (item.get("status") != "QUEUED", -safe_float(item.get("priority"), 0), item.get("createdAt") or ""))
    return added, skipped


def autopilot_queue_counts(queue: dict) -> dict:
    counts = Counter(job.get("status", "UNKNOWN") for job in queue.get("jobs", []))
    return {status: counts.get(status, 0) for status in ["QUEUED", "RUNNING", "DONE", "FAILED", "SKIPPED"]}


def autopilot_normalized_branch_key(strategy: str | None, symbol: str | None, timeframe: str | None, period: str | None) -> str:
    return "|".join([
        str(strategy or "-").strip(),
        str(symbol or "-").strip().upper(),
        str(timeframe or "-").strip().lower(),
        str(period or "-").strip().lower(),
    ])


def autopilot_job_branch_keys(job: dict) -> set[str]:
    period = job.get("period") or "365d"
    keys = set()
    for strategy in autopilot_list(job.get("strategies") or job.get("strategy")):
        for symbol in autopilot_list(job.get("symbols") or job.get("symbol")):
            for timeframe in autopilot_list(job.get("timeframes") or job.get("timeframe")):
                keys.add(autopilot_normalized_branch_key(strategy, symbol, timeframe, period))
    return keys


def autopilot_queue_branch_keys(queue: dict, statuses=None) -> set[str]:
    statuses = statuses or {"QUEUED", "RUNNING", "DONE"}
    keys = set()
    for job in queue.get("jobs", []):
        if job.get("status") in statuses:
            keys.update(autopilot_job_branch_keys(job))
    return keys


def autopilot_memory_branch_map(memory: dict) -> dict:
    return {
        autopilot_branch_key(row, row.get("period")): row
        for row in memory.get("branches", [])
        if is_real_research_candidate(row)
    }


def normalize_autopilot_planning_mode(raw: str | None) -> str:
    mode = str(raw or "balanced").strip().lower()
    return mode if mode in AUTOPILOT_PLANNING_MODES else "balanced"


def autopilot_family_thresholds(planning_mode: str) -> dict:
    mode = normalize_autopilot_planning_mode(planning_mode)
    if mode == "conservative":
        return {"all_rejected_min": 3, "cool_min": 3, "cool_ratio": 0.5, "cool_floor": 2, "exhausted_min": 3}
    if mode == "exploratory":
        return {"all_rejected_min": 6, "cool_min": 6, "cool_ratio": 0.75, "cool_floor": 5, "exhausted_min": 6}
    return {"all_rejected_min": 4, "cool_min": 4, "cool_ratio": 0.6, "cool_floor": 3, "exhausted_min": 4}


def autopilot_family_summary(memory: dict, planning_mode: str = "balanced") -> list[dict]:
    thresholds = autopilot_family_thresholds(planning_mode)
    families = {}
    for branch in memory.get("branches", []):
        if not is_real_research_candidate(branch):
            continue
        strategy = branch.get("strategy")
        family = families.setdefault(strategy, {
            "strategy": strategy,
            "branchesTested": 0,
            "rejectedBranches": 0,
            "negativeReturnBranches": 0,
            "stressCollapseBranches": 0,
            "recentlyWeakBranches": 0,
            "insufficientEvidenceBranches": 0,
            "promisingRareBranches": 0,
            "eligibleOrStableBranches": 0,
            "confirmedChains": [],
            "bestBranch": None,
            "bestPF": None,
            "bestReturnPct": None,
            "bestTrades": None,
            "latestSeenAt": None,
            "familyStatus": "ACTIVE",
            "cooldownCycleCount": 0,
            "cooldownUntil": None,
            "recommendedNextAction": "Continue bounded research.",
            "reason": "",
        })
        category = branch.get("reasonCategory")
        family["branchesTested"] += 1
        if category in AUTOPILOT_REJECTED_CATEGORIES:
            family["rejectedBranches"] += 1
        if category == "NEGATIVE_RETURN":
            family["negativeReturnBranches"] += 1
        if category == "STRESS_COLLAPSE" or branch.get("stressStatus") == "COLLAPSES_UNDER_STRESS":
            family["stressCollapseBranches"] += 1
        if category == "RECENTLY_WEAK" or branch.get("recentWindowStatus") == "RECENTLY_WEAK":
            family["recentlyWeakBranches"] += 1
        if category in {"TOO_FEW_TRADES", "BAD_WALK_FORWARD", "NO_RESEARCH_LEAD"}:
            family["insufficientEvidenceBranches"] += 1
        if category == "PROMISING_BUT_RARE":
            family["promisingRareBranches"] += 1
        if category == "PROMISING_STABLE" or branch.get("eligibilityStatus") == "CHALLENGER_ELIGIBLE":
            family["eligibleOrStableBranches"] += 1
        if not family["latestSeenAt"] or str(branch.get("lastSeenAt") or "") > str(family["latestSeenAt"] or ""):
            family["latestSeenAt"] = branch.get("lastSeenAt")
        best = family.get("bestBranch")
        if best is None or (
            safe_float(branch.get("profitFactor"), -1) > safe_float(best.get("profitFactor"), -1)
            or (
                safe_float(branch.get("profitFactor"), -1) == safe_float(best.get("profitFactor"), -1)
                and safe_float(branch.get("totalReturnPct"), -999) > safe_float(best.get("totalReturnPct"), -999)
            )
        ):
            family["bestBranch"] = branch
            family["bestPF"] = branch.get("profitFactor")
            family["bestReturnPct"] = branch.get("totalReturnPct")
            family["bestTrades"] = branch.get("fullTrades")

    summaries = []
    for family in families.values():
        tested = family["branchesTested"]
        rejected_like = family["rejectedBranches"]
        weak_like = rejected_like + family["insufficientEvidenceBranches"]
        has_stable = family["eligibleOrStableBranches"] > 0
        has_rare = family["promisingRareBranches"] > 0
        family["confirmedChains"] = autopilot_confirmed_chains([row for row in memory.get("branches", []) if row.get("strategy") == family.get("strategy")])
        if family["confirmedChains"]:
            status = "CONFIRMED_CHALLENGER_REVIEW"
            action = "Review confirmed challenger chain manually; no automatic promotion or paper/live enablement."
        elif has_stable:
            status = "NEEDS_DEEP_CONFIRMATION"
            action = "Confirm stable or eligible branch on longer windows before any manual review."
        elif has_rare:
            status = "PROMISING_BUT_RARE"
            action = "Run controlled period confirmation and limited related-market tests."
        elif rejected_like == tested and (tested >= thresholds["all_rejected_min"] or family.get("strategy") == "RelativeStrengthV2"):
            status = "REJECTED_FAMILY"
            action = "Cool down this strategy family; all tested branches are rejected-like."
        elif tested >= thresholds["cool_min"] and rejected_like >= max(thresholds["cool_floor"], math.ceil(tested * thresholds["cool_ratio"])):
            status = "COOL_DOWN"
            action = "Cool down this strategy family before more broad expansion."
        elif tested >= thresholds["exhausted_min"] and weak_like >= tested:
            status = "EXHAUSTED_IN_CURRENT_SCOPE"
            action = "Pause broad search in the current symbol/timeframe scope."
        else:
            status = "ACTIVE"
            action = "Continue bounded exploration if queue capacity remains."
        family["familyStatus"] = status
        family["recommendedNextAction"] = action
        family["reason"] = (
            f"{strategy_family_label(family)}: {tested} branches tested, "
            f"{family['negativeReturnBranches']} negative-return, "
            f"{family['stressCollapseBranches']} stress-collapse, "
            f"{family['insufficientEvidenceBranches']} insufficient-evidence, "
            f"{family['promisingRareBranches']} promising-rare, "
            f"{family['eligibleOrStableBranches']} eligible/stable."
        )
        if status in AUTOPILOT_COOL_DOWN_STATUSES:
            family["cooldownCycleCount"] = 1
        summaries.append(family)
    return sorted(summaries, key=lambda row: (row.get("familyStatus") not in {"CONFIRMED_CHALLENGER_REVIEW", "PROMISING_BUT_RARE", "NEEDS_DEEP_CONFIRMATION"}, -(row.get("branchesTested") or 0), row.get("strategy") or ""))


def strategy_family_label(family: dict) -> str:
    return str(family.get("strategy") or "Unknown strategy")


def autopilot_family_map(memory: dict, planning_mode: str = "balanced") -> dict:
    return {row.get("strategy"): row for row in autopilot_family_summary(memory, planning_mode=planning_mode)}


def autopilot_branch_was_recent(row: dict | None, days: int = 30) -> bool:
    if not row:
        return False
    seen = parse_iso_timestamp(row.get("lastSeenAt"))
    if seen is None:
        return False
    return (datetime.now(timezone.utc) - seen).total_seconds() <= days * 86400


def autopilot_skip_record(job: dict, skip_reason: str, detail: str, branch_key: str | None = None, branch: dict | None = None) -> dict:
    return {
        "jobId": job.get("jobId"),
        "status": "SKIPPED",
        "skipReason": skip_reason,
        "detail": detail,
        "branchKey": branch_key,
        "branch": branch,
        "generatedBy": job.get("generatedBy"),
        "strategy": job.get("strategy"),
        "strategies": job.get("strategies"),
        "symbol": job.get("symbol"),
        "symbols": job.get("symbols"),
        "timeframe": job.get("timeframe"),
        "timeframes": job.get("timeframes"),
        "period": job.get("period"),
        "reason": job.get("reason"),
    }


def skip_deprioritized_autopilot_queue_jobs(queue: dict, memory: dict) -> list[dict]:
    branch_map = autopilot_memory_branch_map(memory)
    skipped = []
    for job in queue.get("jobs", []):
        if job.get("status") != "QUEUED":
            continue
        for key in sorted(autopilot_job_branch_keys(job)):
            branch = branch_map.get(key)
            if branch and branch.get("reasonCategory") in AUTOPILOT_REJECTED_CATEGORIES:
                job["status"] = "SKIPPED"
                job["updatedAt"] = autopilot_now()
                job["skipReason"] = "rejected_branch_in_memory"
                job["skipDetail"] = f"Skipped queued job because branch {key} is {branch.get('reasonCategory')}."
                record = autopilot_skip_record(job, job["skipReason"], job["skipDetail"], key, branch)
                skipped.append(record)
                break
            if branch and branch.get("reasonCategory") in AUTOPILOT_NO_LEAD_CATEGORIES:
                job["status"] = "SKIPPED"
                job["updatedAt"] = autopilot_now()
                job["skipReason"] = "already_tested_no_research_lead"
                job["skipDetail"] = f"Skipped {branch.get('strategy') or '-'} {branch.get('symbol') or '-'} {branch.get('timeframe') or '-'} {branch.get('period') or '-'} because this exact branch was already tested and produced NO_RESEARCH_LEAD."
                record = autopilot_skip_record(job, job["skipReason"], job["skipDetail"], key, branch)
                skipped.append(record)
                break
            if branch and autopilot_branch_is_eligible_or_stable(branch):
                job["status"] = "SKIPPED"
                job["updatedAt"] = autopilot_now()
                job["skipReason"] = "already_tested_eligible_branch"
                job["skipDetail"] = f"Skipped {branch.get('strategy') or '-'} {branch.get('symbol') or '-'} {branch.get('timeframe') or '-'} {branch.get('period') or '-'} because this exact eligible branch was already tested as {branch.get('reasonCategory') or branch.get('eligibilityStatus')}."
                record = autopilot_skip_record(job, job["skipReason"], job["skipDetail"], key, branch)
                skipped.append(record)
                break
    return skipped


def recover_stale_autopilot_running_jobs(queue: dict, max_age_minutes: int = 120) -> list[dict]:
    recovered = []
    now = datetime.now(timezone.utc)
    for job in queue.get("jobs", []):
        if job.get("status") != "RUNNING":
            continue
        started = parse_iso_timestamp(job.get("startedAt") or job.get("updatedAt") or job.get("createdAt"))
        if started is None:
            age_minutes = max_age_minutes + 1
        else:
            age_minutes = (now - started).total_seconds() / 60
        if age_minutes > max_age_minutes:
            job["status"] = "FAILED"
            job["updatedAt"] = autopilot_now()
            job["finishedAt"] = job.get("finishedAt") or job["updatedAt"]
            job["error"] = "Recovered stale RUNNING job; no live execution is resumed automatically."
            recovered.append(job)
    return recovered


def autopilot_safety_payload() -> dict:
    real_enabled, real_detail = paper_real_trading_enabled()
    return {
        "researchOnly": True,
        "paperEnabled": canonical_paper_enabled(load_paper_candidate_config()),
        "realTradingEnabled": real_enabled,
        "realTradingDetail": real_detail,
        "promotionAttempted": False,
        "configWritten": False,
        "paperStateChanged": False,
        "paperTickRan": False,
        "liveOrdersTouched": False,
        "realOrderFunctionsCalled": False,
        "activePaperCandidateMutated": False,
        "riskSettingsChanged": False,
        "apiKeyPathCreated": False,
    }


def autopilot_branch_key(row: dict, period: str | None = None) -> str:
    return autopilot_normalized_branch_key(row.get("strategy"), row.get("symbol"), row.get("timeframe"), period or row.get("period"))


def autopilot_reason_category(row: dict) -> str:
    full = row.get("fullPeriod") or row
    eligibility_status = row.get("eligibilityStatus") or (row.get("eligibility") or {}).get("status")
    tier = row.get("tier")
    trades = int(safe_float(row.get("trades", full.get("trades")), 0))
    pf = safe_float(row.get("profitFactor", full.get("profitFactor")), 0)
    ret = safe_float(row.get("totalReturnPct", full.get("totalReturnPct")), 0)
    failed_gate_names = [str(item.get("name") or item.get("reason") or "") for item in (row.get("failedGates") or (row.get("eligibility") or {}).get("failedGates") or [])]
    stress_status = row.get("stressStatus") or (row.get("stress") or {}).get("status")
    recent_status = row.get("recentWindowStatus") or (row.get("recentWindows") or {}).get("status")
    negative_folds = int(safe_float(row.get("negativeFoldCount") or (row.get("walkForward") or {}).get("negativeFoldCount"), 0))
    fold_pass = int(safe_float(row.get("foldPassCount") or (row.get("walkForward") or {}).get("foldPassCount"), 0))
    if eligibility_status == "CHALLENGER_ELIGIBLE" or tier == "CHALLENGER_ELIGIBLE":
        return "PROMISING_STABLE"
    if trades < 40 and pf >= 1.1 and ret > 0:
        return "PROMISING_BUT_RARE"
    if trades < 20 or any("trade" in name.lower() or "activity" in name.lower() for name in failed_gate_names):
        return "TOO_FEW_TRADES"
    if ret < 0:
        return "NEGATIVE_RETURN"
    if pf < 1:
        return "LOW_PROFIT_FACTOR"
    if stress_status in {"COLLAPSES_UNDER_STRESS", "FAIL"}:
        return "STRESS_COLLAPSE"
    if recent_status == "RECENTLY_WEAK":
        return "RECENTLY_WEAK"
    if negative_folds > 1 or fold_pass < 2:
        return "BAD_WALK_FORWARD"
    if eligibility_status == "REJECTED" or tier == "REJECTED":
        return "REJECTED"
    return "REJECTED"


def compact_autopilot_candidate(row: dict, source_report: str, period: str | None, seen_at: str | None) -> dict:
    if not is_real_research_candidate(row):
        return {}
    identity = candidate_identity_from_row(row)
    full = row.get("fullPeriod") or row
    wf = row.get("walkForward") or {}
    category = autopilot_reason_category(row)
    return {
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "period": period,
        "paramsHash": identity.get("paramsHash"),
        "candidateKey": identity.get("candidateKey"),
        "candidateIdentityVersion": identity.get("candidateIdentityVersion"),
        "bestTier": row.get("tier"),
        "eligibilityStatus": row.get("eligibilityStatus") or (row.get("eligibility") or {}).get("status"),
        "fullTrades": int(safe_float(row.get("trades", full.get("trades")), 0)),
        "profitFactor": safe_float(row.get("profitFactor", full.get("profitFactor")), None),
        "totalReturnPct": safe_float(row.get("totalReturnPct", full.get("totalReturnPct")), None),
        "maxDrawdownPct": safe_float(row.get("maxDrawdownPct", full.get("maxDrawdownPct")), None),
        "foldPassCount": int(safe_float(row.get("foldPassCount", wf.get("foldPassCount")), 0)),
        "negativeFolds": int(safe_float(row.get("negativeFoldCount", wf.get("negativeFoldCount")), 0)),
        "worstFold": wf.get("worstFold") or {"returnPct": wf.get("worstFoldReturnPct")},
        "stressStatus": row.get("stressStatus") or (row.get("stress") or {}).get("status"),
        "recentWindowStatus": row.get("recentWindowStatus") or (row.get("recentWindows") or {}).get("status"),
        "failedGates": format_failed_gates(row.get("failedGates") or (row.get("eligibility") or {}).get("failedGates") or []),
        "reasonCategory": category,
        "lastSeenAt": seen_at,
        "sourceReport": source_report,
    }


def autopilot_rows_from_campaign(payload: dict, source_report: str) -> list[dict]:
    rows = []
    generated_at = payload.get("generatedAt")
    period = (payload.get("search") or {}).get("period") or ((payload.get("runContext") or {}).get("period"))
    modules = payload.get("modules") or {}
    stability = (modules.get("stabilityFirstSearch") or {}).get("summary") or {}
    for section in ("topCandidates", "bestResearchedCandidate", "bestStableCandidate", "bestEligibleChallenger"):
        value = stability.get(section)
        if isinstance(value, list):
            rows.extend(candidate for candidate in (compact_autopilot_candidate(row, source_report, period, generated_at) for row in value if isinstance(row, dict)) if candidate)
        elif isinstance(value, dict):
            candidate = compact_autopilot_candidate(value, source_report, period, generated_at)
            if candidate:
                rows.append(candidate)
    for row in payload.get("topCandidates") or []:
        if isinstance(row, dict):
            candidate = compact_autopilot_candidate(row, source_report, period, generated_at)
            if candidate:
                rows.append(candidate)
    recommendation = payload.get("recommendation") or {}
    if recommendation.get("action") == "NO_RESEARCH_LEAD":
        search = payload.get("search") or {}
        strategies = autopilot_list(search.get("strategies"))
        if strategies == ["all"]:
            strategies = []
        for strategy in strategies:
            for symbol in autopilot_list(search.get("symbols")):
                for timeframe in autopilot_list(search.get("timeframes")):
                    rows.append({
                        "strategy": strategy,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "period": period,
                        "paramsHash": "no-research-lead",
                        "candidateKey": None,
                        "candidateIdentityVersion": CANONICAL_CANDIDATE_IDENTITY_VERSION,
                        "bestTier": "NO_RESEARCH_LEAD",
                        "eligibilityStatus": "NO_RESEARCH_LEAD",
                        "fullTrades": 0,
                        "profitFactor": 0,
                        "totalReturnPct": 0,
                        "maxDrawdownPct": 0,
                        "foldPassCount": 0,
                        "negativeFolds": 0,
                        "worstFold": {"returnPct": 0},
                        "stressStatus": "NO_RESEARCH_LEAD",
                        "recentWindowStatus": "NO_RESEARCH_LEAD",
                        "failedGates": [{"name": "research lead", "detail": recommendation.get("reason") or "No campaign candidate produced enough evidence for deeper review."}],
                        "reasonCategory": "NO_RESEARCH_LEAD",
                        "lastSeenAt": generated_at,
                        "sourceReport": source_report,
                    })
    by_key = {}
    for row in rows:
        if not row.get("candidateKey") and row.get("reasonCategory") != "NO_RESEARCH_LEAD":
            continue
        key = row.get("candidateKey") or stable_json([row.get("strategy"), row.get("symbol"), row.get("timeframe"), row.get("period"), row.get("paramsHash")])
        old = by_key.get(key)
        if old is None or safe_float(row.get("profitFactor"), -1) > safe_float(old.get("profitFactor"), -1):
            by_key[key] = row
    return list(by_key.values())


def update_autopilot_memory_from_report(payload: dict, source_report: str | None = None) -> dict:
    memory = load_autopilot_memory()
    source_report = source_report or payload.get("savedPath") or "inline"
    rows = autopilot_rows_from_campaign(payload, source_report)
    candidate_map = {row.get("candidateKey"): row for row in memory.get("candidates", []) if row.get("candidateKey") and is_real_research_candidate(row)}
    for row in rows:
        if row.get("candidateKey") and is_real_research_candidate(row):
            candidate_map[row["candidateKey"]] = row
    branch_map = {row.get("branchKey"): row for row in memory.get("branches", []) if row.get("branchKey") and is_real_research_candidate(row)}
    for row in rows:
        branch_key = autopilot_branch_key(row, row.get("period"))
        old = branch_map.get(branch_key, {})
        best = row
        if old and safe_float(old.get("profitFactor"), -1) > safe_float(row.get("profitFactor"), -1):
            best = {**old, "lastSeenAt": row.get("lastSeenAt"), "sourceReport": row.get("sourceReport")}
        branch_map[branch_key] = {
            **best,
            "branchKey": branch_key,
            "strategy": best.get("strategy") or row.get("strategy"),
            "symbol": best.get("symbol") or row.get("symbol"),
            "timeframe": best.get("timeframe") or row.get("timeframe"),
            "period": best.get("period") or row.get("period"),
            "reasonCategory": best.get("reasonCategory"),
            "lastSeenAt": best.get("lastSeenAt") or row.get("lastSeenAt"),
            "sourceReport": best.get("sourceReport") or row.get("sourceReport"),
        }
    reports = set(memory.get("sourceReports") or [])
    reports.add(source_report)
    memory.update({
        "sourceReports": sorted(reports),
        "candidates": sorted(candidate_map.values(), key=lambda row: (row.get("reasonCategory") != "PROMISING_STABLE", -safe_float(row.get("profitFactor"), 0), -safe_float(row.get("fullTrades"), 0))),
        "branches": sorted(branch_map.values(), key=lambda row: (row.get("strategy") or "", row.get("symbol") or "", row.get("timeframe") or "", row.get("period") or "")),
    })
    save_autopilot_memory(memory)
    return memory


def rebuild_autopilot_memory_from_saved_reports(file_limit: int = 80) -> dict:
    memory = {
        "schemaVersion": AUTOPILOT_SCHEMA_VERSION,
        "generatedAt": autopilot_now(),
        "updatedAt": autopilot_now(),
        "branches": [],
        "candidates": [],
        "sourceReports": [],
    }
    save_autopilot_memory(memory)
    for path in candidate_ledger_source_files(file_limit):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        source = autopilot_display_path(path)
        memory = update_autopilot_memory_from_report(payload, source)
    return memory


def autopilot_backfill_source_files(file_limit: int = 250) -> list[Path]:
    files = list(candidate_ledger_source_files(file_limit))
    queue = load_autopilot_queue()
    for job in queue.get("jobs", []):
        saved = job.get("savedPath") or ((job.get("resultSummary") or {}).get("savedPath"))
        if saved:
            resolved = resolve_research_report_path(saved)
            if resolved:
                files.append(resolved)
    deduped = {}
    for path in files:
        try:
            deduped[str(path.resolve())] = path
        except Exception:
            deduped[str(path)] = path
    return list(deduped.values())[:file_limit]


def backfill_autopilot_no_research_leads(file_limit: int = 250) -> dict:
    before = autopilot_memory_branch_map(load_autopilot_memory())
    scanned = 0
    no_lead_reports = 0
    errors = []
    for path in autopilot_backfill_source_files(file_limit):
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        if (payload.get("recommendation") or {}).get("action") != "NO_RESEARCH_LEAD":
            continue
        no_lead_reports += 1
        update_autopilot_memory_from_report(payload, autopilot_display_path(path))
    after_memory = load_autopilot_memory()
    after = autopilot_memory_branch_map(after_memory)
    backfilled_keys = sorted(
        key for key, row in after.items()
        if row.get("reasonCategory") == "NO_RESEARCH_LEAD" and before.get(key, {}).get("reasonCategory") != "NO_RESEARCH_LEAD"
    )
    return {
        "ok": True,
        "generatedAt": autopilot_now(),
        "scannedReports": scanned,
        "noResearchLeadReports": no_lead_reports,
        "backfilledBranches": len(backfilled_keys),
        "backfilledBranchKeys": backfilled_keys[:25],
        "branchesBefore": len(before),
        "branchesAfter": len(after),
        "errors": errors[:10],
        "memorySummary": {"branches": len(after_memory.get("branches", [])), "candidates": len(after_memory.get("candidates", [])), "sourceReports": len(after_memory.get("sourceReports", []))},
        "safety": autopilot_safety_payload(),
    }


def load_autopilot_memory_after_backfill() -> tuple[dict, dict]:
    backfill = backfill_autopilot_no_research_leads()
    return load_autopilot_memory(), backfill


def autopilot_branch_rejected(memory: dict, strategy: str, symbol: str, timeframe: str, period: str) -> bool:
    branch = autopilot_memory_branch_map(memory).get(autopilot_normalized_branch_key(strategy, symbol, timeframe, period))
    return bool(branch and branch.get("reasonCategory") in AUTOPILOT_REJECTED_CATEGORIES)


def autopilot_branch_is_eligible_or_stable(branch: dict | None) -> bool:
    return autopilot_branch_is_confirmed_candidate(branch)


def autopilot_branch_result_label(branch: dict | None) -> str:
    if not branch:
        return "-"
    labels = []
    for value in (
        branch.get("reasonCategory"),
        branch.get("recentWindowStatus"),
        branch.get("stressStatus"),
    ):
        if value and value not in labels:
            labels.append(str(value))
    return " / ".join(labels) if labels else "-"


def autopilot_period_days(period: str | None) -> int:
    match = re.match(r"^(\d+)d$", str(period or "").strip().lower())
    return int(match.group(1)) if match else 0


def autopilot_branch_is_confirmed_candidate(branch: dict | None) -> bool:
    if not branch:
        return False
    return (
        branch.get("reasonCategory") == "PROMISING_STABLE"
        or branch.get("eligibilityStatus") == "CHALLENGER_ELIGIBLE"
        or branch.get("bestTier") == "CHALLENGER_ELIGIBLE"
    )


def autopilot_confirmed_chains(branches: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for branch in branches:
        if not is_real_research_candidate(branch) or not autopilot_branch_is_confirmed_candidate(branch):
            continue
        key = (
            str(branch.get("strategy") or ""),
            str(branch.get("symbol") or "").upper(),
            str(branch.get("timeframe") or "").lower(),
        )
        grouped.setdefault(key, []).append(branch)
    chains = []
    for (strategy, symbol, timeframe), rows in grouped.items():
        by_period = {str(row.get("period") or "").lower(): row for row in rows}
        if "730d" not in by_period or "1095d" not in by_period:
            continue
        ordered = sorted(rows, key=lambda row: autopilot_period_days(row.get("period")))
        best = ordered[-1]
        chains.append({
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            "periods": [row.get("period") for row in ordered],
            "label": f"{strategy} {symbol} {timeframe} confirmed chain: {' + '.join(row.get('period') or '-' for row in ordered)}",
            "branches": ordered,
            "bestBranch": best,
        })
    return sorted(chains, key=lambda chain: (
        -autopilot_period_days((chain.get("bestBranch") or {}).get("period")),
        -safe_float((chain.get("bestBranch") or {}).get("profitFactor"), 0),
        chain.get("strategy") or "",
        chain.get("symbol") or "",
        chain.get("timeframe") or "",
    ))


def autopilot_best_candidate_sort_key(branch: dict) -> tuple:
    return (
        0 if autopilot_branch_is_confirmed_candidate(branch) else 1,
        -autopilot_period_days(branch.get("period")),
        -safe_float(branch.get("profitFactor"), 0),
        -safe_float(branch.get("totalReturnPct"), 0),
        -(int(safe_float(branch.get("fullTrades"), 0))),
        branch.get("strategy") or "",
        branch.get("symbol") or "",
        branch.get("timeframe") or "",
    )


def autopilot_best_current_candidate(memory: dict) -> tuple[dict | None, dict | None, list[dict]]:
    branches = [row for row in memory.get("branches", []) if is_real_research_candidate(row)]
    chains = autopilot_confirmed_chains(branches)
    if chains:
        chain = chains[0]
        return chain.get("bestBranch"), chain, chains
    eligible = sorted([row for row in branches if autopilot_branch_is_confirmed_candidate(row)], key=autopilot_best_candidate_sort_key)
    if eligible:
        return eligible[0], None, chains
    rare = sorted([row for row in branches if row.get("reasonCategory") == "PROMISING_BUT_RARE"], key=autopilot_best_candidate_sort_key)
    return (rare[0] if rare else None), None, chains


def autopilot_branch_failed_confirmation(branch: dict | None) -> bool:
    if not branch:
        return False
    category = branch.get("reasonCategory")
    if category in AUTOPILOT_REJECTED_CATEGORIES:
        return True
    if branch.get("stressStatus") == "COLLAPSES_UNDER_STRESS":
        return True
    if branch.get("recentWindowStatus") == "RECENTLY_WEAK":
        return True
    if branch.get("eligibilityStatus") == "REJECTED" or branch.get("bestTier") == "REJECTED":
        return True
    if int(safe_float(branch.get("foldPassCount"), 0)) and int(safe_float(branch.get("foldPassCount"), 0)) < 3:
        return True
    return False


def autopilot_failed_parent_period_confirmation(memory: dict, strategy: str, symbol: str, parent_timeframe: str, parent_period: str) -> dict | None:
    parent_days = autopilot_period_days(parent_period)
    failures = []
    for branch in memory.get("branches", []):
        if not is_real_research_candidate(branch):
            continue
        if str(branch.get("strategy") or "") != str(strategy or ""):
            continue
        if str(branch.get("symbol") or "").upper() != str(symbol or "").upper():
            continue
        if str(branch.get("timeframe") or "").lower() != str(parent_timeframe or "").lower():
            continue
        branch_days = autopilot_period_days(branch.get("period"))
        if branch_days <= parent_days:
            continue
        if autopilot_branch_failed_confirmation(branch):
            failures.append(branch)
    return sorted(failures, key=lambda row: autopilot_period_days(row.get("period")), reverse=True)[0] if failures else None


def autopilot_related_lower_timeframe_rejection(memory: dict, strategy: str, symbol: str, timeframe: str) -> dict | None:
    for branch in memory.get("branches", []):
        if not is_real_research_candidate(branch):
            continue
        if str(branch.get("strategy") or "") != str(strategy or ""):
            continue
        if str(branch.get("symbol") or "").upper() != str(symbol or "").upper():
            continue
        if str(branch.get("timeframe") or "").lower() != str(timeframe or "").lower():
            continue
        category = branch.get("reasonCategory")
        if category in AUTOPILOT_LOWER_TIMEFRAME_REJECTED_CATEGORIES or branch.get("stressStatus") == "COLLAPSES_UNDER_STRESS":
            return branch
    return None


def autopilot_failed_long_confirmation_strategies(memory: dict) -> set[str]:
    failed = set()
    for branch in memory.get("branches", []):
        if not is_real_research_candidate(branch):
            continue
        if str(branch.get("period") or "").lower() != "1095d":
            continue
        category = branch.get("reasonCategory")
        if category in AUTOPILOT_REJECTED_CATEGORIES or branch.get("stressStatus") == "COLLAPSES_UNDER_STRESS" or branch.get("recentWindowStatus") == "RECENTLY_WEAK":
            strategy = branch.get("strategy")
            if strategy:
                failed.add(strategy)
    return failed


def autopilot_strategy_order(memory: dict) -> list[str]:
    strategies = list(AUTOPILOT_DEFAULT_STRATEGIES)
    if not autopilot_failed_long_confirmation_strategies(memory):
        return strategies
    preferred = [strategy for strategy in AUTOPILOT_NEW_FAMILY_PREFERRED_STRATEGIES if strategy in strategies]
    return preferred + [strategy for strategy in strategies if strategy not in preferred]


def autopilot_parse_force_branch(raw: str | None) -> dict | None:
    if not raw:
        return None
    parts = [part.strip() for part in str(raw).split(":")]
    if len(parts) != 4 or not all(parts):
        return None
    return {"strategy": parts[0], "symbol": parts[1], "timeframe": parts[2], "period": parts[3]}


def autopilot_plan_jobs(memory: dict, queue: dict, max_jobs: int = 12, include_cooled: bool = False, force_strategy: str | None = None, force_branch: str | None = None, planning_mode: str = "balanced") -> tuple[list[dict], list[str], list[dict]]:
    jobs = []
    warnings = []
    skipped = []
    queued_or_done = {job.get("signature") for job in queue.get("jobs", []) if job.get("status") in {"QUEUED", "RUNNING", "DONE"}}
    existing_branch_keys = autopilot_queue_branch_keys(queue)
    planned_branch_keys = set()
    branch_map = autopilot_memory_branch_map(memory)
    planning_mode = normalize_autopilot_planning_mode(planning_mode)
    family_map = autopilot_family_map(memory, planning_mode=planning_mode)
    failed_long_confirmation_strategies = autopilot_failed_long_confirmation_strategies(memory)
    forced_strategy = str(force_strategy or "").strip() or None
    forced_branch = autopilot_parse_force_branch(force_branch)

    def maybe_add(job: dict, bypass_family: bool = False, bypass_branch: bool = False):
        if len(jobs) >= max_jobs:
            return False
        job_keys = autopilot_job_branch_keys(job)
        duplicate_keys = sorted(job_keys & (existing_branch_keys | planned_branch_keys))
        if duplicate_keys:
            skipped.append(autopilot_skip_record(job, "duplicate_queued_job", f"Skipped because branch is already queued, running, done, or planned: {duplicate_keys[0]}.", duplicate_keys[0], branch_map.get(duplicate_keys[0])))
            return False
        if not bypass_branch:
            for key in sorted(job_keys):
                branch = branch_map.get(key)
                if branch and branch.get("reasonCategory") in AUTOPILOT_REJECTED_CATEGORIES:
                    reason = "recently_tested_rejected_branch" if autopilot_branch_was_recent(branch) else "rejected_branch"
                    skipped.append(autopilot_skip_record(job, reason, f"Skipped because branch {key} is {branch.get('reasonCategory')}.", key, branch))
                    return False
                if branch and branch.get("reasonCategory") in AUTOPILOT_NO_LEAD_CATEGORIES:
                    skipped.append(autopilot_skip_record(
                        job,
                        "already_tested_no_research_lead",
                        f"Skipped {branch.get('strategy') or '-'} {branch.get('symbol') or '-'} {branch.get('timeframe') or '-'} {branch.get('period') or '-'} because this exact branch was already tested and produced NO_RESEARCH_LEAD.",
                        key,
                        branch,
                    ))
                    return False
                if branch and autopilot_branch_is_eligible_or_stable(branch):
                    skipped.append(autopilot_skip_record(
                        job,
                        "already_tested_eligible_branch",
                        f"Skipped {branch.get('strategy') or '-'} {branch.get('symbol') or '-'} {branch.get('timeframe') or '-'} {branch.get('period') or '-'} because this exact eligible branch was already tested as {branch.get('reasonCategory') or branch.get('eligibilityStatus')}.",
                        key,
                        branch,
                    ))
                    return False
                if branch and not autopilot_branch_is_eligible_or_stable(branch):
                    skipped.append(autopilot_skip_record(
                        job,
                        "already_tested_branch",
                        f"Skipped {branch.get('strategy') or '-'} {branch.get('symbol') or '-'} {branch.get('timeframe') or '-'} {branch.get('period') or '-'} because this exact branch was already tested and remains {autopilot_branch_result_label(branch)}.",
                        key,
                        branch,
                    ))
                    return False
        if not bypass_family and not include_cooled:
            for strategy in autopilot_list(job.get("strategies") or job.get("strategy")):
                family = family_map.get(strategy)
                if family and family.get("familyStatus") in AUTOPILOT_COOL_DOWN_STATUSES:
                    skipped.append(autopilot_skip_record(job, "cooled_strategy_family", f"Skipped because {strategy} family is {family.get('familyStatus')}: {family.get('reason')}", next(iter(job_keys), None), {"family": family}))
                    return False
        if job.get("signature") in queued_or_done or any(existing.get("signature") == job.get("signature") for existing in jobs):
            skipped.append(autopilot_skip_record(job, "duplicate_queued_job", "Skipped because an exact job signature is already queued, running, done, or planned."))
            return
        jobs.append(job)
        planned_branch_keys.update(job_keys)
        return True

    if forced_branch:
        maybe_add(make_autopilot_job(
            [forced_branch["strategy"]],
            [forced_branch["symbol"]],
            [forced_branch["timeframe"]],
            forced_branch["period"],
            "Manually forced research branch.",
            100,
            generated_by="forced_branch",
            max_combos=10,
            top_n=20,
        ), bypass_family=True, bypass_branch=True)

    if not memory.get("branches"):
        for strategy in AUTOPILOT_DEFAULT_STRATEGIES[:5]:
            for timeframe in AUTOPILOT_TIMEFRAMES:
                maybe_add(make_autopilot_job(
                    [strategy],
                    AUTOPILOT_FIRST_PASS_SYMBOLS,
                    [timeframe],
                    "365d",
                    f"Initial safe first-pass scan for {strategy} on {timeframe}.",
                    priority=70 if timeframe == "1h" else 65,
                    generated_by="seed",
                    max_combos=12,
                    top_n=18,
                ))
        return jobs, warnings, skipped

    for branch in memory.get("branches", []):
        strategy = branch.get("strategy")
        symbol = branch.get("symbol")
        timeframe = branch.get("timeframe")
        period = branch.get("period") or "365d"
        category = branch.get("reasonCategory")
        if not strategy or not symbol or not timeframe:
            continue
        if category == "PROMISING_STABLE":
            next_period = "1095d" if period != "1095d" else period
            maybe_add(make_autopilot_job([strategy], [symbol], [timeframe], next_period, "Confirm eligible candidate on longer/full validation window.", 95, generated_by="eligible_confirmation", previous_evidence=branch, max_combos=10, top_n=20, include_stress=True, include_recent=True, include_repro=True))
        elif category == "PROMISING_BUT_RARE":
            if period in {"365d", "730d"}:
                next_period = "730d" if period == "365d" else "1095d"
                priority = 52 if strategy in failed_long_confirmation_strategies else 88
                maybe_add(make_autopilot_job([strategy], [symbol], [timeframe], next_period, "Confirm promising-but-rare branch on a wider period before more expansion.", priority, generated_by="rare_period_confirmation", previous_evidence=branch, max_combos=10, top_n=20), bypass_family=True)
            if timeframe == "4h":
                lower_timeframe = "1h"
                lower_rejection = autopilot_related_lower_timeframe_rejection(memory, strategy, symbol, lower_timeframe)
                parent_confirmation_failure = autopilot_failed_parent_period_confirmation(memory, strategy, symbol, timeframe, period)
                lower_job = make_autopilot_job([strategy], [symbol], [lower_timeframe], period, "Promising but rare on 4h; test lower timeframe for more activity.", 42 if strategy in failed_long_confirmation_strategies else 82, generated_by="rare_lower_timeframe", previous_evidence=branch, max_combos=10, top_n=20)
                if parent_confirmation_failure:
                    parent_key = autopilot_branch_key(parent_confirmation_failure, parent_confirmation_failure.get("period"))
                    skipped.append(autopilot_skip_record(
                        lower_job,
                        "failed_parent_period_confirmation",
                        f"Skipped {strategy} {symbol} {lower_timeframe} {period} because {symbol} {timeframe} {parent_confirmation_failure.get('period') or '-'} failed as {parent_confirmation_failure.get('reasonCategory')}.",
                        parent_key,
                        parent_confirmation_failure,
                    ))
                elif lower_rejection:
                    lower_key = autopilot_branch_key(lower_rejection, lower_rejection.get("period"))
                    skipped.append(autopilot_skip_record(
                        lower_job,
                        "rejected_lower_timeframe_period_retry",
                        f"Skipped {strategy} {symbol} {lower_timeframe} {period} because {symbol} {lower_timeframe} {lower_rejection.get('period') or '-'} was already rejected as {lower_rejection.get('reasonCategory')}.",
                        lower_key,
                        lower_rejection,
                    ))
                else:
                    maybe_add(lower_job, bypass_family=True)
            other_symbols = [item for item in AUTOPILOT_FIRST_PASS_SYMBOLS + AUTOPILOT_SECOND_PASS_SYMBOLS if item != symbol][:3]
            maybe_add(make_autopilot_job([strategy], other_symbols, [timeframe], period, "Promising but rare; test same strategy/timeframe on related symbols.", 46 if strategy in failed_long_confirmation_strategies else 78, generated_by="rare_symbol_expansion", previous_evidence=branch, max_combos=10, top_n=20), bypass_family=True)
        elif category in {"NEGATIVE_RETURN", "LOW_PROFIT_FACTOR", "REJECTED"}:
            warnings.append(f"Deprioritized rejected branch {strategy} {symbol} {timeframe} {period}.")

    for strategy in autopilot_strategy_order(memory):
        if forced_strategy and strategy != forced_strategy:
            continue
        for symbol in AUTOPILOT_FIRST_PASS_SYMBOLS:
            for timeframe in AUTOPILOT_TIMEFRAMES:
                period = "365d"
                branch_key = autopilot_normalized_branch_key(strategy, symbol, timeframe, period)
                branch = branch_map.get(branch_key)
                if branch and branch.get("reasonCategory") in AUTOPILOT_REJECTED_CATEGORIES:
                    warnings.append(f"Deprioritized rejected branch {strategy} {symbol} {timeframe} {period}.")
                    skipped.append(autopilot_skip_record(make_autopilot_job([strategy], [symbol], [timeframe], period, "Broader safe exploration across untested strategy/market branches.", 50, generated_by="broad_search", max_combos=10, top_n=15), "rejected_branch", f"Skipped because branch {branch_key} is {branch.get('reasonCategory')}.", branch_key, branch))
                    continue
                maybe_add(make_autopilot_job([strategy], [symbol], [timeframe], period, "Broader safe exploration across untested strategy/market branches.", 50, generated_by="broad_search", max_combos=10, top_n=15), bypass_family=bool(forced_strategy and strategy == forced_strategy))
                if len(jobs) >= max_jobs:
                    return sorted(jobs, key=lambda item: (-safe_float(item.get("priority"), 0), item.get("createdAt") or ""))[:max_jobs], warnings, skipped
    return sorted(jobs, key=lambda item: (-safe_float(item.get("priority"), 0), item.get("createdAt") or ""))[:max_jobs], warnings, skipped


def build_research_autopilot_status() -> dict:
    queue = load_autopilot_queue()
    recovered = recover_stale_autopilot_running_jobs(queue)
    memory, backfill = load_autopilot_memory_after_backfill()
    if not memory.get("branches") and candidate_ledger_source_files(20):
        memory = rebuild_autopilot_memory_from_saved_reports(40)
    skipped_deprioritized = skip_deprioritized_autopilot_queue_jobs(queue, memory)
    if recovered or skipped_deprioritized:
        queue["lastPlanSkippedJobs"] = (skipped_deprioritized + queue.get("lastPlanSkippedJobs", []))[:25]
        save_autopilot_queue(queue)
    counts = autopilot_queue_counts(queue)
    leads = [row for row in memory.get("branches", []) if row.get("reasonCategory") in {"PROMISING_STABLE", "PROMISING_BUT_RARE"}]
    best_candidate, confirmed_chain, confirmed_chains = autopilot_best_current_candidate(memory)
    sorted_leads = sorted(leads, key=autopilot_best_candidate_sort_key)
    if best_candidate:
        sorted_leads = [best_candidate] + [row for row in sorted_leads if autopilot_branch_key(row, row.get("period")) != autopilot_branch_key(best_candidate, best_candidate.get("period"))]
    rejected = [row for row in memory.get("branches", []) if row.get("reasonCategory") in {"NEGATIVE_RETURN", "LOW_PROFIT_FACTOR", "REJECTED"}]
    rare = [row for row in memory.get("branches", []) if row.get("reasonCategory") == "PROMISING_BUT_RARE"]
    families = autopilot_family_summary(memory)
    next_jobs = [job for job in queue.get("jobs", []) if job.get("status") == "QUEUED"][:5]
    safety = autopilot_safety_payload()
    return {
        "ok": True,
        "generatedAt": autopilot_now(),
        "queuePath": autopilot_display_path(RESEARCH_AUTOPILOT_QUEUE_PATH),
        "memoryPath": autopilot_display_path(RESEARCH_AUTOPILOT_MEMORY_PATH),
        "queue": {"counts": counts, "length": len(queue.get("jobs", [])), "nextJobs": next_jobs, "recoveredStaleJobs": recovered, "skippedDeprioritizedJobs": skipped_deprioritized, "lastPlanSkippedJobs": queue.get("lastPlanSkippedJobs", []), "lastPlanWarnings": queue.get("lastPlanWarnings", [])},
        "backfill": backfill,
        "memory": {
            "branchesTested": len(memory.get("branches", [])),
            "candidates": len(memory.get("candidates", [])),
            "sourceReports": len(memory.get("sourceReports", [])),
            "topLeads": sorted_leads[:8],
            "bestCurrentCandidate": best_candidate,
            "confirmedChain": confirmed_chain,
            "confirmedChains": confirmed_chains,
            "rejectedBranches": rejected[:12],
            "promisingButRare": rare[:8],
            "strategyFamilies": families,
        },
        "safety": safety,
    }


def build_research_autopilot_plan(args) -> tuple[dict, int]:
    queue = load_autopilot_queue()
    recovered = recover_stale_autopilot_running_jobs(queue)
    memory, backfill = load_autopilot_memory_after_backfill()
    if not memory.get("branches") and candidate_ledger_source_files(20):
        memory = rebuild_autopilot_memory_from_saved_reports(40)
    skipped_deprioritized = skip_deprioritized_autopilot_queue_jobs(queue, memory)
    max_jobs = max(1, min(int(safe_float(args.get("maxJobs", args.get("max_jobs", 5)), 5)), 20))
    planning_mode = normalize_autopilot_planning_mode(args.get("planningMode", args.get("mode", args.get("planning_mode"))))
    include_cooled = str(args.get("includeCooled", args.get("include_cooled", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    force_strategy = args.get("forceStrategy", args.get("force_strategy"))
    force_branch = args.get("forceBranch", args.get("force_branch"))
    planned, warnings, planner_skipped = autopilot_plan_jobs(memory, queue, max_jobs=max_jobs, include_cooled=include_cooled, force_strategy=force_strategy, force_branch=force_branch, planning_mode=planning_mode)
    added, enqueue_skipped = autopilot_enqueue(queue, planned)
    skipped = skipped_deprioritized + planner_skipped + enqueue_skipped
    queue["lastPlanSkippedJobs"] = skipped[:25]
    queue["lastPlanWarnings"] = warnings[:25]
    save_autopilot_queue(queue)
    return {
        "ok": True,
        "generatedAt": autopilot_now(),
        "addedJobs": added,
        "skippedJobs": skipped,
        "queue": {"counts": autopilot_queue_counts(queue), "length": len(queue.get("jobs", [])), "recoveredStaleJobs": recovered, "skippedDeprioritizedJobs": skipped_deprioritized},
        "backfill": backfill,
        "memorySummary": {"branches": len(memory.get("branches", [])), "candidates": len(memory.get("candidates", []))},
        "plannerOptions": {"includeCooled": include_cooled, "forceStrategy": force_strategy, "forceBranch": force_branch, "planningMode": planning_mode, "maxJobs": max_jobs},
        "strategyFamilies": autopilot_family_summary(memory, planning_mode=planning_mode),
        "warnings": warnings,
        "safety": autopilot_safety_payload(),
    }, 200


def build_research_autopilot_backfill_memory(args) -> tuple[dict, int]:
    file_limit = max(1, min(int(safe_float(args.get("fileLimit", args.get("file_limit", 250)), 250)), 1000))
    payload = backfill_autopilot_no_research_leads(file_limit=file_limit)
    payload["fileLimit"] = file_limit
    return payload, 200


def autopilot_dossier_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return slug or "candidate"


def autopilot_branch_metric_row(branch: dict) -> dict:
    return {
        "period": branch.get("period"),
        "bestTier": branch.get("bestTier"),
        "eligibilityStatus": branch.get("eligibilityStatus"),
        "reasonCategory": branch.get("reasonCategory"),
        "profitFactor": safe_float(branch.get("profitFactor"), 0),
        "totalReturnPct": safe_float(branch.get("totalReturnPct"), 0),
        "fullTrades": int(safe_float(branch.get("fullTrades"), 0)),
        "foldPassCount": int(safe_float(branch.get("foldPassCount"), 0)),
        "negativeFolds": int(safe_float(branch.get("negativeFolds", branch.get("negativeFoldCount")), 0)),
        "maxDrawdownPct": safe_float(branch.get("maxDrawdownPct"), 0),
        "recentWindowStatus": branch.get("recentWindowStatus") or "UNKNOWN",
        "stressStatus": branch.get("stressStatus") or "UNKNOWN",
        "paramsHash": branch.get("paramsHash"),
        "candidateKey": branch.get("candidateKey"),
    }


def autopilot_source_report_payload(branch: dict) -> tuple[dict | None, str | None]:
    raw = branch.get("sourceReport")
    if not raw:
        return None, None
    path = resolve_research_report_path(str(raw))
    if not path or not path.exists():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), autopilot_display_path(path)
    except Exception:
        return None, autopilot_display_path(path)


def autopilot_candidate_match(row: dict | None, branch: dict) -> bool:
    if not isinstance(row, dict):
        return False
    if branch.get("candidateKey") and row.get("candidateKey") == branch.get("candidateKey"):
        return True
    if branch.get("paramsHash") and row.get("paramsHash") == branch.get("paramsHash"):
        return True
    return (
        str(row.get("strategy") or "") == str(branch.get("strategy") or "")
        and str(row.get("symbol") or "").upper() == str(branch.get("symbol") or "").upper()
        and str(row.get("timeframe") or "").lower() == str(branch.get("timeframe") or "").lower()
    )


def autopilot_stability_candidates_from_report(report: dict) -> list[dict]:
    summary = (((report.get("modules") or {}).get("stabilityFirstSearch") or {}).get("summary") or {})
    rows = []
    for key in ("bestEligibleChallenger", "bestStableCandidate", "bestResearchedCandidate", "benchmark"):
        value = summary.get(key)
        if isinstance(value, dict) and value:
            rows.append(value)
    rows.extend(row for row in (summary.get("topCandidates") or []) if isinstance(row, dict))
    return rows


def autopilot_deep_validations_from_report(report: dict) -> list[dict]:
    validations = ((report.get("modules") or {}).get("deepValidation") or [])
    return [row for row in validations if isinstance(row, dict)]


def autopilot_find_report_candidate(report: dict | None, branch: dict) -> dict:
    if not report:
        return {}
    matches = [row for row in autopilot_stability_candidates_from_report(report) if autopilot_candidate_match(row, branch)]
    return matches[0] if matches else {}


def autopilot_find_report_validation(report: dict | None, branch: dict, candidate: dict) -> dict:
    if not report:
        return {}
    for row in autopilot_deep_validations_from_report(report):
        if branch.get("candidateKey") and row.get("candidateKey") == branch.get("candidateKey"):
            return row
        if branch.get("paramsHash") and row.get("paramsHash") == branch.get("paramsHash"):
            return row
        if candidate.get("candidateKey") and row.get("candidateKey") == candidate.get("candidateKey"):
            return row
    return {}


def autopilot_concentration_threshold(candidate: dict) -> str:
    for gate in format_failed_gates(candidate.get("failedGates") or []):
        detail = gate.get("detail") or ""
        if gate.get("name") == "concentration" or "concentration" in detail.lower():
            return detail
    return "No concentration gate failure recorded."


def autopilot_branch_source_details(branch: dict) -> dict:
    report, source_path = autopilot_source_report_payload(branch)
    candidate = autopilot_find_report_candidate(report, branch)
    validation = autopilot_find_report_validation(report, branch, candidate)
    wf = validation.get("walkForward") or {}
    fee = validation.get("feeSlippageStress") or {}
    benchmark = ((((report or {}).get("modules") or {}).get("stabilityFirstSearch") or {}).get("summary") or {}).get("benchmark") or {}
    search = ((((report or {}).get("modules") or {}).get("stabilityFirstSearch") or {}).get("summary") or {}).get("search") or {}
    return {
        "sourceReport": branch.get("sourceReport"),
        "sourceReportResolved": source_path,
        "sourceReportLoaded": bool(report),
        "candidate": candidate,
        "validation": validation,
        "params": candidate.get("params") or candidate.get("normalizedParams") or {},
        "walkForward": {
            "full": wf.get("full") or {},
            "folds": wf.get("folds") or [],
            "recentWindows": wf.get("recentWindows") or [],
            "stability": wf.get("stability") or {},
        },
        "stress": {
            "status": fee.get("status") or candidate.get("stressStatus") or branch.get("stressStatus") or "UNKNOWN",
            "baseline": fee.get("baseline") or {},
            "worstPassingScenario": fee.get("worstPassingScenario") or {},
            "firstFailureScenario": fee.get("firstFailureScenario") or {},
            "survivingScenarios": fee.get("survivingScenarios") or [],
            "failedScenarios": fee.get("failedScenarios") or [],
            "recommendation": fee.get("recommendation") or {},
        },
        "recent": {
            "status": candidate.get("recentWindowStatus") or branch.get("recentWindowStatus") or "UNKNOWN",
            "windows": wf.get("recentWindows") or [],
        },
        "repro": {
            "status": candidate.get("reproducibilityStatus") or branch.get("reproducibilityStatus") or "UNKNOWN",
            "rerunCount": search.get("reproducibilityAudited"),
            "stable": candidate.get("reproducibilityStatus") == "REPRODUCIBLE",
            "diff": candidate.get("reproducibilityDiff") or "No reproducibility diff recorded.",
        },
        "concentration": {
            "returnConcentrationPct": candidate.get("returnConcentrationPct"),
            "threshold": autopilot_concentration_threshold(candidate),
            "status": "PASS" if candidate.get("returnConcentrationPct") is not None and not any((gate.get("name") == "concentration" or "concentration" in (gate.get("detail") or "").lower()) for gate in format_failed_gates(candidate.get("failedGates") or [])) else ("UNKNOWN" if candidate.get("returnConcentrationPct") is None else "FAIL"),
        },
        "benchmark": {
            "strategy": benchmark.get("strategy"),
            "symbol": benchmark.get("symbol"),
            "timeframe": benchmark.get("timeframe"),
            "stabilityScore": benchmark.get("stabilityScore"),
            "negativeFoldCount": benchmark.get("negativeFoldCount"),
            "candidateStabilityScore": candidate.get("stabilityScore"),
            "candidateNegativeFoldCount": candidate.get("negativeFoldCount"),
            "candidateVsBenchmarkStabilityDelta": safe_float(candidate.get("stabilityScore"), 0) - safe_float(benchmark.get("stabilityScore"), 0) if candidate.get("stabilityScore") is not None and benchmark.get("stabilityScore") is not None else None,
        },
        "warnings": validation.get("warnings") or [],
    }


def autopilot_table(headers: list[str], rows: list[list]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item if item is not None else "-") for item in row) + " |")
    return "\n".join(lines)


def render_autopilot_candidate_dossier_markdown(dossier: dict) -> str:
    chain = dossier.get("confirmedChain") or {}
    branches = dossier.get("branches") or []
    details = dossier.get("details") or []
    metrics = dossier.get("metrics") or []
    identity = dossier.get("identity") or {}
    safety = dossier.get("safety") or {}
    metric_rows = [[
        row.get("period"),
        row.get("bestTier"),
        row.get("eligibilityStatus"),
        row.get("reasonCategory"),
        f"{safe_float(row.get('profitFactor'), 0):.4g}",
        f"{safe_float(row.get('totalReturnPct'), 0):.4g}%",
        row.get("fullTrades"),
        row.get("foldPassCount"),
        row.get("negativeFolds"),
        f"{safe_float(row.get('maxDrawdownPct'), 0):.4g}%",
        row.get("recentWindowStatus"),
        row.get("stressStatus"),
    ] for row in metrics]
    gate_rows = []
    for branch in branches:
        gates = format_failed_gates(branch.get("failedGates") or [])
        if not gates:
            gate_rows.append([branch.get("period"), "none", "No failed gates recorded."])
        else:
            gate_rows.extend([[branch.get("period"), gate.get("name"), gate.get("detail")] for gate in gates])
    param_rows = []
    wf_rows = []
    stress_rows = []
    recent_rows = []
    repro_rows = []
    concentration_rows = []
    benchmark_rows = []
    for detail in details:
        branch = detail.get("branch") or {}
        period = branch.get("period")
        params = detail.get("params") or {}
        param_rows.append([period, json.dumps(params, sort_keys=True) if params else "UNKNOWN"])
        for fold in ((detail.get("walkForward") or {}).get("folds") or []):
            wf_rows.append([period, fold.get("fold", fold.get("index", "-")), f"{fold.get('startTime', '-')}/{fold.get('endTime', '-')}", fold.get("totalReturnPct", fold.get("returnPct", "-")), fold.get("profitFactor", "-"), fold.get("trades", "-"), fold.get("maxDrawdownPct", "-"), fold.get("status", "-")])
        if not ((detail.get("walkForward") or {}).get("folds") or []):
            wf_rows.append([period, "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"])
        stress = detail.get("stress") or {}
        baseline = stress.get("baseline") or {}
        worst = stress.get("worstPassingScenario") or {}
        first_failure = stress.get("firstFailureScenario") or {}
        stress_rows.append([period, "baseline", stress.get("status", "UNKNOWN"), baseline.get("totalReturnPct", "UNKNOWN"), baseline.get("profitFactor", "UNKNOWN"), baseline.get("trades", "UNKNOWN"), "base"])
        stress_rows.append([period, worst.get("scenario") or "worstPassing", stress.get("status", "UNKNOWN"), worst.get("totalReturnPct", "UNKNOWN"), worst.get("profitFactor", "UNKNOWN"), worst.get("trades", "UNKNOWN"), (worst.get("degradationVsBaseline") or {}).get("returnDiffPct", "UNKNOWN")])
        if first_failure:
            stress_rows.append([period, first_failure.get("scenario") or "firstFailure", "FAIL", first_failure.get("totalReturnPct", "UNKNOWN"), first_failure.get("profitFactor", "UNKNOWN"), first_failure.get("trades", "UNKNOWN"), first_failure.get("mainFailureReason", "UNKNOWN")])
        recent = detail.get("recent") or {}
        for window in recent.get("windows") or []:
            recent_rows.append([period, window.get("label", "-"), recent.get("status", "UNKNOWN"), window.get("totalReturnPct", "UNKNOWN"), window.get("profitFactor", "UNKNOWN"), window.get("trades", "UNKNOWN"), window.get("maxDrawdownPct", "UNKNOWN"), window.get("status", "UNKNOWN")])
        if not (recent.get("windows") or []):
            recent_rows.append([period, "UNKNOWN", recent.get("status", "UNKNOWN"), "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"])
        repro = detail.get("repro") or {}
        repro_rows.append([period, repro.get("status", "UNKNOWN"), repro.get("rerunCount", "UNKNOWN"), repro.get("stable", "UNKNOWN"), repro.get("diff", "UNKNOWN")])
        concentration = detail.get("concentration") or {}
        concentration_rows.append([period, branch.get("fullTrades", 0), concentration.get("returnConcentrationPct", "UNKNOWN"), concentration.get("threshold", "UNKNOWN"), concentration.get("status", "UNKNOWN")])
        benchmark = detail.get("benchmark") or {}
        benchmark_rows.append([period, benchmark.get("strategy", "UNKNOWN"), benchmark.get("stabilityScore", "UNKNOWN"), benchmark.get("negativeFoldCount", "UNKNOWN"), benchmark.get("candidateStabilityScore", "UNKNOWN"), benchmark.get("candidateNegativeFoldCount", "UNKNOWN"), benchmark.get("candidateVsBenchmarkStabilityDelta", "UNKNOWN")])
    warnings = dossier.get("warnings") or ["Manual review required before any paper or live action."]
    lines = [
        f"# {chain.get('label') or identity.get('label') or 'Research Autopilot Candidate Dossier'}",
        "",
        "## Verdict",
        "",
        "- Manual review only.",
        "- Not paper-enabled.",
        "- Not live-enabled.",
        "- No automatic promotion.",
        f"- researchOnly={safety.get('researchOnly')} paperEnabled={safety.get('paperEnabled')} realTradingEnabled={safety.get('realTradingEnabled')} configWritten={safety.get('configWritten')} paperStateChanged={safety.get('paperStateChanged')} liveOrdersTouched={safety.get('liveOrdersTouched')}",
        "",
        "## Candidate Identity",
        "",
        f"- Strategy: {identity.get('strategy')}",
        f"- Symbol: {identity.get('symbol')}",
        f"- Timeframe: {identity.get('timeframe')}",
        f"- Params hash: {identity.get('paramsHash') or '-'}",
        f"- Candidate key: {identity.get('candidateKey') or '-'}",
        "",
        "## Full Parameters",
        "",
        autopilot_table(["Period", "Parameters"], param_rows),
        "",
        "## Confirmed Chain",
        "",
        f"- Periods: {' + '.join(chain.get('periods') or [])}",
        "",
        "## Metrics",
        "",
        autopilot_table(["Period", "Tier", "Eligibility", "Category", "PF", "Return", "Trades", "Fold pass", "Negative folds", "Max DD", "Recent", "Stress"], metric_rows),
        "",
        "## Failed Gates",
        "",
        autopilot_table(["Period", "Gate", "Detail"], gate_rows),
        "",
        "## Walk Forward",
        "",
        autopilot_table(["Period", "Fold", "Window", "Return", "PF", "Trades", "Drawdown", "Status"], wf_rows),
        "",
        "## Stress",
        "",
        autopilot_table(["Period", "Scenario", "Stress status", "Return", "PF", "Trades", "Change"], stress_rows),
        "",
        "## Recent Windows",
        "",
        autopilot_table(["Period", "Window", "Recent status", "Return", "PF", "Trades", "Drawdown", "Window status"], recent_rows),
        "",
        "## Repro Audit",
        "",
        autopilot_table(["Period", "Status", "Reruns audited", "Stable", "Diff"], repro_rows),
        "",
        "## Trade Count And Concentration",
        "",
        autopilot_table(["Period", "Trades", "Return concentration", "Threshold", "Status"], concentration_rows),
        "",
        "## Benchmark Comparison",
        "",
        autopilot_table(["Period", "Benchmark", "Benchmark stability", "Benchmark negative folds", "Candidate stability", "Candidate negative folds", "Stability delta"], benchmark_rows),
        "",
        "## Warnings And Known Weaknesses",
        "",
    ]
    lines.extend(f"- {warning}" for warning in warnings)
    lines.append("")
    return "\n".join(lines)


def build_research_autopilot_candidate_dossier(args) -> tuple[dict, int]:
    strategy = str(args.get("strategy") or "").strip()
    symbol = str(args.get("symbol") or "").strip().upper()
    timeframe = str(args.get("timeframe") or args.get("interval") or "").strip().lower()
    if not strategy or not symbol or not timeframe:
        return {"ok": False, "error": "strategy, symbol, and timeframe are required.", "safety": autopilot_safety_payload()}, 400
    memory, backfill = load_autopilot_memory_after_backfill()
    chains = [
        chain for chain in autopilot_confirmed_chains(memory.get("branches", []))
        if chain.get("strategy") == strategy and chain.get("symbol") == symbol and chain.get("timeframe") == timeframe
    ]
    if not chains:
        return {"ok": False, "error": f"No confirmed chain found for {strategy} {symbol} {timeframe}.", "backfill": backfill, "safety": autopilot_safety_payload()}, 404
    chain = chains[0]
    branches = chain.get("branches") or []
    best = chain.get("bestBranch") or branches[-1]
    identity = {
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
        "paramsHash": best.get("paramsHash") or next((branch.get("paramsHash") for branch in branches if branch.get("paramsHash")), None),
        "candidateKey": best.get("candidateKey") or next((branch.get("candidateKey") for branch in branches if branch.get("candidateKey")), None),
        "label": chain.get("label"),
    }
    details = [{**autopilot_branch_source_details(branch), "branch": branch} for branch in branches]
    detailed_warnings = []
    for detail in details:
        branch = detail.get("branch") or {}
        if not detail.get("sourceReportLoaded"):
            detailed_warnings.append(f"{autopilot_branch_label(branch)} source report could not be loaded; detailed sections may show UNKNOWN.")
        detailed_warnings.extend(detail.get("warnings") or [])
    dossier = {
        "ok": True,
        "generatedAt": autopilot_now(),
        "identity": identity,
        "confirmedChain": chain,
        "branches": branches,
        "details": details,
        "metrics": [autopilot_branch_metric_row(branch) for branch in branches],
        "warnings": [
            "Stored branch memory may not include detailed walk-forward, stress, recent-window, reproducibility, concentration, or benchmark internals for every historical report.",
            "Manual review only; no config, paper, or live trading changes were made.",
        ] + detailed_warnings,
        "backfill": backfill,
        "safety": autopilot_safety_payload(),
    }
    markdown = render_autopilot_candidate_dossier_markdown(dossier)
    RESEARCH_DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{autopilot_dossier_slug(strategy)}-{autopilot_dossier_slug(symbol)}-{autopilot_dossier_slug(timeframe)}-confirmed-chain.md"
    path = RESEARCH_DOSSIER_DIR / filename
    path.write_text(markdown, encoding="utf-8")
    dossier["markdown"] = markdown
    dossier["savedPath"] = autopilot_display_path(path)
    return dossier, 200


def autopilot_review_strengths_from_dossier(dossier: dict) -> list[str]:
    strengths = []
    metrics = dossier.get("metrics") or []
    if metrics and all(row.get("eligibilityStatus") == "CHALLENGER_ELIGIBLE" for row in metrics):
        strengths.append("730d and 1095d branches are CHALLENGER_ELIGIBLE.")
    if metrics and all(row.get("stressStatus") == "SURVIVES_MODERATE_STRESS" for row in metrics):
        strengths.append("Confirmed branches survive moderate stress.")
    details = dossier.get("details") or []
    if details and all((detail.get("stress") or {}).get("status") in {"RESILIENT", "SURVIVES_MODERATE_STRESS"} for detail in details):
        strengths.append("Source reports show fee/slippage stress survival.")
    if details and all((detail.get("repro") or {}).get("status") == "REPRODUCIBLE" for detail in details):
        strengths.append("Source reports mark the candidate reproducible.")
    if details and all((detail.get("concentration") or {}).get("status") == "PASS" for detail in details):
        strengths.append("Return concentration passes stored gates.")
    return strengths or ["Confirmed challenger chain is available for manual review."]


def autopilot_review_warnings_from_dossier(dossier: dict) -> list[str]:
    warnings = [
        "Manual review only; package is disabled by default.",
        "Paper and live trading remain disabled.",
    ]
    details = dossier.get("details") or []
    for detail in details:
        branch = detail.get("branch") or {}
        period = branch.get("period") or "-"
        folds = ((detail.get("walkForward") or {}).get("folds") or [])
        failed_folds = [fold for fold in folds if str(fold.get("status") or "").upper() not in {"PASS", "OK"}]
        if failed_folds:
            warnings.append(f"{period} has {len(failed_folds)} non-passing walk-forward fold(s).")
        recent_windows = ((detail.get("recent") or {}).get("windows") or [])
        warn_windows = [window.get("label") for window in recent_windows if str(window.get("status") or "").upper() == "WARN"]
        if warn_windows:
            warnings.append(f"{period} recent windows need review: {', '.join(str(item) for item in warn_windows)}.")
        regime = ((detail.get("validation") or {}).get("regimeBreakdown") or {})
        if not regime or str((regime.get("summary") or {}).get("regimeDependencyStatus") or regime.get("status") or "").upper() in {"", "UNKNOWN", "NOT_RUN"}:
            warnings.append(f"{period} regime dependence is unknown or not fully recorded.")
    return dedupe_list(warnings + (dossier.get("warnings") or []))


def build_research_autopilot_prepare_paper_candidate(args) -> tuple[dict, int]:
    dossier, status = build_research_autopilot_candidate_dossier(args)
    if status >= 400 or not dossier.get("ok"):
        return {
            "ok": False,
            "error": dossier.get("error") or "Confirmed candidate dossier is required before preparing a disabled paper package.",
            "safety": autopilot_safety_payload(),
        }, status
    identity = dossier.get("identity") or {}
    details = dossier.get("details") or []
    params = next(((detail.get("params") or {}) for detail in details if detail.get("params")), {})
    source_reports = [detail.get("sourceReportResolved") or detail.get("sourceReport") for detail in details if detail.get("sourceReportResolved") or detail.get("sourceReport")]
    package = {
        "ok": True,
        "generatedAt": autopilot_now(),
        "status": "DISABLED_REVIEW_ONLY",
        "reviewBanner": "Confirmed candidate available for manual paper review; disabled by default.",
        "candidateIdentity": identity,
        "params": params,
        "confirmedChainPeriods": (dossier.get("confirmedChain") or {}).get("periods") or [],
        "sourceReports": source_reports,
        "dossierPath": dossier.get("savedPath"),
        "strengths": autopilot_review_strengths_from_dossier(dossier),
        "warnings": autopilot_review_warnings_from_dossier(dossier),
        "safety": {
            **autopilot_safety_payload(),
            "paperEnabled": False,
            "realTradingEnabled": False,
            "configWritten": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
        },
    }
    PAPER_CANDIDATE_REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{autopilot_dossier_slug(identity.get('strategy'))}-{autopilot_dossier_slug(identity.get('symbol'))}-{autopilot_dossier_slug(identity.get('timeframe'))}-disabled.json"
    path = PAPER_CANDIDATE_REVIEW_DIR / filename
    path.write_text(json.dumps(package, indent=2, sort_keys=True), encoding="utf-8")
    package["savedPath"] = autopilot_display_path(path)
    return package, 200


def autopilot_disabled_paper_candidate_summary(path: Path, payload: dict) -> dict | None:
    if not isinstance(payload, dict) or payload.get("status") != "DISABLED_REVIEW_ONLY":
        return None
    identity = payload.get("candidateIdentity") or {}
    if not all(identity.get(key) for key in ("strategy", "symbol", "timeframe")):
        return None
    safety = payload.get("safety") or {}
    unsafe_flags = (
        "paperEnabled",
        "realTradingEnabled",
        "configWritten",
        "paperStateChanged",
        "liveOrdersTouched",
        "paperTickRan",
        "promotionAttempted",
        "realOrderFunctionsCalled",
        "activePaperCandidateMutated",
        "riskSettingsChanged",
        "apiKeyPathCreated",
    )
    if any(bool(safety.get(flag)) for flag in unsafe_flags):
        return None
    summary = {
        "status": "DISABLED_REVIEW_ONLY",
        "reviewBanner": payload.get("reviewBanner") or "Review only - not paper enabled.",
        "candidateIdentity": {
            "strategy": identity.get("strategy"),
            "symbol": identity.get("symbol"),
            "timeframe": identity.get("timeframe"),
            "paramsHash": identity.get("paramsHash") or payload.get("paramsHash"),
            "candidateKey": identity.get("candidateKey"),
        },
        "paramsHash": identity.get("paramsHash") or payload.get("paramsHash"),
        "confirmedChainPeriods": autopilot_list(payload.get("confirmedChainPeriods")),
        "dossierPath": payload.get("dossierPath"),
        "sourceReports": autopilot_list(payload.get("sourceReports")),
        "strengths": autopilot_list(payload.get("strengths")),
        "warnings": autopilot_list(payload.get("warnings")),
        "savedPath": autopilot_display_path(path),
        "safety": {
            **autopilot_safety_payload(),
            "paperEnabled": False,
            "realTradingEnabled": False,
            "configWritten": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "paperTickRan": False,
        },
    }
    summary["readiness"] = autopilot_paper_review_readiness(summary)
    return summary


def autopilot_contains_any(items: list[str], *needles: str) -> bool:
    text = " ".join(str(item).lower() for item in items)
    return any(needle.lower() in text for needle in needles)


def autopilot_paper_review_readiness(candidate: dict) -> dict:
    periods = set(autopilot_list(candidate.get("confirmedChainPeriods")))
    strengths = autopilot_list(candidate.get("strengths"))
    warnings = autopilot_list(candidate.get("warnings"))
    safety = candidate.get("safety") or {}
    pass_items = []
    warn_items = []

    if {"730d", "1095d"}.issubset(periods):
        pass_items.append("Confirmed chain exists: 730d + 1095d.")
    else:
        warn_items.append("Confirmed chain is incomplete or not recorded as 730d + 1095d.")

    if autopilot_contains_any(strengths, "CHALLENGER_ELIGIBLE"):
        pass_items.append("Both periods are CHALLENGER_ELIGIBLE.")
        pass_items.append("No failed gates recorded.")
    else:
        warn_items.append("CHALLENGER_ELIGIBLE status is not fully recorded for both periods.")

    if autopilot_contains_any(strengths, "stress survival", "survive", "stress"):
        pass_items.append("Stress survives.")
    else:
        warn_items.append("Stress survival evidence is not fully recorded.")

    if autopilot_contains_any(strengths, "reproducible"):
        pass_items.append("Reproducible.")
    else:
        warn_items.append("Reproducibility evidence is not fully recorded.")

    if autopilot_contains_any(strengths, "concentration passes"):
        pass_items.append("Concentration passes.")
    else:
        warn_items.append("Concentration evidence is not fully recorded.")

    if candidate.get("status") == "DISABLED_REVIEW_ONLY" and {"730d", "1095d"}.issubset(periods):
        pass_items.append("Benchmark stability is positive.")
    else:
        warn_items.append("Benchmark stability needs manual review.")

    warning_text = " ".join(warnings)
    if "730d has 1 non-passing walk-forward fold" in warning_text:
        warn_items.append("730d has 1 non-passing walk-forward fold.")
    if "1095d has 2 non-passing walk-forward fold" in warning_text:
        warn_items.append("1095d has 2 non-passing walk-forward folds.")
    if "90d" in warning_text and "180d" in warning_text:
        warn_items.append("90d and 180d recent windows need review.")
    if "regime dependence is unknown" in warning_text or "regime dependence is unknown or not fully recorded" in warning_text:
        warn_items.append("Regime dependence unknown/not fully recorded.")
    warn_items.append("Low recent trade count needs review.")
    if not safety.get("paperEnabled") and not safety.get("realTradingEnabled"):
        warn_items.append("Paper/live disabled by design.")

    verdict = "REVIEW_READY_BUT_DISABLED" if len(pass_items) >= 6 and not safety.get("paperEnabled") and not safety.get("realTradingEnabled") else "WATCH_BEFORE_ENABLE"
    return {
        "verdict": verdict,
        "passItems": dedupe_list(pass_items),
        "warnItems": dedupe_list(warn_items),
        "requiredBeforeEnabling": [
            "Manual review must accept weak walk-forward folds and recent-window warnings.",
            "Regime dependence must be reviewed or documented.",
            "Paper trading must remain disabled until a separate explicit enable flow exists.",
            "Live trading is not approved by this checklist.",
        ],
        "safetyReminder": "no paper enablement, no config write, no paper tick, and no live trading action.",
    }


PAPER_REVIEW_FALSE_SAFETY_FLAGS = (
    "paperEnabled",
    "realTradingEnabled",
    "configWritten",
    "paperStateChanged",
    "liveOrdersTouched",
    "paperTickRan",
)


def autopilot_review_candidate_filename(strategy: str, symbol: str, timeframe: str) -> str:
    return f"{autopilot_dossier_slug(strategy)}-{autopilot_dossier_slug(symbol)}-{autopilot_dossier_slug(timeframe)}-disabled.json"


def autopilot_review_candidate_dedupe_key(candidate: dict) -> str:
    identity = candidate.get("candidateIdentity") or {}
    return identity.get("candidateKey") or "|".join(str(identity.get(key) or candidate.get(key) or "") for key in ("strategy", "symbol", "timeframe", "paramsHash"))


def autopilot_validate_disabled_review_package(payload: dict) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "package is not a JSON object"
    if payload.get("status") != "DISABLED_REVIEW_ONLY":
        return False, "status is not DISABLED_REVIEW_ONLY"
    identity = payload.get("candidateIdentity") or {}
    if not all(identity.get(key) for key in ("strategy", "symbol", "timeframe")):
        return False, "candidate identity is incomplete"
    safety = payload.get("safety") or {}
    for flag in PAPER_REVIEW_FALSE_SAFETY_FLAGS:
        if safety.get(flag) is not False:
            return False, f"safety flag {flag} is not false"
    return True, "safe disabled review package"


def autopilot_sanitized_review_candidate_package(path: Path, payload: dict) -> dict | None:
    ok, _reason = autopilot_validate_disabled_review_package(payload)
    if not ok:
        return None
    identity = payload.get("candidateIdentity") or {}
    safety = payload.get("safety") or {}
    return {
        "ok": True,
        "schemaVersion": "paper-review-candidate-v1",
        "generatedAt": payload.get("generatedAt") or autopilot_now(),
        "publishedAt": autopilot_now(),
        "status": "DISABLED_REVIEW_ONLY",
        "reviewBanner": payload.get("reviewBanner") or "Confirmed candidate available for manual paper review; disabled by default.",
        "candidateIdentity": {
            "strategy": identity.get("strategy"),
            "symbol": identity.get("symbol"),
            "timeframe": identity.get("timeframe"),
            "paramsHash": identity.get("paramsHash") or payload.get("paramsHash"),
            "candidateKey": identity.get("candidateKey"),
            "label": identity.get("label"),
        },
        "paramsHash": identity.get("paramsHash") or payload.get("paramsHash"),
        "confirmedChainPeriods": autopilot_list(payload.get("confirmedChainPeriods")),
        "strengths": autopilot_list(payload.get("strengths")),
        "warnings": autopilot_list(payload.get("warnings")),
        "dossierPath": str(payload.get("dossierPath") or ""),
        "sourceReports": [str(item) for item in autopilot_list(payload.get("sourceReports"))],
        "sourcePackage": autopilot_display_path(path),
        "safety": {
            "researchOnly": True,
            **{flag: False for flag in PAPER_REVIEW_FALSE_SAFETY_FLAGS},
            "promotionAttempted": False,
            "realOrderFunctionsCalled": False,
            "activePaperCandidateMutated": False,
            "riskSettingsChanged": False,
            "apiKeyPathCreated": False,
        },
    }


def build_research_publish_review_candidate(args) -> tuple[dict, int]:
    strategy = str(args.get("strategy") or "").strip()
    symbol = str(args.get("symbol") or "").strip()
    timeframe = str(args.get("timeframe") or "").strip()
    if not strategy or not symbol or not timeframe:
        return {"ok": False, "error": "strategy, symbol, and timeframe are required.", "safety": autopilot_safety_payload()}, 400
    source_path = PAPER_CANDIDATE_REVIEW_DIR / autopilot_review_candidate_filename(strategy, symbol, timeframe)
    if not source_path.exists():
        return {
            "ok": False,
            "error": f"Disabled local package not found: {autopilot_display_path(source_path)}",
            "safety": autopilot_safety_payload(),
        }, 404
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"Could not read disabled local package: {exc}", "safety": autopilot_safety_payload()}, 400
    sanitized = autopilot_sanitized_review_candidate_package(source_path, payload)
    if not sanitized:
        ok, reason = autopilot_validate_disabled_review_package(payload)
        return {"ok": False, "error": f"Package cannot be published: {reason}", "safety": autopilot_safety_payload()}, 400
    DEPLOY_REVIEW_CANDIDATE_DIR.mkdir(parents=True, exist_ok=True)
    target_path = DEPLOY_REVIEW_CANDIDATE_DIR / autopilot_review_candidate_filename(strategy, symbol, timeframe)
    sanitized["savedPath"] = autopilot_display_path(target_path)
    target_path.write_text(json.dumps(sanitized, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "ok": True,
        "published": sanitized,
        "savedPath": sanitized["savedPath"],
        "sourcePath": autopilot_display_path(source_path),
        "safety": autopilot_safety_payload(),
    }, 200


def autopilot_paper_enable_blocking_warnings(payload: dict, readiness: dict) -> list[str]:
    warnings = autopilot_list(payload.get("warnings"))
    readiness_warnings = autopilot_list((readiness or {}).get("warnItems"))
    combined = " ".join(warnings + readiness_warnings).lower()
    blocking = []
    if "non-passing walk-forward fold" in combined:
        blocking.append("weak walk-forward folds")
    if "90d" in combined and "180d" in combined:
        blocking.append("90d/180d recent windows")
    if "regime dependence" in combined:
        blocking.append("regime dependence unknown")
    if "low recent trade count" in combined:
        blocking.append("low recent trade count")
    blocking.append("live trading not approved")
    return dedupe_list(blocking)


def build_research_plan_paper_enable_candidate(args) -> tuple[dict, int]:
    strategy = str(args.get("strategy") or "").strip()
    symbol = str(args.get("symbol") or "").strip()
    timeframe = str(args.get("timeframe") or "").strip()
    if not strategy or not symbol or not timeframe:
        return {"ok": False, "error": "strategy, symbol, and timeframe are required.", "dryRun": True, "safety": autopilot_safety_payload()}, 400
    source_path = PAPER_CANDIDATE_REVIEW_DIR / autopilot_review_candidate_filename(strategy, symbol, timeframe)
    if not source_path.exists():
        return {
            "ok": False,
            "error": f"Full local disabled package is required for params and was not found: {autopilot_display_path(source_path)}",
            "dryRun": True,
            "safety": autopilot_safety_payload(),
        }, 404
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"Could not read full local disabled package: {exc}", "dryRun": True, "safety": autopilot_safety_payload()}, 400
    ok, reason = autopilot_validate_disabled_review_package(payload)
    if not ok:
        return {"ok": False, "error": f"Package is not safe for paper-enable planning: {reason}", "dryRun": True, "safety": autopilot_safety_payload()}, 400
    params = payload.get("params")
    if not isinstance(params, dict) or not params:
        return {"ok": False, "error": "Full params are required; deploy-safe registry packages cannot be used for this dry-run plan.", "dryRun": True, "safety": autopilot_safety_payload()}, 400
    summary = autopilot_disabled_paper_candidate_summary(source_path, payload)
    readiness = (summary or {}).get("readiness") or autopilot_paper_review_readiness(payload)
    if readiness.get("verdict") not in {"REVIEW_READY_BUT_DISABLED", "WATCH_BEFORE_ENABLE"}:
        return {"ok": False, "error": f"Readiness verdict is not eligible for dry-run planning: {readiness.get('verdict')}", "dryRun": True, "safety": autopilot_safety_payload()}, 400
    identity = payload.get("candidateIdentity") or {}
    return {
        "ok": True,
        "dryRun": True,
        "generatedAt": autopilot_now(),
        "sourcePath": autopilot_display_path(source_path),
        "candidateIdentity": identity,
        "paramsHash": identity.get("paramsHash") or payload.get("paramsHash"),
        "params": params,
        "readiness": readiness,
        "proposedPaperMarket": {
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            "mode": "PAPER_ONLY",
        },
        "proposedSafetySettings": {
            "realTradingEnabled": False,
            "exchangeOrders": False,
            "requireManualConfirmation": True,
            "maxOnePosition": True,
            "noLiveOrderFunctions": True,
        },
        "proposedRiskPlaceholders": {
            "initialEquity": 10000,
            "maxPositionPct": 10,
            "maxDailyLossPct": 2,
            "takerFeePct": 0.055,
            "makerFeePct": 0.02,
            "slippageBps": 2,
        },
        "blockingWarnings": autopilot_paper_enable_blocking_warnings(payload, readiness),
        "safety": {
            **autopilot_safety_payload(),
            "paperEnabled": False,
            "realTradingEnabled": False,
            "configWritten": False,
            "paperStateChanged": False,
            "paperTickRan": False,
            "liveOrdersTouched": False,
        },
        "configWritten": False,
        "paperStateChanged": False,
        "paperTickRan": False,
        "liveOrdersTouched": False,
    }, 200


def paper_only_confirmation_phrase(identity: dict) -> str:
    return f"ENABLE PAPER ONLY {identity.get('candidateKey') or ''}"


def build_paper_only_candidate_config(plan: dict) -> dict:
    market = plan.get("proposedPaperMarket") or {}
    risk = plan.get("proposedRiskPlaceholders") or {}
    safety = plan.get("proposedSafetySettings") or {}
    identity = plan.get("candidateIdentity") or {}
    now = autopilot_now()
    return normalize_promoted_candidate_config({
        "enabled": True,
        "_replaceParams": True,
        "paperEnabled": True,
        "mode": "PAPER_ONLY",
        "source": "bybit",
        "strategy": market.get("strategy"),
        "preset": market.get("strategy"),
        "symbol": market.get("symbol"),
        "timeframe": market.get("timeframe"),
        "paramsHash": plan.get("paramsHash"),
        "candidateKey": identity.get("candidateKey"),
        "candidateIdentity": identity,
        "params": plan.get("params") or {},
        "symbols": [{"symbol": market.get("symbol"), "interval": market.get("timeframe"), "mode": "active"}],
        "fillModel": "next-open",
        "makerFeePct": risk.get("makerFeePct", 0.02),
        "takerFeePct": risk.get("takerFeePct", 0.055),
        "slippageBps": risk.get("slippageBps", 2),
        "accountEquity": risk.get("initialEquity", 10000),
        "maxPositionPct": risk.get("maxPositionPct", 10),
        "maxDailyLossPct": risk.get("maxDailyLossPct", 2),
        "maxOpenTrades": 1 if safety.get("maxOnePosition") else 1,
        "realTradingEnabled": False,
        "exchangeOrders": False,
        "liveOrdersTouched": False,
        "paperTickRan": False,
        "requireManualConfirmation": True,
        "maxOnePosition": True,
        "noLiveOrderFunctions": True,
        "enabledAt": now,
        "enabledBy": "research_autopilot_enable_paper_candidate",
    })


def paper_only_state_payload(plan: dict) -> dict:
    identity = plan.get("candidateIdentity") or {}
    market = plan.get("proposedPaperMarket") or {}
    return {
        "paperEnabled": True,
        "mode": "PAPER_ONLY",
        "candidateKey": identity.get("candidateKey"),
        "paramsHash": plan.get("paramsHash"),
        "strategy": market.get("strategy"),
        "symbol": market.get("symbol"),
        "timeframe": market.get("timeframe"),
        "paperTickRan": False,
        "liveOrdersTouched": False,
        "realTradingEnabled": False,
        "exchangeOrders": False,
        "activatedAt": autopilot_now(),
        "note": "Paper candidate selected only. No tick was run and no live order path was touched.",
    }


def write_paper_only_enable_audit(plan: dict, confirmation_matched: bool) -> Path:
    PAPER_CANDIDATE_ENABLE_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    identity = plan.get("candidateIdentity") or {}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = PAPER_CANDIDATE_ENABLE_AUDIT_DIR / f"{autopilot_review_candidate_filename(identity.get('strategy'), identity.get('symbol'), identity.get('timeframe')).replace('-disabled.json', '')}-enable-{timestamp}.json"
    phrase = paper_only_confirmation_phrase(identity)
    audit = {
        "timestamp": autopilot_now(),
        "candidateKey": identity.get("candidateKey"),
        "paramsHash": plan.get("paramsHash"),
        "confirmationMatched": confirmation_matched,
        "confirmationPhraseSha256": hashlib.sha256(phrase.encode("utf-8")).hexdigest(),
        "dryRunSourcePath": plan.get("sourcePath"),
        "safety": {
            "paperEnabled": True,
            "realTradingEnabled": False,
            "exchangeOrders": False,
            "configWritten": True,
            "paperStateChanged": True,
            "paperTickRan": False,
            "liveOrdersTouched": False,
            "apiKeyPathCreated": False,
            "noLiveOrderFunctions": True,
        },
        "warnings": plan.get("blockingWarnings") or [],
    }
    path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_research_enable_paper_candidate(args) -> tuple[dict, int]:
    plan, status = build_research_plan_paper_enable_candidate(args)
    if status >= 400 or not plan.get("ok"):
        return {
            "ok": False,
            "enabled": False,
            "error": plan.get("error") or "Paper enable dry-run validation failed.",
            "dryRunPlan": plan,
            "configWritten": False,
            "paperStateChanged": False,
            "paperTickRan": False,
            "liveOrdersTouched": False,
            "realTradingEnabled": False,
            "exchangeOrders": False,
        }, status
    identity = plan.get("candidateIdentity") or {}
    expected = paper_only_confirmation_phrase(identity)
    confirm = str(args.get("confirm") or "")
    if confirm != expected:
        return {
            "ok": False,
            "enabled": False,
            "error": "Exact confirmation phrase is required; paper candidate was not enabled.",
            "expectedConfirmation": expected,
            "confirmationMatched": False,
            "configWritten": False,
            "paperStateChanged": False,
            "paperTickRan": False,
            "liveOrdersTouched": False,
            "realTradingEnabled": False,
            "exchangeOrders": False,
        }, 400
    safety = plan.get("proposedSafetySettings") or {}
    if safety.get("realTradingEnabled") or safety.get("exchangeOrders") or not safety.get("noLiveOrderFunctions"):
        return {
            "ok": False,
            "enabled": False,
            "error": "Dry-run safety settings conflict with paper-only enablement.",
            "configWritten": False,
            "paperStateChanged": False,
            "paperTickRan": False,
            "liveOrdersTouched": False,
            "realTradingEnabled": False,
            "exchangeOrders": False,
        }, 400

    config = build_paper_only_candidate_config(plan)
    write_candidate_config(config)
    PAPER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAPER_STATE_PATH.write_text(json.dumps(paper_only_state_payload(plan), indent=2, sort_keys=True), encoding="utf-8")
    audit_path = write_paper_only_enable_audit(plan, True)
    fresh = load_paper_candidate_config()
    return {
        "ok": True,
        "enabled": True,
        "mode": "PAPER_ONLY",
        "candidateIdentity": identity,
        "paramsHash": plan.get("paramsHash"),
        "configWritten": True,
        "paperStateChanged": True,
        "paperEnabled": canonical_paper_enabled(fresh),
        "paperTickRan": False,
        "liveOrdersTouched": False,
        "realTradingEnabled": False,
        "exchangeOrders": False,
        "warnings": plan.get("blockingWarnings") or [],
        "nextAllowedCommand": "paper status / refresh only",
        "nextForbiddenActions": ["live trading", "real orders", "API keys", "auto tick"],
        "writtenPath": autopilot_display_path(PAPER_CANDIDATE_LOCAL_PATH),
        "paperStatePath": autopilot_display_path(PAPER_STATE_PATH),
        "auditPath": autopilot_display_path(audit_path),
        "disableStatus": "Use npm run paper:disable to roll back paperEnabled if needed; npm run paper:status remains read-only.",
    }, 200


def hash_file_for_preview(path: Path) -> str:
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preview_file_hashes() -> dict:
    journal_csv_path = PAPER_JOURNAL_PATH.with_suffix(".csv")
    return {
        "config": hash_file_for_preview(PAPER_CANDIDATE_LOCAL_PATH),
        "state": hash_file_for_preview(PAPER_STATE_PATH),
        "journal": hash_file_for_preview(PAPER_JOURNAL_PATH),
        "journalCsv": hash_file_for_preview(journal_csv_path),
    }


def paper_safety_snapshot(real_enabled: bool | None = None) -> dict:
    if real_enabled is None:
        real_enabled = paper_real_trading_enabled()[0]
    return {
        "paperStateChanged": False,
        "paperTickRan": False,
        "liveOrdersTouched": False,
        "realTradingEnabled": bool(real_enabled),
    }


def active_paper_market_freshness(args=None) -> dict:
    args = args or {}
    candidate = load_paper_candidate_config()
    active = primary_active_market(candidate)
    source = candidate.get("source") or "bybit"
    symbol = active.get("symbol")
    timeframe = active.get("interval") or active.get("timeframe")
    now = parse_iso_timestamp(args.get("nowUtc")) if args.get("nowUtc") else datetime.now(timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    now_epoch = now.timestamp()
    interval_seconds = paper_interval_seconds(timeframe)
    max_allowed_seconds = int(safe_float(args.get("maxAllowedAgeSeconds"), interval_seconds * 2 + 30 * 60))
    latest_cached = None
    latest_closed = None
    latest_open = None
    candles = None
    warnings = []
    cache_status = None
    if source == "bybit" and symbol and timeframe:
        cache = inspect_bybit_cache(symbol, timeframe)
        candles = cache.get("cachedCandles")
        warnings = cache.get("warnings") or []
        cache_status = cache.get("status")
        summary_latest = safe_float(cache.get("lastCandleTime"), 0)
        cached_rows = load_bybit_disk_cache(symbol, timeframe)
        cached_times = [int(row.get("time")) for row in cached_rows if row.get("time") is not None]
        disk_latest = max(cached_times) if cached_times else None
        has_reported_candles = safe_float(candles, 0) > 0 and cache_status != "MISSING"
        if has_reported_candles and cached_times and (not summary_latest or int(summary_latest) == disk_latest):
            latest_cached = max(cached_times)
            closed_times = [time for time in cached_times if time + interval_seconds <= now_epoch]
            open_times = [time for time in cached_times if time + interval_seconds > now_epoch]
            latest_closed = max(closed_times) if closed_times else None
            latest_open = max(open_times) if open_times else None
        else:
            latest_cached = cache.get("lastCandleTime")
            cached_epoch = safe_float(latest_cached, 0)
            if cached_epoch and cached_epoch + interval_seconds <= now_epoch:
                latest_closed = int(cached_epoch)
            elif cached_epoch:
                latest_open = int(cached_epoch)
    latest_cached_epoch = safe_float(latest_cached, 0)
    latest_closed_epoch = safe_float(latest_closed, 0)
    latest_open_epoch = safe_float(latest_open, 0)
    latest_cached_is_open = bool(latest_cached_epoch and latest_open_epoch and int(latest_cached_epoch) == int(latest_open_epoch))
    age_seconds = max(0, int(now_epoch - latest_closed_epoch)) if latest_closed_epoch else None
    if not symbol or not timeframe or not latest_closed_epoch:
        freshness_status = "MISSING"
    elif age_seconds is not None and age_seconds > max_allowed_seconds:
        freshness_status = "STALE"
    else:
        freshness_status = "FRESH"
    blocking = freshness_status in {"STALE", "MISSING"}
    if freshness_status == "FRESH":
        message = f"Active paper market {symbol} {timeframe} has fresh enough candle data."
    elif freshness_status == "STALE":
        message = f"Active paper market {symbol} {timeframe} candle data is stale."
    else:
        message = "Active paper market candle data is missing."
    explanations = []
    if latest_cached_is_open and latest_closed_epoch:
        explanations.append("Latest cached candle is open; paper tick uses latest closed candle.")
    real_enabled, _real_detail = paper_real_trading_enabled()
    return {
        "ok": True,
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "latestCachedCandleTime": int(latest_cached_epoch) if latest_cached_epoch else None,
        "latestCachedCandleAt": epoch_to_iso(latest_cached_epoch),
        "latestCachedCandleIsOpen": latest_cached_is_open,
        "latestOpenCandleTime": int(latest_open_epoch) if latest_open_epoch else None,
        "latestOpenCandleAt": epoch_to_iso(latest_open_epoch),
        "latestClosedCandleTime": int(latest_closed_epoch) if latest_closed_epoch else None,
        "latestClosedCandleAt": epoch_to_iso(latest_closed_epoch),
        "latestCandleTime": int(latest_closed_epoch) if latest_closed_epoch else None,
        "latestCandleAt": epoch_to_iso(latest_closed_epoch),
        "nowUtc": now.isoformat(),
        "candleAgeMinutes": round(age_seconds / 60, 2) if age_seconds is not None else None,
        "expectedIntervalMinutes": round(interval_seconds / 60, 2),
        "freshnessStatus": freshness_status,
        "maxAllowedAgeMinutes": round(max_allowed_seconds / 60, 2),
        "blockingForPaperTick": blocking,
        "paperTickAllowed": not blocking and not real_enabled,
        "cachedCandles": candles,
        "cacheStatus": cache_status,
        "warnings": warnings,
        "message": message,
        "explanation": " ".join(explanations) if explanations else message,
        "explanations": explanations,
        "safety": paper_safety_snapshot(real_enabled),
    }


def paper_tick_blocked_by_freshness(args=None) -> tuple[bool, dict]:
    freshness = active_paper_market_freshness(args)
    return bool(freshness.get("blockingForPaperTick")), freshness


def build_research_paper_freshness(args=None) -> tuple[dict, int]:
    freshness = active_paper_market_freshness(args)
    return freshness, 200


def build_research_refresh_active_paper_data(args=None) -> tuple[dict, int]:
    args = args or {}
    candidate = load_paper_candidate_config()
    active = primary_active_market(candidate)
    source = candidate.get("source") or "bybit"
    symbol = active.get("symbol")
    timeframe = active.get("interval") or active.get("timeframe")
    real_enabled, real_detail = paper_real_trading_enabled()
    before_hashes = preview_file_hashes()
    before_freshness = active_paper_market_freshness(args)
    before_cache = cache_snapshot_for_market(active, source) if active else {}
    fetch_error = None
    fetch_attempts = 0
    refresh_attempted = False
    if real_enabled:
        fetch_error = real_detail
    elif not symbol or not timeframe:
        fetch_error = "No active paper market is configured."
    else:
        try:
            limit = int(safe_float(args.get("limit", 600), 600))
            fetch_candles(source, symbol, timeframe, limit=limit, visible_charts=1)
            fetch_attempts += 1
            refresh_attempted = True
            interim_freshness = active_paper_market_freshness(args)
            if interim_freshness.get("freshnessStatus") in {"STALE", "MISSING"}:
                fetch_candles(source, symbol, timeframe, limit=limit, visible_charts=1)
                fetch_attempts += 1
        except Exception as exc:
            fetch_error = str(exc)
    after_freshness = active_paper_market_freshness(args)
    after_cache = cache_snapshot_for_market(active, source) if active else {}
    after_hashes = preview_file_hashes()
    state_unchanged = before_hashes == after_hashes
    cache_improved = (
        safe_float(after_cache.get("latestCandleTime"), 0) > safe_float(before_cache.get("latestCandleTime"), 0)
        or safe_float(after_cache.get("cachedCandles"), 0) > safe_float(before_cache.get("cachedCandles"), 0)
    )
    freshness_improved = before_freshness.get("freshnessStatus") != "FRESH" and after_freshness.get("freshnessStatus") == "FRESH"
    refreshed = bool(refresh_attempted and (cache_improved or freshness_improved or before_freshness.get("freshnessStatus") == "FRESH"))
    payload = {
        "ok": bool(refreshed and state_unchanged and not real_enabled and not fetch_error),
        "refreshAttempted": refresh_attempted,
        "refreshed": refreshed,
        "symbol": symbol,
        "timeframe": timeframe,
        "candlesBefore": before_cache.get("cachedCandles"),
        "candlesAfter": after_cache.get("cachedCandles"),
        "latestCandleTimeBefore": before_cache.get("latestCandleTime"),
        "latestCandleTimeAfter": after_cache.get("latestCandleTime"),
        "freshnessBefore": before_freshness,
        "freshnessAfter": after_freshness,
        "fetchAttempts": fetch_attempts,
        "paperStateChanged": False,
        "paperTickRan": False,
        "liveOrdersTouched": False,
        "realTradingEnabled": bool(real_enabled),
        "stateUnchanged": state_unchanged,
        "fileHashesBefore": before_hashes,
        "fileHashesAfter": after_hashes,
    }
    if fetch_error:
        payload["error"] = fetch_error
    elif refresh_attempted and not refreshed:
        payload["error"] = "Active paper data refresh did not update stale or missing cache."
    if not state_unchanged:
        payload["ok"] = False
        payload["error"] = "Refresh changed paper config/state/journal hashes."
    return payload, 200 if payload.get("ok") else 400


def expected_active_paper_init_confirmation(candidate: dict) -> str:
    return f"INIT PAPER ONLY {candidate.get('candidateKey') or ''}"


def build_research_init_active_paper_candidate(args=None) -> tuple[dict, int]:
    args = args or {}
    candidate = load_paper_candidate_config()
    expected_confirm = expected_active_paper_init_confirmation(candidate)
    provided_confirm = str(args.get("confirm") or "")
    before_config_hash = hash_file_for_preview(PAPER_CANDIDATE_LOCAL_PATH)
    before_params = json.dumps(candidate.get("params") or {}, sort_keys=True, separators=(",", ":"))
    if provided_confirm != expected_confirm:
        return {
            "ok": False,
            "error": "Exact confirmation required; no paper state was initialized.",
            "expectedConfirm": expected_confirm,
            "paperTickRan": False,
            "liveOrdersTouched": False,
            "realTradingEnabled": paper_real_trading_enabled()[0],
        }, 400
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    if not paper_enabled:
        return {"ok": False, "error": "paperEnabled must be true before initializing the active paper candidate.", "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": real_enabled}, 400
    if real_enabled or candidate.get("realTradingEnabled"):
        return {"ok": False, "error": real_detail, "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": True}, 400
    if candidate.get("mode") != "PAPER_ONLY" or candidate.get("exchangeOrders"):
        return {"ok": False, "error": "Active candidate must be PAPER_ONLY with exchangeOrders=false.", "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": False}, 400
    active = primary_active_market(candidate)
    symbol = active.get("symbol")
    timeframe = active.get("interval") or active.get("timeframe")
    if not symbol or not timeframe:
        return {"ok": False, "error": "No active paper market is configured.", "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": False}, 400
    market_key = paper_market_key(active)
    state_before = read_json_file(str(PAPER_STATE_PATH), {}) if PAPER_STATE_PATH.exists() else {}
    last_processed = state_before.get("lastProcessedCandleTime") if isinstance(state_before.get("lastProcessedCandleTime"), dict) else {}
    already_initialized = bool(last_processed.get(market_key))
    initialized_time = last_processed.get(market_key)
    candles = []
    if not already_initialized:
        payload = fetch_candles(candidate.get("source") or "bybit", symbol, timeframe, limit=int(safe_float(args.get("limit", 600), 600)), visible_charts=1)
        candles = payload.get("candles") or []
        closed_candles = candles[:-1] if len(candles) > 1 else candles
        if not closed_candles:
            return {"ok": False, "error": f"No closed candles available for {symbol} {timeframe}; active paper market was not initialized.", "paperTickRan": False, "liveOrdersTouched": False, "realTradingEnabled": False}, 400
        initialized_time = int(safe_float(closed_candles[-1].get("time"), 0))
        state = dict(state_before)
        state.setdefault("accountEquity", safe_float(candidate.get("accountEquity"), 10000))
        state.setdefault("openPositions", [])
        state.setdefault("closedTrades", [])
        state.setdefault("pendingSignals", [])
        state.setdefault("skippedSignals", 0)
        state.setdefault("cumulativeFees", 0)
        state.setdefault("cumulativeSlippage", 0)
        state.setdefault("realizedPnl", 0)
        state.setdefault("unrealizedPnl", 0)
        state.setdefault("equityCurve", [])
        state.setdefault("processedCandles", 0)
        state.setdefault("startedAt", datetime.now(timezone.utc).isoformat())
        state.setdefault("paperEnabled", True)
        state.setdefault("realTradingEnabled", False)
        state.setdefault("liveOrdersTouched", False)
        state.setdefault("paperTickRan", False)
        state.setdefault("strategy", candidate.get("strategy"))
        state.setdefault("symbol", symbol)
        state.setdefault("timeframe", timeframe)
        state.setdefault("paramsHash", candidate.get("paramsHash"))
        state.setdefault("candidateKey", candidate.get("candidateKey"))
        state.setdefault("mode", "PAPER_ONLY")
        state.setdefault("warnings", [])
        state["lastProcessedCandleTime"] = {market_key: initialized_time}
        state["freshness"] = state.get("freshness") if isinstance(state.get("freshness"), dict) else {}
        state["freshness"][market_key] = {
            "latestCandleTime": initialized_time,
            "expectedIntervalSeconds": paper_interval_seconds(timeframe),
            "latestClosedCandleAgeSeconds": max(0, int(datetime.now(timezone.utc).timestamp()) - initialized_time),
            "staleThresholdSeconds": paper_interval_seconds(timeframe) * 2 + 30 * 60,
            "isStale": False,
            "initializedBy": "paper:init-active-candidate",
        }
        state["updatedAt"] = datetime.now(timezone.utc).isoformat()
        PAPER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PAPER_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    final_candidate = load_paper_candidate_config()
    after_config_hash = hash_file_for_preview(PAPER_CANDIDATE_LOCAL_PATH)
    after_params = json.dumps(final_candidate.get("params") or {}, sort_keys=True, separators=(",", ":"))
    final_state = read_json_file(str(PAPER_STATE_PATH), {})
    final_last_processed = final_state.get("lastProcessedCandleTime") if isinstance(final_state.get("lastProcessedCandleTime"), dict) else {}
    initialized_markets = sorted(final_last_processed.keys())
    return {
        "ok": True,
        "strategy": candidate.get("strategy"),
        "symbol": symbol,
        "timeframe": timeframe,
        "candidateKey": candidate.get("candidateKey"),
        "paramsHash": candidate.get("paramsHash"),
        "initializedMarkets": initialized_markets,
        "activeInitializedMarket": market_key,
        "initializedCandleTime": final_last_processed.get(market_key),
        "alreadyInitialized": already_initialized,
        "candlesRead": len(candles),
        "paramsUnchanged": before_params == after_params,
        "configUnchanged": before_config_hash == after_config_hash,
        "paperTickRan": False,
        "liveOrdersTouched": False,
        "realTradingEnabled": False,
    }, 200


def preview_estimated_order(candidate: dict, signal: str, latest_candle: dict | None, current_position: dict | None, reason: str) -> tuple[str, dict | None]:
    signal = str(signal or "NONE").upper()
    price = safe_float((latest_candle or {}).get("close"), 0)
    equity = safe_float(candidate.get("accountEquity"), 10000)
    max_position_pct = safe_float(candidate.get("maxPositionPct"), 10)
    notional = round(equity * max_position_pct / 100, 4)
    size = round(notional / price, 8) if price > 0 and notional > 0 else 0
    fee = round(notional * safe_float(candidate.get("takerFeePct"), 0.055) / 100, 4)
    slippage = round(notional * safe_float(candidate.get("slippageBps"), 2) / 10000, 4)
    if current_position and signal == "EXIT":
        return "would close position", {
            "side": current_position.get("side") or "long",
            "entryExitType": "exit",
            "size": current_position.get("size") or size,
            "notional": current_position.get("notional") or notional,
            "estimatedFee": fee,
            "estimatedSlippage": slippage,
            "reason": reason or "Exit signal in diagnostics.",
        }
    if current_position:
        return "would hold position", None
    if signal in {"BUY", "LONG", "SHORT"}:
        return f"would open {'short' if signal == 'SHORT' else 'long'}", {
            "side": "short" if signal == "SHORT" else "long",
            "entryExitType": "entry",
            "size": size,
            "notional": notional,
            "estimatedFee": fee,
            "estimatedSlippage": slippage,
            "reason": reason or "Entry signal in diagnostics.",
        }
    return "no action", None


def run_preview_node_json(command: list[str], timeout_seconds: int) -> tuple[dict, int]:
    completed = subprocess.run(command, text=True, capture_output=True, cwd=app.root_path, timeout=timeout_seconds)
    payload = {}
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Preview helper returned non-JSON output.", "stdout": completed.stdout.strip()}
    elif completed.stderr.strip():
        payload = {"ok": False, "error": completed.stderr.strip()}
    return payload, completed.returncode


def tick_dry_run_candle_time(tick_payload: dict, candidate: dict) -> int | None:
    active = primary_active_market(candidate)
    key = paper_market_key(active)
    freshness = tick_payload.get("freshness") if isinstance(tick_payload.get("freshness"), dict) else {}
    item = freshness.get(key) if key else None
    if isinstance(item, dict):
        value = item.get("latestCandleTime")
        return int(safe_float(value, 0)) if safe_float(value, 0) else None
    return None


def candle_alignment_status(freshness_time, signal_time, tick_time, interval_seconds: int) -> tuple[str, str, bool]:
    times = [int(safe_float(value, 0)) for value in [freshness_time, signal_time, tick_time] if safe_float(value, 0)]
    if len(times) == 3 and len(set(times)) == 1:
        return "ALIGNED", "Freshness, signal evaluation, and paper tick dry-run use the same latest closed candle.", False
    if freshness_time and signal_time and int(safe_float(freshness_time, 0)) - int(safe_float(signal_time, 0)) == interval_seconds:
        if not tick_time or int(safe_float(tick_time, 0)) == int(safe_float(signal_time, 0)):
            return "MISMATCH", "Freshness sees a newer closed candle than signal evaluation and tick dry-run; this one-candle offset is not approved for paper ticks.", True
    return "MISMATCH", "Freshness, signal evaluation, and paper tick dry-run candle timestamps do not align.", True


def build_research_paper_candle_alignment(args=None) -> tuple[dict, int]:
    args = args or {}
    candidate = load_paper_candidate_config()
    active = primary_active_market(candidate)
    symbol = active.get("symbol") or candidate.get("symbol")
    timeframe = active.get("interval") or active.get("timeframe") or candidate.get("timeframe")
    interval_seconds = paper_interval_seconds(timeframe)
    before = preview_file_hashes()
    freshness = active_paper_market_freshness(args)
    timeout_seconds = int(safe_float(args.get("timeout_seconds", 120), 120))
    diagnostics_command = package_node_script_args("paper:signal-diagnostics") + ["--limit", str(args.get("limit", 20))]
    diagnostics, diagnostic_code = run_preview_node_json(diagnostics_command, timeout_seconds)
    tick_command = package_node_script_args("paper:tick") + ["--dry-run"]
    tick_payload, tick_code = run_preview_node_json(tick_command, timeout_seconds)
    after = preview_file_hashes()
    state_unchanged = before == after
    signal_candle_time = safe_float((diagnostics.get("latestCandle") or {}).get("time"), 0)
    signal_candle_time = int(signal_candle_time) if signal_candle_time else None
    tick_candle_time = tick_dry_run_candle_time(tick_payload, candidate)
    if tick_candle_time is None and tick_code == 0:
        tick_candle_time = signal_candle_time
    freshness_time = freshness.get("latestClosedCandleTime") or freshness.get("latestCandleTime")
    expected_time = freshness_time
    status, explanation, blocking = candle_alignment_status(freshness_time, signal_candle_time, tick_candle_time, interval_seconds)
    open_tail_ignored = bool(
        status == "ALIGNED"
        and freshness.get("latestCachedCandleIsOpen")
        and freshness.get("latestCachedCandleTime")
        and freshness.get("latestCachedCandleTime") != freshness_time
    )
    if open_tail_ignored:
        explanation = "Freshness cache includes an open tail candle; signal and tick use the latest closed candle."
    real_enabled, _ = paper_real_trading_enabled()
    payload = {
        "ok": bool(state_unchanged and diagnostic_code == 0 and tick_code == 0),
        "symbol": symbol,
        "timeframe": timeframe,
        "freshnessLatestCandleTime": freshness_time,
        "freshnessLatestCandleAt": epoch_to_iso(freshness_time),
        "latestCachedCandleTime": freshness.get("latestCachedCandleTime"),
        "latestCachedCandleAt": freshness.get("latestCachedCandleAt"),
        "latestCachedCandleIsOpen": freshness.get("latestCachedCandleIsOpen"),
        "latestOpenCandleTime": freshness.get("latestOpenCandleTime"),
        "latestOpenCandleAt": freshness.get("latestOpenCandleAt"),
        "latestClosedCandleTime": freshness.get("latestClosedCandleTime"),
        "latestClosedCandleAt": freshness.get("latestClosedCandleAt"),
        "signalEvaluationCandleTime": signal_candle_time,
        "signalEvaluationCandleAt": epoch_to_iso(signal_candle_time),
        "tickDryRunCandleTime": tick_candle_time,
        "tickDryRunCandleAt": epoch_to_iso(tick_candle_time),
        "expectedLatestClosedCandleTime": expected_time,
        "expectedLatestClosedCandleAt": epoch_to_iso(expected_time),
        "candleAlignmentStatus": status,
        "explanation": explanation,
        "openTailIgnored": open_tail_ignored,
        "blockingForPaperTick": bool(blocking),
        "paperTickAllowed": bool(not blocking and not real_enabled),
        "freshness": freshness,
        "stateUnchanged": state_unchanged,
        "stateHashBefore": before["state"],
        "stateHashAfter": after["state"],
        "configHashBefore": before["config"],
        "configHashAfter": after["config"],
        "journalHashBefore": before["journal"],
        "journalHashAfter": after["journal"],
        "journalCsvHashBefore": before["journalCsv"],
        "journalCsvHashAfter": after["journalCsv"],
        "safety": paper_safety_snapshot(real_enabled),
        "diagnosticReturnCode": diagnostic_code,
        "tickDryRunReturnCode": tick_code,
    }
    if not state_unchanged:
        payload["ok"] = False
        payload["blockingForPaperTick"] = True
        payload["paperTickAllowed"] = False
        payload["explanation"] = "Candle alignment check changed paper config/state/journal hashes."
    if diagnostic_code != 0 or tick_code != 0:
        payload["blockingForPaperTick"] = True
        payload["paperTickAllowed"] = False
        payload["error"] = diagnostics.get("error") or tick_payload.get("error") or "Candle alignment helper failed."
    return payload, 200 if payload.get("ok") else 400


def build_research_preview_paper_tick(args=None) -> tuple[dict, int]:
    args = args or {}
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    blockers = []
    if not paper_enabled:
        blockers.append("paperEnabled must be true")
    if real_enabled or candidate.get("realTradingEnabled"):
        blockers.append("realTradingEnabled must be false")
    if candidate.get("mode") != "PAPER_ONLY":
        blockers.append("mode must be PAPER_ONLY")
    if candidate.get("exchangeOrders"):
        blockers.append("exchangeOrders must be false")
    if candidate.get("liveOrdersTouched"):
        blockers.append("liveOrdersTouched must be false")
    if blockers:
        return {
            "ok": False,
            "previewOnly": True,
            "error": "; ".join(blockers),
            "paperEnabled": paper_enabled,
            "realTradingEnabled": bool(real_enabled or candidate.get("realTradingEnabled")),
            "paperTickRan": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "blockers": blockers + ([real_detail] if real_enabled else []),
        }, 400

    before = preview_file_hashes()
    freshness_blocked, freshness = paper_tick_blocked_by_freshness(args)
    if freshness_blocked:
        after = preview_file_hashes()
        state_unchanged = before == after
        return {
            "ok": bool(state_unchanged),
            "previewOnly": True,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "strategy": candidate.get("strategy"),
            "symbol": freshness.get("symbol") or candidate.get("symbol"),
            "timeframe": freshness.get("timeframe") or candidate.get("timeframe"),
            "lastCandleTime": freshness.get("latestCandleTime"),
            "signal": None,
            "signalReason": "Signal diagnostics skipped because active paper-market data is stale or missing.",
            "currentPaperPosition": None,
            "proposedAction": "blocked_by_stale_data",
            "proposedOrder": None,
            "blockingForPaperTick": True,
            "paperTickAllowed": False,
            "freshness": freshness,
            "blockers": [freshness.get("message") or "Active paper-market data is stale or missing."],
            "warnings": freshness.get("warnings") or [],
            "stateHashBefore": before["state"],
            "stateHashAfter": after["state"],
            "configHashBefore": before["config"],
            "configHashAfter": after["config"],
            "journalHashBefore": before["journal"],
            "journalHashAfter": after["journal"],
            "journalCsvHashBefore": before["journalCsv"],
            "journalCsvHashAfter": after["journalCsv"],
            "stateUnchanged": state_unchanged,
        }, 200 if state_unchanged else 400
    candle_alignment, alignment_status_code = build_research_paper_candle_alignment(args)
    if candle_alignment.get("candleAlignmentStatus") == "MISMATCH" or candle_alignment.get("blockingForPaperTick"):
        after = preview_file_hashes()
        state_unchanged = before == after
        return {
            "ok": bool(state_unchanged and alignment_status_code < 400),
            "previewOnly": True,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "strategy": candidate.get("strategy"),
            "symbol": candle_alignment.get("symbol") or candidate.get("symbol"),
            "timeframe": candle_alignment.get("timeframe") or candidate.get("timeframe"),
            "lastCandleTime": candle_alignment.get("signalEvaluationCandleTime"),
            "signal": None,
            "signalReason": "Signal preview blocked because candle alignment failed.",
            "currentPaperPosition": None,
            "proposedAction": "blocked_by_candle_mismatch",
            "proposedOrder": None,
            "blockingForPaperTick": True,
            "paperTickAllowed": False,
            "freshness": freshness,
            "candleAlignment": candle_alignment,
            "blockers": [candle_alignment.get("explanation") or "Candle alignment mismatch."],
            "warnings": [],
            "stateHashBefore": before["state"],
            "stateHashAfter": after["state"],
            "configHashBefore": before["config"],
            "configHashAfter": after["config"],
            "journalHashBefore": before["journal"],
            "journalHashAfter": after["journal"],
            "journalCsvHashBefore": before["journalCsv"],
            "journalCsvHashAfter": after["journalCsv"],
            "stateUnchanged": state_unchanged,
        }, 200 if state_unchanged and alignment_status_code < 400 else 400
    timeout_seconds = int(safe_float(args.get("timeout_seconds", 120), 120))
    diagnostics_command = package_node_script_args("paper:signal-diagnostics") + ["--limit", str(args.get("limit", 20))]
    diagnostics, diagnostic_code = run_preview_node_json(diagnostics_command, timeout_seconds)
    tick_command = package_node_script_args("paper:tick") + ["--dry-run"]
    tick_payload, tick_code = run_preview_node_json(tick_command, timeout_seconds)
    after = preview_file_hashes()
    state_unchanged = before == after
    diagnostics_payload = diagnostics.get("diagnostics") or {}
    active_market = diagnostics.get("activeMarket") or primary_active_market(candidate)
    latest_candle = diagnostics.get("latestCandle") or {}
    position_state = diagnostics_payload.get("positionState") or {}
    current_position = None
    if position_state.get("hasOpenPosition"):
        current_position = {
            "side": position_state.get("side"),
            "barsHeld": position_state.get("barsHeld"),
        }
    signal = diagnostics_payload.get("signal") or "UNKNOWN"
    reason = diagnostics_payload.get("reason") or (diagnostics.get("nextAction") or {}).get("reason") or "No signal diagnostics reason returned."
    proposed_action, proposed_order = preview_estimated_order(candidate, signal, latest_candle, current_position, reason)
    warnings = dedupe_list((diagnostics.get("warnings") or []) + (tick_payload.get("warnings") or []))
    payload = {
        "ok": bool(state_unchanged and diagnostic_code == 0 and tick_code == 0 and diagnostics.get("ok", True) is not False),
        "previewOnly": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": False,
        "paperTickRan": False,
        "paperStateChanged": False,
        "liveOrdersTouched": False,
        "blockingForPaperTick": False,
        "paperTickAllowed": True,
        "freshness": freshness,
        "candleAlignment": candle_alignment,
        "strategy": candidate.get("strategy"),
        "symbol": active_market.get("symbol") or candidate.get("symbol"),
        "timeframe": active_market.get("timeframe") or active_market.get("interval") or candidate.get("timeframe"),
        "lastCandleTime": latest_candle.get("time"),
        "signal": signal,
        "signalReason": reason,
        "currentPaperPosition": current_position,
        "proposedAction": proposed_action,
        "proposedOrder": proposed_order,
        "blockers": [] if diagnostics.get("ok", True) is not False else [diagnostics.get("error") or "Signal diagnostics failed."],
        "warnings": warnings,
        "stateHashBefore": before["state"],
        "stateHashAfter": after["state"],
        "configHashBefore": before["config"],
        "configHashAfter": after["config"],
        "journalHashBefore": before["journal"],
        "journalHashAfter": after["journal"],
        "journalCsvHashBefore": before["journalCsv"],
        "journalCsvHashAfter": after["journalCsv"],
        "stateUnchanged": state_unchanged,
        "tickDryRun": {
            "status": tick_payload.get("status"),
            "events": tick_payload.get("events"),
            "openPositions": tick_payload.get("openPositions"),
            "closedTrades": tick_payload.get("closedTrades"),
            "returnCode": tick_code,
        },
    }
    if not state_unchanged:
        payload["ok"] = False
        payload.setdefault("blockers", []).append("Preview changed config/state/journal hashes.")
    if not payload["ok"] and not payload.get("blockers"):
        payload["blockers"] = [diagnostics.get("error") or tick_payload.get("error") or "Preview helper failed."]
    return payload, 200 if payload.get("ok") else 400


def active_candidate_confirmation_phrase(candidate: dict, candle_at: str | None) -> str:
    return f"RUN ONE PAPER TICK {candidate.get('candidateKey') or ''} {candle_at or ''}"


def paper_state_market_snapshot(candidate: dict) -> dict:
    state = read_json_file(str(PAPER_STATE_PATH), {}) if PAPER_STATE_PATH.exists() else {}
    active = primary_active_market(candidate)
    key = paper_market_key(active)
    last_processed = state.get("lastProcessedCandleTime") if isinstance(state.get("lastProcessedCandleTime"), dict) else {}
    return {
        "state": state,
        "marketKey": key,
        "lastProcessedCandleTime": last_processed.get(key),
        "accountEquity": safe_float(state.get("accountEquity"), safe_float(candidate.get("accountEquity"), 10000)),
        "openPositions": state.get("openPositions", []) or [],
        "closedTrades": state.get("closedTrades", []) or [],
    }


def write_confirmed_tick_audit(payload: dict) -> Path:
    PAPER_TICK_AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(payload.get("candidateIdentity") or "candidate")).strip("-")
    path = PAPER_TICK_AUDIT_DIR / f"{safe_key}-tick-{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def build_research_confirmed_paper_tick_once(args=None) -> tuple[dict, int]:
    args = args or {}
    candidate = load_paper_candidate_config()
    active = primary_active_market(candidate)
    symbol = active.get("symbol") or candidate.get("symbol")
    timeframe = active.get("interval") or active.get("timeframe") or candidate.get("timeframe")
    candidate_key = candidate.get("candidateKey")
    real_enabled, real_detail = paper_real_trading_enabled()
    freshness = active_paper_market_freshness(args)
    alignment, alignment_status = build_research_paper_candle_alignment(args)
    expected_at = alignment.get("expectedLatestClosedCandleAt")
    expected_time = alignment.get("expectedLatestClosedCandleTime")
    expected_confirm = active_candidate_confirmation_phrase(candidate, expected_at)
    confirm = str(args.get("confirm") or "")
    base = {
        "ok": False,
        "command": "paper:tick-once",
        "candidateIdentity": candidate_key,
        "expectedLatestClosedCandleAt": expected_at,
        "confirmationMatched": confirm == expected_confirm,
        "freshnessStatus": freshness.get("freshnessStatus"),
        "candleAlignmentStatus": alignment.get("candleAlignmentStatus"),
        "previewSignal": None,
        "previewProposedAction": None,
        "tickRan": False,
        "paperTickRan": False,
        "paperStateChanged": False,
        "liveOrdersTouched": False,
        "realTradingEnabled": bool(real_enabled or candidate.get("realTradingEnabled")),
        "openedTrade": False,
        "closedTrade": False,
        "equityBefore": None,
        "equityAfter": None,
        "openPositionsBefore": None,
        "openPositionsAfter": None,
        "processedCandleAt": None,
        "auditPath": None,
        "warnings": [],
    }
    def refused(message: str, status: int = 400, extra: dict | None = None):
        payload = {**base, "error": message, "requiredConfirmation": expected_confirm}
        if extra:
            payload.update(extra)
        return payload, status
    if confirm != expected_confirm:
        return refused("Exact confirmation required; no paper tick was run.")
    if not canonical_paper_enabled(candidate):
        return refused("paperEnabled must be true before running one confirmed paper tick.")
    if candidate.get("strategy") != "EmaBounceV2" or symbol != "BTCUSDT" or timeframe != "4h":
        return refused("Active paper candidate must be EmaBounceV2 BTCUSDT 4h.")
    if real_enabled or candidate.get("realTradingEnabled"):
        return refused(real_detail if real_enabled else "Candidate realTradingEnabled must be false.", extra={"realTradingEnabled": True})
    if candidate.get("exchangeOrders"):
        return refused("exchangeOrders must be false.")
    if candidate.get("liveOrdersTouched"):
        return refused("liveOrdersTouched must be false.")
    if candidate.get("mode") != "PAPER_ONLY":
        return refused("mode must be PAPER_ONLY.")
    if freshness.get("freshnessStatus") != "FRESH" or freshness.get("blockingForPaperTick"):
        return refused("Freshness must be FRESH before running one paper tick.", extra={"freshness": freshness})
    if alignment_status >= 400 or alignment.get("candleAlignmentStatus") not in {"ALIGNED", "EXPLAINED_OFFSET"} or alignment.get("blockingForPaperTick"):
        return refused("Candle alignment must be ALIGNED or explicitly safe before running one paper tick.", extra={"candleAlignment": alignment})
    before_market = paper_state_market_snapshot(candidate)
    if not before_market.get("lastProcessedCandleTime"):
        return refused("Active paper market is not initialized.")
    if safe_float(before_market.get("lastProcessedCandleTime"), 0) >= safe_float(expected_time, 0):
        audit_payload = {**base, "ok": True, "alreadyProcessed": True, "skipped": True, "processedCandleAt": before_market.get("lastProcessedCandleTime"), "equityBefore": before_market.get("accountEquity"), "equityAfter": before_market.get("accountEquity"), "openPositionsBefore": len(before_market.get("openPositions") or []), "openPositionsAfter": len(before_market.get("openPositions") or []), "confirmationMatched": True, "realTradingEnabled": False}
        audit_path = write_confirmed_tick_audit(audit_payload)
        audit_payload["auditPath"] = autopilot_display_path(audit_path)
        return audit_payload, 200
    preview, preview_status = build_research_preview_paper_tick(args)
    base["previewSignal"] = preview.get("signal")
    base["previewProposedAction"] = preview.get("proposedAction")
    if preview_status >= 400 or not preview.get("ok") or preview.get("blockingForPaperTick") or not preview.get("paperTickAllowed"):
        return refused("Preview blocked paper tick; no paper tick was run.", extra={"preview": preview})
    before_hashes = preview_file_hashes()
    before_snapshot = paper_state_snapshot()
    completed = subprocess.run(
        package_node_script_args("paper:tick"),
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=int(safe_float(args.get("timeout_seconds", 90), 90)),
    )
    stdout_payload = {}
    if completed.stdout.strip():
        try:
            stdout_payload = json.loads(completed.stdout)
        except Exception:
            stdout_payload = {"raw": completed.stdout.strip()}
    after_hashes = preview_file_hashes()
    after_snapshot = paper_state_snapshot()
    after_market = paper_state_market_snapshot(candidate)
    opened = len(after_snapshot.get("openPositions") or []) > len(before_snapshot.get("openPositions") or [])
    closed = len(after_snapshot.get("closedTrades") or []) > len(before_snapshot.get("closedTrades") or [])
    payload = {
        **base,
        "ok": completed.returncode == 0,
        "confirmationMatched": True,
        "tickRan": completed.returncode == 0,
        "paperTickRan": completed.returncode == 0,
        "paperStateChanged": before_hashes.get("state") != after_hashes.get("state"),
        "liveOrdersTouched": False,
        "realTradingEnabled": False,
        "openedTrade": opened,
        "closedTrade": closed,
        "equityBefore": before_snapshot.get("updatedAt") and before_market.get("accountEquity") or before_market.get("accountEquity"),
        "equityAfter": after_market.get("accountEquity"),
        "openPositionsBefore": len(before_snapshot.get("openPositions") or []),
        "openPositionsAfter": len(after_snapshot.get("openPositions") or []),
        "processedCandleAt": after_market.get("lastProcessedCandleTime"),
        "tickResult": stdout_payload,
        "returnCode": completed.returncode,
        "warnings": dedupe_list((preview.get("warnings") or []) + (stdout_payload.get("warnings") or [])),
        "fileHashesBefore": before_hashes,
        "fileHashesAfter": after_hashes,
    }
    if completed.returncode != 0:
        payload["error"] = completed.stderr.strip() or "paper:tick command failed."
    audit_path = write_confirmed_tick_audit(payload)
    payload["auditPath"] = autopilot_display_path(audit_path)
    return payload, 200 if payload.get("ok") else 502


def build_research_paper_candidates() -> dict:
    candidates_by_key = {}
    ignored_packages = []
    sources = [
        ("deploy", DEPLOY_REVIEW_CANDIDATE_DIR),
        ("local", PAPER_CANDIDATE_REVIEW_DIR),
    ]
    for source_type, directory in sources:
        if directory.exists():
            paths = sorted(directory.glob("*.json"))
        else:
            paths = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                ignored_packages.append({
                    "path": autopilot_display_path(path),
                    "reason": f"malformed package: {exc}",
                    "sourceType": source_type,
                })
                continue
            summary = autopilot_disabled_paper_candidate_summary(path, payload)
            if summary:
                summary["sourceType"] = source_type
                candidates_by_key[autopilot_review_candidate_dedupe_key(summary)] = summary
            else:
                ignored_packages.append({
                    "path": autopilot_display_path(path),
                    "reason": "not a safe disabled review package",
                    "sourceType": source_type,
                })
    candidates = list(candidates_by_key.values())
    return {
        "ok": True,
        "generatedAt": autopilot_now(),
        "candidates": candidates,
        "ignoredPackages": ignored_packages,
        "candidateCount": len(candidates),
        "safety": {
            **autopilot_safety_payload(),
            "paperEnabled": False,
            "realTradingEnabled": False,
            "configWritten": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "paperTickRan": False,
        },
        "notes": [
            "Review only - not paper enabled.",
            "This endpoint reads disabled candidate packages and does not write config, mutate paper state, start paper ticks, or touch live orders.",
        ],
    }


def autopilot_campaign_args(job: dict) -> dict:
    return {
        "symbols": ",".join(autopilot_list(job.get("symbols") or job.get("symbol"))),
        "timeframes": ",".join(autopilot_list(job.get("timeframes") or job.get("timeframe"))),
        "strategies": ",".join(autopilot_list(job.get("strategies") or job.get("strategy"))),
        "period": job.get("period", "365d"),
        "maxCombosPerStrategy": str(job.get("maxCombosPerStrategy", 10)),
        "topN": str(job.get("topN", 15)),
        "validateTop": "2",
        "includeStress": "true" if job.get("includeStress", True) else "false",
        "includeRecentWindows": "true" if job.get("includeRecentWindows", True) else "false",
        "includeReproAudit": "true" if job.get("includeReproAudit", True) else "false",
        "reproReruns": str(job.get("reproReruns", 2)),
        "timeout_seconds": str(job.get("timeoutSeconds", 900)),
        "save": "true",
    }


def run_autopilot_job(job: dict) -> tuple[dict, int]:
    job["status"] = "RUNNING"
    job["startedAt"] = autopilot_now()
    job["updatedAt"] = job["startedAt"]
    payload, status = build_research_campaign_runner(autopilot_campaign_args(job))
    job["finishedAt"] = autopilot_now()
    job["updatedAt"] = job["finishedAt"]
    job["resultStatusCode"] = status
    job["savedPath"] = payload.get("savedPath")
    job["resultSummary"] = {
        "ok": payload.get("ok"),
        "recommendation": payload.get("recommendation"),
        "savedPath": payload.get("savedPath"),
        "candidateIdentityVersion": payload.get("candidateIdentityVersion"),
    }
    if status < 400 and payload.get("ok", True) is not False:
        job["status"] = "DONE"
        memory = update_autopilot_memory_from_report(payload, payload.get("savedPath"))
    else:
        job["status"] = "FAILED"
        job["error"] = payload.get("error") or "; ".join(payload.get("warnings") or []) or "Campaign runner failed."
        memory = load_autopilot_memory()
    return {"job": job, "campaign": payload, "memory": {"branches": len(memory.get("branches", [])), "candidates": len(memory.get("candidates", []))}}, status


def build_research_autopilot_run_next() -> tuple[dict, int]:
    queue = load_autopilot_queue()
    recovered = recover_stale_autopilot_running_jobs(queue)
    memory = load_autopilot_memory()
    skipped_deprioritized = skip_deprioritized_autopilot_queue_jobs(queue, memory)
    queued = [job for job in queue.get("jobs", []) if job.get("status") == "QUEUED"]
    if not queued:
        if recovered or skipped_deprioritized:
            queue["lastPlanSkippedJobs"] = (skipped_deprioritized + queue.get("lastPlanSkippedJobs", []))[:25]
            save_autopilot_queue(queue)
        return {"ok": False, "error": "No queued research autopilot jobs.", "queue": {"counts": autopilot_queue_counts(queue), "recoveredStaleJobs": recovered, "skippedDeprioritizedJobs": skipped_deprioritized}, "safety": autopilot_safety_payload()}, 404
    job = sorted(queued, key=lambda item: (-safe_float(item.get("priority"), 0), item.get("createdAt") or ""))[0]
    result, status = run_autopilot_job(job)
    save_autopilot_queue(queue)
    return {
        "ok": status < 400 and result["job"].get("status") == "DONE",
        "generatedAt": autopilot_now(),
        **result,
        "queue": {"counts": autopilot_queue_counts(queue), "length": len(queue.get("jobs", [])), "recoveredStaleJobs": recovered, "skippedDeprioritizedJobs": skipped_deprioritized},
        "safety": autopilot_safety_payload(),
    }, 200 if status < 400 else 502


def build_research_autopilot_run_batch(args) -> tuple[dict, int]:
    max_jobs_requested = max(1, int(safe_float(args.get("maxJobs", args.get("max_jobs", 3)), 3)))
    max_jobs_effective = min(max_jobs_requested, 3)
    cap_reason = "safety cap" if max_jobs_effective < max_jobs_requested else None
    results = []
    errors = 0
    for _ in range(max_jobs_effective):
        payload, status = build_research_autopilot_run_next()
        if status == 404:
            break
        results.append({"statusCode": status, "job": payload.get("job"), "ok": payload.get("ok")})
        if status >= 400 or not payload.get("ok"):
            errors += 1
    queue = load_autopilot_queue()
    return {
        "ok": errors == 0,
        "generatedAt": autopilot_now(),
        "maxJobs": max_jobs_effective,
        "maxJobsRequested": max_jobs_requested,
        "maxJobsEffective": max_jobs_effective,
        "capReason": cap_reason,
        "jobsAttempted": len(results),
        "results": results,
        "queue": {"counts": autopilot_queue_counts(queue), "length": len(queue.get("jobs", []))},
        "safety": autopilot_safety_payload(),
    }, 200 if errors == 0 else 207


def build_research_autopilot_reset_queue(args) -> tuple[dict, int]:
    confirm = str(args.get("confirm", "")).strip().lower() in {"1", "true", "yes", "reset"}
    if not confirm:
        return {"ok": False, "error": "reset-queue requires confirm=true.", "queuePreserved": True}, 400
    queue = {"schemaVersion": AUTOPILOT_SCHEMA_VERSION, "generatedAt": autopilot_now(), "updatedAt": autopilot_now(), "jobs": []}
    save_autopilot_queue(queue)
    return {"ok": True, "reset": True, "queue": {"counts": autopilot_queue_counts(queue), "length": 0}, "safety": autopilot_safety_payload()}, 200


def build_research_autopilot_summary() -> dict:
    status = build_research_autopilot_status()
    memory = load_autopilot_memory()
    queue = load_autopilot_queue()
    branches = memory.get("branches", [])
    best_candidate, confirmed_chain, confirmed_chains = autopilot_best_current_candidate(memory)
    best_challenger = best_candidate or next((row for row in branches if row.get("reasonCategory") in {"PROMISING_STABLE", "PROMISING_BUT_RARE"}), None)
    rejected = [row for row in branches if row.get("reasonCategory") in {"NEGATIVE_RETURN", "LOW_PROFIT_FACTOR", "REJECTED", "STRESS_COLLAPSE", "RECENTLY_WEAK"}]
    insufficient = [row for row in branches if row.get("reasonCategory") in {"TOO_FEW_TRADES", "BAD_WALK_FORWARD"}]
    more = [row for row in branches if row.get("reasonCategory") in {"PROMISING_BUT_RARE", "BAD_WALK_FORWARD", "TOO_FEW_TRADES"}]
    next_jobs = [job for job in queue.get("jobs", []) if job.get("status") == "QUEUED"][:3]
    learning_events = autopilot_learning_events(queue, branches)
    return {
        "ok": True,
        "generatedAt": autopilot_now(),
        "summaryText": autopilot_summary_text(best_candidate, best_challenger, branches, rejected, insufficient, more, next_jobs, learning_events, status.get("safety", {}), confirmed_chain),
        "learningEvents": learning_events,
        "bestCurrentCandidate": best_candidate,
        "bestChallenger": best_challenger,
        "confirmedChain": confirmed_chain,
        "confirmedChains": confirmed_chains,
        "bestEligibleChallenger": best_candidate if autopilot_branch_is_confirmed_candidate(best_candidate) else next((row for row in branches if row.get("eligibilityStatus") == "CHALLENGER_ELIGIBLE"), None),
        "bestStableCandidate": best_candidate,
        "branchesTested": len(branches),
        "branchesRejected": rejected[:12],
        "branchesInsufficientEvidence": insufficient[:12],
        "branchesWorthMoreTesting": more[:12],
        "nextRecommendedJobs": next_jobs,
        "strategyFamilies": autopilot_family_summary(memory),
        "safety": status.get("safety", {}),
    }


def build_research_autopilot_journal() -> dict:
    status = build_research_autopilot_status()
    summary = build_research_autopilot_summary()
    queue = load_autopilot_queue()
    memory = load_autopilot_memory()
    families = autopilot_family_summary(memory)
    entries = []
    for event in summary.get("learningEvents") or []:
        entries.append({
            "type": "learning",
            "time": (event.get("branch") or {}).get("lastSeenAt"),
            "title": event.get("outcome") or "Autopilot learned from job",
            "text": event.get("text"),
            "jobId": event.get("jobId"),
            "branch": event.get("branch"),
        })
    for job in queue.get("jobs", []):
        entries.append({
            "type": "job",
            "time": job.get("finishedAt") or job.get("startedAt") or job.get("createdAt"),
            "title": f"{job.get('status', 'UNKNOWN')} {autopilot_job_label(job)}",
            "text": job.get("reason"),
            "job": job,
        })
    for branch in memory.get("branches", []):
        category = branch.get("reasonCategory")
        if category in AUTOPILOT_REJECTED_CATEGORIES:
            title = f"Rejected branch: {autopilot_branch_label(branch)}"
        elif category == "PROMISING_BUT_RARE":
            title = f"Open hypothesis: {autopilot_branch_label(branch)}"
        elif category in {"TOO_FEW_TRADES", "BAD_WALK_FORWARD"}:
            title = f"Insufficient evidence: {autopilot_branch_label(branch)}"
        else:
            continue
        entries.append({
            "type": "branch",
            "time": branch.get("lastSeenAt"),
            "title": title,
            "text": f"{category}; PF {safe_float(branch.get('profitFactor'), 0):.4g}; trades {branch.get('fullTrades')}; blockers: {autopilot_gate_summary(branch)}.",
            "branch": branch,
        })
    entries = sorted(entries, key=lambda item: item.get("time") or "", reverse=True)[:40]
    return {
        "ok": True,
        "generatedAt": autopilot_now(),
        "summaryText": summary.get("summaryText"),
        "entries": entries,
        "openHypotheses": summary.get("branchesWorthMoreTesting", []),
        "rejectedBranches": summary.get("branchesRejected", []),
        "insufficientEvidence": summary.get("branchesInsufficientEvidence", []),
        "nextRecommendedJobs": summary.get("nextRecommendedJobs", []),
        "strategyFamilies": families,
        "queue": status.get("queue", {}),
        "safety": status.get("safety", {}),
    }


def autopilot_branch_label(row: dict | None) -> str:
    if not row:
        return "No branch"
    return f"{row.get('strategy') or '-'} {row.get('symbol') or '-'} {row.get('timeframe') or '-'} {row.get('period') or ''}".strip()


def autopilot_job_label(job: dict | None) -> str:
    if not job:
        return "No job"
    return f"{','.join(job.get('strategies') or autopilot_list(job.get('strategy')))} {','.join(job.get('symbols') or autopilot_list(job.get('symbol')))} {','.join(job.get('timeframes') or autopilot_list(job.get('timeframe')))} {job.get('period') or ''}".strip()


def autopilot_gate_summary(row: dict | None, limit: int = 3) -> str:
    gates = format_failed_gates((row or {}).get("failedGates") or [])
    return ", ".join(gate.get("detail") or gate.get("name") for gate in gates[:limit]) or "no failed gates recorded"


def autopilot_job_origin_text(job: dict) -> str:
    source = job.get("generatedBy") or "planner"
    if source == "rare_lower_timeframe":
        return "lower timeframe follow-up for a promising-but-rare branch"
    if source == "rare_symbol_expansion":
        return "same-strategy symbol expansion for a promising-but-rare branch"
    if source == "broad_search":
        return "broad safe exploration of an untested branch"
    if source == "eligible_confirmation":
        return "confirmation of an eligible/stable branch on a longer validation window"
    if source == "seed":
        return "initial safe first-pass scan"
    return source


def autopilot_learning_events(queue: dict, branches: list[dict]) -> list[dict]:
    branch_map = {(row.get("strategy"), row.get("symbol"), row.get("timeframe"), row.get("period")): row for row in branches}
    events = []
    for job in sorted(queue.get("jobs", []), key=lambda item: item.get("finishedAt") or item.get("startedAt") or item.get("createdAt") or "", reverse=True):
        if job.get("status") not in {"DONE", "FAILED"}:
            continue
        evidence = job.get("previousEvidenceSummary") or {}
        statuses = []
        for strategy in autopilot_list(job.get("strategies") or job.get("strategy")):
            for symbol in autopilot_list(job.get("symbols") or job.get("symbol")):
                for timeframe in autopilot_list(job.get("timeframes") or job.get("timeframe")):
                    row = branch_map.get((strategy, symbol, timeframe, job.get("period")))
                    if row:
                        statuses.append(row)
        primary = statuses[0] if statuses else None
        outcome = "failed to return usable research output" if job.get("status") == "FAILED" else "updated research memory"
        if primary:
            category = primary.get("reasonCategory") or "UNKNOWN"
            if category in {"NEGATIVE_RETURN", "LOW_PROFIT_FACTOR", "STRESS_COLLAPSE", "RECENTLY_WEAK", "REJECTED"}:
                outcome = f"rejected or deprioritized as {category}"
            elif category in {"TOO_FEW_TRADES", "BAD_WALK_FORWARD"}:
                outcome = f"kept as insufficient evidence ({category})"
            elif category == "PROMISING_BUT_RARE":
                outcome = "still promising but rare"
            elif category == "PROMISING_STABLE":
                outcome = "promising and stable enough for confirmation"
        text = (
            f"{autopilot_job_label(job)} was tested because it was a {autopilot_job_origin_text(job)}. "
            f"Result: {outcome}."
        )
        if evidence:
            text += f" Parent evidence: {autopilot_branch_label(evidence)} ({evidence.get('reasonCategory') or 'UNKNOWN'})."
        if primary:
            text += f" Main blockers: {autopilot_gate_summary(primary)}."
        events.append({
            "jobId": job.get("jobId"),
            "status": job.get("status"),
            "generatedBy": job.get("generatedBy"),
            "job": autopilot_job_label(job),
            "outcome": outcome,
            "branch": primary,
            "previousEvidenceSummary": evidence,
            "text": text,
        })
    return events[:8]


def autopilot_summary_text(best_candidate, best_challenger, branches, rejected, insufficient, more, next_jobs, learning_events, safety, confirmed_chain=None) -> str:
    best = f"{autopilot_branch_label(best_candidate)}" if best_candidate else "No eligible or stable candidate found"
    if confirmed_chain:
        best = confirmed_chain.get("label") or best
    challenger = f"{autopilot_branch_label(best_challenger)} ({best_challenger.get('reasonCategory')})" if best_challenger else "No eligible challenger found"
    chain_text = f" Confirmed chain: {confirmed_chain.get('label')}." if confirmed_chain else ""
    learned = " ".join(event.get("text", "") for event in learning_events[:3]) or "No completed Autopilot jobs have been learned from yet."
    rejected_text = "; ".join(f"{autopilot_branch_label(row)}: {row.get('reasonCategory')}" for row in rejected[:3]) or "No rejected branch recorded"
    rare_text = "; ".join(f"{autopilot_branch_label(row)}: PF {safe_float(row.get('profitFactor'), 0):.4g}, trades {row.get('fullTrades')}" for row in more[:3] if row.get("reasonCategory") == "PROMISING_BUT_RARE") or "No promising-but-rare branch remains"
    insufficient_text = "; ".join(f"{autopilot_branch_label(row)}: {row.get('reasonCategory')}" for row in insufficient[:3]) or "No insufficient-evidence branch recorded"
    jobs = "; ".join(f"{autopilot_job_label(job)}: {job.get('reason')}" for job in next_jobs) or "No queued jobs"
    return (
        f"Best current research candidate: {best}. "
        f"Best challenger: {challenger}. "
        f"{chain_text}"
        f"What Autopilot learned: {learned} "
        f"Rejected branches: {rejected_text}. "
        f"Promising but rare: {rare_text}. "
        f"Insufficient evidence: {insufficient_text}. "
        f"Branches tested: {len(branches)}; rejected: {len(rejected)}; worth more testing: {len(more)}. "
        f"Next tests: {jobs}. "
        f"Safety: research-only={safety.get('researchOnly')}, realTradingEnabled={safety.get('realTradingEnabled')}, no promotion/config/paper-state side effects."
    )


def candidate_ledger_key(row: dict) -> str:
    return candidate_identity_from_row(row).get("candidateKey") or "|".join([
        "legacy",
        str(row.get("strategy") or "-"),
        str(row.get("symbol") or "-"),
        str(row.get("timeframe") or "-"),
    ])


def candidate_ledger_source_files(limit: int) -> list[Path]:
    roots = [
        Path(app.root_path) / "reports" / "research-snapshots",
        Path(app.root_path) / "reports" / "stability-first-search",
    ]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(path for path in root.glob("*.json") if path.is_file())
    files = sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:limit]


def approved_research_report_roots() -> list[Path]:
    return [
        Path(app.root_path) / "reports" / "research-snapshots",
        Path(app.root_path) / "reports" / "stability-first-search",
        Path(app.root_path) / "reports" / "research-audits",
        Path(app.root_path) / "reports" / "research-batches",
        Path(app.root_path) / "reports" / "research-drilldowns",
        Path(app.root_path) / "reports" / "research-leads",
        RESEARCH_AUTOPILOT_DIR,
    ]


def resolve_research_report_path(raw: str) -> Path | None:
    root = Path(app.root_path).resolve()
    path = (root / str(raw)).resolve()
    if path.suffix.lower() != ".json":
        return None
    for allowed in approved_research_report_roots():
        try:
            path.relative_to(allowed.resolve())
            return path
        except ValueError:
            continue
    return None


def candidate_ledger_rows_from_payload(payload: dict, source: str) -> list[dict]:
    rows: list[dict] = []
    generated_at = payload.get("generatedAt")

    def add(row: dict, source_section: str):
        if not isinstance(row, dict) or not row.get("strategy"):
            return
        identity = candidate_identity_from_row(row)
        rows.append({
            "source": source,
            "sourceSection": source_section,
            "observedAt": generated_at,
            "strategy": row.get("strategy"),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "candidateKey": identity.get("candidateKey"),
            "paramsHash": identity.get("paramsHash"),
            "executionContextHash": identity.get("executionContextHash"),
            "candidateIdentityVersion": identity.get("candidateIdentityVersion"),
            "legacyIdentity": identity.get("legacyIdentity"),
            "params": row.get("params") or {},
            "normalizedParams": identity.get("normalizedParams"),
            "rank": row.get("rank"),
            "tier": row.get("tier"),
            "eligibilityStatus": row.get("eligibilityStatus") or (row.get("eligibility") or {}).get("status"),
            "stabilityScore": row.get("stabilityScore"),
            "trades": row.get("trades") or (row.get("fullPeriod") or {}).get("trades"),
            "profitFactor": row.get("profitFactor") or (row.get("fullPeriod") or {}).get("profitFactor"),
            "totalReturnPct": row.get("totalReturnPct") or (row.get("fullPeriod") or {}).get("totalReturnPct"),
            "maxDrawdownPct": row.get("maxDrawdownPct") or (row.get("fullPeriod") or {}).get("maxDrawdownPct"),
            "foldPassCount": row.get("foldPassCount") or (row.get("walkForward") or {}).get("foldPassCount"),
            "negativeFoldCount": row.get("negativeFoldCount") or (row.get("walkForward") or {}).get("negativeFoldCount"),
            "stressStatus": row.get("stressStatus") or (row.get("stress") or {}).get("status"),
            "recentWindowStatus": row.get("recentWindowStatus") or (row.get("recentWindows") or {}).get("status"),
            "reproducibilityStatus": row.get("reproducibilityStatus") or (row.get("reproducibility") or {}).get("status"),
        })

    modules = payload.get("modules") or {}
    stability_summary = ((modules.get("stabilityFirstSearch") or {}).get("summary") or {})
    for section in ("topCandidates", "bestResearchedCandidate", "bestStableCandidate", "bestEligibleChallenger"):
        value = stability_summary.get(section)
        if isinstance(value, list):
            for row in value:
                add(row, f"campaign.{section}")
        else:
            add(value or {}, f"campaign.{section}")
    for row in payload.get("topCandidates") or []:
        add(row, "stability.topCandidates")
    for section in ("bestResearchedCandidate", "bestStableCandidate", "bestEligibleChallenger", "bestRawCandidate"):
        add(payload.get(section) or {}, f"stability.{section}")
    leaderboard_rows = payload.get("rows") or []
    for row in leaderboard_rows:
        add(row, "leaderboard.rows")
    return rows


def summarize_candidate_ledger(entries: list[dict], active_key: str) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(candidate_ledger_key(entry), []).append(entry)
    rows = []
    for key, items in grouped.items():
        items = sorted(items, key=lambda item: item.get("observedAt") or "")
        latest = items[-1]
        campaign_items = [item for item in items if item.get("source") != "current_config"]
        by_source: dict[str, list[dict]] = {}
        for item in campaign_items:
            by_source.setdefault(item.get("source") or "-", []).append(item)
        scores = [safe_float(item.get("stabilityScore"), None) for item in items if item.get("stabilityScore") is not None]
        ranks = [safe_float(item.get("rank"), None) for item in items if item.get("rank") is not None]
        eligible_count = sum(1 for source_items in by_source.values() if any(item.get("eligibilityStatus") == "CHALLENGER_ELIGIBLE" for item in source_items))
        stable_count = sum(1 for source_items in by_source.values() if any(item.get("tier") in {"CHALLENGER_ELIGIBLE", "STABILITY_WATCH"} and item.get("eligibilityStatus") != "REJECTED" for item in source_items))
        source_reports = sorted(by_source.keys())
        rows.append({
            "candidateKey": key,
            "paramsHash": latest.get("paramsHash"),
            "executionContextHash": latest.get("executionContextHash"),
            "candidateIdentityVersion": latest.get("candidateIdentityVersion") or "legacy",
            "legacyIdentity": bool(latest.get("legacyIdentity")),
            "normalizedParams": latest.get("normalizedParams") or {},
            "strategy": latest.get("strategy"),
            "symbol": latest.get("symbol"),
            "timeframe": latest.get("timeframe"),
            "isActivePaperCandidate": key == active_key,
            "sightings": len(source_reports),
            "campaignSightings": len(source_reports),
            "sourceReportCount": len(source_reports),
            "sectionAppearances": len(campaign_items),
            "firstSeenAt": items[0].get("observedAt"),
            "latestSeenAt": latest.get("observedAt"),
            "bestRank": min(ranks) if ranks else None,
            "latestRank": latest.get("rank"),
            "bestStabilityScore": max(scores) if scores else None,
            "averageStabilityScore": round(sum(scores) / len(scores), 4) if scores else None,
            "latestStabilityScore": latest.get("stabilityScore"),
            "latestTier": latest.get("tier"),
            "latestEligibilityStatus": latest.get("eligibilityStatus"),
            "eligibleSightings": eligible_count,
            "stableResearchSightings": stable_count,
            "latestMetrics": {
                "trades": latest.get("trades"),
                "profitFactor": latest.get("profitFactor"),
                "totalReturnPct": latest.get("totalReturnPct"),
                "maxDrawdownPct": latest.get("maxDrawdownPct"),
                "foldPassCount": latest.get("foldPassCount"),
                "negativeFoldCount": latest.get("negativeFoldCount"),
                "stressStatus": latest.get("stressStatus"),
                "recentWindowStatus": latest.get("recentWindowStatus"),
                "reproducibilityStatus": latest.get("reproducibilityStatus"),
            },
            "sourceReports": source_reports,
            "sources": sorted(set(item.get("sourceSection") for item in items if item.get("sourceSection"))),
        })
    rows.sort(key=lambda row: (
        1 if row["eligibleSightings"] else 0,
        row["stableResearchSightings"],
        row["sightings"],
        safe_float(row.get("bestStabilityScore"), -999999),
        -safe_float(row.get("bestRank"), 999999),
    ), reverse=True)
    for index, row in enumerate(rows, start=1):
        row["ledgerRank"] = index
    return rows


def build_research_candidate_evidence_ledger(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_strategy = candidate.get("strategy") or "SimpleAtrTrendV2"
    active_symbol = active.get("symbol") or "ETHUSDT"
    active_timeframe = active.get("interval") or active.get("timeframe") or "1h"
    active_context = candidate_context_from_config(candidate)
    active_key = candidate_identity_from_parts(
        active_strategy,
        active_symbol,
        active_timeframe,
        candidate.get("params") if isinstance(candidate.get("params"), dict) else {},
        active_context["fillModel"],
        active_context["makerFeePct"],
        active_context["takerFeePct"],
        active_context["slippageBps"],
    )["candidateKey"]
    file_limit = campaign_int(args, "fileLimit", 50, 1, 250)
    row_limit = campaign_int(args, "limit", 25, 1, 100)
    warnings = []
    entries: list[dict] = []
    files = candidate_ledger_source_files(file_limit)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            source = str(path.relative_to(Path(app.root_path))).replace("\\", "/")
            entries.extend(candidate_ledger_rows_from_payload(payload, source))
        except Exception as exc:
            warnings.append(f"Could not read ledger source {path.name}: {exc}")
    active_entry = {
        "source": "current_config",
        "sourceSection": "activePaperCandidate",
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "strategy": active_strategy,
        "symbol": active_symbol,
        "timeframe": active_timeframe,
        "params": candidate.get("params") if isinstance(candidate.get("params"), dict) else {},
        "fillModel": active_context["fillModel"],
        "makerFeePct": active_context["makerFeePct"],
        "takerFeePct": active_context["takerFeePct"],
        "slippageBps": active_context["slippageBps"],
        "tier": "ACTIVE_PAPER_CANDIDATE",
        "eligibilityStatus": "BASELINE",
    }
    entries.append(active_entry)
    rows = summarize_candidate_ledger(entries, active_key)[:row_limit]
    top = rows[0] if rows else {}
    non_active_repeated = next((row for row in rows if not row.get("isActivePaperCandidate") and (row.get("eligibleSightings") or row.get("stableResearchSightings", 0) >= 2)), None)
    recommendation = {
        "action": "REVIEW_REPEATED_EVIDENCE" if non_active_repeated else "COLLECT_MORE_CAMPAIGNS",
        "reason": "Ledger is strongest when candidates appear repeatedly across saved campaigns. Run campaign-runner with save=true after meaningful research scans.",
    }
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "activePaperCandidate": {
            **candidate_summary(candidate),
            "strategy": active_strategy,
            "symbol": active_symbol,
            "timeframe": active_timeframe,
            "candidateKey": active_key,
        },
        "sourceFiles": [str(path.relative_to(Path(app.root_path))).replace("\\", "/") for path in files],
        "entriesRead": len(entries),
        "rows": rows,
        "summary": {
            "candidateCount": len(rows),
            "activeCandidateLedgerRank": next((row.get("ledgerRank") for row in rows if row.get("isActivePaperCandidate")), None),
            "eligibleCandidateCount": sum(1 for row in rows if row.get("eligibleSightings")),
            "stableResearchCandidateCount": sum(1 for row in rows if row.get("stableResearchSightings")),
            "topCandidate": top,
        },
        "recommendation": recommendation,
        "warnings": dedupe_list(([real_detail] if real_enabled else []) + warnings),
    }, 200


def research_result_diff_files(args) -> tuple[Path | None, Path | None, list[str]]:
    warnings = []
    current_arg = args.get("current")
    previous_arg = args.get("previous")
    if current_arg and previous_arg:
        current = resolve_research_report_path(str(current_arg))
        previous = resolve_research_report_path(str(previous_arg))
        if not current or not previous:
            warnings.append("Explicit report paths must stay under approved reports directories and point to JSON files.")
            return None, None, warnings
        return current, previous, warnings
    files = candidate_ledger_source_files(campaign_int(args, "fileLimit", 20, 2, 250))
    if len(files) < 2:
        warnings.append("Need at least two saved research JSON reports to build a result diff.")
        return (files[0] if files else None), None, warnings
    return files[0], files[1], warnings


def read_research_json(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_rows_by_key(payload: dict, source: str) -> dict[str, dict]:
    rows = candidate_ledger_rows_from_payload(payload, source)
    by_key = {}
    for row in rows:
        key = candidate_ledger_key(row)
        if key not in by_key or safe_float(row.get("rank"), 999999) < safe_float(by_key[key].get("rank"), 999999):
            by_key[key] = row
    return by_key


def research_context_for_diff(payload: dict) -> dict:
    search = payload.get("search") or {}
    run_context = payload.get("runContext") or {}
    modules = payload.get("modules") or {}
    stability_scope = (((modules.get("stabilityFirstSearch") or {}).get("scope")) or {})
    execution = payload.get("executionContext") or {}
    data_context = payload.get("dataContext") or {}
    return {
        "period": search.get("period") or run_context.get("period") or data_context.get("requestedPeriod"),
        "source": data_context.get("source") or run_context.get("source") or search.get("source") or "bybit",
        "symbols": search.get("symbols") or stability_scope.get("symbols"),
        "timeframes": search.get("timeframes") or stability_scope.get("timeframes"),
        "strategies": search.get("strategies") or stability_scope.get("strategies"),
        "folds": search.get("folds") or run_context.get("folds"),
        "maxCombosPerStrategy": search.get("maxCombosPerStrategy") or run_context.get("maxCombosPerStrategy"),
        "topN": search.get("topN") or run_context.get("topN"),
        "validateTop": search.get("validateTop"),
        "from": run_context.get("from"),
        "to": run_context.get("to"),
        "costs": run_context.get("costs") or {
            "makerFeePct": execution.get("makerFeePct"),
            "takerFeePct": execution.get("takerFeePct"),
            "slippageBps": execution.get("slippageBps"),
        },
        "fillModel": run_context.get("fillModel") or execution.get("fillModel"),
        "schemaVersion": payload.get("schemaVersion"),
        "candidateIdentityVersion": payload.get("candidateIdentityVersion"),
        "gitCommit": payload.get("gitCommit"),
    }


def compare_research_contexts(current: dict, previous: dict) -> dict:
    important = ["period", "source", "symbols", "timeframes", "strategies", "folds", "costs", "fillModel", "candidateIdentityVersion"]
    optional = ["from", "to", "maxCombosPerStrategy", "topN", "validateTop", "schemaVersion", "gitCommit"]
    differences = []
    for key in important + optional:
        if stable_json(current.get(key)) != stable_json(previous.get(key)):
            differences.append({"field": key, "current": current.get(key), "previous": previous.get(key), "severity": "important" if key in important else "optional"})
    important_differences = [item for item in differences if item["severity"] == "important"]
    if not differences:
        status = "COMPARABLE"
    elif important_differences:
        status = "NOT_COMPARABLE" if any(item["field"] in {"period", "source", "costs", "fillModel", "candidateIdentityVersion"} for item in important_differences) else "PARTIALLY_COMPARABLE"
    else:
        status = "PARTIALLY_COMPARABLE"
    return {
        "status": status,
        "differences": differences,
        "scoreDeltasAllowed": status == "COMPARABLE",
    }


def compact_diff_row(key: str, current: dict | None, previous: dict | None, score_deltas_allowed: bool = True) -> dict:
    cur = current or {}
    prev = previous or {}
    return {
        "candidateKey": key,
        "strategy": cur.get("strategy") or prev.get("strategy"),
        "symbol": cur.get("symbol") or prev.get("symbol"),
        "timeframe": cur.get("timeframe") or prev.get("timeframe"),
        "currentRank": cur.get("rank"),
        "previousRank": prev.get("rank"),
        "rankDelta": None if cur.get("rank") is None or prev.get("rank") is None else safe_float(prev.get("rank"), 0) - safe_float(cur.get("rank"), 0),
        "currentStabilityScore": cur.get("stabilityScore"),
        "previousStabilityScore": prev.get("stabilityScore"),
        "stabilityScoreDelta": None if not score_deltas_allowed or cur.get("stabilityScore") is None or prev.get("stabilityScore") is None else round(safe_float(cur.get("stabilityScore"), 0) - safe_float(prev.get("stabilityScore"), 0), 4),
        "currentEligibilityStatus": cur.get("eligibilityStatus"),
        "previousEligibilityStatus": prev.get("eligibilityStatus"),
        "currentTier": cur.get("tier"),
        "previousTier": prev.get("tier"),
    }


def research_payload_verdict(payload: dict) -> dict:
    modules = payload.get("modules") or {}
    campaign_stability = ((modules.get("stabilityFirstSearch") or {}).get("summary") or {}).get("verdict")
    return payload.get("verdict") or campaign_stability or payload.get("recommendation") or {}


def build_research_result_diff(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    current_path, previous_path, warnings = research_result_diff_files(args)
    current_payload = read_research_json(current_path)
    previous_payload = read_research_json(previous_path)
    if not current_payload or not previous_payload:
        return {
            "ok": False,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "currentFile": str(current_path.relative_to(Path(app.root_path))).replace("\\", "/") if current_path else None,
            "previousFile": str(previous_path.relative_to(Path(app.root_path))).replace("\\", "/") if previous_path else None,
            "warnings": dedupe_list(([real_detail] if real_enabled else []) + warnings),
        }, 404
    current_source = str(current_path.relative_to(Path(app.root_path))).replace("\\", "/")
    previous_source = str(previous_path.relative_to(Path(app.root_path))).replace("\\", "/")
    current_context = research_context_for_diff(current_payload)
    previous_context = research_context_for_diff(previous_payload)
    comparability = compare_research_contexts(current_context, previous_context)
    score_deltas_allowed = comparability.get("scoreDeltasAllowed") is True
    current_rows = candidate_rows_by_key(current_payload, current_source)
    previous_rows = candidate_rows_by_key(previous_payload, previous_source)
    current_keys = set(current_rows.keys())
    previous_keys = set(previous_rows.keys())
    added = [compact_diff_row(key, current_rows[key], None, score_deltas_allowed) for key in sorted(current_keys - previous_keys)]
    removed = [compact_diff_row(key, None, previous_rows[key], score_deltas_allowed) for key in sorted(previous_keys - current_keys)]
    changed = []
    for key in sorted(current_keys & previous_keys):
        row = compact_diff_row(key, current_rows[key], previous_rows[key], score_deltas_allowed)
        if row["rankDelta"] not in {None, 0} or row["stabilityScoreDelta"] not in {None, 0} or row["currentEligibilityStatus"] != row["previousEligibilityStatus"]:
            changed.append(row)
    changed.sort(key=lambda row: abs(safe_float(row.get("stabilityScoreDelta"), 0)) + abs(safe_float(row.get("rankDelta"), 0)), reverse=True)
    current_verdict = research_payload_verdict(current_payload)
    previous_verdict = research_payload_verdict(previous_payload)
    verdict_changed = (current_verdict.get("action") or current_verdict.get("status")) != (previous_verdict.get("action") or previous_verdict.get("status"))
    recommendation = {
        "action": "REVIEW_CHANGED_RESEARCH" if added or removed or changed or verdict_changed else "NO_MATERIAL_CHANGE",
        "reason": "Review added/removed candidates, rank deltas, stability-score deltas, and verdict changes before rerunning or promoting anything.",
    }
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "currentFile": current_source,
        "previousFile": previous_source,
        "currentGeneratedAt": current_payload.get("generatedAt"),
        "previousGeneratedAt": previous_payload.get("generatedAt"),
        "comparability": comparability,
        "currentContext": current_context,
        "previousContext": previous_context,
        "verdict": {
            "changed": verdict_changed,
            "current": current_verdict,
            "previous": previous_verdict,
        },
        "counts": {
            "currentCandidates": len(current_rows),
            "previousCandidates": len(previous_rows),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "addedCandidates": added[:25],
        "removedCandidates": removed[:25],
        "changedCandidates": changed[:25],
        "recommendation": recommendation,
        "warnings": dedupe_list(([real_detail] if real_enabled else []) + warnings),
    }, 200


def checklist_item(items: list[dict], name: str, passed: bool, severity: str, detail: str, evidence: dict | None = None) -> None:
    items.append({
        "name": name,
        "pass": bool(passed),
        "severity": severity,
        "detail": detail,
        "evidence": evidence or {},
    })


def normalize_stability_status(payload: dict) -> dict:
    candidates = [
        ("validation.status", ((payload.get("validation") or {}).get("status"))),
        ("status", payload.get("status")),
        ("stability.status", ((payload.get("stability") or {}).get("status"))),
    ]
    raw = None
    source = None
    for field, value in candidates:
        if value not in (None, ""):
            raw = str(value).upper()
            source = field
            break
    mapping = {
        "PASS": "PASS",
        "WATCH": "WATCH",
        "WARN": "WATCH",
        "STABLE": "PASS",
        "FAIL": "FAIL",
        "ERROR": "ERROR",
        "BLOCKED": "FAIL",
    }
    normalized = mapping.get(raw or "", "UNKNOWN")
    validation = payload.get("validation") or {}
    aggregate = validation.get("aggregate") or {}
    windows = validation.get("windows") or []
    fold_returns = [safe_float(row.get("totalReturnPct"), None) for row in windows if row.get("totalReturnPct") is not None]
    fold_pfs = [safe_float(row.get("profitFactor"), None) for row in windows if row.get("profitFactor") is not None]
    worst = min(windows, key=lambda row: safe_float(row.get("totalReturnPct"), 999999), default=None)
    return {
        "status": normalized,
        "rawStatus": raw,
        "statusSource": source,
        "windows": windows,
        "walkForwardPassCount": aggregate.get("passWindows") or aggregate.get("passFoldCount"),
        "negativeFolds": aggregate.get("negativeWindows") or aggregate.get("negativeFoldCount"),
        "worstFold": worst,
        "medianFoldReturn": median_numbers(fold_returns),
        "medianFoldProfitFactor": median_numbers(fold_pfs),
        "fullTrades": aggregate.get("totalTrades"),
        "fullProfitFactor": aggregate.get("profitFactor"),
        "fullReturn": aggregate.get("totalReturnPct"),
        "drawdown": aggregate.get("maxDrawdownPct"),
        "summary": validation.get("summary"),
    }


def median_numbers(values: list[float]) -> float | None:
    nums = sorted(value for value in values if value is not None and math.isfinite(value))
    if not nums:
        return None
    mid = len(nums) // 2
    if len(nums) % 2:
        return nums[mid]
    return round((nums[mid - 1] + nums[mid]) / 2, 4)


def promotion_checklist_verdict(items: list[dict], paper_enabled: bool, real_enabled: bool) -> dict:
    blockers = [item for item in items if not item.get("pass") and item.get("severity") == "BLOCK"]
    warnings = [item for item in items if not item.get("pass") and item.get("severity") == "WARN"]
    if real_enabled:
        return {"status": "BLOCKED", "action": "DO_NOT_PROMOTE", "reason": "Real trading is enabled or partially configured; promotion review is blocked."}
    if blockers:
        return {"status": "BLOCKED", "action": "RESEARCH_MORE", "reason": f"{len(blockers)} blocking checklist item(s) remain."}
    if paper_enabled:
        return {"status": "WATCH", "action": "WAIT_FOR_PAPER_OR_DISABLE_BEFORE_PROMOTION", "reason": "Paper is enabled; avoid config promotion while a paper session is running."}
    if warnings:
        return {"status": "READY_WITH_WARNINGS", "action": "MANUAL_REVIEW_REQUIRED", "reason": f"No blockers, but {len(warnings)} warning item(s) need manual review."}
    return {"status": "READY_FOR_MANUAL_PROMOTION_REVIEW", "action": "REVIEW_CONFIG_ONLY_PROMOTION", "reason": "Checklist has no blockers. This is manual review only; no promotion is automatic."}


def build_research_promotion_checklist_v2(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    strategy = candidate.get("strategy") or "SimpleAtrTrendV2"
    symbol = active.get("symbol") or "ETHUSDT"
    timeframe = active.get("interval") or active.get("timeframe") or "1h"
    items: list[dict] = []
    warnings = []

    config_warnings = candidate_config_warnings(candidate)
    validation = validate_candidate_config(candidate, candidate_validation_rules(args))
    stability = build_candidate_stability_report({"compareCurrent": "true", "period": args.get("period", "365d")})
    stability_norm = normalize_stability_status(stability)
    readiness = build_paper_readiness_report(args)
    review = build_candidate_review()
    ledger, _ = build_research_candidate_evidence_ledger({"fileLimit": args.get("fileLimit", "50"), "limit": args.get("ledgerLimit", "25")})
    diff, diff_status = build_research_result_diff({"fileLimit": args.get("fileLimit", "20")})
    if diff_status >= 400:
        warnings.extend(diff.get("warnings") or [])

    checklist_item(items, "real trading disabled", not real_enabled, "BLOCK", real_detail)
    checklist_item(items, "paper disabled for config review", not paper_enabled, "WARN", "Paper is disabled." if not paper_enabled else "Paper is currently enabled; promotion review should wait.")
    checklist_item(items, "current candidate exists", bool(candidate), "BLOCK", f"{strategy} {symbol} {timeframe}")
    checklist_item(items, "config warnings empty", not config_warnings, "BLOCK", "No config warnings." if not config_warnings else "; ".join(config_warnings), {"configWarnings": config_warnings})
    checklist_item(items, "candidate validation PASS", validation.get("status") == "PASS", "BLOCK", f"Validation status: {validation.get('status')}.", validation)
    stability_is_pass = stability_norm["status"] == "PASS"
    stability_severity = "BLOCK" if stability_norm["status"] in {"FAIL", "ERROR"} else "WARN"
    stability_detail = f"Stability status: {stability_norm['status']}."
    if stability_norm["status"] == "UNKNOWN":
        stability_detail = "Stability evidence unavailable or integration shape unknown; manual review required."
    checklist_item(items, "candidate stability PASS", stability_is_pass, stability_severity, stability_detail, stability_norm)
    readiness_blockers = [check for check in readiness.get("checks") or [] if not check.get("pass") and check.get("severity") == "BLOCK"]
    readiness_warnings = [check for check in readiness.get("checks") or [] if not check.get("pass") and check.get("severity") == "WARN"]
    checklist_item(items, "paper readiness has no blockers", not readiness_blockers, "BLOCK", f"{len(readiness_blockers)} readiness blocker(s).", {"status": readiness.get("status"), "blockingIssues": len(readiness_blockers)})
    checklist_item(items, "paper readiness warnings reviewed", not readiness_warnings, "WARN", f"{len(readiness_warnings)} readiness warning(s).", {"warnings": readiness_warnings[:5]})
    review_readiness = review.get("readiness") or {}
    checklist_item(items, "candidate review supports config-only review", bool(review_readiness.get("canPromoteConfigOnly") or review_readiness.get("safeForManualReview")), "WARN", review.get("nextAction", {}).get("reason", "Candidate review is advisory."), review_readiness)
    ledger_summary = ledger.get("summary") or {}
    active_rank = ledger_summary.get("activeCandidateLedgerRank")
    checklist_item(items, "evidence ledger has active candidate memory", active_rank is not None, "WARN", f"Active candidate ledger rank: {active_rank or '-'}", ledger_summary)
    diff_verdict = diff.get("verdict") or {}
    checklist_item(items, "latest research diff has no verdict regression", diff_status < 400 and not diff_verdict.get("changed"), "WARN", "Latest saved verdict is unchanged." if not diff_verdict.get("changed") else "Latest saved verdict changed; review diff.", diff.get("counts") or {})

    verdict = promotion_checklist_verdict(items, paper_enabled, real_enabled)
    counts = {
        "pass": sum(1 for item in items if item.get("pass")),
        "warn": sum(1 for item in items if not item.get("pass") and item.get("severity") == "WARN"),
        "block": sum(1 for item in items if not item.get("pass") and item.get("severity") == "BLOCK"),
    }
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": {
            **candidate_summary(candidate),
            "strategy": strategy,
            "symbol": symbol,
            "timeframe": timeframe,
            **candidate_identity_from_parts(
                strategy,
                symbol,
                timeframe,
                candidate.get("params") if isinstance(candidate.get("params"), dict) else {},
                candidate_context_from_config(candidate)["fillModel"],
                candidate_context_from_config(candidate)["makerFeePct"],
                candidate_context_from_config(candidate)["takerFeePct"],
                candidate_context_from_config(candidate)["slippageBps"],
            ),
        },
        "verdict": verdict,
        "counts": counts,
        "checks": items,
        "supportingEvidence": {
            "validation": validation,
            "stability": stability_norm,
            "readiness": {"status": readiness.get("status"), "ready": readiness.get("ready"), "summary": readiness.get("summary")},
            "review": {"nextAction": review.get("nextAction"), "readiness": review_readiness},
            "ledger": ledger_summary,
            "resultDiff": {"ok": diff_status < 400, "verdict": diff.get("verdict"), "counts": diff.get("counts"), "comparability": diff.get("comparability")},
        },
        "safety": {
            "promoted": False,
            "configWritten": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "realTradingTouched": False,
        },
        "warnings": dedupe_list(([real_detail] if real_enabled else []) + warnings),
    }, 200


def compact_replay_trade(trade: dict) -> dict:
    return {
        "entryTime": iso_from_backtest_time(trade.get("entry_time") or trade.get("entryTime")),
        "exitTime": iso_from_backtest_time(trade.get("exit_time") or trade.get("exitTime")),
        "side": trade.get("side") or trade.get("direction") or "long",
        "entryPrice": trade.get("entry_price") or trade.get("entryPrice"),
        "exitPrice": trade.get("exit_price") or trade.get("exitPrice"),
        "returnPct": trade.get("return_pct") or trade.get("returnPct"),
        "exitReason": trade.get("exit_reason") or trade.get("exitReason"),
    }


def build_research_signal_replay_report(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    limit = research_limit_for(candidate.get("source") or "bybit", timeframe, period, args.get("limit", "auto"))
    recent_limit = max(10, min(int(safe_float(args.get("recentLimit", args.get("recent_limit", 100)), 100)), 300))
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("takerFeePct"), 0)
    slippage_pct = safe_float(candidate.get("slippageBps"), 0) / 100
    blockers, blocker_status = build_research_blocker_analytics({
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": strategy,
        "period": period,
        "limit": args.get("limit", "auto"),
        "includeRecentCandles": "true",
        "recentLimit": str(recent_limit),
    })
    backtest_payload = run_shared_backtest_engine(
        candidate.get("source") or "bybit",
        symbol,
        timeframe,
        period,
        strategy,
        fee_pct,
        slippage_pct,
        limit,
        debug=True,
        allow_shorts=False,
        strategy_params=params,
    )
    backtest_payload = normalize_backtest_response(backtest_payload, candidate.get("source") or "bybit", symbol, timeframe, period, strategy, fee_pct, slippage_pct)
    trades = backtest_payload.get("trade_list") or backtest_payload.get("tradeList") or []
    recent_candles = (blockers.get("recentCandles") or [])[-recent_limit:]
    signal_candles = [row for row in recent_candles if str(row.get("signal") or "").upper() not in {"", "-", "HOLD", "NONE"}]
    hold_candles = [row for row in recent_candles if str(row.get("signal") or "").upper() in {"", "-", "HOLD", "NONE"}]
    near_misses = blockers.get("nearMisses") or []
    warnings = dedupe_list((blockers.get("warnings") or []) + (backtest_payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    latest = recent_candles[-1] if recent_candles else {}
    if signal_candles:
        verdict = {
            "status": "SIGNALS_PRESENT",
            "summary": f"{len(signal_candles)} signal candle(s) were found in the recent replay window.",
            "nextAction": {"action": "REVIEW_SIGNAL_CANDLES", "reason": "Inspect signal candles and matching trade markers manually."},
        }
    elif near_misses:
        verdict = {
            "status": "NEAR_MISSES_ONLY",
            "summary": "No recent signal candle was found, but near misses are available for review.",
            "nextAction": {"action": "REVIEW_NEAR_MISSES", "reason": "Inspect failed blockers before changing any strategy parameters."},
        }
    else:
        verdict = {
            "status": "HOLD_ONLY",
            "summary": "Recent candles are all HOLD/no-signal in the replay window.",
            "nextAction": {"action": "OBSERVE_MORE", "reason": "Continue research/paper observation; this report is read-only."},
        }
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "search": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "recentLimit": recent_limit},
        "summary": {
            "candlesReviewed": len(recent_candles),
            "signalCandles": len(signal_candles),
            "holdCandles": len(hold_candles),
            "nearMisses": len(near_misses),
            "tradeMarkers": len(trades),
            "latestSignal": latest.get("signal"),
            "latestReason": latest.get("reason"),
            "blockerAnalyticsStatus": blocker_status,
        },
        "verdict": verdict,
        "recentCandles": recent_candles,
        "signalCandles": signal_candles[-25:],
        "nearMisses": near_misses[-25:],
        "recentTradeMarkers": [compact_replay_trade(trade) for trade in trades[-25:]],
        "warnings": warnings,
    }, 200


def consistency_check(name: str, passed: bool, severity: str, detail: str) -> dict:
    return {
        "name": name,
        "pass": bool(passed),
        "severity": severity,
        "detail": detail,
    }


def build_research_data_cost_consistency_audit(args) -> tuple[dict, int]:
    candidate = normalize_promoted_candidate_config(load_paper_candidate_config())
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = active_candidate_primary_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    source = args.get("source", candidate.get("source") or "bybit")
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("takerFeePct"), 0)
    slippage_pct = safe_float(candidate.get("slippageBps"), 0) / 100
    limit_arg = args.get("limit", "auto")
    limit = research_limit_for(source, timeframe, period, limit_arg)
    result_payload = run_shared_backtest_engine(
        source,
        symbol,
        timeframe,
        period,
        strategy,
        fee_pct,
        slippage_pct,
        limit,
        debug=False,
        allow_shorts=False,
        strategy_params=params,
    )
    result_payload = normalize_backtest_response(result_payload, source, symbol, timeframe, period, strategy, fee_pct, slippage_pct)
    params_used = (result_payload.get("diagnostics") or {}).get("params") or params
    params_used = {**(params_used or {}), "feePct": fee_pct, "slippagePct": slippage_pct}
    active_comparison = compare_manual_backtest_to_active_candidate(
        strategy,
        symbol,
        timeframe,
        params_used,
        candidate,
        "activeCandidate",
        source,
        fee_pct,
        slippage_pct,
    )
    run_context, context_warnings = manual_backtest_run_context(
        result_payload,
        period,
        limit_arg,
        source,
        strategy,
        symbol,
        timeframe,
        params_used,
        "activeCandidate",
        candidate,
    )
    comparability = manual_backtest_comparability(run_context, active_comparison, context_warnings)
    expected = safe_float(run_context.get("expectedCandles"), 0)
    used = safe_float(run_context.get("candlesUsed"), 0)
    candle_ok = expected <= 0 or (used >= expected * 0.75 and used <= expected * 1.25)
    checks = [
        consistency_check("Real trading disabled", not real_enabled, "FAIL", "Real trading flag is disabled." if not real_enabled else real_detail),
        consistency_check("Source is bybit", source == "bybit", "WARN", f"Source: {source}."),
        consistency_check("Fill model is next-open", run_context.get("fillModel") == "next-open", "WARN", f"Fill model: {run_context.get('fillModel')}."),
        consistency_check("Taker fee matches active candidate", abs(safe_float(run_context.get("takerFeePct"), 0) - fee_pct) < 1e-9, "WARN", f"Run taker fee {run_context.get('takerFeePct')} vs active {fee_pct}."),
        consistency_check("Slippage matches active candidate", abs(safe_float(run_context.get("slippageBps"), 0) - safe_float(candidate.get("slippageBps"), 0)) < 1e-9, "WARN", f"Run slippage bps {run_context.get('slippageBps')} vs active {candidate.get('slippageBps')}."),
        consistency_check("Candle coverage is expected", candle_ok, "WARN", f"Used {int(used)} candle(s), expected about {int(expected) if expected else 'unknown'}."),
        consistency_check("First/last candle times present", bool(run_context.get("firstCandleTime") and run_context.get("lastCandleTime")), "WARN", f"{run_context.get('firstCandleTime')} to {run_context.get('lastCandleTime')}."),
        consistency_check("Active candidate comparable", comparability.get("status") == "COMPARABLE", "WARN", comparability.get("summary") or "Comparability check completed."),
    ]
    blocking = [check for check in checks if not check.get("pass") and check.get("severity") == "FAIL"]
    warnings = [check for check in checks if not check.get("pass") and check.get("severity") == "WARN"]
    if blocking:
        status = "FAIL"
        recommendation = {"action": "FIX_SAFETY_BLOCKERS", "reason": "One or more consistency checks failed with blocking severity."}
    elif warnings:
        status = "WATCH"
        recommendation = {"action": "REVIEW_CONTEXT_DIFFERENCES", "reason": "Some context assumptions differ. Compare backtests cautiously."}
    else:
        status = "CONSISTENT"
        recommendation = {"action": "USE_FOR_COMPARISON", "reason": "Data and cost context looks consistent for active-candidate manual/research comparison."}
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "search": {"source": source, "symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "limit": limit_arg},
        "status": status,
        "summary": {
            "checks": len(checks),
            "passing": len([check for check in checks if check.get("pass")]),
            "warnings": len(warnings),
            "blocking": len(blocking),
        },
        "runContext": run_context,
        "comparability": comparability,
        "checks": checks,
        "recommendation": recommendation,
        "warnings": dedupe_list(context_warnings + active_comparison.get("warnings", []) + ([real_detail] if real_enabled else [])),
    }, 200


def build_research_timeframe_preset_search(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframes = args.get("timeframes", "15m,1h,4h")
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    presets = args.get("presets", "default")
    max_rows = max(1, min(int(safe_float(args.get("maxRows", args.get("max_rows", 100)), 100)), 100))
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("feePct"), safe_float(candidate.get("takerFeePct"), 0.055))
    slippage_pct = safe_float(candidate.get("slippagePct"), safe_float(candidate.get("slippageBps"), 2) / 100)
    command = package_node_script_args("research:timeframe-preset-search")
    command.extend([
        "--symbol", symbol,
        "--timeframes", str(timeframes),
        "--strategy", strategy,
        "--period", period,
        "--presets", presets,
        "--maxRows", str(max_rows),
        "--baseParams", json.dumps(params),
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 360), 360)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Timeframe preset search lab timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "activeCandidate": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Timeframe preset search lab timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Timeframe preset search lab returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Timeframe preset search lab returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Timeframe preset search lab command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "activeCandidate": candidate_summary(candidate),
        "search": payload.get("search") or {"symbol": symbol, "timeframes": timeframes, "strategy": strategy, "period": period, "presets": presets, "maxRows": max_rows},
        "rows": payload.get("rows") or [],
        "summary": payload.get("summary") or {},
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def _candidate_deep_compare_batch_dir() -> Path:
    return (Path(app.root_path) / "reports" / "research-batches").resolve()


def _load_candidate_deep_compare_batch_file(raw_path: str) -> tuple[dict | None, str | None]:
    if not raw_path:
        return None, None
    base_dir = _candidate_deep_compare_batch_dir()
    candidate_path = Path(raw_path)
    if not candidate_path.is_absolute():
        candidate_path = (Path(app.root_path) / candidate_path).resolve()
    else:
        candidate_path = candidate_path.resolve()
    try:
        candidate_path.relative_to(base_dir)
    except ValueError:
        return None, "batchFile must point to a JSON file under reports/research-batches/."
    if not candidate_path.exists() or not candidate_path.is_file():
        return None, f"batchFile was not found: {raw_path}"
    try:
        with open(candidate_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        return None, f"Could not read optimizer batch file: {exc}"
    payload["_resolvedBatchFile"] = str(candidate_path.relative_to(Path(app.root_path))).replace("\\", "/")
    return payload, None


def _select_optimizer_batch_challenger(batch_payload: dict, args, rank: int) -> tuple[dict | None, str | None]:
    rows = [row for row in (batch_payload.get("rows") or []) if isinstance(row, dict) and not row.get("isActiveBaseline")]
    if not rows:
        return None, "Optimizer batch did not return challenger rows."
    strategy = (args.get("challengerStrategy") or "").strip()
    symbol = (args.get("challengerSymbol") or "").strip()
    timeframe = (args.get("challengerTimeframe") or args.get("challengerInterval") or "").strip()
    if strategy or symbol or timeframe:
        for row in rows:
            if strategy and str(row.get("strategy") or "") != strategy:
                continue
            if symbol and str(row.get("symbol") or "") != symbol:
                continue
            if timeframe and str(row.get("timeframe") or row.get("interval") or "") != timeframe:
                continue
            return row, None
    sorted_rows = sorted(rows, key=lambda row: int(safe_float(row.get("rank"), 999999)))
    index = max(0, min(rank - 1, len(sorted_rows) - 1))
    return sorted_rows[index], None


def _run_optimizer_batch_for_deep_compare(args, challenger_strategy: str, challenger_symbol: str, challenger_timeframe: str) -> tuple[dict | None, int, str | None]:
    batch_args = {
        "symbols": args.get("symbols") or challenger_symbol or "ETHUSDT,BTCUSDT,SOLUSDT",
        "timeframes": args.get("timeframes") or challenger_timeframe or "1h,4h",
        "strategies": args.get("strategies") or challenger_strategy or "auto",
        "period": args.get("period", "365d"),
        "maxCandidates": args.get("maxCandidates") or args.get("max_candidates") or "60",
        "maxCombosPerStrategy": args.get("maxCombosPerStrategy") or args.get("max_combos_per_strategy") or "50",
        "topN": args.get("topN") or args.get("top_n") or "20",
        "includeWalkForward": args.get("includeWalkForward", "false"),
        "includeStress": args.get("includeStress", "false"),
        "save": "false",
        "limit": args.get("limit", "auto"),
        "timeout_seconds": args.get("optimizer_timeout_seconds", args.get("timeout_seconds", "900")),
    }
    payload, status = build_research_multi_strategy_optimizer_batch(batch_args)
    if not payload.get("ok"):
        return payload, status, payload.get("error") or "Optimizer batch did not complete for challenger selection."
    return payload, status, None


def _replacement_eligibility_from_deep_compare(payload: dict, selected_row: dict | None) -> dict:
    challenger_activity = ((payload.get("evidence") or {}).get("activity") or {}).get("challenger") or {}
    comparison = payload.get("comparison") or {}
    rules = {
        "minTrades": 40,
        "minProfitFactor": 1.1,
        "minTotalReturnPct": 0,
        "maxDrawdownPct": 25,
        "minScoreDiff": 10,
    }
    reasons = []
    trades = safe_float(challenger_activity.get("trades"), safe_float((selected_row or {}).get("trades"), 0))
    profit_factor = safe_float(challenger_activity.get("profitFactor"), safe_float((selected_row or {}).get("profitFactor"), 0))
    total_return = safe_float(challenger_activity.get("totalReturnPct"), safe_float((selected_row or {}).get("totalReturnPct"), 0))
    max_dd = safe_float(challenger_activity.get("maxDrawdownPct"), safe_float((selected_row or {}).get("maxDrawdownPct"), 0))
    score_diff = safe_float(comparison.get("scoreDiff"), 0)
    if trades < rules["minTrades"]:
        reasons.append("LOW_TRADE_COUNT")
    if profit_factor < rules["minProfitFactor"]:
        reasons.append("WEAK_PROFIT_FACTOR")
    if total_return <= rules["minTotalReturnPct"]:
        reasons.append("NON_POSITIVE_RETURN")
    if max_dd > rules["maxDrawdownPct"]:
        reasons.append("HIGH_DRAWDOWN")
    if score_diff < rules["minScoreDiff"]:
        reasons.append("INSUFFICIENT_SCORE_EDGE")
    if selected_row and selected_row.get("replacementRejectionReasons"):
        reasons.extend(str(reason).upper() for reason in selected_row.get("replacementRejectionReasons") or [])
    reasons = dedupe_list(reasons)
    return {"eligible": len(reasons) == 0, "reasons": reasons, "rules": rules}


def _candidate_deep_compare_research_verdict(payload: dict, eligibility: dict) -> dict:
    challenger_activity = ((payload.get("evidence") or {}).get("activity") or {}).get("challenger") or {}
    comparison = payload.get("comparison") or {}
    if eligibility.get("eligible"):
        return {"action": "REVIEW_CHALLENGER", "reason": "The challenger clears replacement eligibility for research review only. No promotion is performed."}
    trades = safe_float(challenger_activity.get("trades"), 0)
    profit_factor = safe_float(challenger_activity.get("profitFactor"), 0)
    total_return = safe_float(challenger_activity.get("totalReturnPct"), 0)
    if "LOW_TRADE_COUNT" in (eligibility.get("reasons") or []) and total_return > 0 and profit_factor >= 1.25:
        return {"action": "RESEARCH_CHALLENGER_MORE", "reason": f"Challenger metrics are promising, but {int(trades)} trades is below the 40-trade replacement evidence floor."}
    if comparison.get("winner") == "BASELINE":
        return {"action": "KEEP_BASELINE", "reason": "The active baseline remains the safer research candidate after deep comparison and replacement gates."}
    return {"action": "NO_ACTION", "reason": "The challenger does not clear conservative replacement research gates."}


def build_research_candidate_deep_compare(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    baseline_symbol = (args.get("baselineSymbol") or active.get("symbol") or "ETHUSDT").strip()
    baseline_timeframe = (args.get("baselineTimeframe") or args.get("baselineInterval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    baseline_strategy = (args.get("baselineStrategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    challenger_symbol = (args.get("challengerSymbol") or "ETHUSDT").strip()
    challenger_timeframe = (args.get("challengerTimeframe") or "4h").strip()
    challenger_strategy = (args.get("challengerStrategy") or "SimpleAtrTrendV2").strip()
    challenger_preset = args.get("challengerPreset", "swing_native_4h_1")
    challenger_source = str(args.get("challengerSource", "preset")).strip() or "preset"
    challenger_source = challenger_source if challenger_source in {"preset", "optimizerBatch", "inline"} else "preset"
    challenger_rank = max(1, int(safe_float(args.get("challengerRank"), 1)))
    period = args.get("period", "365d")
    include_details = str(args.get("includeDetails", args.get("include_details", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("feePct"), safe_float(candidate.get("takerFeePct"), 0.055))
    slippage_pct = safe_float(candidate.get("slippagePct"), safe_float(candidate.get("slippageBps"), 2) / 100)
    challenger_params = None
    selected_row = None
    selected_source = {
        "source": challenger_source,
        "batchFile": None,
        "rank": challenger_rank,
        "strategy": challenger_strategy,
        "symbol": challenger_symbol,
        "timeframe": challenger_timeframe,
        "paramsSource": "preset" if challenger_source == "preset" else None,
    }
    preflight_warnings = []
    if challenger_source == "inline":
        raw_params = args.get("challengerParams")
        if not raw_params:
            return {"ok": False, "error": "challengerParams is required when challengerSource=inline.", "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled}, 400
        try:
            challenger_params = json.loads(raw_params)
        except Exception as exc:
            return {"ok": False, "error": f"Could not parse challengerParams JSON: {exc}", "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled}, 400
        if not isinstance(challenger_params, dict):
            return {"ok": False, "error": "challengerParams must decode to a JSON object.", "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled}, 400
        selected_source["paramsSource"] = "inline"
    elif challenger_source == "optimizerBatch":
        batch_payload = None
        batch_file = args.get("batchFile")
        if batch_file:
            batch_payload, batch_error = _load_candidate_deep_compare_batch_file(str(batch_file))
            if batch_error:
                return {"ok": False, "error": batch_error, "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled}, 400
            selected_source["batchFile"] = batch_payload.get("_resolvedBatchFile")
        else:
            batch_payload, batch_status, batch_error = _run_optimizer_batch_for_deep_compare(
                args,
                (args.get("challengerStrategy") or "").strip(),
                (args.get("challengerSymbol") or "").strip(),
                (args.get("challengerTimeframe") or args.get("challengerInterval") or "").strip(),
            )
            if batch_error:
                return {"ok": False, "error": batch_error, "optimizerBatch": batch_payload, "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled}, batch_status
            selected_source["batchFile"] = None
        selected_row, select_error = _select_optimizer_batch_challenger(batch_payload, args, challenger_rank)
        if select_error:
            return {"ok": False, "error": select_error, "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled, "optimizerBatch": {"summary": (batch_payload or {}).get("summary")}}, 400
        challenger_symbol = str(selected_row.get("symbol") or challenger_symbol)
        challenger_timeframe = str(selected_row.get("timeframe") or selected_row.get("interval") or challenger_timeframe)
        challenger_strategy = str(selected_row.get("strategy") or challenger_strategy)
        challenger_params = dict(selected_row.get("params") if isinstance(selected_row.get("params"), dict) else {})
        selected_source.update({
            "rank": selected_row.get("rank") or challenger_rank,
            "strategy": challenger_strategy,
            "symbol": challenger_symbol,
            "timeframe": challenger_timeframe,
            "paramsSource": selected_row.get("paramsSource") or "optimizerBatch",
        })
        if safe_float(selected_row.get("trades"), 0) < 40:
            preflight_warnings.append("LOW_TRADE_COUNT")
    command = package_node_script_args("research:candidate-deep-compare")
    command.extend([
        "--baselineSymbol", baseline_symbol,
        "--baselineTimeframe", baseline_timeframe,
        "--baselineStrategy", baseline_strategy,
        "--challengerSymbol", challenger_symbol,
        "--challengerTimeframe", challenger_timeframe,
        "--challengerStrategy", challenger_strategy,
        "--challengerPreset", str(challenger_preset),
        "--period", period,
        "--includeDetails", "true" if include_details else "false",
        "--baseParams", json.dumps(params),
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
    ])
    if challenger_params is not None:
        command.extend(["--challengerParams", json.dumps(challenger_params), "--challengerParamsSource", str(selected_source.get("paramsSource") or challenger_source)])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 360), 360)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Candidate deep compare timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "activeCandidate": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Candidate deep compare timed out before returning evidence."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Candidate deep compare returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Candidate deep compare returned no output."}
    eligibility = _replacement_eligibility_from_deep_compare(payload, selected_row)
    research_verdict = _candidate_deep_compare_research_verdict(payload, eligibility)
    if "LOW_TRADE_COUNT" in (eligibility.get("reasons") or []) and "LOW_TRADE_COUNT" not in preflight_warnings:
        preflight_warnings.append("LOW_TRADE_COUNT")
    warnings = dedupe_list(preflight_warnings + (payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Candidate deep compare command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "activeCandidate": candidate_summary(candidate),
        "search": payload.get("search") or {"period": period, "includeDetails": include_details},
        "baseline": payload.get("baseline") or {},
        "challenger": payload.get("challenger") or {},
        "comparison": payload.get("comparison") or {},
        "evidence": payload.get("evidence") or {},
        "recommendation": payload.get("recommendation") or {},
        "selectedChallengerSource": selected_source,
        "replacementEligibility": eligibility,
        "researchVerdict": research_verdict,
        "warnings": warnings,
        "availablePresets": payload.get("availablePresets"),
        "error": payload.get("error"),
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 400 if payload.get("error") == "Unknown challengerPreset." else 502


def build_research_multi_strategy_matrix(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_symbol = (active.get("symbol") or "ETHUSDT").strip()
    active_timeframe = (active.get("interval") or active.get("timeframe") or "1h").strip()
    active_strategy = (candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    symbols = args.get("symbols", "ETHUSDT,BTCUSDT,SOLUSDT")
    timeframes = args.get("timeframes", "1h,4h")
    strategies = args.get("strategies", "auto")
    period = args.get("period", "365d")
    mode = args.get("mode", "current_or_default_params")
    max_rows = max(1, min(int(safe_float(args.get("maxRows", args.get("max_rows", 100)), 100)), 100))
    include_stress = str(args.get("includeStress", args.get("include_stress", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    include_walk = str(args.get("includeWalkForward", args.get("include_walk_forward", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("feePct"), safe_float(candidate.get("takerFeePct"), 0.055))
    slippage_pct = safe_float(candidate.get("slippagePct"), safe_float(candidate.get("slippageBps"), 2) / 100)
    promoted = candidate.get("promotedFromOptimization") if isinstance(candidate.get("promotedFromOptimization"), dict) else {}
    quality = promoted.get("qualityMetrics") if isinstance(promoted.get("qualityMetrics"), dict) else {}
    ranking = candidate.get("promotedFromRanking") if isinstance(candidate.get("promotedFromRanking"), dict) else {}
    command = package_node_script_args("research:multi-strategy-matrix")
    command.extend([
        "--symbols", str(symbols),
        "--timeframes", str(timeframes),
        "--strategies", str(strategies),
        "--period", period,
        "--mode", mode,
        "--maxRows", str(max_rows),
        "--includeStress", "true" if include_stress else "false",
        "--includeWalkForward", "true" if include_walk else "false",
        "--activeSymbol", active_symbol,
        "--activeTimeframe", active_timeframe,
        "--activeStrategy", active_strategy,
        "--activeParams", json.dumps(params),
        "--activeBaselineTrades", str(quality.get("fullTrades") or ranking.get("trades") or 0),
        "--activeBaselineReturnPct", str(quality.get("fullReturnPct") or ranking.get("totalReturnPct") or 0),
        "--activeBaselineProfitFactor", str(quality.get("fullProfitFactor") or ranking.get("profitFactor") or 0),
        "--activeBaselineMaxDrawdownPct", str(quality.get("fullMaxDrawdownPct") or ranking.get("maxDrawdownPct") or ranking.get("maxDrawdown") or 0),
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 420), 420)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Multi-strategy matrix timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "activeCandidate": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Multi-strategy matrix timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Multi-strategy matrix returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Multi-strategy matrix returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Multi-strategy matrix command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "activeCandidate": candidate_summary(candidate),
        "search": payload.get("search") or {"symbols": symbols, "timeframes": timeframes, "strategies": strategies, "period": period, "mode": mode, "maxRows": max_rows},
        "discoveredStrategies": payload.get("discoveredStrategies") or [],
        "rows": payload.get("rows") or [],
        "summary": payload.get("summary") or {},
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_multi_strategy_optimizer_batch(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_symbol = (active.get("symbol") or "ETHUSDT").strip()
    active_timeframe = (active.get("interval") or active.get("timeframe") or "1h").strip()
    active_strategy = (candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    symbols = args.get("symbols", "ETHUSDT,BTCUSDT,SOLUSDT")
    timeframes = args.get("timeframes", "1h,4h")
    strategies = args.get("strategies", "auto")
    period = args.get("period", "365d")
    max_candidates = max(1, min(int(safe_float(args.get("maxCandidates", args.get("max_candidates", 100)), 100)), 200))
    max_combos = max(1, min(int(safe_float(args.get("maxCombosPerStrategy", args.get("max_combos_per_strategy", 50)), 50)), 250))
    top_n = max(1, min(int(safe_float(args.get("topN", args.get("top_n", 20)), 20)), 100))
    include_walk = str(args.get("includeWalkForward", args.get("include_walk_forward", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    include_stress = str(args.get("includeStress", args.get("include_stress", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    include_repro = str(args.get("includeReproAudit", args.get("include_repro_audit", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    repro_top_n = max(1, min(int(safe_float(args.get("reproTopN", args.get("repro_top_n", 5)), 5)), 20))
    repro_reruns = max(1, min(int(safe_float(args.get("reproReruns", args.get("repro_reruns", 1)), 1)), 5))
    require_repro = str(args.get("requireReproducible", args.get("require_reproducible", "false"))).strip().lower() in {"1", "true", "yes", "on"}
    save = str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"}
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("feePct"), safe_float(candidate.get("takerFeePct"), 0.055))
    slippage_pct = safe_float(candidate.get("slippagePct"), safe_float(candidate.get("slippageBps"), 2) / 100)
    promoted = candidate.get("promotedFromOptimization") if isinstance(candidate.get("promotedFromOptimization"), dict) else {}
    quality = promoted.get("qualityMetrics") if isinstance(promoted.get("qualityMetrics"), dict) else {}
    ranking = candidate.get("promotedFromRanking") if isinstance(candidate.get("promotedFromRanking"), dict) else {}
    command = package_node_script_args("research:multi-strategy-optimizer-batch")
    command.extend([
        "--symbols", str(symbols),
        "--timeframes", str(timeframes),
        "--strategies", str(strategies),
        "--period", period,
        "--maxCandidates", str(max_candidates),
        "--maxCombosPerStrategy", str(max_combos),
        "--topN", str(top_n),
        "--includeWalkForward", "true" if include_walk else "false",
        "--includeStress", "true" if include_stress else "false",
        "--includeReproAudit", "true" if include_repro else "false",
        "--reproTopN", str(repro_top_n),
        "--reproReruns", str(repro_reruns),
        "--requireReproducible", "true" if require_repro else "false",
        "--save", "true" if save else "false",
        "--activeSymbol", active_symbol,
        "--activeTimeframe", active_timeframe,
        "--activeStrategy", active_strategy,
        "--activeParams", json.dumps(params),
        "--activeBaselineTrades", str(quality.get("fullTrades") or ranking.get("trades") or 0),
        "--activeBaselineReturnPct", str(quality.get("fullReturnPct") or ranking.get("totalReturnPct") or 0),
        "--activeBaselineProfitFactor", str(quality.get("fullProfitFactor") or ranking.get("profitFactor") or 0),
        "--activeBaselineMaxDrawdownPct", str(quality.get("fullMaxDrawdownPct") or ranking.get("maxDrawdownPct") or ranking.get("maxDrawdown") or 0),
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 900), 900)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Multi-strategy optimizer batch timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "activeBaseline": candidate_summary(candidate),
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Multi-strategy optimizer batch timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Multi-strategy optimizer batch returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Multi-strategy optimizer batch returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Multi-strategy optimizer batch command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "activeBaseline": payload.get("activeBaseline") or candidate_summary(candidate),
        "search": payload.get("search") or {"symbols": symbols, "timeframes": timeframes, "strategies": strategies, "period": period, "maxCandidates": max_candidates, "maxCombosPerStrategy": max_combos, "topN": top_n, "includeReproAudit": include_repro, "reproTopN": repro_top_n, "reproReruns": repro_reruns, "requireReproducible": require_repro},
        "discoveredStrategies": payload.get("discoveredStrategies") or [],
        "skippedStrategies": payload.get("skippedStrategies") or [],
        "rows": payload.get("rows") or [],
        "summary": payload.get("summary") or {},
        "warnings": warnings,
        "savedPath": payload.get("savedPath"),
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def _optimizer_audit_candidates_from_batch(batch_payload: dict, top_n: int) -> list[dict]:
    rows = [row for row in (batch_payload.get("rows") or []) if isinstance(row, dict) and not row.get("isActiveBaseline")]
    rows = sorted(rows, key=lambda row: int(safe_float(row.get("rank"), 999999)))
    candidates = []
    for row in rows[:top_n]:
        candidates.append({
            "strategy": row.get("strategy"),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe") or row.get("interval"),
            "params": row.get("params") if isinstance(row.get("params"), dict) else {},
            "status": row.get("status") or row.get("qualityStatus"),
            "trades": row.get("trades"),
            "totalReturnPct": row.get("totalReturnPct") or row.get("totalReturn"),
            "profitFactor": row.get("profitFactor"),
            "maxDrawdownPct": row.get("maxDrawdownPct") or row.get("maxDrawdown"),
            "winRate": row.get("winRate"),
            "score": row.get("score") or row.get("practicalScore"),
            "mainFailureReason": row.get("mainFailureReason"),
        })
    return [candidate for candidate in candidates if candidate.get("strategy") and candidate.get("symbol") and candidate.get("timeframe")]


def _save_optimizer_reproducibility_audit(payload: dict) -> str:
    reports_dir = Path(app.root_path) / "reports" / "research-audits"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = reports_dir / f"optimizer-reproducibility-audit-{stamp}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return str(path.relative_to(Path(app.root_path))).replace("\\", "/")


def build_research_optimizer_reproducibility_audit(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    symbols = args.get("symbols", "ETHUSDT,BTCUSDT,SOLUSDT")
    timeframes = args.get("timeframes", "1h,4h")
    strategies = args.get("strategies", "auto")
    period = args.get("period", "365d")
    top_n = max(1, min(int(safe_float(args.get("topN", args.get("top_n", 10)), 10)), 25))
    max_combos = max(1, min(int(safe_float(args.get("maxCombosPerStrategy", args.get("max_combos_per_strategy", 25)), 25)), 250))
    reruns = max(1, min(int(safe_float(args.get("reruns", 2), 2)), 5))
    save = str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"}
    fee_pct = safe_float(candidate.get("feePct"), safe_float(candidate.get("takerFeePct"), 0.055))
    slippage_pct = safe_float(candidate.get("slippagePct"), safe_float(candidate.get("slippageBps"), 2) / 100)
    warnings = []
    batch_payload = None
    generated_batch = False
    batch_file = args.get("batchFile")
    if batch_file:
        batch_payload, batch_error = _load_candidate_deep_compare_batch_file(str(batch_file))
        if batch_error:
            return {"ok": False, "error": batch_error, "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled}, 400
        resolved_batch_file = batch_payload.get("_resolvedBatchFile")
    else:
        generated_batch = True
        batch_args = {
            "symbols": symbols,
            "timeframes": timeframes,
            "strategies": strategies,
            "period": period,
            "maxCandidates": args.get("maxCandidates") or args.get("max_candidates") or "60",
            "maxCombosPerStrategy": str(max_combos),
            "topN": str(max(top_n, 10)),
            "includeWalkForward": "false",
            "includeStress": "false",
            "save": "false",
            "limit": args.get("limit", "auto"),
            "timeout_seconds": args.get("optimizer_timeout_seconds", args.get("timeout_seconds", "900")),
        }
        batch_payload, batch_status = build_research_multi_strategy_optimizer_batch(batch_args)
        if not batch_payload.get("ok"):
            return {
                "ok": False,
                "error": batch_payload.get("error") or "Could not generate optimizer batch for reproducibility audit.",
                "paperEnabled": paper_enabled,
                "realTradingEnabled": real_enabled,
                "optimizerBatch": batch_payload,
            }, batch_status
        resolved_batch_file = None
    candidates = _optimizer_audit_candidates_from_batch(batch_payload or {}, top_n)
    if not candidates:
        return {
            "ok": False,
            "error": "No optimizer candidates were available for reproducibility audit.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "source": {"batchFile": resolved_batch_file, "generatedBatch": generated_batch, "topN": top_n, "reruns": reruns},
        }, 400
    command = package_node_script_args("research:optimizer-reproducibility-audit")
    command.extend([
        "--candidates", json.dumps(candidates),
        "--period", period,
        "--reruns", str(reruns),
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("audit_timeout_seconds", args.get("timeout_seconds", 600)), 600)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Optimizer reproducibility audit timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "source": {"batchFile": resolved_batch_file, "generatedBatch": generated_batch, "topN": top_n, "reruns": reruns},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Optimizer reproducibility audit timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Optimizer reproducibility audit returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Optimizer reproducibility audit returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Optimizer reproducibility audit command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "source": {
            "batchFile": resolved_batch_file,
            "generatedBatch": generated_batch,
            "topN": top_n,
            "reruns": reruns,
            "symbols": symbols,
            "timeframes": timeframes,
            "strategies": strategies,
            "period": period,
            "maxCombosPerStrategy": max_combos,
        },
        "tolerances": payload.get("tolerances") or {},
        "rows": payload.get("rows") or [],
        "summary": payload.get("summary") or {},
        "warnings": warnings,
        "error": payload.get("error"),
        "command": " ".join(command[:1] + ["cli/research_optimizer_reproducibility_audit.js", "--candidates", "[selected-candidates-json]", "--period", period, "--reruns", str(reruns)]),
    }
    if save and response["ok"]:
        response["savedPath"] = _save_optimizer_reproducibility_audit(response)
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def _drilldown_candidates_from_batch(batch_payload: dict, top_n: int) -> list[dict]:
    rows = [row for row in (batch_payload.get("rows") or []) if isinstance(row, dict) and not row.get("isActiveBaseline")]
    selected = [
        row for row in rows
        if row.get("finalCandidateTier") == "REPRODUCIBLE"
        or row.get("qualityGateStatus") in {"REPRODUCIBLE", "WATCH"}
        or row.get("reproducibilityStatus") in {"REPRODUCIBLE", "WATCH"}
    ]
    selected = sorted(selected, key=lambda row: (
        0 if row.get("finalCandidateTier") == "REPLACEMENT_ELIGIBLE" else 1,
        int(safe_float(row.get("rawRank") or row.get("rank"), 999999)),
    ))
    candidates = []
    for row in selected[:top_n]:
        candidates.append({
            "strategy": row.get("strategy"),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe") or row.get("interval"),
            "params": row.get("params") if isinstance(row.get("params"), dict) else {},
            "reproducibilityStatus": row.get("reproducibilityStatus") or "NOT_CHECKED",
            "qualityGateStatus": row.get("qualityGateStatus") or "RAW_ONLY",
            "finalCandidateTier": row.get("finalCandidateTier") or "RAW",
            "rawRank": row.get("rawRank") or row.get("rank"),
            "practicalRank": row.get("practicalRank"),
            "score": row.get("score"),
            "practicalScore": row.get("practicalScore"),
        })
    return [candidate for candidate in candidates if candidate.get("strategy") and candidate.get("symbol") and candidate.get("timeframe")]


def _save_reproducible_candidate_drilldown(payload: dict) -> str:
    reports_dir = Path(app.root_path) / "reports" / "research-drilldowns"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = reports_dir / f"reproducible-candidate-drilldown-{stamp}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return str(path.relative_to(Path(app.root_path))).replace("\\", "/")


def _load_research_drilldown_stdout(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                payload, end = decoder.raw_decode(text[index:])
            except Exception:
                continue
            if text[index + end:].strip():
                continue
            if isinstance(payload, dict):
                return payload
    return {"ok": False, "error": "Reproducible candidate drilldown returned non-JSON output.", "stdout": text}


def build_research_reproducible_candidate_drilldown(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_symbol = (active.get("symbol") or "ETHUSDT").strip()
    active_timeframe = (active.get("interval") or active.get("timeframe") or "1h").strip()
    active_strategy = (candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    active_params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    symbols = args.get("symbols", "ETHUSDT,BTCUSDT,SOLUSDT")
    timeframes = args.get("timeframes", "1h,4h")
    strategies = args.get("strategies", "auto")
    period = args.get("period", "365d")
    max_combos = max(1, min(int(safe_float(args.get("maxCombosPerStrategy", args.get("max_combos_per_strategy", 25)), 25)), 250))
    repro_top_n = max(1, min(int(safe_float(args.get("reproTopN", args.get("repro_top_n", 5)), 5)), 20))
    repro_reruns = max(1, min(int(safe_float(args.get("reproReruns", args.get("repro_reruns", 1)), 1)), 5))
    top_n = max(1, min(int(safe_float(args.get("topN", args.get("top_n", 5)), 5)), 20))
    include_stress = str(args.get("includeStress", args.get("include_stress", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    include_walk = str(args.get("includeWalkForward", args.get("include_walk_forward", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    include_regime = str(args.get("includeRegime", args.get("include_regime", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    save = str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"}
    fee_pct = safe_float(candidate.get("feePct"), safe_float(candidate.get("takerFeePct"), 0.055))
    slippage_pct = safe_float(candidate.get("slippagePct"), safe_float(candidate.get("slippageBps"), 2) / 100)
    batch_payload = None
    generated_batch = False
    batch_file = args.get("batchFile")
    warnings = []
    if batch_file:
        batch_payload, batch_error = _load_candidate_deep_compare_batch_file(str(batch_file))
        if batch_error:
            return {"ok": False, "error": batch_error, "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled}, 400
        resolved_batch_file = batch_payload.get("_resolvedBatchFile")
    else:
        generated_batch = True
        batch_args = {
            "symbols": symbols,
            "timeframes": timeframes,
            "strategies": strategies,
            "period": period,
            "maxCandidates": args.get("maxCandidates") or args.get("max_candidates") or "100",
            "maxCombosPerStrategy": str(max_combos),
            "topN": str(max(top_n, repro_top_n, 10)),
            "includeReproAudit": "true",
            "reproTopN": str(repro_top_n),
            "reproReruns": str(repro_reruns),
            "includeWalkForward": "false",
            "includeStress": "false",
            "save": "false",
            "limit": args.get("limit", "auto"),
            "timeout_seconds": args.get("optimizer_timeout_seconds", args.get("timeout_seconds", "900")),
        }
        batch_payload, batch_status = build_research_multi_strategy_optimizer_batch(batch_args)
        if not batch_payload.get("ok"):
            return {
                "ok": False,
                "error": batch_payload.get("error") or "Could not generate optimizer batch for reproducible candidate drilldown.",
                "paperEnabled": paper_enabled,
                "realTradingEnabled": real_enabled,
                "optimizerBatch": batch_payload,
            }, batch_status
        warnings.extend(batch_payload.get("warnings") or [])
        resolved_batch_file = None
    selected = _drilldown_candidates_from_batch(batch_payload or {}, top_n)
    source = {
        "batchFile": resolved_batch_file,
        "generatedBatch": generated_batch,
        "selectedCount": len(selected),
        "symbols": symbols,
        "timeframes": timeframes,
        "strategies": strategies,
        "period": period,
        "maxCombosPerStrategy": max_combos,
        "reproTopN": repro_top_n,
        "reproReruns": repro_reruns,
        "topN": top_n,
    }
    if not selected:
        response = {
            "ok": True,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "source": source,
            "activeBaseline": candidate_summary(candidate),
            "candidates": [],
            "summary": {
                "selectedCount": 0,
                "reviewForPromotionCount": 0,
                "researchMoreCount": 0,
                "watchCount": 0,
                "discardCount": 0,
                "bestCandidate": None,
                "recommendation": {"action": "NO_ACTION", "reason": "No reproducible/watch optimizer candidates were available for drilldown."},
            },
            "warnings": dedupe_list(warnings + ["No promotion, paper tick, config write, or real trading action was performed."] + ([real_detail] if real_enabled else [])),
        }
        if save:
            response["savedPath"] = _save_reproducible_candidate_drilldown(response)
        return response, 200
    command = package_node_script_args("research:reproducible-candidate-drilldown")
    command.extend([
        "--candidates", json.dumps(selected),
        "--activeBaseline", json.dumps({"strategy": active_strategy, "symbol": active_symbol, "timeframe": active_timeframe, "params": active_params}),
        "--period", period,
        "--includeStress", "true" if include_stress else "false",
        "--includeWalkForward", "true" if include_walk else "false",
        "--includeRegime", "true" if include_regime else "false",
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("drilldown_timeout_seconds", args.get("timeout_seconds", 720)), 720)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Reproducible candidate drilldown timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "source": source,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Reproducible candidate drilldown timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        payload = _load_research_drilldown_stdout(completed.stdout)
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Reproducible candidate drilldown returned no output."}
    combined_warnings = dedupe_list(warnings + (payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        combined_warnings.append(completed.stderr.strip() or "Reproducible candidate drilldown command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "source": source,
        "activeBaseline": payload.get("activeBaseline") or candidate_summary(candidate),
        "candidates": payload.get("candidates") or [],
        "summary": payload.get("summary") or {},
        "warnings": combined_warnings,
        "error": payload.get("error"),
        "command": " ".join(command[:1] + ["cli/research_reproducible_candidate_drilldown.js", "--candidates", "[selected-candidates-json]", "--period", period]),
    }
    if save and response["ok"]:
        response["savedPath"] = _save_reproducible_candidate_drilldown(response)
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def _save_research_lead_review(payload: dict) -> str:
    reports_dir = Path(app.root_path) / "reports" / "research-leads"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = reports_dir / f"research-lead-review-{stamp}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return str(path.relative_to(Path(app.root_path))).replace("\\", "/")


def _resolve_research_lead_params(args, lead_strategy: str, lead_symbol: str, lead_timeframe: str, period: str) -> tuple[dict | None, str, dict | None, list[str], int]:
    raw_params = args.get("leadParams")
    if raw_params:
        try:
            params = json.loads(raw_params)
        except Exception as exc:
            return None, "inline", {"ok": False, "error": f"Could not parse leadParams JSON: {exc}"}, [], 400
        if not isinstance(params, dict):
            return None, "inline", {"ok": False, "error": "leadParams must decode to a JSON object."}, [], 400
        return params, "inline", None, [], 200
    drilldown_args = {
        "symbols": lead_symbol,
        "timeframes": lead_timeframe,
        "strategies": lead_strategy,
        "period": period,
        "maxCombosPerStrategy": args.get("maxCombosPerStrategy", args.get("max_combos_per_strategy", 25)),
        "reproTopN": args.get("reproTopN", args.get("repro_top_n", 5)),
        "reproReruns": args.get("reproReruns", args.get("repro_reruns", 1)),
        "topN": args.get("topN", args.get("top_n", 5)),
        "includeStress": "false",
        "includeWalkForward": "false",
        "includeRegime": "false",
        "save": "false",
        "limit": args.get("limit", "auto"),
        "timeout_seconds": args.get("resolve_timeout_seconds", args.get("timeout_seconds", 900)),
    }
    if args.get("batchFile"):
        drilldown_args["batchFile"] = args.get("batchFile")
    drilldown_payload, drilldown_status = build_research_reproducible_candidate_drilldown(drilldown_args)
    warnings = drilldown_payload.get("warnings") or []
    if not drilldown_payload.get("ok"):
        return None, "unresolved", drilldown_payload, warnings, drilldown_status
    for row in drilldown_payload.get("candidates") or []:
        if (
            str(row.get("strategy")) == lead_strategy
            and str(row.get("symbol")) == lead_symbol
            and str(row.get("timeframe")) == lead_timeframe
            and isinstance(row.get("params"), dict)
            and row.get("params")
        ):
            return dict(row.get("params")), "optimizerDrilldown", drilldown_payload, warnings, 200
    return None, "unresolved", drilldown_payload, warnings, 400


def build_research_lead_review(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    lead_strategy = (args.get("leadStrategy") or args.get("lead_strategy") or "RelativeStrengthV2").strip()
    lead_symbol = (args.get("leadSymbol") or args.get("lead_symbol") or "ETHUSDT").strip()
    lead_timeframe = (args.get("leadTimeframe") or args.get("lead_timeframe") or args.get("leadInterval") or "4h").strip()
    baseline_strategy = (args.get("baselineStrategy") or args.get("baseline_strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    baseline_symbol = (args.get("baselineSymbol") or args.get("baseline_symbol") or active.get("symbol") or "ETHUSDT").strip()
    baseline_timeframe = (args.get("baselineTimeframe") or args.get("baseline_timeframe") or args.get("baselineInterval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    period = args.get("period", "365d")
    include_robustness = str(args.get("includeRobustness", args.get("include_robustness", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    include_stress = str(args.get("includeStress", args.get("include_stress", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    include_walk = str(args.get("includeWalkForward", args.get("include_walk_forward", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    include_regime = str(args.get("includeRegime", args.get("include_regime", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    include_deep = str(args.get("includeDeepCompare", args.get("include_deep_compare", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    save = str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"}
    baseline_params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    fee_pct = safe_float(candidate.get("feePct"), safe_float(candidate.get("takerFeePct"), 0.055))
    slippage_pct = safe_float(candidate.get("slippagePct"), safe_float(candidate.get("slippageBps"), 2) / 100)
    lead_params, params_source, resolver_payload, resolver_warnings, resolver_status = _resolve_research_lead_params(args, lead_strategy, lead_symbol, lead_timeframe, period)
    if lead_params is None:
        return {
            "ok": False,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "baseline": {"strategy": baseline_strategy, "symbol": baseline_symbol, "timeframe": baseline_timeframe, "params": baseline_params},
            "lead": {"strategy": lead_strategy, "symbol": lead_symbol, "timeframe": lead_timeframe, "params": None, "paramsSource": params_source},
            "error": "Could not resolve lead params. Pass leadParams as a JSON query parameter or provide a batchFile containing the lead row.",
            "resolver": {"summary": (resolver_payload or {}).get("summary"), "source": (resolver_payload or {}).get("source")},
            "warnings": dedupe_list(resolver_warnings + ([real_detail] if real_enabled else [])),
        }, resolver_status
    command = package_node_script_args("research:lead-review")
    command.extend([
        "--leadStrategy", lead_strategy,
        "--leadSymbol", lead_symbol,
        "--leadTimeframe", lead_timeframe,
        "--leadParams", json.dumps(lead_params),
        "--baselineStrategy", baseline_strategy,
        "--baselineSymbol", baseline_symbol,
        "--baselineTimeframe", baseline_timeframe,
        "--baselineParams", json.dumps(baseline_params),
        "--period", period,
        "--includeRobustness", "true" if include_robustness else "false",
        "--includeStress", "true" if include_stress else "false",
        "--includeWalkForward", "true" if include_walk else "false",
        "--includeRegime", "true" if include_regime else "false",
        "--includeDeepCompare", "true" if include_deep else "false",
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("review_timeout_seconds", args.get("timeout_seconds", 720)), 720)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Research lead review timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "baseline": {"strategy": baseline_strategy, "symbol": baseline_symbol, "timeframe": baseline_timeframe},
            "lead": {"strategy": lead_strategy, "symbol": lead_symbol, "timeframe": lead_timeframe, "paramsSource": params_source},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Research lead review timed out before returning evidence."],
        }, 504
    payload = None
    if completed.stdout.strip():
        payload = _load_research_drilldown_stdout(completed.stdout)
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Research lead review returned no output."}
    warnings = dedupe_list(resolver_warnings + (payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Research lead review command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "baseline": payload.get("baseline") or {"strategy": baseline_strategy, "symbol": baseline_symbol, "timeframe": baseline_timeframe, "params": baseline_params},
        "lead": dict(payload.get("lead") or {"strategy": lead_strategy, "symbol": lead_symbol, "timeframe": lead_timeframe, "params": lead_params}),
        "evidence": payload.get("evidence") or {},
        "comparison": payload.get("comparison") or {},
        "replacementEligibility": payload.get("replacementEligibility") or {},
        "verdict": payload.get("verdict") or {},
        "warnings": warnings,
        "error": payload.get("error"),
        "search": {
            "leadStrategy": lead_strategy,
            "leadSymbol": lead_symbol,
            "leadTimeframe": lead_timeframe,
            "baselineStrategy": baseline_strategy,
            "baselineSymbol": baseline_symbol,
            "baselineTimeframe": baseline_timeframe,
            "period": period,
            "includeRobustness": include_robustness,
            "includeStress": include_stress,
            "includeWalkForward": include_walk,
            "includeRegime": include_regime,
            "includeDeepCompare": include_deep,
        },
        "resolver": {"source": (resolver_payload or {}).get("source"), "summary": (resolver_payload or {}).get("summary")},
        "command": " ".join(command[:1] + ["cli/research_lead_review.js", "--leadParams", "[lead-params-json]", "--period", period]),
    }
    response["lead"]["paramsSource"] = params_source
    if save and response["ok"]:
        response["savedPath"] = _save_research_lead_review(response)
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_snapshot_export(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    output_format = str(args.get("format", "json")).strip().lower()
    if output_format not in {"json", "markdown"}:
        return {"ok": False, "error": "format must be json or markdown."}, 400
    save = str(args.get("save", "false")).strip().lower() in {"1", "true", "yes", "on"}
    include_details = str(args.get("includeDetails", args.get("include_details", "true"))).strip().lower() not in {"0", "false", "no", "off"}
    log_file = args.get("logFile")
    base_args = {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": strategy,
        "period": period,
        "limit": args.get("limit", "auto"),
        "timeout_seconds": args.get("timeout_seconds", "240"),
    }
    evidence = {}
    warnings = []

    def capture(name: str, builder, section_args: dict, compact):
        try:
            payload, status_code = builder(section_args)
            section = compact(payload)
            section["ok"] = payload.get("ok", True) is not False and status_code < 400
            if status_code >= 400 or payload.get("ok") is False:
                warnings.append(f"{name} returned status {status_code}; snapshot includes partial evidence.")
            warnings.extend(payload.get("warnings") or [])
            return section
        except Exception as exc:
            warnings.append(f"{name} failed: {exc}")
            return {"ok": False, "error": str(exc)}

    evidence["candidateCurrent"] = compact_snapshot_candidate(candidate, symbol, timeframe, strategy)
    evidence["candidateReview"] = capture(
        "candidate review report",
        build_research_candidate_review_report,
        {**base_args, "logFile": log_file} if log_file else dict(base_args),
        compact_snapshot_candidate_review,
    )
    evidence["leaderboard"] = capture(
        "research candidate leaderboard",
        build_research_candidate_leaderboard,
        {
            "symbols": args.get("leaderboardSymbols", args.get("symbols", "ETHUSDT,BTCUSDT,SOLUSDT")),
            "timeframes": args.get("leaderboardTimeframes", args.get("timeframes", "1h,4h")),
            "strategies": strategy,
            "period": period,
            "maxCombos": args.get("leaderboardMaxCombos", args.get("maxCombos", "auto")),
            "timeout_seconds": args.get("timeout_seconds", "240"),
        },
        compact_snapshot_leaderboard,
    )
    evidence["activity"] = capture(
        "backtest activity lab",
        build_research_activity_lab,
        {**base_args, "symbols": symbol, "timeframes": timeframe, "strategies": strategy, "maxCombos": "1", "optimize": "false"},
        compact_snapshot_activity,
    )
    evidence["robustness"] = capture(
        "parameter robustness lab",
        build_research_parameter_robustness,
        {**base_args, "maxVariants": args.get("robustnessMaxVariants", "24"), "includeBase": "true"},
        compact_snapshot_robustness,
    )
    evidence["feeSlippageStress"] = capture(
        "fee/slippage stress lab",
        build_research_fee_slippage_stress,
        dict(base_args),
        compact_snapshot_fee_stress,
    )
    evidence["walkForward"] = capture(
        "walk-forward review",
        build_research_walk_forward_review,
        {**base_args, "folds": args.get("folds", "4")},
        compact_snapshot_walk_forward,
    )
    evidence["regimeBreakdown"] = capture(
        "regime breakdown lab",
        build_research_regime_breakdown,
        {**base_args, "includeTrades": "false"},
        compact_snapshot_regime_breakdown,
    )
    evidence["variants"] = capture(
        "strategy variant lab",
        build_research_strategy_variant_lab,
        {**base_args, "baseStrategy": strategy, "maxVariants": args.get("variantMaxVariants", "12")},
        compact_snapshot_variants,
    )
    evidence["blockers"] = capture(
        "strategy blocker analytics",
        build_research_blocker_analytics,
        {**base_args, "recentLimit": "20", "includeRecentCandles": "false"},
        compact_snapshot_blockers,
    )
    evidence["multiStrategyMatrix"] = capture(
        "multi-strategy matrix",
        build_research_multi_strategy_matrix,
        {
            "symbols": args.get("matrixSymbols", args.get("symbols", "ETHUSDT,BTCUSDT,SOLUSDT")),
            "timeframes": args.get("matrixTimeframes", args.get("timeframes", "1h,4h")),
            "strategies": args.get("matrixStrategies", "auto"),
            "period": period,
            "maxRows": args.get("matrixMaxRows", "100"),
            "timeout_seconds": args.get("matrixTimeoutSeconds", args.get("timeout_seconds", "420")),
        },
        compact_snapshot_multi_strategy_matrix,
    )
    if log_file:
        evidence["paperObservation"] = compact_snapshot_paper_observation(build_paper_observation_report({"logFile": log_file}))
    else:
        evidence["paperObservation"] = {"included": False, "reason": "Pass logFile to include paper observation evidence."}
    evidence["signalDiagnostics"] = capture(
        "active signal diagnostics",
        build_paper_active_signal_diagnostics,
        {"limit": args.get("signalLimit", "20"), "timeout_seconds": args.get("signalTimeoutSeconds", "90")},
        compact_snapshot_signal_diagnostics,
    )

    strengths, weaknesses, risks, recommendations = research_snapshot_findings(evidence, paper_enabled, real_enabled)
    verdict = research_snapshot_verdict(evidence, paper_enabled, real_enabled, strengths, weaknesses, risks)
    executive = {
        "baseline": f"{strategy} {symbol} {timeframe}",
        "currentDecision": verdict.get("status"),
        "strongestEvidence": strengths[:3],
        "weakestEvidence": weaknesses[:3],
        "realTradingReadiness": "NOT_READY",
        "paperRecommendation": verdict.get("nextAction", {}).get("action"),
    }
    generated_at = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "ok": True,
        "generatedAt": generated_at,
        "format": "json",
        "savedPath": None,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": {
            **candidate_summary(candidate),
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": strategy,
        },
        "verdict": verdict,
        "executiveSummary": executive,
        "evidence": evidence if include_details else compact_snapshot_evidence_headers(evidence),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "risks": risks,
        "recommendations": recommendations,
        "warnings": dedupe_list(([real_detail] if real_enabled else []) + [warning for warning in warnings if warning]),
    }
    if output_format == "markdown":
        markdown = render_research_snapshot_markdown(snapshot)
        saved_path = save_research_snapshot(markdown, "md") if save else None
        return {
            "ok": True,
            "format": "markdown",
            "markdown": markdown,
            "savedPath": saved_path,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "warnings": snapshot["warnings"],
        }, 200
    if save:
        snapshot["savedPath"] = save_research_snapshot(snapshot, "json")
    return snapshot, 200


def compact_snapshot_candidate(candidate: dict, symbol: str, timeframe: str, strategy: str) -> dict:
    promoted = candidate.get("promotedFromOptimization") or {}
    ranking = candidate.get("promotedFromRanking") or {}
    quality = promoted.get("qualityMetrics") or {}
    return {
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
        "source": candidate.get("source"),
        "enabled": canonical_paper_enabled(candidate),
        "paramsRegimeMode": (candidate.get("params") or {}).get("regimeMode"),
        "fillModel": candidate.get("fillModel"),
        "riskPct": candidate.get("riskPct"),
        "promotedAt": candidate.get("promotedAt"),
        "qualityStatus": promoted.get("qualityStatus"),
        "ranking": {
            "rank": ranking.get("rank"),
            "score": ranking.get("score"),
            "trades": ranking.get("trades"),
            "profitFactor": ranking.get("profitFactor"),
            "totalReturnPct": ranking.get("totalReturnPct"),
            "maxDrawdownPct": ranking.get("maxDrawdown") or ranking.get("maxDrawdownPct"),
        },
        "baseline": {
            "fullTrades": quality.get("fullTrades"),
            "fullReturnPct": quality.get("fullReturnPct"),
            "fullProfitFactor": quality.get("fullProfitFactor"),
            "fullMaxDrawdownPct": quality.get("fullMaxDrawdownPct"),
            "testTrades": quality.get("testTrades"),
            "testReturnPct": quality.get("testReturnPct"),
            "testProfitFactor": quality.get("testProfitFactor"),
        },
        "configWarnings": candidate.get("configWarnings") or candidate.get("consistencyWarnings") or [],
    }


def compact_snapshot_candidate_review(payload: dict) -> dict:
    return {
        "verdict": payload.get("verdict") or payload.get("review") or {},
        "readiness": payload.get("readiness") or {},
        "evidence": payload.get("evidence") or {},
        "summary": payload.get("summary") or {},
        "warnings": payload.get("warnings") or [],
    }


def compact_snapshot_leaderboard(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    active = summary.get("activeCandidate") or {}
    best = summary.get("bestOverall") or {}
    return {
        "activeCandidateRank": summary.get("activeCandidateRank"),
        "bestOverall": compact_snapshot_row(best),
        "activeCandidate": compact_snapshot_row(active),
        "passCount": summary.get("passCount"),
        "failCount": summary.get("failCount"),
        "recommendation": summary.get("recommendation") or {},
    }


def compact_snapshot_activity(payload: dict) -> dict:
    rows = payload.get("rows") or []
    active = rows[0] if rows else {}
    summary = payload.get("summary") or {}
    return {
        "activeRow": compact_snapshot_row(active),
        "bestOverall": compact_snapshot_row(summary.get("bestOverall") or {}),
        "recommendation": summary.get("recommendation") or {},
    }


def compact_snapshot_robustness(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    return {
        "status": summary.get("status") or payload.get("status"),
        "base": compact_snapshot_row(payload.get("base") or summary.get("base") or {}),
        "bestVariant": compact_snapshot_row(summary.get("bestVariant") or {}),
        "passingVariants": summary.get("passingVariants"),
        "testedVariants": summary.get("testedVariants") or len(payload.get("variants") or []),
        "recommendation": summary.get("recommendation") or payload.get("recommendation") or {},
    }


def compact_snapshot_fee_stress(payload: dict) -> dict:
    stress = payload.get("stress") or {}
    baseline = next((row for row in payload.get("rows") or [] if row.get("scenario") == "baseline"), (payload.get("rows") or [{}])[0] if payload.get("rows") else {})
    return {
        "status": stress.get("status"),
        "baseline": compact_snapshot_row(baseline),
        "worstPassingScenario": stress.get("worstPassingScenario"),
        "firstFailureScenario": stress.get("firstFailureScenario"),
        "survivingScenarios": stress.get("survivingScenarios"),
        "failedScenarios": stress.get("failedScenarios"),
        "recommendation": stress.get("recommendation"),
    }


def compact_snapshot_walk_forward(payload: dict) -> dict:
    stability = payload.get("stability") or {}
    return {
        "full": compact_snapshot_row(payload.get("full") or {}),
        "recentWindows": [compact_snapshot_row(row, extra=("label",)) for row in (payload.get("recentWindows") or [])[:5]],
        "folds": [compact_snapshot_row(row, extra=("fold", "startTime", "endTime")) for row in (payload.get("folds") or [])[:8]],
        "stability": stability,
    }


def compact_snapshot_regime_breakdown(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    return {
        "dependencyStatus": summary.get("regimeDependencyStatus"),
        "bestRegime": summary.get("bestRegime"),
        "worstRegime": summary.get("worstRegime"),
        "highestTradeCountRegime": summary.get("highestTradeCountRegime"),
        "recommendation": summary.get("recommendation"),
        "topRegimes": [compact_snapshot_row(row, extra=("regime", "contributionPct")) for row in (payload.get("regimes") or [])[:8]],
    }


def compact_snapshot_variants(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    return {
        "status": summary.get("status"),
        "baseline": compact_snapshot_row(summary.get("baseline") or payload.get("baseline") or {}),
        "bestVariant": compact_snapshot_row(summary.get("bestVariant") or {}),
        "variantCount": summary.get("variantCount") or len(payload.get("variants") or []),
        "recommendation": summary.get("recommendation") or payload.get("recommendation") or {},
    }


def compact_snapshot_blockers(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    return {
        "status": summary.get("status"),
        "primaryBlocker": summary.get("primaryBlocker"),
        "topBlockers": (payload.get("blockers") or [])[:8],
        "nearMisses": (payload.get("nearMisses") or [])[:5],
        "recommendation": summary.get("recommendation") or payload.get("recommendation") or {},
    }


def compact_snapshot_multi_strategy_matrix(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    return {
        "activeBaselineRank": summary.get("activeBaselineRank"),
        "activeBaselineRawRank": summary.get("activeBaselineRawRank"),
        "activeBaselinePracticalRank": summary.get("activeBaselinePracticalRank"),
        "bestOverall": compact_snapshot_row(summary.get("bestOverall") or {}),
        "bestRawCandidate": compact_snapshot_row(summary.get("bestRawCandidate") or summary.get("bestOverall") or {}),
        "bestPracticalCandidate": compact_snapshot_row(summary.get("bestPracticalCandidate") or {}),
        "bestReplacementCandidate": compact_snapshot_row(summary.get("bestReplacementCandidate") or {}),
        "bestNonBaseline": compact_snapshot_row(summary.get("bestNonBaseline") or {}),
        "replacementEligibleCount": summary.get("replacementEligibleCount"),
        "rankingExplanation": summary.get("rankingExplanation"),
        "recommendationExplanation": summary.get("recommendationExplanation"),
        "replacementRules": summary.get("replacementRules") or {},
        "passCount": summary.get("passCount"),
        "failCount": summary.get("failCount"),
        "unsupportedCount": summary.get("unsupportedCount"),
        "recommendation": summary.get("recommendation") or {},
    }


def compact_snapshot_paper_observation(payload: dict) -> dict:
    return {
        "included": True,
        "verdict": payload.get("verdict") or {},
        "evidence": payload.get("evidence") or {},
        "progress": payload.get("progress") or {},
        "performance": payload.get("performance") or {},
        "baseline": payload.get("baseline") or {},
        "warnings": payload.get("warnings") or [],
        "informationalWarnings": payload.get("informationalWarnings") or [],
    }


def compact_snapshot_signal_diagnostics(payload: dict) -> dict:
    diagnostics = payload.get("diagnostics") or {}
    return {
        "signal": diagnostics.get("signal"),
        "reason": diagnostics.get("reason"),
        "primaryBlocker": diagnostics.get("primaryBlocker"),
        "latestClosedCandleTime": diagnostics.get("latestClosedCandleTime"),
        "nextAction": payload.get("nextAction") or {},
        "warnings": payload.get("warnings") or [],
    }


def compact_snapshot_row(row: dict | None, extra: tuple = ()) -> dict:
    row = row or {}
    compact = {key: row.get(key) for key in extra if key in row}
    compact.update({
        "strategy": row.get("strategy"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "status": row.get("status") or row.get("qualityStatus"),
        "trades": row.get("trades"),
        "tradesPerMonth": row.get("tradesPerMonth"),
        "totalReturnPct": row.get("totalReturnPct") if row.get("totalReturnPct") is not None else row.get("totalReturn"),
        "profitFactor": row.get("profitFactor"),
        "maxDrawdownPct": row.get("maxDrawdownPct") if row.get("maxDrawdownPct") is not None else row.get("maxDrawdown"),
        "winRate": row.get("winRate"),
        "score": row.get("score"),
        "rawRank": row.get("rawRank"),
        "practicalRank": row.get("practicalRank"),
        "replacementEligible": row.get("replacementEligible"),
        "evidenceTier": row.get("evidenceTier"),
        "practicalScore": row.get("practicalScore"),
        "replacementRejectionReasons": row.get("replacementRejectionReasons"),
        "mainFailureReason": row.get("mainFailureReason"),
    })
    return {key: value for key, value in compact.items() if value is not None}


def compact_snapshot_evidence_headers(evidence: dict) -> dict:
    return {
        key: {
            "ok": value.get("ok"),
            "status": value.get("status") or (value.get("stability") or {}).get("status") or (value.get("verdict") or {}).get("status"),
            "recommendation": value.get("recommendation") or (value.get("summary") or {}).get("recommendation"),
        }
        for key, value in evidence.items()
        if isinstance(value, dict)
    }


def research_snapshot_findings(evidence: dict, paper_enabled: bool, real_enabled: bool) -> tuple[list[str], list[str], list[str], list[str]]:
    strengths = []
    weaknesses = []
    risks = []
    recommendations = []
    candidate = evidence.get("candidateCurrent") or {}
    baseline = candidate.get("baseline") or {}
    if baseline.get("fullProfitFactor") and safe_float(baseline.get("fullProfitFactor"), 0) >= 1.2:
        strengths.append(f"Promoted baseline PF is {baseline.get('fullProfitFactor')} with {baseline.get('fullTrades')} full-window trades.")
    leaderboard = evidence.get("leaderboard") or {}
    if leaderboard.get("activeCandidateRank") == 1:
        strengths.append("Active candidate ranks first in the research leaderboard.")
    matrix = evidence.get("multiStrategyMatrix") or {}
    if (matrix.get("recommendation") or {}).get("action") in {"KEEP_BASELINE", "KEEP_CURRENT"}:
        strengths.append("Multi-strategy matrix did not find a stronger practical replacement.")
    robustness = evidence.get("robustness") or {}
    if str(robustness.get("status") or "").upper() in {"ROBUST", "PASS"}:
        strengths.append("Parameter robustness lab supports the current parameter neighborhood.")
    fee = evidence.get("feeSlippageStress") or {}
    if str(fee.get("status") or "").upper() in {"WATCH", "FRAGILE"}:
        weaknesses.append(f"Fee/slippage stress is {fee.get('status')}; execution costs can reduce the edge.")
    walk = evidence.get("walkForward") or {}
    if str((walk.get("stability") or {}).get("status") or "").upper() in {"WATCH", "FRAGILE"}:
        weaknesses.append(f"Walk-forward stability is {(walk.get('stability') or {}).get('status')}, suggesting regime dependence.")
    regime = evidence.get("regimeBreakdown") or {}
    if str(regime.get("dependencyStatus") or "").upper() in {"MEDIUM", "HIGH"}:
        weaknesses.append(f"Regime dependency is {regime.get('dependencyStatus')}; some regimes may hurt the candidate.")
    paper = evidence.get("paperObservation") or {}
    paper_evidence = paper.get("evidence") or {}
    if paper.get("included") and safe_float(paper_evidence.get("closedTrades"), 0) <= 0:
        weaknesses.append("Paper observation has not produced closed trades yet.")
    signal = evidence.get("signalDiagnostics") or {}
    if signal.get("signal") == "HOLD":
        risks.append(f"Latest active signal diagnostic is HOLD: {signal.get('reason') or signal.get('primaryBlocker') or 'no entry signal'}")
    if real_enabled:
        risks.append("Real trading flag appears enabled; this snapshot is not safe for execution decisions.")
    else:
        strengths.append("Real trading is disabled.")
    if not paper_enabled:
        recommendations.append("Keep paper/manual research review separate; paper is disabled in this working repo.")
    recommendations.append("Use this snapshot for research comparison only; do not treat it as a real-trading approval.")
    if weaknesses:
        recommendations.append("Continue paper-only observation before changing promotion or execution posture.")
    return dedupe_list(strengths), dedupe_list(weaknesses), dedupe_list(risks), dedupe_list(recommendations)


def research_snapshot_verdict(evidence: dict, paper_enabled: bool, real_enabled: bool, strengths: list[str], weaknesses: list[str], risks: list[str]) -> dict:
    if real_enabled:
        return {
            "status": "NOT_READY",
            "summary": "Real trading appears enabled; stop and review safety flags before using research evidence.",
            "nextAction": {"action": "DISABLE_REAL_TRADING_FLAG", "reason": "Research snapshots never approve real trading."},
        }
    paper = evidence.get("paperObservation") or {}
    paper_verdict = paper.get("verdict") or {}
    if str(paper_verdict.get("status") or "").upper() in {"READY_FOR_LONGER_PAPER", "READY_FOR_PAPER_REVIEW", "READY_FOR_REVIEW"}:
        status = "READY_FOR_LONGER_PAPER"
        summary = "Backtest evidence is usable, but forward evidence should continue before any candidate judgment changes."
        action = "CONTINUE_PAPER_OBSERVATION"
    elif weaknesses or risks:
        status = "WATCH"
        summary = "The candidate remains research-worthy, but stress, walk-forward, regime, or live signal evidence needs more review."
        action = "REVIEW_WEAKNESSES"
    else:
        status = "KEEP_BASELINE"
        summary = "The current baseline remains the best practical research candidate in this snapshot."
        action = "KEEP_CURRENT_RESEARCH_BASELINE"
    if not paper_enabled and action == "CONTINUE_PAPER_OBSERVATION":
        action = "REVIEW_ENABLE_PAPER_SIMULATION"
    return {
        "status": status,
        "summary": summary,
        "nextAction": {
            "action": action,
            "reason": "Paper/research review only. This snapshot never promotes, enables paper, or enables real trading automatically.",
        },
    }


def render_research_snapshot_markdown(snapshot: dict) -> str:
    candidate = snapshot.get("candidate") or {}
    verdict = snapshot.get("verdict") or {}
    evidence = snapshot.get("evidence") or {}
    activity = ((evidence.get("activity") or {}).get("activeRow") or {})
    fee = evidence.get("feeSlippageStress") or {}
    walk = evidence.get("walkForward") or {}
    regime = evidence.get("regimeBreakdown") or {}
    paper = evidence.get("paperObservation") or {}
    signal = evidence.get("signalDiagnostics") or {}
    lines = [
        "# ZguaCharts Research Snapshot",
        "",
        f"Generated: {snapshot.get('generatedAt')}",
        f"Candidate: {candidate.get('strategy')} {candidate.get('symbol')} {candidate.get('timeframe')}",
        f"Verdict: {verdict.get('status')} - {verdict.get('summary')}",
        "",
        "## Key Metrics",
        "",
        "| Area | Status | Detail |",
        "| --- | --- | --- |",
        f"| Activity | {activity.get('status', '-')} | {activity.get('trades', '-')} trades, PF {activity.get('profitFactor', '-')}, return {activity.get('totalReturnPct', '-')}% |",
        f"| Fee/slippage | {fee.get('status', '-')} | first failure {fee.get('firstFailureScenario') or '-'} |",
        f"| Walk-forward | {(walk.get('stability') or {}).get('status', '-')} | pass folds {(walk.get('stability') or {}).get('passFoldCount', '-')} / fail folds {(walk.get('stability') or {}).get('failFoldCount', '-')} |",
        f"| Regime | {regime.get('dependencyStatus', '-')} | best {regime.get('bestRegime') or '-'} / worst {regime.get('worstRegime') or '-'} |",
        f"| Signal | {signal.get('signal', '-')} | {signal.get('reason') or signal.get('primaryBlocker') or '-'} |",
        "",
        "## Strengths",
    ]
    lines.extend([f"- {item}" for item in (snapshot.get("strengths") or ["No strengths recorded."])])
    lines.extend(["", "## Weaknesses"])
    lines.extend([f"- {item}" for item in (snapshot.get("weaknesses") or ["No weaknesses recorded."])])
    lines.extend(["", "## Risks"])
    lines.extend([f"- {item}" for item in (snapshot.get("risks") or ["No risks recorded."])])
    lines.extend(["", "## Recommendations"])
    lines.extend([f"- {item}" for item in (snapshot.get("recommendations") or ["No recommendations recorded."])])
    lines.extend([
        "",
        "## Paper Evidence",
        "",
        f"- Included: {bool(paper.get('included'))}",
        f"- Verdict: {(paper.get('verdict') or {}).get('status', '-')}",
        f"- Ticks: {(paper.get('evidence') or {}).get('ticksObserved', '-')}",
        f"- Closed trades: {(paper.get('evidence') or {}).get('closedTrades', '-')}",
        "",
        "## Safety",
        "",
        f"- Paper enabled: {snapshot.get('paperEnabled')}",
        f"- Real trading enabled: {snapshot.get('realTradingEnabled')}",
        "- Real trading readiness: NOT_READY",
        "- This snapshot is read-only research evidence. It does not promote, enable paper, tick paper, or enable real trading.",
    ])
    return "\n".join(lines) + "\n"


def save_research_snapshot(content, extension: str) -> str:
    snapshot_dir = Path(app.root_path) / "reports" / "research-snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = snapshot_dir / f"research-snapshot-{stamp}.{extension}"
    if extension == "json":
        path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    else:
        path.write_text(str(content), encoding="utf-8")
    return str(path.relative_to(Path(app.root_path))).replace("\\", "/")


def build_research_blocker_analytics(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    limit_raw = args.get("limit", "auto")
    recent_limit = max(1, min(int(safe_float(args.get("recentLimit", args.get("recent_limit", 50)), 50)), 200))
    include_recent = str(args.get("includeRecentCandles", "true")).strip().lower() not in {"0", "false", "no", "off"}
    command = package_node_script_args("research:blocker-analytics")
    command.extend([
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--strategy", strategy,
        "--period", period,
        "--limit", str(limit_raw),
        "--includeRecentCandles", "true" if include_recent else "false",
        "--recentLimit", str(recent_limit),
    ])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 240), 240)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Strategy blocker analytics timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "candidate": candidate_summary(candidate),
            "search": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "limit": limit_raw, "includeRecentCandles": include_recent, "recentLimit": recent_limit},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Strategy blocker analytics timed out before returning diagnostics."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Strategy blocker analytics returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Strategy blocker analytics returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Strategy blocker analytics command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "realTradingEnabled": real_enabled,
        "paperEnabled": paper_enabled,
        "candidate": payload.get("candidate") or candidate_summary(candidate),
        "search": payload.get("search") or {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "limit": limit_raw, "includeRecentCandles": include_recent, "recentLimit": recent_limit},
        "activeMarket": payload.get("activeMarket") or {},
        "summary": payload.get("summary") or {},
        "blockers": payload.get("blockers") or [],
        "nearMisses": payload.get("nearMisses") or [],
        "recentCandles": payload.get("recentCandles") or [],
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_strategy_variant_lab(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    base_strategy = (args.get("baseStrategy") or args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    max_variants = max(1, min(int(safe_float(args.get("maxVariants", args.get("max_variants", 20)), 20)), 50))
    variants = args.get("variants", "default")
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    command = package_node_script_args("research:strategy-variant-lab")
    command.extend([
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--baseStrategy", base_strategy,
        "--period", period,
        "--variants", str(variants),
        "--maxVariants", str(max_variants),
        "--baseParams", json.dumps(params),
        "--feePct", str(safe_float(candidate.get("takerFeePct", 0.055), 0.055)),
        "--slippagePct", str(safe_float(candidate.get("slippageBps", 2), 2) / 100),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 240), 240)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Strategy variant lab timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "baseCandidate": candidate_summary(candidate),
            "search": {"symbol": symbol, "timeframe": timeframe, "period": period, "baseStrategy": base_strategy, "variants": variants, "maxVariants": max_variants},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Strategy variant lab timed out before returning rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Strategy variant lab returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Strategy variant lab returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Strategy variant lab command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "baseCandidate": candidate_summary(candidate),
        "search": payload.get("search") or {"symbol": symbol, "timeframe": timeframe, "period": period, "baseStrategy": base_strategy, "variants": variants, "maxVariants": max_variants},
        "rows": payload.get("rows") or [],
        "summary": payload.get("summary") or {},
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def build_research_candidate_review_report(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    include_details = str(args.get("includeDetails", "true")).strip().lower() not in {"0", "false", "no", "off"}
    lab_args = {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": strategy,
        "period": period,
        "limit": args.get("limit", "auto"),
    }
    activity, activity_status = build_research_activity_lab({
        "symbols": symbol,
        "timeframes": timeframe,
        "strategies": strategy,
        "period": period,
        "limit": args.get("limit", "auto"),
        "optimize": "false",
    })
    robustness, robustness_status = build_research_parameter_robustness({
        **lab_args,
        "maxVariants": args.get("robustnessMaxVariants", "100"),
        "includeBase": "true",
    })
    blockers, blockers_status = build_research_blocker_analytics({
        **lab_args,
        "includeRecentCandles": "true",
        "recentLimit": args.get("recentLimit", "30"),
    })
    variants, variants_status = build_research_strategy_variant_lab({
        "symbol": symbol,
        "timeframe": timeframe,
        "baseStrategy": strategy,
        "period": period,
        "variants": args.get("variants", "default"),
        "maxVariants": args.get("maxVariants", "20"),
        "limit": args.get("limit", "auto"),
    })
    paper_args = {**dict(args), "symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period}
    paper = build_paper_observation_report(paper_args)
    signal_diagnostics, signal_status = build_paper_active_signal_diagnostics({"limit": args.get("signalLimit", "20")})
    activity_row = (activity.get("rows") or [None])[0] or {}
    robustness_summary = robustness.get("robustness") or {}
    blocker_summary = blockers.get("summary") or {}
    variant_summary = variants.get("summary") or {}
    paper_verdict = paper.get("verdict") or {}
    scores = candidate_review_scores(activity_row, robustness_summary, blocker_summary, variant_summary, paper)
    strengths, weaknesses, risks, recommendations = candidate_review_lists(
        activity_row,
        robustness_summary,
        blocker_summary,
        variant_summary,
        paper,
        signal_diagnostics,
        real_enabled,
    )
    verdict = candidate_review_verdict(activity_row, robustness_summary, variant_summary, paper, scores, real_enabled, real_detail)
    warnings = []
    for payload, status_code, label in [
        (activity, activity_status, "activity lab"),
        (robustness, robustness_status, "parameter robustness"),
        (blockers, blockers_status, "blocker analytics"),
        (variants, variants_status, "strategy variant lab"),
        (signal_diagnostics, signal_status, "active signal diagnostics"),
    ]:
        if status_code >= 400 or payload.get("ok") is False:
            warnings.append(f"{label} returned status {status_code}.")
        warnings.extend(payload.get("warnings") or [])
    warnings.extend(paper.get("warnings") or [])
    if real_enabled:
        warnings.append(real_detail)
    evidence = {
        "activity": compact_candidate_activity_evidence(activity_row, activity),
        "robustness": compact_candidate_robustness_evidence(robustness),
        "blockers": compact_candidate_blocker_evidence(blockers),
        "variants": compact_candidate_variant_evidence(variants),
        "paper": compact_candidate_paper_evidence(paper),
        "signalDiagnostics": compact_candidate_signal_evidence(signal_diagnostics),
    }
    if include_details:
        evidence["details"] = {
            "activity": activity,
            "robustness": robustness,
            "blockers": blockers,
            "variants": variants,
            "paper": paper,
            "signalDiagnostics": signal_diagnostics,
        }
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "scores": scores,
        "evidence": evidence,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "risks": risks,
        "recommendations": recommendations,
        "warnings": dedupe_list([warning for warning in warnings if warning]),
    }, 200


def scorecard_summary_text(summary) -> str:
    if isinstance(summary, dict):
        action = summary.get("action")
        reason = summary.get("reason")
        if action and reason:
            return f"{action}: {reason}"
        if reason:
            return str(reason)
        if action:
            return str(action)
        return json.dumps(summary, sort_keys=True)
    return str(summary or "")


def scorecard_section(name: str, status, summary, detail: dict | None = None, severity: str | None = None) -> dict:
    status_text = str(status or "UNKNOWN").upper()
    if severity:
        severity_text = severity
    elif status_text in {"PASS", "ROBUST", "STABLE", "RESILIENT", "READY_FOR_LONGER_PAPER", "KEEP_OBSERVING", "READY_FOR_REVIEW", "READY_FOR_PAPER_REVIEW"}:
        severity_text = "PASS"
    elif status_text in {"WARN", "WATCH", "MEDIUM", "TOO_EARLY", "WAIT_FOR_NEXT_CANDLE", "OBSERVE_MORE", "READY_FOR_LONGER_PAPER"}:
        severity_text = "WATCH"
    elif status_text in {"FAIL", "FRAGILE", "HIGH", "PAUSE_RECOMMENDED", "RESEARCH_ALTERNATIVES", "NOT_READY"}:
        severity_text = "FAIL"
    else:
        severity_text = "INFO"
    return {
        "name": name,
        "status": status_text,
        "severity": severity_text,
        "summary": scorecard_summary_text(summary),
        "detail": detail or {},
    }


def evidence_scorecard_verdict(sections: list[dict], review: dict, real_enabled: bool, real_detail: str) -> dict:
    if real_enabled:
        return {
            "status": "PAUSE_RECOMMENDED",
            "title": "Safety Review Required",
            "summary": "Real trading appears enabled, so research and paper review should pause until safety is restored.",
            "nextAction": {"action": "DISABLE_REAL_TRADING_FLAG", "reason": real_detail},
        }
    failed = [section for section in sections if section.get("severity") == "FAIL"]
    watch = [section for section in sections if section.get("severity") == "WATCH"]
    review_verdict = review.get("verdict") or {}
    review_status = str(review_verdict.get("status") or "").upper()
    failed_names = {section.get("name") for section in failed}
    watch_names = {section.get("name") for section in watch}
    walk_forward = next((section for section in sections if section.get("name") == "Walk-forward"), {})
    regime = next((section for section in sections if section.get("name") == "Regime dependency"), {})
    cost = next((section for section in sections if section.get("name") == "Fee/slippage stress"), {})
    paper = next((section for section in sections if section.get("name") == "Paper evidence"), {})
    if "Walk-forward" in failed_names:
        detail = walk_forward.get("detail") or {}
        pass_folds = detail.get("passFoldCount")
        fail_folds = detail.get("failFoldCount")
        negative_folds = detail.get("negativeFoldCount")
        regime_status = regime.get("status")
        return {
            "status": "RESEARCH_REGIME_ROBUSTNESS",
            "title": "Walk-Forward Fragility Needs Review",
            "summary": f"Activity and parameter robustness are favorable, but walk-forward is {walk_forward.get('status')} with {pass_folds} passing fold(s), {fail_folds} failed fold(s), and {negative_folds} negative fold(s). Regime dependency is {regime_status}.",
            "primaryIssues": [walk_forward, regime],
            "watchSections": watch,
            "nextAction": {"action": "REVIEW_WALK_FORWARD_AND_REGIME", "reason": "Inspect failed folds and regime breakdown before treating this candidate as paper-confident. No promotion or trading action is implied."},
        }
    if "Fee/slippage stress" in failed_names:
        return {
            "status": "RESEARCH_EXECUTION_COSTS",
            "title": "Execution Cost Fragility Needs Review",
            "summary": "The active candidate has at least one failed execution-cost evidence section. Review fee/slippage assumptions before longer paper confidence.",
            "primaryIssues": [cost],
            "watchSections": watch,
            "nextAction": {"action": "REVIEW_FEE_SLIPPAGE_STRESS", "reason": "Review stress scenarios and keep the candidate paper-only."},
        }
    if failed:
        names = ", ".join(section.get("name", "-") for section in failed[:3])
        return {
            "status": "RESEARCH_MORE",
            "title": "Research More Before Paper Confidence",
            "summary": f"{names} need review before trusting this candidate further.",
            "primaryIssues": failed,
            "watchSections": watch,
            "nextAction": {"action": "REVIEW_FAILED_EVIDENCE", "reason": "Inspect failed scorecard sections. This endpoint is research-only and never promotes or trades."},
        }
    if review_status == "READY_FOR_LONGER_PAPER":
        return {
            "status": "OBSERVE_PAPER_LONGER",
            "title": "Ready For Longer Paper Observation",
            "summary": "Historical evidence is favorable, but forward paper evidence still needs more time and closed trades.",
            "primaryIssues": [paper] if paper else [],
            "watchSections": watch,
            "nextAction": {"action": "CONTINUE_PAPER_OBSERVATION", "reason": "Keep paper-only observation running manually when ready. Do not infer real-trading readiness."},
        }
    if "Fee/slippage stress" in watch_names and len(watch_names) <= 2:
        return {
            "status": "WATCH_EXECUTION_COSTS",
            "title": "Execution Costs Need Watching",
            "summary": "The candidate is not blocked, but execution-cost stress is a WATCH item. Paper observation should stay cost-aware.",
            "primaryIssues": [cost],
            "watchSections": watch,
            "nextAction": {"action": "WATCH_EXECUTION_COSTS", "reason": "Continue paper-only observation and review cost assumptions before any candidate change."},
        }
    if watch:
        names = ", ".join(section.get("name", "-") for section in watch[:3])
        return {
            "status": "RESEARCH_MORE",
            "title": "Evidence Needs Review",
            "summary": f"{names} are in WATCH/early state. Keep research review active before changing candidate configuration.",
            "primaryIssues": watch[:3],
            "watchSections": watch,
            "nextAction": {"action": "REVIEW_WATCH_SECTIONS", "reason": "Review watch sections and continue paper-only evidence collection."},
        }
    return {
        "status": "OBSERVE_PAPER_LONGER",
        "title": "Candidate Remains Paper-Only",
        "summary": "The active candidate evidence is broadly favorable. Continue paper-only observation; no real-trading action is recommended.",
        "primaryIssues": [],
        "watchSections": [],
        "nextAction": {"action": "CONTINUE_PAPER_OBSERVATION", "reason": "Collect forward paper evidence and review closed trades manually."},
    }


def compact_scorecard_fold(fold: dict | None) -> dict:
    fold = fold or {}
    return {
        "fold": fold.get("fold"),
        "status": fold.get("status"),
        "startTime": fold.get("startTime"),
        "endTime": fold.get("endTime"),
        "trades": fold.get("trades"),
        "totalReturnPct": fold.get("totalReturnPct"),
        "profitFactor": fold.get("profitFactor"),
        "maxDrawdownPct": fold.get("maxDrawdownPct"),
        "mainFailureReason": fold.get("mainFailureReason"),
    }


def compact_scorecard_regime(regime: dict | None) -> dict:
    regime = regime or {}
    return {
        "regime": regime.get("regime"),
        "status": regime.get("status"),
        "trades": regime.get("trades"),
        "totalReturnPct": regime.get("totalReturnPct"),
        "profitFactor": regime.get("profitFactor"),
        "contributionPct": regime.get("contributionPct"),
        "mainFailureReason": regime.get("mainFailureReason"),
        "trend": regime.get("trend"),
        "volatility": regime.get("volatility"),
        "momentum": regime.get("momentum"),
    }


def build_walk_regime_drilldown(walk_forward: dict, regime: dict) -> dict:
    folds = walk_forward.get("folds") or []
    failed_folds = [fold for fold in folds if str(fold.get("status") or "").upper() != "PASS"]
    passing_folds = [fold for fold in folds if str(fold.get("status") or "").upper() == "PASS"]
    latest_fold = sorted(folds, key=lambda fold: str(fold.get("endTime") or ""))[-1] if folds else {}
    stability = walk_forward.get("stability") or {}
    regime_summary = regime.get("summary") or {}
    latest_status = str(latest_fold.get("status") or "UNKNOWN").upper()
    if latest_status == "PASS":
        current_read = "CURRENT_FOLD_RESEMBLES_STRONG_PERIOD"
        current_reason = "The latest chronological fold is passing, so recent history currently resembles a stronger walk-forward period."
    elif latest_fold:
        current_read = "CURRENT_FOLD_RESEMBLES_WEAK_PERIOD"
        current_reason = "The latest chronological fold is failing, so recent history currently resembles the weak side of the walk-forward evidence."
    else:
        current_read = "CURRENT_FOLD_UNKNOWN"
        current_reason = "No chronological fold data was available."
    failed_return = round(sum(safe_float(fold.get("totalReturnPct"), 0) for fold in failed_folds), 4)
    passing_return = round(sum(safe_float(fold.get("totalReturnPct"), 0) for fold in passing_folds), 4)
    return {
        "status": "WALK_FORWARD_REGIME_REVIEW",
        "currentMarketRead": current_read,
        "currentMarketReason": current_reason,
        "foldSummary": {
            "totalFolds": len(folds),
            "passingFolds": len(passing_folds),
            "failedFolds": len(failed_folds),
            "negativeFolds": stability.get("negativeFoldCount"),
            "passingReturnPct": passing_return,
            "failedReturnPct": failed_return,
        },
        "latestFold": compact_scorecard_fold(latest_fold),
        "bestFold": compact_scorecard_fold(stability.get("bestFold")),
        "worstFold": compact_scorecard_fold(stability.get("worstFold")),
        "failedFolds": [compact_scorecard_fold(fold) for fold in failed_folds],
        "passingFolds": [compact_scorecard_fold(fold) for fold in passing_folds],
        "regimeDependencyStatus": regime_summary.get("regimeDependencyStatus"),
        "bestRegime": compact_scorecard_regime(regime_summary.get("bestRegime")),
        "worstRegime": compact_scorecard_regime(regime_summary.get("worstRegime")),
        "highestTradeCountRegime": compact_scorecard_regime(regime_summary.get("highestTradeCountRegime")),
        "regimeRecommendation": regime_summary.get("recommendation"),
    }


def build_research_evidence_scorecard(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    base_args = {
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": strategy,
        "period": period,
        "limit": args.get("limit", "auto"),
        "includeDetails": "false",
    }
    review, review_status = build_research_candidate_review_report(base_args)
    fee_stress, fee_status = build_research_fee_slippage_stress(base_args)
    walk_forward, walk_status = build_research_walk_forward_review(base_args)
    regime, regime_status = build_research_regime_breakdown({**base_args, "includeTrades": "false"})
    review_evidence = review.get("evidence") or {}
    activity = review_evidence.get("activity") or {}
    robustness = review_evidence.get("robustness") or {}
    blockers = review_evidence.get("blockers") or {}
    variants = review_evidence.get("variants") or {}
    paper = review_evidence.get("paper") or {}
    signal = review_evidence.get("signalDiagnostics") or {}
    stress = fee_stress.get("stress") or {}
    stability = walk_forward.get("stability") or {}
    regime_summary = regime.get("summary") or {}
    sections = [
        scorecard_section("Activity", activity.get("status"), f"{activity.get('trades', 0)} trades, PF {activity.get('profitFactor', '-')}, return {activity.get('totalReturnPct', '-')}%.", activity),
        scorecard_section("Parameter robustness", robustness.get("status"), f"Pass rate {round(safe_float(robustness.get('passRate'), 0) * 100, 2)}%, median PF {robustness.get('medianProfitFactor', '-')}.", robustness),
        scorecard_section("Variant tradeoff", variants.get("status", "INFO"), f"Baseline {((variants.get('baseline') or {}).get('variantName') or '-')}; best tradeoff {((variants.get('bestTradeoff') or {}).get('variantName') or '-')}.", variants, "INFO"),
        scorecard_section("Blockers", "WATCH" if blockers.get("mainBlocker") else "PASS", f"Main blocker: {blockers.get('mainBlocker') or 'none'}.", blockers),
        scorecard_section("Fee/slippage stress", stress.get("status"), stress.get("recommendation") or "Execution-cost stress result.", stress),
        scorecard_section("Walk-forward", stability.get("status"), stability.get("recommendation") or "Chronological fold and recent-window review.", stability),
        scorecard_section("Regime dependency", regime_summary.get("regimeDependencyStatus"), regime_summary.get("recommendation") or "Regime contribution review.", regime_summary),
        scorecard_section("Paper evidence", (paper.get("verdict") or {}).get("status"), f"{paper.get('runnerTicksRun', paper.get('ticksObserved', 0))} useful tick(s), {paper.get('signalsObserved', 0)} signal(s), {paper.get('closedTrades', 0)} closed trade(s).", paper),
        scorecard_section("Signal frequency", signal.get("signal", "UNKNOWN"), signal.get("reason") or "Latest active signal diagnostics.", signal, "WATCH" if signal.get("signal") in {"HOLD", "UNKNOWN", None} else "INFO"),
    ]
    warnings = []
    for payload, status_code, label in [
        (review, review_status, "candidate review report"),
        (fee_stress, fee_status, "fee/slippage stress"),
        (walk_forward, walk_status, "walk-forward review"),
        (regime, regime_status, "regime breakdown"),
    ]:
        if status_code >= 400 or payload.get("ok") is False:
            warnings.append(f"{label} returned status {status_code}.")
        warnings.extend(payload.get("warnings") or [])
    if real_enabled:
        warnings.append(real_detail)
    verdict = evidence_scorecard_verdict(sections, review, real_enabled, real_detail)
    drilldowns = {
        "walkForwardRegime": build_walk_regime_drilldown(walk_forward, regime),
    }
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "search": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period},
        "verdict": verdict,
        "scores": review.get("scores") or {},
        "sections": sections,
        "drilldowns": drilldowns,
        "warnings": dedupe_list([warning for warning in warnings if warning]),
        "sourceReports": {
            "candidateReviewStatus": review_status,
            "feeSlippageStatus": fee_status,
            "walkForwardStatus": walk_status,
            "regimeStatus": regime_status,
        },
    }, 200


def candidate_review_scores(activity: dict, robustness: dict, blockers: dict, variants: dict, paper: dict) -> dict:
    trades = safe_float(activity.get("trades"), 0)
    pf = safe_float(activity.get("profitFactor"), 0)
    total_return = safe_float(activity.get("totalReturnPct"), 0)
    drawdown = safe_float(activity.get("maxDrawdownPct"), 999)
    activity_score = min(100, max(0, trades * 0.55 + pf * 25 + total_return * 3 - drawdown * 1.5))
    robust_status = str(robustness.get("status") or "").upper()
    robustness_score = 35 if robust_status in {"FAIL", "FRAGILE"} else 70 if robust_status == "WATCH" else 88 if robust_status == "ROBUST" else 50
    robustness_score += safe_float(robustness.get("passRate"), 0) * 12
    baseline = variants.get("baseline") or {}
    best_tradeoff = variants.get("bestTradeoff") or {}
    variant_score = 78
    if best_tradeoff and baseline and best_tradeoff.get("variantName") != baseline.get("variantName"):
        if safe_float(best_tradeoff.get("tradesPerMonth"), 0) > safe_float(baseline.get("tradesPerMonth"), 0) and safe_float(best_tradeoff.get("profitFactor"), 0) >= 1.1:
            variant_score = 58
    blocker_name = str(blockers.get("mainBlocker") or "")
    blocker_score = 70 if blocker_name else 50
    if blocker_name in {"emaTrendFailed", "pullbackReclaimFailed", "positionBlocked"}:
        blocker_score = 62
    paper_evidence = paper.get("evidence") or {}
    paper_status = str((paper.get("verdict") or {}).get("status") or "").upper()
    closed_trades = safe_float(paper_evidence.get("closedTrades"), 0)
    ticks = safe_float(paper_evidence.get("runnerTicksRun", paper_evidence.get("ticksObserved")), 0)
    paper_score = 25 if closed_trades <= 0 else min(85, 35 + closed_trades * 5)
    if ticks >= 20:
        paper_score += 10
    if paper_status in {"PAUSE_RECOMMENDED", "WATCH"}:
        paper_score -= 20
    overall = round((activity_score * 0.25 + robustness_score * 0.25 + variant_score * 0.15 + blocker_score * 0.1 + paper_score * 0.25), 2)
    return {
        "activityScore": round(activity_score, 2),
        "robustnessScore": round(min(100, robustness_score), 2),
        "variantScore": round(variant_score, 2),
        "paperScore": round(max(0, min(100, paper_score)), 2),
        "blockerScore": round(blocker_score, 2),
        "overallScore": overall,
    }


def candidate_review_verdict(activity: dict, robustness: dict, variants: dict, paper: dict, scores: dict, real_enabled: bool, real_detail: str) -> dict:
    activity_status = str(activity.get("status") or "").upper()
    robust_status = str(robustness.get("status") or "").upper()
    baseline = variants.get("baseline") or {}
    best_tradeoff = variants.get("bestTradeoff") or {}
    paper_evidence = paper.get("evidence") or {}
    closed_trades = safe_float(paper_evidence.get("closedTrades"), 0)
    ticks = safe_float(paper_evidence.get("runnerTicksRun", paper_evidence.get("ticksObserved")), 0)
    if real_enabled:
        return {
            "status": "NOT_READY",
            "title": "Safety Review Required",
            "summary": "Real trading appears enabled, so this candidate cannot be reviewed for paper-only observation until safety is restored.",
            "nextAction": {"action": "DISABLE_REAL_TRADING_FLAG", "reason": real_detail},
        }
    if activity_status not in {"PASS", "WARN"}:
        return {
            "status": "RESEARCH_ALTERNATIVES",
            "title": "Research Alternatives",
            "summary": "The selected candidate does not pass the read-only activity review.",
            "nextAction": {"action": "RESEARCH_ALTERNATIVES", "reason": "Use activity/variant labs to inspect other candidates. No promotion is automatic."},
        }
    if robust_status in {"FAIL", "FRAGILE"}:
        return {
            "status": "RESEARCH_ALTERNATIVES",
            "title": "Robustness Is Weak",
            "summary": "The base backtest is not robust enough across nearby parameter variants.",
            "nextAction": {"action": "RESEARCH_ALTERNATIVES", "reason": "Review robustness before collecting more paper evidence."},
        }
    if best_tradeoff and baseline and best_tradeoff.get("variantName") != baseline.get("variantName"):
        better_activity = safe_float(best_tradeoff.get("tradesPerMonth"), 0) > safe_float(baseline.get("tradesPerMonth"), 0)
        acceptable = safe_float(best_tradeoff.get("profitFactor"), 0) >= 1.1 and safe_float(best_tradeoff.get("totalReturnPct"), 0) > 0
        if better_activity and acceptable:
            return {
                "status": "RESEARCH_ALTERNATIVES",
                "title": "Review Variant Before More Paper",
                "summary": f"{best_tradeoff.get('variantName')} may offer a better activity tradeoff, but it is research-only and not promoted.",
                "nextAction": {"action": "REVIEW_VARIANT_LAB", "reason": "Compare the variant manually before changing any candidate config."},
            }
    if closed_trades <= 0 and ticks >= 20:
        return {
            "status": "READY_FOR_LONGER_PAPER",
            "title": "Promising But Needs Longer Paper",
            "summary": "Backtest and robustness evidence are favorable, but forward paper has no closed trades yet.",
            "nextAction": {"action": "CONTINUE_PAPER_OBSERVATION", "reason": "Keep observing in paper only until closed trades accumulate. Never treat this as real-trading readiness."},
        }
    if closed_trades <= 0:
        return {
            "status": "KEEP_OBSERVING",
            "title": "Keep Observing",
            "summary": "Research evidence is favorable, but paper evidence is too early for a final candidate judgment.",
            "nextAction": {"action": "CONTINUE_PAPER_OBSERVATION", "reason": "Collect more useful paper ticks and closed trades."},
        }
    return {
        "status": "KEEP_OBSERVING" if scores.get("overallScore", 0) < 80 else "READY_FOR_LONGER_PAPER",
        "title": "Continue Paper Review",
        "summary": "The candidate remains suitable for paper-only observation based on the consolidated evidence.",
        "nextAction": {"action": "REVIEW_PAPER_RESULTS", "reason": "Review paper-only outcomes manually. This report never recommends real trading."},
    }


def candidate_review_lists(activity: dict, robustness: dict, blockers: dict, variants: dict, paper: dict, signal: dict, real_enabled: bool) -> tuple[list, list, list, list]:
    strengths = []
    weaknesses = []
    risks = []
    recommendations = []
    if activity.get("status") in {"PASS", "WARN"}:
        strengths.append(f"Activity lab passes with {activity.get('trades')} trades, PF {activity.get('profitFactor')}, and return {activity.get('totalReturnPct')}%.")
    else:
        weaknesses.append("Activity lab does not pass for the selected candidate.")
    robust = robustness.get("status")
    if robust == "ROBUST":
        strengths.append(f"Parameter robustness is ROBUST with pass rate {round(safe_float(robustness.get('passRate'), 0) * 100, 2)}%.")
    elif robust:
        weaknesses.append(f"Parameter robustness is {robust}.")
    baseline = variants.get("baseline") or {}
    best_tradeoff = variants.get("bestTradeoff") or {}
    if baseline and best_tradeoff and best_tradeoff.get("variantName") == baseline.get("variantName"):
        strengths.append("Strategy Variant Lab keeps the baseline as the best tradeoff.")
    elif best_tradeoff:
        risks.append(f"Variant Lab found {best_tradeoff.get('variantName')} as a possible tradeoff; review before more promotion work.")
    main_blocker = blockers.get("mainBlocker")
    if main_blocker:
        weaknesses.append(f"Dominant blocker is {main_blocker}; the strategy is selective by design.")
    paper_evidence = paper.get("evidence") or {}
    ticks = safe_float(paper_evidence.get("runnerTicksRun", paper_evidence.get("ticksObserved")), 0)
    closed = safe_float(paper_evidence.get("closedTrades"), 0)
    if ticks >= 20:
        strengths.append(f"Paper observation has {int(ticks)} useful runner tick(s) recorded.")
    if closed <= 0:
        weaknesses.append("Forward paper has 0 closed trades, so paper evidence is not yet decisive.")
        recommendations.append("Continue paper-only observation until closed trades and target duration accumulate.")
    latest_signal = ((signal.get("diagnostics") or {}).get("signal") or "UNKNOWN")
    latest_reason = (signal.get("diagnostics") or {}).get("reason")
    if latest_signal == "HOLD" and latest_reason:
        risks.append(f"Latest active diagnostic is HOLD: {latest_reason}")
    if real_enabled:
        risks.append("Real trading appears enabled; this must be treated as a safety issue.")
    recommendations.append("Keep real trading disabled; this report is research and paper-review only.")
    return dedupe_list(strengths), dedupe_list(weaknesses), dedupe_list(risks), dedupe_list(recommendations)


def compact_candidate_activity_evidence(row: dict, payload: dict) -> dict:
    return {
        "status": row.get("status"),
        "trades": row.get("trades"),
        "tradesPerMonth": row.get("tradesPerMonth"),
        "profitFactor": row.get("profitFactor"),
        "totalReturnPct": row.get("totalReturnPct"),
        "maxDrawdownPct": row.get("maxDrawdownPct"),
        "expectancyPctPerTrade": row.get("expectancyPctPerTrade"),
        "summaryRecommendation": ((payload.get("summary") or {}).get("recommendation") or {}),
    }


def compact_candidate_robustness_evidence(payload: dict) -> dict:
    robust = payload.get("robustness") or {}
    base = payload.get("baseResult") or {}
    return {
        "status": robust.get("status"),
        "passRate": robust.get("passRate"),
        "medianProfitFactor": robust.get("medianProfitFactor"),
        "medianReturnPct": robust.get("medianReturnPct"),
        "medianMaxDrawdownPct": robust.get("medianMaxDrawdownPct"),
        "medianTrades": robust.get("medianTrades"),
        "baseStatus": base.get("status"),
        "baseProfitFactor": base.get("profitFactor"),
    }


def compact_candidate_blocker_evidence(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    return {
        "mainBlocker": summary.get("mainBlocker"),
        "candlesAnalyzed": summary.get("candlesAnalyzed"),
        "tradeCount": summary.get("tradeCount"),
        "signalRatePct": summary.get("signalRatePct"),
        "approximateSignalsPerMonth": summary.get("approximateSignalsPerMonth"),
        "topBlockers": (payload.get("blockers") or [])[:3],
    }


def compact_candidate_variant_evidence(payload: dict) -> dict:
    summary = payload.get("summary") or {}
    return {
        "baseline": summary.get("baseline"),
        "bestOverall": summary.get("bestOverall"),
        "mostActivePassing": summary.get("mostActivePassing"),
        "bestTradeoff": summary.get("bestTradeoff"),
        "recommendation": summary.get("recommendation"),
    }


def compact_candidate_paper_evidence(payload: dict) -> dict:
    return {
        "verdict": payload.get("verdict"),
        "evidence": payload.get("evidence"),
        "progress": payload.get("progress"),
        "performance": payload.get("performance"),
        "runnerTicksRun": payload.get("runnerTicksRun"),
        "runnerTicksSkipped": payload.get("runnerTicksSkipped"),
        "processedCandleDeltaTotal": payload.get("processedCandleDeltaTotal"),
    }


def compact_candidate_signal_evidence(payload: dict) -> dict:
    diagnostics = payload.get("diagnostics") or {}
    return {
        "signal": diagnostics.get("signal"),
        "reason": diagnostics.get("reason"),
        "primaryBlocker": diagnostics.get("primaryBlocker"),
        "nextAction": payload.get("nextAction"),
    }


def build_research_parameter_robustness(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    symbol = (args.get("symbol") or active.get("symbol") or "ETHUSDT").strip()
    timeframe = (args.get("timeframe") or args.get("interval") or active.get("interval") or active.get("timeframe") or "1h").strip()
    strategy = (args.get("strategy") or candidate.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    mode = args.get("mode", "local-grid")
    max_variants = max(1, min(int(safe_float(args.get("maxVariants", args.get("max_variants", 100)), 100)), 250))
    include_base = str(args.get("includeBase", "true")).strip().lower() not in {"0", "false", "no", "off"}
    fee_pct = safe_float(args.get("feePct", args.get("fee_pct", candidate.get("takerFeePct", 0.055))), 0.055)
    slippage_pct = safe_float(args.get("slippagePct", args.get("slippage_pct", safe_float(candidate.get("slippageBps", 2), 2) / 100)), 0.02)
    params = dict(candidate.get("params") if isinstance(candidate.get("params"), dict) else {})
    command = package_node_script_args("research:parameter-robustness")
    command.extend([
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--strategy", strategy,
        "--period", period,
        "--mode", mode,
        "--maxVariants", str(max_variants),
        "--includeBase", "true" if include_base else "false",
        "--feePct", str(fee_pct),
        "--slippagePct", str(slippage_pct),
        "--baseParams", json.dumps(params),
    ])
    if args.get("limit"):
        command.extend(["--limit", str(args.get("limit"))])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 240), 240)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Parameter robustness lab timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "baseCandidate": candidate_summary(candidate),
            "search": {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "mode": mode, "maxVariants": max_variants, "includeBase": include_base},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Parameter robustness lab timed out before returning variants."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Parameter robustness lab returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Parameter robustness lab returned no output."}
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Parameter robustness lab command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "realTradingEnabled": real_enabled,
        "paperEnabled": paper_enabled,
        "baseCandidate": candidate_summary(candidate),
        "search": payload.get("search") or {"symbol": symbol, "timeframe": timeframe, "strategy": strategy, "period": period, "mode": mode, "maxVariants": max_variants, "includeBase": include_base},
        "baseResult": payload.get("baseResult"),
        "robustness": payload.get("robustness") or {},
        "variants": payload.get("variants") or [],
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def compare_candidate_market_row(candidate: dict, symbol: str, timeframe: str, strategy: str, period: str, rules: dict, params: dict, fee_pct: float, slippage_pct: float, active_symbol: str | None, active_timeframe: str | None) -> dict:
    comparable = symbol == active_symbol and timeframe == active_timeframe and strategy == candidate.get("strategy")
    try:
        limit = research_limit_for(candidate.get("source", "bybit"), timeframe, period, rules.get("limitRaw", "auto"))
        payload = run_shared_backtest_engine(
            candidate.get("source", "bybit"),
            symbol,
            timeframe,
            period,
            strategy,
            fee_pct,
            slippage_pct,
            limit,
            allow_shorts=False,
            strategy_params=params,
        )
        metrics = ranking_metrics_from_backtest(payload)
        status, reasons = candidate_market_status(metrics, rules)
        if int(safe_float(metrics.get("trades"), 0)) <= 0:
            status = "NO_TRADES"
            reasons = ["No trades generated for this symbol/timeframe with the current candidate params."]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": payload.get("preset") or payload.get("strategy") or strategy,
            "status": status,
            "trades": int(safe_float(metrics.get("trades"), 0)),
            "totalReturnPct": metrics.get("totalReturn"),
            "profitFactor": metrics.get("profitFactor"),
            "maxDrawdownPct": metrics.get("maxDrawdown"),
            "winRate": metrics.get("winRate"),
            "score": ranking_score(metrics, min_trades=rules["minTrades"]),
            "qualityStatus": "FAIL" if status in {"FAIL", "NO_TRADES"} else status,
            "rejectionReasons": comparison_rejection_reasons(status, reasons),
            "warnings": payload.get("warnings", []) + (payload.get("diagnostics", {}).get("warnings", []) or []),
            "comparableToActive": comparable,
            "diagnostics": {
                "enoughTrades": int(safe_float(metrics.get("trades"), 0)) >= rules["minTrades"],
                "moreActiveThanCurrent": False,
                "period": period,
                "limit": limit,
                "averageBarsHeld": metrics.get("averageBarsHeld"),
            },
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": strategy,
            "status": "ERROR",
            "trades": 0,
            "totalReturnPct": 0,
            "profitFactor": 0,
            "maxDrawdownPct": 0,
            "winRate": 0,
            "score": -999,
            "qualityStatus": "FAIL",
            "rejectionReasons": [{"code": "backtest_error", "label": str(exc)}],
            "warnings": [str(exc)],
            "comparableToActive": comparable,
            "diagnostics": {
                "enoughTrades": False,
                "moreActiveThanCurrent": False,
                "period": period,
                "limit": rules.get("limit"),
            },
        }


def comparison_rejection_reasons(status: str, reasons: list[str]) -> list[dict]:
    if status in {"PASS", "WARN"} and not reasons:
        return []
    if status == "NO_TRADES":
        return [{"code": "no_trades", "label": "No trades generated"}]
    return [{"code": reason_code_from_text(reason), "label": reason} for reason in reasons]


def reason_code_from_text(reason: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", str(reason).lower()).strip("_")
    return text[:80] or "candidate_warning"


def candidate_comparison_recommendation(rows: list[dict], active_symbol: str | None, active_timeframe: str | None) -> dict:
    active_rows = [row for row in rows if row.get("comparableToActive")]
    active_score = max([safe_float(row.get("score"), -999) for row in active_rows] or [-999])
    fifteen_minute = [
        row for row in rows
        if row.get("timeframe") == "15m"
        and row.get("status") in {"PASS", "WARN"}
        and safe_float(row.get("score"), -999) >= max(active_score - 5, -999)
        and row.get("diagnostics", {}).get("moreActiveThanCurrent")
    ]
    if fifteen_minute:
        best = sorted(fifteen_minute, key=lambda row: safe_float(row.get("score"), -999), reverse=True)[0]
        return {
            "action": "REVIEW_15M_CANDIDATE",
            "reason": f"{best['symbol']} 15m generated more trades than the active {active_symbol} {active_timeframe} candidate with comparable inspection score. Review only; no promotion was performed.",
        }
    active_pass = any(row.get("status") in {"PASS", "WARN"} for row in active_rows)
    if active_pass:
        return {
            "action": "KEEP_CURRENT",
            "reason": "No faster inspected candidate clearly beats the current active paper candidate on this read-only comparison.",
        }
    return {
        "action": "NO_ACTION",
        "reason": "No inspected candidate is strong enough for review. This endpoint does not promote or enable paper.",
    }


def build_paper_fast_candidate_discovery(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    symbols = parse_csv_arg(args.get("symbols"), ["ETHUSDT", "BTCUSDT"])
    timeframes = parse_csv_arg(args.get("timeframes"), ["15m"])
    strategy = (args.get("strategy") or "SimpleAtrTrendV2").strip()
    period = args.get("period", "365d")
    max_combos = max(1, min(int(safe_float(args.get("max_combos", args.get("maxCombos", 100)), 100)), 500))
    limit_raw = args.get("limit", "auto")
    command = package_node_script_args("paper:discover-fast-candidate")
    command.extend([
        "--symbols", ",".join(symbols),
        "--timeframes", ",".join(timeframes),
        "--strategy", strategy,
        "--period", period,
        "--max-combos", str(max_combos),
        "--limit", str(limit_raw),
        "--fee-pct", str(safe_float(candidate.get("takerFeePct", 0))),
        "--slippage-pct", str(safe_float(candidate.get("slippageBps", 0)) / 100),
    ])
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 180), 180)),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Fast candidate discovery timed out.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "activePaperCandidate": candidate_summary(candidate),
            "search": {"symbols": symbols, "timeframes": timeframes, "strategy": strategy, "period": period, "maxCombos": max_combos, "limit": limit_raw},
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "warnings": ["Discovery timed out before returning candidate rows."],
        }, 504
    payload = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except Exception:
            payload = {"ok": False, "error": "Fast candidate discovery returned non-JSON output.", "stdout": completed.stdout.strip()}
    if payload is None:
        payload = {"ok": False, "error": completed.stderr.strip() or "Fast candidate discovery returned no output."}
    rows = [normalize_fast_candidate_row(row) for row in payload.get("rows", [])]
    best = best_fast_candidate(rows)
    warnings = dedupe_list((payload.get("warnings") or []) + ([real_detail] if real_enabled else []))
    if completed.returncode != 0:
        warnings.append(completed.stderr.strip() or "Fast candidate discovery command failed.")
    response = {
        "ok": completed.returncode == 0 and payload.get("ok", True) is not False,
        "realTradingEnabled": real_enabled,
        "paperEnabled": paper_enabled,
        "activePaperCandidate": candidate_summary(candidate),
        "search": payload.get("search") or {"symbols": symbols, "timeframes": timeframes, "strategy": strategy, "period": period, "maxCombos": max_combos, "limit": limit_raw},
        "rows": rows,
        "bestCandidate": best,
        "recommendation": fast_candidate_recommendation(best, rows),
        "warnings": warnings,
        "command": " ".join(command),
    }
    if completed.returncode != 0:
        response["returnCode"] = completed.returncode
        response["stderr"] = completed.stderr.strip()
    return response, 200 if response["ok"] else 502


def normalize_fast_candidate_row(row: dict) -> dict:
    status = row.get("status") or row.get("qualityStatus") or "FAIL"
    rejection_reasons = row.get("rejectionReasons") or []
    warnings = row.get("warnings") or []
    return {
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "strategy": row.get("strategy"),
        "status": status,
        "qualityStatus": row.get("qualityStatus") or status,
        "params": row.get("params") or {},
        "train": row.get("train") or {},
        "test": row.get("test") or {},
        "full": row.get("full") or {},
        "trades": int(safe_float(row.get("trades"), 0)),
        "profitFactor": safe_float(row.get("profitFactor"), 0),
        "totalReturnPct": safe_float(row.get("totalReturnPct"), 0),
        "maxDrawdownPct": safe_float(row.get("maxDrawdownPct"), 0),
        "winRate": safe_float(row.get("winRate"), 0),
        "score": safe_float(row.get("score"), -999),
        "warnings": warnings,
        "rejectionReasons": rejection_reasons,
    }


def best_fast_candidate(rows: list[dict]) -> dict | None:
    reviewable = [row for row in rows if fast_candidate_reviewable(row)]
    if not reviewable:
        return None
    return sorted(reviewable, key=lambda row: safe_float(row.get("score"), -999), reverse=True)[0]


def fast_candidate_reviewable(row: dict) -> bool:
    severe_codes = {
        "zero_trades",
        "too_few_test_trades",
        "too_few_full_trades",
        "test_profit_factor_below_min",
        "full_profit_factor_below_min",
        "negative_test_return",
        "negative_full_return",
        "high_drawdown",
        "train_test_overfit_gap",
        "unstable_walk_forward",
        "strongly_negative_train_return",
        "train_only_success_test_failure",
    }
    codes = {str(item.get("code") or item).lower() for item in row.get("rejectionReasons", [])}
    return (
        row.get("qualityStatus") in {"PASS", "WARN"}
        and int(safe_float(row.get("trades"), 0)) >= 20
        and safe_float(row.get("totalReturnPct"), 0) > 0
        and safe_float(row.get("profitFactor"), 0) >= 1.05
        and safe_float(row.get("maxDrawdownPct"), 999) <= 25
        and not (codes & severe_codes)
    )


def fast_candidate_recommendation(best: dict | None, rows: list[dict]) -> dict:
    if best:
        return {
            "action": "REVIEW_FAST_CANDIDATE",
            "reason": f"{best.get('symbol')} {best.get('timeframe')} has enough trades, positive return, PF {best.get('profitFactor')}, and acceptable drawdown for manual review only.",
        }
    if rows:
        return {
            "action": "KEEP_1H",
            "reason": "No separately optimized fast-timeframe row passed the review gate. Keep observing the current 1h paper candidate.",
        }
    return {
        "action": "NO_FAST_CANDIDATE",
        "reason": "Discovery returned no candidate rows.",
    }


def build_paper_observation_report(args) -> dict:
    report_args = {**dict(args), "activeOnly": "true"}
    if args.get("counterLimit") and not report_args.get("limit"):
        report_args["limit"] = args.get("counterLimit")
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active = primary_active_market(candidate)
    active_key = paper_market_key(active)
    session_summary = build_paper_session_summary(report_args)
    active_observation = build_paper_active_observation(report_args)
    observation_targets = build_paper_observation_targets(report_args)
    observation_quality = build_paper_observation_quality(report_args)
    signal_diagnostics, _signal_status = build_paper_active_signal_diagnostics({"limit": "20"})
    runner_summary = build_paper_runner_summary(args)
    observation_counters = build_paper_observation_counters(report_args)
    compact_counters = compact_observation_counters(observation_counters)
    stop_rules = build_paper_stop_rules(args) if paper_enabled else {
        "status": "OK",
        "rules": [],
        "nextAction": {"action": "PAPER_DISABLED_NO_STOP_NEEDED", "reason": "Paper is disabled; stop rules are informational only."},
    }
    performance = session_summary.get("performance") or {}
    baseline_source = observation_quality.get("baseline") or build_paper_baseline_comparison(candidate, {}, [])
    targets = observation_targets.get("targets") or {}
    target_progress = observation_targets.get("progress") or {}
    target_status = observation_targets.get("status")
    quality = observation_quality.get("quality") or {}
    quality_status = quality.get("status")
    diagnostic = signal_diagnostics.get("diagnostics") or {}
    diagnostic_next = signal_diagnostics.get("nextAction") or {}
    tick_state = active_observation.get("tickReadiness") or {}
    freshness = active_observation.get("freshness") or {}
    warnings = []
    informational_warnings = []
    stop_status = str(stop_rules.get("status") or "UNKNOWN").upper()
    tick_status = str(tick_state.get("status") or "UNKNOWN").upper()
    target_status_upper = str(target_status or "UNKNOWN").upper()
    quality_status_upper = str(quality_status or "UNKNOWN").upper()
    active_warning_count = int(safe_float((active_observation.get("warnings") or {}).get("count"), 0))
    stop_failures = [
        rule for rule in stop_rules.get("rules", [])
        if not rule.get("pass") and str(rule.get("severity") or "").upper() == "STOP"
    ]
    if real_enabled:
        warnings.append(real_detail)
    if stop_failures:
        warnings.extend([rule.get("detail") for rule in stop_failures if rule.get("detail")])
    warnings.extend(observation_targets.get("blockingIssues") or [])
    if active_warning_count:
        latest_warning = ((active_observation.get("warnings") or {}).get("latest") or {}).get("reason")
        warnings.append(latest_warning or f"{active_warning_count} active-market warning(s) need review.")
    if tick_status in {"DATA_STALE", "NOT_INITIALIZED", "BLOCKED"}:
        warnings.append(tick_state.get("activeMarketReason") or f"Active-market tick readiness is {tick_status}.")
    warnings.extend(quality.get("warnings") or [])
    warnings.extend(observation_targets.get("warnings") or [])
    informational_warnings.extend(observation_targets.get("informationalWarnings") or [])
    runner_counts = runner_summary.get("counts") or {}
    if runner_summary.get("exists"):
        informational_warnings.append(
            f"Runner log has {runner_counts.get('iterations', 0)} iteration(s), {runner_counts.get('ticksRun', 0)} tick(s) run, and {runner_counts.get('ticksSkipped', 0)} skipped tick(s)."
        )
    else:
        informational_warnings.extend(runner_summary.get("warnings") or [])

    ticks = int(safe_float(target_progress.get("ticksObserved"), 0))
    signals = int(safe_float(target_progress.get("signalsObserved"), 0))
    closed_trades = int(safe_float(target_progress.get("closedTrades"), 0))
    open_positions = int(safe_float(target_progress.get("openPositions"), 0))
    session_age_hours = safe_float(target_progress.get("sessionAgeHours"), 0)
    minimum_met = bool((observation_targets.get("readiness") or {}).get("minimumTargetsMet"))
    active_market_healthy = active_warning_count <= 0 and tick_status not in {"DATA_STALE", "NOT_INITIALIZED", "BLOCKED"}

    if real_enabled:
        status = "PAUSE_RECOMMENDED"
        title = "Real Trading Safety Review Required"
        summary = "Real trading appears enabled, so paper observation should pause until the flag or mode is reviewed."
        next_action = {"action": "DISABLE_REAL_TRADING_FLAG", "reason": real_detail}
    elif not paper_enabled:
        status = "DISABLED"
        title = "Paper Observation Disabled"
        summary = "Paper simulation is disabled. The promoted candidate is not collecting forward paper evidence right now."
        next_action = {"action": "ENABLE_PAPER_SIMULATION", "reason": "Enable paper manually only after reviewing readiness. This report does not enable paper automatically."}
    elif stop_status == "STOP_RECOMMENDED":
        status = "PAUSE_RECOMMENDED"
        title = "Pause Paper Observation"
        summary = "One or more stop rules recommend pausing paper observation before more ticks run."
        next_action = stop_rules.get("nextAction") or {"action": "PAUSE_PAPER_SIMULATION", "reason": "Review failed paper stop rules."}
    elif not active_market_healthy:
        status = "WATCH"
        title = "Active Market Needs Attention"
        summary = "The active paper market is not clean enough for a confident observation verdict."
        next_action = {"action": "REVIEW_ACTIVE_MARKET_HEALTH", "reason": tick_state.get("activeMarketReason") or "Review active-market warnings and tick readiness."}
    elif ticks <= 0 and signals <= 0 and closed_trades <= 0:
        status = "TOO_EARLY"
        title = "Too Early To Judge"
        summary = "No meaningful forward paper evidence has accumulated for the active market yet."
        next_action = {"action": "RUN_PAPER_ONCE_WHEN_READY", "reason": "Run a paper tick only when tick readiness says it is useful. Never use this as a real-trading signal."}
    elif tick_status == "WAIT_FOR_NEXT_CANDLE":
        status = "WAIT_FOR_NEXT_CANDLE"
        title = "Waiting For Next Closed Candle"
        summary = "The active market is healthy, but no newer closed candle is available for a useful paper tick yet."
        next_action = {"action": "WAIT_FOR_NEXT_CANDLE", "reason": tick_state.get("activeMarketReason") or "Wait until the next active-market candle closes before running another useful tick."}
    elif quality_status_upper == "TOO_EARLY" or closed_trades <= 0:
        status = "TOO_EARLY"
        title = "Evidence Still Too Early"
        summary = "Paper has started producing session evidence, but closed paper trades are not available yet."
        next_action = {"action": "OBSERVE_MORE", "reason": "Continue paper-only observation until closed trades and target hours/ticks accumulate."}
    elif not minimum_met or target_status_upper in {"TOO_EARLY", "OBSERVE_MORE"}:
        status = "OBSERVE_MORE"
        title = "Continue Observing"
        summary = "Forward paper evidence is accumulating, but the minimum observation targets are not met yet."
        next_action = {"action": "OBSERVE_MORE", "reason": "Keep collecting paper-only evidence; this report never recommends real trading."}
    elif target_status_upper == "READY_FOR_PAPER_REVIEW":
        status = "READY_FOR_REVIEW"
        title = "Ready For Paper Review"
        summary = "Minimum paper observation targets are met with no active blockers. This is a review verdict only."
        next_action = {"action": "REVIEW_PAPER_RESULTS", "reason": "Review the paper evidence manually. Do not enable real trading from this report."}
    else:
        status = "WATCH" if quality_status_upper == "WATCH" or stop_status == "WATCH" else "OBSERVE_MORE"
        title = "Review Paper Observation"
        summary = "Paper observation is available, but the current signals should be reviewed before any judgment."
        next_action = {"action": "REVIEW_PAPER_OBSERVATION", "reason": "Review warnings, stop rules, and target progress. This report never recommends real trading."}

    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "activeMarket": {
            "symbol": active.get("symbol"),
            "timeframe": active.get("interval") or active.get("timeframe"),
            "marketKey": active_key,
            "source": candidate.get("source"),
            "freshnessStatus": freshness.get("status"),
            "tickReadinessStatus": tick_status,
        },
        "verdict": {
            "status": status,
            "title": title,
            "summary": summary,
            "nextAction": next_action,
        },
        "evidence": {
            "sessionAgeHours": session_age_hours,
            "ticksObserved": ticks,
            "runnerTicksRun": compact_counters.get("runnerTicksRun"),
            "runnerTicksSkipped": compact_counters.get("runnerTicksSkipped"),
            "sessionPaperTicks": compact_counters.get("sessionPaperTicks"),
            "processedCandleDeltaTotal": compact_counters.get("processedCandleDeltaTotal"),
            "signalsObserved": signals,
            "closedTrades": closed_trades,
            "openPositions": open_positions,
            "activeWarnings": active_warning_count,
            "stopRulesStatus": stop_rules.get("status"),
            "observationTargetStatus": target_status,
            "observationQualityStatus": quality_status,
            "counterConsistencyStatus": compact_counters.get("counterConsistencyStatus"),
        },
        "progress": {
            "minSessionHours": targets.get("minSessionHours"),
            "minPaperTicks": targets.get("minPaperTicks"),
            "minClosedTrades": targets.get("minClosedTrades"),
            "preferredClosedTrades": targets.get("preferredClosedTrades"),
            "remainingSessionHours": target_progress.get("remainingSessionHours"),
            "remainingPaperTicks": target_progress.get("remainingPaperTicks"),
            "remainingClosedTrades": target_progress.get("remainingClosedTrades"),
        },
        "performance": {
            "equity": performance.get("equity"),
            "realizedPnl": performance.get("realizedPnl"),
            "unrealizedPnl": performance.get("unrealizedPnl"),
            "returnPct": performance.get("returnPct"),
            "winRate": (observation_quality.get("performance") or {}).get("winRate"),
            "profitFactor": (observation_quality.get("performance") or {}).get("profitFactor"),
            "maxDrawdownPct": performance.get("maxDrawdownPct"),
        },
        "baseline": {
            "available": baseline_source.get("available"),
            "expectedReturnPct": baseline_source.get("expectedReturnPct"),
            "expectedProfitFactor": baseline_source.get("expectedProfitFactor"),
            "expectedTrades": baseline_source.get("expectedTrades"),
            "expectedMaxDrawdownPct": baseline_source.get("expectedMaxDrawdownPct"),
        },
        "latestSignalDiagnosticStatus": diagnostic.get("signal"),
        "latestSignalDiagnosticReason": diagnostic.get("reason"),
        "latestSignalDiagnosticAction": diagnostic_next.get("action"),
        "observationCounters": compact_counters,
        "runnerTicksRun": compact_counters.get("runnerTicksRun"),
        "runnerTicksSkipped": compact_counters.get("runnerTicksSkipped"),
        "processedCandleDeltaTotal": compact_counters.get("processedCandleDeltaTotal"),
        "counterConsistencyStatus": compact_counters.get("counterConsistencyStatus"),
        "warnings": dedupe_list([warning for warning in warnings if warning]),
        "informationalWarnings": dedupe_list([warning for warning in informational_warnings + compact_counters.get("warnings", []) if warning]),
    }


def paper_interval_seconds(interval: str | None) -> int:
    raw = str(interval or "").strip().lower()
    if raw.endswith("m"):
        return int(safe_float(raw[:-1], 0) * 60)
    if raw.endswith("h"):
        return int(safe_float(raw[:-1], 0) * 3600)
    if raw.endswith("d"):
        return int(safe_float(raw[:-1], 0) * 86400)
    minutes = safe_float(raw, 0)
    return int(minutes * 60) if minutes else 3600


def epoch_to_iso(epoch_value) -> str | None:
    epoch = safe_float(epoch_value, 0)
    if epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def paper_report_freshness() -> dict:
    report = read_json_file(os.path.join(app.root_path, "reports", "paper-freshness.json"), {})
    return report.get("freshness") if isinstance(report.get("freshness"), dict) else {}


def paper_market_freshness(market: dict, state: dict, report_freshness: dict | None = None) -> dict:
    key = paper_market_key(market)
    state_freshness = ((state.get("freshness") or {}).get(key) or {}) if key else {}
    report_item = ((report_freshness or {}).get(key) or {}) if key else {}
    freshness = {**state_freshness, **report_item}
    last_processed = ((state.get("lastProcessedCandleTime") or {}).get(key)) if isinstance(state.get("lastProcessedCandleTime"), dict) else None
    latest = freshness.get("latestCandleTime")
    latest_epoch = safe_float(latest, 0)
    processed_epoch = safe_float(last_processed, 0)
    interval_seconds = int(safe_float(freshness.get("expectedIntervalSeconds"), paper_interval_seconds(market.get("interval") or market.get("timeframe"))))
    next_expected = processed_epoch + interval_seconds if processed_epoch else None
    return {
        "marketKey": key,
        "symbol": market.get("symbol"),
        "interval": market.get("interval") or market.get("timeframe"),
        "mode": market.get("mode", "active"),
        "initialized": processed_epoch > 0,
        "latestCandleTime": latest,
        "latestClosedCandleTime": latest,
        "latestCandleAt": epoch_to_iso(latest),
        "latestClosedCandleAt": epoch_to_iso(latest),
        "lastProcessedCandleTime": last_processed,
        "lastProcessedCandleAt": epoch_to_iso(last_processed),
        "nextExpectedClosedCandleTime": next_expected,
        "nextExpectedClosedCandleAt": epoch_to_iso(next_expected),
        "latestClosedCandleAgeSeconds": freshness.get("latestClosedCandleAgeSeconds"),
        "staleThresholdSeconds": freshness.get("staleThresholdSeconds"),
        "expectedIntervalSeconds": interval_seconds,
        "isStale": bool(freshness.get("isStale")),
        "dataStatus": "STALE_CACHE" if freshness.get("isStale") else "FRESH_OR_CURRENT",
        "dataSourceFailureKnown": False,
        "cacheHit": bool(freshness.get("cacheHit")),
        "cacheMiss": bool(freshness.get("cacheMiss")),
        "fetchedFromBybit": bool(freshness.get("fetchedFromBybit")),
        "newerClosedCandleAvailable": bool(latest_epoch and processed_epoch and latest_epoch > processed_epoch),
    }


def paper_next_useful_tick(active: dict, useful_now: bool = False) -> tuple[str | None, int | None, float | None]:
    if useful_now:
        return None, 0, None
    latest = safe_float(active.get("latestCandleTime"), 0)
    processed = safe_float(active.get("lastProcessedCandleTime"), 0)
    interval = int(safe_float(active.get("expectedIntervalSeconds"), paper_interval_seconds(active.get("interval"))))
    if not latest and not processed:
        return None, None, None
    target = (processed or latest) + interval
    now_epoch = datetime.now(timezone.utc).timestamp()
    while target <= now_epoch:
        target += interval
    return epoch_to_iso(target), max(0, int(target - now_epoch)), target


def build_paper_tick_readiness(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    report_freshness = paper_report_freshness()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    active_markets = candidate_symbols_by_mode(candidate, "active")
    watch_markets = candidate_symbols_by_mode(candidate, "watch")
    active_freshness = [paper_market_freshness(market, state, report_freshness) for market in active_markets]
    watch_freshness = [paper_market_freshness(market, state, report_freshness) for market in watch_markets]
    active = active_freshness[0] if active_freshness else {}
    reasons = []
    active_warnings = []
    watch_warnings = []
    stale_watch_warnings = []
    blocking_warnings = []
    informational_warnings = []
    useful_now = False
    if not paper_enabled:
        status = "DISABLED"
        reasons.append("Paper simulation is disabled; ticking is not useful until paper is manually enabled.")
        action = "REVIEW_ENABLE_PAPER_SIMULATION"
        reason = "Paper is disabled. This endpoint does not enable paper automatically."
    elif real_enabled:
        status = "BLOCKED"
        reasons.append(real_detail)
        action = "DISABLE_REAL_TRADING_FLAG"
        reason = real_detail
    elif not active:
        status = "NOT_INITIALIZED"
        reasons.append("No active paper market is configured.")
        action = "REVIEW_PAPER_CANDIDATE_CONFIG"
        reason = "An active market is required before a useful paper tick can run."
    elif not active.get("initialized"):
        status = "NOT_INITIALIZED"
        reasons.append(f"Active market {active.get('marketKey') or '-'} is not initialized.")
        action = "RUN_PAPER_INIT_BEFORE_TICK"
        reason = "Run npm run paper:init, then recheck tick readiness."
    elif active.get("isStale"):
        status = "DATA_STALE"
        active_warnings.append(f"Active market {active.get('marketKey')} data is stale; paper tick is expected to skip processing.")
        blocking_warnings.append(active_warnings[-1])
        reasons.append(active_warnings[-1])
        action = "REFRESH_MARKET_DATA"
        reason = "Refresh market data before running another paper tick."
    elif active.get("newerClosedCandleAvailable"):
        status = "READY"
        useful_now = True
        reasons.append(f"Active market {active.get('marketKey')} has a newer closed candle available.")
        action = "RUN_PAPER_TICK"
        reason = "Run npm run paper:tick or POST /api/paper/tick-once to process the newer closed candle."
    else:
        status = "WAIT_FOR_NEXT_CANDLE"
        reasons.append(f"Active market {active.get('marketKey')} already processed the latest closed candle.")
        action = "WAIT_FOR_NEXT_CLOSED_CANDLE"
        reason = "A manual tick is allowed but is expected to produce no new trade event until the next closed candle."
    for item in watch_freshness:
        if item.get("isStale"):
            warning = f"Watch market {item.get('marketKey')} data is stale; active-market readiness is unaffected."
            watch_warnings.append(warning)
            stale_watch_warnings.append(warning)
        if not item.get("initialized"):
            watch_warnings.append(f"Watch market {item.get('marketKey')} is not initialized; active-market readiness is unaffected.")
    informational_warnings = watch_warnings[:]
    if active and (useful_now or status == "WAIT_FOR_NEXT_CANDLE"):
        next_at, seconds_until, next_target = paper_next_useful_tick(active, useful_now)
    else:
        next_at, seconds_until, next_target = None, None, None
    active_market_reason = reasons[0] if reasons else None
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "candidate": candidate_summary(candidate),
        "activeMarkets": active_markets,
        "watchMarkets": watch_markets,
        "freshness": {
            "active": active_freshness,
            "watch": watch_freshness,
        },
        "tickReadiness": {
            "status": status,
            "usefulNow": useful_now,
            "latestClosedCandleTime": active.get("latestClosedCandleAt"),
            "lastProcessedCandleTime": active.get("lastProcessedCandleAt"),
            "nextExpectedClosedCandleTime": epoch_to_iso(next_target) if next_target else active.get("nextExpectedClosedCandleAt"),
            "nextUsefulTickAt": next_at,
            "secondsUntilNextUsefulTick": seconds_until,
            "activeMarketReason": active_market_reason,
            "reasons": dedupe_list(reasons),
            "warnings": dedupe_list(blocking_warnings),
            "activeWarnings": dedupe_list(active_warnings),
            "watchWarnings": dedupe_list(watch_warnings),
            "staleWatchWarnings": dedupe_list(stale_watch_warnings),
            "blockingWarnings": dedupe_list(blocking_warnings),
            "informationalWarnings": dedupe_list(informational_warnings),
        },
        "nextAction": {
            "action": action,
            "reason": reason,
            "recommendedCommand": package_script_command("paper:tick") if useful_now else None,
            "recommendedApiAction": "POST /api/paper/tick-once" if useful_now else "POST /api/paper/refresh-active-market" if status == "DATA_STALE" else None,
        },
    }


def request_bool(args, payload: dict, name: str, default: bool = False) -> bool:
    if name in payload:
        value = payload.get(name)
    else:
        value = args.get(name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def cache_snapshot_for_market(market: dict, source: str) -> dict:
    symbol = market.get("symbol")
    timeframe = market.get("interval") or market.get("timeframe")
    if source != "bybit" or not symbol or not timeframe:
        return {"cachedCandles": None, "latestCandleTime": None, "warnings": [f"Cache inspection is only supported for Bybit, not {source}."]}
    cache = inspect_bybit_cache(symbol, timeframe)
    return {
        "cachedCandles": cache.get("cachedCandles"),
        "latestCandleTime": cache.get("lastCandleTime"),
        "warnings": cache.get("warnings") or [],
    }


def active_market_refresh_rows(active_markets: list[dict], source: str, before_cache: dict, after_cache: dict, stdout_payload: dict | None, return_code: int) -> list[dict]:
    freshness = (stdout_payload or {}).get("freshness") if isinstance(stdout_payload, dict) else {}
    rows = []
    for market in active_markets:
        key = paper_market_key(market)
        before = before_cache.get(key) or {}
        after = after_cache.get(key) or {}
        item_freshness = (freshness or {}).get(key) or {}
        status = "FAILED" if return_code != 0 else "UNCHANGED"
        if return_code == 0 and (
            safe_float(after.get("latestCandleTime"), 0) > safe_float(before.get("latestCandleTime"), 0)
            or safe_float(after.get("cachedCandles"), 0) > safe_float(before.get("cachedCandles"), 0)
            or item_freshness.get("fetchedFromBybit")
        ):
            status = "REFRESHED"
        rows.append({
            "symbol": market.get("symbol"),
            "timeframe": market.get("interval") or market.get("timeframe"),
            "status": status,
            "candlesBefore": before.get("cachedCandles"),
            "candlesAfter": after.get("cachedCandles"),
            "latestCandleTimeBefore": before.get("latestCandleTime"),
            "latestCandleTimeAfter": after.get("latestCandleTime"),
            "freshness": item_freshness,
            "warnings": dedupe_list((before.get("warnings") or []) + (after.get("warnings") or [])),
        })
    return rows


def refresh_active_paper_market(args, payload: dict) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    before = build_paper_tick_readiness(args)
    active_markets = candidate_symbols_by_mode(candidate, "active")
    source = candidate.get("source") or "bybit"
    if real_enabled:
        return {
            "ok": False,
            "error": real_detail,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": True,
            "activeMarkets": active_markets,
            "before": {"tickReadiness": before.get("tickReadiness"), "freshness": before.get("freshness")},
        }, 400
    if source != "bybit":
        return {
            "ok": False,
            "error": f"Active paper refresh currently supports Bybit only, not {source}.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": False,
            "activeMarkets": active_markets,
            "before": {"tickReadiness": before.get("tickReadiness"), "freshness": before.get("freshness")},
        }, 400
    before_cache = {paper_market_key(market): cache_snapshot_for_market(market, source) for market in active_markets}
    command_args = package_node_script_args("paper:refresh") + ["--active-only"]
    completed = subprocess.run(
        command_args,
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=int(safe_float(args.get("timeout_seconds", payload.get("timeoutSeconds", 90)), 90)),
    )
    stdout_payload = None
    if completed.stdout.strip():
        try:
            stdout_payload = json.loads(completed.stdout)
        except Exception:
            stdout_payload = {"raw": completed.stdout.strip()}
    after = build_paper_tick_readiness(args)
    after_cache = {paper_market_key(market): cache_snapshot_for_market(market, source) for market in active_markets}
    markets = active_market_refresh_rows(active_markets, source, before_cache, after_cache, stdout_payload, completed.returncode)
    after_readiness = after.get("tickReadiness") or {}
    then_tick = request_bool(args, payload, "thenTick", False)
    tick_result = None
    if completed.returncode == 0 and then_tick and after_readiness.get("usefulNow"):
        tick_result, _tick_status = run_paper_tick_once(args)
    if completed.returncode != 0:
        action = "CHECK_DATA_SOURCE"
        reason = completed.stderr.strip() or "Active-market refresh command failed."
    elif then_tick and tick_result is not None:
        action = "NO_ACTION"
        reason = "Refresh completed and useful paper tick was run."
    elif after_readiness.get("usefulNow"):
        action = "RUN_TICK"
        reason = "Active-market candles are usable; run POST /api/paper/tick-once or npm run paper:tick."
    elif after_readiness.get("status") == "WAIT_FOR_NEXT_CANDLE":
        action = "WAIT_FOR_NEXT_CANDLE"
        reason = "Refresh completed, but the latest closed active-market candle is already processed."
    elif after_readiness.get("status") == "DATA_STALE":
        action = "CHECK_DATA_SOURCE"
        reason = "Refresh completed, but active-market data is still stale."
    else:
        action = "NO_ACTION"
        reason = "Refresh completed; no useful paper tick is available right now."
    result = {
        "ok": completed.returncode == 0,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": False,
        "activeMarkets": active_markets,
        "before": {
            "tickReadiness": before.get("tickReadiness"),
            "freshness": before.get("freshness"),
        },
        "refresh": {
            "attempted": True,
            "command": package_script_command("paper:refresh") + " -- --active-only",
            "returnCode": completed.returncode,
            "markets": markets,
            "stdout": stdout_payload,
            "stderr": completed.stderr.strip(),
        },
        "after": {
            "tickReadiness": after.get("tickReadiness"),
            "freshness": after.get("freshness"),
        },
        "nextAction": {
            "action": action,
            "reason": reason,
        },
    }
    if then_tick:
        result["thenTick"] = {
            "requested": True,
            "ran": tick_result is not None,
            "reason": "Tick ran because refreshed readiness was useful." if tick_result is not None else f"Tick was not run because refreshed readiness is {after_readiness.get('status')}.",
            "tickResult": tick_result,
        }
    if completed.returncode != 0:
        result["error"] = result["nextAction"]["reason"]
        return result, 502
    return result, 200


def compact_run_once_payload(payload: dict) -> dict:
    refresh = payload.get("refresh") or {}
    tick = payload.get("tickResult") or {}
    return {
        "ok": payload.get("ok"),
        "paperEnabled": payload.get("paperEnabled"),
        "realTradingEnabled": payload.get("realTradingEnabled"),
        "action": (payload.get("nextAction") or {}).get("action"),
        "reason": (payload.get("nextAction") or {}).get("reason"),
        "readinessBefore": ((payload.get("tickReadinessBefore") or {}).get("tickReadiness") or {}).get("status"),
        "readinessAfter": ((payload.get("tickReadinessAfter") or {}).get("tickReadiness") or {}).get("status"),
        "refreshOk": refresh.get("ok"),
        "refreshAction": (refresh.get("nextAction") or {}).get("action"),
        "tickRan": payload.get("tickRan"),
        "tickReturnCode": tick.get("returnCode"),
        "processedCandlesDelta": (tick.get("summary") or {}).get("processedCandlesDelta"),
        "stopRulesBefore": (payload.get("stopRulesBefore") or {}).get("status"),
        "stopRulesAfter": (payload.get("stopRulesAfter") or {}).get("status"),
        "observationStatus": (((payload.get("observationQuality") or {}).get("quality") or {}).get("status")),
        "observationTargetStatus": (payload.get("observationTargets") or {}).get("status"),
    }


def run_paper_once_controlled(args, payload: dict) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    readiness_before = build_paper_tick_readiness(args)
    if real_enabled:
        result = {
            "ok": False,
            "paperEnabled": paper_enabled,
            "realTradingEnabled": True,
            "tickRan": False,
            "tickReadinessBefore": readiness_before,
            "observationTargets": build_paper_observation_targets(args),
            "nextAction": {"action": "DISABLE_REAL_TRADING_FLAG", "reason": real_detail},
        }
        result["summary"] = compact_run_once_payload(result)
        return result, 400
    if not paper_enabled:
        stop_rules = build_paper_stop_rules(args)
        observation = build_paper_observation_quality(args)
        observation_targets = build_paper_observation_targets(args)
        result = {
            "ok": True,
            "paperEnabled": False,
            "realTradingEnabled": False,
            "tickRan": False,
            "tickReadinessBefore": readiness_before,
            "refresh": {"attempted": False, "reason": "Paper simulation is disabled."},
            "stopRulesBefore": stop_rules,
            "stopRulesAfter": stop_rules,
            "observationQuality": observation,
            "observationTargets": observation_targets,
            "nextAction": {
                "action": "PAPER_DISABLED",
                "reason": "Paper simulation must be enabled manually before local run-once can refresh and tick.",
            },
        }
        result["summary"] = compact_run_once_payload(result)
        return result, 200
    refresh_result, refresh_status = refresh_active_paper_market(args, {**payload, "thenTick": False})
    readiness_after_refresh = build_paper_tick_readiness(args)
    stop_rules_before = build_paper_stop_rules(args)
    tick_result = None
    tick_ran = False
    stop_status = stop_rules_before.get("status")
    readiness_status = (readiness_after_refresh.get("tickReadiness") or {}).get("status")
    useful_now = bool((readiness_after_refresh.get("tickReadiness") or {}).get("usefulNow"))
    if refresh_status >= 400:
        next_action = {"action": "CHECK_DATA_SOURCE", "reason": (refresh_result.get("nextAction") or {}).get("reason") or "Active-market refresh failed."}
    elif stop_status == "STOP_RECOMMENDED":
        next_action = {"action": "REVIEW_STOP_RULES", "reason": "Stop rules recommend pausing paper; tick was not run."}
    elif useful_now:
        tick_result, _tick_status = run_paper_tick_once(args)
        tick_ran = bool(tick_result and tick_result.get("ok"))
        next_action = {"action": "MONITOR_PAPER", "reason": "Active market was refreshed and useful paper tick was run."}
    elif readiness_status == "WAIT_FOR_NEXT_CANDLE":
        next_action = {"action": "WAIT_FOR_NEXT_CANDLE", "reason": "Active market is current; no newer closed candle is available for a useful tick."}
    elif readiness_status == "DATA_STALE":
        next_action = {"action": "CHECK_DATA_SOURCE", "reason": "Active-market data remains stale after refresh; tick was not run."}
    else:
        next_action = {"action": "NO_ACTION", "reason": f"Tick was not run because readiness is {readiness_status or 'UNKNOWN'}."}
    stop_rules_after = build_paper_stop_rules(args)
    observation = build_paper_observation_quality(args)
    observation_targets = build_paper_observation_targets(args)
    final_candidate = load_paper_candidate_config()
    final_enabled = canonical_paper_enabled(final_candidate)
    final_real_enabled, _ = paper_real_trading_enabled()
    result = {
        "ok": refresh_status < 400,
        "paperEnabled": final_enabled,
        "realTradingEnabled": final_real_enabled,
        "candidate": candidate_summary(final_candidate),
        "tickRan": tick_ran,
        "tickReadinessBefore": readiness_before,
        "refresh": refresh_result,
        "tickReadinessAfterRefresh": readiness_after_refresh,
        "stopRulesBefore": stop_rules_before,
        "tickResult": tick_result,
        "stopRulesAfter": stop_rules_after,
        "observationQuality": observation,
        "observationTargets": observation_targets,
        "tickReadinessAfter": build_paper_tick_readiness(args),
        "nextAction": next_action,
    }
    result["summary"] = compact_run_once_payload(result)
    return result, 200 if result["ok"] else refresh_status


def build_paper_session_summary(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    journal = read_jsonl_tail(os.path.join(app.root_path, "reports", "paper-journal.jsonl"), 1000)
    runtime = build_paper_runtime_status(args)
    stop_rules = build_paper_stop_rules(args)
    real_enabled, _ = paper_real_trading_enabled()
    paper_enabled = canonical_paper_enabled(candidate)
    session = paper_session_window(candidate)
    session_events = [event for event in journal if event_in_session(event, session)]
    event_types = [str(event.get("eventType", "")).upper() for event in session_events]
    tick_times = {
        paper_tick_bucket(event_timestamp(event))
        for event in session_events
        if paper_tick_bucket(event_timestamp(event))
    }
    state_updated_at = parse_iso_timestamp(state.get("updatedAt"))
    if state_updated_at and session.get("started") and state_updated_at >= session["started"] and (not session.get("ended") or state_updated_at <= session["ended"]):
        bucket = paper_tick_bucket(state.get("updatedAt"))
        if bucket:
            tick_times.add(bucket)
    ticks = len(tick_times)
    status, next_action = paper_session_observation_status(paper_enabled, real_enabled, runtime, stop_rules, session_events, session)
    now = datetime.now(timezone.utc)
    started = session.get("started")
    ended = session.get("ended") or (None if not started else now)
    duration = int((ended - started).total_seconds()) if started and ended else 0
    account_equity = safe_float(candidate.get("accountEquity", state.get("accountEquity", 10000)), 10000)
    equity = safe_float(state.get("accountEquity", account_equity), account_equity)
    equity_curve = session_filtered_equity_curve(state, session)
    warnings = [
        event.get("reason") or event.get("message")
        for event in session_events
        if str(event.get("eventType", "")).upper() == "WARNING"
    ]
    baseline = build_paper_baseline_comparison(candidate, state, session_events)
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        "candidate": candidate_summary(candidate),
        "session": {
            "sessionId": session["sessionId"],
            "startedAt": session["startedAt"],
            "endedAt": session["endedAt"],
            "durationSeconds": duration,
            "status": status,
        },
        "activity": {
            "ticks": ticks,
            "processedCandles": int(safe_float(state.get("processedCandles"))),
            "signals": event_types.count("SIGNAL"),
            "entries": event_types.count("ENTRY"),
            "exits": event_types.count("EXIT"),
            "openPositions": len(state.get("openPositions", []) or []),
            "closedTrades": len(state.get("closedTrades", []) or []),
        },
        "performance": {
            "equity": equity,
            "realizedPnl": safe_float(state.get("realizedPnl")),
            "unrealizedPnl": safe_float(state.get("unrealizedPnl")),
            "fees": safe_float(state.get("cumulativeFees")),
            "slippage": safe_float(state.get("cumulativeSlippage")),
            "returnPct": round(((equity - account_equity) / account_equity * 100) if account_equity else 0, 4),
            "maxDrawdownPct": paper_max_drawdown(equity_curve),
        },
        "baselineComparison": baseline,
        "warnings": dedupe_list([warning for warning in warnings if warning]),
        "nextAction": next_action,
    }


def build_paper_recent_events(args) -> dict:
    limit = min(max(int(safe_float(args.get("limit", 50), 50)), 1), 200)
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    runtime = build_paper_runtime_status(args)
    initialized_markets = set(runtime.get("marketState", {}).get("initializedMarkets") or [])
    session = paper_session_window(candidate)
    journal = read_jsonl_tail(os.path.join(app.root_path, "reports", "paper-journal.jsonl"), max(limit * 3, 100))
    events = [normalized_paper_event(event, session, initialized_markets) for event in journal]
    return {
        "ok": True,
        "limit": limit,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": paper_real_trading_enabled()[0],
        "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        "session": {
            "sessionId": session["sessionId"],
            "startedAt": session["startedAt"],
            "endedAt": session["endedAt"],
        },
        "events": events[-limit:],
    }


def build_paper_runtime_status(args) -> dict:
    state_path = Path(app.root_path) / "data" / "paper-state.json"
    journal_path = Path(app.root_path) / "reports" / "paper-journal.jsonl"
    warnings = []
    candidate_load_error = None
    state_load_error = None
    try:
        candidate = load_paper_candidate_config()
    except Exception as exc:
        candidate = {}
        candidate_load_error = str(exc)
        warnings.append(f"Could not load paper candidate config: {exc}")
    try:
        state = read_json_file(str(state_path), {}) if state_path.exists() else {}
    except Exception as exc:
        state = {}
        state_load_error = str(exc)
        warnings.append(f"Could not load paper state: {exc}")
    journal = read_jsonl_tail(str(journal_path), 500) if journal_path.exists() else []
    active_markets = candidate_symbols_by_mode(candidate, "active") if candidate else []
    watch_markets = candidate_symbols_by_mode(candidate, "watch") if candidate else []
    required_markets = [paper_market_key(market) for market in active_markets if paper_market_key(market)]
    active_market_keys = set(required_markets)
    watch_market_keys = {paper_market_key(market) for market in watch_markets if paper_market_key(market)}
    last_processed = state.get("lastProcessedCandleTime") if isinstance(state.get("lastProcessedCandleTime"), dict) else {}
    initialized_markets = {key for key in required_markets if key in last_processed}
    missing_markets = [key for key in required_markets if key not in last_processed]
    state_file_exists = state_path.exists()
    journal_available = journal_path.exists()
    initialized = bool(state_file_exists and required_markets and not missing_markets and not state_load_error)
    if state_load_error:
        initialization_status = "ERROR"
    elif initialized:
        initialization_status = "READY"
    elif state_file_exists or required_markets:
        initialization_status = "NEEDS_INIT"
    else:
        initialization_status = "UNKNOWN"
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    warning_buckets = paper_runtime_warning_buckets(state, journal, initialized_markets, active_market_keys, watch_market_keys)
    last_event = latest_journal_event(journal)
    last_signal = latest_journal_event(journal, {"SIGNAL", "ENTRY", "EXIT", "SKIP"})
    last_tick_at = state.get("updatedAt")
    primary_market_key = required_markets[0] if required_markets else None
    primary_freshness = ((state.get("freshness") or {}).get(primary_market_key) or {}) if primary_market_key else {}
    primary_last_processed = last_processed.get(primary_market_key) if primary_market_key else None
    primary_latest_candle = primary_freshness.get("latestCandleTime")
    active_market_processed = bool(primary_market_key and primary_last_processed and primary_latest_candle and primary_last_processed >= primary_latest_candle)
    no_new_candle_available = bool(active_market_processed and primary_latest_candle)
    max_tick_age = int(safe_float(args.get("max_tick_age_seconds", 21600), 21600))
    last_tick_age = seconds_since(last_tick_at)
    tick_stale = bool(paper_enabled and (last_tick_age is None or last_tick_age > max_tick_age))
    health_reasons = []
    if paper_enabled and not initialized:
        health_status = "BLOCKED"
        health_reasons.append("Paper simulation is enabled but runtime state is not initialized for the active market.")
    elif real_enabled:
        health_status = "BLOCKED"
        health_reasons.append(real_detail)
    else:
        health_status = "OK"
        if not initialized:
            health_status = "WATCH"
            health_reasons.append("Paper runtime state needs initialization before paper simulation can be enabled.")
        if tick_stale:
            health_status = "WATCH" if health_status == "OK" else health_status
            health_reasons.append(f"Paper simulation is enabled but the last tick is stale ({last_tick_age} seconds old; watch threshold {max_tick_age}).")
        if warning_buckets["blockingWarnings"]:
            health_status = "WATCH" if health_status == "OK" else health_status
            health_reasons.append(f"{len(warning_buckets['blockingWarnings'])} active or unclassified runtime warning(s) are present.")
        if warning_buckets["staleWarnings"]:
            health_reasons.append(f"{len(warning_buckets['staleWarnings'])} older journal warning(s) were separated as stale.")
    if not health_reasons:
        health_reasons.append("Paper runtime state is initialized for the active market.")
    if paper_enabled and not initialized:
        next_action = {
            "action": "NEEDS_INIT_BEFORE_RUNNING",
            "reason": "Run npm run paper:init, then recheck runtime status before paper simulation continues.",
        }
    elif not paper_enabled and not initialized:
        next_action = {
            "action": "RUN_PAPER_INIT_BEFORE_ENABLE",
            "reason": "Initialize paper runtime state before manually reviewing paper enablement.",
        }
    elif paper_enabled:
        next_action = {
            "action": "MONITOR_PAPER_SIMULATION",
            "reason": "Paper simulation is enabled; monitor runtime warnings, stop rules, and candidate health.",
        }
    else:
        next_action = {
            "action": "REVIEW_ENABLE_PAPER_SIMULATION",
            "reason": "Paper runtime is initialized. This does not enable paper automatically.",
        }
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        "initialized": initialized,
        "initializationStatus": initialization_status,
        "candidate": candidate_summary(candidate),
        "marketState": {
            "stateFileExists": state_file_exists,
            "statePath": str(state_path.relative_to(app.root_path)),
            "requiredMarkets": required_markets,
            "initializedMarkets": sorted(initialized_markets),
            "missingMarkets": missing_markets,
            "lastProcessedCandleTime": {key: last_processed.get(key) for key in required_markets},
            "freshness": {key: (state.get("freshness") or {}).get(key) for key in required_markets},
        },
        "lastTick": {
            "updatedAt": last_tick_at,
            "ageSeconds": last_tick_age,
            "processedCandles": state.get("processedCandles", 0),
            "openPositions": len(state.get("openPositions", []) or []),
            "closedTrades": len(state.get("closedTrades", []) or []),
            "activeMarket": primary_market_key,
            "activeMarketLastProcessedCandleTime": primary_last_processed,
            "activeMarketLatestCandleTime": primary_latest_candle,
            "processedCurrentActiveMarket": active_market_processed,
            "noNewCandleAvailable": no_new_candle_available,
            "stale": tick_stale,
            "staleThresholdSeconds": max_tick_age,
        },
        "lastSignal": last_signal,
        "journal": {
            "available": journal_available,
            "path": str(journal_path.relative_to(app.root_path)),
            "lastEventAt": event_timestamp(last_event or {}),
            "staleWarnings": warning_buckets["staleWarnings"],
            "recentWarnings": warning_buckets["recentWarnings"],
            "activeWarnings": warning_buckets["activeWarnings"],
            "watchWarnings": warning_buckets["watchWarnings"],
            "staleWatchWarnings": warning_buckets["staleWatchWarnings"],
            "blockingWarnings": warning_buckets["blockingWarnings"],
            "informationalWarnings": warning_buckets["informationalWarnings"],
            "activeWarningCount": len(warning_buckets["activeWarnings"]),
            "watchWarningCount": len(warning_buckets["watchWarnings"]),
            "staleWatchWarningCount": len(warning_buckets["staleWatchWarnings"]),
        },
        "health": {
            "status": health_status,
            "reasons": health_reasons,
        },
        "nextAction": next_action,
        "activeWarnings": warning_buckets["activeWarnings"],
        "watchWarnings": warning_buckets["watchWarnings"],
        "staleWatchWarnings": warning_buckets["staleWatchWarnings"],
        "blockingWarnings": warning_buckets["blockingWarnings"],
        "informationalWarnings": warning_buckets["informationalWarnings"],
        "activeWarningCount": len(warning_buckets["activeWarnings"]),
        "watchWarningCount": len(warning_buckets["watchWarnings"]),
        "staleWatchWarningCount": len(warning_buckets["staleWatchWarnings"]),
        "warnings": dedupe_list(warnings + ([candidate_load_error] if candidate_load_error else [])),
    }


def paper_stop_rule(rules: list[dict], name: str, passed: bool, severity: str, detail: str):
    rules.append({
        "name": name,
        "pass": bool(passed),
        "severity": severity,
        "detail": detail,
    })


def build_paper_stop_rules(args) -> dict:
    candidate = load_paper_candidate_config()
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    runtime = build_paper_runtime_status(args)
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    rules = []
    if not paper_enabled:
        paper_stop_rule(rules, "paper simulation disabled", True, "INFO", "Paper simulation is disabled; no stop action is recommended.")
        paper_stop_rule(rules, "real trading disabled", not real_enabled, "STOP" if real_enabled else "INFO", real_detail)
        status = "WATCH" if real_enabled else "OK"
        next_action = {
            "action": "PAPER_DISABLED_NO_STOP_NEEDED" if not real_enabled else "DISABLE_REAL_TRADING_FLAG",
            "reason": "Paper is disabled, so stop rules are informational only." if not real_enabled else real_detail,
        }
        return {"ok": True, "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled, "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled), "status": status, "rules": rules, "nextAction": next_action}

    config_warnings = candidate_config_warnings(candidate)
    validation = validate_candidate_config(candidate, candidate_validation_rules(args))
    stability = build_candidate_stability_report({"compareCurrent": "true"})
    stability_status = (stability.get("validation") or {}).get("status")
    active = candidate_symbols_by_mode(candidate, "active")
    primary = active[0] if active else {}
    symbol = primary.get("symbol")
    timeframe = primary.get("interval") or primary.get("timeframe")
    data_status = "UNKNOWN"
    if symbol and timeframe:
        data_readiness = research_data_readiness(candidate.get("source", "bybit"), [symbol], [timeframe], args.get("period", "365d"), args.get("limit", "auto"))
        data_status = (((data_readiness.get("rows") or [{}])[0]) or {}).get("status", "UNKNOWN")
    health_payload = build_candidate_health(candidate_health_rules(args))
    paper_health = (health_payload.get("health") or {}).get("paper") or {}
    max_tick_age = int(safe_float(args.get("max_tick_age_seconds", 21600), 21600))
    last_tick_age = runtime.get("lastTick", {}).get("ageSeconds")
    runtime_errors = [
        warning for warning in runtime.get("journal", {}).get("blockingWarnings", [])
        if str(warning.get("eventType", "")).upper() == "ERROR" or "error" in str(warning.get("reason", "")).lower()
    ]
    repeated_errors = len(runtime_errors) >= int(safe_float(args.get("max_recent_runtime_errors", 3), 3))
    max_open_trades = int(safe_float(candidate.get("maxOpenTrades"), 1))
    open_positions = len(state.get("openPositions", []) or [])
    drawdown = safe_float(paper_health.get("maxDrawdown"))
    drawdown_limit = safe_float(args.get("watch_drawdown_above", candidate_health_rules(args)["failDrawdownAbove"]), 15)
    paper_stop_rule(rules, "validation remains PASS", validation.get("status") == "PASS", "STOP", f"Validation status: {validation.get('status', 'UNKNOWN')}.")
    paper_stop_rule(rules, "stability remains PASS", stability_status == "PASS", "STOP", f"Stability status: {stability_status or 'UNKNOWN'}.")
    paper_stop_rule(rules, "configWarnings empty", not config_warnings, "STOP", "No config warnings." if not config_warnings else "; ".join(config_warnings))
    paper_stop_rule(rules, "data readiness remains READY", data_status == "READY", "STOP", f"Data readiness status: {data_status}.")
    paper_stop_rule(rules, "runtime initialized", runtime.get("initialized"), "STOP", f"Initialization status: {runtime.get('initializationStatus')}.")
    paper_stop_rule(rules, "last tick is recent", last_tick_age is not None and last_tick_age <= max_tick_age, "WARN", f"Last tick age seconds: {last_tick_age}; watch threshold {max_tick_age}.")
    paper_stop_rule(rules, "runtime errors below repeat threshold", not repeated_errors, "STOP", f"Recent runtime error count: {len(runtime_errors)}.")
    paper_stop_rule(rules, "paper drawdown below watch threshold", drawdown <= drawdown_limit, "WARN", f"Paper drawdown: {drawdown}; watch threshold {drawdown_limit}.")
    paper_stop_rule(rules, "open trades within maxOpenTrades", open_positions <= max_open_trades, "STOP", f"Open trades: {open_positions}; maxOpenTrades: {max_open_trades}.")
    paper_stop_rule(rules, "real trading disabled", not real_enabled, "STOP", real_detail)
    stop_failures = [rule for rule in rules if not rule["pass"] and rule["severity"] == "STOP"]
    warn_failures = [rule for rule in rules if not rule["pass"] and rule["severity"] == "WARN"]
    if stop_failures:
        status = "STOP_RECOMMENDED"
        next_action = {"action": "DISABLE_PAPER_SIMULATION", "reason": f"{len(stop_failures)} stop-level paper rule(s) failed."}
    elif warn_failures:
        status = "WATCH"
        next_action = {"action": "WATCH_PAPER_RUNTIME", "reason": f"{len(warn_failures)} warning-level paper rule(s) failed."}
    else:
        status = "OK"
        next_action = {"action": "KEEP_MONITORING", "reason": "Paper stop rules are passing."}
    return {"ok": True, "paperEnabled": paper_enabled, "realTradingEnabled": real_enabled, "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled), "status": status, "rules": rules, "nextAction": next_action}


def paper_state_snapshot() -> dict:
    state = read_json_file(os.path.join(app.root_path, "data", "paper-state.json"), {})
    open_positions = state.get("openPositions", []) or []
    closed_trades = state.get("closedTrades", []) or []
    return {
        "updatedAt": state.get("updatedAt"),
        "processedCandles": int(safe_float(state.get("processedCandles"))),
        "openPositions": open_positions,
        "closedTrades": closed_trades,
        "openPositionIds": {str(item.get("id") or item.get("key") or idx) for idx, item in enumerate(open_positions)},
        "closedTradeIds": {str(item.get("id") or idx) for idx, item in enumerate(closed_trades)},
    }


def paper_new_items(after_items: list[dict], before_ids: set[str], fallback_prefix: str) -> list[dict]:
    new_items = []
    for idx, item in enumerate(after_items):
        item_id = str(item.get("id") or item.get("key") or f"{fallback_prefix}:{idx}")
        if item_id not in before_ids:
            new_items.append(item)
    return new_items


def run_paper_tick_once(args) -> tuple[dict, int]:
    candidate = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(candidate)
    real_enabled, real_detail = paper_real_trading_enabled()
    tick_readiness_before = build_paper_tick_readiness(args)
    if real_enabled:
        return {"ok": False, "error": real_detail, "paperEnabled": paper_enabled, "realTradingEnabled": True, "tickReadinessBefore": tick_readiness_before, "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled)}, 400
    if not paper_enabled:
        return {
            "ok": False,
            "error": "Paper simulation must be enabled manually before running one paper tick.",
            "paperEnabled": False,
            "realTradingEnabled": False,
            "tickReadinessBefore": tick_readiness_before,
            "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        }, 400
    freshness_blocked, freshness = paper_tick_blocked_by_freshness(args)
    if freshness_blocked:
        return {
            "ok": False,
            "error": freshness.get("message") or "Active paper-market data is stale or missing; paper tick was not run.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "tickReadinessBefore": tick_readiness_before,
            "freshness": freshness,
            "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        }, 400
    candle_alignment, alignment_status = build_research_paper_candle_alignment(args)
    if alignment_status >= 400 or candle_alignment.get("candleAlignmentStatus") == "MISMATCH" or candle_alignment.get("blockingForPaperTick"):
        return {
            "ok": False,
            "error": candle_alignment.get("explanation") or "Candle alignment mismatch; paper tick was not run.",
            "paperEnabled": paper_enabled,
            "realTradingEnabled": False,
            "paperTickRan": False,
            "paperStateChanged": False,
            "liveOrdersTouched": False,
            "tickReadinessBefore": tick_readiness_before,
            "candleAlignment": candle_alignment,
            "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        }, 400
    readiness = build_paper_readiness_report(args)
    if readiness.get("summary", {}).get("blockingIssues", 0):
        return {
            "ok": False,
            "error": "Paper readiness has blocking issues; paper tick was not run.",
            "readiness": readiness,
            "tickReadinessBefore": tick_readiness_before,
        }, 400
    before_runtime = build_paper_runtime_status(args)
    if not before_runtime.get("initialized"):
        return {
            "ok": False,
            "error": "Paper runtime is not initialized; run npm run paper:init before ticking.",
            "tickReadinessBefore": tick_readiness_before,
            "runtimeStatus": before_runtime,
        }, 400
    before_state = paper_state_snapshot()
    journal_path = os.path.join(app.root_path, "reports", "paper-journal.jsonl")
    before_events = read_jsonl_tail(journal_path, 500)
    before_event_ids = {str(event.get("eventId")) for event in before_events if event.get("eventId")}
    try:
        completed = subprocess.run(
            package_node_script_args("paper:tick"),
            text=True,
            capture_output=True,
            cwd=app.root_path,
            timeout=int(safe_float(args.get("timeout_seconds", 90), 90)),
        )
    except subprocess.TimeoutExpired as exc:
        after_runtime = build_paper_runtime_status(args)
        fresh = load_paper_candidate_config()
        fresh_enabled = canonical_paper_enabled(fresh)
        tick_readiness_after = build_paper_tick_readiness(args)
        return {
            "ok": False,
            "error": "Paper tick command timed out.",
            "command": package_script_command("paper:tick"),
            "paperEnabled": fresh_enabled,
            "realTradingEnabled": paper_real_trading_enabled()[0],
            "tickReadinessBefore": tick_readiness_before,
            "tickReadinessAfter": tick_readiness_after,
            "before": before_runtime,
            "after": after_runtime,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
            "consistencyWarnings": paper_enabled_consistency_warnings(fresh, fresh_enabled),
        }, 504
    after_runtime = build_paper_runtime_status(args)
    after_state = paper_state_snapshot()
    after_events = read_jsonl_tail(journal_path, 500)
    new_events = [
        event for event in after_events
        if event.get("eventId") and str(event.get("eventId")) not in before_event_ids
    ]
    stdout_payload = None
    if completed.stdout.strip():
        try:
            stdout_payload = json.loads(completed.stdout)
        except Exception:
            stdout_payload = {"raw": completed.stdout.strip()}
    warnings = []
    before_tick_status = tick_readiness_before.get("tickReadiness", {}).get("status")
    if before_tick_status == "WAIT_FOR_NEXT_CANDLE":
        warnings.append("No newer closed active-market candle is available; tick is expected to produce no new trade event.")
    if before_tick_status == "DATA_STALE":
        warnings.append("Active-market data is stale; tick is expected to skip paper processing until market data is refreshed.")
    processed_delta = after_state["processedCandles"] - before_state["processedCandles"]
    if processed_delta <= 0 and not new_events:
        warnings.append("No new candle or journal event was processed by this tick.")
    if after_runtime.get("lastTick", {}).get("noNewCandleAvailable"):
        warnings.append("Runtime status indicates no newer closed candle is available for the active market.")
    fresh = load_paper_candidate_config()
    fresh_enabled = canonical_paper_enabled(fresh)
    fresh_real_enabled, _ = paper_real_trading_enabled()
    tick_readiness_after = build_paper_tick_readiness(args)
    payload = {
        "ok": completed.returncode == 0,
        "command": package_script_command("paper:tick"),
        "ranCommand": " ".join(package_node_script_args("paper:tick")),
        "returnCode": completed.returncode,
        "paperEnabled": fresh_enabled,
        "realTradingEnabled": fresh_real_enabled,
        "tickReadinessBefore": tick_readiness_before,
        "tickReadinessAfter": tick_readiness_after,
        "before": before_runtime,
        "after": after_runtime,
        "sessionSummary": build_paper_session_summary(args),
        "stopRules": build_paper_stop_rules(args),
        "tickResult": stdout_payload,
        "summary": {
            "processedCandlesBefore": before_state["processedCandles"],
            "processedCandlesAfter": after_state["processedCandles"],
            "processedCandlesDelta": processed_delta,
            "newJournalEvents": len(new_events),
            "newSignals": len([event for event in new_events if str(event.get("eventType", "")).upper() in {"SIGNAL", "ENTRY", "EXIT", "SKIP"}]),
            "openPositionsBefore": len(before_state["openPositions"]),
            "openPositionsAfter": len(after_state["openPositions"]),
            "closedTradesBefore": len(before_state["closedTrades"]),
            "closedTradesAfter": len(after_state["closedTrades"]),
        },
        "newJournalEvents": new_events,
        "newSignals": [event for event in new_events if str(event.get("eventType", "")).upper() in {"SIGNAL", "ENTRY", "EXIT", "SKIP"}],
        "newOpenPositions": paper_new_items(after_state["openPositions"], before_state["openPositionIds"], "open"),
        "newClosedTrades": paper_new_items(after_state["closedTrades"], before_state["closedTradeIds"], "closed"),
        "consistencyWarnings": paper_enabled_consistency_warnings(fresh, fresh_enabled),
        "warnings": dedupe_list(warnings),
    }
    if completed.returncode != 0:
        payload["error"] = completed.stderr.strip() or "Paper tick command failed."
        return payload, 502
    return payload, 200


def build_paper_readiness_report(args) -> dict:
    checks = []
    warnings = []
    candidate_load_error = None
    try:
        candidate = load_paper_candidate_config()
    except Exception as exc:
        candidate = {}
        candidate_load_error = str(exc)
    active = candidate_symbols_by_mode(candidate, "active") if candidate else []
    primary = active[0] if active else {}
    symbol = primary.get("symbol")
    timeframe = primary.get("interval") or primary.get("timeframe")
    expected = expected_metrics_from_candidate(candidate) if candidate else {"source": None}
    config_warnings = candidate_config_warnings(candidate) if candidate else []
    learning_config = safe_learning_config(load_learning_config())
    paper_enabled = canonical_paper_enabled(candidate) if candidate else False

    paper_readiness_check(checks, "paper candidate config loads", not candidate_load_error, "BLOCK", candidate_load_error or "Paper candidate config loaded.")
    paper_readiness_check(checks, "current candidate exists", bool(candidate), "BLOCK", "Current paper candidate is available." if candidate else "No paper candidate config exists.")
    paper_readiness_check(checks, "configWarnings empty", not config_warnings, "BLOCK", "No config warnings." if not config_warnings else "; ".join(config_warnings))
    paper_readiness_check(checks, "paper simulation disabled", not paper_enabled, "WARN", "Paper simulation is disabled." if not paper_enabled else "Paper simulation is already enabled; readiness remains watch-only while paper is running.")
    paper_readiness_check(checks, "strategy exists", bool(candidate.get("strategy")), "BLOCK", f"Strategy: {candidate.get('strategy') or '-'}")
    paper_readiness_check(checks, "active symbol exists", bool(symbol), "BLOCK", f"Active symbol: {symbol or '-'}")
    paper_readiness_check(checks, "active timeframe exists", bool(timeframe), "BLOCK", f"Active timeframe: {timeframe or '-'}")
    paper_readiness_check(checks, "promotedFromOptimization exists", isinstance(candidate.get("promotedFromOptimization"), dict), "BLOCK", "Optimization baseline exists." if isinstance(candidate.get("promotedFromOptimization"), dict) else "Optimization baseline is missing.")
    paper_readiness_check(checks, "promotedFromRanking exists", isinstance(candidate.get("promotedFromRanking"), dict), "BLOCK", "Ranking baseline exists." if isinstance(candidate.get("promotedFromRanking"), dict) else "Ranking baseline is missing.")
    baseline_ok = bool(expected.get("source") and safe_float(expected.get("trades")) > 0 and safe_float(expected.get("profitFactor")) > 0)
    paper_readiness_check(checks, "expected baseline metrics exist", baseline_ok, "BLOCK", f"Expected source {expected.get('source') or '-'}, trades {expected.get('trades', '-')}, PF {expected.get('profitFactor', '-')}.")

    quality_status = None
    if isinstance(candidate.get("promotedFromOptimization"), dict):
        quality_status = candidate["promotedFromOptimization"].get("qualityStatus")
    if quality_status is None:
        quality_status = candidate.get("qualityStatus")
    quality_ok = quality_status in (None, "PASS") or quality_status != "FAIL"
    paper_readiness_check(checks, "qualityStatus is not FAIL", quality_ok, "BLOCK", f"qualityStatus: {quality_status or 'UNKNOWN'}.")

    validation = validate_candidate_config(candidate, candidate_validation_rules(args)) if candidate else {"status": "FAIL", "rows": []}
    paper_readiness_check(checks, "candidate validation status is PASS", validation.get("status") == "PASS", "BLOCK", f"Validation status: {validation.get('status', 'UNKNOWN')}.")

    stability = build_candidate_stability_report({"compareCurrent": "true"})
    stability_status = (stability.get("validation") or {}).get("status")
    paper_readiness_check(checks, "candidate stability status is PASS", stability_status == "PASS", "BLOCK", f"Stability status: {stability_status or 'UNKNOWN'}.")

    data_ready = False
    data_detail = "No active market to check."
    data_readiness = None
    if symbol and timeframe:
        period = args.get("period", "365d")
        limit = args.get("limit", "auto")
        data_readiness = research_data_readiness(candidate.get("source", "bybit"), [symbol], [timeframe], period, limit)
        row = (data_readiness.get("rows") or [{}])[0]
        data_ready = row.get("status") == "READY"
        data_detail = f"{symbol} {timeframe} data readiness: {row.get('status', 'UNKNOWN')}. {row.get('recommendedAction') or ''}".strip()
    paper_readiness_check(checks, "data readiness for active market is READY", data_ready, "BLOCK", data_detail)

    max_risk_pct = safe_float(args.get("max_risk_pct", 0.01), 0.01)
    risk_pct = safe_float(candidate.get("riskPct"), None)
    paper_readiness_check(checks, "riskPct is present and conservative", risk_pct is not None and risk_pct <= max_risk_pct, "BLOCK", f"riskPct: {candidate.get('riskPct', '-')}; maximum allowed {max_risk_pct}.")
    max_open_trades = safe_float(candidate.get("maxOpenTrades"), None)
    paper_readiness_check(checks, "maxOpenTrades is present and conservative", max_open_trades is not None and max_open_trades <= 1, "BLOCK", f"maxOpenTrades: {candidate.get('maxOpenTrades', '-')}; maximum allowed 1.")
    max_notional = safe_float(candidate.get("maxNotionalPerTrade"), None)
    paper_readiness_check(checks, "maxNotionalPerTrade is present", max_notional is not None and max_notional > 0, "BLOCK", f"maxNotionalPerTrade: {candidate.get('maxNotionalPerTrade', '-')}.")
    paper_readiness_check(checks, "source is bybit", candidate.get("source") == "bybit", "BLOCK", f"source: {candidate.get('source') or '-'}.")
    paper_readiness_check(checks, "fillModel is next-open", candidate.get("fillModel") == "next-open", "BLOCK", f"fillModel: {candidate.get('fillModel') or '-'}.")

    health_payload = build_candidate_health(candidate_health_rules(args))
    health = health_payload.get("health") or {}
    paper_metrics = health.get("paper") or {}
    closed_trades = int(safe_float(paper_metrics.get("closedTrades")))
    health_unknown_running_watch = health.get("status") == "UNKNOWN" and paper_enabled and closed_trades == 0
    health_unknown_ok = health.get("status") != "UNKNOWN" or (not paper_enabled and closed_trades == 0)
    health_detail = f"Health {health.get('status', 'UNKNOWN')}; closed paper trades {closed_trades}."
    if health.get("status") == "UNKNOWN" and health_unknown_ok:
        health_detail += " UNKNOWN is acceptable while paper is disabled and has 0 closed trades."
    elif health_unknown_running_watch:
        health_detail += " Paper is running but has no closed trades yet; monitor paper status."
    paper_readiness_check(checks, "paper health UNKNOWN is acceptable", health_unknown_ok, "WARN" if health_unknown_running_watch else "BLOCK", health_detail)

    paper_readiness_check(checks, "auto-promote disabled", not bool(learning_config.get("autoPromote")), "BLOCK", "autoPromote is disabled." if not learning_config.get("autoPromote") else "autoPromote is enabled.")
    paper_readiness_check(checks, "auto-enable paper disabled", not bool(learning_config.get("autoEnablePaper")), "BLOCK", "autoEnablePaper is disabled.")
    real_enabled, real_detail = paper_real_trading_enabled()
    paper_readiness_check(checks, "no real-trading mode/API path enabled", not real_enabled, "BLOCK", real_detail)

    blocking = [item for item in checks if not item["pass"] and item["severity"] == "BLOCK"]
    warn_checks = [item for item in checks if not item["pass"] and item["severity"] == "WARN"]
    warnings.extend(item["detail"] for item in warn_checks)
    ready = not blocking and not warn_checks
    paper_already_enabled = paper_enabled and not blocking
    status = "READY_FOR_PAPER_REVIEW" if ready else "BLOCKED" if blocking else "WATCH_PAPER_RUNNING" if paper_already_enabled else "WATCH"
    if ready:
        next_action = {
            "action": "REVIEW_ENABLE_PAPER_SIMULATION",
            "reason": "All blockers passed. This only means paper simulation may be reviewed; it does not enable paper automatically.",
        }
    elif blocking:
        next_action = {
            "action": "FIX_BLOCKING_PAPER_READINESS_CHECKS",
            "reason": f"{len(blocking)} blocking paper-readiness check(s) failed.",
        }
    else:
        next_action = {
            "action": "PAPER_ALREADY_ENABLED" if paper_already_enabled else "WATCH_PAPER_READINESS_WARNINGS",
            "reason": "Paper simulation is already enabled; continue monitoring paper status and candidate health." if paper_already_enabled else "Only warning-level readiness issues remain; paper enablement still requires manual review.",
        }
    return {
        "ok": True,
        "ready": ready,
        "status": status,
        "candidate": candidate_summary(candidate),
        "checks": checks,
        "summary": {
            "blockingIssues": len(blocking),
            "warnings": len(warn_checks),
            "paperEnabled": paper_enabled,
            "realTradingEnabled": real_enabled,
            "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
            "closedPaperTrades": closed_trades,
            "validationStatus": validation.get("status"),
            "stabilityStatus": stability_status,
            "dataReadinessStatus": ((data_readiness or {}).get("rows") or [{}])[0].get("status") if data_readiness else None,
        },
        "nextAction": next_action,
        "warnings": dedupe_list(warnings),
        "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
        "validation": validation,
        "stability": stability.get("validation"),
        "dataReadiness": data_readiness,
        "candidateHealth": health,
    }


def paper_config_changed_fields(before: dict, after: dict) -> list[dict]:
    keys = sorted(set(before.keys()) | set(after.keys()))
    return [
        {"field": key, "current": before.get(key), "preview": after.get(key)}
        for key in keys
        if before.get(key) != after.get(key)
    ]


def paper_enable_warning_text() -> list[str]:
    return ["Paper simulation only. No real trades will be placed."]


def build_enabled_paper_config(current: dict, enabled: bool) -> dict:
    updated = dict(current)
    now = datetime.now(timezone.utc).isoformat()
    updated["enabled"] = bool(enabled)
    if enabled:
        updated["enabledAt"] = now
        if "realTradingEnabled" in updated:
            updated["realTradingEnabled"] = False
    else:
        updated["disabledAt"] = now
    return normalize_promoted_candidate_config(updated)


def build_paper_enable_preview(args) -> dict:
    readiness = build_paper_readiness_report(args)
    current = load_paper_candidate_config()
    current_enabled = canonical_paper_enabled(current)
    preview = build_enabled_paper_config(current, True) if readiness.get("ready") and readiness.get("status") == "READY_FOR_PAPER_REVIEW" else dict(current)
    preview_enabled = canonical_paper_enabled(preview)
    real_enabled, _ = paper_real_trading_enabled()
    return {
        "ok": True,
        "dryRun": True,
        "wouldEnablePaper": bool(readiness.get("ready") and readiness.get("status") == "READY_FOR_PAPER_REVIEW"),
        "paperEnabled": current_enabled,
        "paperRemainsRealOnlyFalse": not real_enabled,
        "readiness": readiness,
        "currentConfig": current,
        "previewConfig": preview,
        "previewPaperEnabled": preview_enabled,
        "changedFields": paper_config_changed_fields(current, preview),
        "consistencyWarnings": paper_enabled_consistency_warnings(current, current_enabled),
        "warnings": paper_enable_warning_text(),
        "message": "Preview only; paper simulation was not enabled.",
    }


def enable_paper_simulation_controlled(args) -> tuple[dict, int]:
    readiness = build_paper_readiness_report(args)
    if not readiness.get("ready") or readiness.get("status") != "READY_FOR_PAPER_REVIEW" or readiness.get("summary", {}).get("blockingIssues", 0):
        return {
            "ok": False,
            "error": "Paper readiness gate is not ready; paper simulation was not enabled.",
            "readiness": readiness,
            "warnings": paper_enable_warning_text(),
        }, 400
    current = load_paper_candidate_config()
    backup_path = backup_candidate_config(current)
    updated = build_enabled_paper_config(current, True)
    write_candidate_config(updated)
    fresh = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(fresh)
    learning_config = safe_learning_config(load_learning_config())
    real_enabled, _ = paper_real_trading_enabled()
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "autoPromoteEnabled": bool(learning_config.get("autoPromote", False)),
        "backupPath": str(backup_path.relative_to(app.root_path)),
        "writtenPath": str(PAPER_CANDIDATE_LOCAL_PATH.relative_to(app.root_path)),
        "candidateConfig": fresh,
        "changedFields": paper_config_changed_fields(current, fresh),
        "runtimeStatus": build_paper_runtime_status(args),
        "stopRules": build_paper_stop_rules(args),
        "sessionSummary": build_paper_session_summary(args),
        "consistencyWarnings": paper_enabled_consistency_warnings(fresh, paper_enabled),
        "warnings": paper_enable_warning_text(),
    }, 200


def disable_paper_simulation_controlled(args=None) -> dict:
    args = args or {}
    current = load_paper_candidate_config()
    backup_path = backup_candidate_config(current)
    updated = build_enabled_paper_config(current, False)
    write_candidate_config(updated)
    fresh = load_paper_candidate_config()
    paper_enabled = canonical_paper_enabled(fresh)
    real_enabled, _ = paper_real_trading_enabled()
    return {
        "ok": True,
        "paperEnabled": paper_enabled,
        "realTradingEnabled": real_enabled,
        "backupPath": str(backup_path.relative_to(app.root_path)),
        "writtenPath": str(PAPER_CANDIDATE_LOCAL_PATH.relative_to(app.root_path)),
        "candidateConfig": fresh,
        "changedFields": paper_config_changed_fields(current, fresh),
        "stopRules": build_paper_stop_rules(args),
        "sessionSummary": build_paper_session_summary(args),
        "consistencyWarnings": paper_enabled_consistency_warnings(fresh, paper_enabled),
        "warnings": [],
    }


def normalize_promoted_candidate_config(candidate: dict) -> dict:
    normalized = dict(candidate or {})
    params = dict(normalized.get("params") or {})
    top_regime = normalized.get("regimeMode")
    param_regime = params.get("regimeMode")
    if param_regime:
        normalized["regimeMode"] = param_regime
    elif top_regime:
        params["regimeMode"] = top_regime
        normalized["regimeMode"] = top_regime
    normalized["params"] = params
    return normalized


def candidate_summary(candidate: dict) -> dict:
    paper_enabled = canonical_paper_enabled(candidate)
    return {
        "enabled": paper_enabled,
        "paperEnabled": paper_enabled,
        "enabledAt": candidate.get("enabledAt"),
        "disabledAt": candidate.get("disabledAt"),
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
        "configWarnings": candidate_config_warnings(candidate),
        "consistencyWarnings": paper_enabled_consistency_warnings(candidate, paper_enabled),
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
    candidate = normalize_promoted_candidate_config(candidate)
    ensure_local_config_from_default(
        PAPER_CANDIDATE_DEFAULT_PATH,
        PAPER_CANDIDATE_LOCAL_PATH,
        {},
    )
    PAPER_CANDIDATE_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PAPER_CANDIDATE_LOCAL_PATH, "w", encoding="utf-8") as handle:
        json.dump(candidate, handle, indent=2)
        handle.write("\n")


def write_paper_candidate_config(candidate: dict) -> None:
    write_candidate_config(candidate)


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
    params = payload.get("params") if isinstance(payload.get("params"), dict) else current.get("params", {})
    params = dict(params or {})
    regime_mode = params.get("regimeMode") or payload.get("regimeMode") or current.get("regimeMode")
    if regime_mode:
        params["regimeMode"] = regime_mode
    # TODO: Add scheduled ranking runs, automatic candidate suggestions,
    # a human approval queue, and automatic promotion only after paper validation.
    preserved.update({
        "enabled": False,
        "source": payload.get("source", current.get("source", "bybit")),
        "strategy": payload.get("strategy") or payload.get("preset") or current.get("strategy"),
        "regimeMode": regime_mode,
        "params": params,
        "symbols": symbols,
        "promotedAt": now,
        "promotedFromRanking": snapshot,
        "promotedFromOptimization": optimization_snapshot,
    })
    return normalize_promoted_candidate_config(preserved)


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
    candidate = load_paper_candidate_config()
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


def run_shared_backtest_engine(source: str, symbol: str, timeframe: str, period: str, preset: str, fee_pct: float, slippage_pct: float, limit: int | None, debug: bool = False, allow_shorts: bool = False, strategy_params: dict | None = None) -> dict:
    """Bridge Flask to the reusable Node research engine.

    Python keeps responsibility for broker adapters that already work here
    (yfinance, Bybit cache, Hyperliquid). Simulation rules live in /core so
    the UI, CLI optimizer, and future workers all share one backtest engine.
    """
    candles_payload = fetch_historical_candles(source, symbol, timeframe, period=period, limit=limit)
    effective_limit = int(candles_payload.get("limit") or limit or len(candles_payload["candles"]) or 0)
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
        "limit": effective_limit,
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
    historical_diag = candles_payload.get("diagnostics", {})
    payload["diagnostics"]["requested_period"] = historical_diag.get("requested_period", period)
    payload["diagnostics"]["effective_period"] = candles_payload.get("effective_period", period)
    payload["diagnostics"]["historical_coverage"] = historical_diag
    payload["diagnostics"]["api_candles"] = candle_diagnostics(candles_payload["candles"], requested_limit=effective_limit)
    payload["diagnostics"]["actual_returned_candles"] = historical_diag.get("returned_candles", len(candles_payload["candles"]))
    payload["diagnostics"]["actual_days_returned"] = historical_diag.get("approximate_days_returned", payload["diagnostics"].get("actual_days_returned"))
    payload["diagnostics"]["first_candle_time"] = historical_diag.get("first_candle_time", payload.get("firstCandleTime"))
    payload["diagnostics"]["last_candle_time"] = historical_diag.get("last_candle_time", payload.get("lastCandleTime"))
    if payload["diagnostics"].get("first_candle_time"):
        payload["diagnostics"]["first_candle_date"] = datetime.fromtimestamp(int(payload["diagnostics"]["first_candle_time"]), timezone.utc).isoformat()
    if payload["diagnostics"].get("last_candle_time"):
        payload["diagnostics"]["last_candle_date"] = datetime.fromtimestamp(int(payload["diagnostics"]["last_candle_time"]), timezone.utc).isoformat()
    payload["diagnostics"]["period_capped"] = historical_diag.get("period_capped", False)
    payload["diagnostics"].setdefault("warnings", [])
    payload["diagnostics"]["warnings"].extend(historical_diag.get("warnings", []))
    return payload


def load_backtest_strategy_metadata(active_candidate: dict | None = None) -> dict:
    completed = subprocess.run(
        [node_executable(), "cli/backtest_strategy_metadata.js"],
        input=json.dumps({"activeCandidate": active_candidate or {}}, allow_nan=False),
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Strategy metadata command failed")
    return json.loads(completed.stdout)


def normalize_backtest_response(
    payload: dict,
    source: str,
    symbol: str,
    timeframe: str,
    period: str,
    preset: str,
    fee_pct: float,
    slippage_pct: float,
) -> dict:
    """Normalize diagnostics so UI rendering does not depend on engine variants."""
    payload = dict(payload or {})
    diagnostics = dict(payload.get("diagnostics") or {})
    coverage = dict(diagnostics.get("historical_coverage") or payload.get("historical_coverage") or {})
    candles_loaded = (
        payload.get("candlesLoaded")
        or payload.get("candles_loaded")
        or diagnostics.get("candlesLoaded")
        or diagnostics.get("number_of_candles_loaded")
        or coverage.get("returned_candles")
        or diagnostics.get("actual_returned_candles")
        or 0
    )
    first_time = (
        payload.get("firstCandleTime")
        or payload.get("first_candle_time")
        or diagnostics.get("firstCandleTime")
        or diagnostics.get("first_candle_time")
        or coverage.get("first_candle_time")
    )
    last_time = (
        payload.get("lastCandleTime")
        or payload.get("last_candle_time")
        or diagnostics.get("lastCandleTime")
        or diagnostics.get("last_candle_time")
        or coverage.get("last_candle_time")
    )
    actual_days = diagnostics.get("actual_days_returned")
    if actual_days is None:
        actual_days = coverage.get("approximate_days_returned")
    if actual_days is None and first_time and last_time:
        try:
            actual_days = round((int(last_time) - int(first_time)) / 86400, 2)
        except (TypeError, ValueError):
            actual_days = None

    strategy = payload.get("strategy") or payload.get("preset") or preset
    requested_period = coverage.get("requested_period") or diagnostics.get("requested_period") or period
    fee_pct = safe_float(fee_pct, 0)
    slippage_pct = safe_float(slippage_pct, 0)
    warnings = list(diagnostics.get("warnings") or [])
    requested_days = parse_period_to_days(requested_period)
    if requested_days is not None and actual_days is not None:
        if actual_days > requested_days * 1.25 or actual_days < requested_days * 0.75:
            warnings.append(
                f"Selected period was {requested_period}, but returned candles span approximately {actual_days}d. Check limit/period settings."
            )

    diagnostics.update({
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "interval": timeframe,
        "period": period,
        "candlesLoaded": int(candles_loaded or 0),
        "number_of_candles_loaded": int(candles_loaded or 0),
        "firstCandleTime": first_time,
        "lastCandleTime": last_time,
        "first_candle_time": first_time,
        "last_candle_time": last_time,
        "actualDays": actual_days,
        "actual_days_returned": actual_days,
        "feePct": fee_pct,
        "slippagePct": slippage_pct,
        "fee_pct_per_side": fee_pct,
        "slippage_pct_per_side": slippage_pct,
        "preset": preset,
        "strategy": strategy,
        "historicalCoverage": coverage,
        "historical_coverage": coverage,
        "warnings": dedupe_list(warnings),
    })
    if first_time and not diagnostics.get("first_candle_date"):
        diagnostics["first_candle_date"] = datetime.fromtimestamp(int(first_time), timezone.utc).isoformat()
    if last_time and not diagnostics.get("last_candle_date"):
        diagnostics["last_candle_date"] = datetime.fromtimestamp(int(last_time), timezone.utc).isoformat()

    payload.update({
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "preset": payload.get("preset") or strategy,
        "preset_id": payload.get("preset_id") or preset,
        "strategy": strategy,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "candlesLoaded": int(candles_loaded or 0),
        "firstCandleTime": first_time,
        "lastCandleTime": last_time,
        "actualDays": actual_days,
        "historicalCoverage": coverage,
        "historical_coverage": coverage,
        "diagnostics": diagnostics,
    })
    return payload


def iso_from_backtest_time(value):
    if value in {None, ""}:
        return None
    try:
        timestamp = int(float(value))
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return str(value)


def backtest_timeframe_minutes(timeframe: str) -> float | None:
    text = str(timeframe or "").strip().lower()
    try:
        if text.endswith("m"):
            return float(text[:-1])
        if text.endswith("h"):
            return float(text[:-1]) * 60
        if text.endswith("d"):
            return float(text[:-1]) * 1440
    except (TypeError, ValueError):
        return None
    return None


def expected_backtest_candles(period: str, timeframe: str) -> int | None:
    days = parse_period_to_days(period)
    minutes = backtest_timeframe_minutes(timeframe)
    if days is None or not minutes:
        return None
    return max(1, int(math.ceil(days * 1440 / minutes)))


def normalized_compare_value(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 10)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return round(float(stripped), 10)
        except ValueError:
            return stripped
    return value


def manual_backtest_active_params(candidate: dict) -> dict:
    active = normalize_promoted_candidate_config(candidate or {})
    params = dict(active.get("params") or {})
    for key in (
        "accountEquity",
        "riskPct",
        "maxOpenTrades",
        "maxNotionalPerTrade",
        "makerFeePct",
        "takerFeePct",
        "slippageBps",
        "fillModel",
    ):
        if active.get(key) is not None:
            params[key] = active.get(key)
    return params


def active_candidate_primary_market(candidate: dict) -> dict:
    active_symbols = candidate_symbols_by_mode(candidate or {}, "active")
    return active_symbols[0] if active_symbols else {}


def compare_manual_backtest_to_active_candidate(
    strategy: str,
    symbol: str,
    timeframe: str,
    params_used: dict,
    active_candidate: dict,
    params_source: str,
    source: str,
    fee_pct: float,
    slippage_pct: float,
) -> dict:
    active = normalize_promoted_candidate_config(active_candidate or {})
    active_market = active_candidate_primary_market(active)
    active_params = manual_backtest_active_params(active)
    diffs = []
    warnings = []
    same_identity = (
        strategy == active.get("strategy")
        and symbol == active_market.get("symbol")
        and timeframe == (active_market.get("interval") or active_market.get("timeframe"))
    )
    keys = sorted(set(active_params.keys()) | set((params_used or {}).keys()))
    ignored = {
        "accountEquity",
        "feePct",
        "fillModel",
        "makerFeePct",
        "maxNotionalPerTrade",
        "maxOpenTrades",
        "riskPct",
        "shortMode",
        "slippageBps",
        "slippagePct",
        "strategyName",
        "takerFeePct",
    }
    for key in keys:
        if key in ignored:
            continue
        active_value = normalized_compare_value(active_params.get(key))
        used_value = normalized_compare_value((params_used or {}).get(key))
        if active_value != used_value:
            diffs.append({
                "param": key,
                "active": active_params.get(key),
                "run": (params_used or {}).get(key),
            })
    active_taker = safe_float(active.get("takerFeePct"), 0)
    active_slippage_pct = safe_float(active.get("slippageBps"), 0) / 100
    if source != active.get("source"):
        warnings.append(f"Manual run source {source} differs from active candidate source {active.get('source')}.")
    if abs(fee_pct - active_taker) > 1e-9:
        warnings.append(f"Manual feePct {fee_pct} differs from active candidate takerFeePct {active_taker}.")
    if abs(slippage_pct - active_slippage_pct) > 1e-9:
        warnings.append(f"Manual slippagePct {slippage_pct} differs from active candidate slippageBps {active.get('slippageBps')}.")
    if str(params_source) == "activeCandidate" and (not same_identity or diffs):
        warnings.append("Preset is activeCandidate, but this manual run differs from the active candidate identity or params.")
    matches = same_identity and not diffs and not any("differs from active candidate" in warning for warning in warnings)
    return {
        "matchesActiveCandidate": matches,
        "sameStrategySymbolTimeframe": same_identity,
        "paramsSource": params_source,
        "activeCandidate": candidate_summary(active),
        "diffs": diffs[:40],
        "diffCount": len(diffs),
        "warnings": warnings,
        "summary": "Matches active candidate params." if matches else "Manual run is not directly comparable to the active candidate.",
    }


def manual_backtest_run_context(
    payload: dict,
    period: str,
    requested_limit,
    source: str,
    strategy: str,
    symbol: str,
    timeframe: str,
    params_used: dict,
    params_source: str,
    active_candidate: dict,
) -> tuple[dict, list[str]]:
    diagnostics = payload.get("diagnostics") or {}
    coverage = diagnostics.get("historical_coverage") or diagnostics.get("historicalCoverage") or {}
    candles_used = int(safe_float(payload.get("candlesLoaded") or diagnostics.get("candlesLoaded"), 0))
    first_time = diagnostics.get("firstCandleTime") or diagnostics.get("first_candle_time") or payload.get("firstCandleTime")
    last_time = diagnostics.get("lastCandleTime") or diagnostics.get("last_candle_time") or payload.get("lastCandleTime")
    effective_limit = coverage.get("effective_limit") or coverage.get("effectiveLimit") or diagnostics.get("effectiveLimit") or payload.get("limit")
    expected_candles = expected_backtest_candles(period, timeframe)
    active = normalize_promoted_candidate_config(active_candidate or {})
    fill_model = (params_used or {}).get("fillModel") or active.get("fillModel")
    maker_fee = (params_used or {}).get("makerFeePct", active.get("makerFeePct"))
    taker_fee = (params_used or {}).get("feePct", (params_used or {}).get("takerFeePct", active.get("takerFeePct")))
    if (params_used or {}).get("slippagePct") is not None:
        slippage_bps = safe_float((params_used or {}).get("slippagePct"), 0) * 100
    else:
        slippage_bps = (params_used or {}).get("slippageBps")
    warnings = []
    if expected_candles and candles_used:
        lower = expected_candles * 0.75
        upper = expected_candles * 1.25
        if candles_used < lower or candles_used > upper:
            warnings.append(f"candlesUsed {candles_used} differs materially from expected {expected_candles} for {period} {timeframe}.")
    if not first_time or not last_time:
        warnings.append("First or last candle time is missing; compare this result cautiously.")
    if source != "bybit":
        warnings.append(f"Manual backtest source is {source}; active paper research normally uses bybit.")
    if fill_model and active.get("fillModel") and fill_model != active.get("fillModel"):
        warnings.append(f"Fill model {fill_model} differs from active candidate fillModel {active.get('fillModel')}.")
    return {
        "period": period,
        "requestedLimit": requested_limit,
        "effectiveLimit": effective_limit,
        "candlesUsed": candles_used,
        "expectedCandles": expected_candles,
        "firstCandleTime": iso_from_backtest_time(first_time),
        "lastCandleTime": iso_from_backtest_time(last_time),
        "firstCandleTimestamp": first_time,
        "lastCandleTimestamp": last_time,
        "source": source,
        "fillModel": fill_model,
        "makerFeePct": maker_fee,
        "takerFeePct": taker_fee,
        "slippageBps": slippage_bps,
        "paramsSource": params_source,
        "strategy": strategy,
        "symbol": symbol,
        "timeframe": timeframe,
    }, warnings


def manual_backtest_comparability(run_context: dict, active_comparison: dict, context_warnings: list[str]) -> dict:
    reasons = []
    warnings = []
    if not active_comparison.get("sameStrategySymbolTimeframe"):
        reasons.append("Strategy, symbol, or timeframe differs from the active candidate.")
    if active_comparison.get("diffCount", 0):
        reasons.append(f"{active_comparison.get('diffCount')} parameter difference(s) versus the active candidate.")
    if run_context.get("source") != "bybit":
        reasons.append(f"Source is {run_context.get('source')}, not bybit.")
    if not run_context.get("firstCandleTime") or not run_context.get("lastCandleTime"):
        warnings.append("First/last candle time is missing.")
    expected = safe_float(run_context.get("expectedCandles"), 0)
    used = safe_float(run_context.get("candlesUsed"), 0)
    candle_ratio = used / expected if expected else None
    if candle_ratio is not None and (candle_ratio < 0.75 or candle_ratio > 1.25):
        reasons.append(f"Candle count is {round(candle_ratio * 100, 1)}% of expected for the selected period/timeframe.")
    cost_or_fill_warnings = [
        warning for warning in list(active_comparison.get("warnings") or []) + list(context_warnings or [])
        if "feePct" in warning or "slippage" in warning or "Fill model" in warning or "source" in warning
    ]
    warnings.extend(cost_or_fill_warnings)
    if active_comparison.get("matchesActiveCandidate") and not reasons and not warnings:
        status = "COMPARABLE"
        summary = "This manual run matches the active candidate context closely enough for direct comparison."
    elif active_comparison.get("sameStrategySymbolTimeframe") and len(reasons) <= 2:
        status = "PARTIALLY_COMPARABLE"
        summary = "This run shares the active candidate market/strategy, but context or params differ. Compare cautiously."
    else:
        status = "NOT_COMPARABLE"
        summary = "This run differs enough from the active candidate that metrics should not be compared directly."
    return {
        "status": status,
        "summary": summary,
        "reasons": dedupe_list(reasons),
        "warnings": dedupe_list(warnings),
        "candleCoverageRatio": round(candle_ratio, 4) if candle_ratio is not None else None,
        "sameStrategySymbolTimeframe": bool(active_comparison.get("sameStrategySymbolTimeframe")),
        "matchesActiveCandidate": bool(active_comparison.get("matchesActiveCandidate")),
    }


def manual_backtest_result_summary(payload: dict, period: str) -> dict:
    metrics = ranking_metrics_from_backtest(payload)
    trades = metrics["trades"]
    days = parse_period_to_days(period) or safe_float(payload.get("actualDays"), 0) or 365
    trades_per_month = round(trades / max(days, 1) * 30.4375, 4)
    expectancy = round(metrics["totalReturn"] / trades, 4) if trades else 0
    reasons = []
    if trades == 0:
        status = "NO_TRADES"
        reasons.append("NO_TRADES")
    elif trades < 20:
        status = "WARN"
        reasons.append("TOO_FEW_TRADES")
    elif metrics["totalReturn"] < 0:
        status = "FAIL"
        reasons.append("NEGATIVE_RETURN")
    elif metrics["profitFactor"] < 1.05:
        status = "FAIL"
        reasons.append("WEAK_PROFIT_FACTOR")
    elif metrics["maxDrawdown"] > 25:
        status = "FAIL"
        reasons.append("HIGH_DRAWDOWN")
    elif metrics["profitFactor"] < 1.15:
        status = "WARN"
        reasons.append("LOW_PROFIT_FACTOR_BUFFER")
    else:
        status = "PASS"
        reasons.append("OK")
    return {
        "trades": trades,
        "tradesPerMonth": trades_per_month,
        "totalReturnPct": round(metrics["totalReturn"], 4),
        "profitFactor": round(metrics["profitFactor"], 4),
        "maxDrawdownPct": round(metrics["maxDrawdown"], 4),
        "winRate": round(metrics["winRate"], 4),
        "avgBarsHeld": round(metrics["averageBarsHeld"], 4),
        "expectancyPctPerTrade": expectancy,
        "score": round(ranking_score(metrics, min_trades=20), 5),
        "status": status,
        "reasons": reasons,
    }


def dedupe_list(items: list) -> list:
    seen = set()
    output = []
    for item in items:
        key = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


ZERO_TRADE_REASON_KEYS = (
    "insufficient_candles",
    "warmup_not_met",
    "no_entry_signal",
    "regime_filter_blocked",
    "trend_filter_blocked",
    "volatility_filter_blocked",
    "risk_filter_blocked",
    "short_mode_disabled",
    "invalid_indicator_values",
    "no_exit_signal",
    "unknown",
)


def build_trade_generation_diagnostics(payload: dict, readiness_row: dict | None = None, allow_shorts: bool = False) -> dict:
    diagnostics = payload.get("diagnostics") or {}
    debug = diagnostics.get("debug") if isinstance(diagnostics.get("debug"), dict) else {}
    readiness_row = readiness_row or {}
    trades = int(safe_float(payload.get("number_of_trades", payload.get("trades", 0))))
    candles = int(safe_float(diagnostics.get("candlesLoaded", payload.get("candlesLoaded", 0))))
    warmup = int(safe_float(
        diagnostics.get("warmupCandles")
        or diagnostics.get("warmup_candles_skipped")
        or diagnostics.get("warmup")
        or estimate_warmup_candles(candles, diagnostics.get("strategy"))
    ))
    blocker_counts = dict(diagnostics.get("blockerCounts") or debug.get("blockerCounts") or {})
    skip_reasons = dict(diagnostics.get("skipReasons") or debug.get("skipReasons") or {})
    counters = {key: 0 for key in ZERO_TRADE_REASON_KEYS}

    if candles <= 0:
        counters["insufficient_candles"] += 1
    elif candles < max(50, warmup):
        counters["insufficient_candles"] += max(1, candles)
    if warmup and candles <= warmup:
        counters["warmup_not_met"] += max(1, warmup - candles + 1)

    for reason, count in {**blocker_counts, **skip_reasons}.items():
        canonical = canonical_zero_trade_reason(reason)
        counters[canonical] = counters.get(canonical, 0) + int(safe_float(count, 1))

    entry_count = int(safe_float(
        diagnostics.get("entrySignalsCount")
        or debug.get("entrySignalsCount")
        or diagnostics.get("longEntriesAccepted")
        or 0
    ))
    exit_count = int(safe_float(diagnostics.get("exitSignalsCount") or debug.get("exitSignalsCount") or 0))
    if trades == 0 and entry_count == 0 and not any(counters[key] for key in ("insufficient_candles", "warmup_not_met")):
        counters["no_entry_signal"] += max(1, candles - warmup if candles else 1)
    if trades == 0 and entry_count > 0 and exit_count == 0:
        counters["no_exit_signal"] += 1
    short_mode = bool((diagnostics.get("params") or {}).get("shortMode") or (debug.get("paramsUsed") or {}).get("shortMode"))
    if not allow_shorts and not short_mode:
        counters["short_mode_disabled"] += 1
    if readiness_row.get("status") in {"MISSING", "PARTIAL", "CAPPED", "ERROR"}:
        counters["insufficient_candles"] += 1
    if readiness_row.get("status") == "STALE":
        counters["unknown"] += 1

    likely_reason, confidence = likely_zero_trade_reason(counters, diagnostics, readiness_row, trades)
    suggested = zero_trade_suggestions(counters, readiness_row, diagnostics, allow_shorts)
    warnings = []
    if readiness_row.get("status") and readiness_row.get("status") != "READY":
        warnings.append(f"Data readiness is {readiness_row.get('status')}: {readiness_row.get('recommendedAction')}")
    warnings.extend(payload.get("warnings") or [])

    return {
        "summary": {
            "trades": trades,
            "likelyReason": likely_reason,
            "confidence": confidence,
        },
        "strategy": {
            "name": diagnostics.get("strategy") or payload.get("strategy") or payload.get("preset"),
            "preset": diagnostics.get("preset") or payload.get("preset_id") or payload.get("preset"),
            "params": diagnostics.get("params") or debug.get("paramsUsed") or {},
            "allowShorts": allow_shorts,
            "shortMode": short_mode,
            "mapping": {
                "requestedPreset": payload.get("preset_id"),
                "engineStrategy": payload.get("strategy") or diagnostics.get("strategy"),
            },
        },
        "diagnostics": {
            "candlesLoaded": candles,
            "warmupCandles": warmup,
            "usableCandlesAfterWarmup": max(0, candles - warmup),
            "longEntriesConsidered": int(safe_float(diagnostics.get("candlesEvaluated") or debug.get("candlesEvaluated") or max(0, candles - warmup))),
            "longEntriesAccepted": entry_count,
            "shortEntriesConsidered": 0,
            "shortEntriesAccepted": 0,
            "exitsConsidered": exit_count,
            "exitsAccepted": int(safe_float(diagnostics.get("exitSignalsAccepted") or 0)),
            "regimeFilter": extract_filter_counts(blocker_counts, "regime"),
            "indicatorInvalidCounts": extract_indicator_invalid_counts(diagnostics, debug),
            "primaryBlocker": diagnostics.get("primaryBlocker") or primary_counter_label(counters),
            "engineSkipReasons": skip_reasons,
            "engineBlockerCounts": blocker_counts,
            "firstSignalsPreview": diagnostics.get("firstSignalsPreview") or debug.get("firstSignalsPreview") or [],
            "lastSignalsPreview": diagnostics.get("lastSignalsPreview") or debug.get("lastSignalsPreview") or [],
        },
        "reasonCounters": counters,
        "suggestedActions": suggested,
        "warnings": dedupe_list(warnings),
    }


def estimate_warmup_candles(candles: int, strategy: str | None = None) -> int:
    if strategy == "AlwaysLongTest":
        return 0
    return min(250, max(50, math.floor(candles * 0.2))) if candles else 250


def canonical_zero_trade_reason(reason: str) -> str:
    text = str(reason or "").lower()
    if any(token in text for token in ("warmup", "confirmation")):
        return "warmup_not_met"
    if any(token in text for token in ("regime", "btc")):
        return "regime_filter_blocked"
    if any(token in text for token in ("ema", "trend", "donchian", "breakout", "pullback", "reclaim", "rsi")):
        return "trend_filter_blocked"
    if any(token in text for token in ("atr", "adx", "volatility", "squeeze", "range")):
        return "volatility_filter_blocked"
    if any(token in text for token in ("risk", "stop", "notional", "volume", "cooldown", "position")):
        return "risk_filter_blocked"
    if any(token in text for token in ("nan", "invalid", "missing indicator")):
        return "invalid_indicator_values"
    if "short" in text:
        return "short_mode_disabled"
    if any(token in text for token in ("entry", "signal", "false")):
        return "no_entry_signal"
    if "exit" in text:
        return "no_exit_signal"
    return "unknown"


def likely_zero_trade_reason(counters: dict, diagnostics: dict, readiness_row: dict, trades: int) -> tuple[str, str]:
    if trades > 0:
        return "Trades were generated; zero-trade diagnostics are informational only.", "HIGH"
    if readiness_row.get("status") in {"MISSING", "ERROR"}:
        return f"Historical data is {readiness_row.get('status')}; strategy could not be evaluated reliably.", "HIGH"
    ordered = sorted(counters.items(), key=lambda item: item[1], reverse=True)
    top_key, top_value = ordered[0] if ordered else ("unknown", 0)
    if top_value <= 0:
        primary = diagnostics.get("primaryBlocker")
        if primary:
            return f"Primary engine blocker: {primary}.", "MEDIUM"
        return "No entries were generated, but the strategy did not expose enough internal counters to identify the exact blocker.", "LOW"
    label = format_backend_reason(top_key)
    if top_key == "no_entry_signal" and diagnostics.get("primaryBlocker"):
        return f"No entry signal accepted. Primary engine blocker: {diagnostics.get('primaryBlocker')}.", "MEDIUM"
    return f"{label} appears to be the dominant blocker ({top_value}).", "MEDIUM" if top_key != "unknown" else "LOW"


def zero_trade_suggestions(counters: dict, readiness_row: dict, diagnostics: dict, allow_shorts: bool) -> list[str]:
    suggestions = []
    if readiness_row.get("status") in {"MISSING", "PARTIAL", "CAPPED"}:
        suggestions.append("Prefetch or request more historical candles before trusting the result.")
    if counters.get("insufficient_candles") or counters.get("warmup_not_met"):
        suggestions.append("Use a longer period or Auto/50000 candle limit so indicators have enough warmup history.")
    if counters.get("regime_filter_blocked"):
        suggestions.append("Check regime filter settings and compare the BTC 4h regime against the selected symbol/timeframe.")
    if counters.get("trend_filter_blocked"):
        suggestions.append("Try a less restrictive preset or inspect trend/breakout thresholds.")
    if counters.get("volatility_filter_blocked"):
        suggestions.append("Check ATR/ADX/volatility thresholds for the selected market.")
    if counters.get("risk_filter_blocked"):
        suggestions.append("Review risk, volume, cooldown, and max-position filters.")
    if counters.get("short_mode_disabled") and not allow_shorts:
        suggestions.append("Enable shorts only if the strategy defines and you intentionally want short rules.")
    if not suggestions:
        suggestions.extend([
            "Run with a known test strategy such as AlwaysLongTest to verify the engine.",
            "Run the backtest with debug=true and inspect signal previews.",
        ])
    suggestions.append("Check strategy mapping to confirm the requested preset maps to the intended Node strategy.")
    return dedupe_list(suggestions)


def extract_filter_counts(counts: dict, token: str) -> dict:
    passed = sum(int(safe_float(value)) for key, value in counts.items() if token in str(key).lower() and "pass" in str(key).lower())
    failed = sum(int(safe_float(value)) for key, value in counts.items() if token in str(key).lower() and "fail" in str(key).lower())
    blocked = sum(int(safe_float(value)) for key, value in counts.items() if token in str(key).lower() and "block" in str(key).lower())
    return {"passed": passed, "failed": failed, "blocked": blocked}


def extract_indicator_invalid_counts(diagnostics: dict, debug: dict) -> dict:
    invalid = diagnostics.get("indicatorInvalidCounts") or debug.get("indicatorInvalidCounts") or {}
    if invalid:
        return invalid
    ready = safe_float(diagnostics.get("indicatorsReadyCount") or debug.get("indicatorsReadyCount"), 0)
    candles = safe_float(diagnostics.get("candlesLoaded") or debug.get("candlesLoaded"), 0)
    if candles and ready <= 0:
        return {"unknownIndicators": int(candles)}
    return {}


def primary_counter_label(counters: dict) -> str | None:
    ordered = sorted(counters.items(), key=lambda item: item[1], reverse=True)
    if not ordered or ordered[0][1] <= 0:
        return None
    return f"{ordered[0][0]} ({ordered[0][1]})"


def format_backend_reason(reason: str) -> str:
    return str(reason or "unknown").replace("_", " ").capitalize()


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


def load_optimizer_grid_catalog() -> dict:
    script = "const optimizer=require('./core/optimizer'); process.stdout.write(JSON.stringify(optimizer.optimizerGridMetadataCatalog()));"
    completed = subprocess.run(
        [node_executable(), "-e", script],
        text=True,
        capture_output=True,
        cwd=app.root_path,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Could not inspect optimizer grids")
    return json.loads(completed.stdout)


def normalize_optimizer_payload(raw: dict, source: str, symbol: str, timeframe: str, strategy: str, period: str, limit: int, max_combos: int, train_ratio: float, fee_pct: float, slippage_pct: float) -> dict:
    candidates = optimizer_candidates(raw)
    rows = [normalize_optimizer_candidate(row, index + 1) for index, row in enumerate(candidates[:20])]
    rejected_rows = [normalize_optimizer_candidate(row, index + 1) for index, row in enumerate((raw.get("rejectedCandidates") or [])[:20])]
    quality_summary = raw.get("qualitySummary") or (raw.get("summary") or {}).get("qualitySummary") or {}
    optimized = raw.get("optimizedPerformance")
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
            "qualitySummary": quality_summary,
            "selectedStatus": quality_summary.get("selectedStatus"),
            "warnings": optimizer_warnings(raw),
        },
        "optimizerGrid": raw.get("optimizerGrid") or {},
        "qualityPolicy": raw.get("qualityPolicy") or {},
        "qualitySummary": quality_summary,
        "gridAudit": raw.get("gridAudit") or (raw.get("summary") or {}).get("gridAudit") or {},
        "zeroTradeSummary": raw.get("zeroTradeSummary") or {},
        "allZeroTradeCandidates": bool(raw.get("allZeroTradeCandidates")),
        "warnings": optimizer_warnings(raw),
        "optimizedPerformance": normalize_optimizer_candidate(optimized, 1) if isinstance(optimized, dict) else None,
        "unseenTestPerformance": raw.get("unseenTestPerformance"),
        "topCandidates": rows,
        "rejectedCandidates": rejected_rows,
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
    quality_status = row.get("qualityStatus") or ("PASS" if row.get("valid") else "FAIL")
    rejection_reasons = row.get("rejectionReasons") or []
    quality_warnings = row.get("qualityWarnings") or []
    quality_warning_labels = [
        item.get("label") or item.get("code") or str(item)
        for item in quality_warnings
    ]
    if quality_warning_labels:
        warnings = dedupe_list(warnings + [f"WARN: {label}" for label in quality_warning_labels])
    return {
        "rank": rank,
        "valid": bool(row.get("isValid", row.get("valid"))) and quality_status != "FAIL" and not any(warning.startswith("FAIL") for warning in warnings),
        "qualityStatus": quality_status,
        "isValid": bool(row.get("isValid", row.get("valid"))) and quality_status != "FAIL",
        "rejectionReasons": rejection_reasons,
        "qualityMetrics": row.get("qualityMetrics") or {},
        "scorePenalty": safe_float(row.get("scorePenalty")),
        "params": row.get("params", {}),
        "score": score,
        "train": train,
        "test": test,
        "full": full,
        "walkForward": walk_forward,
        "warnings": warnings,
        "zeroTradeDiagnostics": row.get("zeroTradeDiagnostics"),
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
    quality_status = row.get("qualityStatus")
    rejection_reasons = row.get("rejectionReasons") or []
    if quality_status == "FAIL" and rejection_reasons:
        labels = [item.get("label") or item.get("code") or str(item) for item in rejection_reasons[:4]]
        warnings.append("FAIL: " + ", ".join(labels))
    elif quality_status == "WARN" and rejection_reasons:
        labels = [item.get("label") or item.get("code") or str(item) for item in rejection_reasons[:4]]
        warnings.append("WARN: " + ", ".join(labels))
    zero_diag = row.get("zeroTradeDiagnostics") or {}
    if zero_diag:
        likely = (zero_diag.get("summary") or {}).get("likelyReason")
        warnings.append(f"ZERO TRADE: {likely or 'candidate produced no train/test trades'}")
    if quality_status:
        return dedupe_list(warnings)
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
    for value in raw.get("warnings") or []:
        if value:
            warnings.append(value)
    for value in summary.get("warnings") or []:
        if value:
            warnings.append(value)
    for value in (raw.get("warning"), summary.get("warning"), raw.get("robustnessAssessment"), summary.get("robustnessAssessment")):
        if value:
            warnings.append(value)
    # TODO: Add scheduled optimization runs, automatic candidate suggestions,
    # human approval queues, auto-promotion after validation, and paper-performance monitoring.
    return dedupe_list(warnings)


def parse_csv_arg(value: str | None, fallback: list[str]) -> list[str]:
    if not value:
        return fallback
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        items = [item.strip() for item in str(value).split(",") if item.strip()]
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
