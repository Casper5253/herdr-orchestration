---
name: hx-router
description: "Lead playbook for routing work across the Herdr team with the hx CLI: pick worker, model, effort and route; check subscription headroom; track tasks; checkpoint and compact sessions. Use when leading a Herdr team (dev-* or hx-*) on any non-trivial task, before delegating, when a worker fails, or when a session's context grows."
---

# hx router (lead playbook)

`hx` is a stdlib-only CLI: `hx <command>` (wrapper on PATH) or `python -m hx <command>`. Source and design: `~/Documents/Projects/herdr-orchestration` (`docs/ARCHITECTURE.md`, `docs/FINDINGS.md`, `bench/RESULTS.md`). Project state goes to `<repo>/.hx/` of the repo you run it in. Routing decisions are deterministic; spend judgment only where the router says so.

## Policy in one paragraph
Spend OpenCode Go first, because each OpenCode model has its own allowance (5h 20%, week 50%, month 100% of its cap) and it is the cheapest pool. Keep Claude for architecture, prose and judgment, and Codex for review, hard debugging and risky changes. A failure caused by missing context, an unclear brief or a tool problem is fixed by fixing the input, never by a bigger model. Only a capability failure escalates, one step at a time.

## Loop for each piece of work
1. **Check headroom** when a pool may be tight: `hx usage` (each value is tagged measured, estimated or unknown; unknown is not exhausted).
2. **Route**: `hx route --task-id <id> --kind <trivial|routine|investigate|feature|debug|refactor|test|review|architecture> [--risk high] [--size large] [--iterative] [--parallel] [--attempt N --last-failure <missing-context|unclear|tool|capability> --prev '<previous decision json>'] [--persistent '{"model":..,"effort":..,"status":..}']`. Output: worker, model, effort, method, reason, decision_id. Follow it unless you have a concrete reason; if you override, say why in the brief.
3. **Track**: `hx task add <id> --title .. --owner <agent> --files <owned paths> [--worktree <path>]`, then `hx task assign <id> --owner <agent> [--method persistent --agent <name>]`. It refuses overlapping file ownership and agents that are mid-compaction. Parallel workers get disjoint files or their own `git worktree`.
4. **Dispatch by method**:
   - `direct`: do it yourself (trivial; delegation costs more than the work).
   - `persistent`: herdr-team brief to the long-lived OpenCode session (iterative work, warm context).
   - `ocrun`: `hx ocrun --model <m> [--variant <v>] --cwd <repo or worktree> --prompt-file <brief.md> --task-id <id> --decision-id <from route>`: a one-off OpenCode run with a per-task model. It opens its own visible PowerShell window (the user watches workers; the window stays open) and hx reads the result from `opencode export` afterwards. `--headless` is for tests only. Write the brief to a file; keep the worker inside the repo (it auto-rejects outside paths).
   - `ocrun-codex` / `subagent`: `codex exec -m <model> -c model_reasoning_effort=<e>` or a Claude subagent with `effort`, only when OpenCode is exhausted or unsuitable. Run `codex exec` in a visible window too (`Start-Process powershell -ArgumentList -NoExit,...`); a Claude subagent shows in Claude's task view. Codex `--json` emits `error` items that are only warnings (MXC preview notice, hook timeout clamp): judge by the result, not by those.
   - `defer`: every pool is exhausted; report the earliest reset instead of burning retries.
5. **Verify, never trust the report**: run the tests and read the diff yourself. Headless runs can exit 0 after an API error, a rejected permission or `finish=length` with no output; `hx ocrun` classifies these, so check its `ok` and `failure`, and never retry while `retry_safe` is false (the old worker may still be running). `ok` only means the run finished cleanly: still check the diff and the tests.
6. **On failure**: classify the cause, then re-route with `--attempt`, `--last-failure` and `--prev`. At most 2 correction rounds per task, then escalate to the user.
7. **Close**: the owner writes the checkpoint (`hx checkpoint write <id> --objective .. --decisions .. --errors <exact text or none> --tests '<cmd> -> <result>' --next ..`), then `hx task done <id>` (refuses to close on an invalid checkpoint; releases the task's reservations). A stranded reservation: `hx task release <id>`. Cross-family review (author ≠ reviewer) for anything risky.

## Models (OpenCode Go, from the 2026-10-16 benchmark)
- economy: `glm-5.3-flash` (fast, 5/5), then `qwen3.8-flash`, `mimo-v2.6-flash`.
- default: `minimax-m3`, `qwen3.7-plus`. minimax-m3 can burn its whole 32K reasoning budget and write nothing on large edits: give it bounded tasks.
- strong: `kimi-k2.7-code` (escalation only; about 6x glm cost per task).
- persistent session model: `glm-5.3-flash`.
- excluded: deepseek models (account needs the Global-regions privacy setting; that is the user's decision).
Ladders, caps and thresholds live in `policy.json`. Re-run `bench/run.py` before changing defaults.

## Effort
Set it at dispatch, never by reconfiguring a live session. Real controls: `codex exec -c model_reasoning_effort=` (Luna at low used 155 reasoning tokens) and a Claude subagent's `effort`. For OpenCode, effort is mostly expressed by the model tier: `--variant` only matters for models that declare variants (`policy.json` `opencode_variants`); glm-5.3-flash declares low/high/max but showed no measurable difference (bench/RESULTS.md). low = discovery, mechanical edits, simple tests and docs; medium = normal implementation, refactors, moderate debugging; high = architecture, hard bugs, ambiguous or risky work, reviews; max = only after a capability failure at high.

## Context and compaction
- At a task boundary: `hx context decide <agent>`. If it says compact, run `hx compact <agent> --task <id>`. It takes the session lease, validates the checkpoint, sends `/compact` through Herdr, verifies the same session came back smaller, and makes the worker acknowledge the checkpoint. It never compacts mid-task; native auto-compaction is the safety net under pressure.
- If a compaction outcome is unknown (timeout), the agent stays reserved: `hx context recover <agent>` once it is idle again. Do not retry compaction for that task.
- Never send `/compact` from Git Bash: MSYS rewrites it into a Windows path. hx sends it from Python.
- You, the lead, are compacted by Claude Code itself: the PreCompact hook keeps task ids, exact errors and next actions, and the SessionStart(compact) hook re-injects the active tasks and newest checkpoint.
- Durable decisions belong in agentmem (`mem remember`), not in `.hx/`.

## Don't
- Don't route trivial work through agents, or invent work to burn allowance.
- Don't escalate on a context or brief problem.
- Don't let two agents edit the same files outside separate worktrees.
- Don't answer a worker's approval prompt or send keys for the user.
