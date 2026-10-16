# hx v1: final report (2026-10-16)

Built by the `hx-*` Herdr team: hx-claude (lead), hx-codex (review, context controller), hx-opencode (most implementation), plus visible one-off OpenCode workers. Repo: `~/Documents/Projects/herdr-orchestration`. Evidence: `docs/FINDINGS.md`, `bench/RESULTS.md`, `docs/audit/`.

## 1. Architecture
A stdlib-only Python CLI, `hx`, plus two Claude hooks and two skills. No daemon, database or dashboard.
- `hx usage`: quota per pool. OpenCode spend per model per 5h/7d/30d window from the local opencode.db (estimated); Codex `rate_limits` from its rollout logs (measured); Claude from the status line (measured, team sessions only).
- `hx route`: deterministic, staged router (attempt cap → failure cause → pool → model ladder with fallback). Outputs worker, model, effort, variant, method, decision id.
- `hx task` / `hx checkpoint`: per-project ledger in `<repo>/.hx/` with file ownership, worktrees, session reservations, and agent-authored checkpoints validated against the ledger revision.
- `hx context decide|recover`, `hx compact`: protected compaction of persistent workers at task boundaries.
- `hx ocrun`: one-off worker runs in a visible PowerShell window, OpenCode (`--worker opencode`) or Codex (`--worker codex`); results classified from `opencode export` / Codex rollouts.
- Skills: `hx-router` (lead playbook), `herdr-team` (now works for any team prefix). Hooks: PreCompact (task state and compact guardrails), SessionStart(compact) (restores active tasks' next actions and checkpoints), status line (Claude context and quota).

## 2. Files and configuration changed
Repo: `hx/`, `hooks/`, `skills/`, `bench/`, `team/`, `install.ps1`, `policy.json`, `docs/`.
Installed by `install.ps1` (every replaced file backed up as `*.bak-<timestamp>`): `~/.herdr-team/*` (launcher with `-Team`/`-LeadPane`, Claude team settings with hooks and status line, OpenCode overlay with glm-5.3-flash), `~/.codex/rules/herdr-team.rules` (hx-* prompts, `python -m pytest|hx`, `hx`), `~/.codex/trusted-dev.config.toml` (team-neutral identity, repo trust), `~/.agents/pstack/local/{herdr-team,hx-router}` + sync, `~/.local/bin/hx(.cmd)`, one pointer line in `~/.claude/CLAUDE.md`.

## 3. Model-routing policy
OpenCode first: its allowances are per model, so the router walks a ladder and skips any model whose 5h, week or month window is above 85% or that hit a quota error in the last 30 minutes.
- economy: gpt-6-luna → glm-5.3-flash → qwen3.8-flash → mimo-v2.6-flash
- default: qwen3.7-plus → glm-5.3-flash
- strong: kimi-k2.7-code
- excluded: minimax-m3 (empty output on larger edits), deepseek (account region setting)
- review / high risk → Codex gpt-6.1-sol; architecture → Claude.
- OpenCode exhausted → Codex gpt-6-luna (`hx ocrun --worker codex`) or a Claude Sonnet subagent, whichever pool has more headroom; all exhausted → `defer` with the earliest reset.
- Missing context, unclear brief or tool failure → same worker and model, fix the input. Only a capability failure escalates, one step at a time. Max 2 correction rounds.
- Iterative work goes to the warm persistent OpenCode session when its model is in the chosen tier.

## 4. OpenCode benchmark (5 tasks, hidden checks; bench/RESULTS.md)
| model | pass | median s | cost for 5 |
|---|---|---|---|
| gpt-6-luna | 5/5 (one aborted run, retry passed) | 43 | $0.023 (+$0.006 retry) |
| mimo-v2.6-flash | 5/5 | 142 | $0.028 |
| qwen3.8-flash | 5/5 | 125 | $0.046 |
| glm-5.3-flash | 5/5 | 46 | $0.047 |
| minimax-m3 | 5/5 on bench, 4 of 6 real edits failed | 51 | $0.106 |
| qwen3.7-plus | 4/5 | 60 | $0.081 |
| kimi-k2.7-code | 4/5 | 106 | $0.273 |
| deepseek-v4.1-flash | 0/5 (account region setting) | n/a | n/a |
Same bugfix task on other pools at low effort: Codex gpt-5.6-luna pass (28s), Claude Sonnet subagent pass (7s).

## 5. Effort selection
Set at dispatch, never by reconfiguring a live session. low = discovery, mechanical edits, simple tests, docs; medium = normal implementation; high = architecture, hard bugs, review; max only after a capability failure at high. Real controls: Codex `model_reasoning_effort`, Claude subagent `effort`, OpenCode `--variant` only for models that declare variants (gpt-6-luna does). Measured: glm-5.3-flash declares variants but low vs high made no consistent difference, so for most OpenCode models effort is expressed by the tier.

## 6. Checkpointing and compaction
Workers write checkpoints (objective, decisions, exact errors, tests, next action); `hx task done` validates before closing and releases the session reservation. `hx compact <agent>` runs only at a completed task boundary: it reserves the session, validates the checkpoint, sends `/compact` through Herdr, verifies the same session came back smaller, and makes the worker acknowledge the checkpoint's next action with a nonce. A timeout or unverifiable outcome leaves `compact-unknown` and blocks dispatch until `hx context recover`. Mid-task pressure is left to native auto-compaction. The lead is compacted by Claude itself, with the PreCompact and SessionStart(compact) hooks carrying task state.

## 7. Tests and outcomes
232 unit tests pass (`python -m pytest tests -q`). Live checks:
- Communication: Claude↔Codex, Claude↔OpenCode in both directions (Codex↔OpenCode relied on the earlier validation).
- End-to-end on `e2e/todo-cli`: feature routed to the warm session (glm-5.3-flash) → implemented → Codex review (2 findings) → one correction round → 33 tests → merged → task closed with a valid checkpoint → **live compaction 310K → 31.6K tokens, same session** → follow-up task built by the compacted session reusing its earlier work (38 tests).
- Escalation live: minimax-m3 capability failure → kimi-k2.7-code succeeded.
- Fallback paths: forced OpenCode exhaustion routes to Codex gpt-6-luna; `hx ocrun --worker codex` ran live in a visible window and returned ok.
- Visible worker windows confirmed by the user.
- Live runs found bugs the unit tests missed: pane text parsed as JSON, ack match broken by TUI wrapping, hooks unable to import hx, installer mojibake. All fixed (FINDINGS 10-13).

## 8. Efficiency (measured or estimated)
- Kimi was the default worker: $17.10 over 584 calls in the last 7 days (57% of its weekly Go cap), about 138K cached tokens per call. The same benchmark tasks cost about 10x less on gpt-6-luna and 6x less on glm-5.3-flash.
- Same-size fix slices: qwen3.7-plus $0.094 vs kimi $0.53-0.60.
- Compaction cut the persistent worker's re-sent context by about 10x (310K → 31.6K).
- No before/after number for a whole week of work yet: that needs `.hx/routing.jsonl` from real use. Claude and Codex usage avoided is not measured.

## 9. Known limitations
- OpenCode quota is estimated from this machine's history only; there is no API for remaining Go allowance. Real 429/quota detection is unit-tested, never seen live.
- Codex and Claude headroom often reads `unknown` (Codex refreshes only when Codex runs; Claude only in team sessions with the status line). The router treats unknown as usable, not exhausted.
- Lead self-compaction is not proactive: the lead relies on Claude's auto-compact plus hooks. Whether a queued `/compact` to the lead's own pane runs after its turn is being tested last.
- The hooks swallow errors to protect the session; they should also log what they swallow.
- hx-claude in this session predates the install, so the hooks were tested by direct invocation, not by a real Claude compaction.
- The `.hx/` task record does not store method/model from `task assign` (null fields).
- Single-run benchmark cells on small tasks: good for excluding broken models, weak for ranking close ones.

## 10. Next opportunities
1. After 1-2 weeks of real use, `hx report` from `routing.jsonl`: success rate and cost per model and pool; re-tune ladders.
2. Lead self-compaction at task boundaries (depends on the queued-`/compact` test).
3. Re-benchmark on harder, multi-file tasks with 3 runs per cell; include gpt-6-luna variants low vs medium.
4. Log swallowed hook errors; store method/model on assign.
5. Decide on deepseek (needs the Global-regions privacy setting).
