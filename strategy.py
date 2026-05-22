from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from indicators import atr, bollinger_bands, ema, macd, rsi, sma, supertrend, vwap
from signals import BUY_THRESHOLD, SELL_THRESHOLD, build_features, score_row


@dataclass(frozen=True)
class StrategyPreset:
    id: str
    name: str
    intended_timeframes: str
    entry_score: int
    confirmation_bars: int
    cooldown_bars: int
    min_hold_bars: int
    initial_stop_atr: float
    take_profit_atr: float
    trailing_activation_atr: float
    trailing_stop_atr: float
    chasing_atr: float
    use_original: bool = False


PRESETS = {
    "conservative_trend": StrategyPreset(
        id="conservative_trend",
        name="Conservative Trend",
        intended_timeframes="1h, 4h, 1d",
        entry_score=70,
        confirmation_bars=1,
        cooldown_bars=5,
        min_hold_bars=4,
        initial_stop_atr=2.5,
        take_profit_atr=4.0,
        trailing_activation_atr=1.5,
        trailing_stop_atr=2.0,
        chasing_atr=1.5,
    ),
    "momentum_scalping": StrategyPreset(
        id="momentum_scalping",
        name="Momentum Scalping",
        intended_timeframes="3m, 5m, 15m",
        entry_score=55,
        confirmation_bars=1,
        cooldown_bars=3,
        min_hold_bars=3,
        initial_stop_atr=1.8,
        take_profit_atr=2.5,
        trailing_activation_atr=1.0,
        trailing_stop_atr=1.3,
        chasing_atr=1.2,
    ),
    "pullback_trend": StrategyPreset(
        id="pullback_trend",
        name="Pullback Trend",
        intended_timeframes="15m, 1h, 4h",
        entry_score=50,
        confirmation_bars=1,
        cooldown_bars=5,
        min_hold_bars=4,
        initial_stop_atr=2.0,
        take_profit_atr=3.0,
        trailing_activation_atr=1.5,
        trailing_stop_atr=2.0,
        chasing_atr=0.5,
    ),
    "mean_reversion": StrategyPreset(
        id="mean_reversion",
        name="Mean Reversion",
        intended_timeframes="5m, 15m, 1h",
        entry_score=0,
        confirmation_bars=1,
        cooldown_bars=5,
        min_hold_bars=2,
        initial_stop_atr=1.5,
        take_profit_atr=2.0,
        trailing_activation_atr=99.0,
        trailing_stop_atr=99.0,
        chasing_atr=99.0,
    ),
    "original": StrategyPreset(
        id="original",
        name="Current Original Strategy",
        intended_timeframes="Any",
        entry_score=BUY_THRESHOLD,
        confirmation_bars=1,
        cooldown_bars=0,
        min_hold_bars=0,
        initial_stop_atr=1.5,
        take_profit_atr=3.0,
        trailing_activation_atr=99.0,
        trailing_stop_atr=99.0,
        chasing_atr=99.0,
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
    "chasing_filter_failed",
    "pullback_filter_failed",
    "candle_confirmation_failed",
    "bollinger_filter_failed",
]


def preset_options() -> list[dict]:
    return [
        {"id": preset_id, "label": preset.name, "intended_timeframes": preset.intended_timeframes}
        for preset_id, preset in PRESETS.items()
    ]


def get_preset(preset_id: Optional[str]) -> StrategyPreset:
    return PRESETS.get(preset_id or DEFAULT_PRESET_ID, PRESETS[DEFAULT_PRESET_ID])


def dynamic_warmup(candle_count: int) -> int:
    return int(min(250, max(50, np.floor(candle_count * 0.2))))


def build_strategy_frame(df: pd.DataFrame) -> pd.DataFrame:
    features = build_features(df)
    macd_line, macd_signal, macd_histogram = macd(df["close"], 12, 26, 9)
    bb_upper, bb_middle, bb_lower = bollinger_bands(df["close"], 20, 2)
    ema21_value = ema(df["close"], 21)
    ema50_value = ema(df["close"], 50)
    ema200_value = ema(df["close"], 200)
    atr_value = atr(df, 14)
    raw_score = features.apply(score_row, axis=1).clip(-100, 100)

    frame = features.copy()
    frame["time"] = df["time"]
    frame["open"] = df["open"]
    frame["high"] = df["high"]
    frame["low"] = df["low"]
    frame["close"] = df["close"]
    frame["volume"] = df["volume"]
    frame["raw_score"] = raw_score
    frame["score"] = raw_score.ewm(span=3, adjust=False).mean()
    frame["ema21_value"] = ema21_value
    frame["ema50_value"] = ema50_value
    frame["ema200_value"] = ema200_value
    frame["vwap_value"] = vwap(df)
    frame["macd_line"] = macd_line
    frame["macd_signal"] = macd_signal
    frame["macd_histogram"] = macd_histogram
    frame["atr"] = atr_value
    frame["bb_upper"] = bb_upper
    frame["bb_middle"] = bb_middle
    frame["bb_lower"] = bb_lower
    frame["supertrend_line"] = supertrend(df, 10, 3)
    frame["green_candle"] = frame["close"] > frame["open"]
    frame["ema200_slope"] = ema200_value.diff(20)
    return frame


def evaluate_entry(
    frame: pd.DataFrame,
    index: int,
    preset: StrategyPreset,
    cooldown_remaining: int,
    warmup: int,
    allow_shorts: bool = False,
) -> tuple[bool, list[str], str]:
    reasons = common_entry_reasons(frame, index, preset, cooldown_remaining, warmup)
    if preset.use_original:
        prior_score = frame["score"].iloc[index - 1] if index > 0 else np.nan
        entered = not pd.isna(prior_score) and prior_score <= preset.entry_score < frame["score"].iloc[index]
        return entered and not reasons, reasons, "long"

    if preset.id == "conservative_trend":
        reasons.extend(conservative_reasons(frame, index, preset))
    elif preset.id == "momentum_scalping":
        reasons.extend(momentum_reasons(frame, index, preset))
    elif preset.id == "pullback_trend":
        reasons.extend(pullback_reasons(frame, index, preset))
    elif preset.id == "mean_reversion":
        reasons.extend(mean_reversion_reasons(frame, index))

    if allow_shorts:
        # Mirrored short-side support is intentionally not active by default.
        # The backtester can call this later once short rules are validated.
        pass

    return len(reasons) == 0, reasons, "long"


def common_entry_reasons(
    frame: pd.DataFrame,
    index: int,
    preset: StrategyPreset,
    cooldown_remaining: int,
    warmup: int,
) -> list[str]:
    reasons = []
    if index < warmup:
        reasons.append("warmup_active")
    if cooldown_remaining > 0:
        reasons.append("cooldown_active")
    if not score_confirmed(frame, index, preset.entry_score, preset.confirmation_bars):
        reasons.append("confirmation_missing")
    return reasons


def conservative_reasons(frame: pd.DataFrame, index: int, preset: StrategyPreset) -> list[str]:
    row = frame.iloc[index]
    reasons = []
    if not (row["close"] > row["ema200_value"] and row["ema50_value"] > row["ema200_value"] and row["close"] >= row["supertrend_line"]):
        reasons.append("trend_filter_failed")
    if not (row["macd_histogram"] > 0):
        reasons.append("confirmation_missing")
    if not (45 <= row["rsi"] <= 68):
        reasons.append("rsi_filter_failed")
    if too_far_above_ema21(row, preset.chasing_atr):
        reasons.append("chasing_filter_failed")
    return reasons


def momentum_reasons(frame: pd.DataFrame, index: int, preset: StrategyPreset) -> list[str]:
    row = frame.iloc[index]
    reasons = []
    if not (row["close"] > row["ema50_value"] and row["ema_momentum"] > 0):
        reasons.append("trend_filter_failed")
    if not (row["macd_line"] > row["macd_signal"]):
        reasons.append("confirmation_missing")
    if not (48 <= row["rsi"] <= 72):
        reasons.append("rsi_filter_failed")
    if not pd.isna(row["vwap_value"]) and not (row["close"] > row["vwap_value"]):
        reasons.append("vwap_filter_failed")
    if too_far_above_ema21(row, preset.chasing_atr):
        reasons.append("chasing_filter_failed")
    return reasons


def pullback_reasons(frame: pd.DataFrame, index: int, preset: StrategyPreset) -> list[str]:
    row = frame.iloc[index]
    prior = frame.iloc[index - 1] if index > 0 else row
    reasons = []
    if not (row["ema50_value"] > row["ema200_value"] and row["close"] > row["ema200_value"]):
        reasons.append("trend_filter_failed")
    if not (38 <= row["rsi"] <= 55):
        reasons.append("rsi_filter_failed")
    touched_ema21 = abs(row["close"] - row["ema21_value"]) <= 0.5 * row["atr"] or row["low"] <= row["ema21_value"] + 0.5 * row["atr"]
    touched_ema50 = abs(row["close"] - row["ema50_value"]) <= 0.5 * row["atr"] or row["low"] <= row["ema50_value"] + 0.5 * row["atr"]
    if not (touched_ema21 or touched_ema50):
        reasons.append("pullback_filter_failed")
    if not row["green_candle"]:
        reasons.append("candle_confirmation_failed")
    if not (row["macd_histogram"] > prior["macd_histogram"]):
        reasons.append("confirmation_missing")
    if too_far_above_ema21(row, preset.chasing_atr):
        reasons.append("chasing_filter_failed")
    return reasons


def mean_reversion_reasons(frame: pd.DataFrame, index: int) -> list[str]:
    row = frame.iloc[index]
    prior = frame.iloc[index - 1] if index > 0 else row
    reasons = []
    if not (prior["close"] < prior["bb_lower"] and row["close"] > row["bb_lower"]):
        reasons.append("bollinger_filter_failed")
    if not (row["rsi"] < 32):
        reasons.append("rsi_filter_failed")
    ema200_flat = abs(row["ema200_slope"]) <= row["atr"]
    if not (row["close"] > row["ema200_value"] or ema200_flat):
        reasons.append("trend_filter_failed")
    return reasons


def score_confirmed(frame: pd.DataFrame, index: int, threshold: float, bars: int) -> bool:
    if bars <= 1:
        return frame["score"].iloc[index] >= threshold
    start = index - bars + 1
    if start < 0:
        return False
    return bool((frame["score"].iloc[start : index + 1] >= threshold).all())


def too_far_above_ema21(row: pd.Series, atr_multiple: float) -> bool:
    if pd.isna(row["atr"]) or row["atr"] <= 0:
        return False
    return row["close"] > row["ema21_value"] + (atr_multiple * row["atr"])


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

    if preset.id == "conservative_trend":
        if row["close"] < row["ema50_value"]:
            return float(row["close"]), "Close below EMA50"
        if row["close"] < row["supertrend_line"]:
            return float(row["close"]), "Supertrend bearish"
    elif preset.id == "momentum_scalping":
        if bars_held >= preset.min_hold_bars and macd_crossed_below(frame, index):
            return float(row["close"]), "MACD crossed below signal"
        if bars_held >= preset.min_hold_bars and row["close"] < row["ema21_value"]:
            return float(row["close"]), "Close below EMA21"
    elif preset.id == "pullback_trend":
        if row["close"] < row["ema50_value"]:
            return float(row["close"]), "Close below EMA50"
    elif preset.id == "mean_reversion":
        if row["high"] >= row["bb_middle"]:
            return float(row["bb_middle"]), "Middle Bollinger Band reached"
    return None, None


def update_trailing_stop(position: dict, row: pd.Series, preset: StrategyPreset) -> None:
    if pd.isna(row["atr"]) or row["atr"] <= 0 or preset.trailing_activation_atr >= 90:
        return
    position["highest_close"] = max(position["highest_close"], float(row["close"]))
    if not position["trail_active"] and row["close"] >= position["entry_price"] + (preset.trailing_activation_atr * row["atr"]):
        position["trail_active"] = True
    if position["trail_active"]:
        next_stop = position["highest_close"] - (preset.trailing_stop_atr * float(row["atr"]))
        position["trailing_stop"] = max(position.get("trailing_stop") or next_stop, next_stop)


def initial_stop_price(frame: pd.DataFrame, index: int, entry_price: float, preset: StrategyPreset) -> float:
    row = frame.iloc[index]
    atr_stop = entry_price - (preset.initial_stop_atr * float(row["atr"]))
    if preset.id != "pullback_trend":
        return atr_stop
    start = max(0, index - 10)
    swing_low = float(frame["low"].iloc[start : index + 1].min())
    return min(atr_stop, swing_low)


def take_profit_price(frame: pd.DataFrame, index: int, entry_price: float, preset: StrategyPreset) -> float:
    row = frame.iloc[index]
    if preset.id == "mean_reversion":
        return min(float(row["bb_middle"]), entry_price + (preset.take_profit_atr * float(row["atr"])))
    return entry_price + (preset.take_profit_atr * float(row["atr"]))


def macd_crossed_below(frame: pd.DataFrame, index: int) -> bool:
    if index <= 0:
        return False
    prior = frame.iloc[index - 1]
    row = frame.iloc[index]
    return prior["macd_line"] >= prior["macd_signal"] and row["macd_line"] < row["macd_signal"]
