from __future__ import annotations

import json
import math
import os
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
RESEARCH_RUNS_PATH = Path(app.root_path) / "data" / "research-runs.json"
LEARNING_CONFIG_DEFAULT_PATH = CONFIG_DIR / "learning-runner.default.json"
LEARNING_CONFIG_LOCAL_PATH = LOCAL_CONFIG_DIR / "learning-runner.json"
LEARNING_REPORTS_PATH = Path(app.root_path) / "data" / "learning-reports.json"
LEARNING_DECISIONS_PATH = Path(app.root_path) / "data" / "learning-decisions.json"
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
@app.get("/charts")
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
        tick_readiness = build_paper_tick_readiness(request.args)
        real_enabled, _ = paper_real_trading_enabled()
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
            "warnings": state.get("warnings", []),
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
            "staleWarnings": runtime.get("journal", {}).get("staleWarnings", []),
            "recentWarnings": runtime.get("journal", {}).get("recentWarnings", []),
            "activeWarnings": runtime.get("journal", {}).get("activeWarnings", []),
            "watchWarnings": runtime.get("journal", {}).get("watchWarnings", []),
            "staleWatchWarnings": runtime.get("journal", {}).get("staleWatchWarnings", []),
            "blockingWarnings": runtime.get("journal", {}).get("blockingWarnings", []),
            "activeWarningCount": len(runtime.get("journal", {}).get("activeWarnings", [])),
            "watchWarningCount": len(runtime.get("journal", {}).get("watchWarnings", [])),
            "staleWatchWarningCount": len(runtime.get("journal", {}).get("staleWatchWarnings", [])),
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


@app.get("/api/paper/observation-targets")
def paper_observation_targets():
    try:
        return jsonify(build_paper_observation_targets(request.args))
    except Exception as exc:
        return jsonify({"error": f"Could not build paper observation targets: {exc}"}), 502


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
            "loopCommand": "python scripts/paper_run_once.py --loop --interval-minutes 5 --max-iterations 12 --log-file reports/paper-runner-session.jsonl",
            "notes": [
                "This is local paper simulation only and cannot place real trades.",
                "The loop runs only when started manually; no daemon or scheduled task is created.",
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
    }


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
    closed_trades = len(state.get("closedTrades", []) or [])
    signals = event_types.count("SIGNAL")
    open_positions = len(state.get("openPositions", []) or [])
    active_warning_count = int(safe_float(runtime_journal.get("activeWarningCount"), len(runtime_journal.get("activeWarnings") or [])))
    watch_warning_count = int(safe_float(runtime_journal.get("watchWarningCount"), len(runtime_journal.get("watchWarnings") or [])))
    stale_watch_warning_count = int(safe_float(runtime_journal.get("staleWatchWarningCount"), len(runtime_journal.get("staleWatchWarnings") or [])))
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
            "warnings": dedupe_list(blocking_warnings + informational_warnings),
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
        if warning_buckets["watchWarnings"]:
            health_reasons.append(f"{len(warning_buckets['watchWarnings'])} watch-market warning(s) are informational and do not block active paper ticking.")
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
