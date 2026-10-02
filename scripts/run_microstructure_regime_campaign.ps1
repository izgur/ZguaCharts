param(
  [int]$Days = 180,
  [string]$OutputDir = "reports/research-campaigns/microstructure-regime-20260921"
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$jobs = @()
foreach ($symbol in @("BTCUSDT", "ETHUSDT", "SOLUSDT")) {
  foreach ($timeframe in @("3m", "5m", "15m")) {
    $stem = "$symbol-$timeframe"
    $args = @("scripts/search_microstructure_regime_strategies.js", "--symbol", $symbol, "--timeframe", $timeframe, "--days", $Days, "--output", "$OutputDir/$stem.json")
    $jobs += Start-Process -FilePath "node" -ArgumentList $args -RedirectStandardOutput "$OutputDir/$stem.out.log" -RedirectStandardError "$OutputDir/$stem.err.log" -PassThru -WindowStyle Hidden
  }
}
$jobs | Select-Object Id, ProcessName, StartTime
