"""Shared causal divergence detections for simulation and chart inspection."""
import math
import numpy as np
from flask import Blueprint, jsonify, request
from indicators import candles_to_frame, normalize_indicator_settings
from indicator_catalog import zgua_events, zgua_oscillators

blueprint = Blueprint("zgua", __name__, url_prefix="/api/zgua")


def detection(candles, settings=None):
    if not isinstance(candles, list) or not 1 <= len(candles) <= 50000:
        raise ValueError("Supply 1 to 50000 candles")
    previous = -1
    for row in candles:
        if not isinstance(row, dict):
            raise ValueError("Invalid candle")
        for key in ("time", "open", "high", "low", "close", "volume"):
            value = row.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Invalid candle " + key)
        if row["time"] <= previous or min(row[k] for k in ("open", "high", "low", "close")) <= 0 or row["volume"] < 0:
            raise ValueError("Invalid candle order or prices")
        if row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"]):
            raise ValueError("Invalid candle price range")
        previous = row["time"]
    defaults = normalize_indicator_settings({"zgua": {}})["zgua"]
    if settings is not None and not isinstance(settings, dict):
        raise ValueError("Invalid Zgua parameters")
    settings = {key: value for key, value in (settings or {}).items() if key in defaults}
    params = normalize_indicator_settings({"zgua": settings})["zgua"]
    params.update(max_events=len(candles)*8, engulfing=0, sweep=0)
    frame = candles_to_frame(candles)
    oscillators = zgua_oscillators(frame, params)
    return frame, oscillators, zgua_events(frame, oscillators, params)


@blueprint.post("/events")
def events():
    try:
        body = request.get_json() or {}
        _, _, found = detection(body.get("candles"), body.get("params"))
        return jsonify({"events": found})
    except (ValueError, TypeError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.post("/inspect")
def inspect():
    try:
        body = request.get_json() or {}
        frame, oscillators, found = detection(body.get("candles"), body.get("params"))
        selected = body.get("event") or {}
        event = next((item for item in found if all(item[key] == selected.get(key) for key in ("kind", "bullish", "firstTime", "pivotTime", "confirmedAt"))), None)
        if not event:
            raise ValueError("This divergence is not confirmed in the current chart")
        times = frame.time.tolist()
        start, end = times.index(event["firstTime"]), times.index(event["pivotTime"])
        studies = []
        color = "#50c9e8" if event["bullish"] else "#ff657f"
        for name in event["studies"]:
            values = oscillators[name].to_numpy()
            a, b = float(values[start]), float(values[end])
            line = np.linspace(a, b, end-start+1)
            studies.append({"name": name, "firstValue": a, "pivotValue": b,
                "series": [{"name": name, "type": "line", "color": "#e4bf53", "data": [{"time": int(t), **({"value": float(v)} if np.isfinite(v) else {})} for t, v in zip(times, values)]},
                           {"name": "Swing connector", "type": "line", "color": color, "guide": True, "data": [{"time": int(times[start+i]), "value": float(v)} for i, v in enumerate(line)]}],
                "markers": [{"time": event["firstTime"], "position": "inBar", "shape": "circle", "color": color, "text": format(a, ".4g")}, {"time": event["pivotTime"], "position": "inBar", "shape": "circle", "color": color, "text": format(b, ".4g")} ]})
        return jsonify({"event": event, "studies": studies})
    except (ValueError, TypeError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400
