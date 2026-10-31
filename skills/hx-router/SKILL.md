---
name: hx-router
description: "Lead playbook for routing work across the Herdr team with the hx CLI: pick worker, model, effort and route; check subscription headroom; track tasks; checkpoint and compact sessions. Use when leading a Herdr team (agents named <team>-<role>, e.g. dev-* or hx-*) on any non-trivial task, before delegating, when a worker fails, or when a session's context grows."
---

# hx router (lead playbook)

`hx` is a stdlib-only CLI: `hx <command>` (wrapper on PATH) or `python -m hx <command>`. Source and design: `~/Documents/Projects/herdr-orchestration` (`docs/ARCHITECTURE.md`, `docs/FINDINGS.md`, `bench/RESULTS.md`). Project state goes to `<repo>/.hx/` of the repo you run it in. Routing decisions are deterministic; spend judgment only where the router says so.

## Policy in one paragraph
**Until 2026-11-30 (promotion guard): Devin SWE-2 is the primary implementation worker.** SWE-2 is free on Devin Pro in the CLI, so `hx route` sends routine, investigate, feature, debug, refactor and test work to `ocrun-devin` while `hx usage` shows devin `available`. Don't hold SWE-2 back to save it; do keep briefs scoped and never retry blindly (free tokens are not free concurrency or rate limits). Otherwise: spend OpenCode Go first, because each OpenCode model has its own allowance (5h 20%, week 50%, month 100% of its cap) and it is the cheapest pool. Keep Claude for architecture, prose and judgment, and Codex for review, hard debugging and risky changes. A failure caused by missing context, an unclear brief or a tool problem is fixed by fixing the input, never by a bigger model. Only a capability failure escalates, one step at a time.

