# hx: routing, usage and context control for the Herdr team

Status: v1 design, 2026-10-16. Evidence for every provider fact is in `docs/audit/`.

## Goals and non-goals

Maximise useful work across three separate subscriptions. OpenCode Go is spent first, Claude and Codex are kept for judgment, review and escalation. Routine routing, usage checks, checkpointing and compaction run as deterministic scripts; a model is only asked when judgment is needed.

Non-goals: no daemon, database, broker, dashboard or vector store. Python stdlib only. Everything is a CLI command (`python -m hx ...`) that the lead, a hook or a worker calls.

## Key facts that shape the design

| Fact | Source | Consequence |
|---|---|---|
| OpenCode Go uses a **shared pool across models**, estimated at $12/5h, $30/week, $60/30d | User dashboard and local spend calibration, 2026-10-16 correction below | Model rotation does not restore headroom; use Codex/Claude when the pool reaches its soft limit |
| OpenCode stores every assistant message with `modelID`, `cost`, `tokens` in `~/.local/share/opencode/opencode.db` | measured | Per-model window spend is computable locally (estimate: this machine only) |
| `opencode run -m <model> --variant <v> --format json` | audit/opencode.md | Per-task model + effort for headless OpenCode workers |
| Codex rollout JSONL `token_count` events carry `rate_limits` (5h and weekly `used_percent`, `resets_at`) and context tokens | audit/codex.md | Codex quota and context usage are readable without a model call |
| `codex exec -m <id> -c model_reasoning_effort=<e>` | audit/codex.md | Per-task Codex model + effort |
| Claude status line JSON has `context_window.used_percentage` and `rate_limits.five_hour/seven_day` | audit/claude.md | A status line script snapshots Claude quota + context to a state file |
| Claude hooks: `PreCompact` (stdout = extra compact instructions), `SessionStart` matcher `compact` (re-inject) | audit/claude.md | Checkpoint before, restore after, for the lead |
| All three TUIs accept `/compact` typed as a prompt; `herdr agent prompt <name> '/compact'` delivers it | to be verified (V-1) | One native compaction path for persistent workers, no custom summariser |

## Components

```
hx/            python package, stdlib only, entry: python -m hx <command>
  usage.py     provider availability + quota snapshot (measured | estimated | unknown)
  route.py     deterministic router: task -> worker, model, effort, method
  ledger.py    task ledger, file ownership, routing log, outcomes
  checkpoint.py task checkpoints (write, validate, latest)
  context.py   context controller: per-agent utilisation, compaction decision + execution
  ocrun.py     one-off worker runs: OpenCode, Codex, Devin (logs tokens, cost, latency)
  devin.py     Devin SWE-2 worker: preflight, promotion guard, run slots, export checks
policy.json    every tunable: model ladders, plan limits, thresholds, retry caps
hooks/         claude statusline writer, PreCompact checkpoint, SessionStart(compact) restore
bench/         OpenCode model benchmark: fixtures with hidden tests + runner
skills/hx-router/SKILL.md   lead playbook (installed through pstack local + sync)
tests/         pytest, no network, no real model calls
```

State locations:
- Machine state: `~/.herdr-team/state/` (`claude-status.json`, `compact-guard.json`).
- Project state: `<repo>/.hx/` (git-ignored): `ledger.json`, `routing.jsonl`, `checkpoints/<task-id>.md`.
- Durable project memory (layer A) stays in agentmem (`mem remember`, project notes). hx does not duplicate it; `hx checkpoint --promote` hands a decision to `mem remember`.

## 1. Usage checker (`hx usage`)

Output: one JSON object, one entry per pool, each value tagged `measured`, `estimated` or `unknown`.

**Correction, 2026-10-16:** the earlier per-model limit assumption was wrong. The user dashboard showed 82% of 5h and 90% of week while local spend across all models was $9.70 and $26.46. Shared Go caps are estimated at $12/5h, $30/7d and $60/30d. Go Plus caps in policy are an unverified 4x estimate. The old per-model allowance table is retained only as historical documentation and is never read as a routing limit.

