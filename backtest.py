from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from data_source import fetch_historical_candles
from indicators import candles_to_frame
from strategy import (
    DEFAULT_PRESET_ID,
    SKIPPED_REASON_KEYS,
    build_strategy_frame,
    dynamic_warmup,
    evaluate_entry,
    evaluate_exit,
    get_preset,
    initial_stop_price,
    preset_options,
    take_profit_price,
    update_trailing_stop,
)


def run_signal_backtest(
    source: str,
    symbol: str,
    timeframe: str,
    period: str = "60d",
    preset_id: str = DEFAULT_PRESET_ID,
    fee_pct: float = 0,
    slippage_pct: float = 0,
    limit: int = 5000,
    allow_shorts: bool = False,
    use_atr_exits: bool = True,
) -> dict:
    """Long-only signal-score backtest with diagnostics.

    Strategy rules live in strategy.py. This module handles historical candles,
    trade simulation, stats, and response formatting.
    """
    preset = get_preset(preset_id)
    candle_payload = fetch_historical_candles(source, symbol, timeframe, period=period, limit=limit)
    df = candles_to_frame(candle_payload["candles"])
    if df.empty or len(df) < 50:
        return empty_result(source, symbol, timeframe, period, preset_id, preset.name)

    frame = build_strategy_frame(df)
    warmup = dynamic_warmup(len(frame))
    skipped = {key: 0 for key in SKIPPED_REASON_KEYS}
    trades = []
    equity_curve = []
    equity = 1.0
    position = None
    cooldown_remaining = 0

    for index, row in frame.iterrows():
        if position is not None:
            update_trailing_stop(position, row, preset)
            exit_price, exit_reason = evaluate_exit(frame, index, position, preset)
            if exit_price is not None:
                equity, position = close_trade(
                    trades,
                    equity,
                    position,
                    row,
                    exit_price,
                    exit_reason,
                    fee_pct,
                    slippage_pct,
                )
                cooldown_remaining = preset.cooldown_bars

        if position is None:
            should_track_skip = row["score"] >= preset.entry_score
            should_enter, reasons, side = evaluate_entry(frame, index, preset, cooldown_remaining, warmup, allow_shorts=allow_shorts)
            if should_track_skip and not should_enter:
                for reason in reasons:
                    if reason in skipped:
                        skipped[reason] += 1
            if should_enter:
                position = open_position(frame, index, preset, side, use_atr_exits, fee_pct, slippage_pct)

        mark_to_market = equity
        if position is not None:
            mark_to_market = equity * (float(row["close"]) / position["entry_price"])
        equity_curve.append(mark_to_market)

        if cooldown_remaining > 0 and position is None:
            cooldown_remaining -= 1

    if position is not None:
        last = frame.iloc[-1]
        equity, position = close_trade(
            trades,
            equity,
            position,
            last,
            float(last["close"]),
            "End of data",
            fee_pct,
            slippage_pct,
        )

    diagnostics = build_diagnostics(
        df,
        frame,
        trades,
        period,
        candle_payload.get("effective_period", period),
        timeframe,
        warmup,
        source,
        fee_pct,
        slippage_pct,
        limit,
    )
    diagnostics["skipped_trade_reasons"] = skipped
    diagnostics["preset"] = preset.name
    diagnostics["preset_id"] = preset_id

    return {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "preset": preset.name,
        "preset_id": preset_id,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "limit": limit,
        "allow_shorts": allow_shorts,
        "total_return_pct": round((equity - 1) * 100, 2),
        "number_of_trades": len(trades),
        "win_rate": round(win_rate(trades), 2),
        "average_win": round(average_win(trades), 2),
        "average_loss": round(average_loss(trades), 2),
        "max_drawdown": round(max_drawdown(equity_curve) * 100, 2),
        "profit_factor": profit_factor(trades),
        "average_bars_held": round(average_bars_held(trades), 2),
        "trades": trades,
        "markers": markers_from_trades(trades),
        "diagnostics": diagnostics,
        "presets": preset_options(),
    }


