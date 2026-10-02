$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$out = Join-Path $repo "reports\research-campaigns\btcusdt-1h-rs-benchmark-tight-20260909-stdout.log"
$err = Join-Path $repo "reports\research-campaigns\btcusdt-1h-rs-benchmark-tight-20260909-stderr.log"

npm run research:rs-nearby -- --mode benchmark-1h-tight --timeframe 1h --days 1095 --top 80 --max-screen 0 --tag 20260909-benchmark-1h-tight 1> $out 2> $err
