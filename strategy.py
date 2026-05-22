from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from indicators import atr, bollinger_bands, ema, macd, rsi, sma, vwap
from signals import BUY_THRESHOLD, SELL_THRESHOLD, build_features, score_row


@dataclass(frozen=True)
class StrategyPreset:
    name: str
    warmup: int
    entry_score: int
    confirmation_bars: int
    cooldown_bars: int
    min_hold_bars: int
    rsi_min: float
    rsi_max: float
    volume_multiplier: float
    atr_pct_min: float
    atr_pct_max: float
    use_filters: bool
    use_original: bool = False


PRESETS = {
    "conservative_trend": StrategyPreset(
        name="Conservative Trend",
        warmup=250,
        entry_score=70,
        confirmation_bars=2,
        cooldown_bars=5,
        min_hold_bars=4,
        rsi_min=50,
        rsi_max=68,
        volume_multiplier=1.1,
        atr_pct_min=0.0004,
        atr_pct_max=0.06,
        use_filters=True,
    ),
    "momentum_scalping": StrategyPreset(
        name="Momentum Scalping",
        warmup=150,
        entry_score=65,
        confirmation_bars=2,
        cooldown_bars=3,
        min_hold_bars=3,
        rsi_min=52,
        rsi_max=75,
        volume_multiplier=1.0,
        atr_pct_min=0.0002,
        atr_pct_max=0.08,
        use_filters=True,
    ),
    "mean_reversion": StrategyPreset(
        name="Mean Reversion",
        warmup=200,
        entry_score=55,
        confirmation_bars=1,
        cooldown_bars=5,
        min_hold_bars=5,
        rsi_min=35,
        rsi_max=58,
        volume_multiplier=0.8,
        atr_pct_min=0.0002,
        atr_pct_max=0.07,
        use_filters=False,
    ),
    "original": StrategyPreset(
        name="Current Original Strategy",
        warmup=0,
        entry_score=BUY_THRESHOLD,
        confirmation_bars=1,
        cooldown_bars=0,
        min_hold_bars=0,
        rsi_min=0,
        rsi_max=100,
        volume_multiplier=0,
        atr_pct_min=0,
        atr_pct_max=1,
        use_filters=False,
        use_original=True,
    ),
}

DEFAULT_PRESET_ID = "conservative_trend"
SKIPPED_REASON_KEYS = [
    "trend_filter_failed",
    "vwap_filter_failed",
    "rsi_filter_failed",
    "volume_filter_failed",
    "atr_filter_failed",
    "cooldown_active",
    "warmup_active",
    "confirmation_missing",
]


def preset_options() -> list[dict]:
    return [{"id": preset_id, "label": preset.name} for preset_id, preset in PRESETS.items()]


def get_preset(preset_id: Optional[str]) -> StrategyPreset:
    return PRESETS.get(preset_id or DEFAULT_PRESET_ID, PRESETS[DEFAULT_PRESET_ID])


def build_strategy_frame(df: pd.DataFrame) -> pd.DataFrame:
    features = build_features(df)
    macd_line, macd_signal, _ = macd(df["close"], 12, 26, 9)
    bb_upper, bb_middle, bb_lower = bollinger_bands(df["close"], 20, 2)

    frame = features.copy()
    frame["time"] = df["time"]
    frame["open"] = df["open"]
    frame["high"] = df["high"]
    frame["low"] = df["low"]
    frame["close"] = df["close"]
    frame["volume"] = df["volume"]
    frame["score"] = features.apply(score_row, axis=1).clip(-100, 100)
    frame["ema21_value"] = ema(df["close"], 21)
    frame["ema50_value"] = ema(df["close"], 50)
    frame["ema200_value"] = ema(df["close"], 200)
    frame["vwap_value"] = vwap(df)
    frame["macd_line"] = macd_line
    frame["macd_signal"] = macd_signal
    frame["atr"] = atr(df, 14)
    frame["bb_upper"] = bb_upper
    frame["bb_middle"] = bb_middle
    frame["bb_lower"] = bb_lower
    frame["above_threshold"] = frame["score"] >= PRESETS[DEFAULT_PRESET_ID].entry_score
    return frame


