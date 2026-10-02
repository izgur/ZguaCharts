$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

& (Join-Path $PSScriptRoot "start-paper-watch.ps1")
