from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd

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


def available_indicators() -> list[dict]:
    return [
        {"id": key, "label": label, "placement": "overlay" if key in OVERLAY_INDICATORS else "pane"}
        for key, label in INDICATOR_LABELS.items()
    ]


def build_indicator_payload(
    candles: list[dict],
    requested: Iterable[str],
    sma_period: int = 20,
) -> dict:
    """Return Lightweight Charts-friendly indicator series.

    Any broker plugged into data_source.py only needs to return standard OHLCV
    candles. This module intentionally stays broker-agnostic.
    """
    requested_set = {item.strip().lower() for item in requested if item.strip()}
    df = candles_to_frame(candles)

    overlays = []
    panes = []
    if df.empty:
        return {"overlays": overlays, "panes": panes}

    if "ema" in requested_set:
        for period, color in [(9, "#f5b84b"), (21, "#3bc9db"), (50, "#748ffc"), (200, "#ff8787")]:
            overlays.append(line_series(f"EMA {period}", timed(ema(df["close"], period), df), color, warmup=period))

    if "sma" in requested_set:
        period = max(2, min(int(sma_period), 500))
        overlays.append(line_series(f"SMA {period}", timed(sma(df["close"], period), df), "#ced4da", warmup=period))

    if "vwap" in requested_set:
        overlays.append(line_series("VWAP", timed(vwap(df), df), "#b197fc"))

    if "bbands" in requested_set:
        upper, middle, lower = bollinger_bands(df["close"], 20, 2)
        overlays.extend(
            [
                line_series("BB Upper", timed(upper, df), "#91a7ff", warmup=20),
                line_series("BB Middle", timed(middle, df), "#adb5bd", warmup=20),
                line_series("BB Lower", timed(lower, df), "#91a7ff", warmup=20),
            ]
        )

    if "supertrend" in requested_set:
        trend = supertrend(df, 10, 3)
        overlays.append(line_series("Supertrend 10/3", timed(trend, df), "#69db7c", warmup=10))

    if "rsi" in requested_set:
        panes.append(
            {
                "id": "rsi",
                "title": "RSI 14",
                "series": [
                    guide_line("Overbought 70", df, 70, "#ff5c7a"),
                    guide_line("Midline 50", df, 50, "#64748b"),
                    guide_line("Oversold 30", df, 30, "#12b886"),
                    line_series("RSI", timed(rsi(df["close"], 14), df), "#fab005", warmup=14),
                ],
            }
        )

    if "macd" in requested_set:
        macd_line, signal, histogram = macd(df["close"], 12, 26, 9)
        panes.append(
            {
                "id": "macd",
                "title": "MACD 12/26/9",
                "series": [
                    histogram_series("Histogram", timed(histogram, df)),
                    guide_line("Zero", df, 0, "#64748b"),
                    line_series("MACD", timed(macd_line, df), "#4dabf7", warmup=26),
                    line_series("Signal", timed(signal, df), "#ff922b", warmup=35),
                ],
            }
        )

    if "atr" in requested_set:
        panes.append({"id": "atr", "title": "ATR 14", "series": [line_series("ATR", timed(atr(df, 14), df), "#20c997", warmup=14)]})

    if "stochrsi" in requested_set:
        k, d = stoch_rsi(df["close"], 14, 14, 3, 3)
        panes.append(
            {
                "id": "stochrsi",
                "title": "Stoch RSI",
                "series": [
                    guide_line("Upper 80", df, 80, "#ff5c7a"),
                    guide_line("Middle 50", df, 50, "#64748b"),
                    guide_line("Lower 20", df, 20, "#12b886"),
                    line_series("%K", timed(k, df), "#12b886", warmup=30),
                    line_series("%D", timed(d, df), "#f06595", warmup=33),
                ],
            }
        )

    if "cipherb" in requested_set:
        wt1, wt2, money_flow, rsi_line = cipher_b_style(df)
        panes.append(
            {
                "id": "cipherb",
                "title": "Cipher B-style Oscillator",
                "series": [
                    guide_line("Extreme high 60", df, 60, "#ff5c7a"),
                    guide_line("Upper 53", df, 53, "#f06595"),
                    guide_line("Zero", df, 0, "#64748b"),
                    guide_line("Lower -53", df, -53, "#12b886"),
                    guide_line("Extreme low -60", df, -60, "#0ca678"),
                    histogram_series("Money flow", timed(money_flow, df)),
                    line_series("WaveTrend", timed(wt1, df), "#4dabf7", warmup=28),
                    line_series("WT signal", timed(wt2, df), "#f5b84b", warmup=32),
                    line_series("RSI scaled", timed(rsi_line, df), "#b197fc", warmup=14),
                ],
            }
        )

    if "volume" in requested_set:
        panes.append(
            {
                "id": "volume",
                "title": "Volume",
                "series": [
                    histogram_series("Volume", timed(df["volume"], df), use_direction_colors=True, close=df["close"]),
                    line_series("Volume MA20", timed(sma(df["volume"], 20), df), "#f5b84b", warmup=20),
                ],
            }
        )

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


def cipher_b_style(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Open, auditable WaveTrend/money-flow oscillator inspired by common Cipher B theory.

    This is not proprietary Market Cipher B code. It combines the public
    WaveTrend oscillator idea, a smoothed volume-weighted money-flow proxy, and
    a centered RSI line so users can inspect momentum, extremes, and flow in
    one lower pane.
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3
    esa = ema(hlc3, 10)
    deviation = ema((hlc3 - esa).abs(), 10)
    ci = (hlc3 - esa) / (0.015 * deviation.replace(0, np.nan))
    wt1 = ema(ci, 21)
    wt2 = sma(wt1, 4)

    candle_range = (df["high"] - df["low"]).replace(0, np.nan)
    raw_flow = ((df["close"] - df["open"]) / candle_range).clip(-1, 1) * 100
    volume_weight = df["volume"] / sma(df["volume"], 20).replace(0, np.nan)
    money_flow = ema((raw_flow * volume_weight).clip(-100, 100), 8)
    rsi_line = rsi(df["close"], 14) - 50
    return wt1, wt2, money_flow, rsi_line


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