def open_position(
    frame: pd.DataFrame,
    index: int,
    preset,
    side: str,
    use_atr_exits: bool,
    fee_pct: float,
    slippage_pct: float,
) -> dict:
    row = frame.iloc[index]
    entry_price = float(row["close"]) * (1 + slippage_pct / 100)
    return {
        "entry_index": index,
        "entry_time": int(row["time"]),
        "entry_price": entry_price,
        "entry_score": int(round(row["raw_score"])),
        "entry_smoothed_score": int(round(row["score"])),
        "side": side,
        "entry_fee_pct": fee_pct,
        "stop": initial_stop_price(frame, index, entry_price, preset) if use_atr_exits and row["atr"] > 0 else None,
        "take_profit": take_profit_price(frame, index, entry_price, preset) if use_atr_exits and row["atr"] > 0 else None,
        "highest_close": float(row["close"]),
        "trail_active": False,
        "trailing_stop": None,
    }


def close_trade(
    trades: list[dict],
    equity: float,
    position: dict,
    row: pd.Series,
    exit_price: float,
    exit_reason: str,
    fee_pct: float,
    slippage_pct: float,
) -> tuple[float, None]:
    adjusted_exit = exit_price * (1 - slippage_pct / 100)
    gross_return = (adjusted_exit - position["entry_price"]) / position["entry_price"]
    total_cost = (position["entry_fee_pct"] + fee_pct) / 100
    net_return = gross_return - total_cost
    equity *= 1 + net_return
    trades.append(
        {
            "entry_time": position["entry_time"],
            "exit_time": int(row["time"]),
            "entry_price": round(position["entry_price"], 6),
            "exit_price": round(adjusted_exit, 6),
            "entry_score": position["entry_score"],
            "entry_smoothed_score": position["entry_smoothed_score"],
            "exit_score": int(round(row["raw_score"])),
            "exit_smoothed_score": int(round(row["score"])),
            "return_pct": round(net_return * 100, 2),
            "bars_held": int(row.name - position["entry_index"]),
            "exit_reason": exit_reason,
        }
    )
    return equity, None


def build_diagnostics(
    df: pd.DataFrame,
    frame: pd.DataFrame,
    trades: list[dict],
    requested_period: str,
    effective_period: str,
    timeframe: str,
    warmup: int,
    source: str,
    fee_pct: float,
    slippage_pct: float,
    requested_limit: int,
) -> dict:
    first_time = int(df["time"].iloc[0])
    last_time = int(df["time"].iloc[-1])
    actual_days = max((last_time - first_time) / 86400, 0)
    warnings = period_warnings(source, requested_period, effective_period, actual_days, len(df), requested_limit)
    avg_atr_pct = frame["atr_pct"].dropna().mean() * 100 if "atr_pct" in frame else 0
    avg_volume = frame["volume"].dropna().mean() if "volume" in frame else 0
    trades_per_day = len(trades) / actual_days if actual_days > 0 else 0

    return {
        "first_candle_date": iso_time(first_time),
        "last_candle_date": iso_time(last_time),
        "number_of_candles_loaded": int(len(df)),
        "timeframe": timeframe,
        "requested_period": requested_period,
        "effective_period": effective_period,
        "requested_limit": requested_limit,
        "actual_days_returned": round(actual_days, 2),
        "warmup_candles_skipped": int(min(warmup, len(df))),
        "warmup_pct": round((min(warmup, len(df)) / len(df)) * 100, 2) if len(df) else 0,
        "average_atr_pct": round(float(avg_atr_pct) if not pd.isna(avg_atr_pct) else 0, 4),
        "average_volume": round(float(avg_volume) if not pd.isna(avg_volume) else 0, 2),
        "trades_per_day": round(trades_per_day, 4),
        "average_bars_held": round(average_bars_held(trades), 2),
        "fee_pct_per_side": fee_pct,
        "slippage_pct_per_side": slippage_pct,
        "raw_latest_score": round(float(frame["raw_score"].iloc[-1]), 2),
        "smoothed_latest_score": round(float(frame["score"].iloc[-1]), 2),
        "backtest_reliability": reliability_label(len(df)),
        "warnings": warnings,
    }


