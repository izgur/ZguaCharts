from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd
from indicator_catalog import CATALOG, calculate as calculate_catalog_indicator

try:
    import pandas_ta as ta
except Exception:  # pandas-ta is optional; pure pandas fallbacks cover local use.
    ta = None


INDICATOR_LABELS = {
    "ema": "EMA 9/21/50/200",
    "sma": "SMA",
    "vwap": "VWAP",
    "bbands": "Bollinger Bands",
    "rsi": "RSI 14",
    "macd": "MACD 12/26/9",
    "atr": "ATR 14",
    "supertrend": "Supertrend 10/3",
    "stochrsi": "Stoch RSI",
    "volume": "Volume + MA20",
    "cipherb": "Cipher B-style Oscillator",
}

OVERLAY_INDICATORS = {"ema", "sma", "vwap", "bbands", "supertrend"}
LOWER_PANE_INDICATORS = {"rsi", "macd", "atr", "stochrsi", "volume", "cipherb"}

INDICATOR_PARAMETERS = {
    "ema": "close; lengths 9, 21, 50, 200",
    "sma": "close; length 20",
    "vwap": "HLC3; volume weighted; cumulative loaded history",
    "bbands": "close; length 20; deviation 2; population variance",
    "rsi": "close; length 14; levels 70, 50, 30",
    "macd": "close; fast 12; slow 26; signal 9",
    "atr": "length 14; true range",
    "supertrend": "HL2; ATR length 10; multiplier 3",
    "stochrsi": "close; RSI 14; stochastic 14; K 3; D 3; levels 80, 50, 20",
    "volume": "volume; SMA length 20",
    "cipherb": "HLC3; WT channel 10; average 21; signal SMA 4; scale 0.015; volume SMA 20; flow EMA 8; RSI 14; levels 60, 53, 0, -53, -60",
}

INDICATOR_DEFAULTS = {
    "ema": {"length1": 9, "length2": 21, "length3": 50, "length4": 200},
    "sma": {"length": 20}, "vwap": {},
    "bbands": {"length": 20, "deviation": 2},
    "rsi": {"length": 14, "upper": 70, "middle": 50, "lower": 30},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "atr": {"length": 14}, "supertrend": {"length": 10, "multiplier": 3},
    "stochrsi": {"rsi": 14, "stochastic": 14, "k": 3, "d": 3, "upper": 80, "middle": 50, "lower": 20},
    "volume": {"length": 20},
    "cipherb": {"channel": 10, "average": 21, "signal": 4, "volume": 20, "flow": 8, "rsi": 14},
}
for _key, _study in CATALOG.items():
    INDICATOR_LABELS[_key] = _study["label"]
    INDICATOR_PARAMETERS[_key] = _study["parameters"]
    INDICATOR_DEFAULTS[_key] = _study["defaults"]
    (OVERLAY_INDICATORS if _study["placement"] == "overlay" else LOWER_PANE_INDICATORS).add(_key)


