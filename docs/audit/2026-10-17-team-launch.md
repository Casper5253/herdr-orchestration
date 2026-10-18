# Audit: a four-agent Herdr team in real use (2026-10-17)

Session: building the portfolio site (`~/Documents/Projects/Personal Portfolio Website`) with dev-claude (lead, Opus 5.5), dev-haiku (Claude Haiku), dev-luna (Codex gpt-6-luna) and dev-codex (Codex gpt-6.1-sol, medium). OpenCode was out of quota. Every finding names its evidence. The fix plan at the end turns them into a few structural changes, so they hold for every new project and session, not just this one.

## 1. What was exercised, and how it went

| Skill or command | Used for | Result |
|---|---|---|
| `herdr-team` briefs and reports | 9 briefs, about 12 reports | Worked once messages were single-line (finding 8). Long briefs went in files, which worked well. |
| `hx say` to codex, haiku, luna | All team messages | Worked. Fails with `agent_blocked` while the target waits on an approval prompt (findings 10, 12). |
| `hx usage` | Headroom check | Worked; tags measured, estimated, unknown. The Claude pool is read from whichever team Claude has a status file, here the Haiku worker's (finding 7). |
| `hx route` | Routing two tasks | Crashed on first use, now fixed (finding 5). Afterwards it gave sensible decisions (Codex Luna, medium). |
| `hx task add`, `assign`, `done`, `checkpoint write` | 4 tasks, worktree ownership | Worked. Workers wrote their own checkpoints. `task done` closed map, smoke and github. `lab` is left open with a partial checkpoint. |
| `hx context decide` | Every agent | Ran, but returned "outside the configured team" for haiku and luna (finding 6), and "telemetry unknown" for the lead (finding 7). |
| `hx compact` (protected compaction) | Not triggered | **Not verified this session.** Only dev-codex was eligible, and it stayed at 92K of 475K tokens. Meanwhile dev-luna was natively auto-compacted mid-task (finding 11), which is the failure protected compaction exists to prevent. |
| Lead self-compaction | Not possible | The lead had no telemetry (finding 7). |
| `hx ocrun` | Not used | Persistent sessions were used instead. |
| pstack (`poteto-mode`, `figure-it-out`, `show-me-your-work`) | Planning, decision trail | Worked. The decision trail is in the portfolio repo, `docs/decisions.tsv`. |

## 2. Findings

### Starting the team
1. **The lead cannot start teammates under auto mode.** The classifier denied `herdr agent start ... --permission-mode auto` and an edit to `~/.codex/config.toml` ("Create Unsafe Agents"). `team/claude-settings.json` also puts `herdr agent start` under `ask`. The user ran all three starts by hand with `! <command>`; the third ran only after the user explicitly authorised it. `pane split` and `agent rename` were allowed.
2. **The launcher knows only claude, codex and opencode, with fixed models.** It cannot start haiku or luna or pick Sol, so the lead had to copy its split, env and PATH logic by hand.
3. **(Fixed 2026-10-18, d6c25cb.)** **The role list is written in three places:** `hx/say.py:14`, `hx/usage.py:466` and `hx/context.py:194`. Commit f7e04cb added haiku and luna to two of them. The launcher and the profile's `developer_instructions` ("<team>-claude, <team>-codex and <team>-opencode") are a fourth and fifth copy.
4. **A new repo cannot be trusted under `-p trusted-dev`.** Codex error: `config/batchWrite failed: Invalid configuration: invalid transport in mcp_servers.codedb`. The profile file holds its own `[projects.*]` trust entries, so Codex writes trust into the profile. The profile declares `[mcp_servers.codedb.tools.*]` without a `command`, so that write fails validation. `herdr agent start` then timed out at the prompt. Workaround: the user added a trust entry to `config.toml` by hand.
9. **Every Codex start warns** `Failed to save default model ... invalid transport in mcp_servers.codedb`. Same root cause as 4.

