# Builds the Herdr app team grid for one project from the lead's own hc terminal:
# the lead (this terminal) top-left, codex to its right, devin below it, opencode in its own
# tab (a small pane segfaults it). Members are found by their team/role labels, never a raw name.
# Usage:
#   & team\Start-HerdrAppTeam.ps1 -Project C:\path\to\repo [-Team dev] [-DevinCwd <dir>] [-Lead <terminal>] [-DryRun]
# Safe to run twice: validated live roles are reused and missing panes are restored.
# Existing panes are not moved. -DevinCwd defaults to -Project. -Lead (the grid anchor that gets role=<policy
# lead_role>) defaults to this terminal. -DryRun prints every hc and herdr-app command
# it would run and runs none of them (roster queries still run).
param(
    [Parameter(Mandatory)][string]$Project,
    [ValidateSet('dev')][string]$Team = 'dev',
    [string]$DevinCwd,
    [string]$Lead,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if (-not $env:HC_TERMINAL_ID) { throw 'Run this from the lead terminal inside the Herdr app (HC_TERMINAL_ID is unset).' }
$lead = if ($Lead) { $Lead } else { $env:HC_TERMINAL_ID }
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
$leadRole = if ($policy.lead_role) { [string]$policy.lead_role } else { 'lead' }

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

function Resolve-HostTool([string]$Name, [string]$Folder) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        if ($command.CommandType -in @('Function', 'Filter', 'Alias')) { return $Name }
        return $command.Source
    }
    $install = Join-Path $env:LOCALAPPDATA "Programs\$Folder"
    $candidates = @((Join-Path $install "$Name.exe"), (Join-Path $install $Name))
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw "No $Name executable found; searched PATH and $($candidates -join ', ')."
}

$hostTools = @{
    hc = Resolve-HostTool 'hc' 'herdr-core'
    'herdr-app' = Resolve-HostTool 'herdr-app' 'herdr-app'
}

function Invoke-Planned([string[]]$Argv) {
    if ($DryRun) { [Console]::WriteLine((Format-Command $Argv)); return }
    $rest = @($Argv | Select-Object -Skip 1)
    # herdr-app.exe is a windowed program: PowerShell neither waits for it nor captures its output
    # unless it sits in a pipeline, so every call pipes (9 Nov: a plain assignment returned nothing).
    $out = & $hostTools[$Argv[0]] @rest | Out-String
    if ($LASTEXITCODE -ne 0) { throw "$(Format-Command $Argv) failed: $out" }
    return $out
}

# A terminal has a pane once herdr-app ls lists it under a tab; a sub-agent without one sits in
# its parent's sub_agents. pane.focus errors with "no such pane" before that.
function Get-AppPanes {
    $out = & $hostTools['herdr-app'] ls | Out-String
    if ($LASTEXITCODE -ne 0) { throw 'herdr-app ls failed.' }
    $ls = try { $out | ConvertFrom-Json } catch { throw 'herdr-app ls returned no JSON.' }
    if ($ls -isnot [pscustomobject] -or $ls.spaces -isnot [array]) { throw 'Invalid herdr-app roster.' }
    foreach ($space in $ls.spaces) {
        if ($space.tabs -isnot [array]) { throw 'Invalid herdr-app tabs.' }
        foreach ($tab in $space.tabs) {
            if ($tab.terminals -isnot [array]) { throw 'Invalid herdr-app terminals.' }
            foreach ($t in $tab.terminals) {
                if (-not $t.terminal) { throw 'Invalid herdr-app terminal.' }
                if ($t.pane) { $t.terminal }
            }
        }
    }
}

function Wait-AppPane([string]$Terminal) {
    if ($DryRun) { return }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ($Terminal -notin @(Get-AppPanes)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw "herdr-app shows no pane for $Terminal; refusing to focus it." }
        Start-Sleep -Milliseconds 250
    }
}

$lsOut = & $hostTools.hc ls --json
if ($LASTEXITCODE -ne 0) { throw 'hc ls --json failed.' }
$roster = try { $lsOut | Out-String | ConvertFrom-Json } catch { throw 'hc ls --json returned no JSON.' }
if ($roster -isnot [pscustomobject] -or $roster.terminals -isnot [array]) { throw 'Invalid hc roster.' }
$terminals = $roster.terminals
foreach ($t in $terminals) {
    if (-not $t.address -or $t.state -notin @('live', 'finished')) { throw 'Invalid hc terminal.' }
}
$panes = @(Get-AppPanes)
if ($lead -notin $panes) { throw "Lead $lead has no app pane." }

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

