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
OpenCode first. **Correction (16 Oct, FINDINGS 16): OpenCode Go is one shared pool** of about $60/month ($12 per 5h, $30 per week), not per-model allowances. The router reads availability from the pool, walks the ladder cheapest-capable first, and skips a model only for its own recent quota error. When the pool is above 85% it falls back to Codex gpt-6-luna or a Claude Sonnet subagent. At the time of writing the week is at 88%, so routine work is already routed to Codex.
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
Workers write checkpoints (objective, decisions, exact errors, tests, next action); `hx task done` validates before closing and releases the session reservation. `hx compact <agent>` runs only at a completed task boundary: it reserves the session, validates the checkpoint, sends `/compact` through Herdr, verifies the same session came back smaller, and makes the worker acknowledge the checkpoint's next action with a nonce. A timeout or unverifiable outcome leaves `compact-unknown` and blocks dispatch until `hx context recover`. Mid-task pressure: since 7592384 (2026-10-22), `hx context decide` returns `compact` for an idle worker at or above `pressure_threshold` (0.75) even mid-task, with the same guards, and `compact-needed` when no hx task is registered; `hx say` warns when the target is above the threshold. Native auto-compaction is not a safety net for OpenCode: it fires near the 1M native window, far above hx's 200K effective limit (finding below, section 9). The lead is compacted by Claude itself, with the PreCompact and SessionStart(compact) hooks carrying task state.

## 7. Tests and outcomes
240 unit tests pass (`python -m pytest tests -q`). Live checks:
- Communication: all six directions verified on the hx team (Claude↔Codex, Claude↔OpenCode, Codex↔OpenCode, each by a direct `herdr agent prompt`).
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

## 8b. Codex sandbox
Team Codex now runs CLI 0.163.0 with the normal elevated sandbox; 0.163.0 contains the fix for the 0.162.x regression (verified side by side). The MXC workaround is removed from the team profile; the desktop app's own Codex is untouched.

## 9. Known limitations
- OpenCode quota is estimated from this machine's history only (it matched the vendor dashboard within ~2 points on 16 Oct); there is no API for remaining Go allowance. Real 429/quota detection is unit-tested, never seen live.
- Codex and Claude headroom often reads `unknown` (Codex refreshes only when Codex runs; Claude only in team sessions with the status line). The router treats unknown as usable, not exhausted.
- Lead self-compaction works (finding 19): a deferred background `herdr agent prompt <lead> '/compact'` runs once the lead's turn ends, and the session resumes from the hook summary. The hx-router skill now tells the lead to do this at task boundaries.
- The hooks swallow errors to protect the session; they should also log what they swallow.
- hx-claude in this session predates the install, so the hooks were tested by direct invocation, not by a real Claude compaction.
- The `.hx/` task record does not store method/model from `task assign` (null fields).
- Compaction is still pull-only: nothing runs `hx context decide` unless the lead does. On 2026-10-22 (portfolio project) dev-opencode on glm-5.3-flash reached 245K tokens (utilisation 1.23), went idle mid-task and stopped following briefs, because the lead dispatched with `hx say` without `hx task add` and never ran `decide`. 7592384 adds the pressure rule and the `hx say` warning; it does not add an automatic watcher.
- Single-run benchmark cells on small tasks: good for excluding broken models, weak for ranking close ones.

## 10. Next opportunities
1. After 1-2 weeks of real use, `hx report` from `routing.jsonl`: success rate and cost per model and pool; re-tune ladders.
2. Re-benchmark on harder, multi-file tasks with 3 runs per cell; include gpt-6-luna variants low vs medium.
3. Log swallowed hook errors; store method/model on assign.
4. Decide on deepseek (needs the Global-regions privacy setting).
5. Test-suite cleanup: `python -m pytest -q` from the repo root fails at collection (23 errors on 2026-10-22) because archived bench test modules share names and `e2e/todo-cli` imports its own package; only `python -m pytest -q tests` runs. Add `testpaths = ["tests"]` (or `--ignore` for `bench/` and `e2e/`) to the pytest config so the root command works, and make sure CI and the docs use it.
6. Make compaction harder to forget: have `hx say` refuse a new task (not just warn) to a worker above `pressure_threshold` unless `--force`, or offer an opt-in `hx context watch` loop that runs `decide` and `compact` for idle workers. Measure on real use first; the warning alone may be enough.
7. Live-verify the pressure path end to end on a real OpenCode session above 0.75 (only unit-tested so far).