- **opencode**: sum assistant-message `cost` across **all models**, including models outside the ladders, over rolling 5h, 7d and 30d windows. Report `pool.windows` with spend, cap, remaining and fractional utilisation from `policy.opencode_pool[active_plan]`; tag `estimated` (local machine only). Per-model spend and last error remain informational, without per-model caps. Snapshot age measures the database read; newest message age is separate. Read only the `message` table, opened read-only. Never touch `account`, `credential` or `control_account`.
- **codex**: newest rollout JSONL under `~/.codex/sessions`; last `token_count` event: `rate_limits.primary/secondary.used_percent`, `resets_at`, sample timestamp. Tag `measured` with its age.
- **claude**: `~/.herdr-team/state/claude-status.json` written by the status line hook. Tag `measured` with its age; `unknown` if missing or older than 1h.
- **agents**: `herdr agent list` filtered to the team prefix: status per agent.

A pool is `available` when every window is below `policy.thresholds.pool_soft_limit` (default 0.85) and the agent (if persistent) exists.

## 2. Router (`hx route`)

Input (flags or JSON): `--kind` one of `trivial | routine | investigate | feature | debug | refactor | test | review | architecture`, `--risk low|med|high`, `--size small|large`, `--attempt N`, `--last-failure missing-context|tool|capability|unclear|none`, `--parallel`.

Output: `{worker, model, effort, method, reason}`; `method` is `direct | ocrun | persistent | subagent`. Every call appends to `.hx/routing.jsonl`.

Rules, first match wins:
1. `trivial` -> `direct` (lead does it).
2. `last-failure` in `missing-context | unclear | tool` -> same worker and model, effort unchanged, reason says "fix the input, not the model". Only `capability` failures escalate.
3. `review`, `architecture`, `risk=high` -> Codex (`review`, `debug` hard) or Claude (`architecture`, prose). Effort `high`.
4. Everything else -> OpenCode. `size=large` or iterative work -> `persistent` (hx-opencode); bounded or `--parallel` -> `ocrun` (headless, per-task model).
5. **Shared-pool correction, 2026-10-16:** OpenCode availability comes from the shared 5h/7d/30d windows. Within an available pool, walk the effort-tier ladders in order, cheapest capable model first. Only that model's own quota/rate-limit error in the last 30 minutes skips it; model spend does not grant or remove headroom. A warm session does not override ladder order. Escalate one tier per `capability` failure.
6. If any OpenCode shared window reaches the soft limit, or all candidate models have recent quota/rate-limit errors -> Codex economy (`gpt-6-luna`) or a Claude Sonnet subagent, whichever fallback pool has more headroom.
7. `attempt > policy.max_attempts` (default 3) -> Claude/Codex strong model with effort `high`, then stop and report.

Effort tiers (initial; refine from `routing.jsonl` outcomes):
- `low`: discovery, classification, mechanical edits, simple tests, docs.
- `medium`: standard implementation, moderate debugging, refactors.
- `high`: architecture, hard bugs, ambiguous requirements, high-risk changes, reviews.
- `max`: only after a `capability` failure at `high`.

Effort is set at dispatch: `opencode run --variant`, `codex exec -c model_reasoning_effort=`, Claude subagent `effort`. Persistent sessions are not reconfigured mid-life.

## 3. Ledger (`hx task`)

`.hx/ledger.json`: tasks `{id, title, owner, model, effort, method, files[], depends[], status, review, attempts, created, updated, checkpoint}`. Status: `open -> assigned -> in_progress -> review -> done | failed | blocked`.

- `hx task add|assign|status|done|list|show`.
- `assign` refuses when any listed file is owned by another open task (conflict), unless `--worktree` is given (parallel work in its own worktree).
- `done` and `failed` write the outcome into the routing log entry and trigger `hx checkpoint` for that task.
- Atomic writes (temp file + replace) with a lock file, so two agents can update the ledger without corruption.

