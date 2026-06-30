param(
    [string]$Period = "730d",
    [string]$Symbols = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT",
    [string]$Timeframes = "15m,1h,4h",
    [int]$MaxCombosPerStrategy = 60,
    [int]$TimeoutSeconds = 7200,
    [int]$RuntimeBudgetSeconds = 7000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir = Join-Path $Root "reports\overnight-research-all\$Stamp"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Failures = New-Object System.Collections.Generic.List[string]
$SymbolList = $Symbols.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
$TimeframeList = $Timeframes.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }

function Run-ResearchStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Args
    )
    $SafeName = $Name -replace '[^a-zA-Z0-9_.-]', '_'
    $LogPath = Join-Path $LogDir "$SafeName.log"
    Write-Host ""
    Write-Host "=== $Name ==="
    Write-Host "Log: $LogPath"
    Write-Host "python $($Args -join ' ')"
    & python @Args 2>&1 | Tee-Object -FilePath $LogPath
    if ($LASTEXITCODE -ne 0) {
        $message = "Step '$Name' failed with exit code $LASTEXITCODE. See $LogPath"
        Write-Warning $message
        $Failures.Add($message)
    }
}

Write-Host "All-combo overnight research started at $(Get-Date -Format o)"
Write-Host "Logs: $LogDir"
Write-Host "Symbols: $($SymbolList -join ',')"
Write-Host "Timeframes: $($TimeframeList -join ',')"
Write-Host "Period: $Period"
Write-Host "Strategies: auto = all optimizer-supported strategy families"
Write-Host "Safety: research-only commands; no paper tick, no catch-up, no live orders."

foreach ($Timeframe in $TimeframeList) {
    Run-ResearchStep "coverage_all_${Timeframe}_${Period}" @(
        "scripts\research_autopilot.py", "research:history-coverage",
        "--symbols", ($SymbolList -join ","),
        "--timeframes", $Timeframe,
        "--period", $Period,
        "--limit", "period",
        "--timeout-seconds", "$TimeoutSeconds"
    )
}

foreach ($Symbol in $SymbolList) {
    foreach ($Timeframe in $TimeframeList) {
        Run-ResearchStep "optimizer_${Symbol}_${Timeframe}_${Period}_auto" @(
            "scripts\research_autopilot.py", "research:focused-optimizer",
            "--symbols", $Symbol,
            "--timeframes", $Timeframe,
            "--strategies", "auto",
            "--period", $Period,
            "--limit", "period",
            "--max-candidates", "200",
            "--max-combos-per-strategy", "$MaxCombosPerStrategy",
            "--top-n", "30",
            "--no-walk-forward",
            "--no-stress",
            "--no-repro",
            "--no-save",
            "--timeout-seconds", "$TimeoutSeconds",
            "--runtime-budget-seconds", "$RuntimeBudgetSeconds"
        )
    }
}

Write-Host ""
Write-Host "All-combo overnight research finished at $(Get-Date -Format o)"
Write-Host "Logs: $LogDir"
if ($Failures.Count -gt 0) {
    Write-Host "Failures or partial failures:"
    foreach ($Failure in $Failures) {
        Write-Host "- $Failure"
    }
    exit 1
}
Write-Host "All steps completed without nonzero exit codes."