def parameter_schema(key):
    fields = [{"name": name, "label": name.replace("length", "Length ").title(), "default": value,
             "min": 0 if name in ("upper", "middle", "lower") else 0.1 if name in ("deviation", "multiplier") else 2,
             "max": 100 if name in ("upper", "middle", "lower") else 20 if name in ("deviation", "multiplier") else 500,
             "step": 0.1 if name in ("deviation", "multiplier") else 1}
            for name, value in INDICATOR_DEFAULTS[key].items()]
    for field in fields:
        if key == "zgua":
            if field["name"] in ("draw_lines", "engulfing", "sweep") or field["name"].endswith(("_enabled", "_bullish", "_bearish")):
                field.update(min=0, max=1, step=1, type="boolean")
            elif field["name"] in ("price_equal_pct", "osc_equal_pct", "min_osc_pct"):
                field.update(min=0, max=20, step=0.01)
            elif field["name"] in ("left", "right", "lookback"):
                field.update(min=1, max=10, step=1)
            elif field["name"] == "max_events":
                field.update(min=1, max=200, step=1)
        if field["name"] == "offset": field.update(min=0, max=1, step=0.01)
        if field["name"] in ("sigma", "percent"): field.update(min=0.1, max=100, step=0.1)
        if field["name"] == "annualization": field.update(min=1, max=1000000, step=1)
        toggles = {"heikin_ashi": "Heikin Ashi source", "true_range": "Use true range", "legacy_bb": "Legacy BB multiplier", "atr_sma": "Use SMA ATR", "source_close": "Use close price"}
        if field["name"] in toggles: field.update(min=0, max=1, step=1, type="boolean", label=toggles[field["name"]])
        if field["name"] == "sensitivity": field.update(min=0.1, max=20, step=0.1)
        if key == "psar": field.update(min=0.001, max=1, step=0.001)
        if field["name"] == "factor": field.update(min=0.1, max=5, step=0.1)
        if field["name"].endswith("_shift"): field.update(min=0, max=100, step=1)
        if field["name"] == "scale": field.update(min=1, max=1000000000, step=1)
        if field["name"] in ("high_factor", "low_factor"): field.update(min=0.01, max=5, step=0.01)
    return fields


def normalize_indicator_settings(settings):
    if settings is None:
        settings = {}
    if not isinstance(settings, dict) or set(settings) - set(INDICATOR_DEFAULTS):
        raise ValueError("Invalid indicator settings")
    result = {}
    for key, defaults in INDICATOR_DEFAULTS.items():
        custom = settings.get(key, {})
        if not isinstance(custom, dict) or set(custom) - set(defaults):
            raise ValueError("Invalid parameters for " + key)
        values = dict(defaults)
        for field in parameter_schema(key):
            name = field["name"]
            value = custom.get(name, field["default"])
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not field["min"] <= value <= field["max"] or field["step"] == 1 and value != int(value):
                raise ValueError("Invalid " + key + " " + name)
            values[name] = int(value) if field["step"] == 1 else float(value)
        if key in ("rsi", "stochrsi") and not values["lower"] < values["middle"] < values["upper"]:
            raise ValueError("Indicator levels must increase: lower, middle, upper")
        if "fast" in values and "slow" in values and values["fast"] >= values["slow"]:
            raise ValueError(INDICATOR_LABELS[key] + " fast length must be less than slow length")
        if key == "psar" and values["start"] > values["maximum"]:
            raise ValueError("SAR start must not exceed maximum acceleration")
        if key == "zgua" and values["min_bars"] > values["max_bars"]:
            raise ValueError("Zgua minimum pivot distance must not exceed maximum")
        result[key] = values
    return result


def available_indicators() -> list[dict]:
    return [
        {"id": key, "label": label, "parameters": INDICATOR_PARAMETERS[key], "schema": parameter_schema(key), "placement": "overlay" if key in OVERLAY_INDICATORS else "pane"}
        for key, label in INDICATOR_LABELS.items()
    ]


