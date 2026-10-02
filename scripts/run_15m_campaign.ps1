$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$campaign = "15m-broad-ordinary-20260823"
$logDir = Join-Path $root "reports\research-campaigns"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdout = Join-Path $logDir "$campaign-run.out.log"
$stderr = Join-Path $logDir "$campaign-run.err.log"
python scripts\research_campaign_resume.py --campaign $campaign --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT --timeframes 15m --strategies EmaBounceV2,BreakoutRetestV2,RangeExpansionV2,RelativeStrengthV2,FibPullbackContinuationV1,MomentumScalping,MeanReversion,SimpleAtrTrendV2 --period 730d --limit period --max-candidates 240 --max-combos-per-strategy 220 --top-n 50 --timeout-seconds 2100 --runtime-budget-seconds 1800 --max-jobs 40 --clear-stop --reset *> $stdout 2> $stderr
