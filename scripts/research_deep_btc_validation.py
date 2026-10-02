import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPORT_DIR = Path("reports") / "research-campaigns" / "deep-btc-validation"
STOP_FILE = Path("reports") / "research-campaigns" / "DEEP_VALIDATION_STOP"


EMA_CHAMPION_PARAMS = {
    "regimeMode": "looseBtcBull",
    "rsiPullbackLevel": 42,
    "rsiReclaimLevel": 50,
    "rsiMin": 35,
    "rsiMax": 65,
    "atrMultiplier": 2.3,
    "stopAtr": 2.5,
    "takeProfitAtr": 3,
    "trailingAtr": 1.5,
    "cooldownBars": 6,
    "minHoldBars": 1,
    "volumeFilter": False,
    "riskPct": 0.05,
    "indicatorFilterPreset": "none",
    "emaBounceAtr": 0.4,
}


EMA_NEIGHBORHOOD_GRID = {
    "regimeMode": ["looseBtcBull", "symbolTrend", "symbolFastTrend"],
    "rsiPullbackLevel": [35, 38, 40, 42, 45, 48],
    "rsiReclaimLevel": [48, 50, 52, 53, 55],
    "rsiMin": [30, 32, 35, 38, 42],
    "rsiMax": [60, 65, 68, 70, 75],
    "atrMultiplier": [1.8, 2.0, 2.3, 2.6, 2.8, 3.3],
    "stopAtr": [1.8, 2.0, 2.3, 2.5, 2.8, 3.0],
    "takeProfitAtr": [2.5, 3.0, 3.5, 4.0, 5.0],
    "trailingAtr": [1.2, 1.5, 1.8, 2.0, 2.5],
    "cooldownBars": [0, 2, 4, 6, 8, 12],
    "minHoldBars": [0, 1, 2, 3, 6],
    "volumeFilter": [False],
    "riskPct": [0.05],
    "indicatorFilterPreset": ["none", "pullbackTiming", "trendQuality"],
    "emaBounceAtr": [0.2, 0.4, 0.6, 0.8, 1.0, 1.2],
}


SQUEEZE_LOW_DD_LEAD_PARAMS = {
    "regimeMode": "noRegime",
    "squeezeMode": "squeezeOn",
    "squeezeLookback": 100,
    "squeezePercentile": 0.25,
    "pullbackEma": 50,
    "requireTrend": True,
    "requireMomentum": True,
    "minRsi": 50,
    "atrMultiplier": 3,
    "takeProfitAtr": 99,
    "maxHoldBars": 18,
    "cooldownBars": 2,
    "volumeFilter": False,
    "riskPct": 0.05,
    "fillModel": "next-open",
}


SQUEEZE_LOW_DD_GRID = {
    "regimeMode": ["noRegime", "symbolTrend", "looseBtcBull"],
    "squeezeMode": ["squeezeOn", "bbWidthLow"],
    "squeezeLookback": [60, 80, 100, 120],
    "squeezePercentile": [0.2, 0.25, 0.3, 0.35],
    "pullbackEma": [20, 50, 100],
    "requireTrend": [True, False],
    "requireMomentum": [True, False],
    "minRsi": [45, 48, 50, 52, 55],
    "atrMultiplier": [2.2, 2.6, 3.0, 3.4, 4.0],
    "takeProfitAtr": [6, 8, 12, 99],
    "maxHoldBars": [12, 18, 24, 30],
    "cooldownBars": [0, 1, 2, 4],
    "volumeFilter": [False],
    "riskPct": [0.05],
    "fillModel": ["next-open"],
}


BTC_REGIME_CONTROLLER_BASE_PARAMS = {
    "regimeMode": "symbolTrend",
    "controllerMode": "adaptive",
    "donchianEntry": 20,
    "donchianExit": 10,
    "minTrendAdx": 12,
    "trendRsiMin": 50,
    "rsiPullbackLevel": 42,
    "rsiReclaimLevel": 50,
    "rsiOversold": 38,
    "rsiReclaim": 45,
    "meanReversionLookback": 5,
    "maxMeanReversionAdx": 32,
    "minMeanReversionChoppiness": 45,
    "atrMultiplier": 2.3,
    "takeProfitAtr": 3.5,
    "maxHoldBars": 24,
    "cooldownBars": 2,
    "volumeFilter": False,
    "riskPct": 0.05,
    "fillModel": "next-open",
}


