$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $repo "config\local\flow-collector-runtime.json"
$nodePath = (Get-Command node -ErrorAction Stop).Source
if (Test-Path -LiteralPath $runtimePath) {
    $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $runtime.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $runtime.nodePath -and
        $existing.StartTime.ToUniversalTime().Ticks -eq ([datetime]$runtime.startedAt).ToUniversalTime().Ticks) {
        Write-Output "Flow collector already running: $($runtime.pid)"
        exit 0
    }
}
$stamp = Get-Date -Format "yyyyMMdd"
$process = Start-Process -FilePath $nodePath -ArgumentList @(
    "scripts/collect_trade_flow.js", "reports/market-flow", "168"
) -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $repo "reports\market-flow\collector-$stamp-out.log") `
    -RedirectStandardError (Join-Path $repo "reports\market-flow\collector-$stamp-err.log")
[ordered]@{ pid = $process.Id; nodePath = $nodePath; root = $repo;
    startedAt = $process.StartTime.ToUniversalTime().ToString("o") } |
    ConvertTo-Json | Set-Content -LiteralPath $runtimePath
Write-Output "Started hidden flow collector: $($process.Id)"
