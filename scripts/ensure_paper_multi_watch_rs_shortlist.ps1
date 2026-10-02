param(
  [int]$MaxLogAgeMinutes = 15
)

$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$today = Get-Date -Format "yyyyMMdd"
$healthDir = Join-Path $repo "reports\paper-multi"
$healthLog = Join-Path $healthDir "watchdog-rs-shortlist-health-$today.log"
New-Item -ItemType Directory -Path $healthDir -Force | Out-Null

function Write-Health($message) {
  $line = "$(Get-Date -Format o) $message"
  Add-Content -Path $healthLog -Value $line
  Write-Host $line
}

$isFresh = $false
$latestLog = Get-ChildItem -Path $healthDir -Filter "watchdog-rs-shortlist-*.jsonl" -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -notlike "*smoke*" } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($latestLog) {
  $age = (Get-Date) - $latestLog.LastWriteTime
  $isFresh = $age.TotalMinutes -le $MaxLogAgeMinutes
  Write-Health "log=$($latestLog.FullName) ageMinutes=$([Math]::Round($age.TotalMinutes, 2)) fresh=$isFresh"
} else {
  Write-Health "shortlist watcher log missing"
}

if ($isFresh) {
  exit 0
}

Write-Health "starting shortlist paper watcher"
& (Join-Path $PSScriptRoot "start-paper-watch.ps1")