## 11. Addendum 2026-10-31: Devin SWE-2 worker
Status: live. Design in `docs/ARCHITECTURE.md` ("Devin SWE-2 worker"), provider evidence in `docs/audit/devin.md`.

- **What changed.** `hx ocrun --worker devin` runs one local `devin -p` on a pinned SWE-2 id (`swe-2-high` default, `swe-2-max` for high effort, `swe-2-medium` by hand). `hx route` sends routine, investigate, feature, debug, refactor and test work there while `hx usage` shows devin `available`; OpenCode is the fallback. Review, architecture, high risk and trivial routing are unchanged.
- **Cost guard.** SWE-2 is free on self-serve plans in Devin CLI/Desktop "through November 30, 2026" (docs.devin.ai/desktop/models), and Cloud is excluded. hx stops routing to Devin at 2026-11-30T10:00Z (the earliest timezone reading) and refuses runs unless `devin models list` tags the model `Free`. Every turn's model is checked in the export. Lightning variants (billed) are never allowed, there is no fallback model, and `--cloud`/`/handoff` are never used. Moving the end date needs renewed verification and the owner's approval.
- **Verified live (2026-10-31, Devin CLI 3000.11.234, Devin Pro).** `tests/live_devin_acceptance.py` 19/19: CLI and login, the three SWE-2 ids tagged Free, Lightning/SWE-1.7/Claude refused before launch, ended promotion refused by ocrun and skipped by route, a small task implemented on swe-2-high and confirmed by the lead's own test run, `git push` denied and classified, a timeout that kills the process tree, outcome lines with provider. Two concurrent runs in separate repos both succeeded; a third was refused `busy` (`max_concurrent` 2). Questlog MCP works through OpenCode's token.
- **Demonstration.** The lead routed `devin-tests` (`hx route --kind test` → `ocrun-devin swe-2-high`) and dispatched it in a visible window. Devin wrote 58 unit tests in 644 s (1.24M tokens, 1.10M cached, every turn on swe-2-high). The lead re-ran the suite and closed the task with a checkpoint. The live run exposed a classifier false positive that the synthetic tests missed: a file view of `hx/devin.py` matched the rejection words. It is fixed, with a regression test. Unit suite: 397 passed after the review round (section below).
- **Not verified.** What a Devin quota or rate-limit error looks like (classified from output text, never seen). Server-side concurrency limits beyond two runs. Whether free SWE-2 use shows on the account's usage page; check app.devin.ai after a day of use. Live OpenCode and Codex worker runs were not re-run; their code paths are unchanged and their unit tests pass.
- **Windows limits.** Devin's `--sandbox` is macOS/Linux only, so exec runs with the user's rights. The worker config denies destructive and outward commands (push, reset, clean, rebase, checkout, restore, switch, commit, recursive deletes, `gh`, agent CLIs). A denied command ends the run.
- **Review round.** A cross-family review by Devin SWE-2 Max (892 s, read-only; Codex was at 77% of its 5h window) found 2 high, 6 medium and 9 low issues. Most were fixed (details in `docs/ARCHITECTURE.md`): a truncated run could report ok, slot races, crash-on-malformed policy/export, secret env inheritance, a missing-cwd fallthrough in run.ps1, and shared run directories. Devin SWE-2 High wrote the 27 regression tests. A live deny-rule probe found that `devin.exe` slipped past `Exec(devin)` (fixed) and that denying `git -C` breaks routine reads (rule removed; residual risk documented in `docs/audit/devin.md`).
