# Phase 2 baseline — how the team actually works, measured

Window: 2026-10-25 → 2026-11-08 (14 days). Sources: Claude session JSONLs (`~/.claude/projects/**`, 44 files, ~1.1 GB), `.hx/` records in herdr-orchestration and the devin-pane worktree, `git log` in todo-rpg / herdr-orchestration / agent-memory. Method per measure is the quoted command/query. Numbers are what the logs show, not what the skills promise.

## 1. Skills — installed ≠ invoked

Scan: every `tool_use` of the `Skill` tool across all 44 session files, plus `Bash` command text for `hx say`, test runners, and `STATUS:` tokens.

- **75 Skill invocations in 14 days.** Distribution: `poteto-mode` 30, `hx-router` 17, `herdr-team` 11, `artifact-capabilities` 4, `bro` 4, `obsidian` 4, `memory` 2, `figure-it-out` 2, `update-config` 1.
- **The entire leaf layer is unused.** Zero invocations of `eval`, `interrogate`, `architect`, `arena`, `swarm`, `how`, `why`, `recall`, `blast-radius`, `unslop`, `no-comments`, `technical-writing`, `tdd`, `deslop`, `create-verification-skill`, `maintain-verification-skill`, `benchmark-checklist`, and every `principle-*` leaf. Caveat: poteto-mode routes internally without a Skill-tool call when its own prompt instructs it, so "0 invocations" ≠ "0 influence" — but the eval and verification-skill generators produce files, and none were produced.
- All 75 invocations were in main sessions; subagent sessions invoked zero skills.

## 2. Verification — strong for code, absent for skills

Scan: `Bash` tool_use command strings for runner invocations; `STATUS:` tokens in text.

- Real-runner commands are habitual: `vitest` 339, `npm run check` 296, `tsc` 195, `eslint` 168, `npm run e2e` 56. For this repo's surface, work does get proven on the real artifact before "done".
- Reports: `STATUS: done` 67, `blocked` 2 (~97% done rate). Post-done defects still surface in review — e.g. the Q-178 review cut Trail-Ready/Outfitter after "done" (duplicated `ach-gear-10`/`ach-gear-25`), so the human/peer gate still catches what the runner didn't.
- Gap: verification is *code* verification. No eval ever verified a skill or prompt change (§7).

## 3. Review — exists as a mechanism, thin as a practice

Evidence: `.hx/devin-review-result.json`, `.hx/devin-review-tests-result.json`, `.hx/devin-tests-result.json` in herdr-orchestration (devin worker: 50 tool calls, 3.7M tokens, 892 s, `ok: true`); routed review-ish tasks `devin-review`, `devin-review-tests`, `devin-tests`.

- Cross-model review is wired and used: routed review tasks ran and produced artifact results; reviews did find real things (the Q-178 dupes above; the greats page rework).
- But volume is small: ~3 routed review tasks visible vs **334 commits in todo-rpg** over the window (all authored "Casper5253" — workers have no git). Most changes are not independently reviewed before landing.

## 4. Delegation — heavy traffic, high routed failure, one cause

- **~165 briefs**: 139 in the worktree `.hx/briefs/` (includes stale/archived) and 26 in herdr-orchestration `.hx/briefs/`. `hx say` called **629 times** — reporting traffic is heavy.
- Routing (`routing.jsonl` both repos): 29 `decision` records → 21 `outcome` records. Workers: devin 13, codex 6, opencode 4 (decisions).
- **Outcomes: 12 ok / 9 failed = 43% failure — and every single failure is `kind: tool`, a permission denial** (webfetch to github.com/api.github.com, exec `herdr`/`devin`/`git log`, `tasklist`, read of lead-repo paths). No timeout, truncation, or wrong-path failures recorded. The failure mode is entirely sandbox policy, not worker competence.
- Retries: `q196-setup` failed twice on the same denied webfetch; `h3-devin-context` failed twice on denied exec — same-cause retries, so denials aren't being pre-empted.

## 5. Planning — plan-first is real; plans do get amended

- Every piece of work observed ran under a written brief file (~165). The herdr-team contract enforces goal/playbook/principles in briefs.
- Amendments happen live: this session alone shows the lead correcting a brief mid-flight twice (artifacts output path; "I did the checkout for you, skip that step"). Plan-change is handled by follow-up message, not brief versioning — there is no brief-history trail beyond the file's last write.

## 6. Memory — exists, barely exercised

- `agent-memory` repo: 10 commits in the window. The `memory` skill was invoked 2×, `obsidian` 4× in 14 days across all sessions — a handful of vault touches for 300+ repo commits of activity.
- Contrast: `hx` checkpoints exist (`.hx/checkpoints/`, one `handoff-2026-10-31.md`), and in-session "compact/summarize" string hits are ~1000 lines — context is being lost and re-derived constantly rather than persisted.

## 7. Evals — zero

- Zero `eval` invocations; no `~/.agents/pstack/evals/` artifacts; no skill change was ever tested before adoption in the window. This is the largest measured gap between "what the setup claims" and "what it does".

## Context management (the creator's named complaint)

- Auto-compaction fires reactively (long sessions summarize repeatedly — this very session has). Deliberate, structured checkpointing is rare: one `handoff-*.md` and the `.hx/checkpoints/` dir across the whole window. `hx context`/`hx compact` exist in routing records (`h3-devin-context` task, 3 outcomes) but the two measured runs failed on tool denials. Matches the report: "we have context management; it works poorly."

## Headline numbers

- 75 skill invocations; 40+ leaf skills at zero. Evals: zero, ever.
- 43% of routed outcomes failed — 100% of those failures are tool-permission denials.
- 334 todo-rpg commits vs ~3 routed review tasks — review coverage is single-digit percent.