BTC_REGIME_CONTROLLER_GRID = {
    "regimeMode": ["symbolTrend", "looseBtcBull", "noRegime"],
    "controllerMode": ["adaptive", "trendOnly", "pullbackOnly", "meanReversionOnly"],
    "donchianEntry": [10, 20, 35, 55],
    "donchianExit": [5, 10, 20],
    "minTrendAdx": [0, 8, 12, 18],
    "trendRsiMin": [45, 50, 55],
    "rsiPullbackLevel": [38, 42, 45, 48],
    "rsiReclaimLevel": [48, 50, 53],
    "rsiOversold": [32, 35, 38, 42],
    "rsiReclaim": [42, 45, 48, 50],
    "meanReversionLookback": [3, 5, 8],
    "maxMeanReversionAdx": [24, 32, 45, 100],
    "minMeanReversionChoppiness": [0, 38, 45, 52],
    "atrMultiplier": [1.8, 2.3, 2.8, 3.3],
    "takeProfitAtr": [2.5, 3.5, 4.5, 99],
    "maxHoldBars": [12, 18, 24, 36],
    "cooldownBars": [0, 2, 4, 8],
    "volumeFilter": [False],
    "riskPct": [0.05],
    "fillModel": ["next-open"],
}


BREAKOUT_NEIGHBORHOOD_GRID = {
    "regimeMode": ["looseBtcBull", "symbolTrend", "symbolFastTrend"],
    "donchianEntry": [10, 15, 20, 35, 55, 80],
    "donchianExit": [5, 10, 15, 20],
    "adxThreshold": [0, 8, 12, 16, 20],
    "emaTrendLength": [20, 50, 100, 200],
    "atrMultiplier": [1.8, 2.3, 2.8, 3.3],
    "riskPct": [0.05],
    "retestLookback": [2, 3, 5, 8],
    "retestAtr": [0.5, 0.8, 1.2, 1.6],
    "volumeFilter": [False],
    "shortMode": [False],
}


MTF_15M_GRID = {
    "regimeMode": ["symbolFastTrend", "symbolTrend"],
    "mtfRequire1h": [True],
    "mtfRequire4h": [True, False],
    "mtf1hTrendEma": [50, 100],
    "mtf4hTrendEma": [50, 100, 200],
    "mtfMinRsi": [42, 45, 50],
    "mtfDirection": ["long", "both"],
    "mtfEntryMode": ["pullback", "either", "breakout"],
    "rsiPullbackLevel": [38, 42, 45, 48],
    "rsiReclaimLevel": [48, 50, 53, 56],
    "emaBounceAtr": [0.4, 0.8, 1.2],
    "atrMultiplier": [1.4, 1.8, 2.2, 2.8],
    "takeProfitAtr": [2.2, 2.8, 3.4, 4.0],
    "cooldownBars": [4, 8, 12, 16],
    "minHoldBars": [1, 2, 4, 8],
    "donchianEntry": [10, 20, 35],
    "donchianExit": [10, 20],
    "volumeFilter": [False],
    "indicatorFilterPreset": ["none", "pullbackTiming", "trendQuality"],
    "fillModel": ["next-open"],
}