### Running the team
5. **`hx route` crashed** with `argument of type 'NoneType' is not iterable` on a Claude status file with `"rate_limits": null`, which is what a fresh session writes. **Fixed** in f915343, with a regression test; 290 tests pass. `route` and `ocrun` are still missing from `hx --help`.
6. **(Fixed 2026-10-18, d6c25cb.)** **`hx context` ignores haiku and luna**, because of the hard-coded tuple at `context.py:194`. So protected compaction can never be offered to them.
7. **A lead adopted with `-LeadPane` has no telemetry.** It was not started with `team/claude-settings.json`, so it has no status line and no `claude-status-<session>.json`. `hx context decide dev-claude` returns `telemetry unknown or stale`, and lead self-compaction can never trigger.
8. **Multi-line `hx say` messages are cut after the first line.** dev-codex's first review lost all ten findings this way.
10. **Codex cannot write to `.git` without asking the user.** I first believed only chained commands were the problem. The rollout log disproved that: a plain `git add -- scripts/smoke.mjs` with no escalation also failed with `Permission denied ... .git/worktrees/portfolio-sol/index.lock` (rollout `01a14a22`, 14:17:30 UTC). In Codex 0.163.0, a `prefix_rule(..., decision="allow")` removes the approval question but does not lift the sandbox, and the sandbox keeps `.git` read-only even inside `writable_roots`. Any git write therefore needs escalation, and escalation asks the user. `codex execpolicy check` confirms the rule matches plain `git` but not `powershell -Command "git a; git b"`. That is true but beside the point. The `developer_instructions` line added this session ("plain commands are allowed") is wrong and must be replaced (fix plan C).
11. **A Codex worker was auto-compacted mid-task.** dev-luna's session (`01a14a1c`, 258K window) compacted itself at 14:16:53 UTC, three minutes into a correction round (rollout line 723). Protected compaction could not run (finding 6). The lead caught it only because the user asked. The worker's partial work survived because the brief lived in a file.
12. **Codex asks before writing outside the sandbox, for example screenshots to `%TEMP%`.** dev-luna asked to run `msedge.exe --headless --screenshot=C:\Users\caspe\AppData\Local\Temp\...`. `msedge.exe` is already allowed (same cause as 10). The repo's own `npm run smoke`, which writes to `.artifacts/` inside the repo, ran in the sandbox with no prompt.
13. **Killing a pane leaves state behind.** After the user closed dev-luna, its `astro preview` (pid 32368) kept running, blocked the next smoke run, and its `lab` task stayed `assigned` in the ledger.
15. **(Fixed 2026-10-18, d6c25cb.)** **A Claude worker's context could never be read, and any Claude-kind agent was treated as the lead.** Herdr 0.9.5 reports no `agent_session` for Claude panes, so `hx context decide dev-haiku` had no session id and its telemetry was always unknown. Protected compaction could never fire for a Claude worker. Separately, `decide` sent every Claude-kind agent down the lead's self-compaction path. Fixes: the status line records `HERDR_PANE_ID`, and `_session()` falls back to the newest status file written from that pane. The lead is the agent named `<team>-<lead_role>` (`policy.json`, default `claude`), and every other team member is a worker, whatever its kind. Verified live: after the hook change, `hx context decide dev-haiku` found session `34a3ceac` through pane `wA:p3` and read 41% utilisation, and `dev-luna` read 33% from its rollout. Before the fix, both returned "outside the configured team".
14. **The lead learned of a blocked worker only by accident.** `blocked` showed up only when a later `hx say` failed. The user spotted both approval prompts first.

## 3. Fix plan: structural, applies to every project and session

Each change lives in herdr-orchestration (installed by `install.ps1`) or in the user-level config, so every new team on any repo gets it. None of this has been implemented yet; this session only documents it, at the owner's request.

### A. One team definition (fixes 2, 3, 6, part of 7). Done for hx on 2026-10-18 (d6c25cb), owner direction: model and name agnostic
Implemented differently from the table below, which is kept for history. There is no role table: team membership is "a live Herdr agent named `<team>-<anything>`"; the provider comes from Herdr's detected agent kind, so telemetry and compaction work for any role and any model; the lead is `<team>-<lead_role>` from `policy.json`. `hx say`, `hx context` and `hx compact` all follow this. Tests pin a new role (`dev-gemini`) working without code changes, a Claude-kind worker staying a worker, and the lead coming from policy. The skills and the Codex profile describe the team the same way. The launcher (plan B) still has fixed roles.
Add a `team` table to `policy.json`:

    "roles": {
      "claude":   {"kind": "claude",   "args": ["--permission-mode", "auto", "--settings", "{dir}/claude-settings.json"]},
      "haiku":    {"kind": "claude",   "args": ["--model", "haiku", "--permission-mode", "auto", "--settings", "{dir}/claude-settings.json"]},
      "codex":    {"kind": "codex",    "args": ["-p", "trusted-dev", "-m", "gpt-6.1-sol", "-c", "model_reasoning_effort=medium"]},
      "luna":     {"kind": "codex",    "args": ["-p", "trusted-dev", "-m", "gpt-6-luna", "-c", "model_reasoning_effort=medium"]},
      "opencode": {"kind": "opencode", "args": []}
    }

`say.ROLES`, `usage.TEAM_AGENT`, `context.Controller._agent` and the launcher all read it. `hx context` looks up telemetry by `kind`, so a haiku worker uses Claude telemetry and a luna worker uses Codex rollouts. Add a test that every module accepts every role in the table, so the next new role cannot be half-added. Generate the profile's `developer_instructions` team line from the same table at install time.

