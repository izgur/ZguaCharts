from __future__ import annotations

import json
import math
import os
import subprocess

from flask import Flask, jsonify, render_template, request

from data_source import DATA_SOURCE_CONFIG, fetch_candles, fetch_historical_candles
from indicators import available_indicators, build_indicator_payload
from signals import build_signal_payload
from strategy import DEFAULT_PRESET_ID, preset_options


app = Flask(__name__)

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

    try:
        candles_payload = fetch_candles(source, symbol, timeframe, limit=limit)
        payload = build_signal_payload(candles_payload["candles"])
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


@app.get("/api/strategy-ranking")
def strategy_ranking():
    source = request.args.get("source", "bybit")
    symbols = parse_csv_arg(request.args.get("symbols"), ["BTCUSDT"])
    timeframes = parse_csv_arg(request.args.get("timeframes"), ["1h"])
    period = request.args.get("period", "60d")
    presets = parse_csv_arg(request.args.get("presets"), [DEFAULT_PRESET_ID])
    limit = int(request.args.get("limit", "3000"))
    fee_pct = float(request.args.get("fee_pct", "0"))
    slippage_pct = float(request.args.get("slippage_pct", "0"))
    min_trades = int(request.args.get("min_trades", "0"))

    rows = []
    errors = []
    rankable_rows = []
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
                    )
                    metrics = ranking_metrics_from_backtest(payload)
                    row = {
                        "strategy": payload.get("preset") or preset,
                        "preset": preset,
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "period": period,
                        "returnPct": metrics["totalReturn"],
                        "winRate": metrics["winRate"],
                        "maxDrawdown": metrics["maxDrawdown"],
                        "profitFactor": metrics["profitFactor"],
                        "trades": metrics["trades"],
                        "averageBarsHeld": metrics["averageBarsHeld"],
                        "score": ranking_score(metrics),
                    }
                    rows.append(row)
                    if row["trades"] >= min_trades:
                        rankable_rows.append(row)
                except Exception as exc:
                    errors.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "preset": preset,
                        "error": str(exc),
                    })

    rankable_rows.sort(key=lambda item: item["score"], reverse=True)
    for index, row in enumerate(rankable_rows, start=1):
        row["rank"] = index

    return jsonify({
        "source": source,
        "period": period,
        "limit": limit,
        "feePct": fee_pct,
        "slippagePct": slippage_pct,
        "minTrades": min_trades,
        "rows": rankable_rows,
        "allRows": rows,
        "errors": errors,
        "summary": ranking_summary(rankable_rows),
        "diagnostics": {
            "symbols": symbols,
            "timeframes": timeframes,
            "presets": presets,
            "combinationsRequested": len(symbols) * len(timeframes) * len(presets),
            "combinationsCompleted": len(rows),
            "rowsAfterMinTrades": len(rankable_rows),
        },
    })


@app.get("/api/paper/status")
def paper_status():
    try:
        state_path = os.path.join(app.root_path, "data", "paper-state.json")
        config_path = os.path.join(app.root_path, "config", "paper-candidate.json")
        journal_path = os.path.join(app.root_path, "reports", "paper-journal.jsonl")
        state = read_json_file(state_path, {})
        candidate = read_json_file(config_path, {})
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
                "regimeMode": candidate.get("regimeMode"),
                "fillModel": candidate.get("fillModel"),
                "makerFeePct": candidate.get("makerFeePct"),
                "takerFeePct": candidate.get("takerFeePct"),
                "slippageBps": candidate.get("slippageBps"),
            },
            "equityCurve": state.get("equityCurve", [])[-500:],
        })
    except Exception as exc:
        return jsonify({"error": f"Could not load paper status: {exc}"}), 502


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
        ["node", "cli/backtest.js", "--stdin-json"],
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
            "node",
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


def ranking_score(metrics: dict) -> float:
    """Backend-only ranking score for the /analysis page.

    The frontend must render this score as data and must not duplicate this
    formula in JavaScript.
    """
    profit_factor = min(metrics["profitFactor"], 5)
    trades_component = min(metrics["trades"], 200) / 10
    score = (
        metrics["totalReturn"]
        + profit_factor * 18
        + metrics["winRate"] * 0.25
        + trades_component
        - metrics["maxDrawdown"] * 1.3
    )
    return round(score, 2)


def ranking_summary(rows: list[dict]) -> dict:
    if not rows:
        return {
            "bestOverall": None,
            "bestWinRate": None,
            "lowestDrawdown": None,
            "worstResult": None,
        }
    return {
        "bestOverall": rows[0],
        "bestWinRate": max(rows, key=lambda item: item["winRate"]),
        "lowestDrawdown": min(rows, key=lambda item: item["maxDrawdown"]),
        "worstResult": min(rows, key=lambda item: item["score"]),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
