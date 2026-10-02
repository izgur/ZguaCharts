$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$output = Join-Path $root "reports\research-campaigns\trade-flow-live-20h.json"
$log = Join-Path $root "reports\market-flow\analysis-20h.log"
$started = Get-Date
$deadline = $started.AddHours(20)

while ((Get-Date) -lt $deadline) {
    $stamp = (Get-Date).ToString("o")
    try {
        $result = & node scripts\analyze_trade_flow.js reports\market-flow $output 2>&1
        $exitCode = $LASTEXITCODE
        Add-Content -LiteralPath $log -Value "$stamp exitCode=$exitCode $($result -join ' ')"
    } catch {
        Add-Content -LiteralPath $log -Value "$stamp error=$($_.Exception.Message)"
    }
    $remaining = ($deadline - (Get-Date)).TotalSeconds
    if ($remaining -gt 0) {
        Start-Sleep -Seconds ([math]::Min(1800, [math]::Ceiling($remaining)))
    }
}

& node scripts\analyze_trade_flow.js reports\market-flow $output *> $null
Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) finished exitCode=$LASTEXITCODE"
