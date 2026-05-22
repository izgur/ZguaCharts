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
}

OVERLAY_INDICATORS = {"ema", "sma", "vwap", "bbands", "supertrend"}
LOWER_PANE_INDICATORS = {"rsi", "macd", "atr", "stochrsi", "volume"}


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
            overlays.append(line_series(f"EMA {period}", timed(ema(df["close"], period), df), color))

    if "sma" in requested_set:
        period = max(2, min(int(sma_period), 500))
        overlays.append(line_series(f"SMA {period}", timed(sma(df["close"], period), df), "#ced4da"))

    if "vwap" in requested_set:
        overlays.append(line_series("VWAP", timed(vwap(df), df), "#b197fc"))

    if "bbands" in requested_set:
        upper, middle, lower = bollinger_bands(df["close"], 20, 2)
        overlays.extend(
            [
                line_series("BB Upper", timed(upper, df), "#91a7ff"),
                line_series("BB Middle", timed(middle, df), "#adb5bd"),
                line_series("BB Lower", timed(lower, df), "#91a7ff"),
            ]
        )

    if "supertrend" in requested_set:
        trend = supertrend(df, 10, 3)
        overlays.append(line_series("Supertrend 10/3", timed(trend, df), "#69db7c"))

    if "rsi" in requested_set:
        panes.append({"id": "rsi", "title": "RSI 14", "series": [line_series("RSI", timed(rsi(df["close"], 14), df), "#fab005")]})

    if "macd" in requested_set:
        macd_line, signal, histogram = macd(df["close"], 12, 26, 9)
        panes.append(
            {
                "id": "macd",
                "title": "MACD 12/26/9",
                "series": [
                    histogram_series("Histogram", timed(histogram, df)),
                    line_series("MACD", timed(macd_line, df), "#4dabf7"),
                    line_series("Signal", timed(signal, df), "#ff922b"),
                ],
            }
        )

    if "atr" in requested_set:
        panes.append({"id": "atr", "title": "ATR 14", "series": [line_series("ATR", timed(atr(df, 14), df), "#20c997")]})

    if "stochrsi" in requested_set:
        k, d = stoch_rsi(df["close"], 14, 14, 3, 3)
        panes.append(
            {
                "id": "stochrsi",
                "title": "Stoch RSI",
                "series": [line_series("%K", timed(k, df), "#12b886"), line_series("%D", timed(d, df), "#f06595")],
            }
        )

    if "volume" in requested_set:
        panes.append(
            {
                "id": "volume",
                "title": "Volume",
                "series": [
                    histogram_series("Volume", timed(df["volume"], df), use_direction_colors=True, close=df["close"]),
                    line_series("Volume MA20", timed(sma(df["volume"], 20), df), "#f5b84b"),
                ],
            }
        )

    return {"overlays": compact_series(overlays), "panes": compact_panes(panes)}


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


def line_series(name: str, series: pd.Series, color: str) -> dict:
    return {"type": "line", "name": name, "color": color, "data": series_to_points(series)}


def histogram_series(
    name: str,
    series: pd.Series,
    use_direction_colors: bool = False,
    close: Optional[pd.Series] = None,
) -> dict:
    data = []
    for index, value in series.items():
        if pd.isna(value):
            continue
        point = {"time": int(series.index_frame["time"].iloc[index]), "value": float(value)}
        if use_direction_colors and close is not None:
            prior = close.iloc[index - 1] if index > 0 else close.iloc[index]
            point["color"] = "rgba(18, 184, 134, 0.55)" if close.iloc[index] >= prior else "rgba(255, 92, 122, 0.55)"
        elif value < 0:
            point["color"] = "rgba(255, 92, 122, 0.6)"
        else:
            point["color"] = "rgba(18, 184, 134, 0.6)"
        data.append(point)
    return {"type": "histogram", "name": name, "color": "#748ffc", "data": data}


def series_to_points(series: pd.Series) -> list[dict]:
    return [
        {"time": int(series.index_frame["time"].iloc[index]), "value": float(value)}
        for index, value in series.items()
        if not pd.isna(value) and np.isfinite(value)
    ]


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
