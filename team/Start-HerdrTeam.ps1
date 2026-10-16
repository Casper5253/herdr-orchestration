# Opens a Herdr team for one project: <team>-claude (lead), <team>-codex and <team>-opencode.
# Usage (from any Herdr pane):
#   & "$env:USERPROFILE\.herdr-team\Start-HerdrTeam.ps1" C:\path\to\repo [-Team dev|hx] [-NoOpenCode]
#   Add -LeadPane <pane-id> to keep an already-running Claude as the lead: it is renamed <team>-claude and the
#   other two agents open beside it in that pane's workspace instead of a new workspace.
param([Parameter(Mandatory)][string]$Project, [ValidateSet('dev', 'hx')][string]$Team = 'dev', [string]$LeadPane, [switch]$NoOpenCode)

$ErrorActionPreference = 'Stop'
if ($env:HERDR_ENV -ne '1') { throw 'Run this from inside a Herdr pane.' }
$Project = (Resolve-Path $Project).Path
if (-not (Test-Path (Join-Path $Project '.git'))) { throw "$Project is not a Git repository root." }
$dir = Split-Path $PSCommandPath

function Invoke-Herdr { $out = & herdr @args; if ($LASTEXITCODE -ne 0) { throw "herdr $args failed: $out" }; $out | ConvertFrom-Json }

if ($LeadPane) {
    $lead = $LeadPane
    Invoke-Herdr agent rename $lead "$Team-claude" | Out-Null
} else {
    $ws = Invoke-Herdr workspace create --cwd $Project --label ("team-" + (Split-Path $Project -Leaf)) --focus
    $lead = $ws.result.root_pane.pane_id
}
# Team Codex runs the pinned CLI (0.163.0 fixes the elevated-sandbox regression, openai/codex #51634 via PR #51822),
# first on PATH for this pane only; the desktop app's own codex is left alone.
$codexBin = Join-Path $env:USERPROFILE 'Documents\Codex\CLI\codex\0.163.0\bin'
$codexEnv = @()
if (Test-Path (Join-Path $codexBin 'codex.exe')) { $codexEnv = @('--env', "PATH=$codexBin;$env:PATH") }
$right = (Invoke-Herdr pane split $lead --direction right --cwd $Project --no-focus @codexEnv).result.pane.pane_id
if (-not $NoOpenCode) {
    $below = (Invoke-Herdr pane split $right --direction down --cwd $Project --env "OPENCODE_CONFIG=$dir\opencode-trusted-dev.jsonc" --no-focus).result.pane.pane_id
}

# Each start returns once the agent is ready, or agent_not_ready if it shows a first-run trust prompt (answer it in that pane).
if (-not $LeadPane) { & herdr agent start "$Team-claude" --kind claude --pane $lead --timeout 120000 -- --permission-mode auto --settings "$dir\claude-settings.json" }
& herdr agent start "$Team-codex" --kind codex --pane $right --timeout 120000 -- -p trusted-dev
if (-not $NoOpenCode) { & herdr agent start "$Team-opencode" --kind opencode --pane $below --timeout 120000 }
& herdr agent list
