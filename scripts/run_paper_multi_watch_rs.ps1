$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$jsonl = "reports\paper-multi\watchdog-rs-13candidates-20260911.jsonl"
$out = Join-Path $repo "reports\paper-multi\watchdog-rs-13candidates-20260911-out.log"
$err = Join-Path $repo "reports\paper-multi\watchdog-rs-13candidates-20260911-err.log"

npm run paper:multi -- watch --iterations 2016 --interval-minutes 5 --log $jsonl 1> $out 2> $err