def period_warnings(source: str, requested_period: str, effective_period: str, actual_days: float, candle_count: int, requested_limit: int) -> list[str]:
    warnings = []
    requested_days = parse_period_days(requested_period)
    if requested_days and actual_days < requested_days * 0.85:
        warnings.append(f"Requested {requested_period}, but source returned about {actual_days:.1f} days.")
    if effective_period != requested_period:
        warnings.append(f"Using {effective_period} for this source/timeframe instead of requested {requested_period}.")
    if source == "yfinance" and requested_days and actual_days < requested_days * 0.85:
        warnings.append("yfinance intraday history is limited; fewer days may be available for this timeframe.")
    if candle_count < requested_limit:
        warnings.append(f"Requested up to {requested_limit} candles, but source returned {candle_count}.")
    if candle_count < 1000:
        warnings.append("Not enough data: fewer than 1000 candles loaded.")
    return warnings


def reliability_label(candle_count: int) -> str:
    if candle_count < 1000:
        return "LOW"
    if candle_count <= 5000:
        return "MEDIUM"
    return "HIGH"


def parse_period_days(period: str) -> Optional[float]:
    try:
        if period.endswith("d"):
            return float(period[:-1])
        if period.endswith("mo"):
            return float(period[:-2]) * 30
        if period.endswith("y"):
            return float(period[:-1]) * 365
    except ValueError:
        return None
    return None


def empty_result(source: str, symbol: str, timeframe: str, period: str, preset_id: str, preset_name: str) -> dict:
    return {
        "source": source,
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period,
        "preset": preset_name,
        "preset_id": preset_id,
        "fee_pct": 0,
        "slippage_pct": 0,
        "total_return_pct": 0,
        "number_of_trades": 0,
        "win_rate": 0,
        "average_win": 0,
        "average_loss": 0,
        "max_drawdown": 0,
        "profit_factor": 0,
        "average_bars_held": 0,
        "trades": [],
        "markers": [],
        "diagnostics": {},
        "presets": preset_options(),
    }


def win_rate(trades: list[dict]) -> float:
    if not trades:
        return 0
    wins = [trade for trade in trades if trade["return_pct"] > 0]
    return len(wins) / len(trades) * 100


def average_win(trades: list[dict]) -> float:
    wins = [trade["return_pct"] for trade in trades if trade["return_pct"] > 0]
    return sum(wins) / len(wins) if wins else 0


def average_loss(trades: list[dict]) -> float:
    losses = [trade["return_pct"] for trade in trades if trade["return_pct"] < 0]
    return sum(losses) / len(losses) if losses else 0


def average_bars_held(trades: list[dict]) -> float:
    if not trades:
        return 0
    return sum(trade["bars_held"] for trade in trades) / len(trades)


def max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0
    peak = equity_curve[0]
    worst = 0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak)
    return abs(worst)


def profit_factor(trades: list[dict]) -> float:
    gross_win = sum(trade["return_pct"] for trade in trades if trade["return_pct"] > 0)
    gross_loss = abs(sum(trade["return_pct"] for trade in trades if trade["return_pct"] < 0))
    if gross_loss == 0:
        return round(gross_win, 2) if gross_win else 0
    return round(gross_win / gross_loss, 2)


def markers_from_trades(trades: list[dict]) -> list[dict]:
    markers = []
    for trade in trades:
        markers.append(
            {
                "time": trade["entry_time"],
                "position": "belowBar",
                "color": "#12b886",
                "shape": "arrowUp",
                "text": "BT BUY",
            }
        )
        markers.append(
            {
                "time": trade["exit_time"],
                "position": "aboveBar",
                "color": "#ff5c7a",
                "shape": "arrowDown",
                "text": "BT SELL",
            }
        )
    return markers[-120:]


def iso_time(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
