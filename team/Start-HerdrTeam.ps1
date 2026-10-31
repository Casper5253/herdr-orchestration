# Opens a Herdr team for one project: <team>-claude (lead), <team>-codex and <team>-opencode.
# Usage (from any Herdr pane):
#   & "$env:USERPROFILE\.herdr-team\Start-HerdrTeam.ps1" C:\path\to\repo [-Team <name>, default dev] [-NoOpenCode]
#   Add -LeadPane <pane-id> to keep an already-running Claude as the lead: it is renamed <team>-claude and the
#   other two agents open beside it in that pane's workspace instead of a new workspace.
#   Add -Devin to open <team>-devin as well: a SWE-2 pane split down from the codex pane. It refuses to
#   start once the free promotion in policy.json devin.promo.ends_at has passed.
param([Parameter(Mandatory)][string]$Project, [ValidatePattern('^[a-z0-9][a-z0-9-]*$')][string]$Team = 'dev', [string]$LeadPane, [switch]$NoOpenCode, [switch]$Devin)

$ErrorActionPreference = 'Stop'
if ($env:HERDR_ENV -ne '1') { throw 'Run this from inside a Herdr pane.' }
$Project = (Resolve-Path $Project).Path
if (-not (Test-Path (Join-Path $Project '.git'))) { throw "$Project is not a Git repository root." }
$dir = Split-Path $PSCommandPath

function Invoke-Herdr { $out = & herdr @args; if ($LASTEXITCODE -ne 0) { throw "herdr $args failed: $out" }; $out | ConvertFrom-Json }

# -Devin reads policy.json for the model and refuses once the free SWE-2 promotion ends, the same gate
# hx applies to ocrun --worker devin. HX_POLICY_PATH overrides the location, as it does for hx.
$devinModel = $null
if ($Devin) {
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
    $promoEnd = $null
    try { $promoEnd = [DateTimeOffset]::Parse($policy.devin.promo.ends_at) } catch {}
    if (-not $promoEnd -or [DateTimeOffset]::UtcNow -ge $promoEnd) {
        throw 'SWE-2 promotion ended or policy.devin.promo.ends_at is missing; re-verify pricing and get approval before re-enabling.'
    }
    $devinModel = $policy.devin.models.medium
    if (-not $devinModel) { throw 'policy.devin.models.medium is not set.' }
}

if ($LeadPane) {
    $lead = $LeadPane
    Invoke-Herdr agent rename $lead "$Team-claude" | Out-Null
} else {
    $ws = Invoke-Herdr workspace create --cwd $Project --label ("team-" + (Split-Path $Project -Leaf)) --focus
    $lead = $ws.result.root_pane.pane_id
}
# Team Codex runs the pinned CLI (0.163.0 fixes the elevated-sandbox regression, openai/codex #51634 via PR #51822),
# first on PATH for this pane only; the desktop app's own codex is left alone.
$codexBin = Join-Path $env:USERPROFILE 'Documents\Codex\CLI\codex\0.166.0\bin'
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
if ($Devin) {
    # The pane writes setup state (org id, shell setup, theme) into its config, so it gets a fresh copy
    # of the template on every launch.
    $devinState = Join-Path $env:USERPROFILE ".herdr-team\state\$Team-devin"
    New-Item -ItemType Directory -Force $devinState | Out-Null
    Copy-Item (Join-Path $dir 'devin-pane.config.json') (Join-Path $devinState 'config.json') -Force
    $devinPane = (Invoke-Herdr pane split $right --direction down --cwd $Project --no-focus).result.pane.pane_id
    & herdr agent start "$Team-devin" --kind devin --pane $devinPane --timeout 120000 -- --model $devinModel --config "$devinState\config.json" --permission-mode accept-edits --export "$devinState\transcript.json"
}
& herdr agent list
