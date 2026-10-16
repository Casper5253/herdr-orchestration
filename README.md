# herdr-orchestration

`hx`: a small control plane for a three-agent coding team (Claude Code as lead, Codex, OpenCode) running side by side in Herdr panes. It routes work by subscription headroom, picks model and effort, tracks task ownership, and compacts agent context at task boundaries. It is standard-library Python plus a few hooks and skills, with no server or database.

## What's here
- `hx/`: the CLI. `hx usage` (budget per pool), `hx route` (worker, model, effort), `hx task` and `hx checkpoint` (ledger and handoff state), `hx context` and `hx compact` (protected compaction), `hx ocrun` (one-off worker in a visible window).
- `hooks/`: Claude Code status line, PreCompact and SessionStart(compact) hooks.
- `skills/`: `herdr-team` (collaboration contract) and `hx-router` (lead playbook), shared by all three agents.
- `team/`: Herdr team launcher and per-agent trust config. `install.ps1` installs it.
- `policy.json`: model ladders, pool caps and context limits.
- `bench/`: five-task model benchmark with validated graders. Results in `bench/RESULTS.md`.
- `docs/`: `REPORT.md`, `ARCHITECTURE.md`, `FINDINGS.md`, provider audits, and the article `paper/hx-paper.html`.

## Related
- [agent-memory](https://github.com/Casper5253/agent-memory): the shared Obsidian memory (session capture, project notes, `mem search`) described in the article.
- Skills reach all three agents through pstack by Lauren Tan, which is not part of this repo. `install.ps1` copies the two skills above into a local pstack install and runs its sync.

## Use
Windows, Python 3.11+, Herdr 0.9, and the three agent CLIs.

    python -m pytest tests -q
    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
    hx usage
    hx route --kind feature --size medium

`install.ps1` backs up every file it replaces as `*.bak-<timestamp>`. Paths in `team/claude-settings.json` point at this repo's location on the author's machine; adjust them for yours.
