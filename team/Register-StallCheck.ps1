# Registers the hx stall check for the current Windows user. The task runs
# `hx team stalls` every ten minutes in a hidden console; use -Unregister to
# remove it. This script is for the lead to run. It never runs the check.
param(
    [switch]$Unregister,
    [int]$EveryMinutes = 10,
    [string]$TaskName = 'hx-team-stalls',
    [string]$Team = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed scheduled task '$TaskName'."
    return
}

if ($EveryMinutes -lt 1 -or $EveryMinutes -gt 1440) {
    throw 'EveryMinutes must be between 1 and 1440.'
}

$team = $Team
if (-not $team -and $env:HX_HERDR_PREFIX) { $team = $env:HX_HERDR_PREFIX }
if (-not $team -and $env:HC_TERMINAL_ID) {
    try {
        $detail = hc get $env:HC_TERMINAL_ID --json | ConvertFrom-Json
        $team = [string]$detail.labels.team
    } catch { }
}
if (-not $team) { $team = 'dev' }
$team = $team.TrimEnd('-')
if ($team -notmatch '^[a-z0-9][a-z0-9-]*$') {
    throw "Team must be a plain label such as dev or hx; got '$team'."
}

$python = (Get-Command python.exe -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "-m hx team stalls --team $team" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes)
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "hx: alert the $team lead when a worker screen freezes" `
    -Force | Out-Null

Write-Host "Registered '$TaskName' every $EveryMinutes minutes for team $team."
Write-Host "Remove it with: powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"