def build_indicator_payload(
    candles: list[dict],
    requested: Iterable[str],
    sma_period: int = 20,
    settings=None,
) -> dict:
    """Return Lightweight Charts-friendly indicator series.

    Any broker plugged into data_source.py only needs to return standard OHLCV
    candles. This module intentionally stays broker-agnostic.
    """
    requested_set = {item.strip().lower() for item in requested if item.strip()}
    settings = normalize_indicator_settings(settings)
    if sma_period != 20:
        settings["sma"]["length"] = max(2, min(int(sma_period), 500))
    df = candles_to_frame(candles)

    overlays = []
    panes = []
    if df.empty:
        return {"overlays": overlays, "panes": panes}

    if "ema" in requested_set:
        for period, color in zip(settings["ema"].values(), ["#f5b84b", "#3bc9db", "#748ffc", "#ff8787"]):
            overlays.append(line_series(f"EMA {period}", timed(ema(df["close"], period), df), color, warmup=period))

    if "sma" in requested_set:
        period = settings["sma"]["length"]
        overlays.append(line_series(f"SMA {period}", timed(sma(df["close"], period), df), "#ced4da", warmup=period))

    if "vwap" in requested_set:
        overlays.append(line_series("VWAP", timed(vwap(df), df), "#b197fc"))

    if "bbands" in requested_set:
        length = settings["bbands"]["length"]
        upper, middle, lower = bollinger_bands(df["close"], length, settings["bbands"]["deviation"])
        overlays.extend(
            [
                line_series("BB Upper", timed(upper, df), "#91a7ff", warmup=length),
                line_series("BB Middle", timed(middle, df), "#adb5bd", warmup=length),
                line_series("BB Lower", timed(lower, df), "#91a7ff", warmup=length),
            ]
        )

    if "supertrend" in requested_set:
        length, multiplier = settings["supertrend"]["length"], settings["supertrend"]["multiplier"]
        trend = supertrend(df, length, multiplier)
        overlays.append(line_series(f"Supertrend {length}/{multiplier:g}", timed(trend, df), "#69db7c", warmup=length))

    if "rsi" in requested_set:
        p = settings["rsi"]
        panes.append(
            {
                "id": "rsi",
                "title": "RSI " + str(p["length"]),
                "series": [
                    guide_line("Overbought " + str(p["upper"]), df, p["upper"], "#ff5c7a"),
                    guide_line("Midline " + str(p["middle"]), df, p["middle"], "#64748b"),
                    guide_line("Oversold " + str(p["lower"]), df, p["lower"], "#12b886"),
                    line_series("RSI", timed(rsi(df["close"], p["length"]), df), "#fab005", warmup=p["length"]),
                ],
            }
        )

    if "macd" in requested_set:
        p = settings["macd"]
        macd_line, signal, histogram = macd(df["close"], p["fast"], p["slow"], p["signal"])
        panes.append(
            {
                "id": "macd",
                "title": "MACD {fast}/{slow}/{signal}".format(**p),
                "series": [
                    histogram_series("Histogram", timed(histogram, df)),
                    guide_line("Zero", df, 0, "#64748b"),
                    line_series("MACD", timed(macd_line, df), "#4dabf7", warmup=p["slow"]),
                    line_series("Signal", timed(signal, df), "#ff922b", warmup=p["slow"] + p["signal"]),
                ],
            }
        )

    if "atr" in requested_set:
        length = settings["atr"]["length"]
        panes.append({"id": "atr", "title": "ATR " + str(length), "series": [line_series("ATR", timed(atr(df, length), df), "#20c997", warmup=length)]})

    if "stochrsi" in requested_set:
        p = settings["stochrsi"]
        k, d = stoch_rsi(df["close"], p["rsi"], p["stochastic"], p["k"], p["d"])
        panes.append(
            {
                "id": "stochrsi",
                "title": "Stoch RSI",
                "series": [
                    guide_line("Upper " + str(p["upper"]), df, p["upper"], "#ff5c7a"),
                    guide_line("Middle " + str(p["middle"]), df, p["middle"], "#64748b"),
                    guide_line("Lower " + str(p["lower"]), df, p["lower"], "#12b886"),
                    line_series("%K", timed(k, df), "#12b886", warmup=p["rsi"] + p["stochastic"] + p["k"] - 1),
                    line_series("%D", timed(d, df), "#f06595", warmup=p["rsi"] + p["stochastic"] + p["k"] + p["d"] - 1),
                ],
            }
        )

    if "cipherb" in requested_set:
        p = settings["cipherb"]
        wt1, wt2, money_flow, rsi_line = cipher_b_style(df, p["channel"], p["average"], p["signal"], p["volume"], p["flow"], p["rsi"])
        cipher_signal = cipher_b_signal(wt1, wt2, money_flow, rsi_line)
        cipher_markers = cipher_b_markers(df, wt1, wt2)
        panes.append(
            {
                "id": "cipherb",
                "title": f"Cipher B-style Oscillator - {cipher_signal['action']}",
                "signal": cipher_signal,
                "markers": cipher_markers,
                "legend": cipher_b_legend(),
                "series": [
                    guide_line("Extreme high 60", df, 60, "#ff5c7a"),
                    guide_line("Upper 53", df, 53, "#f06595"),
                    guide_line("Zero", df, 0, "#64748b"),
                    guide_line("Lower -53", df, -53, "#12b886"),
                    guide_line("Extreme low -60", df, -60, "#0ca678"),
                    histogram_series("Money flow", timed(money_flow, df)),
                    line_series("WaveTrend", timed(wt1, df), "#4dabf7", warmup=p["channel"] + p["average"] - 3),
                    line_series("WT signal", timed(wt2, df), "#f5b84b", warmup=p["channel"] + p["average"] + p["signal"] - 3),
                    line_series("RSI scaled", timed(rsi_line, df), "#b197fc", warmup=p["rsi"]),
                ],
            }
        )

    if "volume" in requested_set:
        length = settings["volume"]["length"]
        panes.append(
            {
                "id": "volume",
                "title": "Volume",
                "series": [
                    histogram_series("Volume", timed(df["volume"], df), use_direction_colors=True, close=df["close"]),
                    line_series("Volume MA" + str(length), timed(sma(df["volume"], length), df), "#f5b84b", warmup=length),
                ],
            }
        )

    for key in CATALOG:
        if key not in requested_set:
            continue
        rendered = []
        for item in calculate_catalog_indicator(df, key, settings[key]):
            values = timed(item["values"], df)
            output = histogram_series(item["name"], values) if item["type"] == "histogram" else line_series(item["name"], values, item["color"])
            output.update(indicatorId=key, guide=item.get("guide", False))
            for field in ("offset", "cloud", "pointOnly", "segments", "events", "markerGroup"):
                if field in item: output[field] = item[field]
            if item.get("colors") is not None:
                for point, color in zip(output["data"], item["colors"]):
                    point["color"] = str(color)
            if "markers" in item:
                output["markers"] = item["markers"]
            rendered.append(output)
        if key in OVERLAY_INDICATORS:
            overlays.extend(rendered)
        else:
            panes.append({"id": key, "title": INDICATOR_LABELS[key], "series": rendered})
    diagnostics = indicator_diagnostics(df, overlays, panes)
    return {"overlays": compact_series(overlays), "panes": compact_panes(panes), "diagnostics": diagnostics}


