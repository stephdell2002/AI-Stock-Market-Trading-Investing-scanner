<#
.SYNOPSIS
  Watchman daily orchestration for Windows Task Scheduler.

.DESCRIPTION
  One script, four phases. Schedule each phase as a Task Scheduler task at the
  right ET time (convert ET to your local time — see docs/scheduling.md). Every
  run is logged to data\logs\<phase>-YYYY-MM-DD.log.

    powershell -File scripts\watchman_run.ps1 morning    # ~08:05 ET  scan + brief
    powershell -File scripts\watchman_run.ps1 intraday   # every 5m during session
    powershell -File scripts\watchman_run.ps1 evening    # ~16:15 ET  resolve + reports
    powershell -File scripts\watchman_run.ps1 weekly     # refresh screener watchlist
    powershell -File scripts\watchman_run.ps1 monthly    # long-term rebalance

.PARAMETER Phase
  One of: morning, intraday, evening, weekly, monthly.
#>
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('morning', 'intraday', 'evening', 'weekly', 'debuts', 'monthly')]
  [string]$Phase
)

$ErrorActionPreference = 'Stop'

# Repo root from this script's location, so the scheduler's working directory
# never matters.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Prefer the project virtualenv; fall back to a `watchman` on PATH.
$VenvWatchman = Join-Path $RepoRoot '.venv\Scripts\watchman.exe'
if (Test-Path $VenvWatchman) {
  $Watchman = $VenvWatchman
}
elseif (Get-Command watchman -ErrorAction SilentlyContinue) {
  $Watchman = 'watchman'
}
else {
  Write-Error 'watchman not found (no .venv\Scripts\watchman.exe and none on PATH)'
  exit 1
}

# Load API keys from .env if present (KEY=value lines; keys live in env only).
$EnvFile = Join-Path $RepoRoot '.env'
if (Test-Path $EnvFile) {
  Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*)$') {
      [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
  }
}

$LogDir = Join-Path $RepoRoot 'data\logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("{0}-{1}.log" -f $Phase, (Get-Date -Format 'yyyy-MM-dd'))

function Invoke-Watchman {
  param([string[]]$WatchmanArgs)
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  "[$stamp] watchman $($WatchmanArgs -join ' ')" | Tee-Object -FilePath $LogFile -Append
  & $Watchman @WatchmanArgs *>> $LogFile
}

switch ($Phase) {
  'morning' {
    Invoke-Watchman @('scan')
    Invoke-Watchman @('report', '--brief', 'morning')
  }
  'intraday' {
    Invoke-Watchman @('signals')
  }
  'evening' {
    Invoke-Watchman @('signals')
    Invoke-Watchman @('report', '--brief', 'evening')
    Invoke-Watchman @('report')
  }
  'weekly' {
    Invoke-Watchman @('screen')
  }
  'debuts' {
    # New/upcoming listings + data-driven verdicts. Needs a Finnhub or FMP key.
    Invoke-Watchman @('debuts')
  }
  'monthly' {
    Invoke-Watchman @('report', '--rebalance-longterm')
  }
}

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Output "[$stamp] $Phase done -> $LogFile"
