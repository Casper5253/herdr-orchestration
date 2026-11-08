# herdr-orchestration

`hx`: a small control plane for a three-agent coding team (Claude Code as lead, Codex, OpenCode) running side by side in Herdr panes. It routes work by subscription headroom, picks model and effort, tracks task ownership, and compacts agent context at task boundaries. It is standard-library Python plus a few hooks and skills, with no server or database.

## What's here
- `hx/`: the CLI. `hx usage` (budget per pool), `hx route` (worker, model, effort), `hx task` and `hx checkpoint` (ledger and handoff state), `hx context` and `hx compact` (protected compaction), `hx ocrun` (one-off OpenCode, Codex or Devin SWE-2 worker in a visible window).
- Devin SWE-2 worker (free until 30 Nov 2026): `hx ocrun --worker devin --model swe-2-high --cwd <repo> --prompt-file brief.md`. Design in `docs/ARCHITECTURE.md` (Devin section), evidence in `docs/audit/devin.md`, worker permissions in `team/devin-worker.config.json`, live check `python -I tests/live_devin_acceptance.py`.
- `hooks/`: Claude Code status line, PreCompact and SessionStart(compact) hooks.
- `skills/`: `herdr-team` (collaboration contract) and `hx-router` (lead playbook), shared by all three agents.
- `team/`: Herdr team launcher and per-agent trust config (`-Devin` adds a Devin SWE-2 pane). `install.ps1` installs it.
- `policy.json`: model ladders, pool caps and context limits.
- `bench/`: five-task model benchmark with validated graders. Results in `bench/RESULTS.md`.
- `docs/`: `REPORT.md`, `ARCHITECTURE.md`, `FINDINGS.md`, provider audits, and the article `paper/hx-paper.html`.

## Related
- [agent-memory](https://github.com/Casper5253/agent-memory): the shared Obsidian memory (session capture, project notes, `mem search`) described in the article.
- Skills reach all three agents through [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, which is not part of this repo. `install.ps1` copies the two skills above into a local pstack install and runs its sync.

## Use
Windows, Python 3.11+, Herdr 0.9, and the three agent CLIs.

    python -m pytest tests -q
    powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
    hx usage
    hx route --kind feature --size medium

`install.ps1` backs up every file it replaces as `*.bak-<timestamp>`. Paths in `team/claude-settings.json` point at this repo's location on the author's machine; adjust them for yours.

## Context boundaries

`hx task done <id>` validates the completion checkpoint, runs the owning agent's context decision, and returns it in `boundary`. For pane work without a ledger task, run `hx boundary <agent>`. Both append a disposition to the project's `.hx/context-boundaries.jsonl`: `keep`, `defer`, `unknown`, `refused`, or `recovery-needed`, with session telemetry and `readiness` (`ready` or `held`). These operations recommend recovery; they do not execute compaction.

`hx say` holds worker messages at or above the policy's pressure threshold and exits 1 with recovery commands. Use `hx say --force <agent> '<message>'` for intentional recovery/control messages. Reports to the lead remain reachable. Unknown or stale telemetry retains the existing send behavior.

An idle pane without a registered task gets concrete recovery steps: register and checkpoint the work before protected native compaction, or save a handoff and use a fresh owner when native compaction is unverified (Devin). No working agent is cleared automatically.

For the Claude lead, use a stable work-item key, for example `hx boundary dev-claude --work-item release-203`. Pass the same key to `hx context self-compacted dev-claude --session <session> --work-item release-203` after following the returned steps. A task boundary uses its task id; otherwise the saved lead checkpoint's Objective supplies a stable fallback key. Without either, the decision asks for `--work-item`. The loop guard is per item; the cooldown remains per session.

Journal write failures are reported as command failures. Task completion has already saved its validated checkpoint/status at that point; retry the boundary operation to record the disposition after repairing storage.
