# Builds the Herdr app team grid for one project from the lead's own hc terminal:
# the lead (this terminal) top-left, codex to its right, devin below it, opencode below codex.
# Usage:
#   & team\Start-HerdrAppTeam.ps1 -Project C:\path\to\repo [-Team dev] [-DevinCwd <dir>] [-DryRun]
# Safe to run twice: a role that already has a live <team>-<role> terminal is left alone and its
# pane is not moved. -DevinCwd defaults to -Project. -DryRun prints every hc and herdr-app command
# it would run and runs none of them (roster queries still run).
param(
    [Parameter(Mandatory)][string]$Project,
    [ValidatePattern('^[a-z0-9][a-z0-9-]*$')][string]$Team = 'dev',
    [string]$DevinCwd,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (-not $env:HC_TERMINAL_ID) { throw 'Run this from the lead terminal inside the Herdr app (HC_TERMINAL_ID is unset).' }
$lead = $env:HC_TERMINAL_ID
$Project = (Resolve-Path $Project).Path
if (-not (Test-Path (Join-Path $Project '.git'))) { throw "$Project is not a Git repository root." }
if (-not $DevinCwd) { $DevinCwd = $Project }
$DevinCwd = (Resolve-Path $DevinCwd).Path
$dir = Split-Path $PSCommandPath

# Launch models and bin paths come from policy, regardless of stale installed overlay defaults.
$policyPath = $env:HX_POLICY_PATH
if (-not $policyPath) {
    $policyPath = Join-Path $dir '..\policy.json'
    if (-not (Test-Path $policyPath)) {
        # Installed under ~/.herdr-team the script has no repo sibling; the hx.cmd shim install.ps1
        # writes embeds the repo path in PYTHONPATH.
        $hxCmd = (Get-Command hx.cmd -ErrorAction SilentlyContinue).Source
        $hit = if ($hxCmd) { Select-String -Path $hxCmd -Pattern 'set "PYTHONPATH=([^;]+);' -List }
        if ($hit) { $policyPath = Join-Path $hit.Matches[0].Groups[1].Value 'policy.json' }
    }
}
if (-not (Test-Path $policyPath)) { throw 'policy.json not found; set HX_POLICY_PATH or run the launcher from the hx repo checkout.' }
$policy = Get-Content -Raw $policyPath | ConvertFrom-Json

$model = $policy.persistent_opencode_model
if ($model -isnot [string] -or $model -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]*$') {
    throw 'policy.persistent_opencode_model must be a model ID without a provider prefix.'
}
$opencodeModel = "opencode-go/$model"
$devinModel = $policy.devin.models.medium
if (-not $devinModel) { throw 'policy.devin.models.medium is not set.' }
$codexBin = $policy.codex_bin_dir
if ($codexBin -isnot [string] -or -not $codexBin) { throw 'policy.codex_bin_dir is not set.' }
if ($codexBin.StartsWith('~/') -or $codexBin.StartsWith('~\')) {
    $codexBin = $env:USERPROFILE + ($codexBin.Substring(1) -replace '/', '\')
}
$opencodeConfig = Join-Path $env:USERPROFILE '.herdr-team\opencode-trusted-dev.jsonc'
$devinState = Join-Path $env:USERPROFILE ".herdr-team\state\$Team-devin"

function Format-Command([string[]]$Argv) {
    ($Argv | ForEach-Object {
        if ($_ -match '^[A-Za-z0-9_./\\:=+-]+$') { $_ } else { "'" + ($_ -replace "'", "''") + "'" }
    }) -join ' '
}

function Invoke-Planned([string[]]$Argv) {
    if ($DryRun) { [Console]::WriteLine((Format-Command $Argv)); return }
    $rest = @($Argv | Select-Object -Skip 1)
    # herdr-app.exe is a windowed program: PowerShell neither waits for it nor captures its output
    # unless it sits in a pipeline, so every call pipes (9 Nov: a plain assignment returned nothing).
    $out = & $Argv[0] @rest | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$(Format-Command $Argv) failed: $out" }
    return $out
}

# A terminal has a pane once herdr-app ls lists it under a tab; a sub-agent without one sits in
# its parent's sub_agents. pane.focus errors with "no such pane" before that.
function Test-AppPane([string]$Terminal) {
    $out = herdr-app ls | Out-String
    $ls = try { $out | ConvertFrom-Json } catch { throw "herdr-app ls returned no JSON: $out" }
    foreach ($space in $ls.spaces) {
        foreach ($tab in $space.tabs) {
            foreach ($t in $tab.terminals) {
                if ($t.terminal -eq $Terminal -and $t.pane) { return $true }
            }
        }
    }
    return $false
}

function Wait-AppPane([string]$Terminal) {
    if ($DryRun) { return }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-AppPane $Terminal)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw "herdr-app shows no pane for $Terminal; refusing to focus it." }
        Start-Sleep -Milliseconds 250
    }
}

