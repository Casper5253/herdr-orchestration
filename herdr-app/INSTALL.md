# Install: Triage layout + hx usage bar

Everything installs into `~/.config/herdr-app`. The app rewrites its own files
(AGENTS.md, `*.d.ts`, `frame/`, shipped mods and the `main`/`terminals`/`win98`
layouts) on every start; everything copied here is personal and is kept.

Run from this folder (`...\herdr-orchestration\herdr-app`) in PowerShell:

```powershell
$dst = "$HOME\.config\herdr-app"
New-Item -ItemType Directory -Force "$dst\mods", "$dst\layouts" | Out-Null

# 1. Triage mods (vendored for review in vendor/herdr-triage)
Copy-Item -Recurse -Force "vendor\herdr-triage\mods\*" "$dst\mods\"

# 2. The usage bar
Copy-Item -Recurse -Force "mods\hx-usage" "$dst\mods\"

# 3. The Triage layout with the hx-usage slot (this file, NOT the vendor's copy)
Copy-Item -Force "layouts\triage.json" "$dst\layouts\triage.json"
Copy-Item -Force "vendor\herdr-triage\layouts\triage.svg" "$dst\layouts\triage.svg"

# 4. Triage's keys: copy when you have no keys.json yet; otherwise merge the
#    "layout:triage" section into yours by hand (see below).
if (-not (Test-Path "$dst\keys.json")) {
  Copy-Item "vendor\herdr-triage\keys.json" "$dst\keys.json"
}
```

(See "What changed" below if you prefer editing your own triage.json instead of
taking ours.)

## Keys to merge when `keys.json` already exists

```json
"layout:triage": {
  "cmd-shift-j": "triage.next-blocked",
  "cmd-shift-u": "triage.next-done",
  "cmd-shift-r": "layout.toggle:panel"
}
```

`cmd` is Ctrl on Windows.

## Verify and switch

```powershell
herdr-app check "$dst\mods\hx-usage"
Get-ChildItem "$dst\mods\triage*" -Directory | ForEach-Object { herdr-app check $_.FullName }
herdr-app do layout.switch:triage
```

Expected: a 28 px status bar. Left: "N need you / N done / N working" plus the
branch. Right: `opencode ~N% codex N% claude N% devin N/M · <agent> N% ...`,
refreshing every 60 s. `~` marks an estimated reading, `?` an unknown one or a
failed call; hover an entry for the detail. The boundary between the two halves
drags; `ctrl-alt-shift-escape` resets to the default layout if anything goes
wrong.

## What changed vs the vendored Triage

Only `layouts/triage.json`: the `status` node is now a `row` of two blocks —
`triage-status` (fills) and `hx-usage` (420 px, min 140, max 900, resizable).
Triage's own status bar is one `block` mod; it has no mod slot, so the row is
the slot. Every other Triage file installs unchanged.

## Windows notes

- `hx` must be on PATH (it is at `~/.local/bin` here). The bar shows `hx ?` if
  it cannot run.
- Triage's Issues and PR panels need `gh` signed in on a GitHub remote.
- `triage-worktrees` and `triage-launch` exec `sh`: install Git Bash (or put
  `sh.exe` on PATH) or their stats and worktree creation fail.
- "Open in browser" clicks use `xdg-open`, which Windows does not have; they
  fail quietly.
