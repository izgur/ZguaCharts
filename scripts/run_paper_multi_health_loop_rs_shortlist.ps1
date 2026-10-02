$ErrorActionPreference = "Continue"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$iterations = 2016
$sleepSeconds = 600

for ($i = 1; $i -le $iterations; $i += 1) {
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\ensure_paper_multi_watch_rs_shortlist.ps1 -MaxLogAgeMinutes 15
  Start-Sleep -Seconds $sleepSeconds
}
