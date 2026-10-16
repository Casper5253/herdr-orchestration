# Opens a Herdr workspace for one project with dev-claude (lead), dev-codex and dev-opencode.
# Usage (from any Herdr pane): & "$env:USERPROFILE\.herdr-team\Start-HerdrTeam.ps1" C:\path\to\repo [-NoOpenCode]
param([Parameter(Mandatory)][string]$Project, [switch]$NoOpenCode)

$ErrorActionPreference = 'Stop'
if ($env:HERDR_ENV -ne '1') { throw 'Run this from inside a Herdr pane.' }
$Project = (Resolve-Path $Project).Path
if (-not (Test-Path (Join-Path $Project '.git'))) { throw "$Project is not a Git repository root." }
$team = Split-Path $PSCommandPath

function Invoke-Herdr { $out = & herdr @args; if ($LASTEXITCODE -ne 0) { throw "herdr $args failed: $out" }; $out | ConvertFrom-Json }

$ws = Invoke-Herdr workspace create --cwd $Project --label ("team-" + (Split-Path $Project -Leaf)) --focus
$lead = $ws.result.root_pane.pane_id
$right = (Invoke-Herdr pane split $lead --direction right --cwd $Project --no-focus).result.pane.pane_id
if (-not $NoOpenCode) {
    $below = (Invoke-Herdr pane split $right --direction down --cwd $Project --env "OPENCODE_CONFIG=$team\opencode-trusted-dev.jsonc" --no-focus).result.pane.pane_id
}

# Each start returns once the agent is ready, or agent_not_ready if it shows a first-run trust prompt (answer it in that pane).
& herdr agent start dev-claude --kind claude --pane $lead --timeout 120000 -- --permission-mode auto --settings "$team\claude-settings.json"
& herdr agent start dev-codex --kind codex --pane $right --timeout 120000 -- -p trusted-dev
if (-not $NoOpenCode) { & herdr agent start dev-opencode --kind opencode --pane $below --timeout 120000 }
& herdr agent list
