param([switch]$Smoke)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$campaign = Join-Path $repo "reports\research-campaigns\fast-search-20260920"
New-Item -ItemType Directory -Path $campaign -Force | Out-Null
$progress = Join-Path $campaign "progress.jsonl"

if ($Smoke) {
    $jobs = @(@{ Symbol = "BTCUSDT"; Timeframe = "15m"; Days = 30; MaxVariants = 2 })
} else {
    $jobs = @(
        @{ Symbol = "BTCUSDT"; Timeframe = "15m"; Days = 365 },
        @{ Symbol = "ETHUSDT"; Timeframe = "15m"; Days = 365 },
        @{ Symbol = "SOLUSDT"; Timeframe = "15m"; Days = 365 },
        @{ Symbol = "BTCUSDT"; Timeframe = "5m"; Days = 180 },
        @{ Symbol = "ETHUSDT"; Timeframe = "5m"; Days = 180 },
        @{ Symbol = "SOLUSDT"; Timeframe = "5m"; Days = 180 },
        @{ Symbol = "BTCUSDT"; Timeframe = "3m"; Days = 120 },
        @{ Symbol = "ETHUSDT"; Timeframe = "3m"; Days = 120 },
        @{ Symbol = "SOLUSDT"; Timeframe = "3m"; Days = 120 }
    )
}

foreach ($job in $jobs) {
    $name = "$($job.Symbol)-$($job.Timeframe)-$($job.Days)d"
    if ($Smoke) { $name = "smoke-$name" }
    $report = Join-Path $campaign "$name.json"
    if ((Test-Path $report) -and -not $Smoke) {
        try {
            $existing = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
            if ($existing.complete -eq $true) { continue }
        } catch { }
    }
    $args = @("scripts\search_fast_strategies.js", "--symbol", $job.Symbol, "--timeframe", $job.Timeframe,
        "--days", [string]$job.Days, "--output", $report)
    if ($job.MaxVariants) { $args += @("--max-variants", [string]$job.MaxVariants) }
    $started = (Get-Date).ToUniversalTime().ToString("o")
    Write-Host "Starting $name"
    $proc = Start-Process -FilePath node -ArgumentList $args -WorkingDirectory $repo -WindowStyle Hidden -PassThru -Wait `
        -RedirectStandardOutput (Join-Path $campaign "$name-out.log") `
        -RedirectStandardError (Join-Path $campaign "$name-err.log")
    $exitCode = $proc.ExitCode
    [ordered]@{ name = $name; startedAt = $started; finishedAt = (Get-Date).ToUniversalTime().ToString("o"); exitCode = $exitCode; report = $report } |
        ConvertTo-Json -Compress | Add-Content -LiteralPath $progress
    Write-Host "Finished $name exit=$exitCode"
}
