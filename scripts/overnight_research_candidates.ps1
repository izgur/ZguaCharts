Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogDir = Join-Path $Root "reports\overnight-research\$Stamp"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

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
        throw "Step '$Name' failed with exit code $LASTEXITCODE. See $LogPath"
    }
}

Write-Host "Overnight research run started at $(Get-Date -Format o)"
Write-Host "Logs: $LogDir"
Write-Host "Safety: research-only commands; no paper tick, no catch-up, no live orders."

Run-ResearchStep "coverage_bnb_eth_1h_730d" @(
    "scripts\research_autopilot.py", "research:history-coverage",
    "--symbols", "BNBUSDT,ETHUSDT",
    "--timeframes", "1h",
    "--period", "730d",
    "--limit", "period",
    "--timeout-seconds", "1200"
)

Run-ResearchStep "optimizer_bnb_eth_1h_730d" @(
    "scripts\research_autopilot.py", "research:focused-optimizer",
    "--symbols", "BNBUSDT,ETHUSDT",
    "--timeframes", "1h",
    "--strategies", "SimpleAtrTrendV2,RelativeStrengthV2,BreakoutRetestV2,RangeExpansionV2",
    "--period", "730d",
    "--limit", "period",
    "--max-candidates", "8",
    "--max-combos-per-strategy", "60",
    "--top-n", "20",
    "--timeout-seconds", "1800",
    "--runtime-budget-seconds", "1740"
)

$LeadReviews = @(
    @{ Name = "lead_bnb_breakout_1h_730d"; Strategy = "BreakoutRetestV2"; Symbol = "BNBUSDT" },
    @{ Name = "lead_bnb_simpleatr_1h_730d"; Strategy = "SimpleAtrTrendV2"; Symbol = "BNBUSDT" },
    @{ Name = "lead_eth_breakout_1h_730d"; Strategy = "BreakoutRetestV2"; Symbol = "ETHUSDT" },
    @{ Name = "lead_eth_relative_1h_730d"; Strategy = "RelativeStrengthV2"; Symbol = "ETHUSDT" },
    @{ Name = "lead_bnb_range_1h_730d"; Strategy = "RangeExpansionV2"; Symbol = "BNBUSDT" }
)

foreach ($Lead in $LeadReviews) {
    Run-ResearchStep $Lead.Name @(
        "scripts\research_autopilot.py", "research:lead-review",
        "--strategy", $Lead.Strategy,
        "--symbol", $Lead.Symbol,
        "--timeframe", "1h",
        "--period", "730d",
        "--limit", "period",
        "--max-combos-per-strategy", "80",
        "--top-n", "10",
        "--save",
        "--timeout-seconds", "1800",
        "--review-timeout-seconds", "1800"
    )
}

Write-Host ""
Write-Host "Overnight research run finished at $(Get-Date -Format o)"
Write-Host "Review logs in: $LogDir"
Write-Host "Saved lead-review JSON reports, if any, are under reports\research-leads."
