param(
  [int]$MaxSteps = 12,
  [switch]$Execute
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Stop-WithMessage {
  param([string]$Message)
  Write-Host $Message
  exit 1
}

function Invoke-PythonText {
  param([string[]]$Arguments)
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $text = (& python @Arguments 2>&1 | Out-String)
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  return @{
    Text = $text
    Code = $code
  }
}

function Assert-SafeNextAction {
  param([string]$Text)
  if ($Text -notmatch "Safety:\s+realTradingEnabled=False\s+liveOrdersTouched=False\s+schedulerEnabled=False\s+autoTickEnabled=False") {
    Stop-WithMessage "Stopped: next-action safety flags are not all false."
  }
  $blocker = [regex]::Match($Text, "(?m)^Blockers:\s*(.+)$")
  if ($blocker.Success -and $blocker.Groups[1].Value.Trim() -ne "-") {
    Stop-WithMessage "Stopped: next-action reported blockers: $($blocker.Groups[1].Value.Trim())"
  }
}

function Find-ProposedCommand {
  param([string]$Text)
  $match = [regex]::Match($Text, '(?m)^python\s+scripts\\research_autopilot\.py\s+(paper:(?:tick-once|catch-up-next))\s+--confirm\s+"([^"]+)"\s*$')
  if (-not $match.Success) {
    return $null
  }
  return @{
    Action = $match.Groups[1].Value
    Confirm = $match.Groups[2].Value
    Display = $match.Value
  }
}

if (-not $Execute) {
  Write-Host "Dry run only. Re-run with -Execute to process paper ticks/catch-up commands."
}

for ($step = 1; $step -le $MaxSteps; $step++) {
  Write-Host ""
  Write-Host "Step ${step}/${MaxSteps}: checking paper next action..."
  $nextRun = Invoke-PythonText @("scripts\research_autopilot.py", "paper:next-action", "--refresh")
  $nextText = $nextRun.Text
  Write-Host $nextText.Trim()
  if ($nextRun.Code -ne 0) {
    Stop-WithMessage "Stopped: paper:next-action failed with exit code $($nextRun.Code)."
  }

  Assert-SafeNextAction $nextText

  $actionMatch = [regex]::Match($nextText, "(?m)^Next action:\s*(.+)$")
  $nextAction = if ($actionMatch.Success) { $actionMatch.Groups[1].Value.Trim() } else { "UNKNOWN" }
  if ($nextAction -eq "WAIT_FOR_NEXT_CLOSED_CANDLE") {
    Write-Host "Done: paper candidate is caught up to the latest closed candle."
    exit 0
  }
  if ($nextAction -notin @("SAFE_TO_RUN_SINGLE_CONFIRMED_TICK", "CATCH_UP_REQUIRED")) {
    Stop-WithMessage "Stopped: unsupported next action '$nextAction'."
  }

  $command = Find-ProposedCommand $nextText
  if ($null -eq $command) {
    Stop-WithMessage "Stopped: could not find a confirmed paper command in next-action output."
  }

  Write-Host "Proposed command:"
  Write-Host $command.Display
  if (-not $Execute) {
    Write-Host "Dry run stopping before execution."
    exit 0
  }

  Write-Host "Executing confirmed paper command..."
  $resultRun = Invoke-PythonText @("scripts\research_autopilot.py", $command.Action, "--confirm", $command.Confirm)
  $resultText = $resultRun.Text
  Write-Host $resultText.Trim()
  if ($resultRun.Code -ne 0) {
    Stop-WithMessage "Stopped: paper command failed with exit code $($resultRun.Code)."
  }

  try {
    $result = $resultText | ConvertFrom-Json
  } catch {
    Stop-WithMessage "Stopped: paper command did not return JSON."
  }
  if (-not $result.ok) {
    Stop-WithMessage "Stopped: paper command returned ok=false."
  }
  if ($result.realTradingEnabled -ne $false -or $result.liveOrdersTouched -ne $false) {
    Stop-WithMessage "Stopped: paper command safety flags are not clean."
  }
}

Stop-WithMessage "Stopped: reached MaxSteps=$MaxSteps before WAIT_FOR_NEXT_CLOSED_CANDLE."
