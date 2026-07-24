<#
.SYNOPSIS
  Wire the Watchman daily rhythm into Windows Task Scheduler, in your timezone.

.DESCRIPTION
  Converts the ET market schedule to this machine's local time and prints the
  exact `schtasks` commands to create each task. Review them, then re-run with
  -Run to actually create the tasks (or paste the printed lines yourself).

    powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1          # preview
    powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Run     # create tasks
    powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -IncludeDebuts -Run

  Intraday runs every 5 minutes during the session (and, like cron's MINUTE
  schedule, on weekends too — the runner just no-ops when the market is closed).

.PARAMETER Run
  Actually create the scheduled tasks. Without it, the commands are only printed.
.PARAMETER IncludeDebuts
  Also schedule the new-listing scan (needs a Finnhub or FMP key).
#>
param(
  [switch]$Run,
  [switch]$IncludeDebuts
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $Repo 'scripts\watchman_run.ps1'
$et = [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')
$local = [TimeZoneInfo]::Local

function ConvertTo-LocalHHmm([int]$Hour, [int]$Minute) {
  $ref = [datetime]::SpecifyKind([datetime]::new(2025, 6, 4, $Hour, $Minute, 0), 'Unspecified')
  $utc = [TimeZoneInfo]::ConvertTimeToUtc($ref, $et)
  ([TimeZoneInfo]::ConvertTimeFromUtc($utc, $local)).ToString('HH:mm')
}

function New-TaskCommand([string]$Name, [string]$Phase, [string]$Schedule) {
  $tr = 'powershell.exe -ExecutionPolicy Bypass -NoProfile -File \"' + $RunScript + '\" ' + $Phase
  return 'schtasks /Create /TN "' + $Name + '" /TR "' + $tr + '" ' + $Schedule + ' /F'
}

$weekdays = '/SC WEEKLY /D MON,TUE,WED,THU,FRI'
$tasks = @(
  (New-TaskCommand 'Watchman Morning'  'morning'  ("$weekdays /ST " + (ConvertTo-LocalHHmm 8 5))),
  (New-TaskCommand 'Watchman Evening'  'evening'  ("$weekdays /ST " + (ConvertTo-LocalHHmm 16 15))),
  (New-TaskCommand 'Watchman Weekly'   'weekly'   ('/SC WEEKLY /D SUN /ST ' + (ConvertTo-LocalHHmm 18 0))),
  (New-TaskCommand 'Watchman Monthly'  'monthly'  ('/SC MONTHLY /D 1 /ST ' + (ConvertTo-LocalHHmm 18 30))),
  (New-TaskCommand 'Watchman Intraday' 'intraday' ('/SC MINUTE /MO 5 /ST ' + (ConvertTo-LocalHHmm 9 35) + ' /ET ' + (ConvertTo-LocalHHmm 15 55) + ' /K'))
)
if ($IncludeDebuts) {
  $tasks += (New-TaskCommand 'Watchman Debuts' 'debuts' ("$weekdays /ST " + (ConvertTo-LocalHHmm 7 30)))
}

Write-Output "Watchman schedule for timezone: $($local.Id)  (ET converted to local)"
Write-Output ''
foreach ($cmd in $tasks) {
  Write-Output $cmd
  if ($Run) {
    cmd.exe /c $cmd | Out-Null
    Write-Output '  -> created.'
  }
}
if (-not $Run) {
  Write-Output ''
  Write-Output 'Preview only. Re-run with -Run to create these tasks, or paste the lines above.'
}
