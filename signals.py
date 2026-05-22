from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import atr, bollinger_bands, candles_to_frame, ema, macd, rsi, sma, supertrend, vwap


BUY_THRESHOLD = 60
SELL_THRESHOLD = -60


def build_signal_payload(candles: list[dict]) -> dict:
    """Score technical-analysis hints from standard OHLCV candles.

    Broker integrations stay in data_source.py. As long as a broker returns
    time/open/high/low/close/volume candles, this scoring module needs no broker
    specific changes.
    """
    df = candles_to_frame(candles)
    if df.empty or len(df) < 30:
        return empty_payload()

    features = build_features(df)
    scored = pd.DataFrame(index=df.index)
    scored["time"] = df["time"]
    scored["close"] = df["close"]
    scored["score"] = features.apply(score_row, axis=1).clip(-100, 100).round(0)

    latest_index = scored["score"].last_valid_index()
    if latest_index is None:
        return empty_payload()

    latest_features = features.loc[latest_index]
    latest_score = int(scored.loc[latest_index, "score"])
    label = label_for_score(latest_score)

    return {
        "score": latest_score,
        "label": label,
        "tone": tone_for_label(label),
        "components": components_for_row(latest_features),
        "warnings": warnings_for_row(latest_features),
        "markers": markers_from_scores(scored),
    }


def empty_payload() -> dict:
    return {
        "score": 0,
        "label": "NEUTRAL",
        "tone": "neutral",
        "components": [],
        "warnings": ["Not enough candles to score signals."],
        "markers": [],
    }


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    ema9 = ema(df["close"], 9)
    ema21 = ema(df["close"], 21)
    ema50 = ema(df["close"], 50)
    ema200 = ema(df["close"], 200)
    rsi14 = rsi(df["close"], 14)
    macd_line, macd_signal, _ = macd(df["close"], 12, 26, 9)
    vwap_line = vwap(df)
    bb_upper, bb_middle, bb_lower = bollinger_bands(df["close"], 20, 2)
    bb_width = (bb_upper - bb_lower) / bb_middle.replace(0, np.nan)
    bb_width_ma = bb_width.rolling(20, min_periods=10).mean()
    volume_ma20 = sma(df["volume"], 20)
    atr14 = atr(df, 14)
    atr_pct = atr14 / df["close"].replace(0, np.nan)
    atr_pct_ma = atr_pct.rolling(20, min_periods=10).mean()
    supertrend_line = supertrend(df, 10, 3)

    return pd.DataFrame(
        {
            "close": df["close"],
            "close_change": df["close"].diff(),
            "ema_trend": np.where(ema50 > ema200, 1, -1),
            "ema_momentum": np.where(ema9 > ema21, 1, -1),
            "supertrend": np.where(df["close"] >= supertrend_line, 1, -1),
            "rsi": rsi14,
            "macd": np.where(macd_line > macd_signal, 1, -1),
            "price_vwap": np.where(df["close"] > vwap_line, 1, -1),
            "bb_width": bb_width,
            "bb_width_ma": bb_width_ma,
            "volume": df["volume"],
            "volume_ma20": volume_ma20,
            "atr_pct": atr_pct,
            "atr_pct_ma": atr_pct_ma,
        }
    )


def score_row(row: pd.Series) -> float:
    score = 0
    score += 20 * clean_direction(row["ema_trend"])
    score += 15 * clean_direction(row["ema_momentum"])
    score += 15 * clean_direction(row["supertrend"])
    score += score_rsi(row["rsi"])
    score += 15 * clean_direction(row["macd"])
    score += 10 * clean_direction(row["price_vwap"])
    score += score_bollinger(row)
    score += score_volume(row)
    score += score_atr(row)
    return score


def score_rsi(value: float) -> int:
    if pd.isna(value):
        return 0
    if value >= 70:
        return 4
    if value > 50:
        return 10
    if value <= 30:
        return -4
    return -10


