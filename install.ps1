# Installs team/ into the live locations. Every replaced file is backed up next to itself as *.bak-<timestamp>.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot 'team'
$home_ = $env:USERPROFILE
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function Install-File($from, $to) {
    if (Test-Path $to) { Copy-Item $to "$to.bak-$stamp" }
    Copy-Item $from $to -Force
    Write-Host "installed $to"
}

New-Item -ItemType Directory -Force (Join-Path $home_ '.herdr-team') | Out-Null
foreach ($f in 'Start-HerdrTeam.ps1', 'claude-settings.json', 'opencode-trusted-dev.jsonc', 'README.md') {
    Install-File (Join-Path $src $f) (Join-Path $home_ ".herdr-team\$f")
}
Install-File (Join-Path $src 'herdr-team.rules') (Join-Path $home_ '.codex\rules\herdr-team.rules')

# Codex profile: make the identity line team-neutral and trust this repo. Other content (trust entries) is kept.
$profile_ = Join-Path $home_ '.codex\trusted-dev.config.toml'
Copy-Item $profile_ "$profile_.bak-$stamp"
$text = Get-Content -Raw $profile_
$text = $text.Replace('You are dev-codex in a trusted Herdr dev team (dev-claude, dev-codex, dev-opencode).',
    'You are the Codex member of a trusted Herdr dev team: <team>-claude (lead), <team>-codex (you) and <team>-opencode, where <team> is dev or hx. Your own name is the herdr agent list entry whose pane_id equals HERDR_PANE_ID; address only teammates with your own prefix.')
$repoKey = "[projects.'" + $PSScriptRoot.ToLower() + "']"
if (-not $text.Contains($repoKey)) { $text += "`r`n$repoKey`r`ntrust_level = `"trusted`"`r`n" }
[IO.File]::WriteAllText($profile_, $text, (New-Object Text.UTF8Encoding $false))
Write-Host "updated $profile_"
