from flask import Flask, jsonify, render_template, request

from backtest import run_signal_backtest
from data_source import DATA_SOURCE_CONFIG, fetch_candles
from indicators import available_indicators, build_indicator_payload
from signals import build_signal_payload
from strategy import DEFAULT_PRESET_ID, preset_options


app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def config():
    payload = dict(DATA_SOURCE_CONFIG)
    payload["indicators"] = available_indicators()
    payload["strategy_presets"] = preset_options()
    payload["default_strategy_preset"] = DEFAULT_PRESET_ID
    return jsonify(payload)


@app.get("/api/candles")
def candles():
    source = request.args.get("source", "bybit")
    symbol = request.args.get("symbol", "BTCUSDT")
    timeframe = request.args.get("timeframe", "1m")
    limit = int(request.args.get("limit", "240"))

    try:
        payload = fetch_candles(source, symbol, timeframe, limit=limit)
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

    try:
        candles_payload = fetch_candles(source, symbol, timeframe, limit=limit)
        payload = build_indicator_payload(
            candles_payload["candles"],
            names.split(","),
            sma_period=sma_period,
        )
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
    allow_shorts = request.args.get("allowShorts", "false").lower() == "true"

    try:
        payload = run_signal_backtest(
            source,
            symbol,
            timeframe,
            period=period,
            preset_id=preset,
            fee_pct=fee_pct,
            slippage_pct=slippage_pct,
            limit=limit,
            allow_shorts=allow_shorts,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Could not run backtest: {exc}"}), 502

    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
