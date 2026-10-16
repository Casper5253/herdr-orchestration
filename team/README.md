# Herdr trusted dev team

## Launch (from any Herdr pane)
    powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.herdr-team\Start-HerdrTeam.ps1" C:\path\to\repo
This opens a workspace with three panes: dev-claude (lead, left), dev-codex (top right) and dev-opencode (bottom right).
In a new repo, answer the "trust this folder" prompt in any pane that shows one.
Talk to dev-claude: give it the feature request. Mention "herdr-team" or start the message with [team] if it does not pick up the skill on its own.

## How the agents talk
`herdr agent prompt dev-<name> '[team from dev-<me>] ...'` sends a message; the assignee replies the same way. The contract lives in the herdr-team skill
(~/.claude/skills/herdr-team, ~/.codex/skills/herdr-team; OpenCode reads the Claude copy).

## Automatic vs. needs you
Automatic: Herdr list/get/read/wait/status; prompts between dev-* agents; editing code, running tests and making local commits inside the repo.
Needs you: Herdr send-keys, pane run/send-text/close, workspace/server close or stop; anything outside the repo, credentials, network, installs, push/deploy, destructive git, paid services.

## Files (each one can be deleted on its own to roll back)
- ~/.herdr-team/                       launcher, Claude settings, OpenCode overlay, this README
- ~/.codex/rules/herdr-team.rules      Codex Herdr command rules
- ~/.codex/trusted-dev.config.toml     Codex profile (MXC, FDE routing suspended in team sessions only)
- ~/.claude/skills/herdr-team/, ~/.codex/skills/herdr-team/
Full rollback: delete all of the above. Nothing else was changed (~/.codex/rules/default.rules is back to the original).

Two-agent mode (Claude + Codex only): add -NoOpenCode to the launch command.