### B. The launcher is the only way to start agents, and the lead may call it (fixes 1, 2, 4, 7, 9)
- `Start-HerdrTeam.ps1 <repo> -LeadPane $env:HERDR_PANE_ID -Workers codex,luna,haiku` reads the role table, splits panes sensibly, and starts each worker with that role's arguments and the pinned Codex PATH.
- Before starting Codex, it writes `[projects.'<lowercased repo>'] trust_level = "trusted"` into `~/.codex/trusted-dev.config.toml`, where Codex keeps profile trust. Launching a team on a repo is the user's trust decision.
- Add one allow rule to the **user-level** `~/.claude/settings.json`, so it holds in every project: `PowerShell(powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\caspe\.herdr-team\Start-HerdrTeam.ps1:*)`, plus the Bash equivalent. Keep raw `herdr agent start` in `ask`. The script fixes the arguments, so the allow rule cannot start an arbitrary agent.
- With `-LeadPane`, the launcher checks whether the lead has a status file. If not, it prints one line: restart the lead with `claude --settings <dir>\claude-settings.json --resume <id>` to get telemetry and self-compaction.

### C. Codex profile hygiene and the git policy (fixes 4, 9, 10, 12)
- Move the two `[mcp_servers.codedb.tools.*]` tables from `trusted-dev.config.toml` into `config.toml`. The profile then has no half-defined server, and trust writes and default-model saves validate.
- **Git policy: the owner must choose one**, then it gets written into the herdr-team skill and the profile:
  - (default, used since this session) **Codex edits, the lead commits.** Codex workers never run git writes. They report changed files, and the lead commits and merges. No prompts, no security change.
  - (alternative) **Sandbox off for team Codex** (`sandbox_mode = "danger-full-access"` in `trusted-dev`). Git works, but so does every other write and network call. Only if the owner accepts that.
- Replace this session's `developer_instructions` git line with the chosen policy. Its claim that "plain git commands run without approval" is false (finding 10).
- Add to the herdr-team skill: artifacts such as screenshots and logs go inside the repo (for example `.artifacts/`, gitignored), never to `%TEMP%`.
- Add `tests/test_execpolicy.py`, which runs `codex execpolicy check` on the forms we rely on, so a Codex upgrade that changes rule semantics fails a test instead of a session.

### D. Messages (fixes 8, 14)
- `hx say` rejects a message containing a newline, with the error "write long content to a file and send its path". The herdr-team skill already asks for one-line reports. Make the tool enforce it.
- `hx say` checks `herdr agent get` first. If the target is `blocked`, it exits non-zero with "dev-X is waiting for the user". Add `hx team status`, one line per agent (state, context %, task), which the lead runs at every task boundary. That makes a blocked worker visible within one boundary instead of by accident.

### E. Context (fixes 6, 11)
- With A in place, `hx context decide` covers every role. The hx-router skill tells the lead to run `hx context decide` for every worker at each task boundary, and `hx compact` when it says so, before a worker's native threshold is reached.
- A smaller window means an earlier boundary. Put each model's context window in `policy.json` (gpt-6-luna 258K, gpt-6.1-sol 475K) so the decision accounts for it.
- Briefs and correction rounds always live in files (as this session did). A worker that compacts natively, or is restarted, can reread its full brief.

### F. Worker lifecycle (fix 13)
- Add `hx agent retire <name>`: releases the agent's tasks in the ledger (`hx task release`), records the last checkpoint, and lists processes started from its worktree, such as dev servers, for the lead to stop.
- Project verification scripts should start their servers with a unique port and `--force`, or stop a leftover server first. The portfolio's `scripts/smoke.mjs` needs this (see its `docs/STATUS.md`).

### Interim practice until A and E land (used 2026-10-18)
- At each task boundary the lead reads every worker's context directly: Codex from the newest `token_count` in its rollout (`~/.codex/sessions/<date>/*<session>*.jsonl`), Claude from the status line (`ctx N%`).
- A worker above about 50% of its window, about to start a long task, gets a fresh session instead of a compaction: `send-keys esc`, `/quit` (sent from PowerShell, not Git Bash), `herdr agent start` with the same role arguments, then the brief again. This works because briefs live in files and work lives in git. On 2026-10-18 dev-luna was restarted at 54% of 258K before a 14-item round.
- Every brief says: "if your context passes 60%, commit and report partial with the remaining items".
- With the sandbox off (owner decision 2026-10-18), the guardrails are: the profile's `developer_instructions` bounds; `prompt` rules for destructive git (`push`, `reset`, `clean`, `rebase`, `branch -D`, `checkout -- .`, `restore`, `worktree remove`, `stash drop`), checked with `codex execpolicy check`; and no git remote in a project until the owner approves a deploy, because a chained command wrapped in `powershell -Command` matches no rule.

## 4. Order of work
1. C first. Most of it is a config change with an owner decision, and it removes the prompts that block workers.
2. A, then B. B depends on the role table.
3. D and E, small changes in `say.py`, `context.py` and the skills.
4. F.

Each step ends with `python -m pytest tests -q`, and one live launch with `-Workers codex,luna,haiku` on a scratch repo that ends in a protected compaction of a haiku or luna worker. That live run is the check that was missing this session.
