$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  $Python = "python"
}

$OutLog = Join-Path $Root "server.out.log"
$ErrLog = Join-Path $Root "server.err.log"

Write-Host "Starting ZguaCharts from $Root"
Write-Host "Logs:"
Write-Host "  $OutLog"
Write-Host "  $ErrLog"

Start-Process `
  -FilePath $Python `
  -ArgumentList "app.py" `
  -WorkingDirectory $Root `
  -RedirectStandardOutput $OutLog `
  -RedirectStandardError $ErrLog `
  -WindowStyle Hidden

Start-Sleep -Seconds 3

try {
  $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5000/healthz" -TimeoutSec 10
  Write-Host "ZguaCharts is running: http://127.0.0.1:5000/charts"
  Write-Host $health.Content
} catch {
  Write-Host "ZguaCharts did not answer yet. Check server.err.log:"
  if (Test-Path $ErrLog) {
    Get-Content $ErrLog -Tail 40
  }
  throw
}
