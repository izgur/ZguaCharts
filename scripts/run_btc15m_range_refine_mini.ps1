$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

node scripts\range_expansion_refine_btc15m.js `
  --squeezeLookback "60" `
  --squeezePercentile "0.45,0.5,0.55" `
  --rangeLookback "20" `
  --rangeSma "25,30,35" `
  --closeHighPct "0.5,0.55,0.6,0.65" `
  --atrMultiplier "1.8,2,2.2" `
  --output "reports\research-campaigns\btc15m-range-refine-mini-20260824.json" `
  *> "reports\research-campaigns\btc15m-range-refine-mini-20260824.out.log" `
  2> "reports\research-campaigns\btc15m-range-refine-mini-20260824.err.log"