def score_bollinger(row: pd.Series) -> int:
    if pd.isna(row["bb_width"]) or pd.isna(row["bb_width_ma"]):
        return 0
    if row["bb_width"] < row["bb_width_ma"] * 0.75:
        return 0
    if row["bb_width"] > row["bb_width_ma"] * 1.2:
        return 5 if row["close_change"] >= 0 else -5
    return 0


def score_volume(row: pd.Series) -> int:
    if pd.isna(row["volume_ma20"]) or row["volume_ma20"] <= 0:
        return 0
    if row["volume"] > row["volume_ma20"] * 1.5:
        return 5 if row["close_change"] >= 0 else -5
    return 0


def score_atr(row: pd.Series) -> int:
    if pd.isna(row["atr_pct"]) or pd.isna(row["atr_pct_ma"]):
        return 0
    if row["atr_pct"] > row["atr_pct_ma"] * 1.8:
        return -5 if row["close_change"] < 0 else 0
    return 0


def clean_direction(value: float) -> int:
    if pd.isna(value):
        return 0
    return 1 if value >= 0 else -1


def components_for_row(row: pd.Series) -> list[dict]:
    return [
        component("EMA trend", 20 * clean_direction(row["ema_trend"])),
        component("EMA momentum", 15 * clean_direction(row["ema_momentum"])),
        component("Supertrend", 15 * clean_direction(row["supertrend"])),
        component("RSI", score_rsi(row["rsi"])),
        component("MACD", 15 * clean_direction(row["macd"])),
        component("Price vs VWAP", 10 * clean_direction(row["price_vwap"])),
        component("Bollinger state", score_bollinger(row)),
        component("Volume spike", score_volume(row)),
        component("ATR volatility", score_atr(row)),
    ]


def component(name: str, score: int) -> dict:
    return {"name": name, "score": int(score)}


def warnings_for_row(row: pd.Series) -> list[str]:
    warnings = []
    if not pd.isna(row["rsi"]) and row["rsi"] >= 70:
        warnings.append("RSI is overbought.")
    if not pd.isna(row["rsi"]) and row["rsi"] <= 30:
        warnings.append("RSI is oversold.")
    if not pd.isna(row["bb_width"]) and not pd.isna(row["bb_width_ma"]):
        if row["bb_width"] < row["bb_width_ma"] * 0.75:
            warnings.append("Bollinger Bands are squeezed.")
        elif row["bb_width"] > row["bb_width_ma"] * 1.2:
            warnings.append("Bollinger Bands are expanding.")
    if not pd.isna(row["volume_ma20"]) and row["volume"] > row["volume_ma20"] * 1.5:
        warnings.append("Volume spike above MA20.")
    if not pd.isna(row["atr_pct"]) and not pd.isna(row["atr_pct_ma"]) and row["atr_pct"] > row["atr_pct_ma"] * 1.8:
        warnings.append("ATR volatility is elevated.")
    return warnings


def markers_from_scores(scored: pd.DataFrame) -> list[dict]:
    markers = []
    prior_score = None
    for _, row in scored.dropna(subset=["score"]).iterrows():
        current = float(row["score"])
        if prior_score is not None:
            if prior_score <= BUY_THRESHOLD < current:
                markers.append(signal_marker(row["time"], "BUY", "belowBar", "#12b886", "arrowUp"))
            elif prior_score >= SELL_THRESHOLD > current:
                markers.append(signal_marker(row["time"], "SELL", "aboveBar", "#ff5c7a", "arrowDown"))
        prior_score = current
    return markers[-80:]


def signal_marker(time_value: float, text: str, position: str, color: str, shape: str) -> dict:
    return {
        "time": int(time_value),
        "position": position,
        "color": color,
        "shape": shape,
        "text": text,
    }


def label_for_score(score: int) -> str:
    if score >= 75:
        return "STRONG BUY"
    if score > 25:
        return "BUY"
    if score <= -75:
        return "STRONG SELL"
    if score < -25:
        return "SELL"
    return "NEUTRAL"


def tone_for_label(label: str) -> str:
    if "BUY" in label:
        return "buy"
    if "SELL" in label:
        return "sell"
    return "neutral"