## 4. Checkpoints (`hx checkpoint`)

`.hx/checkpoints/<task-id>.md` with fixed sections: Objective, Owner, Status, Files changed (from `git status --porcelain` + `git diff --stat` against the task base), Decisions, Exact errors, Tests (command -> result), Next action, References. The script fills the git-derived sections; the agent supplies the rest via flags or stdin.

`hx checkpoint validate <id>` checks: every section present, Next action non-empty, file list matches `git status`, mtime newer than the last ledger update. Compaction is refused unless validate passes.

Triggers: task done/failed (ledger), Claude `PreCompact` hook, before `hx compact`, and by hand after an important discovery.

## 5. Context controller (`hx context`, `hx compact`)

Utilisation per agent (deterministic):
- Claude lead: `claude-status.json` `used_percentage`.
- Codex: last `token_count` `last_token_usage.input_tokens / model_context_window` in the newest rollout for the hx-codex cwd.
- OpenCode persistent: latest assistant message of the hx-opencode session: `tokens.input + tokens.cache.read` / model context limit (from `opencode models --verbose` or policy).

Decision `hx context decide <agent>` returns `compact | compact-needed | keep` with a reason:
- `compact` at a natural boundary (agent idle, its task just went `done`, next task unrelated) when utilisation >= `boundary_threshold` (default 0.40).
- `compact` under pressure when utilisation >= `pressure_threshold` (default 0.75) and the agent is idle, even while its registered task remains in progress. Pressure overrides the warm-cache preference for related work.
- `compact-needed` under pressure when the agent has no registered hx task; register a task and write a checkpoint before running `hx compact`.
- `keep` when an agent is busy, telemetry is unknown or stale, a lease or compaction guard blocks it, or an in-progress task is below the pressure threshold.

Execution `hx compact <agent>`:
1. Guard: agent idle (`herdr agent get`), not compacted in the last `min_interval_min` (default 20), fewer than `max_per_task` (2) compactions for its current task. Guard state in `compact-guard.json`.
2. `hx checkpoint` + `validate` for the agent's registered task. For pressure compaction, the task may remain `in_progress`; the worktree has no unsaved edits by another agent (`git status` clean or all changes owned by this agent's task and recorded in the checkpoint).
3. `herdr agent prompt <agent> '/compact' --wait --timeout 300000`.
4. Verify: agent returns to idle, utilisation dropped (re-read), then prompt `[team from hx-claude] Context compacted. Resume from .hx/checkpoints/<id>.md; reply with the next action only.` and check the reply names the checkpoint's Next action.
5. Any failure: record `compaction_failed` in the guard, do not retry this task, leave the session as is, report to the lead.

The lead (Claude) is not compacted by hx: Claude's own auto-compact runs, the `PreCompact` hook writes the checkpoint and returns compact instructions (keep task ids, exact errors, decisions), and `SessionStart(compact)` injects `hx task list --active` + the latest checkpoint. Codex and OpenCode native auto-compaction stay on as the safety net.

## 6. Observability

`.hx/routing.jsonl`: one line per routing decision and one per outcome: task id, worker, model, effort, method, reason, duration, tokens/cost when known (ocrun and codex exec report them), attempts, result. `hx report` summarises by pool and model: tasks, success rate, cost, and the Claude/Codex work avoided. No dashboard.

## Review round 1 (hx-codex, f61a26b): binding amendments

These override the sections above where they conflict.