def candles_to_frame(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    if df.empty:
        return df
    for column in ["time", "open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)


def timed(series: pd.Series, df: pd.DataFrame) -> pd.Series:
    series.attrs["source_frame"] = df
    return series


def ema(series: pd.Series, period: int) -> pd.Series:
    if ta is not None:
        result = ta.ema(series, length=period)
        if result is not None:
            return result
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    volume = df["volume"].replace(0, np.nan)
    return (typical * volume).cumsum() / volume.cumsum()


def bollinger_bands(series: pd.Series, period: int, stddev: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = sma(series, period)
    deviation = series.rolling(period, min_periods=period).std(ddof=0)
    return middle + (deviation * stddev), middle, middle - (deviation * stddev)


def rsi(series: pd.Series, period: int) -> pd.Series:
    if ta is not None:
        result = ta.rsi(series, length=period)
        if result is not None:
            return result
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    relative_strength = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + relative_strength))


def macd(series: pd.Series, fast: int, slow: int, signal_period: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal = ema(macd_line, signal_period)
    return macd_line, signal, macd_line - signal


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    previous_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    true_range = ranges.max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def supertrend(df: pd.DataFrame, period: int, multiplier: float) -> pd.Series:
    average_true_range = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2
    upper = hl2 + (multiplier * average_true_range)
    lower = hl2 - (multiplier * average_true_range)

    final_upper = upper.copy()
    final_lower = lower.copy()
    trend = pd.Series(index=df.index, dtype="float64")
    direction = pd.Series(index=df.index, dtype="int64")

    for index in range(len(df)):
        if index == 0:
            direction.iloc[index] = 1
            trend.iloc[index] = lower.iloc[index]
            continue

        if upper.iloc[index] < final_upper.iloc[index - 1] or df["close"].iloc[index - 1] > final_upper.iloc[index - 1]:
            final_upper.iloc[index] = upper.iloc[index]
        else:
            final_upper.iloc[index] = final_upper.iloc[index - 1]

        if lower.iloc[index] > final_lower.iloc[index - 1] or df["close"].iloc[index - 1] < final_lower.iloc[index - 1]:
            final_lower.iloc[index] = lower.iloc[index]
        else:
            final_lower.iloc[index] = final_lower.iloc[index - 1]

        if direction.iloc[index - 1] == -1 and df["close"].iloc[index] > final_upper.iloc[index]:
            direction.iloc[index] = 1
        elif direction.iloc[index - 1] == 1 and df["close"].iloc[index] < final_lower.iloc[index]:
            direction.iloc[index] = -1
        else:
            direction.iloc[index] = direction.iloc[index - 1]

        trend.iloc[index] = final_lower.iloc[index] if direction.iloc[index] == 1 else final_upper.iloc[index]

    return trend


def stoch_rsi(
    series: pd.Series,
    rsi_period: int,
    stoch_period: int,
    k_period: int,
    d_period: int,
) -> tuple[pd.Series, pd.Series]:
    rsi_values = rsi(series, rsi_period)
    lowest = rsi_values.rolling(stoch_period, min_periods=stoch_period).min()
    highest = rsi_values.rolling(stoch_period, min_periods=stoch_period).max()
    raw = ((rsi_values - lowest) / (highest - lowest).replace(0, np.nan)) * 100
    k = raw.rolling(k_period, min_periods=k_period).mean()
    d = k.rolling(d_period, min_periods=d_period).mean()
    return k, d


def cipher_b_style(df: pd.DataFrame, channel=10, average=21, signal=4, volume=20, flow=8, rsi_length=14) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Open, auditable WaveTrend/money-flow oscillator inspired by common Cipher B theory.

    This is not proprietary Market Cipher B code. It combines the public
    WaveTrend oscillator idea, a smoothed volume-weighted money-flow proxy, and
    a centered RSI line so users can inspect momentum, extremes, and flow in
    one lower pane.
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3
    esa = ema(hlc3, channel)
    deviation = ema((hlc3 - esa).abs(), channel)
    ci = (hlc3 - esa) / (0.015 * deviation.replace(0, np.nan))
    wt1 = ema(ci, average)
    wt2 = sma(wt1, signal)

    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    raw_flow = ((df["close"] - df["open"]) / candle_range).clip(-1, 1) * 100
    volume_weight = df["volume"] / sma(df["volume"], volume).replace(0, np.nan)
    money_flow = ema((raw_flow * volume_weight).clip(-100, 100), flow)
    rsi_line = rsi(df["close"], rsi_length) - 50
    return wt1, wt2, money_flow, rsi_line


def cipher_b_signal(wt1: pd.Series, wt2: pd.Series, money_flow: pd.Series, rsi_line: pd.Series) -> dict:
    latest = {
        "wt1": last_valid(wt1),
        "wt2": last_valid(wt2),
        "moneyFlow": last_valid(money_flow),
        "rsiLine": last_valid(rsi_line),
    }
    score = 0
    if latest["wt1"] is not None and latest["wt2"] is not None:
        score += 35 if latest["wt1"] > latest["wt2"] else -35
        if latest["wt1"] > 53:
            score += 10
        elif latest["wt1"] < -53:
            score -= 10
    if latest["moneyFlow"] is not None:
        score += 25 if latest["moneyFlow"] > 0 else -25
    if latest["rsiLine"] is not None:
        score += 15 if latest["rsiLine"] > 0 else -15
    label = "VERY BULLISH" if score >= 55 else "BULLISH" if score >= 20 else "VERY BEARISH" if score <= -55 else "BEARISH" if score <= -20 else "NEUTRAL"
    action = "LONG" if score >= 20 else "SHORT" if score <= -20 else "NEUTRAL"
    return {
        "label": label,
        "action": action,
        "score": int(max(-100, min(100, score))),
        "values": {key: nullable_float(value) for key, value in latest.items()},
    }


def last_valid(series: pd.Series):
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def cipher_b_markers(df: pd.DataFrame, wt1: pd.Series, wt2: pd.Series) -> list[dict]:
    markers = []
    for index in range(1, len(df)):
        previous_diff = wt1.iloc[index - 1] - wt2.iloc[index - 1]
        current_diff = wt1.iloc[index] - wt2.iloc[index]
        if pd.isna(previous_diff) or pd.isna(current_diff):
            continue
        if previous_diff <= 0 < current_diff:
            markers.append({
                "time": int(df["time"].iloc[index]),
                "position": "belowBar",
                "color": "#12b886",
                "shape": "arrowUp",
                "text": "",
                "label": "WT LONG",
            })
        elif previous_diff >= 0 > current_diff:
            markers.append({
                "time": int(df["time"].iloc[index]),
                "position": "aboveBar",
                "color": "#ff5c7a",
                "shape": "arrowDown",
                "text": "",
                "label": "WT SHORT",
            })

    swing_window = 8
    for index in range(swing_window * 2, len(df)):
        prior_slice = slice(index - swing_window * 2, index - swing_window)
        recent_slice = slice(index - swing_window, index)
        prior_price_low = df["low"].iloc[prior_slice].min()
        recent_price_low = df["low"].iloc[recent_slice].min()
        prior_wt_low = wt1.iloc[prior_slice].min()
        recent_wt_low = wt1.iloc[recent_slice].min()
        prior_price_high = df["high"].iloc[prior_slice].max()
        recent_price_high = df["high"].iloc[recent_slice].max()
        prior_wt_high = wt1.iloc[prior_slice].max()
        recent_wt_high = wt1.iloc[recent_slice].max()
        if recent_price_low < prior_price_low and recent_wt_low > prior_wt_low:
            markers.append({
                "time": int(df["time"].iloc[index]),
                "position": "belowBar",
                "color": "#69db7c",
                "shape": "circle",
                "text": "",
                "label": "Bull div",
            })
        if recent_price_high > prior_price_high and recent_wt_high < prior_wt_high:
            markers.append({
                "time": int(df["time"].iloc[index]),
                "position": "aboveBar",
                "color": "#ffa8b6",
                "shape": "circle",
                "text": "",
                "label": "Bear div",
            })
    return markers[-40:]


def cipher_b_legend() -> dict:
    return {
        "lines": [
            {"name": "WaveTrend", "color": "#4dabf7", "meaning": "Primary momentum oscillator."},
            {"name": "WT signal", "color": "#f5b84b", "meaning": "Smoothed trigger line; crosses can mark momentum shifts."},
            {"name": "Money flow", "color": "green/red histogram", "meaning": "Positive flow supports LONG bias; negative flow supports SHORT bias."},
            {"name": "RSI scaled", "color": "#b197fc", "meaning": "RSI centered around zero for trend pressure."},
        ],
        "zones": [
            {"zone": "Very bearish", "value": "< -60", "meaning": "Deep downside momentum; watch for exhaustion or bearish continuation."},
            {"zone": "Bearish", "value": "-60 to -10", "meaning": "Downside pressure dominates."},
            {"zone": "Neutral", "value": "-10 to +10", "meaning": "Momentum is mixed or transitional."},
            {"zone": "Bullish", "value": "+10 to +60", "meaning": "Upside pressure dominates."},
            {"zone": "Very bullish", "value": "> +60", "meaning": "Strong upside momentum; watch overbought risk."},
        ],
        "signals": [
            {"name": "Crosses", "meaning": "WaveTrend crossing above signal supports LONG; crossing below supports SHORT."},
            {"name": "Divergences", "meaning": "Price lower low with oscillator higher low is bullish; price higher high with oscillator lower high is bearish."},
            {"name": "Momentum changes", "meaning": "WaveTrend slope and money-flow direction show whether pressure is accelerating or fading."},
            {"name": "OB/OS zones", "meaning": "Zones near +/-53 and +/-60 highlight stretched conditions."},
        ],
    }


def line_series(name: str, series: pd.Series, color: str, warmup: int = 1) -> dict:
    return {"type": "line", "name": name, "color": color, "warmup": warmup, "data": series_to_points(series, warmup=warmup)}


def guide_line(name: str, df: pd.DataFrame, value: float, color: str) -> dict:
    data = [{"time": int(row["time"]), "value": float(value)} for _, row in df.iterrows()]
    return {
        "type": "line",
        "name": name,
        "color": color,
        "guide": True,
        "data": data,
    }


def histogram_series(
    name: str,
    series: pd.Series,
    use_direction_colors: bool = False,
    close: Optional[pd.Series] = None,
) -> dict:
    data = []
    for index, value in series.items():
        point = {"time": int(series.index_frame["time"].iloc[index]), "value": nullable_float(value)}
        if use_direction_colors and close is not None:
            prior = close.iloc[index - 1] if index > 0 else close.iloc[index]
            point["color"] = "rgba(18, 184, 134, 0.55)" if close.iloc[index] >= prior else "rgba(255, 92, 122, 0.55)"
        elif point["value"] is not None and point["value"] < 0:
            point["color"] = "rgba(255, 92, 122, 0.6)"
        else:
            point["color"] = "rgba(18, 184, 134, 0.6)"
        data.append(point)
    return {"type": "histogram", "name": name, "color": "#748ffc", "data": data}


def series_to_points(series: pd.Series, warmup: int = 1) -> list[dict]:
    return [
        {
            "time": int(series.index_frame["time"].iloc[index]),
            "value": None if index < warmup - 1 else nullable_float(value),
        }
        for index, value in series.items()
    ]


def nullable_float(value) -> Optional[float]:
    if pd.isna(value) or not np.isfinite(value):
        return None
    return float(value)


def compact_series(series_list: list[dict]) -> list[dict]:
    return [item for item in series_list if item["data"]]


def compact_panes(panes: list[dict]) -> list[dict]:
    compacted = []
    for pane in panes:
        series = compact_series(pane["series"])
        if series:
            compacted.append({**pane, "series": series})
    return compacted
pd.Series.index_frame = property(lambda self: self.attrs.get("source_frame"))


def indicator_diagnostics(df: pd.DataFrame, overlays: list[dict], panes: list[dict]) -> dict:
    all_series = overlays + [series for pane in panes for series in pane["series"]]
    overlay_points = {series["name"]: len(series["data"]) for series in all_series}
    first_overlay_time = None
    last_overlay_time = None
    first_non_null = {}
    for series in all_series:
        non_null = [point for point in series["data"] if point.get("value") is not None]
        if non_null:
            first_non_null[series["name"]] = non_null[0]["time"]
            first_overlay_time = non_null[0]["time"] if first_overlay_time is None else min(first_overlay_time, non_null[0]["time"])
            last_overlay_time = non_null[-1]["time"] if last_overlay_time is None else max(last_overlay_time, non_null[-1]["time"])
    return {
        "chartCandlesCount": len(df),
        "backtestCandlesCount": None,
        "overlayPoints": overlay_points,
        "firstChartCandleTime": int(df["time"].iloc[0]) if not df.empty else None,
        "lastChartCandleTime": int(df["time"].iloc[-1]) if not df.empty else None,
        "firstOverlayTime": first_overlay_time,
        "lastOverlayTime": last_overlay_time,
        "firstNonNullOverlayTime": first_non_null,
        "warmupBars": {"EMA 50": 50, "EMA 200": 200, "Donchian": 55, "ATR": 14},
        "droppedBarsReason": "none; full-length arrays include null values before warmup",
    }
