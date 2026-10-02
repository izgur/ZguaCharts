param(
  [switch]$Smoke,
  [switch]$SkipPaperStatus
)

$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$logDir = Join-Path $repo "reports\week-research\$runId"
$campaignDir = Join-Path $repo "reports\research-campaigns"
$summaryPath = Join-Path $campaignDir "week-research-summary-$runId.md"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path $campaignDir -Force | Out-Null

function Run-Step {
  param(
    [string]$Name,
    [string[]]$Arguments
  )

  $started = Get-Date
  $out = Join-Path $logDir "$Name.out.log"
  $err = Join-Path $logDir "$Name.err.log"
  $meta = Join-Path $logDir "$Name.meta.json"
  $commandLine = "npm " + ($Arguments -join " ")

  Write-Host "[$($started.ToString('s'))] START $Name"
  Write-Host "  $commandLine"

  Push-Location $repo
  & npm.cmd @Arguments 1> $out 2> $err
  $exitCode = $LASTEXITCODE
  Pop-Location
  $finished = Get-Date
  $payload = [ordered]@{
    name = $Name
    command = $commandLine
    startedAt = $started.ToUniversalTime().ToString("o")
    finishedAt = $finished.ToUniversalTime().ToString("o")
    exitCode = $exitCode
    durationMinutes = [Math]::Round(($finished - $started).TotalMinutes, 2)
    stdout = $out
    stderr = $err
  }
  $payload | ConvertTo-Json -Depth 6 | Set-Content -Path $meta

  Write-Host "[$($finished.ToString('s'))] END $Name exit=$exitCode"

  npm run research:week-summary -- --output $summaryPath 1>> (Join-Path $logDir "summary.out.log") 2>> (Join-Path $logDir "summary.err.log")
}

function Add-ResearchJob {
  param(
    [System.Collections.Generic.List[object]]$Jobs,
    [string]$Name,
    [string]$Timeframe,
    [string]$Mode,
    [int]$Days,
    [int]$Top,
    [int]$MaxScreen
  )

  $tag = "$runId-$Name"
  $args = @(
    "run", "research:rs-nearby", "--",
    "--timeframe", $Timeframe,
    "--days", [string]$Days,
    "--mode", $Mode,
    "--max-screen", [string]$MaxScreen,
    "--top", [string]$Top,
    "--tag", $tag,
    "--output", "reports\research-campaigns"
  )
  $Jobs.Add([pscustomobject]@{ Name = $Name; Arguments = $args }) | Out-Null
}

$jobs = New-Object 'System.Collections.Generic.List[object]'

if ($Smoke) {
  Add-ResearchJob -Jobs $jobs -Name "smoke-1h-refine" -Timeframe "1h" -Mode "benchmark-1h-refine" -Days 60 -Top 3 -MaxScreen 12
  Add-ResearchJob -Jobs $jobs -Name "smoke-15m-entry" -Timeframe "15m" -Mode "benchmark-15m-entry" -Days 30 -Top 3 -MaxScreen 12
} else {
  Add-ResearchJob -Jobs $jobs -Name "01-4h-benchmark-tight-1460d" -Timeframe "4h" -Mode "benchmark-tight" -Days 1460 -Top 80 -MaxScreen 0
  Add-ResearchJob -Jobs $jobs -Name "02-1h-refine-1095d" -Timeframe "1h" -Mode "benchmark-1h-refine" -Days 1095 -Top 80 -MaxScreen 0
  Add-ResearchJob -Jobs $jobs -Name "03-1h-tight-1095d" -Timeframe "1h" -Mode "benchmark-1h-tight" -Days 1095 -Top 80 -MaxScreen 0
  Add-ResearchJob -Jobs $jobs -Name "04-15m-refine-365d" -Timeframe "15m" -Mode "benchmark-15m-refine" -Days 365 -Top 100 -MaxScreen 0
  Add-ResearchJob -Jobs $jobs -Name "05-15m-entry-365d" -Timeframe "15m" -Mode "benchmark-15m-entry" -Days 365 -Top 100 -MaxScreen 0
  Add-ResearchJob -Jobs $jobs -Name "06-15m-refine-520d" -Timeframe "15m" -Mode "benchmark-15m-refine" -Days 520 -Top 100 -MaxScreen 0
  Add-ResearchJob -Jobs $jobs -Name "07-15m-entry-520d" -Timeframe "15m" -Mode "benchmark-15m-entry" -Days 520 -Top 100 -MaxScreen 0
}

$manifest = [ordered]@{
  runId = $runId
  smoke = [bool]$Smoke
  startedAt = (Get-Date).ToUniversalTime().ToString("o")
  repo = $repo
  logDir = $logDir
  summaryPath = $summaryPath
  jobs = $jobs
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Path (Join-Path $logDir "manifest.json")

if (-not $SkipPaperStatus) {
  Run-Step -Name "00-paper-status" -Arguments @("run", "paper:multi", "--", "status", "--force-exit")
}

foreach ($job in $jobs) {
  Run-Step -Name $job.Name -Arguments $job.Arguments
}

Run-Step -Name "99-final-summary" -Arguments @("run", "research:week-summary", "--", "--output", $summaryPath)

$done = [ordered]@{
  runId = $runId
  completedAt = (Get-Date).ToUniversalTime().ToString("o")
  logDir = $logDir
  summaryPath = $summaryPath
}
$done | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $logDir "completed.json")

Write-Host "Week research queue finished. Summary: $summaryPath"
