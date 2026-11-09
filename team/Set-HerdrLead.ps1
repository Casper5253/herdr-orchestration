# Moves the team's lead seat (role=<policy lead_role>, default lead) to another hc terminal.
# Usage:
#   & team\Set-HerdrLead.ps1 -To <terminal> [-Team dev] [-DryRun]
# Workers always report with `hx say lead`, so a handover is only a relabel. The old lead gets back a role
# named after its provider family (claude, codex, opencode, devin; devin2 when that role is taken), before the
# new lead is labelled, so no two terminals ever share a role. The provider kind comes from the terminal's
# reported status.app, never its raw name. The whole plan is validated before the first label change, and a
# terminal whose raw name no longer matches its identity is reported: hc has no rename, so the launcher keys
# on labels only. Safe to run twice.
param(
    [Parameter(Mandatory)][string]$To,
    [ValidateSet('dev')][string]$Team = 'dev',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$policyPath = $env:HX_POLICY_PATH
if (-not $policyPath) {
    $policyPath = Join-Path (Split-Path $PSCommandPath) '..\policy.json'
    if (-not (Test-Path $policyPath)) {
        $hxCmd = (Get-Command hx.cmd -ErrorAction SilentlyContinue).Source
        $hit = if ($hxCmd) { Select-String -Path $hxCmd -Pattern 'set "PYTHONPATH=([^;]+);' -List }
        if ($hit) { $policyPath = Join-Path $hit.Matches[0].Groups[1].Value 'policy.json' }
    }
}
$leadRole = 'lead'
if ($policyPath -and (Test-Path $policyPath)) {
    $policy = Get-Content -Raw $policyPath | ConvertFrom-Json
    if ($policy.lead_role) { $leadRole = [string]$policy.lead_role }
}

$lsOut = hc ls --json | Out-String
if ($LASTEXITCODE -ne 0) { throw 'hc ls --json failed.' }
$roster = try { $lsOut | ConvertFrom-Json } catch { throw 'hc ls --json returned no JSON.' }
$members = foreach ($t in @($roster.terminals | Where-Object { $_.state -eq 'live' })) {
    $out = hc get $t.address --json | Out-String
    if ($LASTEXITCODE -ne 0) { throw "hc get $($t.address) failed." }
    try { $out | ConvertFrom-Json } catch { throw "Invalid terminal details for $($t.address)." }
}
$target = $members | Where-Object { $_.address -eq $To -or $_.name -eq $To } | Select-Object -First 1
if (-not $target) { throw "$To is not a live hc terminal." }
if ($target.labels.team -and $target.labels.team -ne $Team) {
    throw "$($target.address) is labelled team=$($target.labels.team); refusing to move it into team $Team."
}

function Get-ProviderKind($Detail) {
    # `hc get` details carry the provider under status.app, not the old `agent` field.
    $app = [string]$Detail.status.app
    switch ($app) {
        'claude-code' { return 'claude' }
        'codex' { return 'codex' }
        'opencode' { return 'opencode' }
        'devin' { return 'devin' }
        default { return $null }
    }
}

$onTeam = @($members | Where-Object { $_.labels.team -eq $Team })
$taken = @{}
foreach ($m in $onTeam) { if ($m.address -ne $target.address) { $taken[[string]$m.labels.role] = $true } }

# Plan the whole handover before the first label change: any refusal here mutates nothing.
$plan = @()
$assigned = @{}
foreach ($old in @($onTeam | Where-Object { $_.labels.role -eq $leadRole -and $_.address -ne $target.address })) {
    $family = Get-ProviderKind $old
    if (-not $family) {
        throw "$($old.address) reports no known provider (status.app is missing or unrecognized); refusing the handover."
    }
    $taken.Remove($leadRole)
    $role = $family
    for ($n = 2; $taken.ContainsKey($role) -or $assigned.ContainsKey($role); $n++) { $role = "$family$n" }
    $assigned[$role] = $old.address
    $plan += @{Terminal = $old.address; Role = $role}
}
$targetIsLead = $target.labels.team -eq $Team -and $target.labels.role -eq $leadRole
if (-not $targetIsLead) {
    if ($taken.ContainsKey($leadRole)) {
        throw "Another terminal still holds role=$leadRole on team $Team; the plan is inconsistent."
    }
    $plan += @{Terminal = $target.address; Role = $leadRole}
}
foreach ($entry in $plan) {
    if ($entry.Role -notmatch '^[a-z0-9][a-z0-9-]*$') { throw "Invalid role label $($entry.Role) for $($entry.Terminal)." }
}

function Invoke-Label([string]$Terminal, [string]$Role) {
    $argv = @('label', $Terminal, "team=$Team", "role=$Role")
    if ($DryRun) { [Console]::WriteLine("hc $($argv -join ' ')"); return }
    hc @argv | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "hc $($argv -join ' ') failed." }
    Write-Output "$Terminal is now $Team-$Role"
}

foreach ($entry in $plan) { Invoke-Label $entry.Terminal $entry.Role }
if (-not $plan) { Write-Output "$($target.address) already holds $Team-$leadRole." }

# A raw name is set at launch and cannot be renamed; report any that no longer matches.
foreach ($entry in $plan) {
    $detail = @($members | Where-Object { $_.address -eq $entry.Terminal })[0]
    if ($detail.name -and $detail.name -ne "$Team-$($entry.Role)") {
        Write-Warning "$($entry.Terminal) keeps its raw name $($detail.name); hc has no rename. The launcher resolves members by labels only."
    }
}