# Details for every live terminal once: labels are the canonical identity (a raw
# name is set at launch, cannot be renamed, and may be stale after a handover).
$details = @{}
foreach ($t in @($terminals | Where-Object { $_.state -eq 'live' })) {
    $out = & $hostTools.hc get $t.address --json | Out-String
    if ($LASTEXITCODE -ne 0) { throw "hc get $($t.address) failed." }
    $detail = try { $out | ConvertFrom-Json } catch { throw "Invalid terminal details for $($t.address)." }
    if ($detail.address -ne $t.address -or $detail.state -ne 'live') { throw "Invalid terminal details for $($t.address)." }
    $details[$t.address] = $detail
}

function Get-ProviderKind($Detail) {
    # `hc get` reports the provider under status.app; a raw name says nothing about it.
    $app = [string]$Detail.status.app
    switch ($app) {
        'claude-code' { return 'claude' }
        'codex' { return 'codex' }
        'opencode' { return 'opencode' }
        'devin' { return 'devin' }
        default { return $null }
    }
}

# A -Lead that already holds a worker role would be silently stripped of it; refuse first.
$leadDetail = $details[$lead]
if (-not $leadDetail) { throw "Lead $lead is not a live hc terminal." }
if ($leadDetail.labels.team -and $leadDetail.labels.team -ne $Team) {
    throw "$lead is labelled team=$($leadDetail.labels.team); refusing to pull a foreign terminal into team $Team."
}
if ($leadDetail.labels.team -eq $Team -and $leadDetail.labels.role -and $leadDetail.labels.role -ne $leadRole) {
    throw "$lead already holds role=$($leadDetail.labels.role) on team $Team; hand the seat over with Set-HerdrLead.ps1 first."
}

$live = @{}
foreach ($role in $roles.Keys) {
    $name = "$Team-$role"
    $candidates = @($details.Values | Where-Object {
        $_.labels.team -eq $Team -and $_.labels.role -eq $role })
    if ($candidates.Count -gt 1) {
        throw "Conflicting live role $name at $(($candidates | ForEach-Object { $_.address }) -join ', '); correct it before retrying."
    }
    if ($candidates.Count -eq 1) {
        $detail = $candidates[0]
        $address = $detail.address
        $sameCwd = $false
        try { $sameCwd = (Resolve-Path $detail.cwd).Path -eq $roles[$role].Cwd } catch {}
        # status.app identifies the provider even when the terminal was opened by hand
        # and its argv is only the shell; a launcher-shaped argv is the fallback.
        $provider = Get-ProviderKind $detail
        $programOK = $provider -eq $role
        if (-not $programOK -and $null -eq $detail.status) {
            $argv = @($detail.argv)
            if ($role -eq 'devin') {
                $programOK = $argv.Count -gt 0 -and [IO.Path]::GetFileNameWithoutExtension($argv[0]) -eq 'devin'
            } elseif ($argv.Count -ge 3 -and [IO.Path]::GetFileNameWithoutExtension($argv[0]) -in @('powershell', 'pwsh')) {
                $commandIndex = [array]::IndexOf($argv, '-Command')
                $programOK = $commandIndex -ge 0 -and $commandIndex + 1 -lt $argv.Count -and
                    $argv[$commandIndex + 1] -match "(?:^|;)\s*$role(?:\s|$)"
            }
        }
        if (-not $sameCwd -or -not $programOK) {
            throw "Conflicting live terminal $address ($name); expected cwd $($roles[$role].Cwd), team=$Team, role=$role and program $role. Correct it before retrying."
        }
        $live[$role] = $address
    }
    # A terminal named like the role whose labels do not claim it: a foreign squatter
    # is a conflict; a teammate with a stale post-handover name only warns.
    foreach ($sq in @($details.Values | Where-Object {
            $_.name -eq $name -and -not ($_.labels.team -eq $Team -and $_.labels.role -eq $role) })) {
        if ($sq.labels.team -eq $Team) {
            Write-Warning "$($sq.address) is named $name but holds role=$($sq.labels.role); its raw name is stale."
        } else {
            throw "$($sq.address) is named $name but is not a $Team $role; correct the conflict before retrying."
        }
    }
}