1. **Router is staged, not first-match.** Order: (a) attempt cap (`attempt > max_attempts` -> strong model, then stop); (b) failure cause (`missing-context|unclear|tool` -> same worker/model, fix the input); (c) pool selection (trivial/direct, review/architecture/high risk, else OpenCode); (d) model on the ladder with fallback across ladder steps and pools.
2. **Leases.** One lease per agent session in `~/.herdr-team/state/leases.json` (under the same lock as the ledger). Dispatch to a persistent agent and compaction both acquire it; compaction re-checks session ID and idle immediately before sending `/compact`.
3. **Checkpoints are agent-authored.** Decisions, Exact errors and Next action come from the owning agent (`hx checkpoint write --from-file`); the script only adds git facts. Validate requires the agent sections non-empty and `ledger_rev` equal to the task's current revision. Native auto-compaction is labelled *unprotected safety net*; hx-run compaction is *protected*.
4. **Protected compaction runs only while the agent is idle.** At a normal task boundary, `hx compact` requires the task to be `done|failed`. Under pressure (utilisation >= `pressure_threshold`), it may compact the agent's single registered open task after that task has a valid checkpoint. The task stays open, and all ownership, lease, cooldown, per-task limit, same-session, fresh-telemetry, lower-utilisation and acknowledgement checks still apply. If no hx task is registered, `hx context decide` returns `compact-needed`; it does not authorize an unprotected compaction. On timeout the lease stays in state `compact-unknown`; no dispatch to that agent until it is idle and fresh telemetry is seen.
5. **Ledger concurrency.** One lock (`.hx/ledger.lock`, O_EXCL create, stale after 60s) is held across read-check-mutate-replace. Ledger has a monotonically increasing `rev`. Routing log lines carry `decision_id`; outcomes reference it. Order on completion: checkpoint first, then ledger status, so a crash leaves a recoverable checkpoint.
6. **Telemetry binds to session IDs**, not newest file: `herdr agent get` returns `agent_session.value` (Codex rollout id, OpenCode `ses_...`). Claude status line writes `claude-status-<session_id>.json`; the lead's session id is recorded in the ledger at start.
7. **Ownership uses canonical paths** (resolved, case-folded on Windows). `--worktree` records the worktree root and is verified with `git worktree list`; overlap checks still apply within one worktree.
8. **Availability is tri-state**: `available | exhausted | unknown`. All windows (5h, week, month) are checked after normalising to fractions. Unknown or stale (>1h) pools rank below available ones but are not treated as exhausted. Busy or blocked persistent agents are not eligible for `persistent` dispatch.
9. **Persistent sessions report their real model.** The router reads hx-opencode's actual model (latest message `modelID`). If the chosen model or effort differs, the route becomes `ocrun`. Effort-to-variant mapping is per model in `policy.json`, verified by the benchmark; unmapped -> no `--variant`.
10. **Task base.** Ledger stores `base_commit`, `worktree`, and `rev` per task. Checkpoint validation only compares the task's owned files against `base_commit..HEAD` plus working-tree changes to those files.

## Devin SWE-2 worker (2026-10-31)

Evidence: `docs/audit/devin.md`. Creator decision 2026-11-06 supersedes the 2026-10-31 directive: OpenCode with `deepseek-v4.1-flash` first is the primary implementation worker, and free Devin SWE-2 is the fallback when the OpenCode pool is spent or `policy.routing_order` puts `devin` first. Free tokens do not mean free concurrency: two runs at a time (measured), more only after measuring.

**Shape.** A fourth `hx ocrun` worker, not a new orchestrator: `hx ocrun --worker devin --model swe-2-high|swe-2-max|swe-2-medium`. It runs `devin -p` as a one-off local process, visible window under Herdr and headless otherwise, the same as the OpenCode and Codex workers. ACP (`devin acp`) was not used: hx has no ACP client, and the subprocess plus export file already gives cancellation, a timeout and a verifiable result. Code: `hx/devin.py` (preflight, promotion guard, slots, export parsing) and `run_devin` in `hx/ocrun.py`.

