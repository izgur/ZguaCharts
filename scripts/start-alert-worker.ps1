$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $repo "config\local\alert-worker-runtime.json"
$pythonPath = (Get-Command python -ErrorAction Stop).Source
$mutex = New-Object System.Threading.Mutex($false, "Local\ZguaChartsAlertWorker")
try {
    if (-not $mutex.WaitOne(10000)) { throw "Alert launcher busy" }
    if (Test-Path -LiteralPath $runtimePath) {
        $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
        $existing = Get-Process -Id $runtime.pid -ErrorAction SilentlyContinue
        if ($existing -and $existing.Path -eq $runtime.pythonPath -and
            $existing.StartTime.ToUniversalTime().Ticks -eq ([datetime]$runtime.startedAt).ToUniversalTime().Ticks) {
            Write-Output "Alert worker already running: $($runtime.pid)"
            exit 0
        }
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $repo "config\local"), (Join-Path $repo "reports") | Out-Null
    $process = Start-Process -FilePath $pythonPath -ArgumentList @("scripts/run_alert_worker.py") -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $repo "reports\alert-worker-out.log") `
        -RedirectStandardError (Join-Path $repo "reports\alert-worker-err.log")
    [ordered]@{ pid = $process.Id; pythonPath = $pythonPath; root = $repo; startedAt = $process.StartTime.ToUniversalTime().ToString("o") } |
        ConvertTo-Json | Set-Content -LiteralPath $runtimePath
    Write-Output "Started hidden alert worker: $($process.Id)"
} finally {
    try { $mutex.ReleaseMutex() } catch { }
    $mutex.Dispose()
}