# Two terminals with one role break every hx lookup on the team, so a second lead is refused.
foreach ($t in @($details.Values)) {
    if ($t.address -eq $lead -or $t.address -in $live.Values) { continue }
    if ($t.labels.team -eq $Team -and $t.labels.role -eq $leadRole) {
        throw "$($t.address) already holds role=$leadRole on team $Team. Hand the seat over with Set-HerdrLead.ps1 first."
    }
}

$launchNames = @{}
$takenNames = @{}
foreach ($detail in $details.Values) {
    if ($detail.name) { $takenNames[[string]$detail.name] = $true }
}
foreach ($role in $roles.Keys) {
    if ($live.ContainsKey($role)) { continue }
    $baseName = "$Team-$role"
    $name = $baseName
    for ($n = 2; $takenNames.ContainsKey($name); $n++) { $name = "$baseName-$n" }
    $takenNames[$name] = $true
    $launchNames[$role] = $name
}

if (-not $live.ContainsKey('devin')) {
    if ($policy.devin.enabled -ne $true) { throw 'policy.devin.enabled must be true.' }
    if ($devinModel -cnotin $policy.devin.allowed_models) { throw 'Devin model is not in policy.devin.allowed_models.' }
    $promoEnd = $null
    try { $promoEnd = [DateTimeOffset]::Parse($policy.devin.promo.ends_at) } catch {}
    if (-not $promoEnd -or [DateTimeOffset]::UtcNow -ge $promoEnd) {
        throw 'SWE-2 promotion ended or policy.devin.promo.ends_at is missing; re-verify pricing and get approval before starting a Devin pane.'
    }
    $modelOut = devin models list --format json | Out-String
    if ($LASTEXITCODE -ne 0) { throw 'devin models list failed.' }
    $catalog = try { $modelOut | ConvertFrom-Json } catch { throw 'devin models list returned no JSON.' }
    $variants = @($catalog.families | ForEach-Object { $_.variants } | Where-Object { $_.model_uid -ceq $devinModel })
    if ($variants.Count -ne 1 -or $variants[0].cost_tier -cne 'Free') { throw 'Devin model is unavailable or not Free; refusing launch.' }
}

Invoke-Planned @('hc', 'label', $lead, "team=$Team", "role=$leadRole") | Out-Null
foreach ($role in $roles.Keys) {
    if ($live.ContainsKey($role)) {
        Write-Output "already live $($live[$role])"
        continue
    }
    $name = $launchNames[$role]
    if ($role -eq 'devin') {
        if (-not $DryRun) {
            New-Item -ItemType Directory -Force $devinState | Out-Null
            Copy-Item (Join-Path $dir 'devin-pane.config.json') (Join-Path $devinState 'config.json') -Force
        }
    }
    $spec = $roles[$role]
    $runArgv = @('hc', 'run', '--bg', '--outlive-parent', '--name', $name, '--cwd', $spec.Cwd,
        '--label', "team=$Team", '--label', "role=$role", '--json', '--') + $spec.Argv
    $out = Invoke-Planned $runArgv
    $live[$role] = if ($DryRun) { "t-new-$role" } else { ($out | Out-String | ConvertFrom-Json).address }
    if (-not $live[$role]) { throw "hc run returned no address for $name." }
    if (-not $DryRun) { Write-Output "started $($live[$role])" }
}

foreach ($role in $roles.Keys) {
    $terminal = $live[$role]
    if (-not $DryRun) { $panes = @(Get-AppPanes) }
    if ($terminal -in $panes) {
        Write-Output "$role $terminal already has a pane"
        continue
    }
    # OpenCode segfaults at a small pane size (Bun, measured at 37x16 on 9 Nov); a tab
    # is the proven large surface, so it never gets a grid slot.
    $leadProvider = Get-ProviderKind $leadDetail
    $place = if ($role -eq 'opencode' -or -not $leadProvider -or $leadProvider -eq 'opencode') {
        'tab'
    } elseif ($role -eq 'codex') { 'right' } else { 'down' }
    if ($place -ne 'tab') {
        Wait-AppPane $lead
        Invoke-Planned @('herdr-app', 'do', 'pane.focus', '--terminal', $lead) | Out-Null
    }
    # A terminal hc just started can take a moment before herdr-app sees it running.
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ($true) {
        try { Invoke-Planned @('herdr-app', 'do', 'terminal.keep', '--terminal', $terminal, '--place', $place) | Out-Null; break }
        catch {
            if ("$_" -notmatch 'no running terminal' -or [DateTime]::UtcNow -gt $deadline) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
    Wait-AppPane $terminal
    Write-Output "$role $terminal placed in $place"
}
