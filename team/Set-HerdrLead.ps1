# Moves the team's lead seat (role=<policy lead_role>, default lead) to another hc terminal.
# Usage:
#   & team\Set-HerdrLead.ps1 -To <terminal> [-Team dev] [-DryRun]
# Workers always report with `hx say lead`, so a handover is only a relabel. The old lead gets back a role
# named after its agent family (claude, codex, opencode, devin; devin2 when that role is taken), before the
# new lead is labelled, so no two terminals ever share a role. Safe to run twice.
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

$onTeam = @($members | Where-Object { $_.labels.team -eq $Team })
$taken = @{}
foreach ($m in $onTeam) { if ($m.address -ne $target.address) { $taken[[string]$m.labels.role] = $true } }

function Invoke-Label([string]$Terminal, [string]$Role) {
    if ($Role -notmatch '^[a-z0-9][a-z0-9-]*$') { throw "Invalid role label $Role." }
    $argv = @('label', $Terminal, "team=$Team", "role=$Role")
    if ($DryRun) { [Console]::WriteLine("hc $($argv -join ' ')"); return }
    hc @argv | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "hc $($argv -join ' ') failed." }
    Write-Output "$Terminal is now $Team-$Role"
}

foreach ($old in @($onTeam | Where-Object { $_.labels.role -eq $leadRole -and $_.address -ne $target.address })) {
    $family = if ($old.agent -eq 'claude-code') { 'claude' } else { ([string]$old.agent).ToLower() }
    $role = $family
    for ($n = 2; $taken.ContainsKey($role); $n++) { $role = "$family$n" }
    $taken.Remove($leadRole)
    $taken[$role] = $true
    Invoke-Label $old.address $role
}
if ($target.labels.team -eq $Team -and $target.labels.role -eq $leadRole) {
    Write-Output "$($target.address) already holds $Team-$leadRole."
} else {
    Invoke-Label $target.address $leadRole
}
