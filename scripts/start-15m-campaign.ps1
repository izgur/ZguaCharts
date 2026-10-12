$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$campaign = "15m-broad-causal-20261002"
$runtimePath = Join-Path $repo "config/local/15m-campaign-runtime.json"
$pythonPath = (Get-Command python -ErrorAction Stop).Source
if (Test-Path -LiteralPath $runtimePath) {
    $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $runtime.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $runtime.pythonPath -and
        $existing.StartTime.ToUniversalTime().Ticks -eq ([datetime]$runtime.startedAt).ToUniversalTime().Ticks) {
        Write-Output "15m campaign already running: $($runtime.pid)"
        exit 0
    }
}
$logDir = Join-Path $repo "reports/research-campaigns"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outLog = Join-Path $logDir "$campaign-$stamp-out.log"
$errLog = Join-Path $logDir "$campaign-$stamp-err.log"
$process = Start-Process -FilePath $pythonPath -ArgumentList @(
    "-u", "scripts/research_campaign_resume.py", "--campaign", $campaign,
    "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT", "--timeframes", "15m",
    "--strategies", "SimpleAtrTrendV2,PullbackReclaimV2,BreakoutRetestV2,RangeExpansionV2,RelativeStrengthV2,FibPullbackContinuationV1,MomentumScalping,MeanReversion,MtfPullback15mV1,VwapReversion15mV1,VwapReversion15mFastV1",
    "--period", "730d", "--limit", "period", "--max-candidates", "600",
    "--max-combos-per-strategy", "800", "--top-n", "100",
    "--timeout-seconds", "172800", "--runtime-budget-seconds", "0", "--max-jobs", "55", "--clear-stop"
) -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
[ordered]@{ pid = $process.Id; pythonPath = $pythonPath; root = $repo;
    startedAt = $process.StartTime.ToUniversalTime().ToString("o"); campaign = $campaign;
    outputLog = $outLog; errorLog = $errLog } | ConvertTo-Json | Set-Content -LiteralPath $runtimePath
Write-Output "Started hidden 15m campaign: $($process.Id)"
Write-Output "Progress: reports/research-campaigns/$campaign.json"