## Loop for each piece of work
1. **Check headroom** when a pool may be tight: `hx usage` (each value is tagged measured, estimated or unknown; unknown is not exhausted).
2. **Route**: `hx route --task-id <id> --kind <trivial|routine|investigate|feature|debug|refactor|test|review|architecture> [--risk high] [--size large] [--iterative] [--parallel] [--attempt N --last-failure <missing-context|unclear|tool|capability> --prev '<previous decision json>'] [--persistent '{"model":..,"effort":..,"status":..}']`. Output: worker, model, effort, method, reason, decision_id. Follow it unless you have a concrete reason; if you override, say why in the brief.
3. **Track**: `hx task add <id> --title .. --owner <agent> --files <owned paths> [--worktree <path>]`, then `hx task assign <id> --owner <agent> [--method persistent --agent <name>]`. It refuses overlapping file ownership and agents that are mid-compaction. Parallel workers get disjoint files or their own `git worktree`.
4. **Dispatch by method**:
   - `direct`: do it yourself (trivial; delegation costs more than the work).
   - `persistent`: herdr-team brief to the long-lived OpenCode session (iterative work, warm context).
   - `ocrun`: `hx ocrun --model <m> [--variant <v>] --cwd <repo or worktree> --prompt-file <brief.md> --task-id <id> --decision-id <from route>`: a one-off OpenCode run with a per-task model. It opens its own visible PowerShell window (the user watches workers; the window stays open) and hx reads the result from `opencode export` afterwards. `--headless` is for tests only. Write the brief to a file; keep the worker inside the repo (it auto-rejects outside paths).
   - `ocrun-devin`: `hx ocrun --worker devin --model <swe-2-high|swe-2-max|swe-2-medium> --cwd <repo or worktree> --prompt-file <brief.md> --task-id <id> --decision-id <id>`. One local `devin -p` run (visible window under Herdr) on a pinned SWE-2 id. It refuses before launch unless the model is an allowed SWE-2 id, the promotion is active and `devin models list` tags it Free; afterwards it checks that every turn ran on that id. It never uses Devin Cloud or `/handoff`. The worker may not commit, push, switch branches or reset (a denied command ends the run as `tool: permission denied: <cmd>`), so you commit after review. The result carries `final_message`, `changed_files`, `models_seen`. Failures: `policy` (refused: don't retry; tell the user if the promotion ended), `busy` (another run holds the slot; route says opencode meanwhile), `model-mismatch` (stop and report), `cancelled`, `tool`, `quota`, `exit`. Use `swe-2-medium` by hand for quick, straightforward edits where latency matters; `swe-2-max` is what route picks for hard work.
   - `ocrun-codex` / `subagent`: `codex exec -m <model> -c model_reasoning_effort=<e>` or a Claude subagent with `effort`, only when OpenCode is exhausted or unsuitable. Run `codex exec` in a visible window too (`Start-Process powershell -ArgumentList -NoExit,...`); a Claude subagent shows in Claude's task view. Codex `--json` emits `error` items that are only warnings (MXC preview notice, hook timeout clamp): judge by the result, not by those.
   - `defer`: every pool is exhausted; report the earliest reset instead of burning retries.
5. **Verify, never trust the report**: run the tests and read the diff yourself. Headless runs can exit 0 after an API error, a rejected permission or `finish=length` with no output; `hx ocrun` classifies these, so check its `ok` and `failure`, and never retry while `retry_safe` is false (the old worker may still be running). `ok` only means the run finished cleanly: still check the diff and the tests.
6. **On failure**: classify the cause, then re-route with `--attempt`, `--last-failure` and `--prev`. At most 2 correction rounds per task, then escalate to the user.
7. **Close**: the owner writes the checkpoint (`hx checkpoint write <id> --objective .. --decisions .. --errors <exact text or none> --tests '<cmd> -> <result>' --next ..`), then `hx task done <id>` (refuses to close on an invalid checkpoint; releases the task's reservations). A stranded reservation: `hx task release <id>`. Cross-family review (author ≠ reviewer) for anything risky.

## Devin SWE-2 (free until 2026-11-30T10:00Z, then refused until the user re-approves)
- `swe-2-high` default (route effort low/medium), `swe-2-max` for high/max effort, `swe-2-medium` by hand. Lightning variants are paid and never allowed. A capability failure on `swe-2-max` routes to Codex strong.
- Up to two live Devin sessions at a time (`policy.devin.max_concurrent`, measured 2026-10-31): one-off runs plus live `<team>-devin` panes. A run past the cap, or one admitted while Herdr's pane list cannot be read, is refused `busy` and route sends it to OpenCode. Parallel Devin runs need disjoint files or their own worktree, same as any worker.
- Persistent pane: start it with `Start-HerdrTeam.ps1 <repo> ... -Devin`. When it is idle or done and the task is `--iterative` (or `--persistent` names it), route returns `method: persistent-devin, agent: <team>-devin`. Brief it with `hx say devin` and track the task with `hx task assign <id> --method persistent-devin --agent <team>-devin`. Reusing the pane is never capped. It can run `hx say` and `hx checkpoint`, but not herdr, devin or the other hx commands. **A denied command cancels its turn**, so put the herdr syntax it needs in the brief and say which commands are denied. `hx context decide <team>-devin` reads its footer.
- Live acceptance check: `python -I tests/live_devin_acceptance.py` in the hx repo. Evidence and limits: `docs/audit/devin.md`.

## Models (OpenCode Go, from the 2026-10-16 benchmark)
- economy: `gpt-6-luna` (cheapest, 5/5 with one aborted run retried, real effort variants; small $15 cap), then `qwen3.8-flash`, `mimo-v2.6-flash`, `glm-5.3-flash` last (temporarily demoted 2026-10-22 after repeated stalls in real work).
- default: `qwen3.7-plus`, then `qwen3.8-flash`, then `glm-5.3-flash`. minimax-m3 is excluded: it burns its whole 32K reasoning budget and writes nothing on larger edits.
- strong: `kimi-k2.7-code` (escalation only; about 6x glm cost per task).
- persistent session model: `qwen3.7-plus` (temporarily, since 2026-10-22; was `glm-5.3-flash`).
- Codex fallback: economy `gpt-6-luna`, strong `gpt-6.1-sol` (`hx ocrun --worker codex --model <m> --effort <e>`). Claude fallback: a Sonnet subagent.
- excluded: deepseek models (account needs the Global-regions privacy setting; that is the user's decision).
Ladders, caps and thresholds live in `policy.json`. Re-run `bench/run.py` before changing defaults.

## Effort
Set it at dispatch, never by reconfiguring a live session. Real controls: `codex exec -c model_reasoning_effort=` (Luna at low used 155 reasoning tokens) and a Claude subagent's `effort`. For OpenCode, effort is mostly expressed by the model tier: `--variant` only matters for models that declare variants (`policy.json` `opencode_variants`); glm-5.3-flash declares low/high/max but showed no measurable difference (bench/RESULTS.md). low = discovery, mechanical edits, simple tests and docs; medium = normal implementation, refactors, moderate debugging; high = architecture, hard bugs, ambiguous or risky work, reviews; max = only after a capability failure at high.

## Context and compaction
- Before `hx say` sends a message to a worker, it samples that worker's context telemetry. At or above `pressure_threshold`, stderr names the utilisation and `hx compact <agent>` command; the message still sends. Unknown or stale telemetry does not block communication.
- At every task boundary, for every worker of any role or model: `hx context decide <agent>`. Claude workers are found by pane through the status line, Codex and OpenCode workers through their session logs. If it says compact, run `hx compact <agent> --task <id>`. It takes the session lease, validates the checkpoint, sends `/compact` through Herdr, verifies the same session came back smaller, and makes the worker acknowledge the checkpoint.
- An idle worker at or above `pressure_threshold` (default 0.75) can compact during a registered open task after writing a valid checkpoint. The task remains open and all lease, ownership, cooldown, per-task limit, session and verification guards still apply. If no hx task is registered, `hx context decide` returns `compact-needed` with a reason instead of authorising compaction. `boundary_threshold` (default 0.40) continues to trigger compaction at completed task boundaries. Busy agents are never compacted by hx.
- If a compaction outcome is unknown (timeout), the agent stays reserved: `hx context recover <agent>` once it is idle again. Do not retry compaction for that task.
- Never send `/compact` from Git Bash: MSYS rewrites it into a Windows path. hx sends it from Python.
- At a boundary, run `hx context decide <your name> --session <claude-session-id>` for your Claude lead. Herdr's session id takes precedence; `--session` is the fallback. hx never chooses the newest status file for this decision. When the action is `self-compact`, follow the returned `steps`: write `hx checkpoint write lead --owner <your name> --objective .. --decisions .. --errors .. --tests .. --next ..`, start the background PowerShell command, and end the turn. The lead checkpoint requires no ledger task. After compaction resumes, run `hx context self-compacted <your name> --session <claude-session-id>` to record the loop guard. A decision alone does not record it. `hx compact` does not execute self-compaction. Claude's native auto-compaction remains the safety net.
- Durable decisions belong in agentmem (`mem remember`), not in `.hx/`.

## Don't
- Don't route trivial work through agents, or invent work to burn allowance.
- Don't escalate on a context or brief problem.
- Don't let two agents edit the same files outside separate worktrees.
- Don't answer a worker's approval prompt or send keys for the user.