def evaluate_entry(
    frame: pd.DataFrame,
    index: int,
    preset: StrategyPreset,
    cooldown_remaining: int,
) -> tuple[bool, list[str]]:
    row = frame.iloc[index]
    reasons = []

    if index < preset.warmup:
        reasons.append("warmup_active")
    if cooldown_remaining > 0:
        reasons.append("cooldown_active")
    if not score_confirmed(frame, index, preset.entry_score, preset.confirmation_bars):
        reasons.append("confirmation_missing")

    if preset.use_original:
        prior_score = frame["score"].iloc[index - 1] if index > 0 else np.nan
        entered = not pd.isna(prior_score) and prior_score <= preset.entry_score < row["score"]
        return entered and not reasons, reasons

    if preset.use_filters:
        if not (row["ema50_value"] > row["ema200_value"]):
            reasons.append("trend_filter_failed")
        if not (row["close"] > row["vwap_value"]):
            reasons.append("vwap_filter_failed")
        if not (preset.rsi_min <= row["rsi"] <= preset.rsi_max):
            reasons.append("rsi_filter_failed")
        if not (row["volume"] > row["volume_ma20"] * preset.volume_multiplier):
            reasons.append("volume_filter_failed")
        if not (preset.atr_pct_min <= row["atr_pct"] <= preset.atr_pct_max):
            reasons.append("atr_filter_failed")
    elif preset.name == "Mean Reversion":
        if not (row["rsi"] <= preset.rsi_max and row["close"] <= row["bb_middle"]):
            reasons.append("rsi_filter_failed")
        if not (preset.atr_pct_min <= row["atr_pct"] <= preset.atr_pct_max):
            reasons.append("atr_filter_failed")

    return len(reasons) == 0, reasons


def score_confirmed(frame: pd.DataFrame, index: int, threshold: float, bars: int) -> bool:
    if bars <= 1:
        return frame["score"].iloc[index] >= threshold
    start = index - bars + 1
    if start < 0:
        return False
    window = frame["score"].iloc[start : index + 1]
    prior = frame["score"].iloc[start - 1] if start > 0 else np.nan
    return bool((window >= threshold).all() and (pd.isna(prior) or prior < threshold))


def evaluate_exit(
    frame: pd.DataFrame,
    index: int,
    position: dict,
    preset: StrategyPreset,
) -> tuple[Optional[float], Optional[str]]:
    row = frame.iloc[index]

    if position.get("stop") is not None and row["low"] <= position["stop"]:
        return float(position["stop"]), "ATR stop"
    if position.get("take_profit") is not None and row["high"] >= position["take_profit"]:
        return float(position["take_profit"]), "ATR take profit"
    if position.get("trailing_stop") is not None and row["low"] <= position["trailing_stop"]:
        return float(position["trailing_stop"]), "Trailing ATR stop"

    bars_held = index - position["entry_index"]
    if preset.use_original:
        if row["score"] < 0:
            return float(row["close"]), "Score below 0"
        prior_score = frame["score"].iloc[index - 1] if index > 0 else np.nan
        if not pd.isna(prior_score) and prior_score >= SELL_THRESHOLD > row["score"]:
            return float(row["close"]), "Score crossed below -60"
        return None, None

    if bars_held < preset.min_hold_bars:
        return None, None
    if row["close"] < row["ema21_value"]:
        return float(row["close"]), "Close below EMA21"
    if row["score"] < -20:
        return float(row["close"]), "Score below -20"
    if macd_crossed_below(frame, index):
        return float(row["close"]), "MACD crossed below signal"
    return None, None


def update_trailing_stop(position: dict, row: pd.Series) -> None:
    if pd.isna(row["atr"]) or row["atr"] <= 0:
        return
    position["highest_close"] = max(position["highest_close"], float(row["close"]))
    if not position["trail_active"] and row["close"] >= position["entry_price"] + row["atr"]:
        position["trail_active"] = True
    if position["trail_active"]:
        next_stop = position["highest_close"] - (1.5 * float(row["atr"]))
        position["trailing_stop"] = max(position.get("trailing_stop") or next_stop, next_stop)


def macd_crossed_below(frame: pd.DataFrame, index: int) -> bool:
    if index <= 0:
        return False
    prior = frame.iloc[index - 1]
    row = frame.iloc[index]
    return prior["macd_line"] >= prior["macd_signal"] and row["macd_line"] < row["macd_signal"]
