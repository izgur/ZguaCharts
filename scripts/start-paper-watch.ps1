$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $repo "config\local\paper-watcher-runtime.json"
$nodePath = (Get-Command node -ErrorAction Stop).Source
if (Test-Path -LiteralPath $runtimePath) {
    $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
    $existing = Get-Process -Id $runtime.pid -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $runtime.nodePath -and
        $existing.StartTime.ToUniversalTime().Ticks -eq ([datetime]$runtime.startedAt).ToUniversalTime().Ticks) {
        Write-Output "Paper watcher already running: $($runtime.pid)"
        exit 0
    }
}
$stamp = Get-Date -Format "yyyyMMdd"
$log = "reports/paper-multi/watchdog-rs-shortlist-$stamp.jsonl"
$process = Start-Process -FilePath $nodePath -ArgumentList @(
    "cli/paper_multi.js", "watch", "--iterations", "2016", "--interval-minutes", "5", "--log", $log
) -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $repo "reports\paper-multi\watcher-$stamp-out.log") `
    -RedirectStandardError (Join-Path $repo "reports\paper-multi\watcher-$stamp-err.log")
[ordered]@{ pid = $process.Id; nodePath = $nodePath; root = $repo;
    startedAt = $process.StartTime.ToUniversalTime().ToString("o"); log = $log } |
    ConvertTo-Json | Set-Content -LiteralPath $runtimePath
Write-Output "Started hidden paper watcher: $($process.Id)"
