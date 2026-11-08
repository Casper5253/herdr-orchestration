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

foreach ($file in (Get-Content -Raw (Join-Path $src 'install-files.json') | ConvertFrom-Json)) {
    $target = Join-Path $home_ $file.target
    New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
    Install-File (Join-Path $PSScriptRoot $file.source) $target
}

# Codex profile: make the identity line team-neutral and trust this repo. Other content (trust entries) is kept.
$profile_ = Join-Path $home_ '.codex\trusted-dev.config.toml'
Copy-Item $profile_ "$profile_.bak-$stamp"
$text = [IO.File]::ReadAllText($profile_, [Text.Encoding]::UTF8)
$text = $text.Replace('You are dev-codex in a trusted Herdr dev team (dev-claude, dev-codex, dev-opencode).',
    'You are the Codex member of a trusted Herdr dev team: <team>-claude (lead), <team>-codex (you) and <team>-opencode, where <team> is dev or hx. Your own name is the herdr agent list entry whose pane_id equals HERDR_PANE_ID; address only teammates with your own prefix.')
# Codex 0.163.0 fixed the elevated sandbox (#51634); drop the MXC workaround from the team profile.
$text = $text.Replace("[windows]`r`nsandbox = `"mxc`"`r`n", "").Replace("[windows]`nsandbox = `"mxc`"`n", "")
$text = $text.Replace("[features]`r`nprefer_mxc = true`r`n", "").Replace("[features]`nprefer_mxc = true`n", "")
$repoKey = "[projects.'" + $PSScriptRoot.ToLower() + "']"
if (-not $text.Contains($repoKey)) { $text += "`r`n$repoKey`r`ntrust_level = `"trusted`"`r`n" }
[IO.File]::WriteAllText($profile_, $text, (New-Object Text.UTF8Encoding $false))
Write-Host "updated $profile_"

# Skills: repo copies are the source; pstack sync distributes them to Claude, OpenCode and Codex.
$python = (Get-Command python).Source
& $python -I (Join-Path $home_ '.agents\pstack\sync.py')
& $python -I (Join-Path $home_ '.agents\pstack\sync.py') --check
if ($LASTEXITCODE -ne 0) { throw 'pstack sync --check failed' }

# hx on PATH (~/.local/bin is already on PATH): a .cmd for PowerShell/cmd and a shell script for Git Bash.
$bin = Join-Path $home_ '.local\bin'
New-Item -ItemType Directory -Force $bin | Out-Null
$repo = $PSScriptRoot
function Install-Text($to, $content) {
    # Same backup rule as Install-File; write to a temp file first so a failed write never leaves a half wrapper.
    $tmp = "$to.tmp-$stamp"
    [IO.File]::WriteAllText($tmp, $content, (New-Object Text.UTF8Encoding $false))
    if (Test-Path $to) { Copy-Item $to "$to.bak-$stamp" }
    Move-Item -Force $tmp $to
}
# The shims call one absolute Python: the WindowsApps python stub on PATH cannot run inside Codex's sandbox.
$hxPython = (& uv python find 3.11 2>$null)
if (-not $hxPython) { $hxPython = $python }
Install-Text (Join-Path $bin 'hx.cmd') "@echo off`r`nsetlocal`r`nset `"PYTHONPATH=$repo;%PYTHONPATH%`"`r`n`"$hxPython`" -m hx %*`r`n"
$repoPosix = $repo.Replace('\', '/')
$hxPythonPosix = $hxPython.Replace('\', '/')
Install-Text (Join-Path $bin 'hx') "#!/bin/sh`nPYTHONPATH=`"$repoPosix`${PYTHONPATH:+;`$PYTHONPATH}`" exec `"$hxPythonPosix`" -m hx `"`$@`"`n"
Write-Host "installed $bin\hx(.cmd)"

# One pointer line in the global Claude instructions, added once.
$claudeMd = Join-Path $home_ '.claude\CLAUDE.md'
$line = '- Routing, quota checks, task tracking and compaction for the team run through the `hx` CLI: follow the `hx-router` skill when leading.'
if (-not (Select-String -Path $claudeMd -SimpleMatch $line -Quiet)) {
    Copy-Item $claudeMd "$claudeMd.bak-$stamp"
    $text = [IO.File]::ReadAllText($claudeMd, [Text.Encoding]::UTF8)
    $text = $text.Replace("- Prefer working solo for independent, bounded tasks;", "$line`n- Prefer working solo for independent, bounded tasks;")
    [IO.File]::WriteAllText($claudeMd, $text, (New-Object Text.UTF8Encoding $false))
    Write-Host "updated $claudeMd"
}