$lsOut = & hc ls --json
$terminals = try { ($lsOut | Out-String | ConvertFrom-Json).terminals } catch { throw "hc ls --json returned no JSON: $lsOut" }
if ($null -eq $terminals) { $terminals = @() }

Invoke-Planned @('hc', 'label', $lead, "team=$Team", 'role=claude') | Out-Null

$roles = [ordered]@{
    codex = @{
        Cwd = $Project
        Argv = @('powershell', '-NoLogo', '-NoExit', '-Command',
            "`$env:PATH='$codexBin;'+`$env:PATH; codex --no-daemon -p trusted-dev")
    }
    devin = @{
        Cwd = $DevinCwd
        Argv = @('devin', '--model', $devinModel, '--config', (Join-Path $devinState 'config.json'),
            '--permission-mode', 'dangerous', '--export', (Join-Path $devinState 'transcript.json'))
    }
    opencode = @{
        Cwd = $Project
        Argv = @('powershell', '-NoLogo', '-NoExit', '-Command',
            "`$env:OPENCODE_CONFIG='$opencodeConfig'; opencode -m $opencodeModel")
    }
}

$started = [ordered]@{}
$live = @{}
foreach ($role in $roles.Keys) {
    $name = "$Team-$role"
    $existing = @($terminals | Where-Object { $_.name -eq $name -and $_.state -eq 'live' }) | Select-Object -First 1
    if ($existing) {
        $live[$role] = $existing.address
        Write-Output "already live $($existing.address)"
        continue
    }
    if ($role -eq 'devin') {
        $promoEnd = $null
        try { $promoEnd = [DateTimeOffset]::Parse($policy.devin.promo.ends_at) } catch {}
        if (-not $promoEnd -or [DateTimeOffset]::UtcNow -ge $promoEnd) {
            throw 'SWE-2 promotion ended or policy.devin.promo.ends_at is missing; re-verify pricing and get approval before starting a Devin pane.'
        }
        if (-not $DryRun) {
            New-Item -ItemType Directory -Force $devinState | Out-Null
            Copy-Item (Join-Path $dir 'devin-pane.config.json') (Join-Path $devinState 'config.json') -Force
        }
    }
    $spec = $roles[$role]
    $runArgv = @('hc', 'run', '--bg', '--outlive-parent', '--name', $name, '--cwd', $spec.Cwd,
        '--label', "team=$Team", '--label', "role=$role", '--json', '--') + $spec.Argv
    $out = Invoke-Planned $runArgv
    $started[$role] = if ($DryRun) { $name } else { ($out | Out-String | ConvertFrom-Json).address }
    if (-not $DryRun) { Write-Output "started $($started[$role])" }
}

if ($started.Contains('codex')) {
    Invoke-Planned @('herdr-app', 'do', 'pane.focus', '--terminal', $lead) | Out-Null
    Invoke-Planned @('herdr-app', 'do', 'terminal.keep', '--terminal', $started.codex, '--place', 'right') | Out-Null
    Wait-AppPane $started.codex
}
if ($started.Contains('devin')) {
    Invoke-Planned @('herdr-app', 'do', 'pane.focus', '--terminal', $lead) | Out-Null
    Invoke-Planned @('herdr-app', 'do', 'terminal.keep', '--terminal', $started.devin, '--place', 'down') | Out-Null
    Wait-AppPane $started.devin
}
if ($started.Contains('opencode')) {
    $codexTerminal = if ($started.Contains('codex')) { $started.codex } else { $live.codex }
    Wait-AppPane $codexTerminal
    Invoke-Planned @('herdr-app', 'do', 'pane.focus', '--terminal', $codexTerminal) | Out-Null
    Invoke-Planned @('herdr-app', 'do', 'terminal.keep', '--terminal', $started.opencode, '--place', 'down') | Out-Null
    Wait-AppPane $started.opencode
}