**Routing.** `hx route` stage 3 walks `policy.routing_order` (default `["opencode", "devin"]`) for `policy.devin.kinds` (routine, investigate, feature, debug, refactor, test). OpenCode first: the decision is `{worker: opencode, model: <first available ladder model>, method: ocrun|persistent}`, which is `deepseek-v4.1-flash` while that pool has headroom. Devin is the fallback when no OpenCode ladder model is available, or first when `routing_order` says so: `{worker: devin, model: policy.devin.models[effort], method: ocrun-devin}`. Effort maps low/medium → `swe-2-high` and high/max → `swe-2-max`; the lead dispatches `swe-2-medium` by hand when latency matters. Unchanged: trivial → lead, review and risk=high → Codex, architecture → Claude. Busy, exhausted (quota error in the last 30 min), disabled or past the promotion → the OpenCode ladder exactly as before. A capability failure on `swe-2-max` escalates to Codex strong. A fix-input retry keeps Devin.

**Fail closed.** Before every run, `preflight` refuses unless: `policy.devin.enabled`; the model is in `allowed_models` (the three SWE-2 ids; Lightning, SWE-1.x, Fusion, Adaptive and third-party models are never allowed); `now < policy.devin.promo.ends_at`; and the live `devin models list` tags the model `cost_tier: Free`. After the run, every agent step's `model_name`/`generation_model` in the export must equal the pinned id, or the outcome is `model-mismatch`. There is no automatic fallback model anywhere on this path.

**Promotion guard.** `ends_at = 2026-11-30T10:00:00Z`: the docs give no timezone, so this is the end of 30 Nov in UTC+14, the earliest reading. After it, `hx usage` reports devin `disabled`, `hx route` skips it and `hx ocrun --worker devin` refuses. Moving the date needs renewed pricing verification and the user's approval; it is a policy edit, never automatic.

