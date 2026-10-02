$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$out = Join-Path $repo "reports\research-campaigns\btcusdt-1h-rs-benchmark-refine-20260910-stdout.log"
$err = Join-Path $repo "reports\research-campaigns\btcusdt-1h-rs-benchmark-refine-20260910-stderr.log"

npm run research:rs-nearby -- --mode benchmark-1h-refine --timeframe 1h --days 1095 --top 80 --max-screen 0 --tag 20260910-benchmark-1h-refine 1> $out 2> $err
