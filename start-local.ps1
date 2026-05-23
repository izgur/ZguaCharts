$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  $Python = "python"
}
$Pythonw = $Python
if ($Python -eq "python") {
  $KnownPythonw = Join-Path $env:LOCALAPPDATA "Programs\Python\Python37-32\pythonw.exe"
  if (Test-Path $KnownPythonw) {
    $Pythonw = $KnownPythonw
  }
} else {
  $CandidatePythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
  if (Test-Path $CandidatePythonw) {
    $Pythonw = $CandidatePythonw
  }
}

$OutLog = Join-Path $Root "server.out.log"
$ErrLog = Join-Path $Root "server.err.log"
$PidFile = Join-Path $Root "server.pid"

Write-Host "Starting ZguaCharts from $Root"
Write-Host "Logs:"
Write-Host "  $OutLog"
Write-Host "  $ErrLog"

if (Test-Path $PidFile) {
  $OldPid = Get-Content $PidFile -ErrorAction SilentlyContinue
  if ($OldPid) {
    $OldProcess = Get-Process -Id ([int]$OldPid) -ErrorAction SilentlyContinue
    if ($OldProcess) {
      Write-Host "Stopping existing ZguaCharts process $OldPid"
      Stop-Process -Id ([int]$OldPid) -Force
      Start-Sleep -Seconds 1
    }
  }
}

$CommandLine = '"' + $Pythonw + '" "' + (Join-Path $Root "run_server.py") + '"'
try {
  $process = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $CommandLine
    CurrentDirectory = $Root
  }
  if ($process.ReturnValue -ne 0) {
    throw "Win32_Process returned $($process.ReturnValue)."
  }
  $process.ProcessId | Set-Content $PidFile
  Write-Host "Started process $($process.ProcessId)"
} catch {
  Write-Host "CIM launch unavailable, falling back to Start-Process: $($_.Exception.Message)"
  $process = Start-Process `
    -FilePath $Pythonw `
    -ArgumentList "run_server.py" `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -PassThru
  $process.Id | Set-Content $PidFile
  Write-Host "Started process $($process.Id)"
}

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