PHASES = [
    {
        "name": "btc4h_regime_controller_1095d",
        "symbols": "BTCUSDT",
        "timeframes": "4h",
        "strategies": "BtcRegimeController4hV1",
        "period": "1095d",
        "max_candidates": 700,
        "max_combos": 800,
        "top_n": 120,
        "budget": 21600,
        "timeout": 22200,
        "base_params": BTC_REGIME_CONTROLLER_BASE_PARAMS,
        "grid": BTC_REGIME_CONTROLLER_GRID,
    },
    {
        "name": "btc4h_squeeze_low_dd_validation_1095d",
        "symbols": "BTCUSDT",
        "timeframes": "4h",
        "strategies": "SqueezePullbackHold4hV1",
        "period": "1095d",
        "max_candidates": 700,
        "max_combos": 900,
        "top_n": 120,
        "budget": 21600,
        "timeout": 22200,
        "base_params": SQUEEZE_LOW_DD_LEAD_PARAMS,
        "grid": SQUEEZE_LOW_DD_GRID,
    },
    {
        "name": "btc4h_ema_champion_neighborhood_1095d",
        "symbols": "BTCUSDT",
        "timeframes": "4h",
        "strategies": "EmaBounceV2",
        "period": "1095d",
        "max_candidates": 600,
        "max_combos": 800,
        "top_n": 100,
        "budget": 21600,
        "timeout": 22200,
        "base_params": EMA_CHAMPION_PARAMS,
        "grid": EMA_NEIGHBORHOOD_GRID,
    },
    {
        "name": "btc4h_challengers_1095d",
        "symbols": "BTCUSDT",
        "timeframes": "4h",
        "strategies": "BreakoutRetestV2,RegimeDonchianCloseConfirm,RegimeDonchian20,RangeExpansionV2,RelativeStrengthV2,PullbackReclaimV2",
        "period": "1095d",
        "max_candidates": 500,
        "max_combos": 700,
        "top_n": 100,
        "budget": 28800,
        "timeout": 29400,
        "grid": BREAKOUT_NEIGHBORHOOD_GRID,
    },
    {
        "name": "btc1h_scout_1095d",
        "symbols": "BTCUSDT",
        "timeframes": "1h",
        "strategies": "EmaBounceV2,PullbackReclaimV2,BreakoutRetestV2,RangeExpansionV2,RelativeStrengthV2",
        "period": "1095d",
        "max_candidates": 500,
        "max_combos": 700,
        "top_n": 100,
        "budget": 21600,
        "timeout": 22200,
        "grid": EMA_NEIGHBORHOOD_GRID,
    },
    {
        "name": "btc15m_mtf_scout_365d",
        "symbols": "BTCUSDT",
        "timeframes": "15m",
        "strategies": "MtfPullback15mV1,FibPullbackContinuationV1",
        "period": "365d",
        "max_candidates": 500,
        "max_combos": 700,
        "top_n": 100,
        "budget": 21600,
        "timeout": 22200,
        "grid": MTF_15M_GRID,
    },
]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def phase_command(phase):
    command = [
        sys.executable,
        "scripts\\research_autopilot.py",
        "research:focused-optimizer",
        "--symbols",
        phase["symbols"],
        "--timeframes",
        phase["timeframes"],
        "--strategies",
        phase["strategies"],
        "--period",
        phase["period"],
        "--limit",
        "period",
        "--max-candidates",
        str(phase["max_candidates"]),
        "--max-combos-per-strategy",
        str(phase["max_combos"]),
        "--top-n",
        str(phase["top_n"]),
        "--repro-top-n",
        "12",
        "--repro-reruns",
        "3",
        "--timeout-seconds",
        str(phase["timeout"]),
        "--runtime-budget-seconds",
        str(phase["budget"]),
        "--json",
    ]
    if phase.get("base_params"):
        command.extend(["--optimizer-base-params", json.dumps(phase["base_params"], separators=(",", ":"))])
    if phase.get("grid"):
        command.extend(["--optimizer-grid-overrides", json.dumps(phase["grid"], separators=(",", ":"))])
    return command


def run_phase(run_dir, phase, dry_run=False):
    log_path = run_dir / ("%s.log" % phase["name"])
    command = phase_command(phase)
    record = {
        "name": phase["name"],
        "startedAt": utc_now(),
        "command": command,
        "logPath": str(log_path).replace("\\", "/"),
        "status": "DRY_RUN" if dry_run else "RUNNING",
    }
    if dry_run:
        record["finishedAt"] = utc_now()
        record["returnCode"] = None
        return record

    with log_path.open("w", encoding="utf-8") as log:
        log.write("Started %s\n" % record["startedAt"])
        log.write("Command: %s\n\n" % " ".join(command))
        log.flush()
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    record["finishedAt"] = utc_now()
    record["returnCode"] = completed.returncode
    record["status"] = "DONE" if completed.returncode == 0 else "FAILED"
    return record


def main():
    parser = argparse.ArgumentParser(description="Sequential deep BTC validation runner.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = REPORT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    selected = [phase for phase in PHASES if not args.only or phase["name"] in args.only.split(",")]
    state = {
        "runId": run_id,
        "startedAt": utc_now(),
        "stopFile": str(STOP_FILE).replace("\\", "/"),
        "phases": [],
        "status": "RUNNING",
    }
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    for phase in selected:
        if STOP_FILE.exists():
            state["status"] = "STOPPED"
            state["stoppedBefore"] = phase["name"]
            break
        record = run_phase(run_dir, phase, dry_run=args.dry_run)
        state["phases"].append(record)
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if record["status"] == "FAILED":
            state["status"] = "FAILED"
            break

    if state["status"] == "RUNNING":
        state["status"] = "DONE"
    state["finishedAt"] = utc_now()
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state, indent=2))
    return 0 if state["status"] in ("DONE", "STOPPED") else 1


if __name__ == "__main__":
    sys.exit(main())