**Permissions and isolation.** Each run copies `team/devin-worker.config.json` to `.hx/runs/<title>/devin-config.json`. It turns off the Claude/OpenCode/Cursor imports (the worker must not read the lead's CLAUDE.md) and subagents (they run on a paid model). It allows read/edit/exec and Questlog MCP, and denies push, reset, clean, rebase, checkout, restore, switch, commit, branch -D, worktree remove, recursive deletes, `gh`, `devin`, `hx`, `herdr` and the other agent CLIs, `.env` writes and `~/.ssh` reads. Print mode rejects anything that would ask, and a rejection ends the run (classified `tool: permission denied: <cmd>`). Windows has no Devin sandbox (`--sandbox` is macOS/Linux), so exec runs with the user's rights, the same trust level as the team's Codex rules. The deny rules are guardrails against accidents, not a sandbox (see the probe table in `docs/audit/devin.md`; `git -C <dir> push` stays open because denying `git -C` broke routine reads). The run's environment drops `DEVIN_MODEL`/`DEVIN_PERMISSION_MODE`/`DEVIN_SANDBOX`/`DEVIN_VERBOSITY` and every secret-named variable (`*TOKEN*`, `*SECRET*`, `*API_KEY*`, `*PASSWORD*`, `*CREDENTIAL*`) except `QUESTLOG_TOKEN_OPENCODE`, which the Questlog MCP launcher needs. The run prompt starts with a fixed preamble (stay in the cwd, no commits or branch changes, no cloud handoff, run tests, end with files/tests/unfinished). File ownership still comes from `hx task`: parallel Devin runs need disjoint files or their own worktree.

**Concurrency.** `policy.devin.max_concurrent` (2, measured 2026-10-31) caps live Devin sessions machine-wide: running `ocrun` slots in `~/.herdr-team/state/devin-slots/slot-N.json` (O_EXCL, expiring at timeout + 5 min) plus live `<team>-devin` panes. At admission, `acquire_slot` reads the live pane list from Herdr itself; if Herdr cannot be read or returns malformed data, admission refuses with `busy` (unknown is not zero). The cached pane snapshot is for diagnostics only and never admits a run. A full cap refuses with `busy` (retry-safe), and `hx route` sends the work to OpenCode meanwhile.

**Persistent pane.** Besides one-off runs, Devin can run as a long-lived Herdr pane: `Start-HerdrTeam.ps1 -Devin` splits a pane below Codex and starts `<team>-devin` on `policy.devin.models.medium`. It refuses once `devin.promo.ends_at` has passed. Every launch copies `team/devin-pane.config.json` fresh to `~/.herdr-team/state/<team>-devin/config.json`, because Devin writes setup state into its config. That template is the worker config with a narrower hx deny: the pane may run `hx say` and `hx checkpoint` to report, while `hx ocrun`, `compact`, `context`, `task` and `route` stay with the lead. Routing: when the pane is ready (`idle` or `done`, the `usage.READY` states that context uses too) and the task is iterative or names the pane in `--persistent`, `hx route` returns `method: persistent-devin` with `agent: <team>-devin`. Reusing a ready pane adds no session, so this check runs before the cap; the policy, promotion, model and quota guards still apply. An input-repair retry re-checks pane eligibility and keeps the agent. The ledger treats `persistent-devin` like `persistent` (one `is_persistent_method` predicate), so session leases and compaction locks cover it. `hx context` reads the pane's footer (`SWE-2 High · accept edits  Context: 34k / 262k tokens (13%)`), only from the bottom line, and the stated percentage must agree with the token ratio; anything else is unknown.

**Denied commands end turns.** One denied command ends a `devin -p` run and also cancels a pane turn. Workers tend to explore with `herdr`, `devin` and `hx`, so the one-off run preamble now lists the config's denied rules, generated from the config. A pane gets no preamble, so `hx say` appends a one-line deny list read from the pane's live config (with `team/devin-pane.config.json` as fallback) to every message it sends the pane; still put the herdr syntax the task needs in the brief.

**Result.** The usual ocrun outcome (`ok`, `failure`, `session_id`, tokens, `latency_s`, `run_dir`) plus `provider`, `model`, `effort`, `models_seen`, `final_message`, `changed_files` (git porcelain), `export` and `promo_ends_at`; the routing log line carries `provider` and `models_seen`. Failure kinds: `policy` (refused before launch), `busy`, `cancelled` (worker window closed), `tool` (timeout with the process tree killed, permission denied, no final answer, no export), `quota`, `exit`, `model-mismatch`. A run counts as finished only when its last agent step is message-only (tool calls on the last step mean it stopped mid-work); the export's `permission_mode` must match the requested mode. `changed_files` lists paths that became dirty during the run (`dirty_before` lists the rest); the full final message is in `final_message_file`. Every run claims its own run directory atomically, and a run whose kill was not confirmed keeps its slot until it expires. As with OpenCode, `ok` only means the run ended cleanly: the lead still reads the diff and runs the tests.

**Review round (Devin SWE-2 Max, 2026-10-31).** A cross-family adversarial review (author Claude, reviewer SWE-2 Max; Codex was at 77% of its 5h window) found 2 high, 6 medium and 9 low issues and confirmed the model, promotion and Cloud invariants fail closed. Fixed: a truncated run reporting ok (H1); deny-list gaps (H2, partly: string rules cannot be complete); slot races and leaks (M3: one lock around the slot table, release only by the owner); a malformed policy or export crashing routing for every pool (M4); quota text ignored when an export existed (M5); secrets inherited by the worker (M6); a missing cwd running Devin elsewhere, plus a BOM-less run.ps1 (M7); shared run directories (M8); plus L9, L10, L12-L15. Not changed: L11 (low/medium effort map to the same model; bounded by the attempt cap), L16 (POSIX only), L17 (the promotion is checked at start).

## Validation plan

Unit tests per module with fixture DBs/JSONL. Live checks: V-1 `/compact` via herdr prompt on each TUI; communication matrix; the end-to-end feature run in a scratch repo. See `docs/VALIDATION.md` (written during phase G).

## Rollback

All new state lives in this repo, `<repo>/.hx/` and `~/.herdr-team/state/`. Hooks and the status line are installed by `install.ps1` with backups. Removing the hooks and the `hx-router` skill restores the previous behaviour.
